#!/bin/bash
# Trigger the two Anchanto BigQuery scheduled queries, in dependency order:
#     raw_anchanto_internal   (external GCS table -> parsed internal table)
#  -> anchanto_report         (internal + dim joins -> final mart)
#
# Both are "CREATE OR REPLACE TABLE", i.e. idempotent full rebuilds, so an extra
# run is always safe -- they also run hourly on their own schedule regardless.
#
# AUTH NOTE: these transfer configs are owned by the user account, and the
# metabase-restricted-access service account lacks bigquery.transfers.{get,update}.
# So we explicitly use the user credential already stored in ~/.config/gcloud.
# CLOUDSDK_CORE_ACCOUNT is set per-invocation so we never mutate the shared
# global gcloud config (other automations on this Mac depend on it).
#
# Usage:  ./refresh_bigquery.sh [--dry-run]
set -uo pipefail

SDK="$HOME/google-cloud-sdk/bin"
export PATH="$SDK:$PATH"                       # bq shells out to gcloud via PATH
export CLOUDSDK_CORE_ACCOUNT="brian.rinaldy@scarlett.co.id"

PROJECT_LOC="projects/572457490769/locations/asia-southeast2/transferConfigs"
CFG_INTERNAL="$PROJECT_LOC/6aa21d49-0000-2cb8-a1f7-34c7e90778cb"   # raw_anchanto_internal
CFG_REPORT="$PROJECT_LOC/6aa26d1c-0000-2c66-8083-34391605ec73"     # anchanto_report

DRY_RUN=0
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=1

MAX_WAIT=900        # seconds to wait for a single query to finish
POLL=10

# `bq mk --transfer_run` returns a JSON *array*; `bq show --transfer_run` returns
# a JSON *object*. Handle both, and never let a parse error look like an API error.
json_field () {
    python3 -c '
import json, sys
try:
    d = json.loads(sys.stdin.read())
except Exception:
    sys.exit(1)
if isinstance(d, list):
    if not d:
        sys.exit(1)
    d = d[0]
if not isinstance(d, dict):
    sys.exit(1)
v = d.get(sys.argv[1], "")
print(v if v else "")
' "$1" 2>/dev/null
}

run_config () {
    local label="$1" cfg="$2"

    if [[ ! -x "$SDK/bq" ]]; then
        echo "  BQ WARNING: bq not found at $SDK/bq -- skipping $label"
        return 1
    fi

    if [[ $DRY_RUN -eq 1 ]]; then
        local out name
        out=$(bq show --format=prettyjson --transfer_config "$cfg" 2>&1)
        name=$(printf '%s' "$out" | json_field displayName)
        if [[ -z "$name" ]]; then
            echo "  BQ WARNING: cannot read $label config:"
            printf '%s\n' "$out" | head -5 | sed 's/^/      /'
            return 1
        fi
        echo "  [dry-run] would trigger $label (displayName: $name)"
        return 0
    fi

    local out run_name
    out=$(bq mk --transfer_run --run_time="$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
             --format=prettyjson "$cfg" 2>&1)
    run_name=$(printf '%s' "$out" | json_field name)

    if [[ -z "$run_name" ]]; then
        # Google forces periodic reauth on USER credentials, and reauth needs an
        # interactive browser prompt that a launchd job can never satisfy. This is
        # expected and NOT an emergency: both configs also run hourly on their own
        # schedule, server-side, using credentials BigQuery holds independently of
        # this Mac. So we report it as a distinct, non-fatal condition (rc 2) rather
        # than a failure, to avoid crying wolf every 2 hours.
        if printf '%s' "$out" | grep -qE "Reauthentication failed|problem refreshing your current auth"; then
            echo "  BQ SKIPPED: local gcloud credential for $CLOUDSDK_CORE_ACCOUNT needs an"
            echo "              interactive 'gcloud auth login'. Not triggering $label."
            echo "              Tables still refresh hourly on their own schedule -- no data impact."
            return 2
        fi
        # Anything else is a genuine problem: surface what bq actually said.
        echo "  BQ WARNING: could not start $label. bq said:"
        printf '%s\n' "$out" | head -8 | sed 's/^/      /'
        return 1
    fi
    echo "  BQ started $label"

    local waited=0 state="" show_out
    while (( waited < MAX_WAIT )); do
        show_out=$(bq show --format=prettyjson --transfer_run "$run_name" 2>&1)
        state=$(printf '%s' "$show_out" | json_field state)
        case "$state" in
            SUCCEEDED) echo "  BQ OK $label (${waited}s)"; return 0 ;;
            FAILED|CANCELLED)
                echo "  BQ WARNING: $label finished as $state"
                printf '%s' "$show_out" | json_field errorStatus | head -3 | sed 's/^/      /'
                return 1 ;;
            "")  # couldn't read state at all -- report it rather than spin silently
                echo "  BQ WARNING: cannot read $label run state:"
                printf '%s\n' "$show_out" | head -5 | sed 's/^/      /'
                return 1 ;;
        esac
        sleep "$POLL"; waited=$(( waited + POLL ))
    done
    echo "  BQ WARNING: $label still $state after ${MAX_WAIT}s -- giving up waiting"
    return 1
}

echo "=== BigQuery refresh ==="
run_config "raw_anchanto_internal" "$CFG_INTERNAL"
rc=$?

case $rc in
    0)  # source table rebuilt cleanly -- safe to build the mart on top of it
        run_config "anchanto_report" "$CFG_REPORT"
        [[ $? -eq 0 ]] || rc=1
        ;;
    2)  # credential expired: skip quietly, hourly schedule has it covered
        echo "  BQ SKIPPED: anchanto_report too (same credential)."
        ;;
    *)  echo "  BQ WARNING: skipping anchanto_report because raw_anchanto_internal did not succeed"
        ;;
esac

# rc 2 (credential expired) is deliberately reported as success: it is an expected
# condition with no data impact, and must not make every 2-hourly run look broken.
[[ $rc -eq 2 ]] && rc=0
exit $rc
