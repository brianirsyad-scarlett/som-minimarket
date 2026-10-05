"""Uploads each day's RAW Tokopedia export CSV (before any renaming/cleaning)
to GCS as an audit trail, separate from the cleaned TikTok.parquet that
BigQuery's sales_raw_som2.raw_online_marketplace_tiktok external table reads.

Mirrors the existing per-source "raw" convention already in this bucket (see
BigQuery_Anchanto.py's B2C_INPUT_PREFIX for Anchanto's own raw staging area).

Lives outside OneDrive on purpose -- same reasoning as gcs_upload_tiktok.py.
"""
import os
import sys
import time

os.environ.setdefault(
    "GOOGLE_APPLICATION_CREDENTIALS",
    os.path.expanduser("~/.config/gcloud/keys/odoo-service-account.json"),
)

from google.cloud import storage

LOCAL_SOURCE_DIR = (
    "/Users/salesops/Library/CloudStorage/OneDrive-PT.OptoLumbungSejahtera/"
    "SOM/Anchanto Report/E-Commerce Data/TikTok Seller Data"
)
GCS_BUCKET_NAME = "bucket_som"
GCS_RAW_PREFIX = "sales_parquet/raw/online/tiktok/orders"


def main():
    if not os.path.isdir(LOCAL_SOURCE_DIR):
        print(f"ERROR: source folder not found: {LOCAL_SOURCE_DIR}")
        sys.exit(1)

    csv_files = sorted(f for f in os.listdir(LOCAL_SOURCE_DIR) if f.lower().endswith(".csv"))
    if not csv_files:
        print("No CSV files found in source folder.")
        return

    client = storage.Client()
    bucket = client.bucket(GCS_BUCKET_NAME)

    uploaded = 0
    for filename in csv_files:
        local_path = os.path.join(LOCAL_SOURCE_DIR, filename)
        local_size = os.path.getsize(local_path)
        if local_size == 0:
            print(f"Skipping {filename}: 0 bytes locally")
            continue

        blob_name = f"{GCS_RAW_PREFIX}/{filename}"
        blob = bucket.blob(blob_name)

        # The client library's default upload timeout (120s) isn't enough for
        # this account's largest daily export (~100MB+, e.g. 2026-08-08.csv,
        # confirmed 2026-09-24) over a home/office connection -- retrying with
        # the same short timeout just reproduces the same failure, so give
        # every upload a generous explicit timeout instead.
        last_err = None
        for attempt in range(3):
            try:
                blob.upload_from_filename(local_path, timeout=600)
                blob.reload()
                if blob.size != local_size:
                    last_err = f"uploaded size {blob.size} != local size {local_size}"
                    continue
                uploaded += 1
                last_err = None
                break
            except Exception as e:
                last_err = str(e)
                time.sleep(5)

        if last_err:
            print(f"Error uploading {filename}: {last_err}")

    print(f"✅ Done! Uploaded {uploaded}/{len(csv_files)} raw CSV(s) to "
          f"gs://{GCS_BUCKET_NAME}/{GCS_RAW_PREFIX}/")


if __name__ == "__main__":
    main()
