#!/bin/bash
# Start one SOM GitHub Actions pipeline on time. Called by launchd on the always-on
# iMac, because GitHub's own cron starts these runs hours late.
#
#   trigger.sh odoo | anchanto | accurate | sellin
#
# Needs the GitHub CLI logged in once:  brew install gh && gh auth login
set -u
OWNER="brianirsyad-scarlett"
LOG_DIR="$HOME/som-scheduler/logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/$(date +%Y-%m).log"
# launchd starts jobs with a minimal PATH; Homebrew lives in one of these.
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

case "${1:-}" in
  odoo)     REPO="som-odoo-report-automation";     WF="odoo-export.yml";       ARGS=() ;;
  anchanto) REPO="som-anchanto-report-automation"; WF="anchanto-pipeline.yml"; ARGS=() ;;
  accurate) REPO="som-accurate-report-automation"; WF="accurate-pipeline.yml"; ARGS=() ;;
  # force=false: the workflow's gate decides - it only builds once today's Odoo,
  # Anchanto and Accurate are all in GCS, and only once per day.
  sellin)   REPO="som-sell-in-report-automation";  WF="sell-in-pipeline.yml"
            ARGS=(-f months=prev,current -f anchanto_scope=quarter -f force=false) ;;
  *) echo "usage: $0 odoo|anchanto|accurate|sellin" >&2; exit 2 ;;
esac

for attempt in 1 2 3; do
  if out=$(gh workflow run "$WF" --repo "$OWNER/$REPO" "${ARGS[@]}" 2>&1); then
    echo "$(date '+%F %T')  $1  started  $out" >> "$LOG"
    exit 0
  fi
  echo "$(date '+%F %T')  $1  attempt $attempt failed: $out" >> "$LOG"
  sleep 60
done
exit 1
