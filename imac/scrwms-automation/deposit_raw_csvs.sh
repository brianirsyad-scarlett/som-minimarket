#!/bin/bash
# Copies the 3 current-window local safety-net CSVs into the OneDrive-shared
# "SOM/Anchanto Report" folder, renamed to match the processor's expected
# input glob "B2C_Order_Report_*.csv". Each gets a unique, date-range-based
# suffix so the 3 segments never collide on disk.
#
# Source: ~/scrwms-automation/exports/Anchanto YY M MMM (NN).csv
#         (always the latest successful download for that segment -- see
#         scrwms_report.py's LOCAL_EXPORT_DIR)
# Destination: OneDrive/SOM/Anchanto Report/B2C_Order_Report_<start>-<end>_Adhoc.csv
#
# Does NOT touch the local safety-net copies -- only copies them onward.
set -euo pipefail

DIR="$HOME/scrwms-automation"
DEST="/Users/salesops/Library/CloudStorage/OneDrive-PT.OptoLumbungSejahtera/SOM/Anchanto Report"
PY="$DIR/venv/bin/python3"

cd "$DIR"

# Ask scrwms_report.py what today's 3 segments are (pure computation, no
# network/login) and which local file backs each one.
mapping=$("$PY" - <<'PYEOF'
import datetime, os, sys
sys.path.insert(0, os.path.expanduser("~/scrwms-automation"))
import scrwms_report as s

today = datetime.date.today()
for seg in s.rolling_segments(today, count=3):
    local_name = s._plan_name(seg)
    _, _, _, start, end = seg
    dest_name = f"B2C_Order_Report_{start.strftime('%Y%m%d')}-{end.strftime('%Y%m%d')}_Adhoc.csv"
    print(f"{local_name}\t{dest_name}")
PYEOF
)

if [[ -z "$mapping" ]]; then
    echo "ERROR: could not compute today's segment mapping" >&2
    exit 1
fi

copied=0
missing=0
while IFS=$'\t' read -r local_name dest_name; do
    src="$DIR/exports/$local_name"
    if [[ ! -f "$src" ]]; then
        echo "  MISSING local export, skipping: $local_name"
        missing=$((missing+1))
        continue
    fi
    dest="$DEST/$dest_name"
    # Remove-then-copy: overwriting a file OneDrive already has synced can be
    # rejected for background processes; a fresh create is not.
    [[ -e "$dest" ]] && rm -f "$dest"
    cp "$src" "$dest"
    echo "  deposited: $local_name -> $dest_name"
    copied=$((copied+1))
done <<< "$mapping"

echo "Deposited $copied/3 raw CSVs into '$DEST'"
[[ $missing -eq 0 ]]
