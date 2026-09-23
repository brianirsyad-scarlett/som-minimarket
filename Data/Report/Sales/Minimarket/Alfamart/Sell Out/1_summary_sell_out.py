import pandas as pd
import os
import re

# -------------------------------
# Configuration
# -------------------------------
source_dir = r"D:\SCARLETT_512\SCARLETT-329\SOM\Data\Report\Sales\Minimarket\Alfamart\Sell Out"
dest_dir = source_dir

required_pattern = "detail_performance_by_branch_Selling_Out"
value_keyword = "Value"
qty_keyword = "Qty"

# -------------------------------
# Helper to read Alfamart pipe-delimited files
# -------------------------------
def read_alfamart_file(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    header_idx = None
    for i, line in enumerate(lines):
        if line.startswith("kode_branch|branch_name|tgl|plu|descp|"):
            header_idx = i
            break
    
    if header_idx is None:
        raise ValueError(f"Header not found in {filepath}")
    
    df = pd.read_csv(filepath, sep='|', skiprows=header_idx, encoding='utf-8')
    df.columns = df.columns.str.strip()
    return df

# -------------------------------
# Convert date column to datetime and keep full date (no month extraction yet)
# -------------------------------
def add_date_column(df):
    df['date'] = pd.to_datetime(df['tgl'], format='%d-%b-%y')
    return df   # <-- CHANGED: return only the full date, no year_month

# -------------------------------
# Extract YYYYMM from filename (used for output filename only)
# -------------------------------
def extract_yearmonth_from_filename(filename):
    match = re.search(r'(\d{4}-\d{2}-\d{2})_sd_(\d{4}-\d{2}-\d{2})', filename)
    if match:
        start_date = match.group(1)
        return start_date.replace('-', '')[:6]
    match2 = re.search(r'(\d{4}-\d{2})', filename)
    if match2:
        return match2.group(1).replace('-', '')
    return None

# -------------------------------
# Filter out unwanted rows
# -------------------------------
def filter_data(df):
    # Remove rows where descp contains "PLU K" (case-insensitive)
    df = df[~df['descp'].str.contains('PLU K', case=False, na=False)]
    return df

# -------------------------------
# Find all relevant files and pair them
# -------------------------------
files = os.listdir(source_dir)

value_files = {}
qty_files = {}

for f in files:
    if not f.endswith(".csv"):
        continue
    if required_pattern not in f:
        continue
    base = f.replace(f"_{value_keyword}", "").replace(f"_{qty_keyword}", "")
    if value_keyword in f:
        value_files[base] = f
    elif qty_keyword in f:
        qty_files[base] = f

common_keys = set(value_files.keys()) & set(qty_files.keys())

if not common_keys:
    raise FileNotFoundError("No matching Value/Qty file pairs found.")

print(f"Found {len(common_keys)} file pair(s) to process.")

# -------------------------------
# Process each pair and save as Excel
# -------------------------------
for key in common_keys:
    print(f"\nProcessing: {key}")
    value_file = value_files[key]
    qty_file = qty_files[key]
    
    yearmonth = extract_yearmonth_from_filename(key)
    if not yearmonth:
        yearmonth = extract_yearmonth_from_filename(value_file)
    if not yearmonth:
        yearmonth = pd.Timestamp.now().strftime('%Y%m')
    print(f"  Year-month for output: {yearmonth}")
    
    value_df = read_alfamart_file(os.path.join(source_dir, value_file))
    qty_df = read_alfamart_file(os.path.join(source_dir, qty_file))
    
    # Apply filter BEFORE aggregation (remove PLU K rows)
    value_df = filter_data(value_df)
    qty_df = filter_data(qty_df)
    
    value_df.rename(columns={value_df.columns[-1]: 'value'}, inplace=True)
    qty_df.rename(columns={qty_df.columns[-1]: 'qty'}, inplace=True)
    
    # Add full date column (day preserved)
    value_df = add_date_column(value_df)
    qty_df = add_date_column(qty_df)
    
    # <-- CHANGED: group by branch, product, and full date (not year_month)
    agg_cols = ['branch_name', 'descp', 'date']
    value_agg = value_df.groupby(agg_cols, as_index=False)['value'].sum()
    qty_agg = qty_df.groupby(agg_cols, as_index=False)['qty'].sum()
    
    merged = pd.merge(value_agg, qty_agg, on=agg_cols, how='outer')
    merged['value'] = merged['value'].fillna(0)
    merged['qty'] = merged['qty'].fillna(0)
    
    # Remove rows where value == qty (nonsense)
    merged = merged[merged['value'] != merged['qty']]
    
    # Rename columns
    merged.rename(columns={
        'date': 'Date',          # <-- CHANGED: now full date
        'branch_name': 'Branch',
        'descp': 'Product',
        'value': 'Value IDR',
        'qty': 'Value Qty'
    }, inplace=True)
    
    # Reorder columns: Date (full), Branch, Product, Value IDR, Value Qty
    merged = merged[['Date', 'Branch', 'Product', 'Value IDR', 'Value Qty']]
    merged.sort_values(['Date', 'Branch', 'Product'], inplace=True)   # <-- CHANGED: sort by Date first
    
    output_filename = f"{yearmonth}_Sell Out Alfamart.xlsx"
    output_path = os.path.join(dest_dir, output_filename)
    merged.to_excel(output_path, index=False, sheet_name='Sell Out')
    print(f"  ✅ Saved: {output_path} (rows: {len(merged)})")