"""
Copy the GitHub-built parquets over the production names in sales_parquet/.

    python promote_to_production.py            # dry run: show what would change
    python promote_to_production.py --apply    # back up production, then copy

Every copy is server-side (no download). Before overwriting, each existing
production file is copied to sales_parquet/backup/<YYYYMMDD-HHMM WIB>/, so a
promotion can be undone by copying those back.

Note: the laptop's mirror daemon still uploads these production names from the
local chain. Until it stops, its next upload replaces what this wrote.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from zoneinfo import ZoneInfo

import pyarrow.parquet as pq
from google.cloud import storage

import gcs_paths

WIB = ZoneInfo("Asia/Jakarta")

# GitHub-built source -> production name
PROMOTIONS = {
    gcs_paths.OUT_COMBINED: "sales_parquet/Primary_Sales.parquet",
    "sales_parquet/raw/primary/anchanto/Anchanto.parquet": "sales_parquet/Anchanto.parquet",
    "sales_parquet/raw/primary/pcc/PCC_Sales.parquet": "sales_parquet/PCC_Sales.parquet",
    "sales_parquet/raw/primary/pcc/PCC_Order_Number.parquet": "sales_parquet/PCC_Order_Number.parquet",
}
BACKUP_ROOT = "sales_parquet/backup"
# The pipeline promotes several times a day and each backup is ~2 GB, so: back the
# production files up at most once per BACKUP_EVERY, and delete backups older than KEEP.
BACKUP_EVERY = dt.timedelta(hours=20)
KEEP = dt.timedelta(days=3)

# Columns a promotion is allowed to add. The GitHub Anchanto.parquet carries the
# raw Dispatch Date (Sell In dates a sale by it, else CreatedOn), which the
# local one lacks, and the raw Delivery Date (delivered orders only, used by
# anchanto_report_v2 in BigQuery). Any other added or dropped column blocks --apply.
EXPECTED_NEW_COLUMNS = {
    "sales_parquet/Anchanto.parquet": {"Dispatch Date", "Delivery Date"},
}


def describe(blob) -> str:
    return f"{blob.size / 1e6:,.1f} MB, updated {blob.updated.astimezone(WIB):%Y-%m-%d %H:%M} WIB"


def schema(blob) -> tuple[list[str], int]:
    """Column names and row count, read from the parquet footer only."""
    with blob.open("rb") as fh:
        meta = pq.ParquetFile(fh).metadata
        return meta.schema.to_arrow_schema().names, meta.num_rows


def copy(bucket, src, dst_key: str) -> None:
    """Server-side copy; rewrite() handles objects too large for a single copy call."""
    dst = bucket.blob(dst_key)
    token, _, _ = dst.rewrite(src)
    while token is not None:
        token, _, _ = dst.rewrite(src, token=token)


def backup_folders(bucket) -> list[tuple[dt.datetime, str]]:
    """(timestamp, prefix) of every sales_parquet/backup/<YYYYmmdd-HHMM>/ folder."""
    it = bucket.list_blobs(prefix=f"{BACKUP_ROOT}/", delimiter="/")
    list(it)  # the folder prefixes are filled in once the listing is consumed
    out = []
    for prefix in it.prefixes:
        name = prefix.rstrip("/").rsplit("/", 1)[-1]
        try:
            out.append((dt.datetime.strptime(name, "%Y%m%d-%H%M").replace(tzinfo=WIB), prefix))
        except ValueError:
            continue  # not one of ours - never touch it
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)

    bucket = storage.Client().bucket(gcs_paths.BUCKET)
    stamp = dt.datetime.now(WIB).strftime("%Y%m%d-%H%M")
    plan = []
    blocked = []
    for src_key, dst_key in PROMOTIONS.items():
        src = bucket.get_blob(src_key)
        if src is None:
            print(f"MISSING source gs://{gcs_paths.BUCKET}/{src_key}")
            return 1
        dst = bucket.get_blob(dst_key)
        src_cols, src_rows = schema(src)
        print(f"\n{dst_key}")
        print(f"  new  <- {src_key}  ({describe(src)}, {src_rows:,} rows)")
        if dst is None:
            print("  old     (none)")
        else:
            dst_cols, dst_rows = schema(dst)
            print(f"  old     ({describe(dst)}, {dst_rows:,} rows)")
            added = [c for c in src_cols if c not in dst_cols]
            dropped = [c for c in dst_cols if c not in src_cols]
            if added or dropped:
                print(f"  columns: +{added}  -{dropped}")
            else:
                print("  columns: same")
            unexpected = [c for c in added if c not in EXPECTED_NEW_COLUMNS.get(dst_key, set())]
            if unexpected or dropped:
                blocked.append(dst_key)
                print("  BLOCKED: column change not expected")
        plan.append((src, dst, dst_key))

    if blocked:
        print(f"\nrefusing to promote - unexpected column changes in {blocked}")
        return 1
    if not args.apply:
        print("\ndry run - nothing copied (pass --apply)")
        return 0

    print()
    folders = backup_folders(bucket)
    latest = max((t for t, _ in folders), default=None)
    do_backup = latest is None or dt.datetime.now(WIB) - latest >= BACKUP_EVERY
    print(f"backup: {'taking one' if do_backup else f'skipped - latest is from {latest:%Y-%m-%d %H:%M} WIB'}")
    for src, dst, dst_key in plan:
        if dst is not None and do_backup:
            backup_key = f"{BACKUP_ROOT}/{stamp}/{dst_key.rsplit('/', 1)[-1]}"
            copy(bucket, dst, backup_key)
            print(f"backed up {dst_key} -> {backup_key}")
        copy(bucket, src, dst_key)
        print(f"copied    {src.name} -> {dst_key}")

    # prune old backups (only folders named like a timestamp, and always keep the newest one)
    cutoff = dt.datetime.now(WIB) - KEEP
    newest = max((t for t, _ in backup_folders(bucket)), default=None)
    for t, prefix in backup_folders(bucket):
        if t < cutoff and t != newest:
            for blob in bucket.list_blobs(prefix=prefix):
                blob.delete()
            print(f"pruned    {prefix} ({t:%Y-%m-%d %H:%M})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
