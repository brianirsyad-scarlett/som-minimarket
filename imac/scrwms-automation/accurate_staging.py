import io
import os
import pandas as pd
from google.cloud import storage

# --- CONFIGURATION ---
PROJECT_ID = "datawarehouse-490008"
BUCKET_NAME = "bucket_som"

RAW_PREFIX = "raw_som/accurate/staging_accurate/raw_accurate/"
TARGET_PREFIX = "raw_som/accurate/staging_accurate/"


def convert_xlsx_to_csv(file_content):
    """Reads Excel workbook content and converts valid sheets to a combined DataFrame."""
    xls = pd.ExcelFile(io.BytesIO(file_content), engine="openpyxl")

    # Filter out hidden/internal Excel sheets or ranges
    valid_sheets = [s for s in xls.sheet_names if "xlnm" not in s.lower()]

    if not valid_sheets:
        return pd.DataFrame()

    sheet_dfs = [pd.read_excel(xls, sheet_name=s) for s in valid_sheets]
    df = pd.concat(sheet_dfs, ignore_index=True)

    # Format date columns if present
    date_cols = ["CreatedOn", "SentOn"]
    for col in date_cols:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce").dt.strftime(
                "%Y-%m-%d"
            )

    return df


def process_accurate_files():
    client = storage.Client(project=PROJECT_ID)
    bucket = client.bucket(BUCKET_NAME)

    # List existing CSV files in staging for incremental refresh check
    existing_csv_blobs = set(
        blob.name
        for blob in client.list_blobs(BUCKET_NAME, prefix=TARGET_PREFIX)
        if blob.name.endswith(".csv")
    )

    # List all raw Excel files
    raw_blobs = client.list_blobs(BUCKET_NAME, prefix=RAW_PREFIX)

    processed_count = 0
    skipped_count = 0

    print("--- Starting Accurate Staging Conversion ---")

    for blob in raw_blobs:
        file_name = os.path.basename(blob.name)

        # Ignore non-xlsx, temporary, or system lock files
        if (
            "$" in file_name
            or not file_name.endswith(".xlsx")
            or file_name.startswith("~$")
        ):
            continue

        csv_name = os.path.splitext(file_name)[0] + ".csv"
        target_blob_path = f"{TARGET_PREFIX}{csv_name}"

        # Incremental check: Skip if CSV already exists in staging directory
        if target_blob_path in existing_csv_blobs:
            print(f"[SKIP] CSV already exists in staging: {csv_name}")
            skipped_count += 1
            continue

        print(f"[PROCESS] Converting: gs://{BUCKET_NAME}/{blob.name}")
        content = blob.download_as_bytes()

        df_processed = convert_xlsx_to_csv(content)

        if df_processed.empty:
            print(f" -> Warning: No data extracted from {file_name}")
            continue

        # Upload converted CSV to staging folder
        csv_buffer = io.StringIO()
        df_processed.to_csv(csv_buffer, index=False)

        target_blob = bucket.blob(target_blob_path)
        target_blob.chunk_size = 10 * 1024 * 1024
        target_blob.upload_from_string(
            csv_buffer.getvalue(), content_type="text/csv", timeout=600
        )

        print(
            f" -> Success: Written ({len(df_processed):,} rows) to gs://{BUCKET_NAME}/{target_blob_path}"
        )
        processed_count += 1

    print(
        f"\n--- Staging Finished: {processed_count} processed, {skipped_count} skipped ---"
    )


if __name__ == "__main__":
    process_accurate_files()