#!/usr/bin/env python3
"""Download the reference/prior-quarter files build_odoo_report.py and
build_master_report.py need, into this directory, before running them.

The prior-quarter list is computed from the calendar date (see
quarter_files.py) rather than hardcoded, so nothing here needs editing when
a new quarter starts - the current and previous quarters are excluded, since
build_odoo_report.py rebuilds both fresh in the same run.
"""
import os

from google.cloud import storage

from quarter_files import current_quarter, master_source_files

BUCKET = "bucket_som"

REFERENCE_FILES = [
    "sales_parquet/raw/master data/Master Data Customer Odoo.xlsx",
    "sales_parquet/raw/primary/invoice/Invoice Lumbung.xlsx",
]


def prior_quarter_sources():
    # The current AND previous quarter are both rebuilt fresh in the same run
    # (odoo_quarterly_export.py --include-previous), so only older ones come from GCS.
    year, quarter = current_quarter()
    for _ in range(2):
        quarter -= 1
        if quarter == 0:
            quarter, year = 4, year - 1
    all_but_current = master_source_files(end_year=year, end_quarter=quarter)
    return [f"sales_parquet/raw/primary/odoo/{name}" for name in all_but_current]


def main():
    client = storage.Client()
    bucket = client.bucket(BUCKET)
    for source in REFERENCE_FILES + prior_quarter_sources():
        dest = os.path.basename(source)
        blob = bucket.blob(source)
        if not blob.exists():
            raise SystemExit(f"Missing in GCS: gs://{BUCKET}/{source}")
        blob.download_to_filename(dest)
        print(f"Downloaded gs://{BUCKET}/{source} -> {dest}")


if __name__ == "__main__":
    main()
