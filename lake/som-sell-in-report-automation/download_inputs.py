"""Download every Sell In input from GCS into work/inputs."""

import sys
from pathlib import Path

from google.cloud import storage

import gcs_paths

HERE = Path(__file__).resolve().parent


def main() -> int:
    dest = HERE / "work" / "inputs"
    dest.mkdir(parents=True, exist_ok=True)
    bucket = storage.Client().bucket(gcs_paths.BUCKET)
    missing = []
    for key, name in gcs_paths.INPUTS.items():
        blob = bucket.blob(key)
        if not blob.exists():
            missing.append(key)
            continue
        blob.reload()
        blob.download_to_filename(str(dest / name), timeout=900)
        print(f"downloaded gs://{gcs_paths.BUCKET}/{key}  "
              f"({blob.size / 1e6:.1f} MB, updated {blob.updated:%Y-%m-%d %H:%M} UTC)")
    for key in missing:
        print(f"MISSING gs://{gcs_paths.BUCKET}/{key}")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
