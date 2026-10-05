"""Download every seeded/previous monthly parquet from GCS into work/monthly_parquet,
so the combine step can stack them with the months this run rebuilt."""

import sys
from pathlib import Path

from google.cloud import storage

import gcs_paths

HERE = Path(__file__).resolve().parent


def main() -> int:
    dest = HERE / "work" / "monthly_parquet"
    dest.mkdir(parents=True, exist_ok=True)
    bucket = storage.Client().bucket(gcs_paths.BUCKET)
    n = 0
    for blob in bucket.list_blobs(prefix=f"{gcs_paths.OUT_MONTHLY_PARQUET}/"):
        if not blob.name.endswith(".parquet"):
            continue
        blob.download_to_filename(str(dest / blob.name.rsplit("/", 1)[-1]), timeout=900)
        n += 1
    print(f"downloaded {n} monthly parquet file(s)")
    if n == 0:
        print("No history in GCS - run seed_history.py on the SOM laptop first.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
