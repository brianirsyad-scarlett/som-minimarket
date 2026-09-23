"""
ONE-TIME backfill runner: both brands, from August 1st through today.

Runs `run_pipeline.py --mode backfill --backfill-start 2026-08-01` for
alfamidi and alfamart concurrently (each covers every 10-day store period
since Aug 1 plus the current+previous branch months), waiting for emails
and distributing per the latest-period-only-to-GCS rule.

Scheduled once via Task Scheduler for 2026-09-12 18:00 Jakarta time.
"""

import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
PIPELINE = SCRIPT_DIR / "run_pipeline.py"
BACKFILL_START = "2026-08-01"


def main():
    procs = []
    for brand in ("alfamidi", "alfamart"):
        log_path = SCRIPT_DIR / f"_backfill_{brand}.log"
        log_fh = open(log_path, "w", encoding="utf-8")
        p = subprocess.Popen(
            [sys.executable, str(PIPELINE), "--brand", brand,
             "--mode", "backfill", "--backfill-start", BACKFILL_START],
            stdout=log_fh, stderr=subprocess.STDOUT, text=True,
        )
        procs.append((brand, p, log_fh))
        print(f"Started {brand} backfill (pid {p.pid}), logging to {log_path}")

    exit_code = 0
    for brand, p, log_fh in procs:
        rc = p.wait()
        log_fh.close()
        print(f"{brand} backfill finished with exit code {rc}")
        if rc != 0:
            exit_code = 1

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
