# Registers a Windows Scheduled Task that runs run_daily.ps1 every morning:
# rebuild the previous + current month workbooks, then re-export the CSVs and
# combined_sales.parquet. Run this yourself once, after run_daily.ps1 works
# manually.
#
# Time it AFTER the jobs that refresh the inputs (the Anchanto quarter export,
# the Odoo export and Master Data Sales.xlsx). If this job reads a source while
# another process is still writing it, it will build from a half-written file.
[CmdletBinding()]
param(
    [string] $TaskName = "Sell In Report",
    [string] $At       = "02:00",
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

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -DontStopOnIdleEnd `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 15) `
    -ExecutionTimeLimit (New-TimeSpan -Hours 4)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Rebuilds Data\Report\Sales\<year>\<year> MM Mon.xlsx (previous + current month) from E-Stock, Odoo, Accurate, Anchanto and Master Data Sales - the Power Query refresh, done headlessly - then re-exports the Sales csv folder and combined_sales.parquet." `
    -RunLevel Limited `
    -Force | Out-Null

Write-Host "Registered '$TaskName' - $Cadence at $At (previous + current month)." -ForegroundColor Green
Write-Host "Check it in Task Scheduler, or run now with:" -ForegroundColor Cyan
Write-Host "  Start-ScheduledTask -TaskName `"$TaskName`"" -ForegroundColor Cyan
