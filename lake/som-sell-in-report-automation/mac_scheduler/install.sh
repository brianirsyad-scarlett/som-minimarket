#!/bin/bash
# One-time setup on the always-on iMac. Installs launchd jobs that start the SOM
# GitHub pipelines on time (times are the iMac's local clock - set it to WIB):
#
#   Odoo      00:05, 03:05, 06:05, 09:05, 12:05, 15:05, 18:05, 21:05
#   Anchanto  00:32   (reports are created 00:00-01:00; the pipeline picks those)
#   Accurate  00:47
#   Sell In   01:30, 02:30, 03:30, 05:00, 07:00 - the workflow's gate builds only
#             once all three inputs are in, and only once a day, so extra starts
#             cost a few seconds each
#
# Run:  bash install.sh        Remove:  bash install.sh --uninstall
set -euo pipefail
DEST="$HOME/som-scheduler"
AGENTS="$HOME/Library/LaunchAgents"
LABELS=(com.som.trigger.odoo com.som.trigger.anchanto com.som.trigger.accurate com.som.trigger.sellin)

if [ "${1:-}" = "--uninstall" ]; then
  for l in "${LABELS[@]}"; do
    launchctl bootout "gui/$(id -u)/$l" 2>/dev/null || true
    rm -f "$AGENTS/$l.plist"
  done
  echo "Removed. Logs kept in $DEST/logs."
  exit 0
fi

export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
command -v gh >/dev/null || { echo "Install the GitHub CLI first:  brew install gh"; exit 1; }
gh auth status >/dev/null 2>&1 || { echo "Log in first:  gh auth login"; exit 1; }
[ "$(date +%Z)" = "WIB" ] || echo "Note: this Mac's time zone is $(date +%Z), not WIB - the times above are local."

mkdir -p "$DEST/logs" "$AGENTS"
cp "$(dirname "$0")/trigger.sh" "$DEST/trigger.sh"
chmod +x "$DEST/trigger.sh"

plist() {  # $1 label  $2 pipeline  $3.. "HH:MM" times
  local label=$1 job=$2; shift 2
  {
    cat <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$label</string>
  <key>ProgramArguments</key>
  <array><string>/bin/bash</string><string>$DEST/trigger.sh</string><string>$job</string></array>
  <key>StandardOutPath</key><string>$DEST/logs/launchd.out</string>
  <key>StandardErrorPath</key><string>$DEST/logs/launchd.err</string>
  <key>StartCalendarInterval</key>
  <array>
EOF
    for t in "$@"; do
      printf '    <dict><key>Hour</key><integer>%d</integer><key>Minute</key><integer>%d</integer></dict>\n' \
        "$((10#${t%%:*}))" "$((10#${t##*:}))"
    done
    cat <<EOF
  </array>
</dict>
</plist>
EOF
  } > "$AGENTS/$label.plist"
  launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$AGENTS/$label.plist"
  echo "installed $label: $*"
}

plist com.som.trigger.odoo     odoo     00:05 03:05 06:05 09:05 12:05 15:05 18:05 21:05
plist com.som.trigger.anchanto anchanto 00:32
plist com.som.trigger.accurate accurate 00:47
plist com.som.trigger.sellin   sellin   01:30 02:30 03:30 05:00 07:00

echo
echo "Done. Test one now:  bash $DEST/trigger.sh accurate   (then: tail $DEST/logs/*.log)"
