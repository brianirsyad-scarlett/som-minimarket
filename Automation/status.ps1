# One view of every SOM minimarket collection pipeline - SAT, MIDI and IDM.
#
# Logs live next to the script that writes them (four Automation\* folders plus
# Data\Sent Email), so this reads them in place rather than moving anything.
#
#   .\status.ps1            one-line-per-pipeline summary
#   .\status.ps1 -Detail    plus the tail of each log
#   .\status.ps1 -Days 3    look back further than today
[CmdletBinding()]
param(
    [switch] $Detail,
    [int]    $Days = 1
)

$ErrorActionPreference = "Stop"
$SOM  = "D:\SCARLETT_512\SCARLETT-329\SOM"
$AUTO = Join-Path $SOM "Automation"
$MAIL = Join-Path $SOM "Data\Sent Email"
$since = (Get-Date).Date.AddDays(-1 * ($Days - 1))

# kind: 'py'  -> "2026-09-20 00:06:55  INFO  Done. 12 saved, 0 failed."
# kind: 'b2b' -> "[2026-09-20 07:00:01] ..." + "--- Done: 32 file(s) downloaded ---"
$pipelines = @(
    @{ Group="IDM";  Name="Market Share";   Task="Indomaret Market Share Download";   Log=@("$AUTO\Indomaret-MarketShare\run.log");   Kind="py"  }
    @{ Group="IDM";  Name="Daily Reports";  Task="Indomaret Daily Reports Download";  Log=@("$AUTO\Indomaret-DailyReports\run.log");  Kind="py"  }
    @{ Group="SAT";  Name="Market Share";   Task="Alfamart Market Share Download";    Log=@("$AUTO\Alfamart-MarketShare\run.log");    Kind="py"  }
    @{ Group="MIDI"; Name="Market Share";   Task="Alfamidi Market Share Download";    Log=@("$AUTO\Alfamidi-MarketShare\run.log");    Kind="py"  }
    @{ Group="B2B";  Name="Fire (AM)";      Task="B2B_Fire_0700";                     Log=@("$MAIL\_fire_alfamart.log","$MAIL\_fire_alfamidi.log");       Kind="b2b" }
    @{ Group="B2B";  Name="Collect (AM)";   Task="B2B_Collect_Morning";               Log=@("$MAIL\_collect_alfamart.log","$MAIL\_collect_alfamidi.log"); Kind="b2b" }
    @{ Group="B2B";  Name="Verify (AM)";    Task="B2B_Verify_0710";                   Log=@("$MAIL\_verify_alfamart.log","$MAIL\_verify_alfamidi.log");   Kind="b2b" }
    # There are no evening B2B rows: the user wants one pass a day (09:00) for
    # both brands. B2B_Fire_Evening / _Verify_Evening / _Collect_Evening were
    # unregistered on 2026-09-21. register_b2b_schedule.ps1 -Evening brings them
    # back if that ever changes.
    @{ Group="B2B";  Name="(old combined)"; Task="B2B_Daily_0700_Jakarta";            Log=@("$MAIL\_daily_alfamart.log","$MAIL\_daily_alfamidi.log");     Kind="b2b" }
)

# Failures are reported from the LATEST run, not summed across the window: a
# re-run that fixes the problem has to clear the alarm. Before 2026-09-21 this
# summed every run in the window, so IDM Market Share showed "FAILED x5" from
# the 00:28 run even though the 09:49 re-run finished "12 saved, 0 failed".
# The window total is kept as WindowFailed and reported as "ok (recovered)".
function Get-Outcome($paths, $kind, $since) {
    $files = 0; $latestFailed = 0; $windowFailed = 0; $seen = $false; $newest = $null
    foreach ($p in $paths) {
        if (-not (Test-Path $p)) { continue }
        $item = Get-Item $p
        if (-not $newest -or $item.LastWriteTime -gt $newest) { $newest = $item.LastWriteTime }
        # Last run summary in THIS file. One pipeline can span two brand logs
        # (alfamart + alfamidi), and a clean alfamidi must not mask a failed
        # alfamart - so each log contributes its own latest outcome.
        $lastInFile = $null
        foreach ($line in Get-Content $p -ErrorAction SilentlyContinue) {
            if ($kind -eq 'py') {
                if ($line -match '^(\d{4}-\d{2}-\d{2}) .*Done\.\s+(\d+)\s+saved,\s+(\d+)\s+failed') {
                    if ([datetime]$Matches[1] -ge $since) {
                        $seen = $true; $files += [int]$Matches[2]
                        $windowFailed += [int]$Matches[3]; $lastInFile = [int]$Matches[3]
                    }
                }
            } else {
                if ($line -match 'Done:\s*(\d+)\s*file\(s\)\s*downloaded') {
                    $seen = $true; $files += [int]$Matches[1]; $lastInFile = 0
                }
                elseif ($line -match 'fire-only complete') { $seen = $true; $lastInFile = 0 }
                # A fire that never reached the portal means no email will ever
                # arrive. Until 2026-09-21 this was invisible: run_pipeline.py
                # logged "(continuing)" and exited 0, so a total DNS outage
                # showed up here as a healthy "ok".
                elseif ($line -match 'fire-only FAILED') { $seen = $true; $windowFailed++; $lastInFile = 1 }
            }
        }
        if ($null -ne $lastInFile) { $latestFailed += $lastInFile }
    }
    [pscustomobject]@{ Files=$files; Failed=$latestFailed; WindowFailed=$windowFailed; Seen=$seen; LogTime=$newest }
}

$rows = foreach ($p in $pipelines) {
    $t = Get-ScheduledTask -TaskName $p.Task -ErrorAction SilentlyContinue
    $i = if ($t) { Get-ScheduledTaskInfo -TaskName $p.Task } else { $null }
    $o = Get-Outcome $p.Log $p.Kind $since

    $sched = if ($t) {
        $trg = $t.Triggers | Select-Object -First 1
        $at  = ([datetime]$trg.StartBoundary).ToString("HH:mm")
        if ($trg.Repetition.Interval) { "$at +rep" } else { $at }
    } else { "-" }

    # Task Scheduler status codes that are not failures:
    #   267011 (0x41303) never run yet   267009 (0x41301) currently running
    #   267010 (0x41302) disabled        267014 (0x41306) terminated by user
    $neverRun = $i -and $i.LastTaskResult -eq 267011
    $benign   = @(0, 267009, 267010, 267011)

    $status =
        if (-not $t)                            { "NO TASK" }
        elseif ($t.State -eq 'Disabled')        { "disabled" }
        elseif ($neverRun)                      { "not yet run" }
        elseif ($i.LastTaskResult -eq 267009)   { "running" }
        elseif ($o.Failed -gt 0)                { "FAILED x$($o.Failed)" }
        elseif ($benign -notcontains $i.LastTaskResult) { "exit $($i.LastTaskResult)" }
        # Latest run was clean but something failed earlier today - worth seeing,
        # not worth alarming about.
        elseif ($o.Seen -and $o.WindowFailed -gt 0) { "ok (recovered $($o.WindowFailed))" }
        elseif ($o.Seen)                        { "ok" }
        else                                    { "no data" }

    [pscustomobject]@{
        Pipeline = "{0,-4} {1}" -f $p.Group, $p.Name
        Sched    = $sched
        State    = if ($t) { $t.State } else { "-" }
        # Task Scheduler reports 1999-11-30 for "never ran"
        LastRun  = if ($i -and $i.LastRunTime -and $i.LastRunTime.Year -gt 2000) {
                       $i.LastRunTime.ToString("MM-dd HH:mm")
                   } else { "never" }
        Files    = if ($o.Seen) { $o.Files } else { "-" }
        Status   = $status
        LogAge   = if ($o.LogTime) { "{0,5:N0}m" -f ((Get-Date) - $o.LogTime).TotalMinutes } else { "-" }
        _Detail  = $p
    }
}

Write-Host ""
Write-Host ("  SOM collection status   window: last {0} day(s)   {1}" -f $Days, (Get-Date -Format "yyyy-MM-dd HH:mm")) -ForegroundColor Cyan
Write-Host ""
$rows | Select-Object Pipeline, Sched, State, LastRun, Files, Status, LogAge | Format-Table -AutoSize

$bad = $rows | Where-Object { $_.Status -like "FAILED*" -or $_.Status -like "exit *" -or $_.Status -eq "NO TASK" -or $_.Status -eq "no data" }
if ($bad) {
    Write-Host "  Needs attention:" -ForegroundColor Red
    $bad | ForEach-Object { Write-Host "   - $($_.Pipeline): $($_.Status)" -ForegroundColor Red }
    Write-Host ""
}

# Raw files waiting on a manual converter run - the one step still not automated.
$pending = @(
    @{ What="SAT  Market Share Raw";  Path="$SOM\Data\Report\Sales\Minimarket\Alfamart\Market Share\Raw";  Filter="*.xlsx" }
    @{ What="MIDI Market Share Raw";  Path="$SOM\Data\Report\Sales\Minimarket\Alfamidi\Market Share\Raw";  Filter="*.xlsx" }
    @{ What="IDM  Market Share Raw";  Path="$SOM\Data\Report\Sales\Minimarket\Indomaret\Market Share\Raw"; Filter="*.xlsx" }
    @{ What="IDM  Stock (zip)";       Path="$SOM\Data\Report\Sales\Minimarket\Indomaret\Stock";            Filter="*.zip"  }
    @{ What="IDM  Daily Sell Out";    Path="$SOM\Data\Report\Sales\Minimarket\Indomaret\Daily Sell Out";   Filter="*.zip"  }
    @{ What="IDM  Sell Out (zip)";    Path="$SOM\Data\Report\Sales\Minimarket\Indomaret\Sell Out";         Filter="*.zip"  }
)
# The verify pass runs inside the portal's 1-hour cooldown, so its wording tells
# us whether the preceding fire actually registered:
#   "Sudah diajukan dalam 1 jam terakhir" -> the fire registered (good)
#   "akan dikirim melalui email"          -> the fire was MISSED, verify queued it
foreach ($b in @("alfamart","alfamidi")) {
    $vlog = "$MAIL\_verify_$b.log"
    if (-not (Test-Path $vlog)) { continue }
    $txt       = Get-Content $vlog -Raw
    $confirmed = ([regex]::Matches($txt, "Sudah diajukan dalam 1 jam terakhir")).Count
    $missed    = ([regex]::Matches($txt, "akan dikirim melalui email")).Count
    if ($confirmed -or $missed) {
        if (-not $shownVerifyHeader) {
            Write-Host "  Verify pass (did the fire register?):" -ForegroundColor Yellow
            $shownVerifyHeader = $true
        }
        $colour = if ($missed -gt 0) { "Red" } else { "Green" }
        Write-Host ("   {0,-9} {1,3} confirmed queued, {2,3} were missed and re-queued" -f $b, $confirmed, $missed) -ForegroundColor $colour
    }
}
if ($shownVerifyHeader) { Write-Host "" }

# Since 2026-09-24 Alfamart/Alfamidi sell out is requested and collected in the
# cloud (GitHub som-minimarket-automation) and lands in the DRAFT bucket path,
# not on this disk - so freshness is read from GCS. As before, only the age of
# the newest file that actually arrived is honest: "the workflow ran" is not
# "data arrived" (every row read "ok" on 2026-09-20..22 with nothing delivered).
Write-Host "  Sell-out data freshness, cloud draft (gs://bucket_som/sales_parquet/raw/minimarket):" -ForegroundColor Yellow
$gcsCheck = @'
import sys
from datetime import datetime, timezone
try:
    from google.cloud import storage
    b = storage.Client().bucket("bucket_som")
    for brand in ("alfamart", "alfamidi"):
        for rep in ("sell_out_branch", "sell_out_store"):
            blobs = list(b.list_blobs(prefix=f"sales_parquet/raw/minimarket/{brand}/{rep}/"))
            if not blobs:
                print(f"{brand}|{rep}|none|")
                continue
            t = max(x.updated for x in blobs)
            age = (datetime.now(timezone.utc) - t).total_seconds() / 3600
            print(f"{brand}|{rep}|{age:.1f}|{t.astimezone().strftime('%m-%d %H:%M')}")
except Exception as e:
    print(f"ERROR|{type(e).__name__}: {e}")
'@
$staleFound = $false
# Read with the service-account key (the same one the cloud uses as GCP_SA_KEY),
# not gcloud's application-default login: that personal login expires and then
# needs an interactive re-auth - it did on 2026-09-24 - and a monitor must not
# go blind because somebody's browser session lapsed.
$saKey = "$SOM\Data\Sent Email\sales-som datawarehouse 490008.json"
$prevCreds = $env:GOOGLE_APPLICATION_CREDENTIALS
if (Test-Path $saKey) { $env:GOOGLE_APPLICATION_CREDENTIALS = $saKey }
try { $lines = $gcsCheck | python - 2>$null }
finally { $env:GOOGLE_APPLICATION_CREDENTIALS = $prevCreds }
foreach ($l in $lines) {
    $f = $l -split '\|'
    if ($f[0] -eq 'ERROR') { Write-Host "   could not read GCS: $($f[1])" -ForegroundColor Red; $staleFound = $true; continue }
    if ($f[2] -eq 'none') {
        Write-Host ("   {0,-9} {1,-16} nothing yet" -f $f[0], $f[1]) -ForegroundColor Yellow
        continue
    }
    $ageH = [double]$f[2]
    # One fire a day, so anything past ~36h means deliveries have stopped.
    $colour = if ($ageH -gt 36) { "Red" } elseif ($ageH -gt 20) { "Yellow" } else { "Green" }
    if ($ageH -gt 36) { $staleFound = $true }
    Write-Host ("   {0,-9} {1,-16} newest {2}  ({3:N0}h ago)" -f $f[0], $f[1], $f[3], $ageH) -ForegroundColor $colour
}
if ($staleFound) {
    Write-Host "   -> nothing new for a while. Check, in order:" -ForegroundColor Red
    Write-Host "      1. GitHub Actions in som-minimarket-automation - did the 07:05 fire and 08:05/09:05 collect run green?" -ForegroundColor Red
    Write-Host "      2. GitHub Issues there - are B2B|... issues being created? If not, the Power Automate flows stopped." -ForegroundColor Red
    Write-Host "      3. Outlook Inbox\Alfamart and \Alfamidi - are report emails still arriving at all?" -ForegroundColor Red
}
Write-Host ""

Write-Host "  Unconverted raw files (converter scripts are still manual):" -ForegroundColor Yellow
foreach ($q in $pending) {
    if (Test-Path $q.Path) {
        $n = @(Get-ChildItem $q.Path -Filter $q.Filter -File -ErrorAction SilentlyContinue).Count
        $newest = Get-ChildItem $q.Path -Filter $q.Filter -File -ErrorAction SilentlyContinue |
                  Sort-Object LastWriteTime -Descending | Select-Object -First 1
        $when = if ($newest) { $newest.LastWriteTime.ToString("MM-dd HH:mm") } else { "-" }
        "   {0,-24} {1,4} file(s)   newest {2}" -f $q.What, $n, $when
    }
}
Write-Host ""

if ($Detail) {
    foreach ($r in $rows) {
        Write-Host ("  --- {0} ---" -f $r.Pipeline) -ForegroundColor Cyan
        foreach ($p in $r._Detail.Log) {
            if (Test-Path $p) {
                Write-Host "   $p" -ForegroundColor DarkGray
                Get-Content $p -Tail 4 | ForEach-Object { "     $_" }
            }
        }
        Write-Host ""
    }
}
