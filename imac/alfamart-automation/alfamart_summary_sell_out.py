#!/usr/bin/env python3
"""
alfamart_summary_sell_out.py -- combine the by-branch Value and Qty exports into
the monthly "Sell Out" summary CSV.

Port of the user's 1_summary_sell_out.py + 2_csv_converter.py, with the
intermediate .xlsx step dropped (the deliverable is the CSV). Output matches
the existing objects in
gs://bucket_som/sales_sell out_minimarket/alfamart/sell_out/ :

    <YYYYMM>_Sell Out Alfamart_Sell Out.csv
    Date,Branch,Product,Value IDR,Value Qty      (utf-8-sig)

Usage:
    alfamart_summary_sell_out.py --src-dir branch_raw --out-dir exports
"""

import argparse
import os
import re

import pandas as pd

REQUIRED_PATTERN = "detail_performance_by_branch_Selling_Out"
HEADER_PREFIX = "kode_branch|branch_name|tgl|plu|descp|"

# The portal emits English month abbreviations ("01-sep-26"), but fall back to
# Indonesian ones if that ever changes -- otherwise the parse silently yields
# NaT for Aug/Oct/Dec and rows get dropped.
ID_MONTHS = {"agu": "aug", "okt": "oct", "des": "dec", "mei": "may", "nop": "nov"}


def read_alfamart_file(filepath):
    with open(filepath, "r", encoding="utf-8") as f:
        lines = f.readlines()
    header_idx = next((i for i, l in enumerate(lines) if l.startswith(HEADER_PREFIX)), None)
    if header_idx is None:
        raise ValueError(f"Header not found in {filepath}")
    df = pd.read_csv(filepath, sep="|", skiprows=header_idx, encoding="utf-8")
    df.columns = df.columns.str.strip()
    return df


def add_date_column(df):
    raw = df["tgl"].astype(str).str.strip().str.lower()
    parsed = pd.to_datetime(raw, format="%d-%b-%y", errors="coerce")
    if parsed.isna().any():
        fixed = raw.replace({rf"-{k}-": f"-{v}-" for k, v in ID_MONTHS.items()}, regex=True)
        parsed = parsed.fillna(pd.to_datetime(fixed, format="%d-%b-%y", errors="coerce"))
    if parsed.isna().any():
        bad = raw[parsed.isna()].unique()[:5]
        raise ValueError(f"unparseable dates in tgl, e.g. {list(bad)}")
    df["date"] = parsed
    return df


def filter_data(df):
    return df[~df["descp"].str.contains("PLU K", case=False, na=False)]


def extract_yearmonth(name):
    m = re.search(r"(\d{4})-(\d{2})-\d{2}_sd_\d{4}-\d{2}-\d{2}", name)
    if m:
        return m.group(1) + m.group(2)
    m2 = re.search(r"(\d{4})-(\d{2})", name)
    return (m2.group(1) + m2.group(2)) if m2 else None


def find_pair(src_dir):
    value_file = qty_file = None
    for f in sorted(os.listdir(src_dir)):
        if not f.endswith(".csv") or REQUIRED_PATTERN not in f:
            continue
        if "_Value" in f:
            value_file = f
        elif "_Qty" in f:
            qty_file = f
    if not value_file or not qty_file:
        raise FileNotFoundError(f"need both a Value and a Qty by-branch file in {src_dir}")
    return value_file, qty_file


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src-dir", required=True)
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()

    value_file, qty_file = find_pair(args.src_dir)
    print(f"Value: {value_file}")
    print(f"Qty:   {qty_file}")

    yearmonth = extract_yearmonth(value_file) or pd.Timestamp.now().strftime("%Y%m")

    value_df = filter_data(read_alfamart_file(os.path.join(args.src_dir, value_file)))
    qty_df = filter_data(read_alfamart_file(os.path.join(args.src_dir, qty_file)))

    value_df = value_df.rename(columns={value_df.columns[-1]: "value"})
    qty_df = qty_df.rename(columns={qty_df.columns[-1]: "qty"})

    value_df = add_date_column(value_df)
    qty_df = add_date_column(qty_df)

    agg_cols = ["branch_name", "descp", "date"]
    value_agg = value_df.groupby(agg_cols, as_index=False)["value"].sum()
    qty_agg = qty_df.groupby(agg_cols, as_index=False)["qty"].sum()

    merged = pd.merge(value_agg, qty_agg, on=agg_cols, how="outer")
    merged["value"] = merged["value"].fillna(0)
    merged["qty"] = merged["qty"].fillna(0)

    before = len(merged)
    merged = merged[merged["value"] != merged["qty"]]
    print(f"dropped {before - len(merged)} row(s) where value == qty")

    merged = merged.rename(columns={
        "date": "Date", "branch_name": "Branch", "descp": "Product",
        "value": "Value IDR", "qty": "Value Qty",
    })[["Date", "Branch", "Product", "Value IDR", "Value Qty"]]
    merged = merged.sort_values(["Date", "Branch", "Product"])
    merged["Date"] = merged["Date"].dt.strftime("%Y-%m-%d")

    os.makedirs(args.out_dir, exist_ok=True)
    out_name = f"{yearmonth}_Sell Out Alfamart_Sell Out.csv"
    out_path = os.path.join(args.out_dir, out_name)
    merged.to_csv(out_path, index=False, encoding="utf-8-sig")
    print(f"saved {out_path} (rows: {len(merged)}, "
          f"{merged['Date'].min()} .. {merged['Date'].max()})")


if __name__ == "__main__":
    main()
