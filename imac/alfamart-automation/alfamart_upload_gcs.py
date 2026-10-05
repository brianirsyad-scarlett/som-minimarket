#!/usr/bin/env python3
"""
alfamart_upload_gcs.py -- upload downloaded Alfamart CSVs to Cloud Storage,
mirroring the Anchanto pipeline's gs://bucket_som/raw_som/<source>/ layout.

Must run under the venv python that has google-cloud-storage:
    ~/scrwms-automation/venv/bin/python3

Usage:
    alfamart_upload_gcs.py --src-dir ~/alfamart-automation/exports \
        --bucket bucket_som --prefix raw_som/alfamart \
        --key ~/scrwms-automation/gcs-service-account.json
"""

import argparse
import os
import sys

from google.cloud import storage

# These files are large (the by-store exports run to several hundred MB), so
# raise the chunked-upload timeout well above the library default.
UPLOAD_TIMEOUT = 1800


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src-dir", required=True)
    ap.add_argument("--bucket", required=True)
    ap.add_argument("--prefix", default="")
    ap.add_argument("--key", required=True)
    ap.add_argument("--match", default="",
                    help="only upload .csv files whose name contains this substring")
    args = ap.parse_args()

    if not os.path.isdir(args.src_dir):
        sys.exit(f"ERROR: source dir not found: {args.src_dir}")

    client = storage.Client.from_service_account_json(args.key)
    bucket = client.bucket(args.bucket)

    names = sorted(n for n in os.listdir(args.src_dir)
                   if n.endswith(".csv") and args.match in n)
    if not names:
        print(f"No .csv files matching {args.match!r} in {args.src_dir} -- nothing to upload.")
        return

    uploaded = 0
    for name in names:
        local_path = os.path.join(args.src_dir, name)
        blob_name = f"{args.prefix.rstrip('/')}/{name}" if args.prefix else name
        blob = bucket.blob(blob_name)
        size_mb = os.path.getsize(local_path) / 1024 / 1024
        print(f"  uploading {name} ({size_mb:.1f} MB) -> gs://{args.bucket}/{blob_name}")
        try:
            blob.upload_from_filename(local_path, timeout=UPLOAD_TIMEOUT)
            uploaded += 1
        except Exception as e:
            print(f"  FAILED {name}: {e}", file=sys.stderr)

    print(f"Uploaded {uploaded}/{len(names)} file(s) to gs://{args.bucket}/{args.prefix}")
    if uploaded < len(names):
        sys.exit(1)


if __name__ == "__main__":
    main()
