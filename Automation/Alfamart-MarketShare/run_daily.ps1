# Daily catch-up run: downloads the previous month + current month, every category,
# for one branch. Meant to be run by Task Scheduler at 00:00 - see register_schedule.ps1.
# Existing files in the output folder are overwritten in place.
[CmdletBinding()]
param(
    [string] $Branch = "NAS",
    [string] $Format = "MTD",
    [string] $Out    = "D:\SCARLETT_512\SCARLETT-329\SOM\Data\Report\Sales\Minimarket\Alfamart\Market Share\Raw",
    [switch] $Headful
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$py   = "$here\.venv\Scripts\python.exe"

if (-not (Test-Path $py)) { throw "Virtualenv missing. Run setup.ps1 first." }
if (-not (Test-Path "$here\.env")) { throw ".env missing. Copy .env.example to .env and fill it in." }

$argsList = @(
    "$here\alfamart_market_share.py",
    "--periode",  "recent",
    "--category", "all",
    "--format",   $Format,
    "--branch",   $Branch,
    "--out",      $Out
)
if ($Headful) { $argsList += "--headful" }

# Run detached so stderr can be captured to a file. Without this, a Python
# traceback goes to a console that Task Scheduler throws away: on 2026-09-21 the
# task exited 1 twice with run.log ending at "Opening login page" and no reason
# given, while the identical command run by hand finished "26 saved, 0 failed".
# Mirrors Alfamidi-MarketShare\run_daily.ps1.
$stderrLog = Join-Path $here "run_stderr.log"
$tmpErr    = Join-Path $env:TEMP "alfamart_ms_stderr.txt"

# PowerShell 5.1 joins an -ArgumentList array with plain spaces and does NOT
# quote the elements, so the "...\Market Share\Raw" path has to be quoted here
# or it arrives at Python as two separate arguments.
$quoted = $argsList | ForEach-Object { '"{0}"' -f $_ }

# The portal is intermittently unreachable: ERR_TIMED_OUT / DNS failures at the
# B2B host killed this run on 2026-09-21 (twice) and again on 2026-09-23, each
# time losing a whole day of market share for a blip that cleared in seconds.
# Retrying is safe - every attempt is a fresh login and the script overwrites
# existing files rather than appending, so a partial first attempt costs nothing.
$attempts = 2
$p = $null
for ($i = 1; $i -le $attempts; $i++) {
    $p = Start-Process -FilePath $py -ArgumentList $quoted -NoNewWindow -Wait -PassThru `
            -WorkingDirectory $here -RedirectStandardError $tmpErr

    if ((Test-Path $tmpErr) -and (Get-Item $tmpErr).Length -gt 0) {
        Add-Content -Path $stderrLog -Encoding utf8 -Value @(
            "=== $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  attempt $i/$attempts  exit $($p.ExitCode) ==="
            (Get-Content $tmpErr -Raw)
        )
    }
    Remove-Item $tmpErr -ErrorAction SilentlyContinue

    if ($p.ExitCode -eq 0) { break }
    if ($i -lt $attempts) {
        Write-Host "Attempt $i failed (exit $($p.ExitCode)); retrying in 120s..." -ForegroundColor Yellow
        Start-Sleep -Seconds 120
    }
}
exit $p.ExitCode
