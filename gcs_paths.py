"""Every GCS location this pipeline reads or writes, in one place."""

BUCKET = "bucket_som"

RAW_PREFIX = "sales_parquet/raw/ecommerce/shopee"

# Which local folder name (matching the existing Shopee Star / Shopee Mall
# CSV convention) each shop's raw files land in when downloaded.
SHOP_FOLDERS = {
    "scarlett_whitening": "Shopee Star",
    "scarlettofficialshop": "Shopee Mall",
}

OUTPUT_BQ_PARQUET = "sales_parquet/Shopee_bq.parquet"
