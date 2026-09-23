"""
Master orchestrator for the Alfamidi / Alfamart B2B sell-out pipeline.

Implements the user's rule:
  - Daily-STORE (by-store-by-category) reports are pulled in 10-day periods
    (1-10, 11-20, 21-end). Each run fetches the *current* period first, then
    walks backward 2 more periods (crossing month boundaries as needed).
    Only the current/latest period is pushed to GCS; older periods are
    archived to OneDrive (iMac Sales Ops) only, to keep GCS storage lean.
  - Daily-BRANCH (aggregate) reports keep using the full month-to-date range,
    and are (re)generated for 2 months each run: the current month and the
    full previous month (so late corrections to last month get picked up).
    By-branch summaries are always uploaded to GCS in full (no restriction).

Modes:
    --mode daily              3 rolling store periods + 2 branch months
    --mode backfill --backfill-start 2026-08-01
                               every store period from that date through
                               today, + 2 branch months

Usage:
    python run_pipeline.py --brand alfamidi --mode daily
    python run_pipeline.py --brand alfamart --mode backfill --backfill-start 2026-08-01
"""

import argparse
import re
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from period_utils import rolling_periods, all_periods_since, branch_months  # noqa: E402

SCRIPT_DIR = Path(__file__).parent

BRAND_PATHS = {
    "alfamidi": {
        "trigger": SCRIPT_DIR / "alfamidi_b2b_auto.py",
        "root_dir": Path(r"D:\SCARLETT_512\SCARLETT-329\SOM\Data\Report\Sales\Minimarket\Alfamidi"),
    },
    "alfamart": {
        "trigger": SCRIPT_DIR / "alfamart_b2b_auto.py",
        "root_dir": Path(r"D:\SCARLETT_512\SCARLETT-329\SOM\Data\Report\Sales\Minimarket\Alfamart"),
    },
}
# Sell Out       -> by-branch daily raw files + the monthly summary (built by
#                   1_summary_sell_out.py / 2_csv_converter.py, which live there)
# Daily Sell Out -> by-store-by-category daily detail files (archive only, no
#                   further local processing script touches them)

IMAP_SCRIPT = SCRIPT_DIR / "imap_b2b_download.py"

WAIT_ROUNDS = 8            # how many times to poll for emails
WAIT_SECONDS = 15 * 60     # 15 min between polls


def log(msg):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def run(cmd, **kw):
    log("$ " + " ".join(str(c) for c in cmd))
    r = subprocess.run(cmd, text=True, **kw)
    if r.returncode != 0:
        log(f"  -> exit code {r.returncode} (continuing)")
    return r.returncode


def run_capture(cmd):
    """Like run(), but also returns combined stdout so callers can inspect it."""
    log("$ " + " ".join(str(c) for c in cmd))
    r = subprocess.run(cmd, text=True, capture_output=True)
    if r.stdout:
        print(r.stdout, end="" if r.stdout.endswith("\n") else "\n")
    if r.stderr:
        print(r.stderr, end="" if r.stderr.endswith("\n") else "\n")
    if r.returncode != 0:
        log(f"  -> exit code {r.returncode} (continuing)")
    return r.stdout, r.returncode


DOWNLOADED_RE = re.compile(r"---\s*Done:\s*(\d+)\s*file\(s\)\s*downloaded")


# Every failed fire, so the process can exit non-zero at the end. Before this,
# run()'s "(continuing)" return value was discarded by both callers and the
# pipeline always exited 0: on 2026-09-21 a DNS outage took out ALL requests for
# both brands and Task Scheduler still recorded the task as rc=0 / success.
fire_failures: list[str] = []


FIRE_ATTEMPTS = 2
FIRE_RETRY_SECONDS = 30


def _fire(label, cmd):
    """Fire one request set, retrying once through a transient portal failure.

    Retrying is safe because of the portal's 1-hour cooldown: anything the first
    attempt did get through comes back refused ("Sudah diajukan dalam 1 jam
    terakhir") rather than queued twice.

    Both transients seen so far cost a whole request set and both cleared within
    seconds: a "DB ERROR" where the SSO token should have been (2026-09-21, the
    very next request logged in fine), and DNS failures for b2b.alfamart.co.id /
    b2b.alfamidiku.com. With only one scheduled fire a day there is no later
    pass to absorb these, so it is worth one more try here.
    """
    rc = 0
    for attempt in range(1, FIRE_ATTEMPTS + 1):
        rc = run(cmd)
        if rc == 0:
            return 0
        if attempt < FIRE_ATTEMPTS:
            log(f"  -> {label} failed (exit {rc}); retrying in {FIRE_RETRY_SECONDS}s "
                f"(attempt {attempt + 1} of {FIRE_ATTEMPTS})")
            time.sleep(FIRE_RETRY_SECONDS)
    fire_failures.append(f"{label} (exit {rc})")
    return rc


def fire_store_period(trigger, period):
    _fire(f"by-store {period['start']}..{period['end']}",
          [sys.executable, str(trigger), "--only", "by-store",
           "--start", period["start"].isoformat(), "--end", period["end"].isoformat()])


def fire_branch_month(trigger, month):
    _fire(f"by-branch {month['start']}..{month['end']}",
          [sys.executable, str(trigger), "--only", "by-branch",
           "--start", month["start"].isoformat(), "--end", month["end"].isoformat()])


def download_and_process(brand, latest_period):
    paths = BRAND_PATHS[brand]
    root_dir = paths["root_dir"]
    sell_out_dir = root_dir / "Sell Out"

    log(f"--- IMAP download ({brand}) ---")
    # 2 days, not 1. IMAP SINCE is date-granular, so "1" means "since yesterday's
    # date" - an email that arrives late at night is out of scope by the next
    # morning's poll. Widening costs nothing (already-downloaded files are
    # skipped by filename) and the links live ~24h anyway, so anything older is
    # dead regardless.
    stdout, _ = run_capture([sys.executable, str(IMAP_SCRIPT), "--brand", brand,
                              "--dest", str(root_dir), "--since-days", "2"])

    m = DOWNLOADED_RE.search(stdout or "")
    downloaded = int(m.group(1)) if m else 0
    if downloaded == 0:
        log(f"No new files downloaded for {brand} this poll; "
            f"skipping convert/summarize/distribute (nothing changed to re-upload).")
        return

    converter = sell_out_dir / "2_csv_converter.py"
    summarizer = sell_out_dir / "1_summary_sell_out.py"
    distributor = sell_out_dir / "3_upload_and_distribute.py"

    if converter.exists():
        run([sys.executable, str(converter)])
    if summarizer.exists():
        run([sys.executable, str(summarizer)])
    if distributor.exists():
        cmd = [sys.executable, str(distributor)]
        if latest_period:
            cmd += ["--latest-start", latest_period["start"].isoformat(),
                    "--latest-end", latest_period["end"].isoformat()]
        run(cmd)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--brand", choices=list(BRAND_PATHS), required=True)
    ap.add_argument("--mode", choices=["daily", "backfill"], required=True)
    ap.add_argument("--backfill-start", help="YYYY-MM-DD, required for --mode backfill")
    ap.add_argument("--skip-wait", action="store_true",
                    help="Fire requests and download once immediately, no polling wait")
    ap.add_argument("--fire-only", action="store_true",
                    help="Only fire the report requests, then exit. Pair with a separate "
                         "--collect-only task instead of holding a process open polling.")
    ap.add_argument("--collect-only", action="store_true",
                    help="Skip firing; do a single mailbox poll + process, then exit. "
                         "Meant to be run repeatedly by Task Scheduler.")
    args = ap.parse_args()

    if args.fire_only and args.collect_only:
        raise SystemExit("--fire-only and --collect-only are mutually exclusive")

    today = date.today()
    trigger = BRAND_PATHS[args.brand]["trigger"]

    if args.mode == "daily":
        store_periods = rolling_periods(today, 3)
    else:
        if not args.backfill_start:
            raise SystemExit("--mode backfill requires --backfill-start YYYY-MM-DD")
        store_periods = all_periods_since(date.fromisoformat(args.backfill_start), today)

    months = branch_months(today)

    log(f"=== {args.brand} / {args.mode} ===")
    log("Store periods (most recent first):")
    for p in store_periods:
        tag = "LATEST -> GCS" if p["is_latest"] else "archive -> OneDrive only"
        log(f"  {p['start']} .. {p['end']}  ({tag})")
    log("Branch months:")
    for m in months:
        log(f"  {m['start']} .. {m['end']}  ({m['label']})")

    latest_period = store_periods[0] if store_periods else None

    if not args.collect_only:
        log("\n--- Firing store-period requests (most recent first) ---")
        for p in store_periods:
            fire_store_period(trigger, p)

        log("\n--- Firing branch-month requests ---")
        for m in months:
            fire_branch_month(trigger, m)

    if args.fire_only:
        if fire_failures:
            log(f"=== {args.brand} / fire-only FAILED - {len(fire_failures)} request set(s) "
                f"never reached the portal ===")
            for f in fire_failures:
                log(f"      {f}")
            # Exit non-zero so Task Scheduler and status.ps1 both see it. A fire
            # that never reached the portal means no email will ever arrive, so
            # the collector will sit there finding nothing - this is the only
            # point where the failure is still visible.
            sys.exit(1)
        log(f"=== {args.brand} / fire-only complete - the collector task picks up the emails ===")
        return

    if args.collect_only or args.skip_wait:
        download_and_process(args.brand, latest_period)
        return

    log(f"\n--- Waiting for export emails (up to {WAIT_ROUNDS * WAIT_SECONDS // 60} min total) ---")
    for i in range(WAIT_ROUNDS):
        log(f"Wait round {i + 1}/{WAIT_ROUNDS}: sleeping {WAIT_SECONDS // 60} min...")
        time.sleep(WAIT_SECONDS)
        download_and_process(args.brand, latest_period)

    log(f"=== {args.brand} / {args.mode} complete ===")


if __name__ == "__main__":
    main()
