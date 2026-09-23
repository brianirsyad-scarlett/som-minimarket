"""
RECURRING daily runner: both brands, standing rule
(3 rolling 10-day store periods, 2 branch months, latest store period -> GCS,
older periods -> OneDrive only).

Runs `run_pipeline.py --mode daily` for alfamidi and alfamart concurrently.

Three ways to run it:
    (no flag)       fire requests, then poll the mailbox for up to 2 hours.
                    The original single-task behaviour.
    --fire-only     fire the requests and exit (~4 min).
    --collect-only  one mailbox poll + process, then exit (seconds).

The split exists because the polling loop has no early exit: on 2026-09-20 every
file had arrived by 07:56 but the process kept polling an empty mailbox until
09:20 - about 90 wasted minutes per brand, with a console window held open the
whole time. Task Scheduler does the waiting for free instead: fire once at 07:00,
then collect on a repeating trigger.
"""

import argparse
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
PIPELINE = SCRIPT_DIR / "run_pipeline.py"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--fire-only", action="store_true",
                   help="Fire the report requests and exit")
    g.add_argument("--collect-only", action="store_true",
                   help="Single mailbox poll + process, then exit")
    g.add_argument("--verify", action="store_true",
                   help="Second fire pass, run INSIDE the portal's 1-hour cooldown. "
                        "Requests the portal already has come back 'Sudah diajukan dalam "
                        "1 jam terakhir' (proof the first pass registered); anything the "
                        "first pass missed says 'akan dikirim melalui email' and gets "
                        "queued for real. Logs separately so the first pass is preserved.")
    ap.add_argument("--brands", nargs="+", choices=["alfamidi", "alfamart"],
                    default=["alfamidi", "alfamart"], metavar="BRAND",
                    help="Which brands to run. Defaults to both, which is what the "
                         "scheduled 07:00 pass uses. Handy for re-running one brand by "
                         "hand after a failure without disturbing the other.")
    args = ap.parse_args()

    if args.fire_only:
        extra, phase = ["--fire-only"], "fire"
    elif args.verify:
        extra, phase = ["--fire-only"], "verify"
    elif args.collect_only:
        extra, phase = ["--collect-only"], "collect"
    else:
        extra, phase = [], "daily"

    print(f"Phase '{phase}' for: {', '.join(args.brands)}")

    procs = []
    for brand in args.brands:
        log_path = SCRIPT_DIR / f"_{phase}_{brand}.log"
        # collect runs repeatedly - append so one morning's polls stay in one file
        log_fh = open(log_path, "a" if phase == "collect" else "w", encoding="utf-8")
        p = subprocess.Popen(
            [sys.executable, str(PIPELINE), "--brand", brand, "--mode", "daily", *extra],
            stdout=log_fh, stderr=subprocess.STDOUT, text=True,
        )
        procs.append((brand, p, log_fh))
        print(f"Started {brand} {phase} run (pid {p.pid}), logging to {log_path}")

    exit_code = 0
    for brand, p, log_fh in procs:
        rc = p.wait()
        log_fh.close()
        print(f"{brand} {phase} run finished with exit code {rc}")
        if rc != 0:
            exit_code = 1

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
