"""Tiny GCS copy helper for the workflow.

    python gcs_copy.py get "sales_parquet/x.csv" work/x.csv
    python gcs_copy.py get "sales_parquet/raw/ecommerce/shopee/scarlett_whitening/" work/inputs/Shopee\\ Star/
    python gcs_copy.py put work/Shopee_bq.parquet "sales_parquet/Shopee_bq.parquet"
"""

import sys
from pathlib import Path

from google.cloud import storage

import gcs_paths


def main(argv) -> int:
    cmd, a, b = argv
    bucket = storage.Client().bucket(gcs_paths.BUCKET)
    if cmd == "get":
        if a.endswith("/"):
            dest = Path(b)
            dest.mkdir(parents=True, exist_ok=True)
            n = 0
            for blob in bucket.list_blobs(prefix=a):
                if blob.name.endswith("/"):
                    continue
                blob.download_to_filename(str(dest / blob.name.rsplit("/", 1)[-1]), timeout=900)
                n += 1
            print(f"downloaded {n} object(s) from gs://{gcs_paths.BUCKET}/{a}")
        else:
            Path(b).parent.mkdir(parents=True, exist_ok=True)
            bucket.blob(a).download_to_filename(b, timeout=900)
            print(f"downloaded gs://{gcs_paths.BUCKET}/{a}")
    elif cmd == "put":
        bucket.blob(b).upload_from_filename(a, timeout=900)
        print(f"uploaded {a} -> gs://{gcs_paths.BUCKET}/{b}")
    else:
        raise SystemExit(f"unknown command {cmd!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
