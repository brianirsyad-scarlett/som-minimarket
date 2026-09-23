# Registers a Windows Scheduled Task that queues the reports once a day.
#
# Once a day, not hourly: the portal refuses a repeat of the same report inside
# an hour ("Request Download File sudah diajukan dalam 1 jam terakhir"), and
# there is nothing to gain from asking again anyway - the data only moves once
# a day. ..\Mail-ReportLinks is the piece that runs hourly, because it has to
# catch the emailed links inside their ~24h lifetime.
#
# 05:00 by default so the reports are queued, generated and emailed well before
# anyone needs them, and so the whole 24h link window still lies ahead.
[CmdletBinding()]
param(
    [string] $TaskName = "SOM Alfamidi PerfSales Request",
    [string] $At       = "05:00",
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
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$here\run.ps1`"" `
    -WorkingDirectory $here

$trigger = New-ScheduledTaskTrigger -Daily -At $At

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -DontStopOnIdleEnd `
    -MultipleInstances IgnoreNew `
    -RestartCount 2 `
    -RestartInterval (New-TimeSpan -Minutes 30) `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Queues Alfamidi Performance Sales reports; files arrive by email." `
    -RunLevel Limited `
    -Force | Out-Null

Write-Host "Registered '$TaskName' - daily at $At." -ForegroundColor Green
Write-Host "Run it now with:" -ForegroundColor Cyan
Write-Host "  Start-ScheduledTask -TaskName `"$TaskName`"" -ForegroundColor Cyan
