"""Publish Alfamart / Alfamidi sell out from the cloud draft to PRODUCTION GCS.

The cloud replacement for the GCS side of the laptop chain
2_csv_converter.py -> 1_summary_sell_out.py -> 3_upload_and_distribute.py:

  by branch  newest Value+Qty pair per month (current + previous), summarised
             exactly like 1_summary_sell_out.py, written as the converter's CSV
             -> sales_sell out_minimarket/<brand>/sell_out/<YYYYMM>_Sell Out <Brand>_Sell Out.csv
  by store   newest snapshot of each rolling 10-day period, copied inside GCS
             -> sales_sell out_minimarket/<brand>/daily_sell_out_qty/ and .../daily_sell_out_value/

    python publish_production.py --brand alfamart
    python publish_production.py --brand alfamidi --dry-run

Deliberate differences from the laptop, both fixes:
- By branch uses only the NEWEST pair per month. The laptop summarised every
  pair it found into the same output name, in arbitrary order, so a stale
  earlier cut (e.g. _sd_09-18 next to _sd_09-23) could win.
- By store publishes the newest snapshot of all 3 rolling periods, not only
  the current one. A closed period's final file keeps the same name, so this
  is a no-op once it is there - but it heals gaps: production went stale from
  2026-09-14 while the laptop's uploads failed silently.

Not ported: the OneDrive -> iMac copy (the user chose to leave it as it is).
"""

import argparse
import io
import re
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

import pandas as pd
from google.cloud import storage

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE / "sources"))
import gcs_paths  # noqa: E402
from period_utils import branch_months, rolling_periods  # noqa: E402

PROD_ROOT = "sales_sell out_minimarket"
BRAND_TITLE = {"alfamart": "Alfamart", "alfamidi": "Alfamidi"}
DATES_RE = re.compile(r"_(\d{4}-\d{2}-\d{2})_sd_(\d{4}-\d{2}-\d{2})")
BY_STORE_RE = re.compile(r"detail_performance_Selling_Out_(Value|Qty)_.*_All_Store")
BY_BRANCH_RE = re.compile(r"detail_performance_by_branch_Selling_Out_(Value|Qty)_")
HEADER_START = "kode_branch|branch_name|tgl|plu|descp|"


def dates_of(name):
    m = DATES_RE.search(name)
    return (date.fromisoformat(m.group(1)), date.fromisoformat(m.group(2))) if m else (None, None)


# ---------- by branch: identical logic to 1_summary_sell_out.py ----------------

def read_pipe_file(text: str) -> pd.DataFrame:
    lines = text.splitlines(keepends=True)
    idx = next((i for i, l in enumerate(lines) if l.startswith(HEADER_START)), None)
    if idx is None:
        raise ValueError("Header not found")
    df = pd.read_csv(io.StringIO("".join(lines[idx:])), sep="|")
    df.columns = df.columns.str.strip()
    return df


def summarise(value_text: str, qty_text: str) -> pd.DataFrame:
    value_df, qty_df = read_pipe_file(value_text), read_pipe_file(qty_text)
    value_df = value_df[~value_df["descp"].str.contains("PLU K", case=False, na=False)]
    qty_df = qty_df[~qty_df["descp"].str.contains("PLU K", case=False, na=False)]
    value_df = value_df.rename(columns={value_df.columns[-1]: "value"})
    qty_df = qty_df.rename(columns={qty_df.columns[-1]: "qty"})
    value_df["date"] = pd.to_datetime(value_df["tgl"], format="%d-%b-%y")
    qty_df["date"] = pd.to_datetime(qty_df["tgl"], format="%d-%b-%y")
    agg = ["branch_name", "descp", "date"]
    merged = pd.merge(value_df.groupby(agg, as_index=False)["value"].sum(),
                      qty_df.groupby(agg, as_index=False)["qty"].sum(), on=agg, how="outer")
    merged["value"] = merged["value"].fillna(0)
    merged["qty"] = merged["qty"].fillna(0)
    merged = merged[merged["value"] != merged["qty"]]
    merged = merged.rename(columns={"date": "Date", "branch_name": "Branch", "descp": "Product",
                                    "value": "Value IDR", "qty": "Value Qty"})
    merged = merged[["Date", "Branch", "Product", "Value IDR", "Value Qty"]]
    return merged.sort_values(["Date", "Branch", "Product"])


def as_converter_csv(df: pd.DataFrame) -> bytes:
    """Round-trip through Excel exactly like the laptop (summary -> .xlsx ->
    2_csv_converter -> .csv), so production gets byte-for-byte the same
    formatting: whole numbers come back from Excel as ints, dates as dates."""
    buf = io.BytesIO()
    df.to_excel(buf, index=False, sheet_name="Sell Out")
    buf.seek(0)
    back = pd.read_excel(buf, sheet_name="Sell Out")
    return back.to_csv(index=False).encode("utf-8-sig")


def publish_branch(bucket, brand, today, dry):
    src_prefix = gcs_paths.prefix(brand, "sell_out_branch")
    pairs = defaultdict(dict)   # (month_start, end) -> {"Value": blob, "Qty": blob}
    for b in bucket.list_blobs(prefix=src_prefix):
        name = b.name.rsplit("/", 1)[-1]
        m = BY_BRANCH_RE.search(name)
        start, end = dates_of(name)
        if m and start:
            pairs[(start, end)][m.group(1)] = b
    done = 0
    for month in branch_months(today):
        full = [(end, p) for (start, end), p in pairs.items()
                if start == month["start"] and {"Value", "Qty"} <= p.keys()]
        if not full:
            print(f"  by-branch {month['start']:%Y-%m}: no complete Value+Qty pair in the draft yet")
            continue
        end, p = max(full, key=lambda t: t[0])
        yyyymm = f"{month['start']:%Y%m}"
        out_name = f"{yyyymm}_Sell Out {BRAND_TITLE[brand]}_Sell Out.csv"
        dest = f"{PROD_ROOT}/{brand}/sell_out/{out_name}"
        df = summarise(p["Value"].download_as_text(encoding="utf-8"),
                       p["Qty"].download_as_text(encoding="utf-8"))
        data = as_converter_csv(df)
        if dry:
            print(f"  [dry-run] by-branch {yyyymm} (cut to {end}) -> gs://{bucket.name}/{dest}  ({len(df):,} rows)")
        else:
            bucket.blob(dest).upload_from_string(data, content_type="text/csv")
            print(f"  published by-branch {yyyymm} (cut to {end}) -> gs://{bucket.name}/{dest}  ({len(df):,} rows)")
        done += 1
    return done


# ---------- by store: newest snapshot per rolling period ---------------------

def publish_store(bucket, brand, today, dry):
    src_prefix = gcs_paths.prefix(brand, "sell_out_store")
    blobs = [b for b in bucket.list_blobs(prefix=src_prefix)
             if BY_STORE_RE.search(b.name) and "all_category" not in b.name.lower()]
    copied = same = 0
    for period in rolling_periods(today, 3):
        best = {}   # name with dates blanked -> (end, blob)
        for b in blobs:
            name = b.name.rsplit("/", 1)[-1]
            start, end = dates_of(name)
            if start != period["start"] or end > period["end"]:
                continue
            key = DATES_RE.sub("", name)
            if key not in best or end > best[key][0]:
                best[key] = (end, b)
        for _, src in best.values():
            name = src.name.rsplit("/", 1)[-1]
            kind = BY_STORE_RE.search(name).group(1)
            sub = "daily_sell_out_qty" if kind == "Qty" else "daily_sell_out_value"
            dest_name = f"{PROD_ROOT}/{brand}/{sub}/{name}"
            existing = bucket.get_blob(dest_name)
            if existing is not None and existing.md5_hash == src.md5_hash:
                same += 1
                continue
            if dry:
                print(f"  [dry-run] copy -> gs://{bucket.name}/{dest_name}")
            else:
                bucket.copy_blob(src, bucket, dest_name)   # server-side, no download
            copied += 1
        print(f"  by-store {period['start']}..{period['end']}: {len(best)} newest snapshot(s) in the draft")
    return copied, same


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--brand", required=True, choices=sorted(BRAND_TITLE))
    ap.add_argument("--dry-run", action="store_true", help="show what would be written, write nothing")
    a = ap.parse_args()

    today = date.today()   # the workflow sets TZ=Asia/Jakarta
    bucket = storage.Client().bucket(gcs_paths.BUCKET)
    print(f"--- {a.brand}: draft -> production ({'DRY RUN' if a.dry_run else 'LIVE'}) ---")
    summaries = publish_branch(bucket, a.brand, today, a.dry_run)
    copied, same = publish_store(bucket, a.brand, today, a.dry_run)
    print(f"--- {a.brand}: {summaries} by-branch summary file(s), "
          f"{copied} by-store file(s) {'to copy' if a.dry_run else 'copied'}, {same} already identical ---")
    return 0


if __name__ == "__main__":
    sys.exit(main())
