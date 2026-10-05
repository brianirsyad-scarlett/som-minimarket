"""Publish Indomaret sell out from the cloud draft to PRODUCTION GCS.

    python publish_indomaret.py            # after indomaret-daily's download
    python publish_indomaret.py --dry-run

Replaces the laptop's manual unzip (1_zip_converter / 2_folder_extractor /
3_csv_extractor, which also DELETE the zips) plus the hand upload.

Draft (sales_parquet/raw/minimarket/indomaret/) holds the portal zips as
downloaded (zip > csv.gz). Production (sales_sell out_minimarket/indomaret/):

by store  DAILY_STORE_PERFORMANCE_<D>_... = a 7-day window ending on D.
          Production keeps every day's window as-is (since 2026-04-02), so each
          new one is unpacked, checked, and uploaded under the same name.

by branch DAILY_SELLING_OUT_<D>_...        = a 14-day window ending on D
          DAILY_SELLING_OUT_FULL_MONTH_<M>  = one whole month
          Production must NEVER hold a date twice: Minimarket_Sales concatenates
          every file. So it keeps one FULL_MONTH per closed month and, until that
          arrives, one month-to-date file per open month (DAILY_SELLING_OUT_
          <last day>_... covering the 1st..last day). Each run rebuilds the MTD
          file per date from the NEWEST source that has the date - overlapping
          windows are the same numbers apart from late corrections to the last
          day (e.g. 50 -> 51 units), so the newest is the most correct.
          When a FULL_MONTH arrives it is published and that month's MTD file is
          moved to the draft's sell_out_branch/superseded/ (moved, not deleted).

stock     DAILY_STOCK_BRANCH_<D>_...        = a 3-day window ending on D, per DC
          (national / DC / store split). Kept one-per-day as-is, like by store,
          in stock/.
"""

import argparse
import calendar
import gzip
import io
import re
import sys
import tempfile
import zipfile
from pathlib import Path

import pandas as pd
from google.cloud import storage

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE / "sources"))
import gcs_paths  # noqa: E402

PROD = "sales_sell out_minimarket/indomaret"
STORE_RE = re.compile(r"^DAILY_STORE_PERFORMANCE_(\d{8})_.*\.(zip|csv)$")
STOCK_RE = re.compile(r"^DAILY_STOCK_BRANCH_(\d{8})_.*\.(zip|csv)$")
WINDOW_RE = re.compile(r"^DAILY_SELLING_OUT_(\d{8})_.*\.(zip|csv)$")
FULL_RE = re.compile(r"^DAILY_SELLING_OUT_FULL_MONTH_(\d{6})_.*\.(zip|csv)$")
problems = []


def iso(d8: str) -> str:
    return f"{d8[:4]}-{d8[4:6]}-{d8[6:]}"


def unpack(content: bytes) -> bytes:
    """zip > (csv | csv.gz) -> csv bytes."""
    if content[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            content = zf.read(zf.namelist()[0])
    if content[:2] == b"\x1f\x8b":
        content = gzip.decompress(content)
    return content


def lines_by_date(text: str):
    """(header, {date: [raw lines]}) - rows are kept as the portal wrote them."""
    lines = text.splitlines(keepends=True)
    by = {}
    for l in lines[1:]:
        if l[:4].isdigit():
            by.setdefault(l[:10], []).append(l if l.endswith("\n") else l + "\n")
    return lines[0] if lines[0].endswith("\n") else lines[0] + "\n", by


def name(blob) -> str:
    return blob.name.rsplit("/", 1)[-1]


# ------------------------------------------------------------ by store -----

def publish_windows(bucket, dry: bool, label: str, report: str, pattern, prod_sub: str, days: int) -> None:
    """Windows that production keeps one-per-day, as-is (by store: 7 days;
    stock: 3 days). Each is unpacked, its date range checked, then uploaded."""
    have = {name(b) for b in bucket.list_blobs(prefix=f"{PROD}/{prod_sub}/")}
    todo = sorted((b for b in bucket.list_blobs(prefix=gcs_paths.prefix("indomaret", report))
                   if pattern.match(name(b)) and name(b).rsplit(".", 1)[0] + ".csv" not in have), key=name)
    print(f"--- {label}: {len(todo)} new window(s) to publish ---")
    for b in todo:
        d8 = pattern.match(name(b)).group(1)
        dest = f"{PROD}/{prod_sub}/{name(b).rsplit('.', 1)[0]}.csv"
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as fh:
            fh.write(unpack(b.download_as_bytes()))
            path = fh.name
        dates = pd.read_csv(path, sep="|", usecols=["DATE"], dtype=str)["DATE"]
        lo, hi = dates.min(), dates.max()
        want_lo = (pd.Timestamp(iso(d8)) - pd.Timedelta(days=days - 1)).strftime("%Y-%m-%d")
        if (lo, hi) != (want_lo, iso(d8)):
            problems.append(f"{label} {name(b)}: dates {lo}..{hi}, expected {want_lo}..{iso(d8)} - NOT published")
            Path(path).unlink()
            continue
        if dry:
            print(f"  [dry-run] {dest}  ({len(dates):,} rows {lo}..{hi})")
        else:
            blob = bucket.blob(dest)
            blob.chunk_size = 32 * 1024 * 1024
            blob.upload_from_filename(path, content_type="text/csv", if_generation_match=0, timeout=1800)
            print(f"  published {dest}  ({len(dates):,} rows {lo}..{hi})")
        Path(path).unlink()


# ----------------------------------------------------------- by branch -----

def move(bucket, blob, dry: bool) -> None:
    dest = gcs_paths.prefix("indomaret", "sell_out_branch") + "superseded/" + name(blob)
    if dry:
        print(f"  [dry-run] move {blob.name} -> {dest}")
        return
    copy = bucket.copy_blob(blob, bucket, dest)
    copy.reload()
    if copy.md5_hash != blob.md5_hash:
        raise SystemExit(f"copy of {blob.name} does not match - original left in place")
    blob.delete()
    print(f"  moved {name(blob)} -> {dest}")


def publish_branch(bucket, dry: bool) -> None:
    prod = [b for b in bucket.list_blobs(prefix=f"{PROD}/sell_out/")]
    full_months = {FULL_RE.match(name(b)).group(1) for b in prod if FULL_RE.match(name(b))}
    mtd = {}   # YYYYMM -> blob
    for b in prod:
        m = WINDOW_RE.match(name(b))
        if m and not FULL_RE.match(name(b)):
            mtd.setdefault(m.group(1)[:6], []).append(b)
    draft = [b for b in bucket.list_blobs(prefix=gcs_paths.prefix("indomaret", "sell_out_branch"))
             if "/superseded/" not in b.name]

    # 1. full months
    for b in sorted((b for b in draft if FULL_RE.match(name(b)) and name(b).endswith(".zip")), key=name):
        month = FULL_RE.match(name(b)).group(1)
        if month in full_months:
            continue
        header, by = lines_by_date(unpack(b.download_as_bytes()).decode("utf-8"))
        days = calendar.monthrange(int(month[:4]), int(month[4:]))[1]
        if len(by) != days or any(d[:7].replace("-", "") != month for d in by):
            problems.append(f"FULL_MONTH {name(b)}: {len(by)} dates, expected {days} in {month} - NOT published")
            continue
        dest = f"{PROD}/sell_out/{name(b)[:-4]}.csv"
        body = header + "".join(l for d in sorted(by) for l in by[d])
        if dry:
            print(f"  [dry-run] {dest}  (full month {month})")
        else:
            bucket.blob(dest).upload_from_string(body.encode("utf-8"), content_type="text/csv", if_generation_match=0)
            print(f"  published {dest}  (full month {month})")
        full_months.add(month)
        for old in mtd.pop(month, []):
            move(bucket, old, dry)

    # 2. month-to-date for every open month: newest source per date wins
    sources = [(WINDOW_RE.match(name(b)).group(1), b) for b in draft
               if WINDOW_RE.match(name(b)) and not FULL_RE.match(name(b)) and name(b).endswith(".zip")]
    sources += [(WINDOW_RE.match(name(b)).group(1), b) for bs in mtd.values() for b in bs]
    best, src_of, header = {}, {}, None
    # Oldest first so newer sources overwrite; on the same end date the draft
    # .zip sorts after the published .csv, so the portal's own file wins.
    for d8, b in sorted(sources, key=lambda t: (t[0], name(t[1]))):
        h, by = lines_by_date(unpack(b.download_as_bytes()).decode("utf-8"))
        header = header or h
        for d, rows in by.items():
            if d[:7].replace("-", "") not in full_months:
                best[d], src_of[d] = rows, name(b)
    months = sorted({d[:7].replace("-", "") for d in best})
    print(f"--- by branch: full months {sorted(full_months)[-3:]}, open month(s) {months} ---")
    for month in months:
        dates = sorted(d for d in best if d[:7].replace("-", "") == month)
        first, last = dates[0], dates[-1]
        expected = pd.date_range(f"{month[:4]}-{month[4:]}-01", last).strftime("%Y-%m-%d").tolist()
        if dates != expected:
            missing = sorted(set(expected) - set(dates))
            problems.append(f"MTD {month}: missing {len(missing)} date(s) {missing[:5]} - published without them")
        # Named like the June 2026 hand-made file: DAILY_SELLING_OUT_<last day>_
        # <rest of the name of the file that supplied the last day>.csv
        tail = re.match(r"DAILY_SELLING_OUT_\d{8}_(.+)\.(zip|csv)$", src_of[last]).group(1)
        out_name = f"DAILY_SELLING_OUT_{last.replace('-', '')}_{tail}.csv"
        body = header + "".join(l for d in dates for l in best[d])
        current = mtd.get(month, [])
        if len(current) == 1 and name(current[0]) == out_name and current[0].download_as_text(encoding="utf-8") == body:
            print(f"  MTD {month}: {first}..{last} already published as {out_name}")
            continue
        dest = f"{PROD}/sell_out/{out_name}"
        if dry:
            print(f"  [dry-run] {dest}  (MTD {first}..{last}, {len(dates)} dates, {body.count(chr(10)) - 1:,} rows)")
        else:
            bucket.blob(dest).upload_from_string(body.encode("utf-8"), content_type="text/csv")
            print(f"  published {dest}  (MTD {first}..{last}, {len(dates)} dates, {body.count(chr(10)) - 1:,} rows)")
        for old in current:
            if name(old) != out_name:
                move(bucket, old, dry)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    bucket = storage.Client().bucket(gcs_paths.BUCKET)
    publish_windows(bucket, a.dry_run, "by store", "sell_out_store", STORE_RE, "daily_sell_out", 7)
    publish_windows(bucket, a.dry_run, "stock", "stock", STOCK_RE, "stock", 3)
    publish_branch(bucket, a.dry_run)
    for p in problems:
        print(f"::warning::{p}")
    return 1 if any("NOT published" in p for p in problems) else 0


if __name__ == "__main__":
    sys.exit(main())
