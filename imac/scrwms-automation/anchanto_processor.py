"""
COMBINED ANCHANTO PROCESSING SCRIPT (CSV INPUT - CORRECTED) -- MAC ADAPTATION
==============================================================================
Adapted from the original Windows-path version at:
  OneDrive/SOM/Anchanto Report/anchanto_processor_parquet - from_csv.py
(that file is untouched -- this is a separate copy for scheduled Mac execution,
 since launchd cannot reliably read/execute scripts stored inside the
 OneDrive-synced folder tree; see SCRWMS_SETUP.md).

Changes from the original, everything else is identical:
  1. Paths rewritten from Windows "D:\\SCARLETT_512\\SCARLETT-329\\..." to the
     equivalent Mac OneDrive mount -- same underlying cloud folder, verified by
     directory listing before this file was created.
  2. The two `input("Press Enter...")` prompts only fire when there's an
     interactive terminal (sys.stdin.isatty()) -- otherwise they're skipped, so
     a scheduled/unattended run doesn't hang forever waiting on stdin.
  3. Every output write that could overwrite an EXISTING file now removes that
     file first (_safe_remove). OneDrive's macOS client rejects a background
     process overwriting a file it already has fully synced (confirmed
     repeatedly during the SCRWMS automation work); removing first turns the
     write into a fresh create, which OneDrive allows.

- Reads B2C_Order_Report_*.csv (handles multiline fields)
- FIRST removes rows where Marketplace contains 'INT'
- THEN validates remaining rows against allowed marketplace list
- Skips rows with empty Marketplace (with warning)
- Handles duplicate output filenames by adding a counter
- Converts to Excel, then to CSV, then to Parquet
"""

import os
import sys
import glob
import csv
import numpy as np
import pandas as pd
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm
from collections import defaultdict

# Optional speed optimizations
try:
    import polars as pl
    USE_POLARS = True
    print("Polars detected - using high-speed DataFrame operations")
except ImportError:
    USE_POLARS = False
    print("Polars not installed - using pandas (slower). Install with: pip install polars")

try:
    import pyarrow
    HAVE_PYARROW = True
except ImportError:
    HAVE_PYARROW = False
    print("PyArrow not installed - Parquet compression will be basic. Install with: pip install pyarrow")

# =============================================================================
# CONFIGURATION -- Mac OneDrive paths (same cloud folder as the Windows D:\ version)
# =============================================================================

_SOM = "/Users/salesops/Library/CloudStorage/OneDrive-PT.OptoLumbungSejahtera/SOM"

B2C_INPUT_DIR = f"{_SOM}/Anchanto Report"
EXCEL_OUTPUT_DIR = f"{_SOM}/Anchanto Report/2026"
CSV_OUTPUT_DIR = f"{_SOM}/Anchanto Report/PowerBI/Anchanto_csv"
MAX_WORKERS = 10
DELETE_SOURCE = True

# Allowed values for the Marketplace column (data only, header excluded)
ALLOWED_MARKETPLACE_VALUES = {
    "MPL_Shopee Mall",
    "MPL_Tiktok Shop",
    "MPL_Lazada",
    "MPL_Shopee Star",
    "END_Nexus",
    "WNJ_Manual Jatis",
    "WNJ_OfficialWebsite",
    "MPL_Zalora"
}

MASTER_EXCEL = f"{_SOM}/Matrix/Master Data Sales.xlsx"
OUTPUT_PARQUET = f"{_SOM}/Anchanto Report/PowerBI/anchanto.parquet"

EXPECTED_COLUMNS = [
    "Marketplace", "Order Date", "Order Number", "Item Upc", "Item Name",
    "Ordered Quantity", "Order Status", "Customer Name", "Shipping City",
    "Shipping Postcode", "Order Packing Date", "Delivery Date (DD/MM/YYYY)",
    "Dispatch Scheduled Date", "Unit Price", "Dispatch Date", "Discount Value"
]

# =============================================================================
# Helper: remove-before-write, so a re-run never hits an OneDrive overwrite block
# =============================================================================

def _safe_remove(path):
    if os.path.exists(path):
        try:
            os.remove(path)
        except OSError as e:
            print(f"   (could not pre-remove {os.path.basename(path)}: {e}; will try to overwrite anyway)")

def _confirm(prompt):
    """Only prompt when there's an actual interactive terminal. Scheduled/launchd
    runs have no controlling TTY, so this becomes a no-op there instead of a hang."""
    if sys.stdin.isatty():
        input(prompt)

def _read_with_retry(read_fn, label, attempts=5, per_attempt_timeout=90):
    """Retry a read from an OneDrive-hosted file, with a HARD wall-clock timeout
    per attempt -- not just retry-on-exception. A file that isn't already
    hydrated locally gets fetched from the cloud on first read; that fetch
    sometimes raises TimeoutError/OSError (retry-on-exception handles that), but
    can also just hang indefinitely with no exception at all (confirmed: a read
    that never returned and never raised for 2+ hours). A bare `except` around
    a blocking call can't catch a hang that never completes, so each attempt
    runs in a worker thread and is abandoned (not cancelled -- Python threads
    can't be killed, but we stop waiting on it and move on) if it exceeds
    per_attempt_timeout seconds."""
    import time as _time
    import concurrent.futures as _cf
    last_err = None
    for attempt in range(1, attempts + 1):
        # NOT a context manager: ThreadPoolExecutor.__exit__ calls shutdown(wait=True),
        # which would block until the hung thread finishes -- exactly what we're
        # trying to avoid. shutdown(wait=False) lets us walk away from it; the
        # thread leaks until it eventually finishes or the process exits, but the
        # calling code is never blocked by it again.
        ex = _cf.ThreadPoolExecutor(max_workers=1)
        future = ex.submit(read_fn)
        try:
            result = future.result(timeout=per_attempt_timeout)
            ex.shutdown(wait=False)
            return result
        except _cf.TimeoutError:
            last_err = TimeoutError(f"read did not complete within {per_attempt_timeout}s")
            print(f"   read attempt {attempt}/{attempts} of {label} HUNG "
                  f"(no response in {per_attempt_timeout}s); abandoning and retrying")
        except (TimeoutError, OSError) as e:
            last_err = e
            print(f"   read attempt {attempt}/{attempts} of {label} failed ({e}); retrying")
        ex.shutdown(wait=False)
        _time.sleep(min(5 * attempt, 30))
    raise last_err

# =============================================================================
# STEP 1 FUNCTIONS (CSV input, multiline handling, INT filter, validation)
# =============================================================================

def get_datetime_from_cell(value):
    """Convert any cell value to a Python datetime object."""
    if hasattr(value, 'year') and hasattr(value, 'month') and hasattr(value, 'day'):
        if hasattr(value, 'to_pydatetime'):
            return value.to_pydatetime()
        return value
    if isinstance(value, str):
        try:
            from dateutil import parser
            return parser.parse(value, dayfirst=True)
        except ImportError:
            return pd.to_datetime(value, dayfirst=True).to_pydatetime()
        except Exception:
            pass
    if isinstance(value, (int, float)):
        return pd.to_datetime(value, unit='D', origin='1899-12-30').to_pydatetime()
    raise ValueError(f"Cannot parse date from {value}")

def get_order_date_from_df(df):
    """Extract the first non-null date from the 'Order Date' column."""
    col_name = None
    for col in df.columns:
        if col.lower() == "order date":
            col_name = col
            break
    if col_name is None:
        raise ValueError("Column 'Order Date' not found")
    if USE_POLARS and isinstance(df, pl.DataFrame):
        series = df[col_name]
        first = series.drop_nulls().head(1)
        if len(first) == 0:
            raise ValueError("Order Date column has no valid dates")
        first_val = first.item()
    else:
        series = df[col_name]
        first_val = series.dropna().iloc[0]
    return get_datetime_from_cell(first_val)

def generate_output_filename(order_date, duplicate_counter=0):
    """
    Generate formatted output filename based on order date.
    If duplicate_counter > 0, adds a suffix like _1, _2.
    """
    year = order_date.year % 100
    month = order_date.month
    month_abbr = order_date.strftime("%b")
    day = order_date.day
    if day <= 10:
        bracket = "(1)"
    elif day <= 20:
        bracket = "(2)"
    else:
        bracket = "(3)"
    base = f"Anchanto {year:02d} {month:02d} {month_abbr} {bracket} - Order Report"
    if duplicate_counter > 0:
        base += f"_{duplicate_counter}"
    return base + ".xlsx"

def read_csv_robust(filepath):
    """
    Read a CSV file that may contain multiline quoted fields.
    Returns a pandas DataFrame (no validation yet).
    """
    def logical_lines_generator():
        with open(filepath, 'r', encoding='utf-8-sig') as f:
            in_quotes = False
            current_parts = []
            for raw_line in f:
                line = raw_line.rstrip('\n\r')
                current_parts.append(line)
                quote_count = line.count('"')
                if quote_count % 2 == 1:
                    in_quotes = not in_quotes
                if not in_quotes and current_parts:
                    yield ' '.join(current_parts)
                    current_parts = []
            if current_parts:
                yield ' '.join(current_parts)

    all_rows = []
    for logical_line in logical_lines_generator():
        reader = csv.reader([logical_line], quotechar='"', delimiter=',')
        for row in reader:
            all_rows.append(row)
    if not all_rows:
        raise ValueError("No data rows found in CSV")
    header = all_rows[0]
    if len(header) < len(EXPECTED_COLUMNS):
        raise ValueError(f"CSV header has {len(header)} columns, expected at least {len(EXPECTED_COLUMNS)}")
    data = all_rows[1:]
    df = pd.DataFrame(data, columns=header[:len(header)])
    return df

def filter_and_validate(df, filepath):
    """
    1. Remove rows where Marketplace contains 'INT' (case-insensitive)
    2. Remove rows where Marketplace is empty (warn)
    3. Validate remaining rows: Marketplace must be in ALLOWED_MARKETPLACE_VALUES
    Returns (filtered_df, removed_INT_count, removed_empty_count, validation_error)
    """
    original_count = len(df)
    # Step 1: remove INT rows
    if USE_POLARS and isinstance(df, pl.DataFrame):
        int_mask = df['Marketplace'].cast(pl.Utf8).str.contains('(?i)INT', literal=False)
        filtered = df.filter(~int_mask)
        removed_int = original_count - filtered.height
        # Convert to pandas for easier handling
        df = filtered.to_pandas()
    else:
        int_mask = df['Marketplace'].astype(str).str.contains('INT', case=False, na=False)
        filtered = df[~int_mask]
        removed_int = original_count - len(filtered)
        df = filtered

    # Step 2: remove rows with empty Marketplace (after stripping)
    before_empty = len(df)
    df['Marketplace_clean'] = df['Marketplace'].astype(str).str.strip()
    df = df[df['Marketplace_clean'] != '']
    removed_empty = before_empty - len(df)

    # Step 3: validate remaining
    invalid_mask = ~df['Marketplace_clean'].isin(ALLOWED_MARKETPLACE_VALUES)
    invalid_rows = df[invalid_mask]
    if not invalid_rows.empty:
        bad_examples = invalid_rows['Marketplace_clean'].dropna().unique()[:10]
        error_msg = (f"Validation failed: {len(invalid_rows)} rows have Marketplace values not in allowed list. "
                     f"Examples: {list(bad_examples)}. This indicates a parsing error.")
        return None, removed_int, removed_empty, error_msg

    df.drop(columns=['Marketplace_clean'], inplace=True)
    return df, removed_int, removed_empty, None

def process_b2c_file(input_file, used_filenames_lock, used_filenames):
    """
    Process a single B2C Order Report CSV file:
      - Read robustly
      - Remove INT rows and empty Marketplace rows
      - Validate remaining rows
      - Generate unique output filename (avoid duplicates)
      - Save as Excel
      - Optionally delete original CSV
    """
    if not os.path.isfile(input_file):
        return (False, input_file, None, f"File not found: {input_file}")

    print(f"\nProcessing: {os.path.basename(input_file)}")
    try:
        df = read_csv_robust(input_file)
        print(f"   Read using: robust CSV parser (multiline-aware)")
    except Exception as e:
        return (False, input_file, None, f"Failed to read CSV: {e}")

    # Extract order date
    try:
        order_date = get_order_date_from_df(df)
        print(f"   Order Date: {order_date.strftime('%Y-%m-%d')}")
    except Exception as e:
        return (False, input_file, None, f"Failed to parse Order Date: {e}")

    # Filter INT and empty, validate
    filtered_df, removed_int, removed_empty, validation_err = filter_and_validate(df, input_file)
    if validation_err:
        return (False, input_file, None, validation_err)

    print(f"   Removed {removed_int} rows with 'INT' in Marketplace")
    if removed_empty > 0:
        print(f"   Removed {removed_empty} rows with empty Marketplace")
    print(f"   Kept {len(filtered_df)} valid rows")

    # Generate unique output filename (handle duplicates across parallel processes)
    base_filename = generate_output_filename(order_date, 0)
    with used_filenames_lock:
        counter = used_filenames.get(base_filename, 0)
        if counter > 0:
            final_filename = generate_output_filename(order_date, counter)
        else:
            final_filename = base_filename
        used_filenames[base_filename] = counter + 1

    output_path = os.path.join(EXCEL_OUTPUT_DIR, final_filename)
    try:
        _safe_remove(output_path)  # this segment's file from a previous run, if any
        if USE_POLARS and isinstance(filtered_df, pl.DataFrame):
            filtered_df.to_pandas().to_excel(output_path, index=False, engine='openpyxl')
        else:
            filtered_df.to_excel(output_path, index=False, engine='openpyxl')
        print(f"   Saved to: {final_filename}")
        return (True, input_file, output_path, f"INT removed: {removed_int}, empty removed: {removed_empty}")
    except Exception as e:
        return (False, input_file, None, f"Failed to save Excel: {e}")

# =============================================================================
# STEP 2 FUNCTIONS (Excel to CSV conversion)
# =============================================================================

def excel_to_csv(excel_path, csv_path):
    try:
        if not os.path.exists(csv_path):
            df = pd.read_excel(excel_path, engine='openpyxl')
            df.to_csv(csv_path, index=False, encoding='utf-8-sig')
            return (True, "CSV created (new file)", True)
        else:
            excel_mtime = os.path.getmtime(excel_path)
            csv_mtime = os.path.getmtime(csv_path)
            if excel_mtime > csv_mtime:
                df = pd.read_excel(excel_path, engine='openpyxl')
                _safe_remove(csv_path)  # overwriting an existing synced file
                df.to_csv(csv_path, index=False, encoding='utf-8-sig')
                return (True, "CSV updated (Excel was newer)", True)
            else:
                return (True, "CSV skipped (already up to date)", False)
    except Exception as e:
        return (False, f"Error: {e}", False)

# =============================================================================
# STEP 3 FUNCTIONS (Parquet creation)
# =============================================================================

def find_column(df, keywords):
    for col in df.columns:
        col_lower = col.lower()
        if all(kw.lower() in col_lower for kw in keywords):
            return col
    return None

def build_parquet_from_csvs():
    print("\n" + "-" * 70)
    print("STEP 3: Building Parquet file from CSV files")
    print("-" * 70)
    all_csv = glob.glob(os.path.join(CSV_OUTPUT_DIR, "*.csv"))
    csv_files = [f for f in all_csv if "$" not in os.path.basename(f)]
    if not csv_files:
        print(f"No CSV files found in {CSV_OUTPUT_DIR}")
        return False
    print(f"Found {len(csv_files)} CSV file(s).")
    data_frames = []
    for f in csv_files:
        try:
            df = _read_with_retry(
                lambda f=f: pd.read_csv(f, encoding='utf-8',
                                        usecols=lambda col: col in EXPECTED_COLUMNS,
                                        dtype=str, low_memory=False),
                os.path.basename(f))
            df["Name"] = os.path.splitext(os.path.basename(f))[0]
            data_frames.append(df)
            print(f"Loaded: {os.path.basename(f)} ({len(df)} rows)")
        except Exception as e:
            print(f"Error reading {f}: {e}")
    if not data_frames:
        return False
    combined = pd.concat(data_frames, ignore_index=True)
    print(f"Total rows before transformation: {len(combined):,}")
    delivery = combined.get("Delivery Date (DD/MM/YYYY)", pd.Series([None] * len(combined)))
    dispatch = combined.get("Dispatch Date", pd.Series([None] * len(combined)))
    scheduled = combined.get("Dispatch Scheduled Date", pd.Series([None] * len(combined)))
    combined["SentOn"] = np.where(delivery.notna() & (delivery != ""), delivery,
                                   np.where(dispatch.notna() & (dispatch != ""), dispatch, scheduled))
    drop_cols = ["Order Packing Date", "Delivery Date (DD/MM/YYYY)", "Dispatch Scheduled Date", "Dispatch Date"]
    drop_existing = [c for c in drop_cols if c in combined.columns]
    combined.drop(columns=drop_existing, inplace=True)
    combined.rename(columns={"Order Date": "CreatedOn", "Name": "Source"}, inplace=True)
    for col in ["CreatedOn", "SentOn"]:
        if col in combined.columns:
            combined[col] = pd.to_datetime(combined[col], errors='coerce')
    for col in ["Ordered Quantity", "Unit Price", "Discount Value"]:
        if col in combined.columns:
            combined[col] = pd.to_numeric(combined[col], errors='coerce').fillna(0).astype('int64')
    text_cols = ["Marketplace", "Order Number", "Item Upc", "Item Name", "Order Status", "Customer Name", "Shipping City", "Shipping Postcode", "Source"]
    for col in text_cols:
        if col in combined.columns:
            combined[col] = combined[col].fillna("").astype(str)
    uppercase_cols = ["Item Name", "Marketplace", "Source", "Order Status", "Customer Name", "Shipping City"]
    for col in uppercase_cols:
        if col in combined.columns:
            combined[col] = combined[col].str.upper()
    print("\nLoading product master...")
    if not os.path.exists(MASTER_EXCEL):
        print(f"ERROR: Master file not found at {MASTER_EXCEL}")
        return False
    try:
        product_df = _read_with_retry(
            lambda: pd.read_excel(MASTER_EXCEL, sheet_name="Product", skiprows=2, header=0),
            os.path.basename(MASTER_EXCEL))
    except Exception as e:
        print(f"ERROR: could not read master file after retries: {e}")
        return False
    item_col = find_column(product_df, ["item", "name"])
    brand_col = find_column(product_df, ["brand"])
    cat_col = find_column(product_df, ["category"])
    subcat_col = find_column(product_df, ["sub", "category"]) or find_column(product_df, ["subcategory"])
    variant_col = find_column(product_df, ["variant"])
    product_name_col = find_column(product_df, ["product", "name"])
    type_col = find_column(product_df, ["type"])
    rename_map = {}
    if item_col: rename_map[item_col] = "ItemName"
    if brand_col: rename_map[brand_col] = "Brand"
    if cat_col: rename_map[cat_col] = "Category"
    if subcat_col: rename_map[subcat_col] = "Sub Category"
    if variant_col: rename_map[variant_col] = "Variant"
    if product_name_col: rename_map[product_name_col] = "Product Name"
    if type_col: rename_map[type_col] = "Type of Item"
    product_master = product_df[list(rename_map.keys())].rename(columns=rename_map)
    product_master["ItemName"] = product_master["ItemName"].astype(str).str.strip().str.upper()
    product_master = product_master.drop_duplicates(subset=["ItemName"])
    product_master = product_master[product_master["ItemName"] != ""]
    print(f"Product master cleaned: {len(product_master):,} unique items")
    print("\nMerging with product master...")
    combined = combined.merge(product_master, left_on="Item Name", right_on="ItemName", how="left")
    combined.drop(columns=["ItemName"], inplace=True)
    final_order = ["Source", "Marketplace", "CreatedOn", "SentOn", "Order Number", "Item Upc", "Item Name", "Order Status", "Customer Name", "Shipping City", "Shipping Postcode", "Ordered Quantity", "Unit Price", "Discount Value", "Brand", "Category", "Sub Category", "Variant", "Product Name", "Type of Item"]
    final_order = [col for col in final_order if col in combined.columns]
    combined = combined[final_order]
    print("\nSaving to Parquet...")
    os.makedirs(os.path.dirname(OUTPUT_PARQUET), exist_ok=True)
    _safe_remove(OUTPUT_PARQUET)  # overwriting the existing shared parquet
    if HAVE_PYARROW:
        combined.to_parquet(OUTPUT_PARQUET, index=False, engine="pyarrow", compression="zstd", coerce_timestamps="us")
    else:
        combined.to_parquet(OUTPUT_PARQUET, index=False, engine="fastparquet", compression="snappy")
    file_size_gb = os.path.getsize(OUTPUT_PARQUET) / (1024**3)
    print(f"\nSUCCESS: Parquet saved to {OUTPUT_PARQUET}")
    print(f"   Total rows: {len(combined):,}")
    print(f"   File size: {file_size_gb:.2f} GB")
    return True

# =============================================================================
# MAIN ORCHESTRATION
# =============================================================================

def main():
    os.makedirs(EXCEL_OUTPUT_DIR, exist_ok=True)
    os.makedirs(CSV_OUTPUT_DIR, exist_ok=True)

    print("=" * 70)
    print(" COMBINED ANCHANTO PROCESSING SCRIPT (CSV INPUT - CORRECTED, Mac)")
    print("=" * 70)
    print(f"B2C Input Directory:      {B2C_INPUT_DIR}")
    print(f"Excel Output Directory:   {EXCEL_OUTPUT_DIR}")
    print(f"CSV Output Directory:     {CSV_OUTPUT_DIR}")
    print(f"Master Data Excel:        {MASTER_EXCEL}")
    print(f"Output Parquet:           {OUTPUT_PARQUET}")
    print(f"Parallel Workers:         {MAX_WORKERS}")
    print(f"Delete Source .csv Files: {DELETE_SOURCE}")
    print("=" * 70)

    # STEP 1
    print("\n" + "-" * 70)
    print("STEP 1: Processing B2C_Order_Report CSV files")
    print("-" * 70)
    b2c_files = glob.glob(os.path.join(B2C_INPUT_DIR, "B2C_Order_Report_*.csv"))
    if not b2c_files:
        print(f"\nNo B2C_Order_Report .csv files found in: {B2C_INPUT_DIR}")
    else:
        print(f"\nFound {len(b2c_files)} B2C CSV file(s) to process:")
        for f in b2c_files:
            print(f"   - {os.path.basename(f)}")
        if DELETE_SOURCE:
            print("\nWARNING: Original B2C .csv files will be DELETED after successful processing!")
            _confirm("   Press Enter to continue, or Ctrl+C to cancel...")

        from threading import Lock
        used_filenames_lock = Lock()
        used_filenames = defaultdict(int)

        processed = 0
        failed = 0
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(process_b2c_file, f, used_filenames_lock, used_filenames): f for f in b2c_files}
            for future in tqdm(as_completed(futures), total=len(b2c_files), desc="Processing B2C files", unit="file"):
                success, infile, out_excel, msg = future.result()
                if success:
                    processed += 1
                    print(f"\nSUCCESS: {os.path.basename(infile)}")
                    print(f"   - {msg}")
                    print(f"   - Output: {os.path.basename(out_excel)}")
                    if DELETE_SOURCE:
                        try:
                            os.remove(infile)
                            print(f"   - Deleted original: {os.path.basename(infile)}")
                        except Exception as e:
                            print(f"   - Failed to delete original: {e}")
                else:
                    failed += 1
                    print(f"\nFAILED: {os.path.basename(infile)}")
                    print(f"   - Error: {msg}")
        print("\n" + "-" * 70)
        print(f"STEP 1 COMPLETE:")
        print(f"   Successfully processed: {processed} file(s)")
        print(f"   Failed: {failed} file(s)")

    # STEP 2
    print("\n" + "-" * 70)
    print("STEP 2: Converting Excel files to CSV")
    print("-" * 70)
    excel_files = glob.glob(os.path.join(EXCEL_OUTPUT_DIR, "*.xlsx"))
    if not excel_files:
        print(f"\nNo Excel files found in: {EXCEL_OUTPUT_DIR}")
    else:
        print(f"\nFound {len(excel_files)} Excel file(s) to convert:")
        converted = 0
        skipped = 0
        failed = 0
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {}
            for xl in excel_files:
                csv_name = os.path.basename(xl)[:-5] + ".csv"
                csv_path = os.path.join(CSV_OUTPUT_DIR, csv_name)
                futures[executor.submit(excel_to_csv, xl, csv_path)] = (xl, csv_name)
            for future in tqdm(as_completed(futures), total=len(excel_files), desc="Converting to CSV", unit="file"):
                xl_file, csv_name = futures[future]
                success, msg, conv_flag = future.result()
                if success:
                    if conv_flag:
                        converted += 1
                        print(f"\nCONVERTED: {os.path.basename(xl_file)} -> {csv_name}")
                        print(f"   - {msg}")
                    else:
                        skipped += 1
                        print(f"\nSKIPPED: {os.path.basename(xl_file)} -> {csv_name}")
                        print(f"   - {msg}")
                else:
                    failed += 1
                    print(f"\nFAILED: {os.path.basename(xl_file)}")
                    print(f"   - Error: {msg}")
        print("\n" + "-" * 70)
        print(f"STEP 2 COMPLETE:")
        print(f"   Converted: {converted} file(s)")
        print(f"   Skipped (up to date): {skipped} file(s)")
        print(f"   Failed: {failed} file(s)")

    # STEP 3
    parquet_ok = build_parquet_from_csvs()

    print("\n" + "=" * 70)
    print(" FINAL SUMMARY")
    print("=" * 70)
    print("INT filter applied - rows with 'INT' in Marketplace are REMOVED")
    print(f"Filtered Excel files are in: {EXCEL_OUTPUT_DIR}")
    print(f"CSV files are in: {CSV_OUTPUT_DIR}")
    if parquet_ok:
        print(f"Parquet file created: {OUTPUT_PARQUET}")
    else:
        print("WARNING: Parquet file NOT created (no CSV data or missing master file)")
    if DELETE_SOURCE:
        print("Original B2C .csv files have been DELETED")
    else:
        print("Original B2C .csv files were preserved (DELETE_SOURCE = False)")
    _confirm("\nPress Enter to exit...")
    return parquet_ok

if __name__ == "__main__":
    # Exit non-zero when the parquet wasn't rebuilt -- otherwise this failure
    # is invisible to run_refresh.sh (and anyone reading its log) since every
    # other step can succeed while this is the one that actually matters.
    sys.exit(0 if main() else 1)
