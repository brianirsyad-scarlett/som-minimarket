"""
Decide whether the Sell In -> PCC chain should run now.

The three upstream pipelines (Odoo, Anchanto, Accurate) run in their own repos, and
GitHub starts scheduled runs late - sometimes hours late - so a fixed start time
cannot guarantee they are done. Instead this workflow is scheduled several times a
night and runs only when:

  * all three inputs in GCS were updated today (Asia/Jakarta), and
  * today's Sell In output is older than the latest Anchanto / Accurate update
    (Odoo refreshes every 3 hours, so it is required fresh but does not re-trigger).

Writes run=true|false and reason=... to $GITHUB_OUTPUT. With --final (the last
scheduled slot of the night), inputs still missing is an error, so it gets emailed.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from zoneinfo import ZoneInfo

from google.cloud import storage

import gcs_paths

WIB = ZoneInfo("Asia/Jakarta")
UPSTREAM = {
    "Odoo": "sales_parquet/raw/primary/odoo/Odoo Report.xlsx",
    "Anchanto": "sales_parquet/raw/primary/anchanto/Anchanto.parquet",
    "Accurate": "sales_parquet/raw/primary/accurate/0. 2025 Accurate.xlsx",
}
TRIGGERS = ("Anchanto", "Accurate")


def updated(bucket, key):
    b = bucket.get_blob(key)
    return b.updated.astimezone(WIB) if b else None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--final", action="store_true")
    args = ap.parse_args(argv)

    bucket = storage.Client().bucket(gcs_paths.BUCKET)
    today = dt.datetime.now(WIB).date()
    times = {name: updated(bucket, key) for name, key in UPSTREAM.items()}
    out_time = updated(bucket, gcs_paths.OUT_COMBINED)
    for name, t in times.items():
        print(f"{name:9s} {t:%Y-%m-%d %H:%M} WIB" if t else f"{name:9s} MISSING")
    print(f"Sell In   {out_time:%Y-%m-%d %H:%M} WIB" if out_time else "Sell In   (never built)")

    stale = [n for n, t in times.items() if t is None or t.date() < today]
    if args.force:
        run, reason = True, "forced"
    elif stale:
        run, reason = False, f"waiting for today's {', '.join(stale)}"
    elif out_time and out_time > max(times[n] for n in TRIGGERS):
        run, reason = False, "already built after today's Anchanto and Accurate"
    else:
        run, reason = True, "all inputs fresh"
    print(f"run={run} ({reason})")

    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a", encoding="utf-8") as fh:
            fh.write(f"run={'true' if run else 'false'}\nreason={reason}\n")
    if args.final and not run and stale:
        print(f"Last slot of the night and still {reason}.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
