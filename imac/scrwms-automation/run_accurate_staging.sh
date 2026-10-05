#!/bin/bash
# Daily Accurate -> GCS staging conversion, meant to run from launchd.
# Self-contained: reads raw xlsx from GCS raw/, converts to csv, writes to
# GCS staging/ (skips files already converted). No local SOM files touched.
set -uo pipefail

DIR="$HOME/scrwms-automation"
KEY="$DIR/gcs-service-account.json"
GCS_PY="$DIR/venv/bin/python3"
LOCK="/tmp/accurate_staging.lock"
LOG="$DIR/accurate_staging.log"

cd "$DIR" || exit 1

if ! mkdir "$LOCK" 2>/dev/null; then
    echo "$(date '+%F %T')  previous run still active ($LOCK) -- skipping" >> "$LOG"
    exit 0
fi
trap 'rmdir "$LOCK" 2>/dev/null || true' EXIT

if [[ ! -x "$GCS_PY" || ! -f "$KEY" ]]; then
    echo "$(date '+%F %T')  ERROR: GCS python or key missing ($GCS_PY / $KEY)" >> "$LOG"
    exit 1
fi

{
    echo "===== $(date '+%F %T')  starting accurate staging run ====="
    GOOGLE_APPLICATION_CREDENTIALS="$KEY" "$GCS_PY" accurate_staging.py
    rc=$?
    if [[ $rc -eq 0 ]]; then
        echo "===== $(date '+%F %T')  OK ====="
    else
        echo "===== $(date '+%F %T')  FAILED (exit $rc) ====="
    fi
} >> "$LOG" 2>&1
