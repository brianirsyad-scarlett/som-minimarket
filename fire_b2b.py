"""Request the Alfamart / Alfamidi B2B sell-out reports - by store and by branch.

Same rolling periods as the laptop (period_utils): 3 by-store 10-day periods
and the current + previous month by branch. Each request set is retried once,
and the run EXITS NON-ZERO when any set never reached the portal.

    python fire_b2b.py --brand alfamart
    python fire_b2b.py --brand alfamidi --verify   # ~10 min later, inside the cooldown

The portal does not return files. It emails a signed download link per report;
Power Automate turns each email into a GitHub issue, and collect_b2b.py
downloads them.

--verify re-runs exactly the same requests. The portal refuses to re-queue an
export within an hour, so each reply says what the first fire achieved:

    "Sudah diajukan dalam 1 jam terakhir"  -> the fire registered it   (confirmed)
    "Akan dikirim ... email"               -> the fire MISSED it, and
                                              this verify just queued it (healed)
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
counts = {"confirmed": 0, "queued": 0}


def fire(trigger: Path, only: str, start: date, end: date) -> int:
    cmd = [sys.executable, "-u", str(trigger), "--only", only,
           "--start", start.isoformat(), "--end", end.isoformat()]
    rc = 0
    for attempt in range(1, ATTEMPTS + 1):
        print(f"\n=== {only} {start} .. {end}  (attempt {attempt}/{ATTEMPTS}) ===", flush=True)
        r = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
        sys.stdout.write(r.stdout)
        sys.stdout.write(r.stderr)
        sys.stdout.flush()
        rc = r.returncode
        for line in r.stdout.lower().splitlines():
            if "portal:" in line:
                if COOLDOWN_MARK in line:
                    counts["confirmed"] += 1
                elif QUEUED_MARK in line:
                    counts["queued"] += 1
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
    ap.add_argument("--verify", action="store_true",
                    help="second pass inside the cooldown: report what the first fire registered")
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

    label = "verify" if a.verify else "fire"
    summary = f"{a.brand} {label}: {counts['confirmed']} already registered, {counts['queued']} newly queued"
    if a.verify:
        if counts["queued"]:
            # The first fire missed these. They are queued now (self-healed),
            # but it should be visible, not buried in a log.
            print(f"::warning::{a.brand}: the fire missed {counts['queued']} request(s); verify queued them")
            summary += " - the fire MISSED these, verify healed them"
        else:
            summary += " - the fire registered everything"
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as fh:
            fh.write(f"- {summary}\n")
    print(f"\n=== {summary} ===")

    if failures:
        print(f"=== {a.brand} {label}: FAILED - {len(failures)} request set(s) never reached the portal ===")
        for f in failures:
            print(f"      {f}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
