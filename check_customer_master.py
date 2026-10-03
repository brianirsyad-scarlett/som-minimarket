#!/usr/bin/env python3
"""Flag quarter-report rows whose Customer State / Customer City came out blank,
i.e. whose Customer Name + Delivery Address is not in Master Data Customer Odoo.xlsx,
and email the list.

Runs after build_odoo_report.py, both on the laptop (scheduled task) and on GitHub.
It never edits the master file - the user adds the missing rows by hand.

    python check_customer_master.py                 # current quarter, email if needed
    python check_customer_master.py --quarter 2026Q3 --no-email

Email is sent only when the unmatched list changed since the last alert, or once per
day (WIB) while it stays unresolved, plus one "all matched" email when it clears. The
last-alert state lives in GCS so the laptop and GitHub don't both send the same email.

Credentials: SMTP_USER / SMTP_APP_PASSWORD from the environment (GitHub secrets), else
B2B_GMAIL_USER / B2B_GMAIL_APP_PASSWORD from ~/.b2b_email.env (laptop). Recipients:
NOTIFY_TO, comma-separated.
"""

import argparse
import json
import os
import re
import smtplib
import sys
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_TO = "brian.rinaldy@lmbg.co.id"
BUCKET = "bucket_som"
STATE_BLOB = "sales_parquet/raw/primary/odoo/_checks/customer_master_alert.json"
LOCAL_GCP_KEY = r"D:\SCARLETT_512\SCARLETT-329\SOM\Data\Sent Email\sales-som datawarehouse 490008.json"
WIB = timezone(timedelta(hours=7))


def current_quarter():
    today = datetime.now(WIB)
    return today.year, (today.month - 1) // 3 + 1


def report_path(year, quarter):
    seq = 13 + (year - 2026) * 4 + (quarter - 1)  # same numbering as build_odoo_report.py
    return os.path.join(BASE_DIR, f"{seq}. {year} Q{quarter} Odoo Report.xlsx")


def find_unmatched(path):
    df = pd.read_excel(path)
    blank = df[df["CustomerState"].isna() | df["CustomerCity"].isna()]
    if blank.empty:
        return len(df), pd.DataFrame(columns=["CustomerName", "Address", "Rows", "Qty", "FirstOrder", "LastOrder"])
    g = (blank.groupby(["CustomerName", "Address"], dropna=False)
         .agg(Rows=("CustomerName", "size"), Qty=("Quantity", "sum"),
              FirstOrder=("CreatedOn", "min"), LastOrder=("CreatedOn", "max"))
         .reset_index().sort_values("Qty", ascending=False))
    return len(df), g


# ---------------------------------------------------------------- GCS state
def _bucket():
    if not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") and os.path.exists(LOCAL_GCP_KEY):
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = LOCAL_GCP_KEY
    from google.cloud import storage
    return storage.Client().bucket(BUCKET)


def load_state():
    try:
        blob = _bucket().get_blob(STATE_BLOB)
        return json.loads(blob.download_as_text()) if blob else {}
    except Exception as e:  # state is only for de-duplication - never block the alert
        print(f"  (could not read alert state: {e})")
        return {}


def save_state(state):
    try:
        _bucket().blob(STATE_BLOB).upload_from_string(json.dumps(state, indent=1),
                                                      content_type="application/json")
    except Exception as e:
        print(f"  (could not save alert state: {e})")


# ---------------------------------------------------------------- email
def smtp_credentials():
    user, pw = os.environ.get("SMTP_USER"), os.environ.get("SMTP_APP_PASSWORD")
    if user and pw:
        return user, pw
    env_path = Path.home() / ".b2b_email.env"
    if env_path.exists():
        env = {}
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
        if env.get("B2B_GMAIL_USER") and env.get("B2B_GMAIL_APP_PASSWORD"):
            return env["B2B_GMAIL_USER"], env["B2B_GMAIL_APP_PASSWORD"]
    return None, None


def send(subject, body, attachment=None):
    user, pw = smtp_credentials()
    if not (user and pw):
        print("  No SMTP credentials (SMTP_USER/SMTP_APP_PASSWORD or ~/.b2b_email.env) - email not sent.")
        return False
    to = [a.strip() for a in os.environ.get("NOTIFY_TO", DEFAULT_TO).split(",") if a.strip()]
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, user, ", ".join(to)
    msg.set_content(body)
    if attachment and os.path.exists(attachment):
        msg.add_attachment(Path(attachment).read_bytes(), maintype="text", subtype="csv",
                           filename=os.path.basename(attachment))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=60) as s:
        s.login(user, pw)
        s.send_message(msg)
    print(f"  sent '{subject}' to {', '.join(to)}")
    return True


def build_body(report_name, total_rows, g, source):
    lines = [
        f"{report_name}: {int(g['Rows'].sum())} of {total_rows:,} rows have a blank Customer State / "
        f"Customer City, because their Customer Name + Delivery Address is not in "
        f"Master Data Customer Odoo.xlsx.",
        "",
    ]
    for i, r in enumerate(g.itertuples(index=False), 1):
        lines += [
            f"{i}. {r.CustomerName}  -  {r.Rows} rows, qty {r.Qty:,.0f}, orders "
            f"{pd.Timestamp(r.FirstOrder):%d %b} to {pd.Timestamp(r.LastOrder):%d %b %Y}",
            f"   Address: {r.Address}",
            "",
        ]
    lines += [
        "To fix: add one row per customer above to Master Data Customer Odoo.xlsx (Sheet1),",
        "  column A CustomerName  = the customer name as shown",
        "  column B CustomerState = e.g. Jawa Barat (ID)   (same style as the existing rows)",
        "  column C CustomerCity  = e.g. Kota Bogor",
        "  column D Address       = the address exactly as shown (upper/lower case and extra spaces don't matter)",
        "The next Odoo run fills the blanks in. The attached CSV has the same list.",
        "",
        f"Sent by check_customer_master.py ({source}).",
    ]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--quarter", help="e.g. 2026Q3 (default: quarter in progress, WIB)")
    ap.add_argument("--report", help="path to a quarter Odoo Report.xlsx (overrides --quarter)")
    ap.add_argument("--csv", help="where to write the unmatched list (default: next to this script)")
    ap.add_argument("--no-email", action="store_true")
    ap.add_argument("--force-email", action="store_true", help="send even if already alerted today")
    args = ap.parse_args()

    if args.report:
        path = args.report
    else:
        if args.quarter:
            m = re.fullmatch(r"(\d{4})[-\s]?[Qq]([1-4])", args.quarter.strip())
            if not m:
                sys.exit(f"Bad --quarter '{args.quarter}', expected e.g. 2026Q3")
            year, quarter = int(m.group(1)), int(m.group(2))
        else:
            year, quarter = current_quarter()
        path = report_path(year, quarter)
    if not os.path.exists(path):
        sys.exit(f"Report not found: {path}")

    name = os.path.basename(path)
    total, g = find_unmatched(path)
    csv_path = args.csv or os.path.join(BASE_DIR, "unmatched_customers.csv")
    g.to_csv(csv_path, index=False, encoding="utf-8-sig")

    if g.empty:
        print(f"{name}: all {total:,} rows matched Master Data Customer Odoo.")
    else:
        print(f"MISMATCH FOUND - {name}: {int(g['Rows'].sum())} rows, {len(g)} customer+address pair(s):")
        for r in g.itertuples(index=False):
            print(f"  {r.CustomerName} | {r.Address} | {r.Rows} rows, qty {r.Qty:,.0f}")
        print(f"  list written to {csv_path}")

    if args.no_email:
        return 0

    keys = sorted(f"{r.CustomerName}|{r.Address}" for r in g.itertuples(index=False))
    today = datetime.now(WIB).strftime("%Y-%m-%d")
    state = load_state()
    prev_keys, prev_day = state.get("keys", []), state.get("sent_on")
    source = "GitHub Actions" if os.environ.get("GITHUB_ACTIONS") else "laptop"

    if not keys:
        if prev_keys:
            if send(f"[Odoo] Customer master: all rows matched again ({name})",
                    f"{name}: every row now has a Customer State and City - the earlier "
                    f"{len(prev_keys)} unmatched customer+address pair(s) are resolved.\n\n"
                    f"Sent by check_customer_master.py ({source})."):
                save_state({"keys": [], "sent_on": today})
        return 0

    if keys == prev_keys and prev_day == today and not args.force_email:
        print("  Already emailed this list today - not sending again.")
        return 0

    rows = int(g["Rows"].sum())
    subject = f"[Odoo] {rows} row{'s' if rows != 1 else ''} with blank Customer State/City - {len(g)} customer address{'es' if len(g) != 1 else ''} missing from master"
    if send(subject, build_body(name, total, g, source), csv_path):
        save_state({"keys": keys, "sent_on": today})
    return 0


if __name__ == "__main__":
    sys.exit(main())
