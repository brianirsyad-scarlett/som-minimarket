#!/bin/bash
# Daily Alfamart B2B report pull, meant to run from launchd at 7am WIB.
#   1. Log in (Selenium + TOTP) and trigger every sell-out-by-branch and
#      sell-out-by-store export for the current month-to-date (~26 requests).
#   2. Each triggers an emailed download link (valid 24h), so poll the inbox
#      periodically to pick them up as they land.
#   3. Downloaded CSVs go to a local safety-net folder first (launchd can't
#      reliably read/write OneDrive-synced paths -- see SCRWMS_SETUP.md),
#      then get best-effort copied into the OneDrive "Alfamart" folder.
set -uo pipefail

DIR="$HOME/alfamart-automation"
LOCAL_OUT="$DIR/exports"
ONEDRIVE_OUT="/Users/salesops/Library/CloudStorage/OneDrive-PT.OptoLumbungSejahtera/SOM_iMac/iMac_Sales Ops/Alfamart"
ENV_FILE="$HOME/.alfamart.env"
LOCK="/tmp/alfamart_daily.lock"
# Absolute path required: under launchd, `python3` resolves to /usr/bin/python3
# (system Python), which does NOT have selenium -- same trap as SCRWMS's venv.
PY="/Library/Frameworks/Python.framework/Versions/3.14/bin/python3"
# Reuse the SCRWMS venv + service account for the GCS upload step.
GCS_PY="$HOME/scrwms-automation/venv/bin/python3"
GCS_KEY="$HOME/scrwms-automation/gcs-service-account.json"
SUMMARY_OUT="$DIR/summary"

cd "$DIR" || exit 1

if ! mkdir "$LOCK" 2>/dev/null; then
    echo "$(date '+%F %T')  previous run still active ($LOCK) -- skipping"
    exit 0
fi
trap 'rmdir "$LOCK" 2>/dev/null || true' EXIT

if [[ ! -f "$ENV_FILE" ]]; then
    echo "ERROR: $ENV_FILE not found." >&2
    exit 1
fi
# shellcheck disable=SC1090
source "$ENV_FILE"

# One directory per run. Each run pulls ~700MB of by-store CSVs, so they're
# kept separate (and the big ones deleted after a verified upload) rather than
# piling up in a single folder and filling the disk.
RUN_DIR="$LOCAL_OUT/$(date '+%F')"
mkdir -p "$RUN_DIR" "$SUMMARY_OUT"

if [[ ! -x "$PY" ]]; then
    echo "ERROR: python interpreter not found at $PY" >&2
    exit 1
fi

notify() {  # notify <status> <body>
    "$PY" alfamart_notify.py --status "$1" --body "$2" || true
}

# Status line into OneDrive (syncs to phone/Windows, needs no credentials) and
# an email (needs the Gmail app password). Must be called on EVERY exit path,
# including the early ones -- otherwise a run that stops early is silent.
report() {  # report <status> <message>
    if [[ -d "$ONEDRIVE_OUT" ]]; then
        printf '%s  %-7s %s\n' "$(date '+%F %T')" "$1" "$2" \
            >> "$ONEDRIVE_OUT/_run_status.txt" 2>/dev/null || true
    fi
    notify "$1" "$2"
}

echo "===== $(date '+%F %T')  triggering exports ====="
if ! "$PY" alfamart_trigger.py; then
    echo "ERROR: trigger step failed -- no exports were requested, aborting." >&2
    report "FAILED" "Trigger step failed - no exports requested (login/TOTP or portal problem)."
    exit 1
fi

# The by-store (per-category) exports typically email their download link
# within 1-2 minutes; the by-branch (nationwide) ones are much slower and can
# take up to ~an hour. Each link is valid for 24h once generated, so there's
# no need to race an expiry -- just poll periodically for long enough to
# comfortably catch the slow by-branch emails too.
# Without Gmail credentials the download half can't run at all. Skip it rather
# than looping for 90 minutes logging the same error -- the links stay valid
# for 24h, so a later manual run of alfamart_fetch_downloads.py still gets
# everything this run just triggered.
if [[ -z "${ALFAMART_GMAIL_ADDRESS:-}" || -z "${ALFAMART_GMAIL_APP_PASSWORD:-}" ]]; then
    echo "===== $(date '+%F %T')  NO GMAIL CREDENTIALS -- skipping download phase ====="
    echo "  Exports were triggered and their links are valid for 24h."
    echo "  Add ALFAMART_GMAIL_ADDRESS / ALFAMART_GMAIL_APP_PASSWORD to $ENV_FILE, then run:"
    echo "    cd $DIR && source $ENV_FILE && python3 alfamart_fetch_downloads.py --out-dir \"$RUN_DIR\""
    report "PARTIAL" "Exports triggered OK but NOT downloaded (no Gmail app password in ~/.alfamart.env). Links valid 24h."
    echo "===== $(date '+%F %T')  done (trigger only) ====="
    exit 0
fi

run_problems=""

echo "===== $(date '+%F %T')  polling for download emails (90 min, every 5 min) ====="
for i in $(seq 1 18); do
    "$PY" alfamart_fetch_downloads.py --out-dir "$RUN_DIR"
    sleep 300
done

echo "===== $(date '+%F %T')  uploading to GCS ====="
# Separate interpreter: the venv has google-cloud-storage, the framework python
# has selenium. Neither has both.
store_rc=0
if [[ -x "$GCS_PY" && -f "$GCS_KEY" ]]; then
    "$GCS_PY" alfamart_upload_gcs.py --src-dir "$RUN_DIR" --key "$GCS_KEY" \
        --bucket bucket_som \
        --match "Selling_Out_Qty_BRANCH_NASIONAL_All_Store" \
        --prefix "sales_sell out_minimarket/alfamart/daily_sell_out_qty" \
        || { store_rc=1; run_problems+="by-store Qty upload failed. "; echo "  (Qty upload reported a problem -- continuing)"; }
    "$GCS_PY" alfamart_upload_gcs.py --src-dir "$RUN_DIR" --key "$GCS_KEY" \
        --bucket bucket_som \
        --match "Selling_Out_Value_BRANCH_NASIONAL_All_Store" \
        --prefix "sales_sell out_minimarket/alfamart/daily_sell_out_value" \
        || { store_rc=1; run_problems+="by-store Value upload failed. "; echo "  (Value upload reported a problem -- continuing)"; }
else
    store_rc=1
    run_problems+="GCS tooling missing, no uploads attempted. "
    echo "WARNING: skipping GCS upload ($GCS_PY or $GCS_KEY missing)"
fi

# Combine the by-branch Value+Qty pair into the monthly summary and overwrite
# the current month's object. Alfamart restates the most recent day or two, so
# re-pulling month-to-date and overwriting is intentional, not redundant.
echo "===== $(date '+%F %T')  building monthly sell-out summary ====="
if "$PY" alfamart_summary_sell_out.py --src-dir "$RUN_DIR" --out-dir "$SUMMARY_OUT"; then
    if [[ -x "$GCS_PY" && -f "$GCS_KEY" ]]; then
        "$GCS_PY" alfamart_upload_gcs.py --src-dir "$SUMMARY_OUT" --key "$GCS_KEY" \
            --bucket bucket_som \
            --match "Sell Out Alfamart_Sell Out" \
            --prefix "sales_sell out_minimarket/alfamart/sell_out" \
            || { run_problems+="summary upload failed. "; echo "  (summary upload reported a problem -- continuing)"; }
    fi
else
    run_problems+="summary build failed (by-branch Value/Qty pair may not have arrived). "
    echo "  (summary build failed -- by-branch pair may not have arrived)"
fi

echo "===== $(date '+%F %T')  syncing to OneDrive ====="
mkdir -p "$ONEDRIVE_OUT" 2>/dev/null || echo "WARNING: could not create $ONEDRIVE_OUT"
# Overwrite rather than -n: by-store chunk filenames are stable for the whole
# 10-day window, so a newer copy is simply a more complete one.
if [[ -d "$ONEDRIVE_OUT" ]]; then
    cp -f "$SUMMARY_OUT"/*.csv "$ONEDRIVE_OUT"/ 2>/dev/null || true
    cp -f "$RUN_DIR"/*.csv "$ONEDRIVE_OUT"/ 2>/dev/null || true
fi

# The by-store files are ~350MB each and GCS is the system of record, so drop
# them once they're safely uploaded -- otherwise this fills the disk at ~700MB
# a day. Anything that failed to upload is left in place for a retry.
if [[ $store_rc -eq 0 ]]; then
    echo "===== $(date '+%F %T')  removing uploaded by-store CSVs from $RUN_DIR ====="
    rm -f "$RUN_DIR"/*_All_Store_All_Category_*.csv
else
    echo "WARNING: keeping local by-store CSVs (upload did not fully succeed)"
fi

downloaded=$(ls -1 "$RUN_DIR"/*.csv 2>/dev/null | wc -l | tr -d ' ')

if [[ -n "$run_problems" ]]; then
    report "FAILED" "${run_problems}Files left in $RUN_DIR: $downloaded"
else
    report "OK" "Uploaded by-store Value+Qty and the monthly summary. Files left in $RUN_DIR: $downloaded"
fi

echo "===== $(date '+%F %T')  done ====="
