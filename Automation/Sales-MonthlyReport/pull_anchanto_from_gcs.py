"""Pull the previous + current month Anchanto order reports from GCS into the laptop.

The laptop's own Anchanto download stopped delivering B2C CSVs after 2026-09-22,
while the GitHub pipeline (som-anchanto-report-automation) keeps building the
same "Anchanto YY MM Mon (n) - Order Report.xlsx" files daily under
gs://bucket_som/sales_parquet/raw/primary/anchanto/source/. The 15:00 routine
runs this first, then run_anchanto_daily.ps1 (xlsx -> csv -> Anchanto Report
<year> Q<n>.xlsx), so the monthly workbooks and combined_sales.parquet pick up
the latest Anchanto data.

A local file is replaced only when the GCS copy differs (md5) and is not
drastically smaller; the old copy is backed up first.

    python pull_anchanto_from_gcs.py            # prev + current month
    python pull_anchanto_from_gcs.py --dry-run  # show what would change
"""
import argparse
import base64
import datetime as dt
import hashlib
import os
import re
import shutil
import sys
import zipfile
from pathlib import Path

os.environ.setdefault(
    "GOOGLE_APPLICATION_CREDENTIALS",
    r"D:\SCARLETT_512\SCARLETT-329\SOM\Data\Sent Email\sales-som datawarehouse 490008.json",
)
from google.cloud import storage  # noqa: E402

BUCKET = "bucket_som"
PREFIX = "sales_parquet/raw/primary/anchanto/source/"
LOCAL_DIR = Path(r"D:\SCARLETT_512\SCARLETT-329\SOM\Data\Report\Sales\Anchanto Report")
BACKUP_DIR = Path(__file__).resolve().parent / "backups" / "anchanto"
KEEP = 3
# A GCS copy this much smaller than the local one is treated as a broken export.
MIN_SIZE_RATIO = 0.8
MONTH_ABBR = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def months(today: dt.date) -> list[tuple[int, int]]:
    first = today.replace(day=1)
    prev = first - dt.timedelta(days=1)
    return [(prev.year, prev.month), (today.year, today.month)]


def local_md5(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return base64.b64encode(h.digest()).decode()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    bucket = storage.Client().bucket(BUCKET)
    errors = 0
    changed = 0
    for year, month in months(dt.date.today()):
        tag = f"Anchanto {year % 100:02d} {month:02d} {MONTH_ABBR[month]}"
        pattern = re.compile(rf"^{re.escape(tag)}( \(\d\))? - Order Report\.xlsx$")
        blobs = [b for b in bucket.list_blobs(prefix=PREFIX + tag) if pattern.match(b.name[len(PREFIX):])]
        if not blobs:
            print(f"WARNING no GCS files for {tag}")
            continue
        for blob in sorted(blobs, key=lambda b: b.name):
            name = blob.name[len(PREFIX):]
            dest = LOCAL_DIR / str(year) / name
            if dest.exists() and local_md5(dest) == blob.md5_hash:
                print(f"same     {name}")
                continue
            if dest.exists() and blob.size < dest.stat().st_size * MIN_SIZE_RATIO:
                print(f"ERROR    {name}: GCS copy is {blob.size:,} B vs local {dest.stat().st_size:,} B - not replaced")
                errors += 1
                continue
            old = f"{dest.stat().st_size:,} B" if dest.exists() else "missing"
            print(f"{'would update' if args.dry_run else 'update  '} {name}: local {old} -> GCS {blob.size:,} B "
                  f"(GCS updated {blob.updated.astimezone():%Y-%m-%d %H:%M})")
            if args.dry_run:
                continue

            part = dest.with_name(dest.name + ".part")
            dest.parent.mkdir(parents=True, exist_ok=True)
            blob.download_to_filename(str(part))
            if not zipfile.is_zipfile(part):
                part.unlink()
                print(f"ERROR    {name}: downloaded file is not a valid xlsx - not replaced")
                errors += 1
                continue
            if dest.exists():
                BACKUP_DIR.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dest, BACKUP_DIR / f"{name}.{dt.datetime.now():%Y%m%d_%H%M%S}.bak")
                for stale in sorted(BACKUP_DIR.glob(f"{glob_escape(name)}.*.bak"), reverse=True)[KEEP:]:
                    stale.unlink()
            os.replace(part, dest)
            changed += 1

    print(f"\n{changed} file(s) updated, {errors} error(s)")
    print(f"Exit code: {1 if errors else 0}")
    return 1 if errors else 0


def glob_escape(s: str) -> str:
    return re.sub(r"([\[\]*?])", r"[\1]", s)


if __name__ == "__main__":
    sys.exit(main())
