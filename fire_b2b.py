"""Request the Alfamart / Alfamidi B2B reports - sell out by store and by
branch, and STOCK by branch.

Same rolling periods as the laptop (period_utils): 3 by-store 10-day periods,
and the current + previous month by branch (sell out and stock alike). Each request set is retried once,
and the run EXITS NON-ZERO when any set never reached the portal.

    python fire_b2b.py --brand alfamart
    python fire_b2b.py --brand alfamidi

Every request is clicked TWICE, back to back (the trigger scripts' --confirm).
The portal refuses to re-queue an export within an hour, so the second reply
says what the first click achieved - no separate verify pass, no waiting:

    "Sudah diajukan dalam 1 jam terakhir"  -> the first click registered   (confirmed)
    "Akan dikirim ... email"               -> the first click did NOT; the
                                              second one queued it       (healed)

The portal does not return files. It emails a signed download link per report;
Power Automate turns each email into a GitHub issue, and collect_b2b.py
downloads them.
"""

import argparse
import os
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
COOLDOWN_MARK = "sudah diajukan"   # already requested within the hour
QUEUED_MARK = "akan dikirim"       # freshly queued: the link will be emailed
counts = {"confirmed": 0, "healed": 0, "unconfirmed": 0}


def fire(trigger: Path, only: str, start: date, end: date, stock: bool = False) -> int:
    cmd = [sys.executable, "-u", str(trigger), "--only", only,
           "--start", start.isoformat(), "--end", end.isoformat(), "--confirm"]
    if stock:
        cmd.append("--stock")
    label = f"{only} stock" if stock else only
    rc = 0
    for attempt in range(1, ATTEMPTS + 1):
        print(f"\n=== {label} {start} .. {end}  (attempt {attempt}/{ATTEMPTS}) ===", flush=True)
        r = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
        sys.stdout.write(r.stdout)
        sys.stdout.write(r.stderr)
        sys.stdout.flush()
        rc = r.returncode
        for line in r.stdout.lower().splitlines():
            if "confirm:" in line:
                if COOLDOWN_MARK in line:
                    counts["confirmed"] += 1
                elif QUEUED_MARK in line:
                    counts["healed"] += 1
                else:
                    counts["unconfirmed"] += 1
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
    for m in branch_months(today):
        if fire(trigger, "by-branch", m["start"], m["end"], stock=True) != 0:
            failures.append(f"stock by-branch {m['start']}..{m['end']}")

    c = counts
    summary = (f"{a.brand}: {c['confirmed']} confirmed by the 2nd click, "
               f"{c['healed']} missed by the 1st click and queued by the 2nd, "
               f"{c['unconfirmed']} with no clear 2nd reply")
    if c["healed"]:
        print(f"::warning::{a.brand}: {c['healed']} request(s) did not register on the first click "
              f"(the second click queued them)")
    if c["unconfirmed"]:
        print(f"::warning::{a.brand}: {c['unconfirmed']} request(s) got no recognisable second reply")
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as fh:
            fh.write(f"- {summary}\n")
    print(f"\n=== {summary} ===")

    if failures:
        print(f"=== {a.brand}: FAILED - {len(failures)} request set(s) never reached the portal ===")
        for f in failures:
            print(f"      {f}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
