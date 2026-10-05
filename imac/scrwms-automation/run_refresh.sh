#!/bin/bash
# Anchanto / SCRWMS refresh. Runs from a LOCAL folder -- launchd cannot reliably
# read scripts inside the OneDrive-synced "iMac_Sales Ops" folder.
#
# Usage:  ./run_refresh.sh <segment-count>
#
#   1  -> only the CURRENT 10-day segment          (cheap; runs every 2 hours)
#   3  -> current + previous two 10-day segments   (~260MB; runs once daily)
#
# Segments are the fixed thirds of a month (01 = days 1-10, 02 = 11-20,
# 03 = 21-end) and roll across month boundaries automatically.
#
# Each run:
#   1. bulk-deletes existing B2C Order Report schedules on Anchanto (--replace)
#   2. creates the segment(s) above and downloads each to the local safety-net dir
#   3. best-effort syncs into OneDrive "Anchanto Export/", and uploads to
#      gs://bucket_som/sales_parquet/raw/online/anchanto/scrwms/ as
#      "Anchanto YY M MMM - Order Report-N.csv"
#   4. rebuilds the downstream BigQuery tables (raw_anchanto_internal -> anchanto_report)
#
# Older segments are NOT regenerated on the 2-hourly run: that data is settled,
# and their files simply stay in the bucket until the daily run refreshes them.
#
# The daily (count=3) run additionally, once all 3 segments are fresh:
#   5. deposits raw CSVs into OneDrive "SOM/Anchanto Report/" as
#      B2C_Order_Report_<start>-<end>_Adhoc.csv (see deposit_raw_csvs.sh)
#   6. runs anchanto_processor.py (Mac-adapted from the original Windows-path
#      script) to rebuild the Excel/CSV/parquet outputs PowerBI reads from.
#      This REPLACES the old Windows Task Scheduler job -- see SCRWMS_SETUP.md.
set -euo pipefail

COUNT="${1:-3}"
if ! [[ "$COUNT" =~ ^[1-9][0-9]*$ ]]; then
    echo "ERROR: segment count must be a positive integer, got '$COUNT'" >&2
    exit 2
fi

DIR="$HOME/scrwms-automation"
OUT="/Users/salesops/Library/CloudStorage/OneDrive-PT.OptoLumbungSejahtera/SOM_iMac/iMac_Sales Ops/Anchanto Export"
ENV_FILE="$HOME/.scrwms.env"
GCS_KEY="$DIR/gcs-service-account.json"
PY="$DIR/venv/bin/python3"      # venv has google-cloud-storage; /usr/bin/python3 does not
PROC_PY="/Library/Frameworks/Python.framework/Versions/3.14/bin/python3"  # has pandas/openpyxl/polars/pyarrow, for anchanto_processor.py

# One lock shared by BOTH the 2-hourly and daily jobs: they hit the same SCRWMS
# account and the same delete-by-type logic, so they must never overlap.
LOCK="/tmp/scrwms_refresh.lock"

cd "$DIR"

if ! mkdir "$LOCK" 2>/dev/null; then
    echo "$(date '+%F %T')  another refresh still active ($LOCK) -- skipping count=$COUNT run"
    exit 0
fi
trap 'rmdir "$LOCK" 2>/dev/null || true' EXIT

if [[ ! -f "$ENV_FILE" ]]; then
    echo "ERROR: $ENV_FILE not found. Create it from .scrwms.env.template (see SCRWMS_SETUP.md)." >&2
    exit 1
fi
# shellcheck disable=SC1090
source "$ENV_FILE"

# macOS ships no `timeout`/`gtimeout` binary, and OneDrive reads/writes have been
# observed to hang indefinitely with no exception at all (not just error out) --
# so anything touching that folder gets a hard wall-clock deadline here. Without
# this, a single hang would hold $LOCK forever and silently stop BOTH the
# 2-hourly and daily jobs from ever running again.
run_with_timeout () {
    local secs="$1"; shift
    "$@" &
    local pid=$!
    ( sleep "$secs" && kill -9 "$pid" 2>/dev/null ) &
    local watchdog=$!
    local rc=0
    wait "$pid" || rc=$?
    kill "$watchdog" 2>/dev/null
    wait "$watchdog" 2>/dev/null
    return $rc
}

if [[ "$COUNT" == "1" ]]; then MODE="2-hourly (current segment only)"; else MODE="daily (last $COUNT segments)"; fi
echo "===== $(date '+%F %T')  refresh start -- $MODE ====="

# Steps 1-3. A non-zero exit here must NOT skip the BigQuery refresh: partial
# success is normal (OneDrive sync fails while the GCS upload succeeds) and the
# tables should still pick up whatever did land in the bucket.
scrwms_rc=0
"$PY" scrwms_report.py rolling \
    --profile b2c_order_report.json \
    --count "$COUNT" \
    --replace \
    --out-dir "$OUT" \
    --gcs-bucket bucket_som \
    --gcs-prefix sales_parquet/raw/online/anchanto/scrwms \
    --gcs-key "$GCS_KEY" || scrwms_rc=$?
if [[ $scrwms_rc -ne 0 ]]; then
    echo "  (scrwms_report.py exited $scrwms_rc -- continuing to BigQuery refresh anyway)"
fi

# Step 4.
bq_rc=0
"$DIR/refresh_bigquery.sh" || bq_rc=$?
if [[ $bq_rc -ne 0 ]]; then
    echo "  (BigQuery refresh reported a problem -- see BQ WARNING lines above)"
fi


# Steps 5-6: only on the full (daily) run -- the processor needs all 3
# segments fresh, and running it every 2 hours on the small 1-segment refresh
# would just churn the shared Excel/parquet outputs for no benefit.
proc_rc=0
if [[ "$COUNT" -ge 3 ]]; then
    echo "=== Depositing raw CSVs + running Anchanto processor ==="
    run_with_timeout 300 "$DIR/deposit_raw_csvs.sh" \
        || { echo "  (deposit_raw_csvs.sh failed or timed out -- skipping processor)"; proc_rc=1; }
    if [[ $proc_rc -eq 0 ]]; then
        run_with_timeout 1800 "$PROC_PY" "$DIR/anchanto_processor.py" < /dev/null || proc_rc=$?
        if [[ $proc_rc -eq 137 ]]; then
            echo "  (anchanto_processor.py killed after exceeding 1800s -- likely an OneDrive hang"
            echo "   that its own internal retry/timeout logic didn't catch; investigate)"
        elif [[ $proc_rc -ne 0 ]]; then
            echo "  (anchanto_processor.py exited $proc_rc)"
        fi
    fi
fi

echo "===== $(date '+%F %T')  done -- $MODE (scrwms=$scrwms_rc bq=$bq_rc proc=$proc_rc) ====="
exit $(( scrwms_rc != 0 || bq_rc != 0 || proc_rc != 0 ? 1 : 0 ))
