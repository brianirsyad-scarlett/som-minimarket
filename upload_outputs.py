"""Upload this run's monthly workbooks, monthly parquets and the combined parquet."""

import sys
from pathlib import Path

from google.cloud import storage

import gcs_paths

HERE = Path(__file__).resolve().parent
WORK = HERE / "work"


def main() -> int:
    bucket = storage.Client().bucket(gcs_paths.BUCKET)
    uploads = []
    rebuilt = sorted((WORK / "output").glob("*/*.xlsx"))
    for xlsx in rebuilt:
        uploads.append((xlsx, f"{gcs_paths.OUT_WORKBOOKS}/{xlsx.parent.name}/{xlsx.name}"))
    # Only the months this run rebuilt - the rest of work/monthly_parquet is the
    # history download_history.py just fetched, unchanged.
    for xlsx in rebuilt:
        pq = WORK / "monthly_parquet" / f"{xlsx.stem}.parquet"
        if pq.exists():
            uploads.append((pq, f"{gcs_paths.OUT_MONTHLY_PARQUET}/{pq.name}"))
    combined = WORK / "Primary_Sales.parquet"
    if combined.exists():
        uploads.append((combined, gcs_paths.OUT_COMBINED))

    if not uploads:
        print("nothing to upload")
        return 1
    for path, key in uploads:
        bucket.blob(key).upload_from_filename(str(path), timeout=900)
        print(f"uploaded {path.name} -> gs://{gcs_paths.BUCKET}/{key} ({path.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
