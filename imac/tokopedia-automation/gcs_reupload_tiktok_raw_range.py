"""One-off: re-uploads a specific date range of raw per-day TikTok CSVs from
"TikTok Seller Data" to gs://bucket_som/sales_parquet/raw/ecommerce/tiktok/,
using the same plain YYYY-MM-DD.csv naming as the nightly gcs_upload_tiktok_raw.py.

Used for backfilling/re-uploading a manually re-downloaded historical range
(e.g. after re-exporting 2026-01-01..2026-04-25 because the original raw data
needed a re-pull) without re-touching the rest of the rolling-window folder.

Lives outside OneDrive on purpose -- same reasoning as the other upload scripts.
"""
import os
import sys
import time
from datetime import date, timedelta

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

START_DATE = date(2026, 1, 1)
END_DATE = date(2026, 7, 31)


def main():
    if not os.path.isdir(LOCAL_SOURCE_DIR):
        print(f"ERROR: source folder not found: {LOCAL_SOURCE_DIR}")
        sys.exit(1)

    target_names = set()
    d = START_DATE
    while d <= END_DATE:
        target_names.add(f"{d.isoformat()}.csv")
        d += timedelta(days=1)

    csv_files = sorted(
        f for f in os.listdir(LOCAL_SOURCE_DIR)
        if f in target_names
    )
    if not csv_files:
        print(f"No CSV files found in {START_DATE}..{END_DATE} range.")
        return
    print(f"Found {len(csv_files)} file(s) in range {START_DATE}..{END_DATE} "
          f"(range covers {len(target_names)} possible dates -- some may not "
          "be downloaded yet).")

    client = storage.Client()
    bucket = client.bucket(GCS_BUCKET_NAME)

    uploaded = 0
    for i, filename in enumerate(csv_files, 1):
        local_path = os.path.join(LOCAL_SOURCE_DIR, filename)
        local_size = os.path.getsize(local_path)
        if local_size == 0:
            print(f"[{i}/{len(csv_files)}] Skipping {filename}: 0 bytes locally")
            continue

        blob_name = f"{GCS_RAW_PREFIX}/{filename}"
        blob = bucket.blob(blob_name)

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
            print(f"[{i}/{len(csv_files)}] Error uploading {filename}: {last_err}")
        else:
            print(f"[{i}/{len(csv_files)}] Uploaded {filename} ({local_size / 1e6:.1f} MB)")

    missing = sorted(target_names - set(csv_files))
    print(f"\n✅ Done! Uploaded {uploaded}/{len(csv_files)} found CSV(s) to "
          f"gs://{GCS_BUCKET_NAME}/{GCS_RAW_PREFIX}/")
    if missing:
        print(f"Note: {len(missing)} date(s) in {START_DATE}..{END_DATE} were not "
              f"found locally (not downloaded yet), e.g. {missing[:5]}{'...' if len(missing) > 5 else ''}")


if __name__ == "__main__":
    main()
