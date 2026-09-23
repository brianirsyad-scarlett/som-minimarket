import pandas as pd
import os

input_folder = r"D:\SCARLETT_512\SCARLETT-329\SOM\Data\Report\Sales\Minimarket\Alfamart\Sell Out"

for file in os.listdir(input_folder):
    if file.endswith(".xlsx"):
        xlsx_path = os.path.join(input_folder, file)
        excel_file = pd.ExcelFile(xlsx_path)
        for sheet_name in excel_file.sheet_names:
            df = pd.read_excel(xlsx_path, sheet_name=sheet_name)
            csv_filename = f"{file.replace('.xlsx', '')}_{sheet_name}.csv"
            csv_path = os.path.join(input_folder, csv_filename)
            df.to_csv(csv_path, index=False, encoding='utf-8-sig')
            print(f"Converted: {file} / sheet '{sheet_name}' -> {csv_filename}")