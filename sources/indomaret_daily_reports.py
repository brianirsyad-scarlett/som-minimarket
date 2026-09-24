"""
Indomaret B2B (DCP) - daily operational report downloader.

Separate from the Market Share automation (Indomaret-MarketShare) because this
hits a completely different part of the API: pre-generated report *files*
(zip archives Indomaret's backend builds once a day), not a live Power BI query.

Flow, reverse-engineered from a HAR capture of the "Laporan" menu's numbered
report list:
  1. POST api-idmsso.indomaret-bisnis.com/.../auth/login   (same as Market Share)
  2. POST .../auth/otp/validate                            -> accessToken
  3. GET  api-dcp.indomaret-bisnis.com/.../report/search?reportTypeId=<id>&sort=id,desc
     -> a page of previously-generated reports for that type, newest first.
  4. GET  .../report/download/<downloadToken>
     -> {"data": {"signedUrl": "https://storage.googleapis.com/...&X-Goog-Expires=59"}}
     This is a short-lived (59s) pre-signed GCS URL - fetch it immediately.
  5. GET  <signedUrl>  (no auth needed - it's a public pre-signed URL)
     -> the actual .zip bytes.

The zip is saved as-is (original filename), matching what a manual browser
download produces - the existing "1_zip_converter.py" / "2_folder_extractor.py"
scripts already sitting in these destination folders expect exactly that as
their raw input, so this script does not extract anything itself.

Credentials are read from .env and never leave this machine.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import time
from pathlib import Path

try:
    import pyotp
    import requests
    from dotenv import load_dotenv
except ImportError as exc:
    sys.exit(f"Missing dependency ({exc.name}). Run setup.ps1 first.")

SSO_LOGIN_URL = "https://api-idmsso.indomaret-bisnis.com/api/v1/sso/auth/login"
SSO_OTP_URL = "https://api-idmsso.indomaret-bisnis.com/api/v1/sso/auth/otp/validate"
SEARCH_URL = "https://api-dcp.indomaret-bisnis.com/dcp/api/v1/user/bo/report/search"
DOWNLOAD_URL = "https://api-dcp.indomaret-bisnis.com/dcp/api/v1/user/bo/report/download"

CLIENT = "dcp_app"
SSO_HEADERS = {"client-code": CLIENT, "Origin": "https://sso.indomaret-bisnis.com"}
DCP_HEADERS = {"Origin": "https://connect.indomaret-bisnis.com",
               "Referer": "https://connect.indomaret-bisnis.com/"}

BASE_OUT = Path(r"D:\SCARLETT_512\SCARLETT-329\SOM\Data\Report\Sales\Minimarket\Indomaret")

# reportTypeId -> (display name, destination folder). IDs and names come straight
# from GET .../report/type?unpaged=true.
REPORT_TYPES = {
    2: ("Daily Selling Out Performance by Branch and Product", BASE_OUT / "Sell Out"),
    3: ("Daily Selling Out & Stock by Store and Product", BASE_OUT / "Daily Sell Out"),
    10: ("Daily Stock by Branch and Product", BASE_OUT / "Stock"),
}

SCRIPT_DIR = Path(__file__).resolve().parent
log = logging.getLogger("indomaret-daily")


def current_otp(secret: str | None) -> str:
    if secret:
        cleaned = re.sub(r"\s+", "", secret).upper()
        totp = pyotp.TOTP(cleaned)
        if totp.interval - (time.time() % totp.interval) < 3:
            time.sleep(4)
        return totp.now()
    code = input("Indomaret Authenticator 6-digit code: ").strip()
    if not re.fullmatch(r"\d{6}", code):
        raise SystemExit("Expected a 6-digit code.")
    return code


def login(session: requests.Session, email: str, password: str, secret: str | None) -> str:
    log.info("Logging in as %s", email)
    resp = session.post(SSO_LOGIN_URL, json={"email": email, "password": password, "client": CLIENT},
                         headers=SSO_HEADERS, timeout=30)
    data = resp.json()
    if resp.status_code != 200 or data.get("status") != "00":
        raise SystemExit(f"Login rejected - check INDOMARET_EMAIL / INDOMARET_PASSWORD. "
                          f"Server said: {data.get('message')!r}")

    log.info("Submitting 2FA code")
    otp_resp = session.post(SSO_OTP_URL, json={
        "email": email, "otp": current_otp(secret), "client": CLIENT, "password": password,
    }, headers=SSO_HEADERS, timeout=30)
    otp_data = otp_resp.json()
    if otp_resp.status_code != 200 or otp_data.get("status") != "00":
        raise SystemExit(f"2FA rejected - check INDOMARET_TOTP_SECRET and this PC's clock. "
                          f"Server said: {otp_data.get('message')!r}")

    log.info("Logged in")
    return otp_data["data"]["accessToken"]


def safe_filename(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*]', "_", name).strip()


def download_newest(session: requests.Session, access_token: str, report_type_id: int,
                     name: str, out_dir: Path, force: bool, look_back: int) -> list[Path]:
    """Downloads any of the `look_back` most recent reports that aren't on disk yet.

    Looking at more than just the single newest matters: this runs once a day, so if
    Indomaret's generation slips and two files show up between runs, taking only the
    newest would skip the middle one permanently."""
    headers = {**DCP_HEADERS, "Authorization": f"Bearer {access_token}"}

    resp = session.get(SEARCH_URL, params={
        "perPage": look_back, "page": 1, "sort": "id,desc",
        "reportTypeId": report_type_id, "branchCode": '["ALL"]',
    }, headers=headers, timeout=30)
    data = resp.json()
    if resp.status_code != 200 or data.get("status") != "00":
        raise RuntimeError(f"HTTP {resp.status_code} listing reports for {name!r}: "
                            f"{data.get('message')!r}")

    content = data["data"]["content"]
    if not content:
        log.warning("No generated reports found for %r yet", name)
        return []

    # Oldest-first so a gap fills in chronological order.
    saved = []
    for entry in reversed(content):
        file_name = safe_filename(entry["fileName"])
        dest = out_dir / file_name

        if dest.exists() and not force:
            continue

        log.info("Fetching %s: %s", name, file_name)
        dl_resp = session.get(f"{DOWNLOAD_URL}/{entry['downloadToken']}", headers=headers,
                               timeout=30)
        dl_data = dl_resp.json()
        if dl_resp.status_code != 200 or dl_data.get("status") != "00":
            raise RuntimeError(f"HTTP {dl_resp.status_code} getting a download link for "
                                f"{name!r}: {dl_data.get('message')!r}")

        signed_url = dl_data["data"]["signedUrl"]
        # The signed URL is valid for ~59 seconds (X-Goog-Expires=59) - fetch it right away,
        # with no Authorization header (it's a public pre-signed GCS URL).
        file_resp = requests.get(signed_url, timeout=180)
        if not file_resp.ok:
            raise RuntimeError(f"HTTP {file_resp.status_code} downloading the file itself "
                                f"for {name!r}")

        out_dir.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(file_resp.content)
        log.info("Saved %s (%.1f KB)", dest.name, len(file_resp.content) / 1024)
        saved.append(dest)

    if not saved:
        log.info("Already have the %d most recent %s - nothing to do", len(content), name)
    return saved


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Download the newest Indomaret daily operational reports.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--report", default="all",
                   help="reportTypeId (2, 3, or 10), a comma list like 2,3, or 'all'")
    # Cloud runs start from an empty disk, so the output folder is an argument
    # rather than the laptop's hardcoded D:\ path.
    p.add_argument("--out", type=Path, default=None,
                   help="Write under this folder instead of BASE_OUT")
    p.add_argument("--force", action="store_true",
                   help="Re-download even if a file with that name already exists")
    p.add_argument("--look-back", type=int, default=5, dest="look_back",
                   help="How many of the most recent reports to check for gaps")
    p.add_argument("--list", action="store_true", help="Print report types and exit")
    a = p.parse_args(argv)

    if a.list:
        print("\nREPORT TYPES:")
        for rid, (name, dest) in REPORT_TYPES.items():
            print(f"  {rid:>3}  {name}  ->  {dest}")
        sys.exit(0)

    if a.report != "all":
        # A comma list, so several report types share ONE login: two logins
        # inside the same 30-second TOTP window can be refused as a reused code.
        try:
            a.report = [int(x) for x in str(a.report).split(",") if x.strip()]
        except ValueError:
            p.error(f"--report must be numbers or 'all', got {a.report!r}")
        bad = [r for r in a.report if r not in REPORT_TYPES]
        if bad:
            p.error(f"Unknown reportTypeId {bad}. Use --list to see valid ids.")
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
    email = os.getenv("INDOMARET_EMAIL")
    password = os.getenv("INDOMARET_PASSWORD")
    secret = os.getenv("INDOMARET_TOTP_SECRET") or None
    if not email or not password:
        log.error("INDOMARET_EMAIL / INDOMARET_PASSWORD missing. Copy .env.example to .env "
                  "and fill it in.")
        return 2
    if not secret and not sys.stdin.isatty():
        log.error("INDOMARET_TOTP_SECRET is required for unattended runs.")
        return 2

    report_ids = list(REPORT_TYPES) if args.report == "all" else args.report

    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36",
    })

    access_token = login(session, email, password, secret)

    saved, failed = [], []
    for rid in report_ids:
        name, out_dir = REPORT_TYPES[rid]
        if args.out:
            out_dir = args.out / out_dir.name
        try:
            saved.extend(download_newest(session, access_token, rid, name, out_dir,
                                          args.force, args.look_back))
        except Exception as exc:
            log.error("FAILED %s: %s", name, exc)
            failed.append(name)

    log.info("Done. %d saved, %d failed.", len(saved), len(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
