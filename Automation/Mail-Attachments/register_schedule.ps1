# Registers a Windows Scheduled Task that runs run.ps1 on a schedule.
#
# Important difference from the other automations here: this one drives Outlook
# over COM, which needs an interactive desktop session. The task is therefore
# registered to run ONLY when you are logged on (-LogonType Interactive). A
# "run whether user is logged on or not" task lands in session 0, where there is
# no Outlook profile to attach to, and every run fails with an RPC error.
#
# Default is hourly during working hours rather than once a day, since the point
# is to pick up report mail not long after it arrives.
[CmdletBinding()]
param(
    [string] $TaskName = "SOM Mail Attachment Harvester",
    [string] $At       = "07:00",
    [int]    $RepeatMinutes = 60,   # 0 disables the intra-day repeat
    [int]    $ForHours = 12,
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
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Description "Saves new Outlook attachments into the SOM Data tree via MAPI/COM." `
    -Force | Out-Null

$every = if ($RepeatMinutes -gt 0) { "every $RepeatMinutes min for $ForHours h from $At" } else { "daily at $At" }
Write-Host "Registered '$TaskName' - $every (only while you are logged on)." -ForegroundColor Green
Write-Host "Run it now with:" -ForegroundColor Cyan
Write-Host "  Start-ScheduledTask -TaskName `"$TaskName`"" -ForegroundColor Cyan
