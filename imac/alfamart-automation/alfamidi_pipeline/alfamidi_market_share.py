import glob
import os
import re
import pandas as pd

# ============================================================================
# CONFIGURATION
# ============================================================================
folder_path = r"/Users/salesops/Library/CloudStorage/OneDrive-PT.OptoLumbungSejahtera/SOM/Minimarket/Alfamidi/Market Share"

# Column indices to extract (0-based)
BRAND_COL_IDX = 1  # column B
PLU_COL_IDX = 3  # column D
MARKET_SHARE_COL_IDX = 7  # column H


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================
def find_header_row(df_raw):
    """Find the row index where column A contains 'NO'."""
    for i, val in enumerate(df_raw.iloc[:, 0]):
        if pd.notna(val) and str(val).strip().upper() == "NO":
            return i
    return None


def parse_filename_info(filename):
    """Extract Date (as pure Date object) and Category from filename using regex.

    Handles filenames where 'MTD' appears before or after '_Actual_'.
    """
    pattern = r"_Actual_([A-Za-z]{3}-\d{4})_(?:MTD_)?(.+?)_BRANCH"
    match = re.search(pattern, filename, re.IGNORECASE)

    if match:
        date_str = match.group(1)  # Extracts 'Aug-2026'
        raw_category = match.group(2)  # Extracts category string

        # Remove leftover 'MTD_' prefix if present
        category_str = re.sub(
            r"^MTD_", "", raw_category, flags=re.IGNORECASE
        ).strip("_")

        # Convert to pure date object (removes timestamp 00:00:00)
        date_val = pd.to_datetime(
            date_str, format="%b-%Y", errors="coerce"
        ).date()
    else:
        date_val = pd.NaT
        category_str = "Unknown"

    return date_val, category_str


# ============================================================================
# MAIN SCRIPT
# ============================================================================
def main():
    # 1. Check if folder exists
    if not os.path.isdir(folder_path):
        print(f"ERROR: Folder does not exist: {folder_path}")
        return

    # 2. Find all matching Excel files
    pattern = os.path.join(
        folder_path, "*Market Share by Category by Month by Branch_*.xlsx"
    )
    files = glob.glob(pattern)

    if not files:
        pattern_xls = os.path.join(
            folder_path, "*Market Share by Category by Month by Branch_*.xls"
        )
        files = glob.glob(pattern_xls)
        if not files:
            print(f"No matching .xlsx or .xls files found in:\n{folder_path}")
            return
        else:
            print(f"Found {len(files)} .xls file(s).")
    else:
        print(f"Found {len(files)} file(s) to process.\n")

    all_data = []

    for file_path in files:
        filename = os.path.basename(file_path)
        print(f"Processing: {filename}")

        # Extract Date and Category from filename
        date_val, category_val = parse_filename_info(filename)

        try:
            # Read whole sheet without header to locate the "NO" row
            df_raw = pd.read_excel(file_path, sheet_name=0, header=None)
        except Exception as e:
            print(f"  ERROR reading file: {e}")
            continue

        # Find header row
        header_row_idx = find_header_row(df_raw)
        if header_row_idx is None:
            print("  Skipping: could not find header row with 'NO' in column A")
            continue

        # Read again using header row
        try:
            df = pd.read_excel(file_path, sheet_name=0, header=header_row_idx)
        except Exception as e:
            print(f"  ERROR reading file header at row {header_row_idx}: {e}")
            continue

        # Verify minimum column count
        max_needed = max(BRAND_COL_IDX, PLU_COL_IDX, MARKET_SHARE_COL_IDX)
        if df.shape[1] <= max_needed:
            print(
                f"  Skipping: file has only {df.shape[1]} columns (< {max_needed+1})."
            )
            continue

        # Extract target columns
        brand_col = df.iloc[:, BRAND_COL_IDX]
        plu_col = df.iloc[:, PLU_COL_IDX]
        market_share_col = df.iloc[:, MARKET_SHARE_COL_IDX]

        # Build DataFrame with proper structure
        temp_df = pd.DataFrame({
            "Date": date_val,
            "Category": category_val,
            "Brand": brand_col,
            "Market Share": market_share_col,
            "PLU": plu_col,
        })

        # Remove rows with empty/NaN Brand
        temp_df = temp_df.dropna(subset=["Brand"])
        temp_df = temp_df[temp_df["Brand"].astype(str).str.strip() != ""]

        print(f"  Extracted {len(temp_df)} rows")
        all_data.append(temp_df)

    # 3. Combine and save results
    if not all_data:
        print("\nNo data extracted from any file. Summary.xlsx not created.")
        return

    final_df = pd.concat(all_data, ignore_index=True)

    # Reorder columns explicitly
    final_df = final_df[
        ["Date", "Category", "Brand", "Market Share", "PLU"]
    ]

    output_path = os.path.join(folder_path, "Summary.xlsx")

    # Save to Excel formatted as 'mmm-yyyy'
    with pd.ExcelWriter(
        output_path, engine="openpyxl", date_format="mmm-yyyy"
    ) as writer:
        final_df.to_excel(writer, index=False)

    print(f"\nSUCCESS: Summary saved to:\n{output_path}")
    print(f"Total rows: {len(final_df)}")


if __name__ == "__main__":
    main()