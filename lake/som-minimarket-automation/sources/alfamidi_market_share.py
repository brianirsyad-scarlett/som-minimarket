"""
Alfamidi B2B - Market Share (Modular) automated downloader.

Flow reproduced from the HAR captures:
  1. POST b2b.alfamidiku.com/login.php               (uname, upass - no CSRF token, no 2FA)
     -> response body is "Login Berhasil" + a JS redirect to index.php
  2. GET  b2b.alfamidiku.com/get_laporan_new_premium.php  -> hands session to the appspot app
  3. GET  midi-b2b.et.r.appspot.com/marketshare-modular
  4. POST midi-b2b.et.r.appspot.com/marketshare/modular/xls  -> xlsx bytes

Differences from the Alfamart automation this was copied from:
  - No 2FA / OTP step at all.
  - No CSRF "token" field on login.
  - The download form calls the branch field "area"/"areatext" (Alfamart calls it
    "branch"/"branchtext"), and "tipe-area" holds the literal label ("BRANCH"/"REGIONAL")
    instead of a short code ("DC"/"REG").
  - Filename field order is unit/group/format/year/periode/... (Alfamart is
    unit/group/year/periode/format/...).
  - Alfamidi exposes only 6 categories vs Alfamart's 13, and its own set of branch codes.

Credentials are read from .env and never leave this machine.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    from dotenv import load_dotenv
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
except ImportError as exc:
    sys.exit(f"Missing dependency ({exc.name}). Run setup.ps1 first.")

BASE = "https://b2b.alfamidiku.com"
NP = "https://midi-b2b.et.r.appspot.com"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_OUT = Path(
    r"D:\SCARLETT_512\SCARLETT-329\SOM\Data\Report\Sales\Minimarket\Alfamidi\Market Share\Raw"
)

# --- value -> label maps, lifted from the live <select> elements -------------
UNIT = {"v": "Value"}
GROUP = {"BRAND": "By Brand", "PLU": "By Item"}
YEAR = {"ACT": "Actual", "LAST": "Last Year"}
FORMAT = {"MTD": "MTD", "YTD": "YTD"}
AREA_TYPE = {"BRANCH": "BRANCH", "REG": "REGIONAL"}
REPORT = {
    "1": "Market Share Total Item by Month (Selling Out)",
    "2": "Market Share by Category by Month by Branch",
}
CATEGORY = {
    "3251": "BODY LOTION",
    "3252": "BODY SERUM",
    "3241": "FACIAL WASH SOAP",
    "3239": "MEN PARFUME EDT & EXTRAIT",
    "3240": "SUNSCREEN",
    "3232": "WOMEN PARFUME EDT & EXTRAIT",
}
AREA = {
    "NAS": "NASIONAL", "SFZ1": "DC MIDI MEDAN", "SHZ1": "DC MIDI BITUNG",
    "SCZ1": "DC MIDI BEKASI", "SLZ1": "DC MIDI PASURUAN", "SZZ1": "DC MIDI CDC PASURUAN",
    "SQZ1": "DC MIDI BOYOLALI", "SGZ1": "DC MIDI SAMARINDA", "SEZ1": "DC MIDI MAKASAR",
    "SOZ1": "DC MIDI PALU 2", "SSZ1": "DC MIDI MANADO 2", "SRZ1": "BRANCH KENDARI 2",
    "SNZ1": "DC MIDI AMBON",
}

log = logging.getLogger("alfamidi")


def period_label(periode: str) -> str:
    return datetime.strptime(periode, "%Y-%m").strftime("%b-%Y")


def add_month(periode: str, n: int = 1) -> str:
    d = datetime.strptime(periode, "%Y-%m")
    month = d.month - 1 + n
    year = d.year + month // 12
    month = month % 12 + 1
    return f"{year:04d}-{month:02d}"


def expand_periods(spec: str) -> list[str]:
    """'2026-09' | '2026-01:2026-09' | '2026-01,2026-03,2026-09' | 'all' (Jan -> current
    month) | 'recent' (previous month + current month, for a daily catch-up run)."""
    if spec == "all":
        spec = f"2026-01:{datetime.now():%Y-%m}"
    if spec == "recent":
        cur = f"{datetime.now():%Y-%m}"
        return [add_month(cur, -1), cur]

    if ":" in spec:
        start, end = spec.split(":", 1)
        for s in (start, end):
            if not re.fullmatch(r"\d{4}-\d{2}", s):
                raise ValueError(f"Bad month in range {spec!r}: {s!r}")
        periods, cur = [], start
        while cur <= end:
            periods.append(cur)
            if cur == end:
                break
            cur = add_month(cur)
        if not periods or periods[-1] != end:
            raise ValueError(f"Range {spec!r} is out of order (start must be <= end)")
        return periods

    periods = [s.strip() for s in spec.split(",") if s.strip()]
    for s in periods:
        if not re.fullmatch(r"\d{4}-\d{2}", s):
            raise ValueError(f"--periode must look like 2026-09 (got {s!r})")
    return periods


def safe_filename(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*]', "_", name).strip()


def build_payload(a: argparse.Namespace, category: str, periode: str) -> dict:
    """Recreate exactly the form the Download button posts."""
    labels = {
        "unittext": UNIT[a.unit],
        "grouptext": GROUP[a.group],
        "formattext": FORMAT[a.format],
        "yeartext": YEAR[a.year],
        "periodetext": period_label(periode),
        "categorytext": CATEGORY[category],
        "tipe-areatext": AREA_TYPE[a.area_type],
        "areatext": AREA[a.area],
    }
    stem = "_".join([
        REPORT[a.report],
        labels["unittext"], labels["grouptext"], labels["formattext"], labels["yeartext"],
        labels["periodetext"], labels["categorytext"],
        labels["tipe-areatext"], labels["areatext"],
    ])
    return {
        "unit": a.unit,
        "group": a.group,
        "format": a.format,
        "year": a.year,
        "periode": periode,
        "category": category,
        "tipe-area": a.area_type,
        "area": a.area,
        **labels,
        "tipe_prf": a.report,
        # The server appends ".xlsx" itself - sending it here too doubles the extension.
        "filename": stem,
    }


def login(page, username: str, password: str) -> None:
    log.info("Opening login page")
    page.goto(f"{BASE}/login.php", wait_until="domcontentloaded", timeout=60_000)

    page.fill('input[name="uname"]', username)
    page.fill('input[name="upass"]', password)
    log.info("Submitting credentials for %s", username)
    with page.expect_navigation(wait_until="domcontentloaded", timeout=60_000):
        page.press('input[name="upass"]', "Enter")

    # The POST response is a transient "Login Berhasil" page that immediately
    # JS-redirects to index.php - by the time we look, we may already be on
    # index.php, so checking for the literal "Login Berhasil" text is a race.
    # Check for signs of the authenticated home page instead (has "Logout",
    # no login form) rather than the transient message.
    try:
        page.wait_for_url("**/index.php*", timeout=15_000)
    except PWTimeout:
        pass
    page.wait_for_load_state("networkidle", timeout=15_000)

    body = page.content()
    logged_in = "logout" in body.lower() and 'name="upass"' not in body.lower()
    if not logged_in and "login berhasil" not in body.lower():
        snippet = re.sub(r"<[^>]+>", " ", body)
        snippet = re.sub(r"\s+", " ", snippet).strip()[:200]
        raise SystemExit(f"Login rejected - check ALFAMIDI_USERNAME / ALFAMIDI_PASSWORD. "
                          f"Server said: {snippet!r}")
    log.info("Logged in (now at %s)", page.url)


def open_modular(page) -> None:
    """Follow the 'Laporan > Dashboard & Modular' handoff into the appspot app."""
    log.info("Handing session over to %s", NP)
    try:
        with page.context.expect_page(timeout=8_000) as popup:
            page.goto(f"{BASE}/get_laporan_new_premium.php",
                      wait_until="domcontentloaded", timeout=60_000)
        new_page = popup.value
        new_page.wait_for_load_state("domcontentloaded", timeout=60_000)
        page = new_page
        log.info("Handoff opened a new tab: %s", new_page.url)
    except PWTimeout:
        pass  # no popup, the redirect happened in place

    page.goto(f"{NP}/marketshare-modular", wait_until="domcontentloaded", timeout=60_000)
    if "marketshare-modular" not in page.url:
        raise SystemExit(f"Could not reach the Market Share page (at {page.url})")
    log.info("Market Share modular page ready")
    return page


def download_one(page, payload: dict, out_dir: Path) -> Path:
    label = payload["categorytext"]
    log.info("Requesting: %s / %s / %s", label, payload["periodetext"], payload["formattext"])

    resp = page.request.post(
        f"{NP}/marketshare/modular/xls",
        form=payload,
        headers={
            "Referer": f"{NP}/marketshare-modular",
            "Origin": NP,
            "X-Requested-With": "XMLHttpRequest",
        },
        timeout=180_000,
    )
    if not resp.ok:
        raise RuntimeError(f"HTTP {resp.status} for {label}")

    ctype = (resp.headers.get("content-type") or "").split(";")[0]
    body = resp.body()
    if ctype != XLSX_MIME or body[:2] != b"PK":
        if b"login" in body[:2000].lower():
            raise RuntimeError("Session expired - the server returned the login page.")
        raise RuntimeError(f"Expected xlsx, got {ctype!r} ({len(body)} bytes) for {label}")

    name = safe_filename(f"{payload['filename']}.xlsx")
    disp = resp.headers.get("content-disposition", "")
    m = re.search(r'filename="?([^"]+)"?', disp)
    if m:
        name = safe_filename(m.group(1))

    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / name
    action = "Overwrote" if dest.exists() else "Saved"
    dest.write_bytes(body)
    log.info("%s %s (%.1f KB)", action, dest.name, len(body) / 1024)
    return dest


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Download Alfamidi Market Share (Modular) reports.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--periode", default="2026-09",
                   help="Report month YYYY-MM, a range 'YYYY-MM:YYYY-MM', a comma list, "
                        "'all' (Jan-2026 through the current month), or 'recent' "
                        "(previous month + current month)")
    p.add_argument("--category", default="3251",
                   help="Category id, or 'all' for every category")
    p.add_argument("--format", default="MTD", choices=sorted(FORMAT))
    p.add_argument("--group", default="BRAND", choices=sorted(GROUP))
    p.add_argument("--year", default="ACT", choices=sorted(YEAR))
    p.add_argument("--unit", default="v", choices=sorted(UNIT))
    p.add_argument("--area-type", default="BRANCH", choices=sorted(AREA_TYPE),
                   dest="area_type")
    p.add_argument("--area", default="NAS", help="Branch/area code, e.g. NAS")
    p.add_argument("--report", default="2", choices=sorted(REPORT))
    p.add_argument("--out", type=Path, default=DEFAULT_OUT, help="Output folder")
    p.add_argument("--headful", action="store_true", help="Show the browser (debugging)")
    p.add_argument("--list", action="store_true", help="Print category/area codes and exit")
    a = p.parse_args(argv)

    if a.list:
        print("\nCATEGORIES:")
        for k, v in CATEGORY.items():
            print(f"  {k:>6}  {v}")
        print("\nAREAS/BRANCHES:")
        for k, v in AREA.items():
            print(f"  {k:>6}  {v}")
        sys.exit(0)

    try:
        a.periods = expand_periods(a.periode)
    except ValueError as exc:
        p.error(str(exc))
    if a.category != "all" and a.category not in CATEGORY:
        p.error(f"Unknown category {a.category!r}. Use --list to see valid codes.")
    if a.area not in AREA:
        p.error(f"Unknown area {a.area!r}. Use --list to see valid codes.")
    return a


def main(argv=None) -> int:
    args = parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(SCRIPT_DIR / "run.log", encoding="utf-8"),
        ],
    )

    load_dotenv(SCRIPT_DIR / ".env")
    username = os.getenv("ALFAMIDI_USERNAME")
    password = os.getenv("ALFAMIDI_PASSWORD")
    if not username or not password:
        log.error("ALFAMIDI_USERNAME / ALFAMIDI_PASSWORD missing. Copy .env.example to .env "
                  "and fill it in.")
        return 2

    categories = list(CATEGORY) if args.category == "all" else [args.category]
    periods = args.periods
    jobs = [(per, cat) for per in periods for cat in categories]
    out_dir = args.out
    saved, failed = [], []

    log.info("Queued %d report(s): %d period(s) x %d category(ies)",
              len(jobs), len(periods), len(categories))

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not args.headful)
        ctx = browser.new_context(accept_downloads=True)
        page = ctx.new_page()
        try:
            login(page, username, password)
            page = open_modular(page)
            for per, cat in jobs:
                try:
                    saved.append(download_one(page, build_payload(args, cat, per), out_dir))
                except Exception as exc:
                    log.error("FAILED %s / %s: %s", per, CATEGORY[cat], exc)
                    failed.append((per, cat))
                if len(jobs) > 1:
                    time.sleep(2)
        finally:
            ctx.close()
            browser.close()

    log.info("Done. %d saved, %d failed. Folder: %s", len(saved), len(failed), out_dir)
    return 1 if failed or not saved else 0


if __name__ == "__main__":
    sys.exit(main())
