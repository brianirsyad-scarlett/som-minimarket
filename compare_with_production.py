"""
Compare the draft output with the production Primary_Sales.parquet, per month and
report source: rows, Quantity, Qty Sales, Revenue, Qty Return.

Every row of a monthly workbook has its SentOn inside that month, so grouping
the production file by SentOn month gives the same slices as the draft's
monthly files.

    python compare_with_production.py work/monthly_parquet/"2026 08 Aug.parquet" ...
    python compare_with_production.py --production <local path> <monthly parquet>...

Differences on the current and previous month are expected when the two runs
did not read the same inputs (Odoo / Accurate / Anchanto refresh at different
times). Older months should match exactly.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

import polars as pl

import gcs_paths

METRICS = ["Quantity", "Qty Sales", "Revenue", "Qty Return"]


def summarise(df: pl.DataFrame) -> pl.DataFrame:
    return (df.group_by("ReportSource")
              .agg(pl.len().alias("rows"), *[pl.col(m).sum() for m in METRICS])
              .sort("ReportSource"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("monthly", nargs="+", type=Path)
    ap.add_argument("--production", type=Path, default=None,
                    help="Local copy of the production parquet (default: download it).")
    args = ap.parse_args(argv)

    prod_path = args.production
    if prod_path is None:
        from google.cloud import storage
        prod_path = Path(tempfile.gettempdir()) / "production_Primary_Sales.parquet"
        storage.Client().bucket(gcs_paths.BUCKET).blob(
            gcs_paths.PRODUCTION_COMBINED).download_to_filename(str(prod_path), timeout=900)

    prod = pl.scan_parquet(prod_path)
    mismatched = 0
    for path in args.monthly:
        draft = pl.read_parquet(path)
        month = draft.select(pl.col("SentOn").drop_nulls().dt.truncate("1mo").mode().first()).item()
        p = prod.filter(pl.col("SentOn").dt.truncate("1mo") == month).collect()
        a, b = summarise(draft), summarise(p)
        joined = a.join(b, on="ReportSource", how="full", suffix="_prod", coalesce=True).fill_null(0)
        diff = joined.filter(pl.any_horizontal(
            [pl.col(c) != pl.col(f"{c}_prod") for c in ["rows", *METRICS]]))
        status = "MATCH" if diff.is_empty() else "DIFF"
        mismatched += not diff.is_empty()
        print(f"\n{path.stem}  ({month})  draft {draft.height:,} rows / production {p.height:,} rows  -> {status}")
        with pl.Config(tbl_rows=50, tbl_cols=20, tbl_width_chars=200, thousands_separator=True):
            print(joined.select("ReportSource", "rows", "rows_prod",
                                *[x for m in METRICS for x in (m, f"{m}_prod")]))
    return 1 if mismatched else 0


if __name__ == "__main__":
    sys.exit(main())
