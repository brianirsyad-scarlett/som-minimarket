import os
import re
import glob
import pandas as pd
import openpyxl

# Folder path containing the source files
folder_path = r"D:\SCARLETT_512\SCARLETT-329\SOM\Data\Report\Sales\Minimarket\Indomaret\Market Share"

all_dfs = []

# Search pattern for source Excel files
search_pattern = os.path.join(folder_path, "*.xlsx")

for file_path in glob.glob(search_pattern):
    filename = os.path.basename(file_path)
    
    # Process files containing "data" while ignoring previous output files
    if "data" in filename.lower() and "market share idm" not in filename.lower():
        try:
            # 1. Extract metadata from cell A1
            wb = openpyxl.load_workbook(file_path, data_only=True)
            sheet = wb.active
            a1_value = str(sheet['A1'].value or '')
            
            month_match = re.search(r'month_id is (.+)', a1_value)
            cat_match = re.search(r'cat_nm is (.+)', a1_value)
            
            date_val = None
            month_dt = None
            if month_match:
                raw_date_str = month_match.group(1).strip()
                month_dt = pd.to_datetime(raw_date_str, format='%d %B %Y')
                date_val = month_dt.strftime('%d/%m/%Y')  # DD/MM/YYYY format
            
            cat_val = cat_match.group(1).strip() if cat_match else None
            
            # 2. Read table data starting at row 3 (A3 onwards)
            df = pd.read_excel(file_path, skiprows=2)
            df = df.dropna(how='all')
            
            # 3. Create compiled structure (Renamed 'Total PLU' to 'PLU')
            compiled_df = pd.DataFrame({
                'Date': date_val,
                'Category': cat_val,
                'Brand': df.iloc[:, 0],         # Column A
                'Market Share': df.iloc[:, 3],  # Column D
                'PLU': df.iloc[:, 2],           # Column C
                '_Month_Key': month_dt.strftime('%Y%m') if month_dt else 'Unknown'
            })
            
            all_dfs.append(compiled_df)
            print(f"Successfully processed: {filename}")
            
        except Exception as e:
            print(f"Error processing {filename}: {e}")

# 4. Group by Month and export into separate Excel files
if all_dfs:
    master_df = pd.concat(all_dfs, ignore_index=True)
    
    for month_key, group in master_df.groupby('_Month_Key'):
        # Clean up temporary helper column
        output_df = group.drop(columns=['_Month_Key'])
        
        # Build output filename: e.g., "202608_Market Share IDM.xlsx"
        output_filename = f"{month_key}_Market Share IDM.xlsx"
        output_path = os.path.join(folder_path, output_filename)
        
        output_df.to_excel(output_path, index=False)
        print(f"Exported {len(output_df)} rows to: {output_filename}")
else:
    print("No matching files found or processed.")