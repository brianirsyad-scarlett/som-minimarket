"""Uploads TikTok.parquet to GCS, following the same layout as the other
per-source parquet files already in gs://bucket_som/sales_parquet/ (e.g.
Anchanto.parquet, BA_Sales.parquet, Minimarket_Sales.parquet).

Lives outside OneDrive on purpose -- see tiktok_pipeline_check.sh's note on
OneDrive evicting rarely-touched scripts back to cloud-only placeholders;
no reason to expose a brand new script to that same failure mode.

Auth: uses the existing metabase-restricted-access service account key
(the same one BigQuery_Anchanto.py relies on via GOOGLE_APPLICATION_CREDENTIALS/
gcloud's application-default resolution) -- confirmed to have both read and
write access to gs://bucket_som/sales_parquet/ on 2026-09-23.
"""
import os
import sys
import time

os.environ.setdefault(
    "GOOGLE_APPLICATION_CREDENTIALS",
    os.path.expanduser("~/.config/gcloud/keys/odoo-service-account.json"),
)

from google.cloud import storage

LOCAL_PARQUET = (
    "/Users/salesops/Library/CloudStorage/OneDrive-PT.OptoLumbungSejahtera/"
    "SOM/Anchanto Report/E-Commerce Data/TikTok.parquet"
)
GCS_BUCKET_NAME = "bucket_som"
GCS_BLOB_NAME = "sales_parquet/raw/online/tiktok/TikTok.parquet"


def main():
    if not os.path.exists(LOCAL_PARQUET):
        print(f"ERROR: local file not found: {LOCAL_PARQUET}")
        sys.exit(1)

    local_size = os.path.getsize(LOCAL_PARQUET)
    if local_size == 0:
        print("ERROR: local TikTok.parquet is 0 bytes -- refusing to upload a bad file")
        sys.exit(1)

    print(f"Uploading {LOCAL_PARQUET} ({local_size / (1024**3):.2f} GB) "
          f"to gs://{GCS_BUCKET_NAME}/{GCS_BLOB_NAME} ...")

    client = storage.Client()
    bucket = client.bucket(GCS_BUCKET_NAME)
    blob = bucket.blob(GCS_BLOB_NAME)

    # The client library's default upload timeout (120s) isn't reliably
    # enough for a 1GB+ file over every network condition (a transient
    # timeout was observed 2026-09-28 despite this exact upload succeeding
    # nightly at this size for weeks) -- use a generous explicit timeout
    # plus a few retries, same treatment as gcs_upload_tiktok_raw.py.
    last_err = None
    for attempt in range(3):
        try:
            blob.upload_from_filename(LOCAL_PARQUET, timeout=600)
            blob.reload()
            if blob.size != local_size:
                last_err = f"uploaded size {blob.size} does not match local size {local_size}"
                continue
            last_err = None
            break
        except Exception as e:
            last_err = str(e)
            time.sleep(5)

    if last_err:
        print(f"ERROR: {last_err}")
        sys.exit(1)

    print(f"✅ Done! Uploaded {blob.size / (1024**3):.2f} GB to "
          f"gs://{GCS_BUCKET_NAME}/{GCS_BLOB_NAME}")


if __name__ == "__main__":
    main()
