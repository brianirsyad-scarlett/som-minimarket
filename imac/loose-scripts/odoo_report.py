#!/usr/bin/env python3
"""Every-2-hours refresh -- CURRENT quarter every run, PREVIOUS quarter
only once per Jakarta day (memory/bandwidth efficiency: last quarter's
data barely changes, so re-fetching + re-syncing it every 2 hours is
wasted work; see should_refresh_previous_quarter()).

1. Re-export the current quarter (and the previous quarter, on the one
   daily run that refreshes it) from Odoo (web session), reusing
   odoo_export.py -> writes 'Sales Order YYYY Qn.xlsx' and
   'Sales Analysis YYYY Qn.xlsx' into the OneDrive export folder.
2. Combine current + previous quarter into one merged dataset ('Odoo
   Report'), written both as .xlsx and .csv -- always both, reading
   whatever's on disk for the previous quarter (fresh on the daily run,
   up to ~24h stale otherwise).
3. Sync whatever was actually just re-fetched to GCS: upload the raw
   .xlsx to raw_sales_analysis/ + raw_sales_order/ (overwrite), then
   immediately convert each to the flat, cleaned CSV schema and overwrite
   it in the parent staging_sales_analysis/ / staging_sales_order/
   folder. This is the automatic trigger for the conversion -- it runs
   right after the raw file lands.
4. Upload the joined Odoo Report CSV to staging_odoo_report/ (overwrite),
   so the Sales Analysis + Sales Order join also lands in GCS, not just
   locally. Quantity = Qty Delivered (unconditional, no CBD branch).
   InvoiceOn and CustomerMatched/CustomerState/CustomerCity are joined in
   from staging_sales_invoice and Master Data Customer Odoo.csv.

Run:  python3 ~/odoo_report.py
Scheduled by ~/Library/LaunchAgents/com.brian.odooreport.plist (StartInterval 7200).
"""

import io
import os
import re
import subprocess
import sys
import tempfile
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

import odoo_export as oe   # ~/odoo_export.py (script dir is on sys.path)

# Service-account key for the google-cloud-storage Python client (used for
# read-heavy GCS work). gsutil (used for the up/download of individual
# files elsewhere in this script) picks up the same service account via
# `gcloud auth activate-service-account` separately -- this only affects
# calls that go through google.cloud.storage directly.
os.environ.setdefault(
    "GOOGLE_APPLICATION_CREDENTIALS",
    str(Path.home() / ".config" / "gcloud" / "keys" / "odoo-service-account.json"))

OUT_DIR = oe.OUTPUT_DIR
REPORT_XLSX = OUT_DIR / "Odoo Report.xlsx"
REPORT_CSV = OUT_DIR / "Odoo Report.csv"
UNMATCHED_CSV = OUT_DIR / "Odoo Report Unmatched Customers.csv"

# ---- GCS sync ---------------------------------------------------------
# Stable, non-synced install (~/google-cloud-sdk) -- NOT the OneDrive folder,
# which has already moved/been reorganized once and broke this path before.
GSUTIL = str(Path.home() / "google-cloud-sdk" / "bin" / "gsutil")
GCS_BUCKET = "bucket_som"
SA_RAW_PREFIX = f"gs://{GCS_BUCKET}/raw_som/odoo/staging_sales_analysis/raw_sales_analysis/"
SA_OUT_PREFIX = f"gs://{GCS_BUCKET}/raw_som/odoo/staging_sales_analysis/"
SO_RAW_PREFIX = f"gs://{GCS_BUCKET}/raw_som/odoo/staging_sales_order/raw_sales_order/"
SO_OUT_PREFIX = f"gs://{GCS_BUCKET}/raw_som/odoo/staging_sales_order/"
# Joined Sales Analysis + Sales Order ("Odoo Report") -- mirrors the other
# two staging_* folders so it sits alongside them in the same pipeline.
REPORT_OUT_PREFIX = f"gs://{GCS_BUCKET}/raw_som/odoo/staging_odoo_report/"
# Sales Invoice staging (account.invoice.report exports) -- used only to
# look up InvoiceOn (Accounting Date) per order. This source is refreshed
# manually in Odoo/GCS, not by this job, and lags behind (last raw file as
# of 2026-09 covers only through ~Aug 23) -- InvoiceOn will be blank for
# any order invoiced after that until the raw invoice export is refreshed.
SI_STAGE_PREFIX = f"gs://{GCS_BUCKET}/raw_som/odoo/staging_sales_invoice/"
# Reference list of known Customer & Address combos, semicolon-delimited:
# CustomerName;CustomerState;CustomerCity;Address;Customer&Address
MASTER_CUSTOMER_BLOB = "raw_som/odoo/Master Data Customer Odoo.csv"
UNMATCHED_OUT_PREFIX = f"gs://{GCS_BUCKET}/raw_som/odoo/staging_odoo_report/"


def _normalize_key(s):
    s = s.fillna("").astype(str).str.replace("\xa0", " ", regex=False)
    return s.str.replace(r"\s+", " ", regex=True).str.strip().str.upper()

# order_date / customer_level_1 / ... schema already used downstream in the
# bucket -- keep producing these exact column names so nothing consuming
# staging_sales_analysis/*.csv breaks.
SA_GCS_RENAME = {
    "Order Date": "order_date", "Customer Level 1": "customer_level_1",
    "Customer Level 2": "customer_level_2", "Customer State": "customer_state",
    "Customer City": "customer_city", "Customer": "customer_name",
    "Customer Entity": "customer_entity", "Salesperson": "salesperson",
    "Order Reference": "order_reference", "Status": "order_status",
    "Product": "product", "Qty Ordered": "qty_ordered",
    "Qty To Deliver": "qty_to_deliver", "Qty Delivered": "qty_delivered",
    "Qty To Invoice": "qty_to_invoice", "Qty Invoiced": "qty_invoiced",
}
SA_GCS_COLUMN_ORDER = list(SA_GCS_RENAME.values())
SA_GCS_METRIC_COLS = ["qty_ordered", "qty_to_deliver", "qty_delivered",
                      "qty_to_invoice", "qty_invoiced"]

# Final Sales-Analysis column order (after rename), before the joined fields.
SA_ORDER = [
    "CreatedOn", "CustomerLevel1", "CustomerLevel2", "CustomerState",
    "CustomerCity", "CustomerName", "CustomerEntity", "SalesPerson",
    "ItemName", "OrderReference",
    "Qty Ordered", "Qty To Deliver", "Qty Delivered",
    "Qty To Invoice", "Qty Invoiced", "Status",
]

FINAL_ORDER = [
    "CreatedOn", "CustomerLevel1", "CustomerLevel2", "CustomerState",
    "CustomerCity", "CustomerName", "CustomerEntity", "SalesPerson",
    "ItemName", "OrderReference",
    "Qty Ordered", "Qty To Deliver", "Qty Delivered",
    "Qty To Invoice", "Qty Invoiced", "Status",
    "DO Number", "SO Number",
    "Invoice Status", "SentOn", "Delivery Status", "Payment Terms", "Address",
    "Quantity", "Checked", "Customer & Address", "CustomerMatched", "InvoiceOn",
]

SA_RENAME = {
    "Order Date": "CreatedOn",
    "Customer Level 1": "CustomerLevel1",
    "Customer Level 2": "CustomerLevel2",
    "Customer State": "CustomerState",
    "Customer City": "CustomerCity",
    "Customer": "CustomerName",
    "Customer Entity": "CustomerEntity",
    "Salesperson": "SalesPerson",
    "Product": "ItemName",
    "Order Reference": "OrderReference",
}

CUSTOMER_BLOCKLIST = ("opto", "tumbuh karya abadi", "bintang berlian laboratoria")


def _now_jakarta():
    return (datetime.now(timezone.utc).replace(tzinfo=None)
            + timedelta(hours=oe.JAKARTA_OFFSET_HOURS))


def current_quarter():
    now_jkt = _now_jakarta()
    y, q = now_jkt.year, (now_jkt.month - 1) // 3 + 1
    return (y, q)


def previous_quarter(quarter=None):
    y, q = quarter or current_quarter()
    return (y - 1, 4) if q == 1 else (y, q - 1)


def current_and_previous_quarter():
    curr = current_quarter()
    return [previous_quarter(curr), curr]


# Previous-quarter data barely moves day to day -- re-fetching it from Odoo
# and re-syncing it to GCS every 2 hours is wasted work. It's refreshed
# once per Jakarta calendar day instead; every other run only touches the
# current quarter. The Odoo Report / GCS sync still always COMBINES
# current + previous, just reading whatever's already on disk for the
# previous quarter (up to ~24h stale) on the runs that skip re-fetching it.
LAST_DAILY_SYNC_FILE = Path.home() / ".odoo_report_last_daily_sync"


def should_refresh_previous_quarter():
    """True (and today's date) once per Jakarta day; False the other runs."""
    today = _now_jakarta().strftime("%Y-%m-%d")
    try:
        last = LAST_DAILY_SYNC_FILE.read_text().strip()
    except FileNotFoundError:
        last = None
    return today != last, today


def mark_previous_quarter_refreshed(today):
    LAST_DAILY_SYNC_FILE.write_text(today)


def norm_ws(s):
    """Collapse newlines / nbsp / repeated whitespace to a single space."""
    if not isinstance(s, str):
        return None
    s = re.sub(r"\s+", " ", s.replace("\xa0", " "))
    return s.strip() or None


def _clean_text_upper(val):
    """Match the bucket scripts' cleanup: collapse whitespace, strip, upper."""
    if isinstance(val, str):
        val = re.sub(r"[\r\n\t]+", " ", val)
        val = re.sub(r"\s+", " ", val).strip().upper()
    return val


def flatten_sales_analysis(local_path):
    """Flat, cleaned version of the raw Sales Analysis export, in the
    order_date/customer_level_1/... schema the bucket already expects."""
    df = pd.read_excel(local_path)
    df = df.drop(columns=["Sales Team"], errors="ignore")
    df = df.rename(columns=SA_GCS_RENAME)
    df = df[[c for c in SA_GCS_COLUMN_ORDER if c in df.columns]]
    for c in df.columns:
        if df[c].dtype == "object":
            df[c] = df[c].apply(_clean_text_upper)
    for c in SA_GCS_METRIC_COLS:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
    return df


def clean_sales_order(local_path):
    """Same cleanup as the bucket's staging_sales_order.py: flat read +
    whitespace/upper-case cleanup on text columns, no reshaping needed."""
    df = pd.read_excel(local_path)
    for c in df.columns:
        if df[c].dtype == "object":
            df[c] = df[c].apply(_clean_text_upper)
    return df


def _gsutil_cp(local_path, gcs_uri):
    subprocess.run([GSUTIL, "-q", "cp", str(local_path), gcs_uri], check=True)


def _upload_raw_and_convert(local_path, raw_prefix, out_prefix, convert_fn):
    name = local_path.name
    _gsutil_cp(local_path, raw_prefix + name)
    print(f"  GCS raw:  {raw_prefix}{name}")

    df = convert_fn(local_path)
    csv_name = name.rsplit(".", 1)[0] + ".csv"
    fd, tmp_path = tempfile.mkstemp(suffix=".csv")
    os.close(fd)
    try:
        df.to_csv(tmp_path, index=False)
        _gsutil_cp(tmp_path, out_prefix + csv_name)
    finally:
        os.remove(tmp_path)
    print(f"  GCS csv:  {len(df):,} rows -> {out_prefix}{csv_name}")


def sync_to_gcs(quarters):
    for (y, q) in quarters:
        sa_path = OUT_DIR / f"Sales Analysis {y} Q{q}.xlsx"
        so_path = OUT_DIR / f"Sales Order {y} Q{q}.xlsx"
        try:
            if sa_path.exists():
                _upload_raw_and_convert(sa_path, SA_RAW_PREFIX, SA_OUT_PREFIX,
                                        flatten_sales_analysis)
            if so_path.exists():
                _upload_raw_and_convert(so_path, SO_RAW_PREFIX, SO_OUT_PREFIX,
                                        clean_sales_order)
        except (subprocess.CalledProcessError, FileNotFoundError, OSError) as e:
            print(f"  WARNING: GCS sync failed for {y} Q{q}: {e}")


def export_quarters(quarters):
    session, url = oe.get_session()
    for (y, q) in quarters:
        q_start, q_end = oe.quarter_range_jakarta(y, q)
        oe.export_quarter(session, url, y, q, q_start, q_end)


def load_invoice_lookup():
    """order_reference -> most recent invoice Accounting Date, sourced from
    the staged Sales Invoice CSVs in GCS (staging_sales_invoice/*.csv,
    NOT the raw_sales_invoice/ subfolder). Reads via the GCS Python client
    directly (in-memory, no subprocess) -- `gsutil -m cp` on ~30 small
    files was observed to hang for 10+ minutes on this Mac; this is the
    same data over the API in under 2 seconds. Returns an empty Series
    (all lookups miss -> NaT) if the source can't be read for any reason,
    so a hiccup here never breaks the rest of the report."""
    try:
        from google.cloud import storage
        client = storage.Client()
        prefix = SI_STAGE_PREFIX.split(f"{GCS_BUCKET}/", 1)[1]
        blobs = client.list_blobs(GCS_BUCKET, prefix=prefix)

        frames = []
        for blob in blobs:
            if not blob.name.endswith(".csv"):
                continue
            try:
                data = blob.download_as_bytes()
                df = pd.read_csv(
                    io.BytesIO(data),
                    usecols=["Move/Invoice lines/Source Document", "Accounting Date"],
                    dtype=str)
            except Exception as e:
                print(f"  WARNING: could not read invoice file {blob.name}: {e}")
                continue
            frames.append(df)

        if not frames:
            return pd.Series(dtype="datetime64[ns]")
        inv = pd.concat(frames, ignore_index=True)
        inv = inv.rename(columns={
            "Move/Invoice lines/Source Document": "OrderReference",
            "Accounting Date": "InvoiceOn",
        })
        inv["OrderReference"] = inv["OrderReference"].astype(str).str.strip().str.upper()
        inv["InvoiceOn"] = pd.to_datetime(inv["InvoiceOn"], errors="coerce")
        inv = inv.dropna(subset=["OrderReference", "InvoiceOn"])
        return inv.groupby("OrderReference")["InvoiceOn"].max()
    except Exception as e:
        print(f"  WARNING: could not load Sales Invoice lookup for InvoiceOn: {e}")
        return pd.Series(dtype="datetime64[ns]")


def load_master_customer():
    """Reference list of known Customer & Address combos -> their canonical
    CustomerState/CustomerCity. Returns an empty DataFrame if the source
    can't be read, so a hiccup here never breaks the rest of the report
    (everything just falls through as unmatched)."""
    try:
        from google.cloud import storage
        client = storage.Client()
        blob = client.bucket(GCS_BUCKET).blob(MASTER_CUSTOMER_BLOB)
        data = blob.download_as_bytes()
        try:
            df = pd.read_csv(io.BytesIO(data), sep=";", encoding="utf-8-sig")
        except UnicodeDecodeError:
            df = pd.read_csv(io.BytesIO(data), sep=";", encoding="latin1")
        df = df[["CustomerState", "CustomerCity", "Customer&Address"]].copy()
        df["_key"] = _normalize_key(df["Customer&Address"])
        return df.drop_duplicates(subset=["_key"], keep="first").set_index("_key")
    except Exception as e:
        print(f"  WARNING: could not load Master Data Customer: {e}")
        return pd.DataFrame(columns=["CustomerState", "CustomerCity"])


def apply_master_customer(merged):
    """Match each row's Customer & Address against the master list: replace
    CustomerState/CustomerCity with the master's canonical values on a
    match, leave the original (Sales-Analysis-derived) values in place on a
    miss, flag every row with CustomerMatched, and return the distinct list
    of unmatched Customer & Address combos (their pre-master State/City
    kept, as a temporary placeholder until the master list is updated)."""
    master = load_master_customer()
    key = _normalize_key(merged["Customer & Address"])
    matched = key.isin(master.index)

    merged["CustomerMatched"] = np.where(matched, "Yes", "No")
    merged.loc[matched, "CustomerState"] = key[matched].map(master["CustomerState"])
    merged.loc[matched, "CustomerCity"] = key[matched].map(master["CustomerCity"])

    unmatched = (merged.loc[~matched,
                            ["CustomerName", "CustomerState", "CustomerCity",
                             "Address", "Customer & Address"]]
                 .drop_duplicates())
    print(f"  Master Data Customer: {int(matched.sum()):,} / {len(merged):,} rows matched "
          f"({unmatched.shape[0]:,} distinct unmatched Customer & Address)")
    return unmatched


def build_report(quarters):
    sa_frames, so_frames = [], []
    for (y, q) in quarters:
        sa_path = OUT_DIR / f"Sales Analysis {y} Q{q}.xlsx"
        so_path = OUT_DIR / f"Sales Order {y} Q{q}.xlsx"
        if not sa_path.exists() or not so_path.exists():
            print(f"  WARNING: missing export for {y} Q{q}, skipping that quarter")
            continue
        sa_frames.append(pd.read_excel(sa_path, dtype=str))
        so_frames.append(pd.read_excel(so_path, dtype=str))
    if not sa_frames:
        sys.exit("No quarterly exports available to combine.")

    # ---- Sales Analysis: filter + rename + reorder ----------------------
    sa = pd.concat(sa_frames, ignore_index=True)
    sa = sa[sa["Status"].str.strip().eq("Sales Done")]
    lvl2 = sa["Customer Level 2"].fillna("")
    sa = sa[~lvl2.str.contains("general", case=False, na=False)
            & ~lvl2.str.contains("intercompany", case=False, na=False)]
    for bad in CUSTOMER_BLOCKLIST:
        sa = sa[~sa["Customer"].fillna("").str.contains(bad, case=False, na=False)]
    sa = sa[sa["Product"].fillna("").str.startswith("[8")]

    sa = sa.drop(columns=["Sales Team"]).rename(columns=SA_RENAME)[SA_ORDER]
    sa["CreatedOn"] = pd.to_datetime(sa["CreatedOn"], errors="coerce")
    for c in ["Qty Ordered", "Qty To Deliver", "Qty Delivered",
              "Qty To Invoice", "Qty Invoiced"]:
        sa[c] = pd.to_numeric(sa[c], errors="coerce")

    # ---- Sales Order: shape the joined fields --------------------------
    so = pd.concat(so_frames, ignore_index=True).rename(columns={
        "Creation Date": "Order Date",
        "Delivery Address": "Address",
        "Effective Date": "SentOn",
    })
    so = so[["Order Reference", "Invoice Status", "SentOn",
             "Payment Terms", "Address"]].copy()
    so["Address"] = so["Address"].map(norm_ws)
    so["SentOn"] = pd.to_datetime(so["SentOn"], errors="coerce")
    so = so.drop_duplicates(subset=["Order Reference"], keep="first")
    so["Delivery Status"] = pd.NA                        # not in the export
    so = so[["Order Reference", "Invoice Status", "SentOn",
             "Delivery Status", "Payment Terms", "Address"]]

    # ---- Left join ----------------------------------------------------
    merged = sa.merge(so, how="left",
                      left_on="OrderReference", right_on="Order Reference")
    merged = merged.drop(columns=["Order Reference"])

    # ---- Extra columns ---------------------------------------------------
    merged["DO Number"] = pd.NA
    merged["SO Number"] = pd.NA
    merged["Quantity"] = merged["Qty Delivered"]
    merged["Checked"] = pd.NA
    merged["Customer & Address"] = (merged["CustomerName"].fillna("").astype(str)
                                    + "-" + merged["Address"].fillna("").astype(str))

    invoice_lookup = load_invoice_lookup()
    invoice_key = merged["OrderReference"].astype(str).str.strip().str.upper()
    merged["InvoiceOn"] = invoice_key.map(invoice_lookup)

    # Master Data Customer: overwrite CustomerState/CustomerCity on a match,
    # leave the Sales-Analysis-derived values in place (temporary) on a miss.
    unmatched = apply_master_customer(merged)

    merged = merged[FINAL_ORDER]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    merged.to_excel(REPORT_XLSX, index=False)
    merged.to_csv(REPORT_CSV, index=False)
    unmatched.to_csv(UNMATCHED_CSV, index=False)
    matched = merged["Invoice Status"].notna().sum()
    print(f"  Odoo Report: {len(merged):,} rows x {merged.shape[1]} cols  "
          f"({matched/len(merged):.1%} joined)")
    print(f"  wrote {REPORT_XLSX}")
    print(f"  wrote {REPORT_CSV}")
    print(f"  wrote {UNMATCHED_CSV}  ({len(unmatched):,} distinct unmatched customers)")


def upload_report_to_gcs():
    """Push the joined Odoo Report CSV + the unmatched-customers CSV to
    GCS, overwriting each run."""
    for path, prefix in [(REPORT_CSV, REPORT_OUT_PREFIX),
                         (UNMATCHED_CSV, UNMATCHED_OUT_PREFIX)]:
        try:
            _gsutil_cp(path, prefix + path.name)
            print(f"  GCS csv:  {prefix}{path.name}")
        except (subprocess.CalledProcessError, FileNotFoundError, OSError) as e:
            print(f"  WARNING: {path.name} GCS upload failed: {e}")


def main():
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    curr_q = current_quarter()
    prev_q = previous_quarter(curr_q)
    do_prev, today = should_refresh_previous_quarter()

    fetch_quarters = [prev_q, curr_q] if do_prev else [curr_q]
    report_quarters = [prev_q, curr_q]  # report always covers both
    label = " + ".join(f"{y} Q{q}" for y, q in fetch_quarters)
    print(f"\n===== Odoo Report run {stamp}  (fetching {label}"
          f"{' -- daily previous-quarter refresh' if do_prev else ''}) =====")
    try:
        export_quarters(fetch_quarters)
        build_report(report_quarters)   # reads whatever's on disk for both
        sync_to_gcs(fetch_quarters)      # only re-sync what was re-fetched
        upload_report_to_gcs()
        if do_prev:
            mark_previous_quarter_refreshed(today)
        print("===== done =====")
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
