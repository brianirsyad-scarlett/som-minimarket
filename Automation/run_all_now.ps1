# Re-run the whole SAT / MIDI / IDM collection by hand.
#
# Use this when a scheduled run was missed - e.g. 2026-09-21, when the laptop
# slept through the 07:00 window and the morning B2B pass never executed.
# It triggers the registered Task Scheduler jobs rather than calling the scripts
# directly, so a manual run behaves exactly like a scheduled one (same Python,
# same .env, same logs, same output folders).
#
#   .\run_all_now.ps1                everything: market share, daily reports, B2B fire
#   .\run_all_now.ps1 -Wait          ...then keep polling the mailbox until the files land
#   .\run_all_now.ps1 -Collect       mailbox collection pass only
#   .\run_all_now.ps1 -Only IDM      one chain only (SAT | MIDI | IDM | B2B)
#   .\run_all_now.ps1 -WhatIf        show what would run, start nothing
#
# The two halves behave very differently:
#
#   Market Share + IDM Daily Reports are synchronous - they log in, download,
#   and are done in minutes. Files are on disk when the task finishes.
#
#   B2B sell-out (SAT + MIDI) is asynchronous - the "fire" only *requests* the
#   reports. The portal emails download links minutes to half an hour later, and
#   a separate "collect" pass fetches them. Firing is not the same as having the
#   data. Use -Wait, or let the scheduled collectors pick it up.
#
# Watch out for the portal's 1-hour cooldown: re-firing inside an hour is a
# no-op that reports "Sudah diajukan dalam 1 jam terakhir". That is proof the
# earlier request registered, not a new request.
[CmdletBinding(SupportsShouldProcess)]
param(
    # Accepts several chains: -Only SAT,MIDI re-runs just the two that failed.
    [ValidateSet("ALL","SAT","MIDI","IDM","B2B")]
    [string[]] $Only = @("ALL"),
    [switch] $Collect,
    [switch] $Wait,
    # -Wait polling: how often to re-check the mailbox, and for how long.
    [int] $PollEveryMin = 10,
    [int] $PollForMin   = 60
)

$ErrorActionPreference = "Stop"
$AUTO = Split-Path -Parent $MyInvocation.MyCommand.Path

# Each entry: the task to start, which chain it belongs to, and whether we can
# expect files on disk by the time it exits.
$jobs = @(
    @{ Task="Indomaret Market Share Download";  Chain="IDM";  What="IDM  Market Share (Aug + Sep, 6 categories)" }
    @{ Task="Indomaret Daily Reports Download"; Chain="IDM";  What="IDM  Daily Reports (sell out / daily sell out / stock)" }
    @{ Task="Alfamart Market Share Download";   Chain="SAT";  What="SAT  Market Share (prev + current month)" }
    @{ Task="Alfamidi Market Share Download";   Chain="MIDI"; What="MIDI Market Share (prev + current month)" }
)
$fireTask    = "B2B_Fire_0700"
$verifyTask  = "B2B_Verify_0710"
$collectTask = "B2B_Collect_Morning"

# LastRunTime as it was just before we started each task, so Wait-ForTasks can
# tell "has not started yet" apart from "already finished".
$script:baseline = @{}

function Start-And-Report($name, $label) {
    $t = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    if (-not $t) { Write-Host "   MISSING  $name" -ForegroundColor Red; return $false }
    if ($t.State -eq 'Running') { Write-Host "   already running  $label" -ForegroundColor Yellow; return $true }
    if (-not $PSCmdlet.ShouldProcess($name, "Start")) {
        Write-Host "   would start      $label" -ForegroundColor DarkGray; return $false
    }
    $i = Get-ScheduledTaskInfo -TaskName $name -ErrorAction SilentlyContinue
    $script:baseline[$name] = if ($i) { $i.LastRunTime } else { $null }
    Start-ScheduledTask -TaskName $name
    Write-Host "   started          $label" -ForegroundColor Green
    return $true
}

function Wait-ForTasks($names, $timeoutMin) {
    # Start-ScheduledTask returns immediately and the task can sit in 'Ready' for
    # several seconds before it actually launches. Waiting only on
    # State -eq 'Running' therefore returns instantly and the caller charges on:
    # on 2026-09-21 that made the "verify" pass run in PARALLEL with the fire it
    # was supposed to be verifying - both logged the same 21:44:26 timestamp -
    # which defeats the whole point of verifying inside the cooldown.
    # A task counts as finished only once LastRunTime has moved past the value we
    # recorded before starting it AND it is no longer running.
    $deadline = (Get-Date).AddMinutes($timeoutMin)
    while ((Get-Date) -lt $deadline) {
        $pending = @()
        foreach ($n in $names) {
            $t = Get-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue
            if (-not $t) { continue }
            $i = Get-ScheduledTaskInfo -TaskName $n -ErrorAction SilentlyContinue
            $base    = if ($script:baseline.ContainsKey($n)) { $script:baseline[$n] } else { $null }
            $launched = (-not $base) -or (-not $i) -or ($i.LastRunTime -gt $base)
            if ($t.State -eq 'Running' -or -not $launched) { $pending += $n }
        }
        if (-not $pending) { return $true }
        Write-Host ("   ... waiting on {0}: {1}" -f $pending.Count, ($pending -join ", ")) -ForegroundColor DarkGray
        Start-Sleep -Seconds 15
    }
    Write-Host "   timed out waiting - check status.ps1" -ForegroundColor Yellow
    return $false
}

Write-Host ""
Write-Host ("  Manual SOM run   scope: {0}   {1}" -f ($Only -join ","), (Get-Date -Format "yyyy-MM-dd HH:mm")) -ForegroundColor Cyan
Write-Host ""

# --- collection-only mode --------------------------------------------------
if ($Collect) {
    Write-Host "  Mailbox collection pass (SAT + MIDI):" -ForegroundColor Cyan
    [void](Start-And-Report $collectTask "B2B collect - download whatever links have arrived")
    Write-Host ""
    Write-Host "  Downloads land under ...\Minimarket\<brand>\Sell Out and \Daily Sell Out."
    Write-Host "  Re-run this as often as you like; already-downloaded files are skipped."
    Write-Host ""
    return
}

# --- 1. synchronous downloads ----------------------------------------------
$started = @()
$wanted  = $jobs | Where-Object { $Only -contains "ALL" -or $Only -contains $_.Chain }
if ($wanted) {
    Write-Host "  Direct downloads (files on disk when these finish):" -ForegroundColor Cyan
    foreach ($j in $wanted) {
        if (Start-And-Report $j.Task $j.What) { $started += $j.Task }
        # The nightly schedule staggers these a minute apart; three concurrent
        # Playwright browsers plus a Power BI export is a lot for one laptop.
        if (-not $WhatIfPreference) { Start-Sleep -Seconds 20 }
    }
    Write-Host ""
}

# --- 2. asynchronous B2B request -------------------------------------------
# One task fires both brands, so any of these chains pulls it in - it only ever
# runs once per invocation.
$doB2B = @($Only | Where-Object { $_ -in @("ALL","SAT","MIDI","B2B") }).Count -gt 0
if ($doB2B) {
    Write-Host "  B2B sell-out request (SAT + MIDI - arrives by email, NOT immediately):" -ForegroundColor Cyan
    [void](Start-And-Report $fireTask "B2B fire - request by-store + by-branch reports")
    Write-Host ""
}

# --- 3. optionally wait it out ---------------------------------------------
if ($Wait) {
    if ($started) {
        Write-Host "  Waiting for the direct downloads to finish..." -ForegroundColor Cyan
        [void](Wait-ForTasks $started 60)
        Write-Host ""
    }

    if ($doB2B) {
        Write-Host "  Waiting for the fire to finish, then verifying it registered..." -ForegroundColor Cyan
        [void](Wait-ForTasks @($fireTask) 20)

        # Deliberately inside the 1-hour cooldown: anything the fire registered
        # comes back "Sudah diajukan dalam 1 jam terakhir" (proof), anything it
        # missed gets queued for real (self-heal).
        [void](Start-And-Report $verifyTask "B2B verify - confirm the requests registered")
        [void](Wait-ForTasks @($verifyTask) 20)
        Write-Host ""

        $deadline = (Get-Date).AddMinutes($PollForMin)
        $pass = 0
        while ((Get-Date) -lt $deadline) {
            $pass++
            Write-Host ("  Collection pass {0} at {1}:" -f $pass, (Get-Date -Format "HH:mm")) -ForegroundColor Cyan
            [void](Start-And-Report $collectTask "B2B collect")
            [void](Wait-ForTasks @($collectTask) 30)

            foreach ($b in "alfamart","alfamidi") {
                $log = Join-Path (Split-Path $AUTO -Parent) "Data\Sent Email\_collect_$b.log"
                if (Test-Path $log) {
                    $last = Get-Content $log | Select-String "Done: \d+ file" | Select-Object -Last 1
                    if ($last) { Write-Host ("     {0,-9} {1}" -f $b, $last.Line.Trim()) }
                }
            }
            if ((Get-Date).AddMinutes($PollEveryMin) -ge $deadline) { break }
            Write-Host ("     next pass in {0} min" -f $PollEveryMin) -ForegroundColor DarkGray
            Start-Sleep -Seconds ($PollEveryMin * 60)
        }
        Write-Host ""
    }
}

Write-Host "  Next steps:" -ForegroundColor Yellow
Write-Host "   .\status.ps1            see how every pipeline ended up"
Write-Host "   .\status.ps1 -Detail    plus the tail of each log"
if ($doB2B -and -not $Wait) {
    Write-Host "   .\run_all_now.ps1 -Collect    once the emails land (minutes to ~30 min)"
}
Write-Host ""
