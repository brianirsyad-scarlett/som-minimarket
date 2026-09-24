# Registers the split B2B schedule. ONE pass a day, in the morning - the user
# does not want a night run for Alfamart or Alfamidi:
#
#   B2B_Fire_0700       09:00 daily            fire the report requests, exit (~4 min)
#   B2B_Verify_0710     09:10 daily            re-fire inside the cooldown to prove it registered
#   B2B_Collect_Morning 11:00, 12:00, 13:00    one mailbox poll each, exit in seconds
#
# Collection is hourly rather than every 15 min because the emails are not fast:
# measured on 2026-09-22, a 07:00 fire produced emails from 07:08 through 08:03,
# so roughly 8-65 min after firing. The first poll therefore waits 2 hours after
# the 09:00 fire, by which point everything should have landed; 12:00 and 13:00
# are backstops for a slow one.
#
# Moved from 07:00 to 09:00 on 2026-09-22 at the user's request. The task NAMES
# still say 0700/0710 - renaming them would break the rollback commands below,
# status.ps1 and preflight.ps1 for no gain. Trust the trigger, not the name.
#
# This replaces the single B2B_Daily_0700_Jakarta task, which fired requests and
# then held a console open polling for a fixed 8 x 15 min with no early exit.
# Measured on 2026-09-20: all files had arrived by 07:56, but it kept polling an
# empty mailbox until 09:20 - ~90 wasted minutes per brand.
#
# The old task is DISABLED, not deleted, so rollback is one command:
#   Enable-ScheduledTask  -TaskName "B2B_Daily_0700_Jakarta"
#   Unregister-ScheduledTask -TaskName "B2B_Fire_0700"       -Confirm:$false
#   Unregister-ScheduledTask -TaskName "B2B_Collect_Morning" -Confirm:$false
#
# NOTE: tasks on this machine must run under the Interactive principal - S4U and
# SYSTEM register fine but silently never execute.
# !! SINCE 2026-09-24 THE CLOUD DOES THIS. Alfamart/Alfamidi sell out is
# requested, verified and collected by GitHub Actions
# (brianirsyad-scarlett/som-minimarket-automation: 07:05 fire, +10 min verify,
# 08:05 / 09:05 collect). The laptop tasks this script registers were DISABLED.
# Re-registering them would request every report twice a day, so the script
# refuses unless you pass -AlsoOnLaptop on purpose.
[CmdletBinding()]
param(
    [switch] $AlsoOnLaptop,
    [string] $FireAt    = "09:00",
    [string] $CollectAt = "11:00",
    [int]    $EveryMin  = 60,
    # 120 min of repetition from $CollectAt at $EveryMin = three polls:
    # 11:00, 12:00, 13:00. Task Scheduler fires the start time itself plus one
    # repetition per interval across the duration, so this is 2h, not 3h.
    [int]    $ForMin    = 120,
    # OPT-IN, OFF BY DEFAULT. The user does not want a night run for either
    # brand - reports are requested once a day at 07:00. Passing -Evening brings
    # back a second fire/verify/collect pass; $EveningBrands narrows which brands
    # it fires. Do not enable it without being asked.
    [switch] $Evening,
    [string] $EveningFireAt    = "20:00",
    [string] $EveningCollectAt = "20:15",
    [int]    $EveningForMin    = 75,   # 20:15 -> 21:30
    # Second fire pass, deliberately INSIDE the portal's 1-hour cooldown so that
    # anything the first pass registered comes back "Sudah diajukan dalam 1 jam
    # terakhir" (proof) while anything it missed gets queued for real (self-heal).
    [string] $VerifyAt        = "09:10",
    [string] $EveningVerifyAt = "20:10",
    # Only used when -Evening is explicitly passed.
    [string[]] $EveningBrands = @("alfamart"),
    [switch] $Rollback
)

$ErrorActionPreference = "Stop"
if (-not $AlsoOnLaptop -and -not $Rollback) {
    throw ("Alfamart/Alfamidi sell out now runs in GitHub Actions (som-minimarket-automation). " +
           "Re-registering the laptop tasks would fire every report twice. Pass -AlsoOnLaptop to do it anyway.")
}
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$py   = "C:\Users\BrianRinaldyIrsyad\AppData\Local\Python\bin\python.exe"
$runner = Join-Path $here "run_daily_all.py"

if ($Rollback) {
    foreach ($n in @("B2B_Fire_0700","B2B_Collect_Morning","B2B_Fire_Evening","B2B_Collect_Evening",
                     "B2B_Verify_0710","B2B_Verify_Evening")) {
        if (Get-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue) {
            Unregister-ScheduledTask -TaskName $n -Confirm:$false
            Write-Host "Removed $n" -ForegroundColor Yellow
        }
    }
    Enable-ScheduledTask -TaskName "B2B_Daily_0700_Jakarta" | Out-Null
    Write-Host "Re-enabled B2B_Daily_0700_Jakarta - back to the original single task." -ForegroundColor Green
    return
}

if (-not (Test-Path $py))     { throw "Python not found at $py" }
if (-not (Test-Path $runner)) { throw "run_daily_all.py not found at $runner" }

$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
# -WakeToRun and the battery flags are not optional on a laptop. Without them the
# 2026-09-21 morning pass never ran at all: the machine was in Modern Standby, no
# task was allowed to wake it, and none would have started on battery either.
# StartWhenAvailable alone did not catch it up - NextRunTime just rolled to the
# next day and the day's data was lost with no error logged anywhere.
$settings  = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopOnIdleEnd `
                -WakeToRun -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
                -ExecutionTimeLimit (New-TimeSpan -Hours 1)

# --- 1. fire ---------------------------------------------------------------
$fireAction = New-ScheduledTaskAction -Execute $py `
    -Argument "`"$runner`" --fire-only" -WorkingDirectory $here
Register-ScheduledTask -TaskName "B2B_Fire_0700" `
    -Action $fireAction -Trigger (New-ScheduledTaskTrigger -Daily -At $FireAt) `
    -Principal $principal -Settings $settings `
    -Description "Fires the Alfamart/Alfamidi by-store + by-branch report requests, then exits. Collection is a separate task." `
    -Force | Out-Null
Write-Host "Registered B2B_Fire_0700 - daily at $FireAt" -ForegroundColor Green

# --- 2. collect (repeating) -------------------------------------------------
$collectTrigger = New-ScheduledTaskTrigger -Daily -At $CollectAt
$collectTrigger.Repetition = (New-ScheduledTaskTrigger -Once -At $CollectAt `
    -RepetitionInterval (New-TimeSpan -Minutes $EveryMin) `
    -RepetitionDuration (New-TimeSpan -Minutes $ForMin)).Repetition

$collectAction = New-ScheduledTaskAction -Execute $py `
    -Argument "`"$runner`" --collect-only" -WorkingDirectory $here
Register-ScheduledTask -TaskName "B2B_Collect_Morning" `
    -Action $collectAction -Trigger $collectTrigger `
    -Principal $principal -Settings $settings `
    -Description "Polls the mailbox for Alfamart/Alfamidi export links, downloads and processes anything new. Exits in seconds when there is nothing." `
    -Force | Out-Null
Write-Host "Registered B2B_Collect_Morning - every $EveryMin min from $CollectAt for $ForMin min" -ForegroundColor Green

# --- 1b. morning verify pass (inside the cooldown) --------------------------
$verifyAction = New-ScheduledTaskAction -Execute $py `
    -Argument "`"$runner`" --verify" -WorkingDirectory $here
Register-ScheduledTask -TaskName "B2B_Verify_0710" `
    -Action $verifyAction -Trigger (New-ScheduledTaskTrigger -Daily -At $VerifyAt) `
    -Principal $principal -Settings $settings `
    -Description "Second fire pass inside the 1-hour cooldown: proves the 07:00 requests registered, and re-queues any the portal never received." `
    -Force | Out-Null
Write-Host "Registered B2B_Verify_0710 - daily at $VerifyAt" -ForegroundColor Green

# --- 2b. optional evening pass ---------------------------------------------
if ($Evening) {
    $evBrandArg = "--brands " + ($EveningBrands -join " ")

    $evFireAction = New-ScheduledTaskAction -Execute $py `
        -Argument "`"$runner`" --fire-only $evBrandArg" -WorkingDirectory $here
    Register-ScheduledTask -TaskName "B2B_Fire_Evening" `
        -Action $evFireAction -Trigger (New-ScheduledTaskTrigger -Daily -At $EveningFireAt) `
        -Principal $principal -Settings $settings `
        -Description "Evening re-request of the $($EveningBrands -join '/') reports, so the current by-store period reflects a full day of sales." `
        -Force | Out-Null
    Write-Host "Registered B2B_Fire_Evening - daily at $EveningFireAt ($($EveningBrands -join ', '))" -ForegroundColor Green

    $evVerifyAction = New-ScheduledTaskAction -Execute $py `
        -Argument "`"$runner`" --verify $evBrandArg" -WorkingDirectory $here
    Register-ScheduledTask -TaskName "B2B_Verify_Evening" `
        -Action $evVerifyAction -Trigger (New-ScheduledTaskTrigger -Daily -At $EveningVerifyAt) `
        -Principal $principal -Settings $settings `
        -Description "Second fire pass inside the 1-hour cooldown: proves the 20:00 $($EveningBrands -join '/') requests registered, and re-queues any the portal never received." `
        -Force | Out-Null
    Write-Host "Registered B2B_Verify_Evening - daily at $EveningVerifyAt ($($EveningBrands -join ', '))" -ForegroundColor Green

    $evCollectTrigger = New-ScheduledTaskTrigger -Daily -At $EveningCollectAt
    $evCollectTrigger.Repetition = (New-ScheduledTaskTrigger -Once -At $EveningCollectAt `
        -RepetitionInterval (New-TimeSpan -Minutes $EveryMin) `
        -RepetitionDuration (New-TimeSpan -Minutes $EveningForMin)).Repetition

    $evCollectAction = New-ScheduledTaskAction -Execute $py `
        -Argument "`"$runner`" --collect-only" -WorkingDirectory $here
    Register-ScheduledTask -TaskName "B2B_Collect_Evening" `
        -Action $evCollectAction -Trigger $evCollectTrigger `
        -Principal $principal -Settings $settings `
        -Description "Evening mailbox collection for the Alfamart/Alfamidi export links." `
        -Force | Out-Null
    Write-Host "Registered B2B_Collect_Evening - every $EveryMin min from $EveningCollectAt for $EveningForMin min" -ForegroundColor Green
}

# --- 3. stand the old task down --------------------------------------------
$old = Get-ScheduledTask -TaskName "B2B_Daily_0700_Jakarta" -ErrorAction SilentlyContinue
if ($old) {
    Disable-ScheduledTask -TaskName "B2B_Daily_0700_Jakarta" | Out-Null
    Write-Host "Disabled B2B_Daily_0700_Jakarta (kept for rollback, not deleted)." -ForegroundColor Yellow
}
