#!/bin/bash
# Safety net for the Anchanto TikTok reporting pipeline (chained onto the
# nightly Tokopedia export in run_tokped_daily.sh). Scheduled via launchd at
# 03:00 and 04:00: if TikTok.parquet hasn't been rebuilt since today's 00:00
# run started -- the nightly export failed, the Mac was asleep at midnight,
# a OneDrive file-eviction race broke the conversion, etc. -- re-run the
# CSV-copy + parquet-rebuild steps here instead of waiting for tomorrow.
# Also uploads the rebuilt parquet to GCS (gcs_upload_tiktok.py) and the raw,
# per-day export CSVs as a separate audit trail (gcs_upload_tiktok_raw.py) --
# both scripts live outside OneDrive (~/tokopedia-automation) on purpose, so
# they aren't subject to the same eviction risk as the two scripts below.
set -uo pipefail

ECOM_DIR="/Users/salesops/Library/CloudStorage/OneDrive-PT.OptoLumbungSejahtera/SOM/Anchanto Report/E-Commerce Data"
PARQUET="$ECOM_DIR/TikTok.parquet"
PY="/Library/Frameworks/Python.framework/Versions/3.14/bin/python3"
LOCK="/tmp/tiktok_pipeline_check.lock"
LOG="$HOME/Library/Logs/tiktok-pipeline-check.log"
GCS_UPLOAD_SCRIPT="$HOME/tokopedia-automation/gcs_upload_tiktok.py"
GCS_UPLOAD_RAW_SCRIPT="$HOME/tokopedia-automation/gcs_upload_tiktok_raw.py"

if ! mkdir "$LOCK" 2>/dev/null; then
    echo "$(date '+%F %T')  previous check/rerun still active ($LOCK) -- skipping" >> "$LOG"
    exit 0
fi
trap 'rmdir "$LOCK" 2>/dev/null || true' EXIT

today_midnight_epoch=$(date -j -f "%Y-%m-%d %H:%M:%S" "$(date '+%Y-%m-%d') 00:00:00" "+%s")

if [[ -f "$PARQUET" ]]; then
    parquet_mtime=$(stat -f "%m" "$PARQUET")
else
    parquet_mtime=0
fi

if [[ "$parquet_mtime" -ge "$today_midnight_epoch" ]]; then
    echo "$(date '+%F %T')  TikTok.parquet already updated today ($(date -r "$parquet_mtime" '+%F %T')) -- nothing to do" >> "$LOG"
    exit 0
fi

echo "$(date '+%F %T')  TikTok.parquet NOT updated since today's 00:00 run -- re-running conversion pipeline" >> "$LOG"

# Raw CSV audit upload is independent of the conversion below (different
# source folder, doesn't need ECOM_DIR as cwd) and only needs to happen once
# per day -- placing it here, gated on the same "not yet updated today"
# check as the conversion, means it runs on whichever invocation (00:00
# chain or the 03:00/04:00 safety net) actually does today's work, and is
# skipped by the other invocations once that's already happened.
echo "$(date '+%F %T')  running gcs_upload_tiktok_raw.py (raw CSV audit upload)" >> "$LOG"
"$PY" -u "$GCS_UPLOAD_RAW_SCRIPT" >> "$LOG" 2>&1
rc0=$?
echo "$(date '+%F %T')  gcs_upload_tiktok_raw.py exit=$rc0" >> "$LOG"

if ! cd "$ECOM_DIR"; then
    echo "$(date '+%F %T')  ERROR: could not cd into $ECOM_DIR (OneDrive not mounted/reachable yet?) -- pwd=$(pwd)" >> "$LOG"
    exit 1
fi

# OneDrive's Files-On-Demand can evict a rarely-touched local file (these two
# scripts included -- confirmed 2026-09-23) back to a cloud-only placeholder
# (0 disk blocks). Running an evicted .py file as a script has been observed
# to silently execute as empty (exit 0, zero output, under a second) instead
# of erroring or blocking to re-download -- which is exactly the "silent
# false success" this safety net exists to catch, so check for it explicitly
# up front rather than trusting the scripts' own exit code.
_ensure_materialized() {
    local f="$1"
    local blocks
    blocks=$(stat -f "%b" "$f" 2>/dev/null || echo 0)
    if [[ "$blocks" != "0" ]]; then
        return 0
    fi
    echo "$(date '+%F %T')  WARNING: $f is a OneDrive cloud-only placeholder (0 blocks) -- attempting to materialize it (up to 20s)" >> "$LOG"
    ( cat "$f" > /dev/null 2>>"$LOG" ) &
    local catpid=$! waited=0
    while kill -0 "$catpid" 2>/dev/null && [[ $waited -lt 20 ]]; do
        sleep 1
        waited=$((waited + 1))
    done
    if kill -0 "$catpid" 2>/dev/null; then
        kill "$catpid" 2>/dev/null
        echo "$(date '+%F %T')  ERROR: $f still not materialized after 20s -- OneDrive itself needs attention (restart the app, or check for a stuck sync state); not running it while it may read as empty" >> "$LOG"
        return 1
    fi
    blocks=$(stat -f "%b" "$f" 2>/dev/null || echo 0)
    if [[ "$blocks" == "0" ]]; then
        echo "$(date '+%F %T')  ERROR: $f still shows 0 blocks after materialize attempt -- not running it while it may read as empty" >> "$LOG"
        return 1
    fi
    echo "$(date '+%F %T')  $f materialized OK" >> "$LOG"
    return 0
}

echo "$(date '+%F %T')  running tiktok_converter.py (cwd=$(pwd), python=$PY)" >> "$LOG"
if _ensure_materialized "tiktok_converter.py"; then
    "$PY" -u tiktok_converter.py >> "$LOG" 2>&1
    rc1=$?
else
    rc1=1
fi
echo "$(date '+%F %T')  tiktok_converter.py exit=$rc1" >> "$LOG"

rc2=0
if [[ "$rc1" -eq 0 ]]; then
    echo "$(date '+%F %T')  running parquet_converter_tiktok.py" >> "$LOG"
    if _ensure_materialized "parquet_converter_tiktok.py"; then
        "$PY" -u parquet_converter_tiktok.py >> "$LOG" 2>&1
        rc2=$?
    else
        rc2=1
    fi
    echo "$(date '+%F %T')  parquet_converter_tiktok.py exit=$rc2" >> "$LOG"
else
    echo "$(date '+%F %T')  skipping parquet_converter_tiktok.py since tiktok_converter.py failed" >> "$LOG"
fi

rc3=0
if [[ "$rc2" -eq 0 ]]; then
    echo "$(date '+%F %T')  running gcs_upload_tiktok.py" >> "$LOG"
    "$PY" -u "$GCS_UPLOAD_SCRIPT" >> "$LOG" 2>&1
    rc3=$?
    echo "$(date '+%F %T')  gcs_upload_tiktok.py exit=$rc3" >> "$LOG"
else
    echo "$(date '+%F %T')  skipping gcs_upload_tiktok.py since parquet rebuild failed" >> "$LOG"
fi

rc=$rc0
[[ "$rc" -eq 0 ]] && rc=$rc1
[[ "$rc" -eq 0 ]] && rc=$rc2
[[ "$rc" -eq 0 ]] && rc=$rc3
echo "$(date '+%F %T')  rerun finished (exit $rc)" >> "$LOG"
exit "$rc"
