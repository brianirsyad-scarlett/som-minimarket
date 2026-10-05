"""
PLACEHOLDER -- not the real module.

alfamidi_upload_and_distribute.py imports period_index()/period_bounds() from
this file (10-day-period boundary logic: periods start on the 1st/11th/21st
of each month). The real period_utils.py lives on salesops-512 at:

    D:\SCARLETT_512\SCARLETT-329\SOM\Data\Sent Email\period_utils.py

Copy that real file over this one (same filename, same folder) before
relying on the daily Alfamidi automation -- until then, this stub fails
loudly instead of silently computing wrong period boundaries.
"""


def period_index(day: int) -> int:
    raise NotImplementedError(
        "period_utils.py is a placeholder. Copy the real period_utils.py "
        "from D:\\SCARLETT_512\\SCARLETT-329\\SOM\\Data\\Sent Email on "
        "salesops-512 into this folder (alfamidi_pipeline/), replacing this file."
    )


def period_bounds(year: int, month: int, idx: int):
    raise NotImplementedError(
        "period_utils.py is a placeholder. Copy the real period_utils.py "
        "from D:\\SCARLETT_512\\SCARLETT-329\\SOM\\Data\\Sent Email on "
        "salesops-512 into this folder (alfamidi_pipeline/), replacing this file."
    )
