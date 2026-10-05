"""
The two PCC steps from Data\\Report\\Sales\\Anchanto Report\\Anchanto_City, for GitHub:

  sales   = 1_BigQuery_PCC_Sales.py
            Primary_Sales (offline) + Anchanto (online) -> PCC_Sales.parquet
  orders  = 2_BigQuery_Master_Order_Number_City.py
            sales_order_number/*order_number*.csv + Master_Order Number City.csv
            -> PCC_Order_Number.parquet, and the list of unmatched (Province, City)

Same columns, joins and filters as the local scripts. They are written with polars'
streaming engine instead of pandas so ~45M rows fit on a GitHub runner; the output
schema matches the production files (strings, date32 dates, int64 Quantity).

    python pcc.py sales  --primary P.parquet --anchanto A.parquet --out PCC_Sales.parquet
    python pcc.py orders --csv-dir DIR --master M.csv --out PCC_Order_Number.parquet \
                         --unmatched unmatched.csv
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import polars as pl

FINAL_COLUMNS = ["Brand", "Category", "Channel", "CreatedOn", "City", "CustomerName",
                 "Order Number", "Product Name", "Quantity", "SentOn", "Sub Channel",
                 "Type of Item", "Variant"]
DATE_COLS = {"CreatedOn", "SentOn"}

# pandas.read_csv's default NA strings: the local script reads with pandas, so these
# cells are missing there. Its merge then matches a missing City to the master's
# blank City row (pandas joins NaN to NaN) - mirrored below by keying missing as "nan".
PANDAS_NA = ["", "#N/A", "#N/A N/A", "#NA", "-1.#IND", "-1.#QNAN", "-NaN", "-nan", "1.#IND",
             "1.#QNAN", "<NA>", "N/A", "NA", "NULL", "NaN", "None", "n/a", "nan", "null"]


def _pandas_str_key(col: str) -> pl.Expr:
    """The City join key: stripped, with missing keyed so it matches missing."""
    return pl.col(col).fill_null("nan").str.strip_chars()


def _typed(lf: pl.LazyFrame) -> pl.LazyFrame:
    exprs = []
    for c in FINAL_COLUMNS:
        if c in DATE_COLS:
            exprs.append(pl.col(c).cast(pl.Date))
        elif c == "Quantity":
            exprs.append(pl.col(c).cast(pl.Int64))
        else:
            exprs.append(pl.col(c).cast(pl.String))
    return lf.select(exprs)


def pcc_sales(primary: Path, anchanto: Path, out: Path) -> int:
    offline = (pl.scan_parquet(primary)
               .select(["Brand", "Category", "Channel", "CreatedOn", "City", "CustomerName",
                        "NoPesanan", "Product Name", "Quantity", "SentOn", "Sub Channel",
                        "Type of Item", "Variant"])
               .rename({"NoPesanan": "Order Number"}))
    online = (pl.scan_parquet(anchanto)
              .select(["Brand", "Category", "CreatedOn", "Shipping City", "Marketplace",
                       "Order Number", "Product Name", "Ordered Quantity", "SentOn",
                       "Type of Item", "Variant"])
              .rename({"Shipping City": "City", "Marketplace": "CustomerName",
                       "Ordered Quantity": "Quantity"})
              .with_columns(pl.lit("ONLINE").alias("Channel"),
                            pl.col("CustomerName").alias("Sub Channel")))
    union = pl.concat([_typed(offline), _typed(online)], how="vertical")
    out.parent.mkdir(parents=True, exist_ok=True)
    union.sink_parquet(out)
    n = pl.scan_parquet(out).select(pl.len()).collect().item()
    print(f"PCC_Sales: {n:,} rows -> {out}")
    return n


def pcc_orders(csv_dir: Path, master: Path, out: Path, unmatched_csv: Path | None,
               keyword: str = "order_number") -> list[tuple[str, str]]:
    files = sorted(p for p in csv_dir.glob("*.csv") if keyword.lower() in p.name.lower())
    if not files:
        raise SystemExit(f"No CSV files containing '{keyword}' in {csv_dir}")
    print(f"{len(files)} order-number file(s)")

    orders = pl.concat([pl.scan_csv(f, infer_schema=False, null_values=PANDAS_NA) for f in files], how="diagonal_relaxed")

    # Master: ';'-separated, BOM; first row per City (drop_duplicates keep='first').
    m = pl.read_csv(master, separator=";", encoding="utf8-lossy", infer_schema=False,
                    null_values=PANDAS_NA)
    m = m.rename({c: c.strip().lstrip("﻿") for c in m.columns})
    for col in ("City", "ADM2_NAME (STANDARDIZED)", "ADM1"):
        if col not in m.columns:
            raise SystemExit(f"Master file is missing column {col!r}: {m.columns}")
    m = (m.with_columns(_pandas_str_key("City").alias("City_clean"))
          .select("City_clean", pl.col("ADM2_NAME (STANDARDIZED)").alias("ADM2_Standardized"), "ADM1")
          .unique(subset="City_clean", keep="first", maintain_order=True))
    print(f"Master: {m.height:,} unique cities")

    merged = (orders.with_columns(_pandas_str_key("City").alias("City_clean"))
                    .join(m.lazy(), on="City_clean", how="left")
                    .drop("City_clean"))

    unmatched = (merged.filter(pl.col("ADM2_Standardized").is_null())
                       .select(pl.col("Province").str.strip_chars(), pl.col("City").str.strip_chars())
                       .unique().sort(["Province", "City"], nulls_last=True)
                       .collect(engine="streaming"))
    pairs = [(p or "", c or "") for p, c in unmatched.iter_rows()]

    final = (merged.unique()
                   .with_columns(pl.col("ADM2_Standardized").fill_null("-"), pl.col("ADM1").fill_null("-")))
    cols = final.collect_schema().names()
    cols.remove("ADM1")
    cols.insert(cols.index("ADM2_Standardized") + 1, "ADM1")
    out.parent.mkdir(parents=True, exist_ok=True)
    final.select(cols).sink_parquet(out)
    n = pl.scan_parquet(out).select(pl.len()).collect().item()
    print(f"PCC_Order_Number: {n:,} rows -> {out}")

    if pairs:
        print(f"\n{len(pairs)} unmatched (Province, City) pair(s):")
        for p, c in pairs:
            print(f"{p},{c}")
    else:
        print("\nAll cities matched.")
    if unmatched_csv:
        unmatched_csv.parent.mkdir(parents=True, exist_ok=True)
        with unmatched_csv.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["Province", "City"])
            w.writerows(pairs)
    return pairs


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sales")
    s.add_argument("--primary", type=Path, required=True)
    s.add_argument("--anchanto", type=Path, required=True)
    s.add_argument("--out", type=Path, required=True)
    o = sub.add_parser("orders")
    o.add_argument("--csv-dir", type=Path, required=True)
    o.add_argument("--master", type=Path, required=True)
    o.add_argument("--out", type=Path, required=True)
    o.add_argument("--unmatched", type=Path)
    a = ap.parse_args(argv)
    if a.cmd == "sales":
        pcc_sales(a.primary, a.anchanto, a.out)
    else:
        pcc_orders(a.csv_dir, a.master, a.out, a.unmatched)
    return 0


if __name__ == "__main__":
    sys.exit(main())
