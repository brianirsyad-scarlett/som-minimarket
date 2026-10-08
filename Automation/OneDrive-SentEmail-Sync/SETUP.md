# Sync "Sent Email" to OneDrive (Windows, Task Scheduler + robocopy)

**Instructions for Claude (or a human) running on the target Windows PC.**
Run these steps on this computer itself. Ask the user to confirm the two paths below before changing anything.

## Goal

Every 5 minutes, copy new/changed files from the local folder to the OneDrive folder, so OneDrive uploads them.
One-way (local -> OneDrive). Nothing is deleted from OneDrive.

| | Path (confirm with the user; it may differ on this PC) |
|---|---|
| Source (local) | `D:\SCARLETT_512\SCARLETT-329\SOM\Data\Sent Email` |
| Destination (OneDrive) | `D:\OneDrive - PT. Opto Lumbung Sejahtera\SOM\Sent Email` |

## Steps

1. Check both folders exist (`Test-Path`). The destination is created if missing.
2. Open PowerShell **as Administrator** and run the block below as ONE paste. Edit `$src` / `$dst` first if the paths differ.

```powershell
$src  = 'D:\SCARLETT_512\SCARLETT-329\SOM\Data\Sent Email'
$dst  = 'D:\OneDrive - PT. Opto Lumbung Sejahtera\SOM\Sent Email'
$name = 'SOM Sent Email OneDrive Sync'
$log  = Join-Path $env:USERPROFILE 'sent_email_sync.log'
New-Item -ItemType Directory -Force -Path $dst | Out-Null

$argText = '"{0}" "{1}" /E /XO /R:2 /W:5 /NP /NFL /NDL /LOG+:"{2}" /XF ~$* *.tmp /XD .git __pycache__ .tmp' -f $src, $dst, $log
$action  = New-ScheduledTaskAction -Execute 'robocopy.exe' -Argument $argText
$trigger = New-ScheduledTaskTrigger -Daily -At '00:00'
$trigger.Repetition = (New-ScheduledTaskTrigger -Once -At '00:00' -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration (New-TimeSpan -Hours 23 -Minutes 59)).Repetition
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
$settings  = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 10)

if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) { Unregister-ScheduledTask -TaskName $name -Confirm:$false }
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Principal $principal -Settings $settings | Out-Null
Start-ScheduledTask -TaskName $name
Start-Sleep 20
Get-ScheduledTaskInfo -TaskName $name | Format-List LastRunTime, LastTaskResult, NextRunTime
```

## Verify

- `LastRunTime` is today (not 1999) and `LastTaskResult` is **0-7**. Robocopy exit codes 0-7 are all success
  (1 = files copied, 2 = extra files in destination). 8 or higher = real failure.
- `Get-Content "$env:USERPROFILE\sent_email_sync.log" -Tail 20` shows a run summary.
- Files from the source appear in the OneDrive folder.

## Notes / pitfalls

- Run PowerShell commands that return different object types **separately**; PowerShell hides the output of later ones.
- `LastTaskResult 267011` = "task has not run yet" (not an error).
- `S4U` logon type did not run on the original PC; `Interactive` works. The task runs **only while the user is logged in**.
- Excludes Office lock files (`~$*`), `*.tmp`, `.git` and `__pycache__` folders. No `/MIR`, so deletions are not mirrored.
- The source was ~56 GB / ~3,200 files. If OneDrive space is limited, add `/XD "<folder>"` for big folders.

## Stop / remove

```powershell
Unregister-ScheduledTask -TaskName 'SOM Sent Email OneDrive Sync' -Confirm:$false
```
