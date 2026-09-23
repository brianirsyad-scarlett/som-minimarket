# Lets the SOM collection tasks actually run on an unattended laptop.
#
# Why: on 2026-09-21 the whole morning B2B pass (fire 07:00, verify 07:10,
# collect 07:15-09:00) silently never executed. The machine was in Modern
# Standby from some time after midnight until 08:24, and every task had:
#
#   WakeToRun                  = False   -> the trigger cannot wake the machine
#   DisallowStartIfOnBatteries = True    -> and would not start unplugged anyway
#
# StartWhenAvailable was already True, but that only catches up a task the
# scheduler considers missed; it did not fire these, and by 08:35 NextRunTime
# had already rolled to the following day. A whole day of by-store data was lost
# without a single error anywhere.
#
#   .\fix_task_wake_settings.ps1            apply
#   .\fix_task_wake_settings.ps1 -Revert    put it back
#
# NOTE: WakeToRun depends on wake timers being enabled in the power plan. On this
# machine they are ON for AC and OFF for battery, so a sleeping laptop wakes for
# these tasks only while plugged in. Changing the battery side is a Windows power
# setting - do that yourself in Settings if you want it, this script won't.
[CmdletBinding()]
param([switch] $Revert)

$ErrorActionPreference = "Stop"

$tasks = @(
    "Alfamart Market Share Download"
    "Alfamidi Market Share Download"
    "Indomaret Market Share Download"
    "Indomaret Daily Reports Download"
    "B2B_Fire_0700"
    "B2B_Verify_0710"
    "B2B_Collect_Morning"
    "B2B_Fire_Evening"
    "B2B_Verify_Evening"
    "B2B_Collect_Evening"
)

foreach ($n in $tasks) {
    $t = Get-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue
    if (-not $t) { Write-Host "skip  $n (not registered)" -ForegroundColor DarkGray; continue }

    # Mutate the task's own settings object so ExecutionTimeLimit, priority and
    # anything else set at registration survive.
    $s = $t.Settings
    $s.WakeToRun                  = -not $Revert
    $s.DisallowStartIfOnBatteries =      $Revert
    $s.StopIfGoingOnBatteries     =      $Revert
    $s.StartWhenAvailable         = $true

    Set-ScheduledTask -TaskName $n -Settings $s | Out-Null
    Write-Host ("{0}  {1}" -f $(if ($Revert) { "reverted" } else { "fixed   " }), $n) -ForegroundColor Green
}

Write-Host ""
Get-ScheduledTask -TaskName $tasks -ErrorAction SilentlyContinue |
    Select-Object TaskName,
        @{n='Wake';           e={$_.Settings.WakeToRun}},
        @{n='RunsOnBattery';  e={-not $_.Settings.DisallowStartIfOnBatteries}},
        @{n='StartWhenAvail'; e={$_.Settings.StartWhenAvailable}} |
    Format-Table -AutoSize
