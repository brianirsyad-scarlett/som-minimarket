"""
Step 2 of the daily chain, for GCS: monthly workbooks -> Primary_Sales.parquet.

The local step (Data\\Report\\Sales\\Sales csv\\primary_sales_csv_and_parquet.py)
exports every sheet of every monthly workbook to CSV, then reads all of those
CSVs as text, concatenates them and types a handful of columns. Keeping ~5 GB of
CSVs in GCS just to redo that is wasteful, so here each month is kept as one
small parquet instead, produced by the same steps:

    workbook sheet --pandas/openpyxl--> CSV text --read as str--> typed()

and the combined file is those monthly parquets stacked. typed() is the local
script's type conversion, unchanged. History months are seeded once from the
local CSVs by seed_history.py; each run rebuilds only the months it just wrote.

    python build_primary_parquet.py month work/output/2026/"2026 09 Sep.xlsx" ...
    python build_primary_parquet.py combine work/monthly_parquet work/Primary_Sales.parquet
"""

from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

# Same lists as primary_sales_csv_and_parquet.py.
DATE_COLS = ["CreatedOn", "SentOn", "InvoiceOn"]
NUMERIC_COLS = ["Qty Sales", "Revenue", "Qty Return", "Quantity"]
TEXT_COLS = ["NoPesanan", "SalesType", "SubSalesType", "Channel Group", "Channel",
             "Sub Channel", "Region", "Province", "City", "CustomerName",
             "Payment Terms", "Customer & Address", "SO Number", "ReportSource",
             "ItemName", "Status", "Brand", "Category", "SubCategory",
             "Variant", "Product Name", "Type of Item"]


def typed(combined: pd.DataFrame) -> pd.DataFrame:
    """primary_sales_csv_and_parquet.py's type conversions, verbatim.

    That script runs them on every month at once; a column a month lacks is
    NaN there, which the fills below then turn into "" / 0. Adding the missing
    known columns first gives each month the same result on its own.
    """
    for col in DATE_COLS + NUMERIC_COLS + TEXT_COLS:
        if col not in combined.columns:
            combined[col] = pd.Series([None] * len(combined), dtype=object)

    for col in DATE_COLS:
        if col in combined.columns:
            combined[col] = pd.to_datetime(combined[col], errors='coerce').dt.date
    for col in NUMERIC_COLS:
        if col in combined.columns:
            combined[col] = pd.to_numeric(combined[col], errors='coerce').fillna(0).astype('int64')
    for col in TEXT_COLS:
        if col in combined.columns:
            combined[col] = combined[col].fillna('').astype(str)
    return combined


def read_csv_texts(frames_or_paths) -> pd.DataFrame:
    """Read CSVs the way the local script does (all text) and stack them."""
    parts = []
    for src in frames_or_paths:
        df = pd.read_csv(src, encoding='utf-8', dtype=str, low_memory=False)
        if not df.empty:
            parts.append(df)
    if not parts:
        return pd.DataFrame()
    return pd.concat(parts, ignore_index=True)


def workbook_to_frame(xlsx: Path) -> pd.DataFrame:
    """Every sheet of one monthly workbook, via the same CSV round trip as locally."""
    excel_file = pd.ExcelFile(xlsx, engine='openpyxl')
    buffers = []
    for sheet in excel_file.sheet_names:
        df = pd.read_excel(xlsx, sheet_name=sheet, engine='openpyxl')
        if df.empty:
            print(f"  skipped empty sheet '{sheet}'")
            continue
        buf = io.StringIO()
        df.to_csv(buf, index=False)
        buf.seek(0)
        buffers.append(buf)
        print(f"  sheet '{sheet}': {len(df):,} rows")
    return read_csv_texts(buffers)


def write_month(df: pd.DataFrame, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out, index=False, engine='pyarrow')
    print(f"wrote {out} ({len(df):,} rows, {out.stat().st_size / 1e6:.1f} MB)")


def combine(monthly_dir: Path, out: Path) -> int:
    """Stack the monthly parquets into one file, one month in memory at a time."""
    files = sorted(monthly_dir.glob("*.parquet"))
    if not files:
        print(f"no monthly parquet files in {monthly_dir}")
        return 1

    # Union of every month's columns. A column only some months have becomes null
    # in the others - what pd.concat does locally for the columns typed() does not fill.
    fields: dict[str, pa.DataType] = {}
    for f in files:
        for field in pq.read_schema(f):
            if field.name.startswith("__index_level_"):
                continue
            t = field.type
            if pa.types.is_string(t):
                t = pa.large_string()
            # An all-blank column in one month is typed null there; take the
            # real type from whichever month has data.
            if field.name not in fields or pa.types.is_null(fields[field.name]):
                fields[field.name] = t
    # A month where a date column is entirely blank comes out of .dt.date as
    # NaT objects, which pyarrow stores as a timestamp. On the combined frame
    # locally the column is date32; force that.
    for name in DATE_COLS:
        if name in fields:
            fields[name] = pa.date32()
    fields = {n: (pa.large_string() if pa.types.is_null(t) else t) for n, t in fields.items()}
    schema = pa.schema([pa.field(n, t) for n, t in fields.items()])

    total = 0
    out.parent.mkdir(parents=True, exist_ok=True)
    with pq.ParquetWriter(out, schema) as writer:
        for f in files:
            table = pq.read_table(f)
            cols = []
            for field in schema:
                if field.name in table.column_names:
                    cols.append(table.column(field.name).cast(field.type))
                else:
                    cols.append(pa.nulls(table.num_rows, field.type))
            writer.write_table(pa.Table.from_arrays(cols, schema=schema))
            total += table.num_rows
            print(f"  {f.name}: {table.num_rows:,} rows")
    print(f"wrote {out} ({total:,} rows, {out.stat().st_size / 1e6:.1f} MB)")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("month", help="monthly workbook(s) -> monthly parquet(s)")
    m.add_argument("workbooks", nargs="+", type=Path)
    m.add_argument("--out-dir", type=Path, default=Path("work/monthly_parquet"))
    c = sub.add_parser("combine", help="monthly parquets -> Primary_Sales.parquet")
    c.add_argument("monthly_dir", type=Path)
    c.add_argument("out", type=Path)
    args = ap.parse_args(argv)

    if args.cmd == "month":
        for xlsx in args.workbooks:
            print(f"{xlsx.name}")
            write_month(typed(workbook_to_frame(xlsx)), args.out_dir / f"{xlsx.stem}.parquet")
        return 0
    return combine(args.monthly_dir, args.out)


if __name__ == "__main__":
    sys.exit(main())
