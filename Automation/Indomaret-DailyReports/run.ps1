# Downloads the newest file for each of the 3 daily reports (or just one, with -Report).
[CmdletBinding()]
param(
    [string] $Report = "all",   # 2, 3, 10, or "all"
    [switch] $Force             # re-download even if the file already exists
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$py   = "$here\.venv\Scripts\python.exe"

if (-not (Test-Path $py)) { throw "Virtualenv missing. Run setup.ps1 first." }
if (-not (Test-Path "$here\.env")) { throw ".env missing. Copy .env.example to .env and fill it in." }

$argsList = @("$here\indomaret_daily_reports.py", "--report", $Report)
if ($Force) { $argsList += "--force" }

& $py @argsList
exit $LASTEXITCODE
