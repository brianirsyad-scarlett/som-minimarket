# Registers a Windows Scheduled Task that runs run.ps1 hourly.
#
# Links expire in roughly 24 hours, so hourly gives ~24 attempts per link -
# plenty of margin without hammering the portal.
#
# Registered as Interactive (only while you are logged on) because the drop
# folder is populated by OneDrive sync, and OneDrive only runs inside your
# session. A logged-off run would see whatever synced earlier but would never
# pick up a new email, which is the failure mode worth avoiding.
[CmdletBinding()]
param(
    [string] $TaskName = "SOM Emailed Report Links",
    [string] $At       = "06:30",
    [int]    $RepeatMinutes = 60,
    [int]    $ForHours = 18,
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
if (-not (Test-Path "$here\.env")) { throw ".env missing - run setup.ps1, then review it." }

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$here\run.ps1`"" `
    -WorkingDirectory $here

$trigger = New-ScheduledTaskTrigger -Daily -At $At
if ($RepeatMinutes -gt 0) {
    $trigger.Repetition = (New-ScheduledTaskTrigger -Once -At $At `
        -RepetitionInterval (New-TimeSpan -Minutes $RepeatMinutes) `
        -RepetitionDuration (New-TimeSpan -Hours $ForHours)).Repetition
}

$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel Limited

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -DontStopOnIdleEnd `
    -MultipleInstances IgnoreNew `
    -RestartCount 2 `
    -RestartInterval (New-TimeSpan -Minutes 10) `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 45)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Description "Extracts download links from portal report emails (via OneDrive) and fetches the files." `
    -Force | Out-Null

Write-Host "Registered '$TaskName' - every $RepeatMinutes min for $ForHours h from $At." -ForegroundColor Green
Write-Host "Run it now with:" -ForegroundColor Cyan
Write-Host "  Start-ScheduledTask -TaskName `"$TaskName`"" -ForegroundColor Cyan
