#!/usr/bin/env python3
"""
Odoo exporter - Sales Orders (current quarter, confirmed only) +
Sales Analysis (current month, Offline channel, excluding 'opto').
Field names and filters captured directly from your browser's actual
requests (via HAR export), so they match your real Odoo setup exactly.
Uses the same web-session login as your browser (not XML-RPC).
"""

import argparse
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
import pandas as pd
from dotenv import load_dotenv

OUTPUT_DIR = Path.home() / "Library/CloudStorage/OneDrive-PT.OptoLumbungSejahtera/SOM_iMac/iMac_Sales Ops/Odoo Export"

JAKARTA_OFFSET_HOURS = 7  # Asia/Jakarta has no DST, always UTC+7

# --- Sales Order ---
SALES_ORDER_FIELDS = [
    "name", "create_date", "partner_id", "shipping_address",
    "partner_shipping_id", "amount_total", "invoice_status", "effective_date",
    "payment_term_id", "user_id", "state",
]
SALES_ORDER_HEADERS = [
    "Order Reference", "Creation Date", "Customer", "Delivery Address",
    "Delivery Address", "Total", "Invoice Status", "Effective Date",
    "Payment Terms", "Salesperson", "Status",
]

# --- Sales Analysis ---
SALES_ANALYSIS_FIELDS = [
    "date", "partner_analytic_level_1", "partner_analytic_level_2",
    "state_id", "city", "partner_id", "commercial_partner_id", "user_id",
    "name", "state", "product_tmpl_id", "team_id",
    "product_uom_qty", "qty_to_deliver", "qty_delivered",
    "qty_to_invoice", "qty_invoiced",
]
SALES_ANALYSIS_RENAME = {
    "date": "Order Date",
    "partner_analytic_level_1": "Customer Level 1",
    "partner_analytic_level_2": "Customer Level 2",
    "state_id": "Customer State",
    "city": "Customer City",
    "partner_id": "Customer",
    "commercial_partner_id": "Customer Entity",
    "user_id": "Salesperson",
    "name": "Order Reference",
    "state": "Status",
    "product_tmpl_id": "Product",
    "team_id": "Sales Team",
    "product_uom_qty": "Qty Ordered",
    "qty_to_deliver": "Qty To Deliver",
    "qty_delivered": "Qty Delivered",
    "qty_to_invoice": "Qty To Invoice",
    "qty_invoiced": "Qty Invoiced",
}


def get_session():
    env_path = Path.home() / ".odoo_export.env"
    if not env_path.exists():
        sys.exit(f"Missing credentials file: {env_path}")
    load_dotenv(env_path)

    url = os.environ["ODOO_URL"].rstrip("/")
    db = os.environ["ODOO_DB"]
    username = os.environ["ODOO_USER"]
    password = os.environ["ODOO_PASSWORD"]

    session = requests.Session()
    auth_resp = session.post(
        f"{url}/web/session/authenticate",
        json={"jsonrpc": "2.0", "method": "call",
              "params": {"db": db, "login": username, "password": password}},
        timeout=30,
    )
    auth_resp.raise_for_status()
    auth_data = auth_resp.json()
    if auth_data.get("error"):
        sys.exit(f"Login failed: {auth_data['error']}")
    if not (auth_data.get("result") or {}).get("uid"):
        sys.exit("Login failed: no session returned. Check ODOO_DB/ODOO_USER/ODOO_PASSWORD.")
    return session, url


def fetch(session, url, model, domain, fields, timeout=600, retries=3):
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            resp = session.post(
                f"{url}/web/dataset/call_kw",
                json={"jsonrpc": "2.0", "method": "call",
                      "params": {"model": model, "method": "search_read",
                                  "args": [domain, fields], "kwargs": {}}},
                timeout=timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("error"):
                sys.exit(f"Query on {model} failed: {data['error']}")
            return data.get("result", [])
        except (requests.Timeout, requests.ConnectionError) as e:
            last_err = e
            if attempt < retries:
                wait = 15 * attempt
                print(f"  {model}: {type(e).__name__} on attempt {attempt}/{retries}, "
                      f"retrying in {wait}s...")
                time.sleep(wait)
    sys.exit(f"Query on {model} failed after {retries} attempts: {last_err}")


def get_selection_labels(session, url, model, fields):
    """Fetch human-readable labels for selection-type fields (e.g. state
    codes like 'to_approve' -> 'To Be Approved'), straight from Odoo's own
    field metadata, so we never have to hand-guess custom labels."""
    resp = session.post(
        f"{url}/web/dataset/call_kw",
        json={"jsonrpc": "2.0", "method": "call",
              "params": {"model": model, "method": "fields_get",
                          "args": [fields], "kwargs": {"attributes": ["selection"]}}},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("error"):
        return {}
    labels = {}
    for fname, meta in (data.get("result") or {}).items():
        sel = meta.get("selection")
        if sel:
            labels[fname] = dict(sel)
    return labels


def apply_selection_labels(records, labels):
    for record in records:
        for fname, mapping in labels.items():
            if fname in record and record[fname] in mapping:
                record[fname] = mapping[record[fname]]


def quarter_range_jakarta(year, q):
    """UTC start/end strings for calendar quarter q of `year`, where the
    quarter boundaries are defined in Asia/Jakarta local time (UTC+7)."""
    start_month = 3 * (q - 1) + 1
    start_jakarta = datetime(year, start_month, 1)
    end_jakarta = (datetime(year + 1, 1, 1)
                   if q == 4
                   else datetime(year, start_month + 3, 1))
    start_utc = start_jakarta - timedelta(hours=JAKARTA_OFFSET_HOURS)
    end_utc = end_jakarta - timedelta(hours=JAKARTA_OFFSET_HOURS) - timedelta(seconds=1)
    return (start_utc.strftime("%Y-%m-%d %H:%M:%S"),
            end_utc.strftime("%Y-%m-%d %H:%M:%S"))


def current_quarter_range_jakarta():
    now_jakarta = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=JAKARTA_OFFSET_HOURS)
    q = (now_jakarta.month - 1) // 3 + 1
    q_start, q_end = quarter_range_jakarta(now_jakarta.year, q)
    return q, q_start, q_end


def parse_quarter(text):
    """'2024Q4', '2024-Q4', 'Q4 2024', '2024/4' -> (2024, 4)."""
    m = re.search(r"(\d{4})\D*Q?\D*([1-4])", text, re.IGNORECASE) \
        or re.search(r"Q?([1-4])\D+(\d{4})", text, re.IGNORECASE)
    if not m:
        raise argparse.ArgumentTypeError(f"Cannot parse quarter: {text!r} (use e.g. 2024Q4)")
    a, b = m.group(1), m.group(2)
    year, q = (int(a), int(b)) if len(a) == 4 else (int(b), int(a))
    return year, q


def quarters_between(start, end):
    (sy, sq), (ey, eq) = start, end
    if (sy, sq) > (ey, eq):
        sys.exit(f"Start quarter {sy}Q{sq} is after end quarter {ey}Q{eq}.")
    out, y, q = [], sy, sq
    while (y, q) <= (ey, eq):
        out.append((y, q))
        y, q = (y + 1, 1) if q == 4 else (y, q + 1)
    return out



def save(records, filename, rename_map=None, headers=None, field_order=None):
    if not records:
        print(f"{filename}: no records returned.")
        return
    df = pd.DataFrame(records)
    if "id" in df.columns:
        df = df.drop(columns=["id"])
    for col in df.columns:
        if df[col].apply(lambda v: isinstance(v, list)).any():
            df[col] = df[col].apply(lambda v: v[1] if isinstance(v, list) else v)
    # Odoo returns False (not blank) for any empty field via the API.
    # Only clear actual Python False, never numeric 0 (0 == False in Python).
    df = df.map(lambda v: None if v is False else v)
    if field_order and headers:
        df = df[field_order]
        df.columns = headers
    elif rename_map:
        df = df.rename(columns=rename_map)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / f"{filename}.xlsx"
    df.to_excel(out_path, index=False)
    print(f"Saved {len(df)} rows to {out_path}")


def export_quarter(session, url, year, q, q_start, q_end):
    print(f"\n=== {year} Q{q}  ({q_start} .. {q_end} UTC) ===")

    so_domain = [
        ("state", "not in", ["draft", "sent", "cancel"]),
        ("date_order", ">=", q_start),
        ("date_order", "<=", q_end),
    ]
    so_records = fetch(session, url, "sale.order", so_domain, SALES_ORDER_FIELDS)
    so_labels = get_selection_labels(session, url, "sale.order", SALES_ORDER_FIELDS)
    apply_selection_labels(so_records, so_labels)
    save(so_records, f"Sales Order {year} Q{q}",
         headers=SALES_ORDER_HEADERS, field_order=SALES_ORDER_FIELDS)

    sa_domain = [
        ("state", "not in", ["draft", "cancel", "sent"]),
        ("partner_analytic_level_1", "ilike", "Offline"),
        ("partner_id", "not ilike", "opto"),
        ("date", ">=", q_start),
        ("date", "<=", q_end),
    ]
    sa_records = fetch(session, url, "sale.report", sa_domain, SALES_ANALYSIS_FIELDS)
    sa_labels = get_selection_labels(session, url, "sale.report", SALES_ANALYSIS_FIELDS)
    apply_selection_labels(sa_records, sa_labels)
    save(sa_records, f"Sales Analysis {year} Q{q}", SALES_ANALYSIS_RENAME)


def main():
    parser = argparse.ArgumentParser(
        description="Export Odoo Sales Orders + Sales Analysis, per quarter.")
    parser.add_argument("--from", dest="from_q", type=parse_quarter, metavar="YYYYQn",
                        help="First quarter to export, e.g. 2024Q4. "
                             "Omit to export the current quarter only.")
    parser.add_argument("--to", dest="to_q", type=parse_quarter, metavar="YYYYQn",
                        help="Last quarter to export (inclusive). Defaults to --from.")
    args = parser.parse_args()

    session, url = get_session()

    if args.from_q:
        end_q = args.to_q or args.from_q
        targets = quarters_between(args.from_q, end_q)
        print(f"Exporting {len(targets)} quarter(s): "
              f"{targets[0][0]}Q{targets[0][1]} .. {targets[-1][0]}Q{targets[-1][1]}")
        for year, q in targets:
            q_start, q_end = quarter_range_jakarta(year, q)
            export_quarter(session, url, year, q, q_start, q_end)
    else:
        q, q_start, q_end = current_quarter_range_jakarta()
        export_quarter(session, url, datetime.now().year, q, q_start, q_end)


if __name__ == "__main__":
    main()
