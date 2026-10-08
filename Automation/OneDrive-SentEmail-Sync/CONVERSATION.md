# Conversation handoff: Sent Email -> OneDrive sync

Summary of a Claude Code (cloud) session, saved so Claude on another computer can continue. Date: 2026-10-08.
Repo: `brianirsyad-scarlett/som-minimarket`, branch `claude/local-onedrive-sync-zskfze`.

## Original request

"Connect my local folder `D:\SCARLETT_512\SCARLETT-329\SOM\Data\Sent Email` to my OneDrive
`D:\OneDrive - PT. Opto Lumbung Sejahtera\SOM\Sent Email`." Later clarified: **no backup copies, just sync**,
using robocopy (optionally via Python), scheduled in Task Scheduler.

## Constraint

The cloud session cannot access the user's PC. All commands on the PC were pasted by the user into
PowerShell (Admin). A session running on the PC itself (Claude Desktop, or `claude remote-control`) can run them directly.

## What was done (final state on the user's main PC)

- Scheduled task **"SOM Sent Email OneDrive Sync"** is installed and **working**.
  - Runs `robocopy` every 5 min, one-way local -> OneDrive, `/E /XO` (no deletions, skips older files).
  - Excludes `~$*`, `*.tmp`, and folders `.git`, `__pycache__`, `.tmp`.
  - Logs to `C:\Users\BrianRinaldyIrsyad\sent_email_sync.log`.
  - Logon type **Interactive** (runs only while the user is logged in).
- Verified: manual robocopy copied 18 files, skipped 3,246 (already in OneDrive). Task `LastTaskResult = 2`
  (robocopy success: extra dirs exist only in OneDrive: `Claim Syukuran September`, `Offline GT_Sales Plan`).

## Lessons learned (avoid repeating)

- Logon type `S4U` never ran the task (`LastTaskResult 267011`, "not run yet"); `Interactive` worked.
- Running several PowerShell commands that output different object types in one paste hides later output; run them separately.
- Robocopy exit codes 0-7 = success, 8+ = failure.
- Source is about 56 GB / 3,200 files; mostly already in OneDrive.

## Files in this repo folder (`Automation/OneDrive-SentEmail-Sync/`)

| File | Status |
|---|---|
| `SETUP.md` | **Use this**: step-by-step instructions + the working PowerShell block |
| `CONVERSATION.md` | This handoff |
| `sync_sent_email.py` | Early Python/robocopy loop version. **Not used** by the final setup |
| `register_schedule.ps1` | Early task registration for the Python script. **Not used**; never confirmed working |

## Open items

- To set this up on another computer: follow `SETUP.md` (confirm the two paths first).
- Optional cleanup in OneDrive: delete `~$Festive Plan.pptx` and any `.git` / `__pycache__` folders copied before the exclusions were added.
- Optional: remove the unused `sync_sent_email.py` and `register_schedule.ps1` from the branch.
- Optional: exclude large folders (`DatabricksJDBC-3.3.3`, `duckdb_*`, photo/video folders) if OneDrive space is limited.
- No pull request was created (none was requested).

## Prompt to give Claude on the other computer

> Read `Automation/OneDrive-SentEmail-Sync/CONVERSATION.md` and `SETUP.md`, then set up the
> Sent Email -> OneDrive sync on this computer. Confirm the folder paths with me first.
