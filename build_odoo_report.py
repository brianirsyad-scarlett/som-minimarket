#!/usr/bin/env python3
"""Pure-Python rebuild of the "<seq>. <year> Qn Odoo Report.xlsx" workbook.

Reimplements, in pandas, the logic that used to live in:
  - the workbook's embedded Power Query ("Odoo Report" / "Odoo - Sales Order"
    queries, decoded from its DataMashup part), and
  - the worksheet's per-row XLOOKUP calculated columns (CustomerState,
    CustomerCity, Customer & Address, InvoiceOn).

Known, accepted deviation from the original M code (per business decision):
Customer Reference / DO Number / SO Number are no longer present in the
Sales Order export, so they are left blank instead of being parsed out of
Customer Reference, the "drop rows with no DO Number" filter is skipped, and
"Checked" always resolves to "-Not Found" (a real DO Number can never match
the 2024 Accurate Report lookup).

This script only ever writes to an explicit --out path - it never touches
the production "Odoo Report.xlsx" file.
"""

import argparse
import os
import re
import shutil
import sys
from datetime import datetime

import pandas as pd

from xlsx_table_writer import write_as_table

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# On the local machine these two reference files live outside this folder
# (D:\...\Master Data Sales\Matrix\... and D:\...\Sales Invoice\...). This
# GitHub Actions copy expects the workflow to have downloaded them into this
# same directory first (see .github/workflows/odoo-export.yml), since the
# runner has no access to the local D: drive.
MASTER_CUSTOMER_PATH = os.path.join(BASE_DIR, "Master Data Customer Odoo.xlsx")
INVOICE_LUMBUNG_PATH = os.path.join(BASE_DIR, "Invoice Lumbung.xlsx")
BACKUP_ROOT = os.path.join(BASE_DIR, "_backup")

LEVEL_COLS = [
    "CreatedOn", "CustomerLevel1", "CustomerLevel2", "CustomerState_raw", "CustomerCity_raw",
    "CustomerName", "CustomerEntity", "SalesPerson", "OrderReference", "Status", "ItemName",
]
METRIC_COLS = ["Qty Ordered", "Qty To Deliver", "Qty Delivered", "Qty To Invoice", "Qty Invoiced"]

FINAL_COLUMNS = [
    "CreatedOn", "CustomerLevel1", "CustomerLevel2", "CustomerState", "CustomerCity",
    "CustomerName", "CustomerEntity", "SalesPerson", "ItemName", "OrderReference",
    "Qty Ordered", "Qty To Deliver", "Qty Delivered", "Qty To Invoice", "Qty Invoiced",
    "Status", "DO Number", "SO Number", "Invoice Status", "SentOn", "Delivery Status",
    "Payment Terms", "Address", "Quantity", "Checked", "Customer & Address", "InvoiceOn",
]

EXCLUDED_CUSTOMER_NAMES = {"PT. Bintang Berlian Laboratoria", "TUMBUH KARYA ABADI"}
EXCLUDED_SENTON_CUSTOMER = "PT. Opto Lingkar Sejahtera"


def excel_clean_trim_upper(*parts):
    """Mirrors Excel's TRIM(CLEAN(UPPER(a & "-" & b & ...)))."""
    text = "-".join("" if p is None else str(p) for p in parts).upper()
    text = re.sub(r"[\x00-\x1f]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def unnest_sales_analysis(path):
    """Flatten the indented Sales Analysis pivot export into leaf (product) rows."""
    df_raw = pd.read_excel(path, header=None)
    current = {col: None for col in LEVEL_COLS}
    rows = []

    for _, row in df_raw.iloc[3:].iterrows():
        label = row.iloc[0]
        if pd.isna(label) or str(label).strip() in ("", "Total"):
            continue

        text = str(label)
        spaces = len(text) - len(text.lstrip(" "))
        level_idx = (spaces // 5) - 1
        clean_val = text.strip()

        if 0 <= level_idx < len(LEVEL_COLS):
            current[LEVEL_COLS[level_idx]] = clean_val
            for k in range(level_idx + 1, len(LEVEL_COLS)):
                current[LEVEL_COLS[k]] = None

            # Only leaf (product) rows carry a "[<barcode>" style item code.
            if level_idx == len(LEVEL_COLS) - 1 and "[8" in clean_val:
                record = current.copy()
                for i, metric in enumerate(METRIC_COLS, start=1):
                    value = row.iloc[i] if i < len(row) else 0
                    record[metric] = pd.to_numeric(value, errors="coerce") or 0
                rows.append(record)

    # An empty quarter (just started, no orders yet) still needs the full column set.
    result = pd.DataFrame(rows, columns=LEVEL_COLS + METRIC_COLS)
    result["CreatedOn"] = pd.to_datetime(result["CreatedOn"], format="%d %b %Y", errors="coerce")
    return result


def load_sales_order(path):
    df = pd.read_excel(path)
    # The export has two columns both labelled "Delivery Address": the char
    # field (full formatted text) comes first and is the one used downstream.
    delivery_cols = [c for c in df.columns if c == "Delivery Address"]
    address_col = delivery_cols[0] if delivery_cols else None

    out = pd.DataFrame()
    out["OrderReference"] = df["Order Reference"]
    out["Invoice Status"] = df.get("Invoice Status")
    out["SentOn"] = pd.to_datetime(df.get("Effective Date"), errors="coerce")
    out["Payment Terms"] = df.get("Payment Terms")
    if address_col is not None:
        out["Address"] = df[address_col].astype("string").str.replace("\n", " ", regex=False).str.strip()
    else:
        out["Address"] = None

    # Not exported by Odoo anymore - kept for schema parity, always blank.
    out["DO Number"] = None
    out["SO Number"] = None
    out["Delivery Status"] = None
    return out


def build_customer_lookup(path):
    df = pd.read_excel(path, sheet_name="Sheet1", usecols="A:D", header=0)
    df.columns = ["CustomerName", "CustomerState", "CustomerCity", "Address"]
    keys = df.apply(lambda r: excel_clean_trim_upper(r["CustomerName"], r["Address"]), axis=1)
    lookup = {}
    for key, state, city in zip(keys, df["CustomerState"], df["CustomerCity"]):
        if key and key not in lookup:  # XLOOKUP returns the first match
            lookup[key] = (state, city)
    return lookup


def build_invoice_lookup(path):
    df = pd.read_excel(
        path, sheet_name="Invoice Lumbung (2)",
        usecols=["Accounting Date", "SO Number", "Item Name"],
    )
    keys = df["SO Number"].astype("string").fillna("") + df["Item Name"].astype("string").fillna("")
    lookup = {}
    for key, invoice_on in zip(keys, df["Accounting Date"]):
        if key and key not in lookup:
            lookup[key] = invoice_on
    return lookup



def build_report(sales_order_path, sales_analysis_path, year, quarter):
    print(f"Reading Sales Analysis: {sales_analysis_path}")
    analysis = unnest_sales_analysis(sales_analysis_path)
    print(f"  {len(analysis):,} leaf rows")

    print(f"Reading Sales Order: {sales_order_path}")
    order = load_sales_order(sales_order_path)
    print(f"  {len(order):,} rows")

    print("Joining Sales Analysis with Sales Order on OrderReference ...")
    merged = analysis.merge(order, on="OrderReference", how="left")

    merged["Quantity"] = merged.apply(
        lambda r: r["Qty Ordered"] if r["Payment Terms"] == "CBD" else r["Qty Delivered"], axis=1
    )

    before = len(merged)
    merged = merged[
        (merged["Status"] == "Sales Done")
        & merged["SentOn"].notna()
        & (merged["CustomerName"] != EXCLUDED_SENTON_CUSTOMER)
    ]
    print(f"  Status/SentOn filter: {before:,} -> {len(merged):,} rows")

    # DO Number is always blank now, so it can never match the 2024 Accurate
    # Report lookup - "Checked" is unconditionally "-Not Found" (see module docstring).
    merged["Checked"] = "-Not Found"

    merged = merged.sort_values("CreatedOn", kind="stable")

    before = len(merged)
    merged = merged[
        (merged["CustomerLevel1"] != "GENERAL")
        & (merged["CustomerLevel2"] != "GENERAL")
        & (~merged["CustomerName"].isin(EXCLUDED_CUSTOMER_NAMES))
    ]
    print(f"  Customer exclusion filter: {before:,} -> {len(merged):,} rows")

    q_start = datetime(year, 3 * (quarter - 1) + 1, 1)
    q_end_month = 3 * (quarter - 1) + 3
    q_end = (datetime(year, q_end_month, 1).replace(day=28) + pd.offsets.MonthEnd(1)).to_pydatetime()
    before = len(merged)
    merged = merged[(merged["CreatedOn"] >= q_start) & (merged["CreatedOn"] <= q_end)]
    print(f"  Quarter date filter ({q_start.date()}..{q_end.date()}): {before:,} -> {len(merged):,} rows")

    print(f"Loading customer state/city lookup: {MASTER_CUSTOMER_PATH}")
    customer_lookup = build_customer_lookup(MASTER_CUSTOMER_PATH)
    print(f"  {len(customer_lookup):,} unique customer&address keys")

    print(f"Loading invoice date lookup: {INVOICE_LUMBUNG_PATH}")
    invoice_lookup = build_invoice_lookup(INVOICE_LUMBUNG_PATH)
    print(f"  {len(invoice_lookup):,} unique SO+item keys")

    merged["Customer & Address"] = merged.apply(
        lambda r: excel_clean_trim_upper(r["CustomerName"], r["Address"]), axis=1
    )
    state_city = merged["Customer & Address"].map(lambda k: customer_lookup.get(k, (None, None)))
    merged["CustomerState"] = [sc[0] for sc in state_city]
    merged["CustomerCity"] = [sc[1] for sc in state_city]

    invoice_key = merged["OrderReference"].astype("string").fillna("") + merged["ItemName"].astype("string").fillna("")
    merged["InvoiceOn"] = invoice_key.map(invoice_lookup)

    for col in FINAL_COLUMNS:
        if col not in merged.columns:
            merged[col] = None
    return merged[FINAL_COLUMNS]


def current_quarter():
    today = datetime.now()
    return today.year, (today.month - 1) // 3 + 1


def parse_quarter(text):
    if text.strip().lower() == "previous":
        year, quarter = current_quarter()
        return (year - 1, 4) if quarter == 1 else (year, quarter - 1)
    m = re.fullmatch(r"(\d{4})[-\s]?[Qq]([1-4])", text.strip())
    if not m:
        sys.exit(f"Bad --quarter '{text}', expected e.g. 2026Q3")
    return int(m.group(1)), int(m.group(2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quarter", help="e.g. 2026Q3. Defaults to the quarter in progress.")
    parser.add_argument("--sales-order", help="Path to the quarter's Sales Order.xlsx (default: auto from --quarter)")
    parser.add_argument("--sales-analysis", help="Path to the quarter's Sales Analysis.xlsx (default: auto from --quarter)")
    parser.add_argument("--out", help="Output path. Must not be the production Odoo Report file (use --replace-production for that).")
    parser.add_argument(
        "--replace-production",
        action="store_true",
        help="Back up and overwrite the real '<prefix> Odoo Report.xlsx' in this folder instead of writing to --out.",
    )
    args = parser.parse_args()
    if not args.out and not args.replace_production:
        sys.exit("Pass either --out <path> or --replace-production.")

    year, quarter = parse_quarter(args.quarter) if args.quarter else current_quarter()
    seq = 13 + (year - 2026) * 4 + (quarter - 1)
    prefix = f"{seq}. {year} Q{quarter}"

    sales_order_path = args.sales_order or os.path.join(BASE_DIR, f"{prefix} Sales Order.xlsx")
    sales_analysis_path = args.sales_analysis or os.path.join(BASE_DIR, f"{prefix} Sales Analysis.xlsx")

    if args.replace_production:
        out_path = os.path.join(BASE_DIR, f"{prefix} Odoo Report.xlsx")
    else:
        out_path = os.path.abspath(args.out)
        if os.path.basename(out_path).lower() == f"{prefix} odoo report.xlsx".lower():
            sys.exit("Refusing to write directly over the production 'Odoo Report.xlsx' file - pass --replace-production instead.")

    report = build_report(sales_order_path, sales_analysis_path, year, quarter)

    if args.replace_production and os.path.exists(out_path):
        os.makedirs(BACKUP_ROOT, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = os.path.join(BACKUP_ROOT, f"{os.path.basename(out_path)}.{stamp}.bak")
        shutil.copy2(out_path, backup_path)
        print(f"Backed up existing production file to {backup_path}")

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    write_as_table(report, out_path, table_name="Odoo_Report")
    print(f"\nWrote {len(report):,} rows to {out_path}")


if __name__ == "__main__":
    main()
