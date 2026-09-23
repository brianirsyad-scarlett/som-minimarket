# Registers a Windows Scheduled Task that runs run.ps1 once a day.
#
# Unlike the Market Share automations (scheduled at 00:00), these reports are
# generated server-side by Indomaret once a day in the afternoon (WIB) - the
# HAR capture this was built from showed each day's file appearing around
# 14:00-15:00 local time. Running at 00:00 would just re-fetch yesterday's
# already-downloaded file. Default here is 16:00 to reliably catch each day's
# file after it's actually been generated; adjust with -At if that drifts.
[CmdletBinding()]
param(
    [string] $TaskName = "Indomaret Daily Reports Download",
    [string] $At       = "16:00",
    [ValidateSet("Daily", "Weekly")]
    [string] $Cadence  = "Daily",
    [switch] $Remove
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

if ($Remove) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed scheduled task '$TaskName'." -ForegroundColor Green
    return
}

if (-not (Test-Path "$here\.venv\Scripts\python.exe")) { throw "Run setup.ps1 first." }
if (-not (Test-Path "$here\.env")) { throw ".env missing - fill it in and test run.ps1 first." }

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$here\run.ps1`"" `
    -WorkingDirectory $here

switch ($Cadence) {
    "Daily"  { $trigger = New-ScheduledTaskTrigger -Daily -At $At }
    "Weekly" { $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday,Saturday,Sunday -At $At }
}

# A sleeping laptop silently skips the whole run - see Automation\fix_task_wake_settings.ps1.
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -DontStopOnIdleEnd `
    -WakeToRun -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 15) `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Downloads the newest Indomaret Daily Stock / Daily Sell Out / Sell Out reports." `
    -RunLevel Limited `
    -Force | Out-Null

Write-Host "Registered '$TaskName' - $Cadence at $At." -ForegroundColor Green
Write-Host "Check it in Task Scheduler, or run now with:" -ForegroundColor Cyan
Write-Host "  Start-ScheduledTask -TaskName `"$TaskName`"" -ForegroundColor Cyan
