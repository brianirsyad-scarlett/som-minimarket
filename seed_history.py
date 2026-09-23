"""
One-time (or occasional) local seeding: turn the existing local Sales csv\\<year>\\*.csv
into one parquet per month and upload them to GCS, so the GitHub run only has to
rebuild the months it touches.

Run on the SOM laptop:
    python seed_history.py                 # every month found
    python seed_history.py --only "2025 06 Jun"

It reads exactly the CSVs the local primary_sales_csv_and_parquet.py combines
(every *.csv under Sales csv, minus names containing "report" or
"combined_sales"), grouped by the workbook they came from ("<year> <MM> <Mon>_<sheet>.csv").
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

from google.cloud import storage

import gcs_paths
from build_primary_parquet import read_csv_texts, typed, write_month

LOCAL_CSV_DIR = Path(r"D:\SCARLETT_512\SCARLETT-329\SOM\Data\Report\Sales\Sales csv")
EXCLUDE_PATTERNS = ["report", "combined_sales"]
HERE = Path(__file__).resolve().parent


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv-dir", type=Path, default=LOCAL_CSV_DIR)
    ap.add_argument("--only", action="append", default=[], help="workbook stem, e.g. '2025 06 Jun'")
    ap.add_argument("--no-upload", action="store_true")
    args = ap.parse_args(argv)

    groups: dict[str, list[Path]] = defaultdict(list)
    for p in sorted(args.csv_dir.rglob("*.csv")):
        if any(x in p.name.lower() for x in EXCLUDE_PATTERNS):
            continue
        groups[p.name.split("_", 1)[0]].append(p)

    out_dir = HERE / "work" / "monthly_parquet"
    bucket = None if args.no_upload else storage.Client().bucket(gcs_paths.BUCKET)
    for stem, paths in sorted(groups.items()):
        if args.only and stem not in args.only:
            continue
        print(f"{stem}: {[p.name for p in paths]}")
        out = out_dir / f"{stem}.parquet"
        write_month(typed(read_csv_texts(paths)), out)
        if bucket is not None:
            key = f"{gcs_paths.OUT_MONTHLY_PARQUET}/{out.name}"
            bucket.blob(key).upload_from_filename(str(out), timeout=900)
            print(f"  uploaded gs://{gcs_paths.BUCKET}/{key}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
