"""Sync the local "Sent Email" folder to OneDrive using robocopy (Windows only).

Usage:
    python sync_sent_email.py              # run forever, sync every 60 seconds
    python sync_sent_email.py --once       # single sync, then exit
    python sync_sent_email.py --mirror     # also delete files in OneDrive that were deleted locally
    python sync_sent_email.py --interval 30
"""
import argparse
import subprocess
import sys
import time
from datetime import datetime

SOURCE = r"D:\SCARLETT_512\SCARLETT-329\SOM\Data\Sent Email"
DEST = r"D:\OneDrive - PT. Opto Lumbung Sejahtera\SOM\Sent Email"


def sync(mirror: bool) -> int:
    cmd = [
        "robocopy", SOURCE, DEST,
        "/MIR" if mirror else "/E",   # /MIR also removes deleted files from DEST
        "/XO",                        # skip files older than the copy in DEST
        "/R:2", "/W:5",               # retry twice, wait 5s (file may be locked)
        "/NP", "/NJH", "/NJS",        # quiet output
    ]
    code = subprocess.run(cmd).returncode
    # robocopy exit codes 0-7 are success (bit flags), 8+ means failure
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{stamp}] robocopy exit code {code} ({'OK' if code < 8 else 'ERROR'})", flush=True)
    return code


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--once", action="store_true", help="sync once and exit")
    p.add_argument("--mirror", action="store_true", help="mirror deletions to OneDrive")
    p.add_argument("--interval", type=int, default=60, help="seconds between syncs (default 60)")
    args = p.parse_args()

    if args.once:
        return 1 if sync(args.mirror) >= 8 else 0
    while True:
        sync(args.mirror)
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
