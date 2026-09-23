"""
Indomaret B2B (DCP / Power BI Embedded) - Market Share automated downloader.

Architecture is completely different from the Alfamart/Alfamidi automations:
Indomaret's portal is a React SPA backed by JSON APIs, and the "Market Share"
report itself is a Power BI report embedded via "Power BI Embed for Customers".
There is no server-rendered <form> to submit - instead:

  1. POST api-idmsso.indomaret-bisnis.com/api/v1/sso/auth/login
     {email, password, client:"dcp_app"}  -> confirms 2FA is required, no token yet.
  2. POST api-idmsso.indomaret-bisnis.com/api/v1/sso/auth/otp/validate
     {email, otp, client, password}       -> {accessToken, refreshToken, ...}
  3. POST api-dcp.indomaret-bisnis.com/dcp/api/v1/user/bo/dashboard/detail
     {workspaceId, reportId} + "Authorization: Bearer <accessToken>"
     -> {embedToken: {token, expiration}}   (a short-lived Power BI bearer token)
  4. POST https://wabi-south-east-asia-d-primary-redirect.analysis.windows.net/export/xlsx
     + "Authorization: Bearer <embedToken>"
     body = an exact Power BI "ExecuteSemanticQuery + ExportDataCommand" payload,
     captured live from the browser and reused as a template here, with only the
     month/category/etc. filter literals swapped out.
     -> raw .xlsx bytes (no Content-Disposition filename - Power BI doesn't send one).

Because everything after login is plain JSON over HTTPS, this script uses `requests`
only - no Playwright/Chromium needed, unlike the Alfamart/Alfamidi automations.

The exported sheet's cell A1 contains Power BI's own "Applied filters: ..." text
and the data table starts at row 3 - this matches what
"Market Share\\1_market_share_converter.py" already expects from a manual export,
so files land in a format compatible with the existing downstream pipeline
(that script currently only scans the parent "Market Share" folder, not "Raw" -
point it at the Raw folder, or move files up, if you want it to pick these up).

Credentials are read from .env and never leave this machine.
"""

from __future__ import annotations

import argparse
import copy
import json
import logging
import os
import re
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

try:
    import pyotp
    import requests
    from dotenv import load_dotenv
except ImportError as exc:
    sys.exit(f"Missing dependency ({exc.name}). Run setup.ps1 first.")

SSO_LOGIN_URL = "https://api-idmsso.indomaret-bisnis.com/api/v1/sso/auth/login"
SSO_OTP_URL = "https://api-idmsso.indomaret-bisnis.com/api/v1/sso/auth/otp/validate"
DASHBOARD_DETAIL_URL = "https://api-dcp.indomaret-bisnis.com/dcp/api/v1/user/bo/dashboard/detail"
WABI_BASE = "https://wabi-south-east-asia-d-primary-redirect.analysis.windows.net"
EXPORT_URL = f"{WABI_BASE}/export/xlsx"

CLIENT = "dcp_app"
WORKSPACE_ID = "d9bb6774-5ab4-4d02-ab30-6ecd14f1e45f"
REPORT_ID = "d6a8c32a-050f-4fb2-b028-5faac50e8c94"
EXPLORE_URL = (f"{WABI_BASE}/explore/reports/{REPORT_ID}/modelsAndExploration"
               "?preferReadOnlySession=true&skipQueryData=true")
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
# Seconds to wait for Power BI to build one xlsx. See download_one().
EXPORT_TIMEOUT = 420

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_OUT = Path(
    r"D:\SCARLETT_512\SCARLETT-329\SOM\Data\Report\Sales\Minimarket\Indomaret\Market Share\Raw"
)

# Categories, lifted live from the report's own category slicer (a "distinct cat_nm"
# query captured in the HAR) - this report only covers Scarlett's own categories.
CATEGORY = [
    "BEAUTY LIQUID SOAP",
    "BODY COLOGNE FOR WOMEN",
    "BODY LOTION FOR WOMEN",
    "FACIAL WASH & SCRUB FOR WOMEN",
    "SERUM ESSENCE",
    "SUNSCREEN",
]
RANGE_PERIOD = ["YoY", "YTD"]
UNIT = ["IDR", "Qty"]

log = logging.getLogger("indomaret")

# The exact JSON body Power BI's own "Export data" button sends, captured live from
# the browser for one sample (BEAUTY LIQUID SOAP / Sep-2026 / YoY / IDR / SCARLETT).
# We deep-copy this per request and only touch the six filter literals + the
# human-readable FiltersDescription line - everything else (the DAX measure
# definitions, model wiring, column list) must stay byte-identical to what the
# live report actually sends.
_EXPORT_TEMPLATE_TEXT = r"""
{"exportDataType":0,"executeSemanticQueryRequest":{"version":"1.0.0","queries":[{"Query":{"Commands":[{"SemanticQueryDataShapeCommand":{"Query":{"Version":2,"From":[{"Name":"f","Entity":"fact_b2b_market_competition_monthly_vw","Type":0},{"Name":"m","Entity":"Market Competition","Schema":"extension","Type":0},{"Name":"m1","Entity":"Month Filter","Type":0},{"Name":"m2","Entity":"Market Competition","Type":0}],"Select":[{"Column":{"Expression":{"SourceRef":{"Source":"f"}},"Property":"brand"},"Name":"fact_b2b_market_competition_monthly_vw.brand","NativeReferenceName":"Brand"},{"Column":{"Expression":{"SourceRef":{"Source":"f"}},"Property":"principle_name"},"Name":"fact_b2b_market_competition_monthly_vw.principle_name","NativeReferenceName":"Principal"},{"Measure":{"Expression":{"SourceRef":{"Source":"m"}},"Property":"Total Product National"},"Name":"Market Competition.Total Product National","NativeReferenceName":"Total Product National"},{"Measure":{"Expression":{"SourceRef":{"Source":"m"}},"Property":"Market Share by Product"},"Name":"Market Competition.Market Share by Product","NativeReferenceName":"Market Share TY"},{"Measure":{"Expression":{"SourceRef":{"Source":"m"}},"Property":"Market Share by Product LY"},"Name":"Market Competition.Market Share by Product LY","NativeReferenceName":"Market Share LY"},{"Measure":{"Expression":{"SourceRef":{"Source":"m"}},"Property":"% of Share Change"},"Name":"Market Competition.% of Share Change","NativeReferenceName":"% of Share Change"},{"Measure":{"Expression":{"SourceRef":{"Source":"m"}},"Property":"Growth"},"Name":"Market Competition.Growth","NativeReferenceName":"Growth Value"}],"Where":[{"Condition":{"Not":{"Expression":{"Comparison":{"ComparisonKind":0,"Left":{"Measure":{"Expression":{"SourceRef":{"Source":"m"}},"Property":"Total Product National"}},"Right":{"Literal":{"Value":"null"}}}}}},"Target":[{"Column":{"Expression":{"SourceRef":{"Source":"f"}},"Property":"brand"}},{"Column":{"Expression":{"SourceRef":{"Source":"f"}},"Property":"principle_name"}}]},{"Condition":{"In":{"Expressions":[{"Column":{"Expression":{"SourceRef":{"Source":"m1"}},"Property":"month_id"}}],"Values":[[{"Literal":{"Value":"datetime'2026-09-01T00:00:00'"}}]]}}},{"Condition":{"In":{"Expressions":[{"Column":{"Expression":{"SourceRef":{"Source":"m2"}},"Property":"Range Period"}}],"Values":[[{"Literal":{"Value":"'YoY'"}}]]}}},{"Condition":{"In":{"Expressions":[{"Column":{"Expression":{"SourceRef":{"Source":"m2"}},"Property":"Value"}}],"Values":[[{"Literal":{"Value":"'IDR'"}}]]}}},{"Condition":{"In":{"Expressions":[{"Column":{"Expression":{"SourceRef":{"Source":"f"}},"Property":"cat_nm"}}],"Values":[[{"Literal":{"Value":"'BEAUTY LIQUID SOAP'"}}]]}}},{"Condition":{"In":{"Expressions":[{"Column":{"Expression":{"SourceRef":{"Source":"f"}},"Property":"brand_filter"}}],"Values":[[{"Literal":{"Value":"'SCARLETT'"}}]]}}}],"OrderBy":[{"Direction":2,"Expression":{"Measure":{"Expression":{"SourceRef":{"Source":"m"}},"Property":"Market Share by Product"}}}]},"Binding":{"Primary":{"Groupings":[{"Projections":[0,1,2,3,4,5,6],"Subtotal":0}]},"DataReduction":{"Primary":{"Top":{"Count":1000000}},"Secondary":{"Top":{"Count":100}}},"Version":1},"Extension":{"Version":0,"Name":"extension","Entities":[{"Extends":"Market Competition","Name":"Market Competition","Measures":[{"Name":"Total Product National","Expression":"CALCULATE(\n    SWITCH(\n        TRUE(),\n        SELECTEDVALUE('Market Competition'[Range Period]) = \"YoY\", MAX(fact_b2b_market_competition_monthly_vw[CY_national_prd_cnt_brand]),\n        SELECTEDVALUE('Market Competition'[Range Period]) = \"YTD\", MAX(fact_b2b_market_competition_monthly_vw[YTD_national_prd_cnt_brand])\n    ),\n    FILTER(\n        'Month',\n        'Month'[month_id] = SELECTEDVALUE('Month Filter'[month_id])\n    )\n)","DataType":3},{"Name":"Market Share by Product","Expression":"COALESCE(DIVIDE(\n    [Measure Range Period],\n    CALCULATE(\n        [Measure Range Period],\n        ALL(fact_b2b_market_competition_monthly_vw[prd_nm], fact_b2b_market_competition_monthly_vw[brand], fact_b2b_market_competition_monthly_vw[principle_name])\n    )\n), 0)","DataType":3},{"Name":"Measure Range Period","Expression":"SWITCH(\n    TRUE(),\n    SELECTEDVALUE('Market Competition'[Range Period]) = \"YOY\", CALCULATE(\n        [Total Measure],\n        fact_b2b_market_competition_monthly_vw[month_id] = SELECTEDVALUE('Month Filter'[month_id])\n    ),\n    SELECTEDVALUE('Market Competition'[Range Period]) = \"YTD\", CALCULATE(\n        TOTALYTD(\n            [Total Measure],\n            'Month'[month_id]\n        ),\n        FILTER(\n            'Month',\n            'Month'[month_id] <= SELECTEDVALUE('Month Filter'[month_id]) &&\n            YEAR('Month'[month_id]) = YEAR(SELECTEDVALUE('Month Filter'[month_id]))\n        )\n    )\n)","DataType":3},{"Name":"Total Measure","Expression":"SWITCH(\n    TRUE(),\n    SELECTEDVALUE('Market Competition'[Value]) = \"IDR\", SUM(fact_b2b_market_competition_monthly_vw[CY_gross]),\n    SELECTEDVALUE('Market Competition'[Value]) = \"Qty\", SUM(fact_b2b_market_competition_monthly_vw[CY_qty])\n)","DataType":3},{"Name":"Market Share by Product LY","Expression":"COALESCE(DIVIDE(\n    [Measure Range Period LY],\n    CALCULATE(\n        [Measure Range Period LY],\n        ALL(fact_b2b_market_competition_monthly_vw[prd_nm], fact_b2b_market_competition_monthly_vw[brand], fact_b2b_market_competition_monthly_vw[principle_name])\n    )\n), 0)","DataType":3},{"Name":"Measure Range Period LY","Expression":"SWITCH(\r\n    TRUE(),\r\n    SELECTEDVALUE('Market Competition'[Range Period]) = \"YOY\", CALCULATE(\r\n        [Total Measure],\r\n        FILTER(\r\n            fact_b2b_market_competition_monthly_vw,\r\n            fact_b2b_market_competition_monthly_vw[month_id] = DATEADD('Month Filter'[month_id], -1, YEAR)\r\n        )\r\n    ),\r\n    SELECTEDVALUE('Market Competition'[Range Period]) = \"YTD\", CALCULATE(\r\n        TOTALYTD(\r\n            [Total Measure],\r\n            'Month'[month_id]\r\n        ),\r\n        FILTER(\r\n            // 'Month',\r\n            // 'Month'[month_id] <= SELECTEDVALUE('Month Filter'[month_id]) &&\r\n            // YEAR('Month'[month_id]) = YEAR(SELECTEDVALUE('Month Filter'[month_id])) - 1\r\n            'Month',\r\n        'Month'[month_id] <= SELECTEDVALUE('Month Filter'[month_id]) &&\r\n        'Month'[month_id] >= DATE(YEAR(SELECTEDVALUE('Month Filter'[month_id])) - 1, 1, 1) &&\r\n        'Month'[month_id] <= DATE(YEAR(SELECTEDVALUE('Month Filter'[month_id])) - 1, MONTH(SELECTEDVALUE('Month Filter'[month_id])), DAY(SELECTEDVALUE('Month Filter'[month_id])))\r\n        )\r\n    )\r\n)","DataType":3},{"Name":"% of Share Change","Expression":"COALESCE([Market Share by Product] - [Market Share by Product LY], 0)","DataType":3},{"Name":"Growth","Expression":"COALESCE(DIVIDE(\n    [Measure Range Period] - [Measure Range Period LY],\n    [Measure Range Period LY]\n), 0)","DataType":3}]}]}}},{"ExportDataCommand":{"Columns":[{"QueryName":"fact_b2b_market_competition_monthly_vw.brand","Name":"Brand"},{"QueryName":"fact_b2b_market_competition_monthly_vw.principle_name","Name":"Principal"},{"QueryName":"Market Competition.Total Product National","Name":"Total Product National"},{"QueryName":"Market Competition.Market Share by Product","Name":"Market Share TY"},{"QueryName":"Market Competition.Market Share by Product LY","Name":"Market Share LY"},{"QueryName":"Market Competition.% of Share Change","Name":"% of Share Change"},{"QueryName":"Market Competition.Growth","Name":"Growth Value"}],"Ordering":[0,1,2,3,4,5,6],"FiltersDescription":"Applied filters:\nTotal Product National is not blank\nmonth_id is 01 September 2026\nRange Period is YoY\nValue is IDR\ncat_nm is BEAUTY LIQUID SOAP\nbrand_filter is SCARLETT"}}]}}],"cancelQueries":[],"modelId":437735,"userPreferredLocale":"en-GB"},"artifactId":1094154}
"""


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


def _find_where(where_list: list, property_name: str) -> dict:
    for cond in where_list:
        try:
            exprs = cond["Condition"]["In"]["Expressions"]
        except KeyError:
            continue
        for ex in exprs:
            if ex.get("Column", {}).get("Property") == property_name:
                return cond
    raise KeyError(f"Could not find a Where clause filtering on {property_name!r}")


def build_export_body(periode: str, category: str, range_period: str, unit: str,
                       brand: str, model_id: int, artifact_id: int) -> dict:
    body = json.loads(_EXPORT_TEMPLATE_TEXT)
    where = body["executeSemanticQueryRequest"]["queries"][0]["Query"]["Commands"][0][
        "SemanticQueryDataShapeCommand"]["Query"]["Where"]

    month_dt = datetime.strptime(periode, "%Y-%m")
    month_literal = f"datetime'{month_dt:%Y-%m-01T00:00:00}'"
    _find_where(where, "month_id")["Condition"]["In"]["Values"][0][0]["Literal"]["Value"] = month_literal
    _find_where(where, "Range Period")["Condition"]["In"]["Values"][0][0]["Literal"]["Value"] = f"'{range_period}'"
    _find_where(where, "Value")["Condition"]["In"]["Values"][0][0]["Literal"]["Value"] = f"'{unit}'"
    _find_where(where, "cat_nm")["Condition"]["In"]["Values"][0][0]["Literal"]["Value"] = f"'{category}'"
    _find_where(where, "brand_filter")["Condition"]["In"]["Values"][0][0]["Literal"]["Value"] = f"'{brand}'"

    export_cmd = body["executeSemanticQueryRequest"]["queries"][0]["Query"]["Commands"][1]["ExportDataCommand"]
    export_cmd["FiltersDescription"] = (
        "Applied filters:\n"
        "Total Product National is not blank\n"
        f"month_id is {month_dt:%d %B %Y}\n"
        f"Range Period is {range_period}\n"
        f"Value is {unit}\n"
        f"cat_nm is {category}\n"
        f"brand_filter is {brand}"
    )
    # These are minted per-session by /modelsAndExploration, not fixed report constants -
    # a stale/mismatched value here is almost certainly why a cold export call gets a 403.
    body["executeSemanticQueryRequest"]["modelId"] = model_id
    body["artifactId"] = artifact_id
    return body


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


SSO_HEADERS = {
    # The real frontend sends "client-code" as an HTTP header, separate from the
    # "client" field in the JSON body - both are required. Missing this header
    # produces a generic {"status":"...", "message":"failed"} with no other clue.
    "client-code": CLIENT,
    "Origin": "https://sso.indomaret-bisnis.com",
}
DCP_HEADERS = {
    "Origin": "https://connect.indomaret-bisnis.com",
    "Referer": "https://connect.indomaret-bisnis.com/",
}


def login(session: requests.Session, email: str, password: str, secret: str | None) -> str:
    """Runs the SSO login + OTP flow, returns the accessToken."""
    log.info("Logging in as %s", email)
    resp = session.post(SSO_LOGIN_URL, json={"email": email, "password": password, "client": CLIENT},
                         headers=SSO_HEADERS, timeout=30)
    data = resp.json()
    if resp.status_code != 200 or data.get("status") != "00":
        raise SystemExit(f"Login rejected - check INDOMARET_EMAIL / INDOMARET_PASSWORD. "
                          f"Server said: {data.get('message')!r}")
    if not data.get("data", {}).get("is2faEnabled", True):
        raise SystemExit("Portal reports 2FA is not enabled for this account - "
                          "this script assumes TOTP is required. Check manually.")

    log.info("Submitting 2FA code")
    otp_resp = session.post(SSO_OTP_URL, json={
        "email": email, "otp": current_otp(secret), "client": CLIENT, "password": password,
    }, headers=SSO_HEADERS, timeout=30)
    otp_data = otp_resp.json()
    if otp_resp.status_code != 200 or otp_data.get("status") != "00":
        raise SystemExit(f"2FA rejected - check INDOMARET_TOTP_SECRET and this PC's clock. "
                          f"Server said: {otp_data.get('message')!r}")

    access_token = otp_data["data"]["accessToken"]
    log.info("Logged in")
    return access_token


def get_embed_token(session: requests.Session, access_token: str) -> str:
    log.info("Requesting a Power BI embed token for the Market Share report")
    resp = session.post(
        DASHBOARD_DETAIL_URL,
        json={"workspaceId": WORKSPACE_ID, "reportId": REPORT_ID},
        headers={**DCP_HEADERS, "Authorization": f"Bearer {access_token}"},
        timeout=30,
    )
    data = resp.json()
    if resp.status_code != 200 or data.get("status") != "00":
        raise SystemExit(f"Could not get an embed token (HTTP {resp.status_code}). "
                          f"Server said: {data.get('message')!r}")
    token = data["data"]["embedToken"]["token"]
    log.info("Embed token expires at %s", data["data"]["embedToken"].get("expiration"))
    return token


def _pbi_headers(embed_token: str) -> dict:
    # Power BI's own service distinguishes an embed token from a regular AAD/user
    # token: embed tokens go in "Authorization: EmbedToken <token>", not "Bearer".
    # Using "Bearer" here is a plausible explanation for a 403 on both the
    # exploration call and the export call.
    return {
        "Authorization": f"EmbedToken {embed_token}",
        "ActivityId": str(uuid.uuid4()),
        "RequestId": str(uuid.uuid4()),
        "X-PowerBI-HostEnv": "Embed for Customers",
        "Origin": "https://app.powerbi.com",
        "Referer": "https://app.powerbi.com/",
    }


def get_exploration_context(session: requests.Session, embed_token: str) -> tuple[int, int]:
    """Opens the report's Power BI exploration session - required before /export/xlsx
    will accept requests - and returns the (modelId, artifactId) it hands back.
    These are minted per-session, not fixed constants, despite looking like stable
    report/dataset IDs."""
    log.info("Opening the report's Power BI exploration session")
    resp = session.get(EXPLORE_URL, headers={**_pbi_headers(embed_token), "Accept": "application/json"},
                        timeout=60)
    if not resp.ok:
        raise SystemExit(f"Could not open the report exploration session (HTTP {resp.status_code}).")
    data = resp.json()
    model_id = data["models"][0]["id"]
    artifact_id = data["exploration"]["reportId"]
    log.info("Exploration session ready (modelId=%s, artifactId=%s)", model_id, artifact_id)
    return model_id, artifact_id


def download_one(session: requests.Session, embed_token: str, periode: str, category: str,
                  range_period: str, unit: str, brand: str, model_id: int, artifact_id: int,
                  out_dir: Path) -> Path:
    log.info("Requesting: %s / %s / %s / %s", category, periode, range_period, unit)
    body = build_export_body(periode, category, range_period, unit, brand, model_id, artifact_id)

    # Power BI builds the xlsx synchronously, and a cold model is slow: on
    # 2026-09-21 five of six August exports died on a 180s read timeout while a
    # September one squeaked through at 178s. The work was almost certainly
    # finishing server-side, so give it room and retry once rather than losing
    # the month.
    resp = None
    for attempt in (1, 2):
        try:
            resp = session.post(EXPORT_URL, json=body,
                                headers=_pbi_headers(embed_token), timeout=EXPORT_TIMEOUT)
            break
        except requests.exceptions.ReadTimeout:
            if attempt == 2:
                raise
            log.warning("Timed out after %ss on %s / %s - retrying once",
                        EXPORT_TIMEOUT, category, periode)

    if resp.status_code == 401:
        raise PermissionError("Embed token expired")
    if not resp.ok:
        raise RuntimeError(f"HTTP {resp.status_code} for {category}/{periode}")

    ctype = (resp.headers.get("content-type") or "").split(";")[0]
    content = resp.content
    if ctype != XLSX_MIME or content[:2] != b"PK":
        raise RuntimeError(f"Expected xlsx, got {ctype!r} ({len(content)} bytes) "
                            f"for {category}/{periode}")

    month_label = datetime.strptime(periode, "%Y-%m").strftime("%b-%Y")
    name = safe_filename(f"Data_{category}_{month_label}_{range_period}_{unit}.xlsx")

    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / name
    action = "Overwrote" if dest.exists() else "Saved"
    dest.write_bytes(content)
    log.info("%s %s (%.1f KB)", action, dest.name, len(content) / 1024)
    return dest


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Download Indomaret Market Share (Power BI) reports.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--periode", default=f"{datetime.now():%Y-%m}",
                   help="Report month YYYY-MM, a range 'YYYY-MM:YYYY-MM', a comma list, "
                        "'all' (Jan-2026 through the current month), or 'recent' "
                        "(previous month + current month)")
    p.add_argument("--category", default="all",
                   help="One of the known categories (quote it), or 'all'")
    p.add_argument("--range-period", default="YoY", choices=RANGE_PERIOD, dest="range_period")
    p.add_argument("--unit", default="IDR", choices=UNIT)
    p.add_argument("--brand", default="SCARLETT")
    p.add_argument("--out", type=Path, default=DEFAULT_OUT, help="Output folder")
    p.add_argument("--list", action="store_true", help="Print category codes and exit")
    a = p.parse_args(argv)

    if a.list:
        print("\nCATEGORIES:")
        for c in CATEGORY:
            print(f"  {c}")
        sys.exit(0)

    try:
        a.periods = expand_periods(a.periode)
    except ValueError as exc:
        p.error(str(exc))
    if a.category != "all" and a.category not in CATEGORY:
        p.error(f"Unknown category {a.category!r}. Use --list to see valid names.")
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

    categories = CATEGORY if args.category == "all" else [args.category]
    periods = args.periods
    jobs = [(per, cat) for per in periods for cat in categories]
    out_dir = args.out
    saved, failed = [], []

    log.info("Queued %d report(s): %d period(s) x %d category(ies)",
              len(jobs), len(periods), len(categories))

    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36",
    })

    access_token = login(session, email, password, secret)
    embed_token = get_embed_token(session, access_token)
    model_id, artifact_id = get_exploration_context(session, embed_token)

    for per, cat in jobs:
        try:
            saved.append(download_one(session, embed_token, per, cat, args.range_period,
                                       args.unit, args.brand, model_id, artifact_id, out_dir))
        except PermissionError:
            log.info("Embed token expired mid-run, refreshing")
            embed_token = get_embed_token(session, access_token)
            model_id, artifact_id = get_exploration_context(session, embed_token)
            try:
                saved.append(download_one(session, embed_token, per, cat, args.range_period,
                                           args.unit, args.brand, model_id, artifact_id, out_dir))
            except Exception as exc:
                log.error("FAILED %s / %s: %s", per, cat, exc)
                failed.append((per, cat))
        except Exception as exc:
            log.error("FAILED %s / %s: %s", per, cat, exc)
            failed.append((per, cat))
        if len(jobs) > 1:
            time.sleep(1)

    log.info("Done. %d saved, %d failed. Folder: %s", len(saved), len(failed), out_dir)
    return 1 if failed or not saved else 0


if __name__ == "__main__":
    sys.exit(main())
