#!/usr/bin/env python3
"""Upload this run's outputs to GCS: the two raw Odoo exports and the rebuilt
quarter report for BOTH the previous and the current quarter, and the rebuilt all-time master report.

Uploads explicit filenames rather than every *.xlsx in the directory, since
the working directory also holds downloaded reference/prior-quarter files
(Master Data Customer Odoo.xlsx, Invoice Lumbung.xlsx, prior quarters) that
must not be re-uploaded to the wrong place.
"""
import os
from datetime import datetime

from google.cloud import storage

BUCKET = "bucket_som"
PREFIX = "sales_parquet/raw/primary/odoo/"

SEQ_BASE_YEAR = 2026
SEQ_BASE_NUMBER = 13


def quarter_prefixes():
    """Previous quarter first, then the current one (both are rebuilt each run)."""
    today = datetime.now()
    year, quarter = today.year, (today.month - 1) // 3 + 1
    prev_year, prev_quarter = (year - 1, 4) if quarter == 1 else (year, quarter - 1)
    prefixes = []
    for y, q in ((prev_year, prev_quarter), (year, quarter)):
        seq = SEQ_BASE_NUMBER + (y - SEQ_BASE_YEAR) * 4 + (q - 1)
        prefixes.append(f"{seq}. {y} Q{q}")
    return prefixes


def main():
    filenames = []
    for prefix in quarter_prefixes():
        filenames += [
            f"{prefix} Sales Order.xlsx",
            f"{prefix} Sales Analysis.xlsx",
            f"{prefix} Odoo Report.xlsx",
        ]
    filenames.append("Odoo Report.xlsx")

    client = storage.Client()
    bucket = client.bucket(BUCKET)
    for fname in filenames:
        if not os.path.exists(fname):
            raise SystemExit(f"Expected output file not found: {fname}")
        dest = PREFIX + fname
        bucket.blob(dest).upload_from_filename(fname)
        print(f"Uploaded {fname} -> gs://{BUCKET}/{dest}")


if __name__ == "__main__":
    main()
