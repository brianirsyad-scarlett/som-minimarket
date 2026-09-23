"""
Alfamidi B2B - Dashboard & Modular auto request-download.

Replays the exact browser flow captured in the HARs:

  1. POST https://b2b.alfamidiku.com/login.php          (uname / upass)
  2. GET  /get_laporan_new_premium.php                  -> one-time SSO token
  3. GET  https://midi-b2b.et.r.appspot.com/login-authentication?token=...
  4. GET  /performancesales-modular                     (scrape live category list)
  5. POST /perfsales/modular/bibdbs/by-branch           tipe_prf=5  "by Item by Branch by Day"
     POST /perfsales/modular/bibdbs/request-download    tipe_prf=4  "by Item by Store by Day"

The site does NOT return the file. Each accepted request is queued server-side and
the download link is emailed to the B2B account once the file is ready
("Link Download File Akan dikirim Via Email Jika File Sudah Tersedia").

Requests fired per run:
    by branch          : 2   (Value, Qty)  - cat=all
    by store by category: 2 x <number of categories>  (Value, Qty per category)

Usage:
    python alfamidi_b2b_auto.py                       # month-to-date, all categories
    python alfamidi_b2b_auto.py --dry-run             # show payloads, send nothing
    python alfamidi_b2b_auto.py --start 2026-09-01 --end 2026-09-12
    python alfamidi_b2b_auto.py --yesterday           # single day
    python alfamidi_b2b_auto.py --only by-branch      # or: by-store
    python alfamidi_b2b_auto.py --category 3251 --category 3240

Credentials come from env vars B2B_MIDI_USER / B2B_MIDI_PASS, else the constants below.
"""

import argparse
import html
import os
import re
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import requests

# --- CONFIGURATION ---
def _cfg(key):
    """A login setting: environment variable first, then ~/.b2b_email.env.

    These used to be hardcoded as os.environ.get() fallbacks - and because the
    scheduled tasks set no environment variables, the hardcoded values were what
    actually ran every day. They were moved to the env file (outside the repo)
    before this code went to GitHub. No silent default: a missing key fails the
    run loudly instead of logging in with something stale.
    """
    val = os.environ.get(key)
    if val:
        return val
    env_file = Path.home() / ".b2b_email.env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line.startswith(key + "="):
                val = line.split("=", 1)[1].strip()
                if val:
                    return val
    raise SystemExit(f"Missing {key}: set it in the environment or in {env_file}")


B2B_USER = _cfg("B2B_MIDI_USER")
B2B_PASS = _cfg("B2B_MIDI_PASS")

B2B_BASE = "https://b2b.alfamidiku.com"
MIDI_BASE = "https://midi-b2b.et.r.appspot.com"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)

# Fixed filter values, matching what the browser sends for these two reports.
INDICATOR = ("a", "Selling Out")          # the only indicator these two forms offer
BRANCH = ("nas", "NASIONAL")              # whole country
ITEM = ("all", "All Item")
STORE = ("all", "All Store")
UNITS = [("v", "Value"), ("q", "Qty")]

MAX_RANGE_DAYS = 31                       # "Periode (Max 31 days)" on both forms
SLEEP_BETWEEN_REQUESTS = 2.0              # be gentle; the browser paced ~4-6s apart

TIMEOUT = 180


# --------------------------------------------------------------------------- #
# session / login
# --------------------------------------------------------------------------- #
def open_session():
    """Log into B2B, ride the SSO token over to the appspot dashboard."""
    s = requests.Session()
    s.headers.update({"User-Agent": UA})

    print("--- Login b2b.alfamidiku.com ---")
    s.get(f"{B2B_BASE}/login.php", timeout=TIMEOUT)
    r = s.post(
        f"{B2B_BASE}/login.php",
        data={"uname": B2B_USER, "upass": B2B_PASS},
        headers={"Referer": f"{B2B_BASE}/login.php", "Origin": B2B_BASE},
        timeout=TIMEOUT,
    )
    if "Login Berhasil" not in r.text:
        raise SystemExit(f"Login failed for {B2B_USER}. Response:\n{r.text[:500]}")
    print(f"Logged in as {B2B_USER}")

    print("--- Laporan > Dashboard & Modular ---")
    r = s.get(
        f"{B2B_BASE}/get_laporan_new_premium.php",
        headers={"Referer": f"{B2B_BASE}/index.php"},
        timeout=TIMEOUT,
    )
    m = re.search(r'window\.location\.replace\("([^"]+)"\)', r.text)
    if not m:
        raise SystemExit(f"No SSO token in get_laporan_new_premium.php:\n{r.text[:500]}")
    token_url = m.group(1)
    print(f"SSO token -> {token_url.split('token=')[-1]}")

    r = s.get(token_url, timeout=TIMEOUT)
    if not s.cookies.get("session", domain="midi-b2b.et.r.appspot.com"):
        raise SystemExit("SSO handshake did not return an appspot session cookie.")
    print(f"Dashboard session established ({r.url})")
    return s


def load_modular_page(s):
    """Fetch the Performance Sales Modular page; returns its HTML."""
    r = s.get(
        f"{MIDI_BASE}/performancesales-modular",
        headers={"Referer": f"{MIDI_BASE}/index"},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    return r.text


def scrape_csrf_token(page_html):
    """
    The page's inline script does $.ajaxSetup({beforeSend: ... 'X-CSRFToken' ...})
    with a per-session token. The browser sends this on every POST; our script
    didn't, which may be why requests were being silently dropped server-side
    despite the "Berhasil" acknowledgment.
    """
    m = re.search(r'var\s+csrf_token\s*=\s*"([^"]+)"', page_html)
    return m.group(1) if m else None


def scrape_categories(page_html):
    """
    Read the category dropdown of the 'by Item by Store by Day' form
    (frm-filter-report-modular-4) so new categories are picked up automatically.
    """
    form = re.search(
        r'<form id="frm-filter-report-modular-4".*?</form>', page_html, re.S
    )
    if not form:
        raise SystemExit("Could not find frm-filter-report-modular-4 on the page.")
    sel = re.search(r'name="category".*?</select>', form.group(0), re.S)
    if not sel:
        raise SystemExit("Could not find the category dropdown in form 4.")

    cats = []
    for value, label in re.findall(
        r'<option value="([^"]+)">([^<]+)</option>', sel.group(0)
    ):
        if value == "all":          # form 4 has no "All Category" option
            continue
        cats.append((value, html.unescape(label).strip()))
    if not cats:
        raise SystemExit("Category dropdown came back empty.")
    return cats


# --------------------------------------------------------------------------- #
# payload building
# --------------------------------------------------------------------------- #
def _fname_part(text):
    """Mirror the site's JS: spaces -> underscores, parentheses dropped."""
    return re.sub(r"\s", "_", text).replace("(", "").replace(")", "")


def build_by_branch(unit, tgla, tglb):
    """tipe_prf=5 - Performance by Item by Branch by Day, all categories."""
    u, unit_text = unit
    texts = [INDICATOR[1], unit_text, "All Category", ITEM[1], BRANCH[1]]
    return {
        "tipe_prf": "5",
        "opt": INDICATOR[0],
        "u": u,
        "br": BRANCH[0],
        "cat": "all",
        "plu": ITEM[0],
        "tgla": tgla,
        "tglb": tglb,
        "indicatortext": INDICATOR[1],
        "unittext": unit_text,
        "categorytext": "All Category",
        "itemtext": ITEM[1],
        "branchtext": BRANCH[1],
        "filename": "detail_performance_by_branch_"
        + "_".join(_fname_part(t) for t in texts),
    }


def build_by_store(unit, category, tgla, tglb):
    """tipe_prf=4 - Performance by Item by Store by Day, one category."""
    u, unit_text = unit
    cat_id, cat_text = category
    texts = [INDICATOR[1], unit_text, cat_text, ITEM[1], BRANCH[1], STORE[1]]
    return {
        "tipe_prf": "4",
        "opt": INDICATOR[0],
        "u": u,
        "br": BRANCH[0],
        "cat": cat_id,
        "plu": ITEM[0],
        "tgla": tgla,
        "tglb": tglb,
        "st": STORE[0],
        "indicatortext": INDICATOR[1],
        "unittext": unit_text,
        "categorytext": cat_text,
        "itemtext": ITEM[1],
        "branchtext": BRANCH[1],
        "storetext": STORE[1],
        "filename": "detail_performance_"
        + "_".join(_fname_part(t) for t in texts),
    }


# --------------------------------------------------------------------------- #
# firing
# --------------------------------------------------------------------------- #
def post_request_download(s, path, payload, csrf_token=None):
    headers = {
        "Referer": f"{MIDI_BASE}/performancesales-modular",
        "Origin": MIDI_BASE,
        "X-Requested-With": "XMLHttpRequest",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    }
    if csrf_token:
        headers["X-CSRFToken"] = csrf_token
    r = s.post(f"{MIDI_BASE}/{path}", data=payload, headers=headers, timeout=TIMEOUT)
    r.raise_for_status()
    try:
        body = r.json()
    except ValueError:
        return False, r.text.strip()[:200]
    return body.get("code") == "T", str(body.get("result", "")).replace("\n", " ").strip()


def prime_category(s, cat_id, csrf_token=None):
    """
    The browser hits these two when the category dropdown changes. They do not
    affect the queued file, but keeping them makes the traffic look identical.
    """
    headers = {
        "Referer": f"{MIDI_BASE}/performancesales-modular",
        "Origin": MIDI_BASE,
        "X-Requested-With": "XMLHttpRequest",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    }
    if csrf_token:
        headers["X-CSRFToken"] = csrf_token
    for path in ("xhr/listitembycat", "perfsales/check-cat-sigaret"):
        try:
            s.post(
                f"{MIDI_BASE}/{path}",
                data={"cat": cat_id},
                headers=headers,
                timeout=TIMEOUT,
            )
        except requests.RequestException:
            pass


# --------------------------------------------------------------------------- #
# dates
# --------------------------------------------------------------------------- #
def resolve_dates(args):
    if args.yesterday:
        d = date.today() - timedelta(days=1)
        return d.isoformat(), d.isoformat()
    if args.today:
        d = date.today()
        return d.isoformat(), d.isoformat()

    today = date.today()
    start = date.fromisoformat(args.start) if args.start else today.replace(day=1)
    end = date.fromisoformat(args.end) if args.end else today

    if end < start:
        raise SystemExit(f"--end {end} is before --start {start}")
    span = (end - start).days + 1
    if span > MAX_RANGE_DAYS:
        raise SystemExit(
            f"Range is {span} days; the site caps these reports at {MAX_RANGE_DAYS}. "
            "Split it into shorter runs."
        )
    return start.isoformat(), end.isoformat()


# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", help="YYYY-MM-DD (default: 1st of current month)")
    ap.add_argument("--end", help="YYYY-MM-DD (default: today)")
    ap.add_argument("--yesterday", action="store_true", help="single day: yesterday")
    ap.add_argument("--today", action="store_true", help="single day: today")
    ap.add_argument("--only", choices=["by-branch", "by-store"],
                    help="fire only one of the two report families")
    ap.add_argument("--category", action="append", dest="categories",
                    help="limit by-store to this category id (repeatable)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the payloads without sending them")
    ap.add_argument("--limit", type=int,
                    help="fire only the first N jobs (for a single-shot verification)")
    ap.add_argument("--skip", type=int, default=0,
                    help="skip the first N jobs (e.g. ones already fired)")
    args = ap.parse_args()

    tgla, tglb = resolve_dates(args)

    s = open_session()
    page = load_modular_page(s)
    csrf_token = scrape_csrf_token(page)
    print(f"CSRF token   : {'found' if csrf_token else 'NOT FOUND - requests may be silently dropped server-side'}")
    categories = scrape_categories(page)
    if args.categories:
        wanted = set(args.categories)
        categories = [c for c in categories if c[0] in wanted]
        missing = wanted - {c[0] for c in categories}
        if missing:
            raise SystemExit(f"Unknown category id(s): {', '.join(sorted(missing))}")

    print(f"\nPeriode      : {tgla} .. {tglb}")
    print(f"Categories   : {len(categories)} -> "
          + ", ".join(f"{cid} {name}" for cid, name in categories))

    jobs = []
    if args.only != "by-store":
        for unit in UNITS:
            jobs.append(
                ("perfsales/modular/bibdbs/by-branch",
                 f"By Branch / Daily / {unit[1]}",
                 build_by_branch(unit, tgla, tglb),
                 None)
            )
    if args.only != "by-branch":
        for cat in categories:
            for unit in UNITS:
                jobs.append(
                    ("perfsales/modular/bibdbs/request-download",
                     f"By Store by Category / Daily / {unit[1]} / {cat[1]}",
                     build_by_store(unit, cat, tgla, tglb),
                     cat[0])
                )

    if args.skip:
        jobs = jobs[args.skip:]
    if args.limit:
        jobs = jobs[: args.limit]
    print(f"Requests     : {len(jobs)}"
          + ("   [DRY RUN - nothing will be sent]" if args.dry_run else ""))
    print()

    ok = fail = 0
    last_cat = None
    for i, (path, label, payload, cat_id) in enumerate(jobs, 1):
        print(f"[{i}/{len(jobs)}] {label}")
        if args.dry_run:
            print(f"          POST /{path}")
            for k, v in payload.items():
                print(f"            {k} = {v}")
            continue

        if cat_id and cat_id != last_cat:
            prime_category(s, cat_id, csrf_token)
            last_cat = cat_id

        try:
            success, msg = post_request_download(s, path, payload, csrf_token)
        except requests.RequestException as exc:
            success, msg = False, f"{type(exc).__name__}: {exc}"

        if success:
            ok += 1
            # Log the portal's own wording, not just "OK". It distinguishes a
            # freshly queued export ("Laporan akan dikirim melalui email") from
            # one the portal is refusing to rebuild because it already produced
            # it recently ("Laporan telah didownload ... lalu"). Both come back
            # code=T, so without this the log cannot tell them apart.
            print(f"          OK   {payload['filename']}")
            if msg:
                print(f"               portal: {msg}")
        else:
            fail += 1
            print(f"          FAIL {msg}")

        if i < len(jobs):
            time.sleep(SLEEP_BETWEEN_REQUESTS)

    if not args.dry_run:
        print(f"\n--- Done: {ok} queued, {fail} failed ---")
        print("Download links arrive by email on the B2B account once each file is built.")
        if fail:
            sys.exit(1)


if __name__ == "__main__":
    main()
