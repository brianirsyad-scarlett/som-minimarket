#!/bin/bash
# Daily Tokopedia order export, meant to run from launchd every night at 00:00.
#   1. Reuses the saved Chrome session (browser_profile/) -- logs in automatically
#      only if that session has expired, using the password stored in Keychain.
#   2. Exports orders one day at a time for the rolling window [today-45, today].
#   3. Copies the downloaded CSVs into the OneDrive "TikTok Seller Data" folder.
#   4. On success, chains into the Anchanto reporting pipeline: renames/copies
#      those CSVs into the "TikTok" folder, rebuilds TikTok.parquet from the
#      full historical set, then uploads it to gs://bucket_som/sales_parquet/
#      TikTok.parquet, and separately uploads each raw per-day export CSV to
#      gs://bucket_som/sales_parquet/raw/ecommerce/tiktok/ as an audit trail
#      -- see tiktok_pipeline_check.sh for the 03:00/04:00 safety net that
#      catches a failure/skip anywhere in this chain.
set -uo pipefail

DIR="$HOME/tokopedia-automation"
LOCK="/tmp/tokped_daily.lock"
# Absolute path required: under launchd, `python3` resolves to system Python,
# which does not have selenium installed.
PY="/Library/Frameworks/Python.framework/Versions/3.14/bin/python3"
ONEDRIVE_OUT="/Users/salesops/Library/CloudStorage/OneDrive-PT.OptoLumbungSejahtera/SOM/Anchanto Report/E-Commerce Data/TikTok Seller Data"
ENV_FILE="$HOME/.tokopedia.env"

cd "$DIR" || exit 1

# Optional: enables auto-fetching the email verification code if re-login needs
# one. Without it, an expired session just falls back to asking for a manual
# `tokped_login.py` run, same as before -- see README.md.
if [[ -f "$ENV_FILE" ]]; then
    # shellcheck disable=SC1090
    source "$ENV_FILE"
fi

if ! mkdir "$LOCK" 2>/dev/null; then
    echo "$(date '+%F %T')  previous run still active ($LOCK) -- skipping"
    exit 0
fi
trap 'rmdir "$LOCK" 2>/dev/null || true' EXIT

if [[ ! -x "$PY" ]]; then
    echo "ERROR: python interpreter not found at $PY" >&2
    exit 1
fi

echo "===== $(date '+%F %T')  starting Tokopedia daily export ====="

status="OK"
if ! "$PY" tokped_export.py; then
    status="FAILED"
fi

if [[ "$status" == "OK" ]]; then
    echo "----- $(date '+%F %T')  chaining into Anchanto TikTok pipeline -----"
    # Delegates to tiktok_pipeline_check.sh rather than duplicating the
    # conversion calls here -- it already has the OneDrive-eviction
    # materialization guard (a stuck/evicted tiktok_converter.py or
    # parquet_converter_tiktok.py silently "succeeds" while doing nothing,
    # confirmed 2026-09-23) and the "only run if not already updated today"
    # check, so running it here just does today's attempt early; the 03:00/
    # 04:00 schedule is what still catches it if this attempt is skipped
    # (Mac asleep at midnight) or fails.
    if ! bash "$DIR/tiktok_pipeline_check.sh"; then
        echo "WARNING: tiktok_pipeline_check.sh reported a failure -- TikTok.parquet may be stale (the 03:00/04:00 run will retry, see tiktok-pipeline-check.log)"
    fi
fi

mkdir -p "$ONEDRIVE_OUT" 2>/dev/null || true
if [[ -d "$ONEDRIVE_OUT" ]]; then
    # A plain `>> file 2>/dev/null` doesn't actually suppress a redirection-
    # open failure -- bash prints that (e.g. "Operation not permitted", the
    # same OneDrive file-lock/eviction race seen elsewhere in this pipeline)
    # to the script's own stderr regardless, since the message comes from
    # setting up the redirection itself, before the command's own stderr
    # applies. Wrapping the whole thing in a group redirect catches that too,
    # and retrying gives the transient lock a moment to clear.
    status_line="$(date '+%F %T')  $(printf '%-7s' "$status")"
    for attempt in 1 2 3 4 5; do
        if { printf '%s\n' "$status_line" >> "$ONEDRIVE_OUT/_run_status.txt"; } 2>/dev/null; then
            break
        fi
        sleep 3
    done
fi

echo "===== $(date '+%F %T')  done ($status) ====="
[[ "$status" == "OK" ]]
