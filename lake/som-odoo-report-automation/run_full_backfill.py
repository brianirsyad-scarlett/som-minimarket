#!/usr/bin/env python3
"""Weekly backfill: re-export and rebuild every quarter from 2026 Q1 through
the current quarter, then rebuild the combined master Odoo Report.

Odoo and Accurate keep back-dating entries into already-reported quarters
for a while after they close, so a quarter's numbers can still shift after
its own quarter has ended - this catches those corrections across all
quarters, not just the current one. The 2025 legacy annual file predates
this pipeline and isn't re-exportable from Odoo by quarter, so it's left as
downloaded, unchanged.
"""
import subprocess
import sys

from google.cloud import storage

from quarter_files import LEGACY_ANNUAL_FILE, current_quarter, quarter_prefix, quarters_through

BUCKET = "bucket_som"
DEST_PREFIX = "sales_parquet/raw/primary/odoo/"

SETUP_FILES = [
    "sales_parquet/raw/master data/Master Data Customer Odoo.xlsx",
    "sales_parquet/raw/primary/invoice/Invoice Lumbung.xlsx",
    f"sales_parquet/raw/primary/odoo/{LEGACY_ANNUAL_FILE}",
]


def run(*args):
    print(f"$ {' '.join(args)}")
    subprocess.run([sys.executable, *args], check=True)


def main():
    client = storage.Client()
    bucket = client.bucket(BUCKET)

    for source in SETUP_FILES:
        dest = source.rsplit("/", 1)[-1]
        bucket.blob(source).download_to_filename(dest)
        print(f"Downloaded gs://{BUCKET}/{source} -> {dest}")

    end_year, end_quarter = current_quarter()
    quarters = list(quarters_through(end_year, end_quarter))
    print(f"Backfilling {len(quarters)} quarter(s): "
          + ", ".join(f"{y}Q{q}" for y, q in quarters))

    for year, quarter in quarters:
        label = f"{year}Q{quarter}"
        run("odoo_quarterly_export.py", "--quarter", label)
        run("build_odoo_report.py", "--quarter", label, "--replace-production")

    run("build_master_report.py", "--replace-production")

    filenames = ["Odoo Report.xlsx"]
    for year, quarter in quarters:
        prefix = quarter_prefix(year, quarter)
        filenames += [
            f"{prefix} Sales Order.xlsx",
            f"{prefix} Sales Analysis.xlsx",
            f"{prefix} Odoo Report.xlsx",
        ]

    for fname in filenames:
        dest = DEST_PREFIX + fname
        bucket.blob(dest).upload_from_filename(fname)
        print(f"Uploaded {fname} -> gs://{BUCKET}/{dest}")


if __name__ == "__main__":
    main()
