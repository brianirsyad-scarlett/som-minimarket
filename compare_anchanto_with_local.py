"""
Local-only check: the Anchanto rows the draft builds from Anchanto.parquet vs
the rows the local sales_monthly_report.py reads from the quarterly workbooks.

    python compare_anchanto_with_local.py 2026-08 2026-09

Needs work/inputs/Anchanto.parquet and work/inputs/Master Data Sales.xlsx
(download_inputs.py), and the SOM drive for the local side.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import polars as pl

import sales_monthly_report as draft

LOCAL_SCRIPT = Path(r"D:\SCARLETT_512\SCARLETT-329\SOM\Automation\Sales-MonthlyReport\sales_monthly_report.py")


def load_local():
    spec = importlib.util.spec_from_file_location("local_smr", LOCAL_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def as_text(df: pl.DataFrame, cols) -> pl.DataFrame:
    return df.select([pl.col(c).cast(pl.String) for c in cols])


def main(argv) -> int:
    local = load_local()
    draft.configure_paths(draft.WORK / "inputs", None)
    bad = 0
    for token in argv or ["prev", "current"]:
        import datetime as dt
        y, m = draft.parse_month(token, dt.date.today())
        spec_l = local.MonthSpec(y, m, [15])
        spec_d = draft.MonthSpec(y, m, [15])
        a = local.q_anchanto(spec_l.anchanto_files(), spec_l.start, spec_l.end)
        b = draft.q_anchanto(spec_d, spec_d.start, spec_d.end)
        cols = sorted(set(a.columns) & set(b.columns))
        only = sorted(set(a.columns) ^ set(b.columns))
        ha = as_text(a, cols).select(pl.struct(cols).hash(seed=1)).to_series().sort()
        hb = as_text(b, cols).select(pl.struct(cols).hash(seed=1)).to_series().sort()
        same = ha.equals(hb)
        bad += not same
        print(f"\n{y}-{m:02d}: local {a.height:,} rows, Qty {a['Quantity'].sum():,} | "
              f"draft {b.height:,} rows, Qty {b['Quantity'].sum():,} | "
              f"{'IDENTICAL' if same else 'DIFFERENT'}"
              + (f" | columns on one side only: {only}" if only else ""))
        if not same:
            ga = a.group_by("SalesType").agg(pl.len().alias("rows_local"), pl.col("Quantity").sum().alias("qty_local"))
            gb = b.group_by("SalesType").agg(pl.len().alias("rows_draft"), pl.col("Quantity").sum().alias("qty_draft"))
            print(ga.join(gb, on="SalesType", how="full", coalesce=True).sort("SalesType"))
            for c in cols:
                if not as_text(a, [c]).to_series().sort().equals(as_text(b, [c]).to_series().sort()):
                    print(f"  column differs: {c}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
