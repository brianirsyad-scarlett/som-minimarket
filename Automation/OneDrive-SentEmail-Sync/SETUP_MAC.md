# Sync "Sent Email" to OneDrive on macOS (launchd + rsync)

**Instructions for Claude (or a human) running on the iMac itself.**
Windows version is in `SETUP.md` (robocopy + Task Scheduler). macOS has neither, so this uses `rsync` + `launchd`.
Read `CONVERSATION.md` first for context.

## Goal

Every 5 minutes, copy new/changed files from the local "Sent Email" folder to the OneDrive folder, so OneDrive uploads them.
One-way (local -> OneDrive). Nothing is deleted from OneDrive.

## Step 0: find and CONFIRM the paths with the user (do not guess)

On the Windows PC they were:
- Source: `D:\SCARLETT_512\SCARLETT-329\SOM\Data\Sent Email`
- OneDrive: `D:\OneDrive - PT. Opto Lumbung Sejahtera\SOM\Sent Email`

On the iMac, find the real ones:
```bash
ls -d ~/Library/CloudStorage/OneDrive* ~/OneDrive* 2>/dev/null     # OneDrive root (needs the OneDrive app signed in)
find ~ -maxdepth 6 -type d -name "Sent Email" 2>/dev/null          # local source candidates
```
Ask the user which is the source and which is the OneDrive destination. Stop if OneDrive is not installed / signed in.

## Step 1: install (set SRC and DST first)

```bash
SRC="/path/to/local/Sent Email"
DST="$HOME/Library/CloudStorage/OneDrive-<Org>/SOM/Sent Email"

[ -d "$SRC" ] || { echo "Source missing"; exit 1; }
mkdir -p "$DST" "$HOME/bin"

cat > "$HOME/bin/sync_sent_email.sh" <<SCRIPT
#!/bin/bash
# One-way copy, no deletions, skips files newer in destination.
/usr/bin/rsync -rtu --exclude='~\$*' --exclude='*.tmp' --exclude='.git' --exclude='__pycache__' --exclude='.DS_Store' \\
  "$SRC/" "$DST/"
echo "\$(date '+%F %T') rsync exit \$?" >> "\$HOME/sent_email_sync.log"
SCRIPT
chmod +x "$HOME/bin/sync_sent_email.sh"

PLIST="$HOME/Library/LaunchAgents/com.som.sentemail-onedrive-sync.plist"
cat > "$PLIST" <<EOP
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.som.sentemail-onedrive-sync</string>
  <key>ProgramArguments</key><array><string>$HOME/bin/sync_sent_email.sh</string></array>
  <key>StartInterval</key><integer>300</integer>
  <key>RunAtLoad</key><true/>
</dict></plist>
EOP

launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null
launchctl bootstrap "gui/$(id -u)" "$PLIST"
launchctl kickstart -k "gui/$(id -u)/com.som.sentemail-onedrive-sync"
sleep 10; tail -3 "$HOME/sent_email_sync.log"
```

## Verify

- `tail ~/sent_email_sync.log` shows `rsync exit 0` (non-zero = failure).
- New files appear in the OneDrive folder.
- `launchctl list | grep com.som.sentemail` lists the job.

## Pitfalls

- **Full Disk Access:** if the log says `Operation not permitted`, add `/bin/bash` (and `/usr/bin/rsync`) in
  System Settings -> Privacy & Security -> Full Disk Access, then re-run `kickstart`.
- Runs only while the user is logged in (LaunchAgent). Catches up after login.
- Do not use `rsync --delete` unless the user wants deletions mirrored.
- Never copy credentials/browser profiles into OneDrive; see `imac/DATA_INVENTORY.md` for local-only items.

## Stop / remove

```bash
launchctl bootout "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.som.sentemail-onedrive-sync.plist"
rm "$HOME/Library/LaunchAgents/com.som.sentemail-onedrive-sync.plist" "$HOME/bin/sync_sent_email.sh"
```
