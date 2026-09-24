"""Request the Alfamart / Alfamidi B2B sell-out reports - by store and by branch.

The cloud twin of the laptop's `run_pipeline.py --fire-only`: same rolling
periods (period_utils), same one-retry rule, and it EXITS NON-ZERO when any
request set never reached the portal.

    python fire_b2b.py --brand alfamart
    python fire_b2b.py --brand alfamidi

The portal does not return files. It emails a signed download link per
report; Power Automate turns each email into a GitHub issue, and
collect_b2b.py downloads them later.

The portal refuses to re-queue the same export within an hour, answering
"Sudah diajukan dalam 1 jam terakhir". So if the laptop already fired at
09:00, this run is a harmless no-op that doubles as proof the laptop's
requests registered.
"""

import argparse
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE / "sources"))
from period_utils import branch_months, rolling_periods  # noqa: E402

TRIGGERS = {
    "alfamart": HERE / "sources" / "alfamart_b2b_auto.py",
    "alfamidi": HERE / "sources" / "alfamidi_b2b_auto.py",
}
ATTEMPTS = 2
RETRY_SECONDS = 30


def fire(trigger: Path, only: str, start: date, end: date) -> int:
    cmd = [sys.executable, "-u", str(trigger), "--only", only,
           "--start", start.isoformat(), "--end", end.isoformat()]
    rc = 0
    for attempt in range(1, ATTEMPTS + 1):
        print(f"\n=== {only} {start} .. {end}  (attempt {attempt}/{ATTEMPTS}) ===", flush=True)
        rc = subprocess.run(cmd).returncode
        if rc == 0:
            return 0
        if attempt < ATTEMPTS:
            # Retrying is safe: the 1-hour cooldown refuses duplicates.
            print(f"  -> exit {rc}; retrying in {RETRY_SECONDS}s", flush=True)
            time.sleep(RETRY_SECONDS)
    return rc


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--brand", required=True, choices=sorted(TRIGGERS))
    a = ap.parse_args()

    today = date.today()   # the workflow sets TZ=Asia/Jakarta
    trigger = TRIGGERS[a.brand]
    failures = []
    for p in rolling_periods(today, 3):
        if fire(trigger, "by-store", p["start"], p["end"]) != 0:
            failures.append(f"by-store {p['start']}..{p['end']}")
    for m in branch_months(today):
        if fire(trigger, "by-branch", m["start"], m["end"]) != 0:
            failures.append(f"by-branch {m['start']}..{m['end']}")

    if failures:
        print(f"\n=== {a.brand}: FAILED - {len(failures)} request set(s) never reached the portal ===")
        for f in failures:
            print(f"      {f}")
        return 1
    print(f"\n=== {a.brand}: all request sets fired ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
