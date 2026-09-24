"""Download every shop's raw Shopee CSV files from GCS into work/inputs/<Shopee Star|Shopee Mall>/.

The Mac pipeline (shopee_scheduled_export.py) mirrors its local Shopee Star /
Shopee Mall CSV folders into gs://bucket_som/sales_parquet/raw/ecommerce/shopee/<shop>/
every night after login+download. This script is this repo's equivalent of
reading those OneDrive folders directly, so the BQ parquet rebuild never
needs OneDrive or the Mac to be reachable - only whatever is already in GCS.
"""

import sys
from pathlib import Path

from google.cloud import storage

import gcs_paths

HERE = Path(__file__).resolve().parent


def main() -> int:
    bucket = storage.Client().bucket(gcs_paths.BUCKET)
    total = 0
    for shop, folder_name in gcs_paths.SHOP_FOLDERS.items():
        prefix = f"{gcs_paths.RAW_PREFIX}/{shop}/"
        dest = HERE / "work" / "inputs" / folder_name
        dest.mkdir(parents=True, exist_ok=True)
        n = 0
        for blob in bucket.list_blobs(prefix=prefix):
            if not blob.name.lower().endswith(".csv"):
                continue
            name = blob.name.rsplit("/", 1)[-1]
            blob.download_to_filename(str(dest / name), timeout=900)
            n += 1
        print(f"{shop}: downloaded {n} CSV file(s) from gs://{gcs_paths.BUCKET}/{prefix} -> {dest}")
        total += n
    if total == 0:
        raise SystemExit("No CSV files found in GCS raw prefix for either shop - aborting.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
