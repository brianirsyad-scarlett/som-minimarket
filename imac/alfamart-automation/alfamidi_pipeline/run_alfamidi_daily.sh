#!/bin/bash
# Daily Alfamidi pipeline, meant to run from launchd.
#   Unlike Alfamart, Alfamidi has no login/2FA fetch step here -- the source
#   CSVs/XLSX in OneDrive SOM/Minimarket/Alfamidi/{Sell Out,Daily Sell Out}
#   are expected to already be synced in from salesops-512 by the time this
#   runs. This script only converts + uploads + mirrors what it finds.
#
#   Steps: 1) Market Share raw converter  2) Sell Out summary builder
#          3) xlsx->csv converter          4) GCS upload + iMac mirror
#
#   Step 4 needs a real period_utils.py (period boundary logic) copied in
#   from salesops-512 -- see period_utils.py in this folder. Until that's
#   done, step 4 is skipped (logged as SKIPPED, not silently wrong) while
#   steps 1-3 still run and produce local output.
set -uo pipefail

DIR="$HOME/alfamart-automation/alfamidi_pipeline"
LOCK="/tmp/alfamidi_daily.lock"
LOG="$DIR/alfamidi_daily.log"

# Same split as Alfamart: framework python has pandas/openpyxl, the scrwms
# venv has google-cloud-storage. Neither has both.
PY="/Library/Frameworks/Python.framework/Versions/3.14/bin/python3"
GCS_PY="$HOME/scrwms-automation/venv/bin/python3"
GCS_KEY="$HOME/scrwms-automation/gcs-service-account.json"

cd "$DIR" || exit 1

if ! mkdir "$LOCK" 2>/dev/null; then
    echo "$(date '+%F %T')  previous run still active ($LOCK) -- skipping" >> "$LOG"
    exit 0
fi
trap 'rmdir "$LOCK" 2>/dev/null || true' EXIT

{
    echo "===== $(date '+%F %T')  starting Alfamidi daily run ====="

    echo "--- 1/4 Market Share converter ---"
    "$PY" alfamidi_market_share.py || echo "  (market share step reported a problem -- continuing)"

    echo "--- 2/4 Sell Out summary builder ---"
    "$PY" alfamidi_summary_sell_out.py || echo "  (summary step reported a problem -- continuing)"

    echo "--- 3/4 xlsx -> csv converter ---"
    "$PY" alfamidi_csv_converter.py || echo "  (csv converter step reported a problem -- continuing)"

    echo "--- 4/4 GCS upload + iMac mirror ---"
    # Compute today's 10-day period bounds via period_utils.py. Fails loudly
    # (NotImplementedError) until the real period_utils.py replaces the
    # placeholder -- in that case, skip step 4 rather than upload with wrong
    # (or no) period filtering.
    BOUNDS=$("$PY" -c "
import datetime, period_utils
today = datetime.date.today()
idx = period_utils.period_index(today.day)
start, end = period_utils.period_bounds(today.year, today.month, idx)
print(start.isoformat(), end.isoformat())
" 2>&1)
    if [[ $? -ne 0 ]]; then
        echo "  SKIPPED: period_utils.py is still the placeholder (or errored):"
        echo "$BOUNDS" | sed 's/^/    /'
    else
        read -r LATEST_START LATEST_END <<< "$BOUNDS"
        if [[ -x "$GCS_PY" && -f "$GCS_KEY" ]]; then
            GOOGLE_APPLICATION_CREDENTIALS="$GCS_KEY" "$GCS_PY" alfamidi_upload_and_distribute.py \
                --latest-start "$LATEST_START" --latest-end "$LATEST_END" \
                || echo "  (upload/distribute step reported a problem -- see log)"
        else
            echo "  SKIPPED: GCS python or key missing ($GCS_PY / $GCS_KEY)"
        fi
    fi

    echo "===== $(date '+%F %T')  done ====="
} >> "$LOG" 2>&1
