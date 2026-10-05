"""
Alfamidi Sell Out - Step 3: upload to GCS and mirror into iMac Sales Ops.

Reads by-store-by-category detail csvs from the sibling "Daily Sell Out"
folder, and the by-branch monthly summary csv(s) from this "Sell Out" folder
(built by 2_csv_converter.py / 1_summary_sell_out.py, which live here).

What it does, mirroring the existing Alfamart pipeline
(D:\\OneDrive - PT. Opto Lumbung Sejahtera\\SOM_iMac\\iMac_Sales Ops\\Alfamart):

  1. Uploads each "by store by category / daily" detail csv (from
     "..\\Alfamidi\\Daily Sell Out") to GCS:
       *_Selling_Out_Qty_*_All_Store.csv   -> .../alfamidi/daily_sell_out_qty/
       *_Selling_Out_Value_*_All_Store.csv -> .../alfamidi/daily_sell_out_value/
  2. Uploads the combined by-branch summary csv(s)
     ("YYYYMM_Sell Out Alfamidi*.csv", built by 1_summary_sell_out.py from the
     detail_performance_by_branch_Selling_Out_{Value,Qty} pair) to:
       .../alfamidi/sell_out/
  3. Copies every file this run touched into one consolidated local folder:
       D:\\OneDrive - PT. Opto Lumbung Sejahtera\\SOM_iMac\\iMac_Sales Ops\\Alfamidi\\
     (OneDrive syncs that folder to the iMac; the folder name is the "Alfamidi" tag,
     same convention as the existing "Alfamart" folder there.)
  4. Writes/updates a _run_status.txt in that folder, same convention as Alfamart's.

Usage:
    python 3_upload_and_distribute.py                # do it (uploads everything to GCS)
    python 3_upload_and_distribute.py --dry-run       # show what would happen
    python 3_upload_and_distribute.py --latest-start 2026-09-11 --latest-end 2026-09-12
        # only by-store files whose embedded date range matches exactly go to
        # GCS (+ OneDrive); every other by-store file goes to OneDrive only.
        # By-branch summaries are always uploaded to GCS regardless.
"""

import argparse
import re
import shutil
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

from period_utils import period_index, period_bounds  # noqa: E402

SOURCE_DIR = Path(
    "/Users/salesops/Library/CloudStorage/OneDrive-PT.OptoLumbungSejahtera/SOM/Minimarket/Alfamidi/Sell Out"
)
DAILY_DIR = Path(
    "/Users/salesops/Library/CloudStorage/OneDrive-PT.OptoLumbungSejahtera/SOM/Minimarket/Alfamidi/Daily Sell Out"
)
IMAC_DIR = Path(
    "/Users/salesops/Library/CloudStorage/OneDrive-PT.OptoLumbungSejahtera/SOM_iMac/iMac_Sales Ops/Alfamidi"
)

PROJECT_ID = "datawarehouse-490008"
BUCKET_NAME = "bucket_som"
PREFIX_QTY = "sales_sell out_minimarket/alfamidi/daily_sell_out_qty/"
PREFIX_VALUE = "sales_sell out_minimarket/alfamidi/daily_sell_out_value/"
PREFIX_SELLOUT = "sales_sell out_minimarket/alfamidi/sell_out/"

BY_STORE_RE = re.compile(r"detail_performance_Selling_Out_(Value|Qty)_.*_All_Store")
SUMMARY_RE = re.compile(r"^\d{6}_Sell Out Alfamidi.*\.csv$")

# A separate, foreign full-catalog "All Category" export (not fired by our
# per-category pipeline) can also land in Daily Sell Out via the same
# mailbox/marker. It's 300MB-1GB+ per file and never matches a
# --latest-period date range, so it never reaches GCS - but exclude it here
# too so it isn't swept into the OneDrive mirror every run either.
BY_STORE_EXCLUDE_RE = re.compile(r"All_Category", re.IGNORECASE)

DATE_RANGE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})_sd_(\d{4}-\d{2}-\d{2})")

# Pre-existing archive files (e.g. the old half-month "..._sd_2026-08-15.csv"
# convention) predate the 10-day-period pipeline entirely and must never be
# touched by the dedup below, even if their start date happens to coincide
# with a period start. Only files this pipeline itself could have written
# (created/modified on or after the day the period rule shipped) are
# eligible for cleanup.
PERIOD_RULE_CUTOFF = datetime(2026, 9, 12)


def dedupe_daily_dir(dry_run):
    """
    Within Daily Sell Out, the "latest period" file for a given category grows
    a new snapshot every day the period is in progress (same start date,
    end date creeping toward the period boundary) - e.g. ..._sd_2026-09-12.csv
    then ..._sd_2026-09-13.csv for the same Sep-11-start period. The older
    snapshot is a strict subset of the newer one and is pure clutter that
    would otherwise get mirrored to OneDrive every run.

    A file whose end date falls PAST its period's real boundary (e.g. a
    Sep-1-start file ending Sep-12, when period 1 always ends Sep-10) is not
    a snapshot at all - it's leftover from some other/older request scope -
    and gets removed outright rather than kept as "latest".

    Groups files by (name with the date range blanked out, start date), then
    for each group keeps only the file with the largest end date that does
    not exceed the period's real end; every other file in the group is
    removed as superseded/invalid. Files older than PERIOD_RULE_CUTOFF are
    excluded from grouping entirely (see constant above) so pre-existing
    archive data is never a candidate for deletion or for "keep".
    """
    groups = defaultdict(list)
    for p in DAILY_DIR.glob("*.csv"):
        if BY_STORE_EXCLUDE_RE.search(p.name):
            continue
        if not BY_STORE_RE.search(p.name):
            continue
        if datetime.fromtimestamp(p.stat().st_mtime) < PERIOD_RULE_CUTOFF:
            continue
        m = DATE_RANGE_RE.search(p.name)
        if not m:
            continue
        start = date.fromisoformat(m.group(1))
        end = date.fromisoformat(m.group(2))
        key = (DATE_RANGE_RE.sub("<DATES>", p.name), start)
        groups[key].append((end, p))

    removed = 0
    for (_, start), entries in groups.items():
        if len(entries) < 2 or start.day not in (1, 11, 21):
            continue
        _, official_end = period_bounds(start.year, start.month, period_index(start.day))
        valid = [(e, p) for e, p in entries if e <= official_end]
        keep_end, keep_path = max(valid or entries, key=lambda t: t[0])
        for e, p in entries:
            if p == keep_path:
                continue
            removed += 1
            tag = "invalid (past period end)" if e > official_end else "superseded snapshot"
            if dry_run:
                print(f"  [dry-run] would remove {tag}: {p.name}")
            else:
                print(f"  removed {tag}: {p.name}")
                p.unlink()
    if removed:
        print(f"Daily Sell Out cleanup: removed {removed} superseded/invalid file(s).")
    return removed


def find_files():
    by_store_qty, by_store_value, summaries = [], [], []
    for p in DAILY_DIR.glob("*.csv"):
        if BY_STORE_EXCLUDE_RE.search(p.name):
            continue
        m = BY_STORE_RE.search(p.name)
        if m:
            (by_store_qty if m.group(1) == "Qty" else by_store_value).append(p)
    for p in SOURCE_DIR.glob("*.csv"):
        if SUMMARY_RE.match(p.name):
            summaries.append(p)
    return by_store_qty, by_store_value, summaries


def file_date_range(p: Path):
    m = DATE_RANGE_RE.search(p.name)
    if not m:
        return None, None
    return date.fromisoformat(m.group(1)), date.fromisoformat(m.group(2))


def split_by_period(files, latest_start, latest_end):
    """Return (in_latest_period, rest) - by embedded filename date range."""
    if latest_start is None:
        return files, []
    in_latest, rest = [], []
    for f in files:
        fs, fe = file_date_range(f)
        (in_latest if (fs, fe) == (latest_start, latest_end) else rest).append(f)
    return in_latest, rest


def upload_all(client, files, prefix, dry_run):
    bucket = None if dry_run else client.bucket(BUCKET_NAME)
    for f in files:
        dest = f"{prefix}{f.name}"
        if dry_run:
            print(f"  [dry-run] gs://{BUCKET_NAME}/{dest}")
        else:
            bucket.blob(dest).upload_from_filename(str(f))
            print(f"  uploaded  gs://{BUCKET_NAME}/{dest}")


def mirror_to_imac(files, dry_run):
    if not files:
        return
    if dry_run:
        print(f"  [dry-run] would ensure folder: {IMAC_DIR}")
    else:
        IMAC_DIR.mkdir(parents=True, exist_ok=True)
    for f in files:
        dest = IMAC_DIR / f.name
        if dry_run:
            print(f"  [dry-run] copy -> {dest}")
        else:
            shutil.copy2(f, dest)
            print(f"  copied    {dest}")


def write_status(ok, message, dry_run):
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S}  {'OK' if ok else 'PARTIAL'} {message}\n"
    if dry_run:
        print(f"  [dry-run] would append to _run_status.txt: {line.strip()}")
        return
    IMAC_DIR.mkdir(parents=True, exist_ok=True)
    with open(IMAC_DIR / "_run_status.txt", "a", encoding="utf-8") as fh:
        fh.write(line)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--latest-start", help="YYYY-MM-DD - start of the current/latest period")
    ap.add_argument("--latest-end", help="YYYY-MM-DD - end of the current/latest period")
    args = ap.parse_args()

    latest_start = date.fromisoformat(args.latest_start) if args.latest_start else None
    latest_end = date.fromisoformat(args.latest_end) if args.latest_end else None
    if bool(latest_start) != bool(latest_end):
        raise SystemExit("--latest-start and --latest-end must be given together.")

    print(f"--- Cleaning up superseded/invalid snapshots in {DAILY_DIR} ---")
    dedupe_daily_dir(args.dry_run)

    by_store_qty, by_store_value, summaries = find_files()
    print(f"Found in {DAILY_DIR} (by-store) and {SOURCE_DIR} (by-branch summary):")
    print(f"  by-store QTY files   : {len(by_store_qty)} -> {[p.name for p in by_store_qty]}")
    print(f"  by-store VALUE files : {len(by_store_value)} -> {[p.name for p in by_store_value]}")
    print(f"  by-branch summaries  : {len(summaries)} -> {[p.name for p in summaries]}")

    if not (by_store_qty or by_store_value or summaries):
        raise SystemExit(
            "Nothing to upload. Run 2_csv_converter.py and 1_summary_sell_out.py "
            "on the downloaded report files first."
        )

    qty_gcs, qty_archive_only = split_by_period(by_store_qty, latest_start, latest_end)
    val_gcs, val_archive_only = split_by_period(by_store_value, latest_start, latest_end)
    if latest_start:
        print(f"\nLatest period : {latest_start} .. {latest_end}"
              f" -> {len(qty_gcs) + len(val_gcs)} file(s) to GCS,"
              f" {len(qty_archive_only) + len(val_archive_only)} file(s) to OneDrive only")

    client = None
    if not args.dry_run:
        from google.cloud import storage
        client = storage.Client(project=PROJECT_ID)

    print("\n--- GCS upload ---")
    upload_all(client, qty_gcs, PREFIX_QTY, args.dry_run)
    upload_all(client, val_gcs, PREFIX_VALUE, args.dry_run)
    upload_all(client, summaries, PREFIX_SELLOUT, args.dry_run)

    print(f"\n--- Mirror to {IMAC_DIR} (all periods, GCS or not) ---")
    mirror_to_imac(by_store_qty + by_store_value + summaries, args.dry_run)

    write_status(
        True,
        f"Uploaded {len(qty_gcs)} qty + {len(val_gcs)} value by-store files (latest period only) "
        f"and {len(summaries)} sell_out summary file(s) to GCS; "
        f"mirrored {len(by_store_qty) + len(by_store_value)} by-store + {len(summaries)} summary "
        f"file(s) to iMac Sales Ops.",
        args.dry_run,
    )
    print("\nDone." + ("  [DRY RUN - nothing was written]" if args.dry_run else ""))


if __name__ == "__main__":
    main()
