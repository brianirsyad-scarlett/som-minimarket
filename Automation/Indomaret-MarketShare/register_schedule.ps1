# Registers a Windows Scheduled Task that runs run_daily.ps1 every night at 00:00,
# downloading the previous month + current month (every category). Run this
# yourself once, from an elevated PowerShell, after run_daily.ps1 works manually.
[CmdletBinding()]
param(
    [string] $TaskName = "Indomaret Market Share Download",
    [string] $At       = "00:00",
    [ValidateSet("Daily", "Weekly", "Monthly")]
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
if (-not (Test-Path "$here\.env")) { throw ".env missing - fill it in and test run_daily.ps1 first." }

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$here\run_daily.ps1`"" `
    -WorkingDirectory $here

switch ($Cadence) {
    "Daily"   { $trigger = New-ScheduledTaskTrigger -Daily -At $At }
    "Weekly"  { $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday -At $At }
    "Monthly" { $trigger = New-CimInstance -CimClass (
                    Get-CimClass MSFT_TaskMonthlyTrigger root/Microsoft/Windows/TaskScheduler
                ) -ClientOnly -Property @{
                    DaysOfMonth = 3
                    StartBoundary = ([datetime]::Today.ToString("yyyy-MM-dd") + "T" + $At + ":00")
                    Enabled = $true
                } }
}

# A sleeping laptop silently skips the whole run - see Automation\fix_task_wake_settings.ps1.
# 2h, not 1h: Power BI builds each xlsx synchronously and a cold model can take
# minutes per category, so 12 categories plus a retry will not fit in an hour.
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -DontStopOnIdleEnd `
    -WakeToRun -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 10) `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Downloads the Indomaret Market Share report (previous + current month, all categories) into the SOM Raw folder." `
    -RunLevel Limited `
    -Force | Out-Null

Write-Host "Registered '$TaskName' - $Cadence at $At (previous + current month, all categories)." -ForegroundColor Green
Write-Host "Check it in Task Scheduler, or run now with:" -ForegroundColor Cyan
Write-Host "  Start-ScheduledTask -TaskName `"$TaskName`"" -ForegroundColor Cyan
