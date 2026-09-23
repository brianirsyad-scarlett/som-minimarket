"""
Emailed report links - extractor and downloader.

Some portal reports are not downloaded in-session like the Market Share
modulars are. You ask the portal to generate them, it builds the file
server-side, and some minutes later it emails you a download link. This script
is the second half of that: it picks the link out of the email and fetches the
file before the link expires (observed: roughly 24 hours).

Why it reads files off disk instead of reading the mailbox:

  The day-to-day mail client here is New Outlook
  (Microsoft.OutlookForWindows), which has no COM/MAPI object model - there is
  no local way to read the mailbox at all. The Gmail/M365 cloud connectors are
  blocked at tenant level. So a Power Automate flow (standard connectors only,
  no premium HTTP action, so it keeps working after the trial ends) writes each
  matching email's HTML body into a OneDrive folder, OneDrive syncs it to this
  PC, and this script takes it from there. README.md has the flow setup.

Flow:
  1. Scan DROP_DIR for email-body files Power Automate has dropped.
  2. Match each against a source in sources.json (Alfamart, Alfamidi, ...).
  3. Pull candidate URLs out of the HTML, unescaping entities.
  4. Download each new one, naming it from Content-Disposition where offered.
  5. File it into that source's dest folder, record it, move the body to
     _processed/.

Re-running is safe: URLs already fetched are skipped via state.json.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import logging
import os
import re
import sys
from datetime import datetime, timedelta
from email import policy
from email.parser import BytesParser
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

try:
    import requests
    from bs4 import BeautifulSoup
    from dotenv import load_dotenv
except ImportError as exc:
    sys.exit(f"Missing dependency ({exc.name}). Run setup.ps1 first.")

HERE = Path(__file__).resolve().parent
STATE_FILE = HERE / "state.json"
SOURCES_FILE = HERE / "sources.json"
LOG_FILE = HERE / "run.log"

DEFAULT_DROP = Path(
    r"D:\OneDrive - PT. Opto Lumbung Sejahtera\Apps\ReportLinks"
)
DEFAULT_DEST = Path(r"D:\SCARLETT_512\SCARLETT-329\SOM\Data\Report\Sales\Minimarket")

# Same hard blocklist as the attachment harvester - a download link from an
# email is exactly as untrusted as an attachment, and this runs unattended.
BLOCKED_EXT = {
    ".exe", ".dll", ".scr", ".com", ".pif", ".bat", ".cmd", ".msi", ".msp",
    ".ps1", ".psm1", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh", ".hta",
    ".cpl", ".reg", ".lnk", ".inf", ".sys", ".drv", ".ocx", ".jar", ".apk",
    ".iso", ".img", ".vhd", ".chm", ".application", ".gadget", ".msc",
}

# Links every marketing email carries. Matched on host SUFFIX, not substring:
# a blanket "google.com" would also throw away a drive.google.com delivery link,
# and silently dropping the actual report is the worst failure this can have.
# Deliberately excludes google.com / microsoft.com for that reason - link_regex
# in sources.json is the right place to narrow things down.
NOISE_HOST_SUFFIXES = {
    "aka.ms", "twitter.com", "x.com", "facebook.com", "linkedin.com",
    "instagram.com", "youtube.com", "youtu.be", "tiktok.com", "w3.org",
    "schemas.microsoft.com", "schemas.openxmlformats.org",
    # Asset CDNs - named exactly, since their paths often carry no extension
    # for ASSET_EXT to catch (e.g. fonts.googleapis.com/css?family=Roboto).
    "fonts.googleapis.com", "fonts.gstatic.com", "gstatic.com",
    "cdn.jsdelivr.net", "cdnjs.cloudflare.com",
}

# Opt-out links, which sit on the same host as the real download.
NOISE_PATH_HINTS = ("unsubscribe", "berhenti-berlangganan", "optout",
                    "opt-out", "email-preferences")

# Embedded assets - a banner in an <a> would otherwise look like a candidate.
ASSET_EXT = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".svg", ".ico", ".webp",
             ".css", ".js", ".woff", ".woff2", ".ttf"}


def is_noise(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    if any(host == s or host.endswith("." + s) for s in NOISE_HOST_SUFFIXES):
        return True
    lowered = url.lower()
    if any(hint in lowered for hint in NOISE_PATH_HINTS):
        return True
    return Path(urlparse(url).path).suffix.lower() in ASSET_EXT

log = logging.getLogger("report_links")


def setup_logging(verbose: bool) -> None:
    log.setLevel(logging.DEBUG if verbose else logging.INFO)
    fmt = logging.Formatter("%(asctime)s  %(levelname)-7s %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)
    log.addHandler(stream)
    handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    handler.setFormatter(fmt)
    log.addHandler(handler)


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def load_json(path: Path, fallback):
    """Read JSON, tolerating a UTF-8 BOM.

    utf-8-sig, not utf-8: editing sources.json in Notepad or writing it from
    PowerShell adds a BOM, which makes a plain utf-8 json.loads throw. That
    would silently fall back to "no sources" - every report lands in Unsorted
    with no link filtering, and nothing looks broken.
    """
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("Could not read %s (%s).", path.name, exc)
        return fallback


def safe_filename(name: str) -> str:
    name = re.split(r"[\\/]", unquote(name).replace("\x00", ""))[-1]
    name = re.sub(r'[<>:"|?*]', "_", name)
    name = "".join(ch for ch in name if ord(ch) >= 32).strip().rstrip(".")
    return name[:180] or "download"


def unique_path(folder: Path, filename: str) -> Path:
    candidate = folder / filename
    if not candidate.exists():
        return candidate
    stem, suffix = candidate.stem, candidate.suffix
    for i in range(2, 1000):
        candidate = folder / f"{stem} ({i}){suffix}"
        if not candidate.exists():
            return candidate
    return folder / f"{stem} ({datetime.now():%Y%m%d%H%M%S}){suffix}"


def read_body(path: Path) -> tuple[str, str]:
    """Return (body, sender) for one dropped file.

    .eml gets real MIME parsing rather than a plain read. A message saved out of
    Outlook is normally quoted-printable, which breaks long URLs across lines
    with '=' soft breaks - exactly what a signed URL full of query parameters
    looks like. Read as plain text those links come out silently truncated:
    they look plausible in the log and 404 on fetch. Power Automate writes the
    decoded body directly, so .html needs none of this.
    """
    if path.suffix.lower() == ".eml":
        with path.open("rb") as fh:
            msg = BytesParser(policy=policy.default).parse(fh)
        sender = str(msg.get("From", "") or "")
        part = msg.get_body(preferencelist=("html", "plain"))
        if part is None:
            return "", sender
        try:
            return part.get_content(), sender
        except (LookupError, UnicodeDecodeError):
            payload = part.get_payload(decode=True) or b""
            return payload.decode("utf-8", errors="replace"), sender

    return path.read_text(encoding="utf-8", errors="replace"), ""


def unwrap_safelinks(url: str) -> str:
    """Recover the real URL from a Microsoft Defender Safe Links wrapper.

    Every link in inbound mail gets rewritten to
    `https://<region>.safelinks.protection.outlook.com/?url=<encoded>&data=…`,
    so the href is Microsoft's redirector, not the download. The wrapper also
    carries the recipient's address in `data=`, and is scoped to the tenant.

    parse_qs already percent-decodes once, which is exactly right here: the
    inner URL is double-encoded in the wrapper (`%252F`), so one decode leaves
    the `%2F` and `%40` that legitimately belong inside the signed URL's
    credential parameter. Decoding a second time would corrupt the signature.
    """
    host = (urlparse(url).hostname or "").lower()
    if not host.endswith("safelinks.protection.outlook.com"):
        return url
    inner = parse_qs(urlparse(url).query).get("url", [None])[0]
    return inner or url


def extract_links(body: str) -> list[str]:
    """Every href plus any bare URL in the text, entity-unescaped.

    The unescaping matters: Power Automate writes the HTML body verbatim, so a
    signed URL arrives as ...?X-Goog-Expires=59&amp;Signature=... and fetching
    that literally returns a signature error.
    """
    urls: list[str] = []
    try:
        soup = BeautifulSoup(body, "html.parser")
        for anchor in soup.find_all("a"):
            # Outlook keeps the pre-rewrite URL in `originalsrc` when it wraps a
            # link in Safe Links. Prefer it: it needs no unwrapping and cannot
            # be mangled by the redirector. Power Automate bodies often lack it,
            # which is what unwrap_safelinks() below is for.
            href = anchor.get("originalsrc") or anchor.get("href")
            if href:
                urls.append(href)
        text = soup.get_text(" ")
    except Exception as exc:  # malformed HTML shouldn't kill the run
        log.debug("HTML parse failed (%s) - falling back to regex.", exc)
        text = body
        urls.extend(re.findall(r'originalsrc=["\']([^"\']+)["\']', body, re.I))
        urls.extend(re.findall(r'href=["\']([^"\']+)["\']', body, re.I))

    urls.extend(re.findall(r"https?://[^\s<>\"']+", text))

    seen, cleaned = set(), []
    for url in urls:
        url = html.unescape(url.strip()).rstrip(".,;)")
        if not url.lower().startswith(("http://", "https://")):
            continue
        url = unwrap_safelinks(url)
        if is_noise(url):
            continue
        if url not in seen:
            seen.add(url)
            cleaned.append(url)
    return cleaned


def filename_from_response(response, url: str) -> str:
    """Prefer the server's Content-Disposition, else the URL path."""
    disposition = response.headers.get("Content-Disposition", "")
    match = re.search(r"filename\*=UTF-8''([^;]+)", disposition, re.I)
    if not match:
        match = re.search(r'filename="?([^";]+)"?', disposition, re.I)
    if match:
        return safe_filename(match.group(1))

    path_name = Path(unquote(urlparse(url).path)).name
    if path_name and "." in path_name:
        return safe_filename(path_name)
    return f"report-{datetime.now():%Y%m%d-%H%M%S}.xlsx"


def looks_like_login_page(response) -> bool:
    ctype = response.headers.get("Content-Type", "").lower()
    if "html" not in ctype:
        return False
    snippet = response.text[:4000].lower()
    return any(k in snippet for k in
               ("login", "sign in", "masuk", "password", "session expired"))


def resolve_dest(source, url: str, dest_root: Path) -> Path:
    """Where this particular download belongs.

    One sender can produce several report types that must be filed apart, so a
    source may carry `routes` matched against the URL - which conveniently
    contains the portal's filename. First match wins, so order them
    most-specific-first (by_branch_Stok before a bare by_branch). Falls back to
    the source's own `dest`, then Unsorted.
    """
    if source:
        for route in source.get("routes", []):
            pattern = route.get("url_regex")
            if pattern and re.search(pattern, url, re.I) and route.get("dest"):
                return dest_root / route["dest"]
        if source.get("dest"):
            return dest_root / source["dest"]
    return dest_root / "Unsorted"


def match_source(sources: list, filename: str, body: str, sender: str = ""):
    """First source whose sender/subject/filename pattern matches."""
    haystack = f"{filename}\n{sender}\n{body[:8000]}".lower()
    for source in sources:
        needles = [n.lower() for n in source.get("match_contains", [])]
        if needles and not any(n in haystack for n in needles):
            continue
        return source
    return None


def download(session, url: str, dest_dir: Path, timeout: int, max_bytes: int):
    """Returns the saved Path, or None with the reason already logged."""
    try:
        response = session.get(url, timeout=timeout, stream=True,
                               allow_redirects=True)
    except requests.RequestException as exc:
        log.error("    request failed: %s", exc)
        return None

    if response.status_code in (403, 404, 410):
        log.warning("    link dead or expired (HTTP %d)", response.status_code)
        return None
    if not response.ok:
        log.error("    HTTP %d", response.status_code)
        return None
    if looks_like_login_page(response):
        log.warning("    got a login page, not a file - this link needs an "
                    "authenticated session. See README 'Links behind a login'.")
        return None

    filename = filename_from_response(response, url)
    if Path(filename).suffix.lower() in BLOCKED_EXT:
        log.warning("    BLOCKED executable download: %s", filename)
        return None

    declared = int(response.headers.get("Content-Length") or 0)
    if declared and declared > max_bytes:
        log.warning("    too large (%.1f MB)", declared / 1024 / 1024)
        return None

    dest_dir.mkdir(parents=True, exist_ok=True)
    final = unique_path(dest_dir, filename)
    written = 0
    try:
        with final.open("wb") as fh:
            for chunk in response.iter_content(chunk_size=1024 * 256):
                written += len(chunk)
                if written > max_bytes:
                    raise IOError(f"exceeded {max_bytes} bytes mid-stream")
                fh.write(chunk)
    except (IOError, requests.RequestException) as exc:
        log.error("    download aborted: %s", exc)
        final.unlink(missing_ok=True)
        return None

    if written == 0:
        log.warning("    empty response, discarded")
        final.unlink(missing_ok=True)
        return None

    log.info("    saved %s (%.1f KB)", final, written / 1024)
    return final


def save_state(state: dict) -> None:
    try:
        STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except OSError as exc:
        log.error("Could not write state.json: %s", exc)


def watchdog(state: dict, saw_bodies: bool, days: int) -> bool:
    """Shout if the Power Automate flow appears to have stopped delivering.

    This is the failure that actually hurts: the flow dies quietly - a lapsed
    licence, an expired connection after a password change, Microsoft disabling
    it after repeated failures, or the whole environment being a trial one that
    got deleted - and nobody notices until someone asks where last fortnight's
    reports went. An empty drop folder looks identical to "no reports due", so
    time since the last delivery is the only signal available locally.

    Returns True if an alert fired.
    """
    now = datetime.now()
    if saw_bodies:
        state["last_body_seen"] = now.isoformat(timespec="seconds")
        return False

    raw = state.get("last_body_seen")
    if not raw:
        return False  # nothing has ever arrived, so there's no baseline yet
    try:
        last = datetime.fromisoformat(raw)
    except ValueError:
        return False

    idle = now - last
    if idle <= timedelta(days=days):
        return False

    log.error("WATCHDOG: no report email has arrived for %d days "
              "(last was %s).", idle.days, last.strftime("%Y-%m-%d %H:%M"))
    log.error("The Power Automate flow has most likely stopped. Check, in "
              "order: (1) make.powerautomate.com > My flows - is it still "
              "listed and On? (2) its run history for failures, (3) the "
              "connection, which breaks on password/MFA changes, (4) that it "
              "is in the Default environment, not a trial one.")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Download report files from links emailed by the portals.")
    parser.add_argument("--dry-run", action="store_true",
                        help="show the links found, download nothing")
    parser.add_argument("--keep", action="store_true",
                        help="do not move processed email bodies aside")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    setup_logging(args.verbose)
    load_dotenv(HERE / ".env")

    drop_dir = Path(os.getenv("DROP_DIR", "").strip() or DEFAULT_DROP)
    dest_root = Path(os.getenv("DEST_ROOT", "").strip() or DEFAULT_DEST)
    timeout = env_int("HTTP_TIMEOUT", 120)
    max_bytes = env_int("MAX_MB", 200) * 1024 * 1024
    ttl_hours = env_int("LINK_TTL_HOURS", 24)

    if not drop_dir.exists():
        log.error("Drop folder does not exist: %s", drop_dir)
        log.error("Create it in OneDrive and point the Power Automate flow at "
                  "it - see README.md step 1.")
        return 2

    sources = load_json(SOURCES_FILE, [])
    state = load_json(STATE_FILE, {})
    done = set(state.get("fetched", {}))

    bodies = sorted(
        p for p in drop_dir.iterdir()
        if p.is_file() and p.suffix.lower() in {".html", ".htm", ".txt", ".eml"}
    )

    stale = watchdog(state, bool(bodies), env_int("WATCHDOG_DAYS", 3))

    if not bodies:
        log.info("Nothing waiting in %s", drop_dir)
        if not args.dry_run:
            save_state(state)
        # Non-zero so Task Scheduler's "Last Run Result" flags it rather than
        # showing a reassuring 0x0 while nothing has arrived for a fortnight.
        return 3 if stale else 0

    log.info("Found %d email body file(s) in %s", len(bodies), drop_dir)
    if args.dry_run:
        log.info("DRY RUN - nothing will be downloaded.")

    session = requests.Session()
    session.headers["User-Agent"] = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                     "AppleWebKit/537.36 (KHTML, like Gecko) "
                                     "Chrome/124.0 Safari/537.36")

    fetched = failed = 0
    new_entries = {}

    for body_file in bodies:
        try:
            body, sender = read_body(body_file)
        except (OSError, ValueError) as exc:
            log.error("Cannot read %s: %s", body_file.name, exc)
            continue

        age = datetime.now() - datetime.fromtimestamp(body_file.stat().st_mtime)
        source = match_source(sources, body_file.name, body, sender)
        label = source.get("name") if source else "unmatched"
        log.info("%s  [%s, %s old]%s", body_file.name, label,
                 str(timedelta(seconds=int(age.total_seconds()))),
                 f"  from {sender}" if sender else "")

        if age > timedelta(hours=ttl_hours):
            log.warning("  older than %dh - its link has probably expired, "
                        "trying anyway", ttl_hours)

        links = extract_links(body)
        if source and source.get("link_regex"):
            pattern = source["link_regex"]
            links = [u for u in links if re.search(pattern, u, re.I)]
        if not links:
            log.warning("  no candidate download links found")
            log.debug("  (tune link_regex in sources.json, or run --verbose "
                      "to dump what was seen)")
            continue

        got_one = False
        for url in links:
            digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
            if digest in done:
                log.debug("  already fetched: %s", url[:100])
                got_one = True
                continue

            dest_dir = resolve_dest(source, url, dest_root)

            log.info("  link: %s", url[:140])
            if args.dry_run:
                log.info("    WOULD DOWNLOAD -> %s", dest_dir)
                got_one = True
                continue

            saved = download(session, url, dest_dir, timeout, max_bytes)
            if saved:
                done.add(digest)
                new_entries[digest] = {
                    "url": url[:500],
                    "file": str(saved),
                    "source": label,
                    "fetched_at": datetime.now().isoformat(timespec="seconds"),
                }
                fetched += 1
                got_one = True
            else:
                failed += 1

        # Only file the body away once something actually came of it, so a
        # transient network failure doesn't silently lose the link.
        if got_one and not args.dry_run and not args.keep:
            archive = drop_dir / "_processed"
            archive.mkdir(exist_ok=True)
            try:
                body_file.replace(unique_path(archive, body_file.name))
            except OSError as exc:
                log.debug("  could not archive %s: %s", body_file.name, exc)

    if not args.dry_run:
        if new_entries:
            state.setdefault("fetched", {}).update(new_entries)
        state["last_run"] = datetime.now().isoformat(timespec="seconds")
        save_state(state)

    log.info("Done - %d downloaded, %d failed.", fetched, failed)
    return 1 if failed and not fetched else 0


if __name__ == "__main__":
    sys.exit(main())
