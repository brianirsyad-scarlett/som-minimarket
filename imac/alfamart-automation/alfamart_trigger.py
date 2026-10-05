#!/usr/bin/env python3
"""
alfamart_trigger.py -- log in and fire every "sell out by branch, by day" and
"sell out by store, by day" export request for the current month-to-date.

Each request only makes the portal email a download link (see README.md for
why); this script does not fetch the files. Run alfamart_fetch_downloads.py
afterwards (or via the daily wrapper script) to pick up the resulting emails.

Usage:
    source ~/.alfamart.env && python3 alfamart_trigger.py
    python3 alfamart_trigger.py --from 2026-09-01 --to 2026-09-11   # backfill
"""

import argparse
import calendar
import datetime as dt
import time

from alfamart_common import login_and_get_np_session, np_post

COMMON = {
    "tipe-area": "DC", "branch": "NAS",
    "tipe-areatext": "BRANCH", "branchtext": "NASIONAL", "branchtitle": "NASIONAL",
}

# Alfamart's sell-out data lands D+2: on the 12th, the 10th is the newest day
# with data. Requesting up to today just returns two empty days and puts a
# misleading date range in the filename.
LAG_DAYS = 2


def latest_data_date(today=None):
    return (today or dt.date.today()) - dt.timedelta(days=LAG_DAYS)


def chunk_for(d):
    """The 1-10 / 11-20 / 21-EOM window containing d, as (start, end) dates.

    The by-store export covering a whole month is ~1.1GB, so it's pulled in
    thirds. The full window is always requested even when only part of it has
    data yet -- that keeps the filename stable for the whole period, so each
    run overwrites one object instead of creating a new one per day.
    """
    if d.day <= 10:
        return d.replace(day=1), d.replace(day=10)
    if d.day <= 20:
        return d.replace(day=11), d.replace(day=20)
    return d.replace(day=21), d.replace(day=calendar.monthrange(d.year, d.month)[1])




def trigger_by_branch(session_cookie, date_from, date_to):
    for unit, unittext in (("v", "Value"), ("q", "Qty")):
        filename = (f"detail_performance_by_branch_Selling_Out_{unittext}"
                    f"_BRANCH_NASIONAL_All_Category_All_Item")
        resp = np_post(session_cookie, "/perfsales/modular/bibdbs/by-branch", {
            "indicator": "a", "unit": unit,
            "periode_awal_bybranch": date_from, "periode_akhir_bybranch": date_to,
            "category": "ALL", "item": "ALL",
            "indicatortext": "Selling Out", "unittext": unittext,
            "categorytext": "All Category", "categorytitle": "All Category", "itemtext": "All Item",
            "tipe_prf": "5", "filename": filename,
            **COMMON,
        })
        print(f"  [by-branch/{unittext}] {resp.strip()[:120]}")


def trigger_by_store(session_cookie, date_from, date_to):
    # category=ALL works here (verified against the live portal), so this is one
    # combined store-level file per unit rather than one per category. Still
    # large -- ~300MB per 10-day chunk -- and pipe-delimited.
    for unit, unittext in (("v", "Value"), ("q", "Qty")):
        # Match the portal's own filename convention, which is what the existing
        # objects in gs://bucket_som/sales_sell out_minimarket/alfamart/ use.
        # The portal appends "_O-0108_<from>_sd_<to>.csv" to whatever stem we send.
        filename = (f"detail_performance_Selling_Out_{unittext}"
                    f"_BRANCH_NASIONAL_All_Store_All_Category_All_Item")
        resp = np_post(session_cookie, "/perfsales/modular/bibdbs/request-download", {
            "indicator": "a", "unit": unit,
            "periode_awal": date_from, "periode_akhir": date_to,
            "store": "ALL", "category": "ALL", "categorytext": "All Category",
            "item": "ALL", "indicatortext": "Selling Out", "unittext": unittext,
            "storetext": "All Store", "itemtext": "All Item",
            "tipe_prf": "4", "filename": filename,
            **COMMON,
        })
        print(f"  [by-store/{unittext}] {resp.strip()[:120]}")
        time.sleep(0.5)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="date_from",
                    help="override by-branch start date, YYYY-MM-DD")
    ap.add_argument("--to", dest="date_to",
                    help="override the latest data date, YYYY-MM-DD "
                         "(default: today minus 2 days)")
    ap.add_argument("--chunk", choices=["1", "2", "3"],
                    help="force a specific by-store chunk of --to's month "
                         "(1=days 1-10, 2=11-20, 3=21-EOM)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print what would be requested without logging in")
    args = ap.parse_args()

    data_to = dt.date.fromisoformat(args.date_to) if args.date_to else latest_data_date()

    # by-branch is small (<1MB) and the monthly summary needs the whole month,
    # so it stays month-to-date. The month comes from the data date, not today,
    # so the first days of a new month still finish off the old one.
    branch_from = args.date_from or data_to.replace(day=1).isoformat()

    if args.chunk:
        anchor = data_to.replace(day={"1": 5, "2": 15, "3": 25}[args.chunk])
        store_windows = [chunk_for(anchor)]
    else:
        # Only the in-progress chunk. Once a chunk's end date has passed the
        # D+2 horizon its data is frozen, so re-pulling it would move ~700MB
        # of identical rows every day. Earlier chunks stay where they already
        # are, in GCS and in the OneDrive folder.
        store_windows = [chunk_for(data_to)]

    print(f"Latest available data date (D+{LAG_DAYS}): {data_to}")

    if args.dry_run:
        print(f"[dry-run] by-branch: {branch_from} .. {data_to}")
        for start, end in store_windows:
            print(f"[dry-run] by-store:  {start} .. {end}  (Value + Qty)")
        print(f"[dry-run] would trigger {2 + 2 * len(store_windows)} export emails.")
        return

    print("Logging in (headless Chrome)...")
    session_cookie = login_and_get_np_session(headless=True)
    print("Logged in, session established.")

    print(f"Triggering sell-out-by-branch exports {branch_from} .. {data_to}")
    trigger_by_branch(session_cookie, branch_from, data_to.isoformat())

    for start, end in store_windows:
        print(f"Triggering sell-out-by-store exports {start} .. {end}")
        trigger_by_store(session_cookie, start.isoformat(), end.isoformat())

    print(f"Done. Triggered {2 + 2 * len(store_windows)} export emails.")
    print(f"TRIGGERED_AT={dt.datetime.now().isoformat()}")


if __name__ == "__main__":
    main()
