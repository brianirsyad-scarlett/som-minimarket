"""One-off backfill: uploads the full historical TikTok CSV set (the renamed
All_order_YYYY_MM_DD.csv files in the Anchanto "TikTok" folder -- the input to
parquet_converter_tiktok.py, not the 56-day rolling raw export in "TikTok
Seller Data") to the same GCS raw/ecommerce/tiktok/ prefix as the nightly
raw-CSV audit upload.

This is NOT wired into the nightly pipeline -- it's a one-time historical
backfill (~633 files, ~10GB as of 2026-09-25). The nightly
gcs_upload_tiktok_raw.py already covers going-forward daily uploads from the
rolling-window folder; running this every night would just re-upload ~10GB of
unchanged history for nothing.

Lives outside OneDrive on purpose -- same reasoning as the other upload scripts.
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
    "SOM/Anchanto Report/E-Commerce Data/TikTok"
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
    skipped_unchanged = 0
    for i, filename in enumerate(csv_files, 1):
        local_path = os.path.join(LOCAL_SOURCE_DIR, filename)
        local_size = os.path.getsize(local_path)
        if local_size == 0:
            print(f"[{i}/{len(csv_files)}] Skipping {filename}: 0 bytes locally")
            continue

        blob_name = f"{GCS_RAW_PREFIX}/{filename}"
        blob = bucket.blob(blob_name)

        # Skip re-uploading a file already present with the same size -- this
        # script may need to be re-run if interrupted partway through a 10GB
        # backfill, and there's no point re-sending what's already there.
        if blob.exists():
            blob.reload()
            if blob.size == local_size:
                skipped_unchanged += 1
                continue

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

    print(f"\n✅ Done! Uploaded {uploaded} new, skipped {skipped_unchanged} already-present, "
          f"out of {len(csv_files)} total CSV(s) to gs://{GCS_BUCKET_NAME}/{GCS_RAW_PREFIX}/")


if __name__ == "__main__":
    main()
