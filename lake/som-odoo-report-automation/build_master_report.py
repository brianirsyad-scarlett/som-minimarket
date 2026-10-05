#!/usr/bin/env python3
"""Rebuild "Odoo Report.xlsx" - the all-time report combining every
"<seq>. <year> [Qn] Odoo Report.xlsx" file in this folder.

Reimplements, in pandas, the workbook's embedded Power Query (decoded from
its DataMashup part): union every "*Report*202*.xlsx" file's data table,
then upper-case a fixed set of text columns.

Unlike the original M query, this script does NOT scan folders/subfolders -
it takes an explicit file list, so files under Odoo_automation_backups (or
anywhere else) can never be silently double-counted the way they could be if
this workbook were ever refreshed in Excel again (Folder.Files() recurses
into subfolders, and a backup .xlsx has the same embedded table name pattern
the query keys on).
"""

import argparse
import os
import shutil
import sys
from datetime import datetime

import pandas as pd

from quarter_files import master_source_files
from xlsx_table_writer import write_as_table

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# On the local machine this points outside the Odoo folder; on the ephemeral
# GitHub Actions runner there is no equivalent to protect, so it's just a
# throwaway subfolder here.
BACKUP_ROOT = os.path.join(BASE_DIR, "_backup")

UPPERCASE_COLUMNS = [
    "CustomerLevel1", "CustomerLevel2", "CustomerState", "CustomerCity", "CustomerName",
    "CustomerEntity", "SalesPerson", "ItemName", "Status", "DO Number", "SO Number",
    "Invoice Status", "Address", "Payment Terms", "Customer & Address", "Checked",
]


def build_master(source_paths):
    frames = []
    for path in source_paths:
        print(f"Reading {path} ...")
        # keep_default_na=False: some source rows have the literal text "None"
        # in place of a real value (a pre-existing data-quality quirk); pandas'
        # default NA-string list would otherwise silently turn that text into
        # a null, which the original Power Query never did.
        df = pd.read_excel(path, sheet_name=0, keep_default_na=False, na_values=[""])
        print(f"  {len(df):,} rows")
        frames.append(df)

    combined = pd.concat(frames, ignore_index=True)
    for col in UPPERCASE_COLUMNS:
        if col in combined.columns:
            combined[col] = combined[col].astype("string").str.upper()

    return combined


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--files", nargs="+", default=None,
        help="Quarterly/yearly Odoo Report files to combine (relative to this folder unless absolute). "
        "Defaults to every quarter from 2026 Q1 through the current quarter, plus the 2025 legacy file.",
    )
    parser.add_argument("--out", help="Output path. Must not be the production 'Odoo Report.xlsx'.")
    parser.add_argument(
        "--replace-production", action="store_true",
        help="Back up and overwrite the real 'Odoo Report.xlsx' in this folder instead of writing to --out.",
    )
    args = parser.parse_args()
    if not args.out and not args.replace_production:
        sys.exit("Pass either --out <path> or --replace-production.")

    files = args.files if args.files is not None else master_source_files()
    source_paths = [p if os.path.isabs(p) else os.path.join(BASE_DIR, p) for p in files]
    missing = [p for p in source_paths if not os.path.exists(p)]
    if missing:
        sys.exit("Missing source file(s):\n  " + "\n  ".join(missing))

    if args.replace_production:
        out_path = os.path.join(BASE_DIR, "Odoo Report.xlsx")
    else:
        out_path = os.path.abspath(args.out)
        if os.path.basename(out_path).lower() == "odoo report.xlsx":
            sys.exit("Refusing to write directly over the production 'Odoo Report.xlsx' - pass --replace-production instead.")

    combined = build_master(source_paths)

    if args.replace_production and os.path.exists(out_path):
        os.makedirs(BACKUP_ROOT, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = os.path.join(BACKUP_ROOT, f"{os.path.basename(out_path)}.{stamp}.bak")
        shutil.copy2(out_path, backup_path)
        print(f"Backed up existing production file to {backup_path}")

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    write_as_table(combined, out_path, table_name="Odoo_Report")
    print(f"\nWrote {len(combined):,} rows to {out_path}")


if __name__ == "__main__":
    main()
