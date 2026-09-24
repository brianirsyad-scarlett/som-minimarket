"""
Alfamart B2B - Dashboard & Modular auto request-download (mirrors alfamidi_b2b_auto.py).

Login here is two-factor (Google Authenticator TOTP), unlike Alfamidi's plain
username/password:

  1. GET  https://b2b.alfamart.co.id/login.php               -> scrape hidden "token"
  2. POST https://b2b.alfamart.co.id/do_login.php             (uname, upass, token)
     -> 302 to validasi-otp.php
  3. GET  https://b2b.alfamart.co.id/validasi-otp.php         -> scrape hidden "token"
  4. POST https://b2b.alfamart.co.id/validasi-otp.php         (code=<live TOTP>, token)
     -> "Login Berhasil"
  5. GET  https://b2b.alfamart.co.id/get_laporan_new_premium.php -> SSO token redirect
  6. GET  https://b2b-np.alfamart.co.id/login-authentication?token=...
  7. Same performancesales-modular / bibdbs flow as Alfamidi, categories scraped live
     (Alfamart has ~13 categories vs Alfamidi's 6).

TOTP secret comes from ~/.b2b_email.env's ALFAMART_TOTP_SECRET (pyotp generates
the current 6-digit code, no manual entry needed).

Usage: identical flags to alfamidi_b2b_auto.py
    python alfamart_b2b_auto.py --dry-run --start 2026-09-01 --end 2026-09-12
    python alfamart_b2b_auto.py --limit 1          # single-shot verification
    python alfamart_b2b_auto.py --skip 1
"""

import argparse
import html
import os
import re
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import pyotp
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


ALFAMART_USER = _cfg("ALFAMART_B2B_USER")
ALFAMART_PASS = _cfg("ALFAMART_B2B_PASS")

B2B_BASE = "https://b2b.alfamart.co.id"
NP_BASE = "https://b2b-np.alfamart.co.id"

ENV_PATH = Path.home() / ".b2b_email.env"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)

INDICATOR = ("a", "Selling Out")
# Form 5 (by branch) also offers "b" = Stok - same endpoint, same email
# delivery; files come back as detail_performance_by_branch_Stok_<unit>_...
STOCK_INDICATOR = ("b", "Stok")
TIPE_AREA = ("DC", "BRANCH")
BRANCH = ("NAS", "NASIONAL")
ITEM = ("ALL", "All Item")
STORE = ("ALL", "All Store")
CATEGORY_ALL = ("ALL", "All Category")
UNITS = [("v", "Value"), ("q", "Qty")]

MAX_RANGE_DAYS = 31
SLEEP_BETWEEN_REQUESTS = 2.0
TIMEOUT = 180


def load_totp_secret():
    # Environment first: in GitHub Actions the seed arrives as a secret, and
    # there is no ~/.b2b_email.env on the runner.
    env_secret = os.environ.get("ALFAMART_TOTP_SECRET", "").strip().replace(" ", "")
    if env_secret:
        return env_secret
    if not ENV_PATH.exists():
        raise SystemExit(f"Missing {ENV_PATH} (need ALFAMART_TOTP_SECRET).")
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if line.startswith("ALFAMART_TOTP_SECRET="):
            secret = line.split("=", 1)[1].strip().replace(" ", "")
            if not secret:
                raise SystemExit("ALFAMART_TOTP_SECRET is empty.")
            return secret
    raise SystemExit(f"{ENV_PATH} has no ALFAMART_TOTP_SECRET line.")


def scrape_hidden_token(page_html, field_name="token"):
    m = re.search(
        rf'name=["\']{field_name}["\']\s+value=["\']([^"\']+)["\']', page_html
    )
    if not m:
        m = re.search(
            rf'value=["\']([^"\']+)["\']\s+name=["\']{field_name}["\']', page_html
        )
    return m.group(1) if m else None


def open_session():
    s = requests.Session()
    s.headers.update({"User-Agent": UA})
    totp_secret = load_totp_secret()

    print("--- Step 1: login.php ---")
    r = s.get(f"{B2B_BASE}/login.php", timeout=TIMEOUT)
    login_token = scrape_hidden_token(r.text)
    if not login_token:
        raise SystemExit("Could not find hidden 'token' field on login.php.")
    print(f"login token -> {login_token[:12]}...")

    print("--- Step 2: do_login.php ---")
    r = s.post(
        f"{B2B_BASE}/do_login.php",
        data={"uname": ALFAMART_USER, "upass": ALFAMART_PASS, "token": login_token},
        headers={"Referer": f"{B2B_BASE}/login.php", "Origin": B2B_BASE},
        timeout=TIMEOUT,
        allow_redirects=True,
    )
    if "validasi-otp" not in r.url:
        raise SystemExit(f"Expected redirect to validasi-otp.php, got: {r.url}\n{r.text[:500]}")

    print("--- Step 3: validasi-otp.php (GET) ---")
    otp_token = scrape_hidden_token(r.text)
    if not otp_token:
        r = s.get(f"{B2B_BASE}/validasi-otp.php", timeout=TIMEOUT)
        otp_token = scrape_hidden_token(r.text)
    if not otp_token:
        raise SystemExit("Could not find hidden 'token' field on validasi-otp.php.")
    print(f"otp token   -> {otp_token[:12]}...")

    code = pyotp.TOTP(totp_secret).now()
    print(f"--- Step 4: validasi-otp.php (POST code={code}) ---")
    r = s.post(
        f"{B2B_BASE}/validasi-otp.php",
        data={"code": code, "token": otp_token},
        headers={"Referer": f"{B2B_BASE}/validasi-otp.php", "Origin": B2B_BASE},
        timeout=TIMEOUT,
    )
    if "Login Berhasil" not in r.text:
        raise SystemExit(f"OTP login failed:\n{r.text[:500]}")
    print("Login Berhasil")

    print("--- Step 5: get_laporan_new_premium.php (SSO handoff) ---")
    r = s.get(
        f"{B2B_BASE}/get_laporan_new_premium.php",
        headers={"Referer": f"{B2B_BASE}/index.php"},
        timeout=TIMEOUT,
    )
    m = re.search(r'window\.location\.replace\("([^"]+)"\)', r.text)
    if not m:
        raise SystemExit(f"No SSO token in get_laporan_new_premium.php:\n{r.text[:500]}")
    token_url = m.group(1)
    # Only a prefix: the full token is a live session credential.
    print(f"SSO token -> {token_url.split('token=')[-1][:6]}...")

    r = s.get(token_url, timeout=TIMEOUT)
    if not s.cookies.get("session", domain="b2b-np.alfamart.co.id"):
        raise SystemExit("SSO handshake did not return a b2b-np session cookie.")
    print(f"Dashboard session established ({r.url})")
    return s


def load_modular_page(s):
    r = s.get(
        f"{NP_BASE}/performancesales-modular",
        headers={"Referer": f"{NP_BASE}/index"},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    return r.text


def scrape_csrf_token(page_html):
    m = re.search(r'var\s+csrf_token\s*=\s*"([^"]+)"', page_html)
    return m.group(1) if m else None


def scrape_categories(page_html):
    form = re.search(
        r'<form id="frm-filter-report-modular-4".*?</form>', page_html, re.S
    )
    if not form:
        raise SystemExit("Could not find frm-filter-report-modular-4 on the page.")
    sel = re.search(r'name="category".*?</select>', form.group(0), re.S)
    if not sel:
        raise SystemExit("Could not find the category dropdown in form 4.")
    # Strip HTML comments first - form-4's "All Category" option is
    # deliberately commented out (this endpoint doesn't support it), but a
    # plain regex would still match text inside <!-- -->.
    sel_html = re.sub(r"<!--.*?-->", "", sel.group(0), flags=re.S)
    cats = []
    for value, label in re.findall(
        r'<option value="([^"]+)">([^<]+)</option>', sel_html
    ):
        if value.strip().lower() == "all":
            continue
        cats.append((value, html.unescape(label).strip()))
    if not cats:
        raise SystemExit("Category dropdown came back empty.")
    return cats


def _fname_part(text):
    return re.sub(r"\s", "_", text).replace("(", "").replace(")", "")


def build_by_branch(unit, tgla, tglb, indicator=INDICATOR):
    """
    Form-5 (frm-filter-report-modular-5): field names come straight off the
    HTML form (indicator, unit, periode_awal_bybranch, periode_akhir_bybranch,
    tipe-area, branch, category, item) - Alfamart's JS passes the raw form
    dict through unchanged, unlike Alfamidi's renamed opt/u/br/cat/plu/tgla/tglb.
    """
    u, unit_text = unit
    texts = [indicator[1], unit_text, CATEGORY_ALL[1], ITEM[1], BRANCH[1]]
    return {
        "tipe_prf": "5",
        "indicator": indicator[0],
        "unit": u,
        "periode_awal_bybranch": tgla,
        "periode_akhir_bybranch": tglb,
        "tipe-area": TIPE_AREA[0],
        "branch": BRANCH[0],
        "category": CATEGORY_ALL[0],
        "item": ITEM[0],
        "indicatortext": indicator[1],
        "unittext": unit_text,
        "categorytext": CATEGORY_ALL[1],
        "itemtext": ITEM[1],
        "branchtext": BRANCH[1],
        "filename": "detail_performance_by_branch_" + "_".join(_fname_part(t) for t in texts),
    }


def build_by_store(unit, category, tgla, tglb):
    """Form-4 (frm-filter-report-modular-4): same raw-form-field convention."""
    u, unit_text = unit
    cat_id, cat_text = category
    texts = [INDICATOR[1], unit_text, cat_text, ITEM[1], BRANCH[1], STORE[1]]
    return {
        "tipe_prf": "4",
        "indicator": INDICATOR[0],
        "unit": u,
        "periode_awal": tgla,
        "periode_akhir": tglb,
        "tipe-area": TIPE_AREA[0],
        "branch": BRANCH[0],
        "store": STORE[0],
        "category": cat_id,
        "item": ITEM[0],
        "indicatortext": INDICATOR[1],
        "unittext": unit_text,
        "categorytext": cat_text,
        "itemtext": ITEM[1],
        "branchtext": BRANCH[1],
        "storetext": STORE[1],
        "filename": "detail_performance_" + "_".join(_fname_part(t) for t in texts),
    }


def post_request_download(s, path, payload, csrf_token=None):
    headers = {
        "Referer": f"{NP_BASE}/performancesales-modular",
        "Origin": NP_BASE,
        "X-Requested-With": "XMLHttpRequest",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    }
    if csrf_token:
        headers["X-CSRFToken"] = csrf_token
    r = s.post(f"{NP_BASE}/{path}", data=payload, headers=headers, timeout=TIMEOUT)
    r.raise_for_status()
    try:
        body = r.json()
    except ValueError:
        return False, r.text.strip()[:200]
    return body.get("code") == "T", str(body.get("result", "")).replace("\n", " ").strip()


def prime_category(s, cat_id, csrf_token=None):
    headers = {
        "Referer": f"{NP_BASE}/performancesales-modular",
        "Origin": NP_BASE,
        "X-Requested-With": "XMLHttpRequest",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    }
    if csrf_token:
        headers["X-CSRFToken"] = csrf_token
    for path in ("xhr/listitembycat", "perfsales/check-cat-sigaret"):
        try:
            s.post(f"{NP_BASE}/{path}", data={"cat": cat_id}, headers=headers, timeout=TIMEOUT)
        except requests.RequestException:
            pass


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
        raise SystemExit(f"Range is {span} days; site caps at {MAX_RANGE_DAYS}.")
    return start.isoformat(), end.isoformat()


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--yesterday", action="store_true")
    ap.add_argument("--today", action="store_true")
    ap.add_argument("--only", choices=["by-branch", "by-store"])
    ap.add_argument("--category", action="append", dest="categories")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--skip", type=int, default=0)
    ap.add_argument("--stock", action="store_true",
                    help="by-branch STOK instead of Selling Out (implies --only by-branch)")
    ap.add_argument("--confirm", action="store_true",
                    help="click every request twice; the second reply proves the first registered")
    args = ap.parse_args()
    if args.stock:
        args.only = "by-branch"

    tgla, tglb = resolve_dates(args)

    s = open_session()
    page = load_modular_page(s)
    csrf_token = scrape_csrf_token(page)
    print(f"CSRF token   : {'found' if csrf_token else 'NOT FOUND'}")
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
            jobs.append(("perfsales/modular/bibdbs/by-branch",
                         f"By Branch / Daily / {unit[1]}",
                         build_by_branch(unit, tgla, tglb, STOCK_INDICATOR if args.stock else INDICATOR), None))
    if args.only != "by-branch":
        for cat in categories:
            for unit in UNITS:
                jobs.append(("perfsales/modular/bibdbs/request-download",
                             f"By Store by Category / Daily / {unit[1]} / {cat[1]}",
                             build_by_store(unit, cat, tgla, tglb), cat[0]))

    if args.skip:
        jobs = jobs[args.skip:]
    if args.limit:
        jobs = jobs[: args.limit]
    print(f"Requests     : {len(jobs)}"
          + ("   [DRY RUN]" if args.dry_run else ""))
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
            if args.confirm:
                # Second click on the same request, straight away. The portal
                # refuses to re-queue an export within an hour, so a reply of
                # "Sudah diajukan dalam 1 jam terakhir" proves the first click
                # registered. "Akan dikirim" means the first did NOT register
                # and this click queued it instead - healed either way.
                time.sleep(1)
                try:
                    _, msg2 = post_request_download(s, path, payload, csrf_token)
                except requests.RequestException as exc:
                    msg2 = f"FAILED {type(exc).__name__}: {exc}"
                print(f"               confirm: {msg2}")
        else:
            fail += 1
            print(f"          FAIL {msg}")
        if i < len(jobs):
            time.sleep(SLEEP_BETWEEN_REQUESTS)

    if not args.dry_run:
        print(f"\n--- Done: {ok} queued, {fail} failed ---")
        if fail:
            sys.exit(1)


if __name__ == "__main__":
    main()
