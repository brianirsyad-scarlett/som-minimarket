# Verifies the two upstream sources actually refreshed today, and force-runs
# whichever did not, before the monthly workbooks and the parquet are built.
#
# Both upstream jobs have been observed reporting success while delivering
# nothing (Anchanto launched no process at all on 2026-09-17/18 yet still
# returned exit 0), and the Odoo routine loses its midnight slot whenever the
# previous session is still open. Nothing downstream notices: the monthly build
# happily rebuilds from a stale source and the parquet inherits it, timestamps
# all looking current. This checks the sources themselves.
#
# A source counts as fresh when its file was written at or after that job's
# scheduled start time today. The grace period only decides when we are allowed
# to judge - not how new the file must be.
param(
    [int]$GraceMinutes = 30,
    [int]$AnchantoTimeoutMinutes = 90
)

$ErrorActionPreference = "Continue"

$PY = "C:\Users\BrianRinaldyIrsyad\AppData\Local\Python\pythoncore-3.14-64\python.exe"
$ODOO_DIR = "D:\SCARLETT_512\SCARLETT-329\SOM\Data\Report\Sales\Odoo"
$ODOO_MASTER = Join-Path $ODOO_DIR "Odoo Report.xlsx"
$ANCHANTO_DIR = "D:\SCARLETT_512\SCARLETT-329\SOM\Data\Report\Sales\Anchanto Report"
$ANCHANTO_TASK = "Anchanto Report"

# Scheduled start times, as hours past local midnight.
$ODOO_HOUR = 0
$ANCHANTO_HOUR = 1

$now = Get-Date
$today = $now.Date

# Must not write to the success stream: these functions return booleans, and an
# emitted string would be folded into that return value, making every result a
# non-empty array - which is always truthy, so a STALE source would read as fresh.
$LogFile = Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "run.log"
function Write-Step($msg) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  PREFLIGHT  $msg"
    Write-Host $line
    Add-Content -Path $LogFile -Value $line -Encoding utf8
}

function Test-SourceFresh($path, $scheduledHour, $label) {
    $scheduled = $today.AddHours($scheduledHour)
    $judgeAfter = $scheduled.AddMinutes($GraceMinutes)

    if (-not (Test-Path $path)) {
        Write-Step "$label MISSING: $path"
        return $false
    }
    if ($now -lt $judgeAfter) {
        Write-Step "$label not due yet (grace until $($judgeAfter.ToString('HH:mm'))) - skipping check"
        return $true
    }

    $mtime = (Get-Item $path).LastWriteTime
    if ($mtime -ge $scheduled) {
        Write-Step "$label OK - updated $($mtime.ToString('yyyy-MM-dd HH:mm:ss'))"
        return $true
    }

    Write-Step "$label STALE - last updated $($mtime.ToString('yyyy-MM-dd HH:mm:ss')), expected at or after $($scheduled.ToString('yyyy-MM-dd HH:mm'))"
    return $false
}

function Get-AnchantoQuarterFile {
    $q = [math]::Ceiling($now.Month / 3)
    Join-Path $ANCHANTO_DIR "Anchanto Report $($now.Year) Q$q.xlsx"
}

# ---------------------------------------------------------------- Odoo
function Invoke-OdooForceRun {
    Write-Step "FORCE RUN Odoo - starting"
    Push-Location $ODOO_DIR
    try {
        foreach ($step in @(
            @("odoo_quarterly_export.py"),
            @("build_odoo_report.py", "--replace-production"),
            @("build_master_report.py", "--replace-production")
        )) {
            Write-Step "  python $($step -join ' ')"
            # Same reason as Write-Step: keep script output off the success stream.
            & $PY @step 2>&1 | ForEach-Object { Write-Host $_ }
            if ($LASTEXITCODE -ne 0) {
                Write-Step "  FAILED with exit code $LASTEXITCODE - aborting Odoo force run"
                return $false
            }
        }
    }
    finally { Pop-Location }
    Write-Step "FORCE RUN Odoo - done"
    return $true
}

# ------------------------------------------------------------ Anchanto
function Invoke-AnchantoForceRun {
    Write-Step "FORCE RUN Anchanto - starting scheduled task"
    $before = (Get-ScheduledTaskInfo -TaskName $ANCHANTO_TASK).LastRunTime
    Start-ScheduledTask -TaskName $ANCHANTO_TASK

    $deadline = (Get-Date).AddMinutes($AnchantoTimeoutMinutes)
    Start-Sleep -Seconds 10
    while ((Get-Date) -lt $deadline) {
        $state = (Get-ScheduledTask -TaskName $ANCHANTO_TASK).State
        $info = Get-ScheduledTaskInfo -TaskName $ANCHANTO_TASK
        if ($state -eq 'Ready' -and $info.LastRunTime -ne $before) {
            Write-Step "FORCE RUN Anchanto - finished, LastTaskResult=$($info.LastTaskResult)"
            return ($info.LastTaskResult -eq 0)
        }
        Start-Sleep -Seconds 30
    }
    Write-Step "FORCE RUN Anchanto - TIMED OUT after $AnchantoTimeoutMinutes min"
    return $false
}

# ------------------------------------------------------------ main
Write-Step "checking upstream sources (grace ${GraceMinutes}m)"
$problems = @()

if (-not (Test-SourceFresh $ODOO_MASTER $ODOO_HOUR "Odoo Report.xlsx")) {
    if (-not (Invoke-OdooForceRun)) { $problems += "Odoo force run failed" }
}

$anchantoFile = Get-AnchantoQuarterFile
if (-not (Test-SourceFresh $anchantoFile $ANCHANTO_HOUR "$(Split-Path $anchantoFile -Leaf)")) {
    if (-not (Invoke-AnchantoForceRun)) { $problems += "Anchanto force run failed" }
}

if ($problems.Count -gt 0) {
    Write-Step "COMPLETED WITH PROBLEMS: $($problems -join '; ')"
    exit 1
}
Write-Step "sources OK"
exit 0
