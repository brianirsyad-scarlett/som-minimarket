"""Build Shopee_bq.parquet from the CSV files downloaded by download_raw.py.

Cleaning logic ported verbatim from the Mac pipeline's
shopee_parquet_converters.py (convert_shopee_bq / _build_combined) so this
produces byte-for-byte-equivalent rows given the same input CSVs. The
OneDrive-specific retry/atomic-write machinery in that script isn't needed
here (GitHub Actions' disk is local and ephemeral), so it's left out.
"""

import glob
import os
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
INPUTS_DIR = HERE / "work" / "inputs"
STAR_FOLDER = INPUTS_DIR / "Shopee Star"
MALL_FOLDER = INPUTS_DIR / "Shopee Mall"
OUTPUT_PARQUET_BQ = HERE / "work" / "Shopee_bq.parquet"

# Abort rather than silently publish a parquet missing more than this many
# CSVs (e.g. a partial/broken GCS listing) - mirrors the local safety check.
MAX_TOLERABLE_SKIPPED = 5

DESIRED_COLUMNS = [
    "No. Pesanan", "Status Pesanan", "Alasan Pembatalan", "Status Pembatalan/ Pengembalian",
    "No. Resi", "Opsi Pengiriman", "Antar ke counter/ pick-up",
    "Pesanan Harus Dikirimkan Sebelum (Menghindari keterlambatan)", "Waktu Pengiriman Diatur",
    "Waktu Pesanan Dibuat", "Waktu Pembayaran Dilakukan", "Metode Pembayaran",
    "SKU Induk", "Nama Produk", "Nomor Referensi SKU", "Nama Variasi",
    "Harga Awal", "Harga Setelah Diskon", "Jumlah", "Returned quantity",
    "Dibayar Pembeli", "Total Diskon", "Diskon Dari Penjual", "Diskon Dari Shopee",
    "Berat Produk", "Jumlah Produk di Pesan", "Total Berat", "Nama Gudang",
    "Voucher Ditanggung Penjual", "Cashback Koin", "Voucher Ditanggung Shopee",
    "Paket Diskon", "Paket Diskon (Diskon dari Shopee)", "Paket Diskon (Diskon dari Penjual)",
    "Potongan Koin Shopee", "Diskon Kartu Kredit", "Ongkos Kirim Dibayar oleh Pembeli",
    "Estimasi Potongan Biaya Pengiriman", "Ongkos Kirim Pengembalian Barang",
    "Total Pembayaran", "Perkiraan Ongkos Kirim", "Catatan dari Pembeli", "Catatan",
    "Username (Pembeli)", "Nama Penerima", "No. Telepon", "Alamat Pengiriman",
    "Kota/Kabupaten", "Provinsi", "Waktu Pesanan Selesai",
    "Marketplace"
]

DATE_COLUMNS = [
    "Waktu Pesanan Selesai", "Waktu Pesanan Dibuat", "Waktu Pembayaran Dilakukan",
    "Waktu Pengiriman Diatur", "Pesanan Harus Dikirimkan Sebelum (Menghindari keterlambatan)"
]

NUMERIC_COLUMNS = [
    "Perkiraan Ongkos Kirim", "Total Pembayaran", "Ongkos Kirim Pengembalian Barang",
    "Estimasi Potongan Biaya Pengiriman", "Ongkos Kirim Dibayar oleh Pembeli",
    "Diskon Kartu Kredit", "Potongan Koin Shopee", "Paket Diskon (Diskon dari Penjual)",
    "Paket Diskon (Diskon dari Shopee)", "Voucher Ditanggung Shopee", "Cashback Koin",
    "Voucher Ditanggung Penjual", "Jumlah Produk di Pesan", "Diskon Dari Shopee",
    "Diskon Dari Penjual", "Total Diskon", "Dibayar Pembeli", "Returned quantity",
    "Jumlah", "Harga Setelah Diskon", "Harga Awal"
]

SKIPPED_FILES = []


def clean_column(name):
    """Replace any character that is not a letter, digit, or underscore with '_'."""
    return re.sub(r'[^a-zA-Z0-9_]', '_', name)


def process_shopee_folder(folder_path, marketplace_name, add_missing_columns=None):
    csv_files = glob.glob(os.path.join(folder_path, "*.csv"))
    csv_files = [f for f in csv_files if "$" not in os.path.basename(f)]
    if not csv_files:
        print(f"  No CSV files found in {folder_path}")
        return pd.DataFrame()

    print(f"  Found {len(csv_files)} CSV files in {os.path.basename(folder_path)}")

    all_dfs = []
    for file in csv_files:
        try:
            df = pd.read_csv(file, encoding='utf-8', dtype=str, low_memory=False)
        except (TimeoutError, OSError) as e:
            print(f"    WARNING: skipping unreadable file: {os.path.basename(file)} ({e})")
            SKIPPED_FILES.append(os.path.basename(file))
            continue

        existing_desired = [col for col in DESIRED_COLUMNS if col in df.columns and col != "Marketplace"]
        df = df[existing_desired]

        if "No. Pesanan" in df.columns:
            df = df[df["No. Pesanan"] != "No. Pesanan"]

        for col in DESIRED_COLUMNS:
            if col not in df.columns and col != "Marketplace":
                default = add_missing_columns.get(col, "") if add_missing_columns else ""
                df[col] = default

        for col in DATE_COLUMNS:
            if col in df.columns:
                df[col] = df[col].replace("-", np.nan)
                df[col] = pd.to_datetime(df[col], errors='coerce').dt.date

        for col in NUMERIC_COLUMNS:
            if col in df.columns:
                df[col] = df[col].astype(str).str.replace('.', '', regex=False)
                df[col] = df[col].replace("", "0").replace("nan", "0")
                df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0).astype('int64')

        text_cols = [c for c in df.columns if c not in DATE_COLUMNS and c not in NUMERIC_COLUMNS]
        for col in text_cols:
            df[col] = df[col].fillna("").astype(str)

        df["Marketplace"] = marketplace_name
        all_dfs.append(df)

    if not all_dfs:
        return pd.DataFrame()
    return pd.concat(all_dfs, ignore_index=True)


def build_combined():
    SKIPPED_FILES.clear()
    print("\nReading Shopee Mall...")
    mall_df = process_shopee_folder(str(MALL_FOLDER), marketplace_name="Shopee Mall")
    print(f"  Mall rows after cleaning: {len(mall_df):,}")

    print("\nReading Shopee Star...")
    star_missing = {"Nama Gudang": ""}
    star_df = process_shopee_folder(str(STAR_FOLDER), marketplace_name="Shopee Star", add_missing_columns=star_missing)
    print(f"  Star rows after cleaning: {len(star_df):,}")

    print("\nAppending Mall and Star...")
    combined = pd.concat([mall_df, star_df], ignore_index=True)
    print(f"  Total rows: {len(combined):,}")

    for col in NUMERIC_COLUMNS:
        if col in combined.columns:
            combined[col] = pd.to_numeric(combined[col], errors='coerce').fillna(0).astype('int64')

    return combined


def main() -> int:
    print("Building Shopee_bq.parquet from GCS raw CSVs...")
    combined = build_combined()

    if len(SKIPPED_FILES) > MAX_TOLERABLE_SKIPPED:
        raise RuntimeError(
            f"Refusing to write Shopee_bq.parquet: {len(SKIPPED_FILES)} CSV file(s) could not "
            f"be read, more than the {MAX_TOLERABLE_SKIPPED} straggler(s) this is willing to "
            f"silently drop. Skipped: {SKIPPED_FILES[:10]}{'...' if len(SKIPPED_FILES) > 10 else ''}"
        )

    final_cols = [c for c in DESIRED_COLUMNS if c in combined.columns]
    combined = combined[final_cols]

    print("\nRenaming columns for BigQuery compatibility...")
    combined = combined.rename(columns={col: clean_column(col) for col in combined.columns})

    OUTPUT_PARQUET_BQ.parent.mkdir(parents=True, exist_ok=True)
    print(f"\nSaving to Parquet: {OUTPUT_PARQUET_BQ}")
    combined.to_parquet(OUTPUT_PARQUET_BQ, index=False, engine="pyarrow", compression="zstd", coerce_timestamps="us")

    size_gb = os.path.getsize(OUTPUT_PARQUET_BQ) / (1024 ** 3)
    print(f"   SUCCESS: {len(combined):,} rows, {size_gb:.2f} GB, {len(combined.columns)} columns")

    if SKIPPED_FILES:
        print(f"\nWARNING: {len(SKIPPED_FILES)} file(s) could not be read and were excluded:")
        for name in SKIPPED_FILES:
            print(f"   - {name}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
