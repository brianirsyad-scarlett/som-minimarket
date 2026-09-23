"""
Alfamidi B2B - Performance Sales (modular) report requester.

Unlike the Market Share modular (Alfamidi-MarketShare), these reports are NOT
returned in-session. The portal queues them and replies:

    {"code": "T",
     "result": "Request Download File Berhasil \\nLink Download File Akan
                dikirim Via Email Jika File Sudah Tersedia"}

i.e. "the download link will be emailed to you when the file is ready". So this
script only *asks* for the reports. Collecting them is the job of
..\\Mail-ReportLinks, which picks the links out of the emails.

Flow, reproduced from the HAR captures:
  1. POST b2b.alfamidiku.com/login.php                (uname, upass - no 2FA)
  2. GET  b2b.alfamidiku.com/get_laporan_new_premium.php  -> hands session over
  3. GET  midi-b2b.et.r.appspot.com/performancesales-modular
  4. POST midi-b2b.et.r.appspot.com/perfsales/modular/bibdbs/by-branch
     or   midi-b2b.et.r.appspot.com/perfsales/modular/bibdbs/request-download

The two endpoints correspond to the "jenis_performace" dropdown:
    tipe_prf=5  Performance by Item by Branch by Day   -> /by-branch
    tipe_prf=4  Performance by Item by Store by Day    -> /request-download
The by-store variant additionally takes st / storetext.

Two things worth knowing before editing this:

  * Categories are discovered from the live page, never hardcoded. Alfamidi
    exposes 6 today; if they add more, the next run picks them up on its own.

  * `filename` is client-supplied and the server honours it. Every request
    therefore embeds a short token, recorded in requests.json, which is the
    only reliable way to tie a file that arrives by email hours later back to
    the request that asked for it.

Credentials are read from .env and never leave this machine.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import secrets
import sys
import time
from datetime import date, datetime
from pathlib import Path

try:
    from dotenv import load_dotenv
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
except ImportError as exc:
    sys.exit(f"Missing dependency ({exc.name}). Run setup.ps1 first.")

# The 10-day period math is shared with 3_upload_and_distribute.py, which
# derives each by-store file's period from its start date and deletes anything
# that overshoots the boundary. Importing the same module rather than
# reimplementing it is the only way those two stay in agreement.
PERIOD_UTILS_DIR = r"D:\SCARLETT_512\SCARLETT-329\SOM\Data\Sent Email"
sys.path.insert(0, PERIOD_UTILS_DIR)
try:
    from period_utils import branch_months, rolling_periods  # noqa: E402
except ImportError:
    sys.exit(f"Cannot import period_utils from {PERIOD_UTILS_DIR}. That module "
             "defines the by-store period calendar shared with "
             "3_upload_and_distribute.py; this script must not guess at it.")

BASE = "https://b2b.alfamidiku.com"
NP = "https://midi-b2b.et.r.appspot.com"
PERF_PAGE = f"{NP}/performancesales-modular"

SCRIPT_DIR = Path(__file__).resolve().parent
MANIFEST = SCRIPT_DIR / "requests.json"

# tipe_prf -> (endpoint, label, needs a store field)
REPORTS = {
    "5": (f"{NP}/perfsales/modular/bibdbs/by-branch",
          "Performance by Item by Branch by Day", False),
    "4": (f"{NP}/perfsales/modular/bibdbs/request-download",
          "Performance by Item by Store by Day", True),
}

# The "opt" field is the indicator dropdown on these two sections.
INDICATOR = {"a": "Selling Out", "b": "Stok"}
UNIT = {"q": "Qty", "v": "Value"}

# Fallback only - the real list is read off the page at runtime.
FALLBACK_CATEGORIES = {
    "3251": "BODY LOTION",
    "3252": "BODY SERUM",
    "3241": "FACIAL WASH SOAP",
    "3239": "MEN PARFUME EDT & EXTRAIT",
    "3240": "SUNSCREEN",
    "3232": "WOMEN PARFUME EDT & EXTRAIT",
}

log = logging.getLogger("alfamidi_perfsales")


def setup_logging(verbose: bool) -> None:
    log.setLevel(logging.DEBUG if verbose else logging.INFO)
    fmt = logging.Formatter("%(asctime)s  %(levelname)-7s %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)
    log.addHandler(stream)
    handler = logging.FileHandler(SCRIPT_DIR / "run.log", encoding="utf-8")
    handler.setFormatter(fmt)
    log.addHandler(handler)


def branch_windows(spec: str) -> list[tuple[str, str, str]]:
    """(tgla, tglb, label) per by-branch request - month-to-date + prev month.

    Straight from period_utils.branch_months(). Previous month is re-requested
    every day on purpose: late-posted transactions get backdated into a closed
    month, and this is what catches them.
    """
    windows = []
    for i, m in enumerate(branch_months(date.today())):
        if i == 0 and spec not in ("current", "both"):
            continue
        if i == 1 and spec not in ("previous", "both"):
            continue
        kind = "MTD" if i == 0 else "full"
        windows.append((m["start"].isoformat(), m["end"].isoformat(),
                        f"{m['label'][:4]}-{m['label'][4:]} {kind}"))
    return windows


def store_windows(n: int) -> list[tuple[str, str, str]]:
    """(tgla, tglb, label) per by-store request - 10-day periods, newest first.

    By-store uses a completely different calendar from by-branch: periods of
    1-10, 11-20, 21-end, per period_utils. This is not cosmetic. The downstream
    `3_upload_and_distribute.py` derives each file's period from its start date
    and deletes anything whose end date overshoots that period's real boundary
    as "invalid (past period end)" - so a month-to-date by-store file starting
    on the 1st and ending mid-month gets thrown away.
    """
    windows = []
    for p in rolling_periods(date.today(), n):
        label = f"{p['year']:04d}-{p['month']:02d} P{p['idx']}"
        if p["is_latest"]:
            label += " (in progress)"
        windows.append((p["start"].isoformat(), p["end"].isoformat(), label))
    return windows


def slug(text: str) -> str:
    """Portal filenames use underscores and no punctuation."""
    return re.sub(r"_+", "_", re.sub(r"[^A-Za-z0-9]+", "_", text)).strip("_")


def login(page, username: str, password: str) -> None:
    log.info("Opening login page")
    page.goto(f"{BASE}/login.php", wait_until="domcontentloaded", timeout=60_000)

    page.fill('input[name="uname"]', username)
    page.fill('input[name="upass"]', password)
    log.info("Submitting credentials for %s", username)
    with page.expect_navigation(wait_until="domcontentloaded", timeout=60_000):
        page.press('input[name="upass"]', "Enter")

    # Same race as the Market Share script: "Login Berhasil" is a transient page
    # that JS-redirects to index.php, so look for the authenticated home page
    # rather than that message.
    try:
        page.wait_for_url("**/index.php*", timeout=15_000)
    except PWTimeout:
        pass
    page.wait_for_load_state("networkidle", timeout=15_000)

    body = page.content()
    logged_in = "logout" in body.lower() and 'name="upass"' not in body.lower()
    if not logged_in and "login berhasil" not in body.lower():
        snippet = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body)).strip()[:200]
        raise SystemExit("Login rejected - check ALFAMIDI_USERNAME / "
                         f"ALFAMIDI_PASSWORD. Server said: {snippet!r}")
    log.info("Logged in (now at %s)", page.url)


def open_perfsales(page):
    """Follow the 'Laporan > Dashboard & Modular' handoff, then the perf page."""
    log.info("Handing session over to %s", NP)
    try:
        with page.context.expect_page(timeout=8_000) as popup:
            page.goto(f"{BASE}/get_laporan_new_premium.php",
                      wait_until="domcontentloaded", timeout=60_000)
        page = popup.value
        page.wait_for_load_state("domcontentloaded", timeout=60_000)
        log.info("Handoff opened a new tab: %s", page.url)
    except PWTimeout:
        pass  # no popup - the redirect happened in place

    page.goto(PERF_PAGE, wait_until="domcontentloaded", timeout=60_000)
    if "performancesales-modular" not in page.url:
        raise SystemExit(f"Could not reach the Performance Sales page (at {page.url})")
    log.info("Performance Sales modular page ready")
    return page


def discover_categories(page) -> dict[str, str]:
    """Read the category dropdown off the live page.

    Deliberately not hardcoded: Alfamidi lists 6 categories today, and a new one
    must be picked up automatically rather than silently missed. Every
    "category-filter-report-modular-N" select carries the same list, so the
    first one that yields real codes wins.
    """
    html = page.content()
    found: dict[str, str] = {}

    for m in re.finditer(
            r'<select[^>]*(?:id|name)\s*=\s*["\']category-filter-report-modular-[^"\']*["\'][^>]*>(.*?)</select>',
            html, re.I | re.S):
        for value, text in re.findall(
                r'<option[^>]*value\s*=\s*["\']([^"\']*)["\'][^>]*>(.*?)</option>',
                m.group(1), re.I | re.S):
            if value.lower() == "all" or not value.strip():
                continue
            label = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", text))
            found[value] = _unescape(label.strip())
        if found:
            break

    if not found:
        log.warning("Could not read the category dropdown - falling back to the "
                    "6 known codes. A newly added category would be MISSED; "
                    "check the page layout.")
        return dict(FALLBACK_CATEGORIES)

    log.info("Discovered %d categories: %s", len(found),
             ", ".join(sorted(found.values())))
    known = set(FALLBACK_CATEGORIES)
    for code in sorted(set(found) - known):
        log.info("  new category since this script was written: %s = %s",
                 code, found[code])
    return found


def _unescape(text: str) -> str:
    import html as _html
    return _html.unescape(text)


def plan_requests(reports: list[str], categories: dict[str, str],
                  units: list[str],
                  branch_wins: list[tuple[str, str, str]],
                  store_wins: list[tuple[str, str, str]]) -> list[tuple]:
    """Every (report, category, unit, window) combination to request.

    The two report types use different calendars and different category
    handling, so they cannot share a window list:

      by-branch  all categories in one file, month-to-date + previous month
                 -> 1 cat x 2 units x 2 months  =  4
      by-store   one request per category, 10-day periods
                 -> 6 cats x 2 units x 3 periods = 36

    New categories flow straight through, so a 7th would make it 42.
    """
    planned = []
    for tipe_prf in reports:
        if tipe_prf == "5":
            cats, windows = [("all", "All Category")], branch_wins
        else:
            cats = sorted(categories.items(), key=lambda kv: kv[1])
            windows = store_wins
        for tgla, tglb, wlabel in windows:
            for cat, cat_text in cats:
                for unit in units:
                    planned.append((tipe_prf, cat, cat_text, unit,
                                    tgla, tglb, wlabel))
    return planned


def build_payload(tipe_prf: str, cat: str, cat_text: str, unit: str,
                  tgla: str, tglb: str, opt: str, token: str) -> dict:
    """Form fields exactly as the page posts them.

    Values here are DECODED. The HAR shows them percent-encoded on the wire
    ("Selling+Out", "BODY+LOTION") because that is how a form body looks -
    Playwright encodes them again, so passing encoded values would double-encode.
    """
    unit_text = UNIT[unit]
    ind_text = INDICATOR[opt]
    _, _, needs_store = REPORTS[tipe_prf]

    if needs_store:
        stem = (f"detail_performance_{slug(ind_text)}_{slug(unit_text)}"
                f"_{slug(cat_text)}_All_Item_NASIONAL_All_Store")
    else:
        stem = (f"detail_performance_by_branch_{slug(ind_text)}_{slug(unit_text)}"
                f"_{slug(cat_text)}_All_Item_NASIONAL")

    payload = {
        "tipe_prf": tipe_prf,
        "opt": opt,
        "u": unit,
        "br": "nas",
        "cat": cat,
        "plu": "all",
        "tgla": tgla,
        "tglb": tglb,
        "indicatortext": ind_text,
        "unittext": unit_text,
        "categorytext": cat_text,
        "itemtext": "All Item",
        "branchtext": "NASIONAL",
        # The token is what lets Mail-ReportLinks tie the emailed file back to
        # this request - see requests.json.
        "filename": f"{stem}_{tgla.replace('-', '')}_{tglb.replace('-', '')}_{token}",
    }
    if needs_store:
        payload["st"] = "all"
        payload["storetext"] = "All Store"
    return payload


# The portal refuses a repeat of the same report inside an hour. Confirmed live
# on 2026-09-18, the reply is:
#
#   {"code": "T",
#    "result": "Request Download File Sudah diajukan dalam 1 jam terakhir
#               \nsilahkan cek email untuk download file atau menunggu request
#               file sebelumnya selesai"}
#
# Note the code is "T" - byte for byte the SAME success code a genuine queue
# returns. Nothing in the status or the code distinguishes the two, so matching
# on the message text is the only way to tell them apart, and it has to happen
# BEFORE the code check below. Get this wrong and a throttled request is
# recorded as queued, leaving a token in requests.json waiting on an email that
# is never sent.
THROTTLE_HINTS = ("sudah diajukan", "jam terakhir", "1 jam")

QUEUED, THROTTLED, FAILED = "queued", "throttled", "failed"


def classify_reply(text: str) -> str:
    """Decide what the portal's answer means. Pure, so it can be tested.

    Raises SystemExit if the session has lapsed.
    """
    if "login" in text[:400].lower() and "berhasil" not in text.lower():
        raise SystemExit("Session expired - the server returned the login page.")

    lowered = text.lower()
    # Deliberately ahead of the JSON/code check - see THROTTLE_HINTS above.
    if any(hint in lowered for hint in THROTTLE_HINTS):
        return THROTTLED

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return FAILED

    if str(data.get("code", "")).upper() != "T":
        return FAILED
    return QUEUED


def request_one(page, tipe_prf: str, payload: dict) -> str:
    endpoint, label, _ = REPORTS[tipe_prf]
    resp = page.request.post(
        endpoint,
        form=payload,
        headers={
            "Referer": PERF_PAGE,
            "Origin": NP,
            "X-Requested-With": "XMLHttpRequest",
        },
        timeout=180_000,
    )
    if not resp.ok:
        log.error("  HTTP %d for %s", resp.status, payload["filename"])
        return FAILED

    text = resp.text()
    status = classify_reply(text)

    if status == THROTTLED:
        log.info("  throttled - already requested within the last hour, "
                 "no new email will be sent")
        log.debug("  throttle reply verbatim: %s", text[:300])
    elif status == FAILED:
        log.error("  refused for %s: %s", payload["filename"], text[:200])
    else:
        log.info("  queued: %s", payload["filename"])
    return status


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Queue Alfamidi Performance Sales reports (delivered by email).")
    p.add_argument("--report", choices=["branch", "store", "both"], default="both",
                   help="branch = by-branch only, store = per-category by-store")
    p.add_argument("--months", choices=["current", "previous", "both"],
                   default="both",
                   help="by-branch only: which month windows (MTD / previous)")
    p.add_argument("--periods", type=int, default=3,
                   help="by-store only: how many 10-day periods back, newest "
                        "first (default 3, matching period_utils.rolling_periods)")
    p.add_argument("--unit", choices=["q", "v", "both"], default="both")
    p.add_argument("--indicator", choices=["a", "b"], default="a",
                   help="a = Selling Out (default), b = Stok")
    p.add_argument("--categories",
                   help="comma-separated category codes; default is every "
                        "category found on the page")
    p.add_argument("--delay", type=float, default=2.0,
                   help="seconds between requests (default 2)")
    p.add_argument("--dry-run", action="store_true",
                   help="list what would be requested, send nothing")
    p.add_argument("--headful", action="store_true", help="show the browser")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args(argv)

    setup_logging(args.verbose)
    load_dotenv(SCRIPT_DIR / ".env")

    username = os.getenv("ALFAMIDI_USERNAME", "").strip()
    password = os.getenv("ALFAMIDI_PASSWORD", "").strip()
    if not username or not password:
        return _fail("ALFAMIDI_USERNAME / ALFAMIDI_PASSWORD missing from .env")

    units = ["q", "v"] if args.unit == "both" else [args.unit]
    branch_wins = branch_windows(args.months)
    store_wins = store_windows(args.periods)
    reports = {"branch": ["5"], "store": ["4"], "both": ["5", "4"]}[args.report]

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not args.headful)
        page = browser.new_page()
        try:
            login(page, username, password)
            page = open_perfsales(page)

            if args.categories:
                codes = [c.strip() for c in args.categories.split(",") if c.strip()]
                discovered = discover_categories(page)
                categories = {c: discovered.get(c, c) for c in codes}
            else:
                categories = discover_categories(page)

            planned = plan_requests(reports, categories, units,
                                    branch_wins, store_wins)

            log.info("%d report(s) to request", len(planned))
            if "5" in reports:
                log.info("  by-branch windows: %s",
                         ", ".join(w[2] for w in branch_wins))
            if "4" in reports:
                log.info("  by-store  periods: %s",
                         ", ".join(w[2] for w in store_wins))

            if args.dry_run:
                for tipe_prf, cat, cat_text, unit, tgla, tglb, wlabel in planned:
                    token = "DRYRUN"
                    payload = build_payload(tipe_prf, cat, cat_text, unit,
                                            tgla, tglb, args.indicator, token)
                    log.info("  WOULD REQUEST [%s] %s", REPORTS[tipe_prf][1],
                             payload["filename"])
                log.info("DRY RUN - nothing was sent.")
                return 0

            manifest = _load_manifest()
            ok = bad = skipped = 0
            for i, (tipe_prf, cat, cat_text, unit, tgla, tglb, wlabel) in enumerate(planned, 1):
                token = secrets.token_hex(3)
                payload = build_payload(tipe_prf, cat, cat_text, unit,
                                        tgla, tglb, args.indicator, token)
                log.info("[%d/%d] %s | %s | %s | %s",
                         i, len(planned), REPORTS[tipe_prf][1], cat_text,
                         UNIT[unit], wlabel)
                try:
                    status = request_one(page, tipe_prf, payload)
                except SystemExit:
                    raise
                except Exception as exc:
                    log.error("  failed: %s", exc)
                    status = FAILED

                if status == THROTTLED:
                    # No email is coming, so recording a token would leave an
                    # orphan in requests.json that never resolves.
                    skipped += 1
                elif status == QUEUED:
                    ok += 1
                    manifest[token] = {
                        "filename": payload["filename"],
                        "report": REPORTS[tipe_prf][1],
                        "tipe_prf": tipe_prf,
                        "category": cat_text,
                        "category_code": cat,
                        "unit": UNIT[unit],
                        "indicator": INDICATOR[args.indicator],
                        "window": wlabel,
                        "tgla": tgla,
                        "tglb": tglb,
                        "requested_at": _now(),
                    }
                    _save_manifest(manifest)
                else:
                    bad += 1

                if args.delay and i < len(planned):
                    time.sleep(args.delay)

            summary = f"Done - {ok} queued, {skipped} throttled, {bad} failed."
            log.info("%s Links arrive by email; ..\\Mail-ReportLinks collects "
                     "them.", summary)
            if skipped and not ok:
                log.info("Everything was throttled, so this run was a no-op - "
                         "the same reports were already requested within the "
                         "hour. That is not an error.")
            # Throttling is a benign no-op, so it must not turn the scheduled
            # task red - only genuine failures with nothing queued do that.
            return 1 if bad and not ok else 0
        finally:
            browser.close()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _load_manifest() -> dict:
    if not MANIFEST.exists():
        return {}
    try:
        return json.loads(MANIFEST.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("Could not read requests.json (%s) - starting fresh.", exc)
        return {}


def _save_manifest(manifest: dict) -> None:
    # Written after every request, not at the end: if the run dies halfway, the
    # tokens already sent must still be resolvable when their emails arrive.
    try:
        MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    except OSError as exc:
        log.error("Could not write requests.json: %s", exc)


def _fail(message: str) -> int:
    log.error(message)
    return 2


if __name__ == "__main__":
    sys.exit(main())
