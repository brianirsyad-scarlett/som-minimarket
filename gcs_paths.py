"""Every GCS location this repo writes, in one place.

DRAFT ONLY. Production minimarket files live under
"sales_sell out_minimarket/<brand>/..." and are owned by the laptop
(Data/Report/Sales/Minimarket/<brand>/Sell Out/3_upload_and_distribute.py).
Nothing here writes there - moving to production is a cutover decision for
the user, not something this code does on its own.
"""

BUCKET = "bucket_som"
ROOT = "sales_parquet/raw/minimarket"

CHAINS = ("alfamart", "alfamidi", "indomaret")
REPORTS = ("market_share", "sell_out_branch", "sell_out_store")


def prefix(chain: str, report: str) -> str:
    if chain not in CHAINS:
        raise ValueError(f"unknown chain {chain!r}")
    if report not in REPORTS:
        raise ValueError(f"unknown report {report!r}")
    return f"{ROOT}/{chain}/{report}/"
