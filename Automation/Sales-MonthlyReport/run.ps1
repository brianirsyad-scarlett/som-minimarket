# Rebuilds one or more monthly sales workbooks.
#
#   .\run.ps1                          # current month
#   .\run.ps1 -Month prev              # previous month
#   .\run.ps1 -Month 2026-09
#   .\run.ps1 -Month 2026-07,2026-08   # backfill several
#   .\run.ps1 -DryRun                  # row counts only, writes nothing
#   .\run.ps1 -OutDir D:\scratch       # trial run, leaves the real folder alone
[CmdletBinding()]
param(
    [string[]] $Month    = @("current"),
    [string[]] $SplitDay = @("15"),   # Online sheets are cut at these days of the month
    [string]   $OutDir   = "",
    [switch]   $DryRun,
    [switch]   $NoBackup
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$py   = "$here\.venv\Scripts\python.exe"

if (-not (Test-Path $py)) { throw "Virtualenv missing. Run setup.ps1 first." }

# Invoked as `powershell -File run.ps1 -Month 2026-08,2026-09` the whole list
# arrives as one string, so split it back out here.
$months    = $Month    | ForEach-Object { $_ -split "[,;]" } | Where-Object { $_ }
$splitDays = $SplitDay | ForEach-Object { $_ -split "[,;]" } | Where-Object { $_ }

$argsList = @("$here\sales_monthly_report.py")
foreach ($m in $months)    { $argsList += @("--month", $m.Trim()) }
foreach ($d in $splitDays) { $argsList += @("--split-day", $d.Trim()) }
if ($OutDir)   { $argsList += @("--out-dir", $OutDir) }
if ($DryRun)   { $argsList += "--dry-run" }
if ($NoBackup) { $argsList += "--no-backup" }

& $py @argsList
exit $LASTEXITCODE
