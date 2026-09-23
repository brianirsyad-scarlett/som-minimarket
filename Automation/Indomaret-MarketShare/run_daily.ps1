# Daily catch-up run: downloads the previous month + current month, every category.
# Meant to be run by Task Scheduler at 00:00 - see register_schedule.ps1.
# Existing files in the output folder are overwritten in place.
[CmdletBinding()]
param(
    [string] $RangePeriod = "YoY",
    [string] $Unit        = "IDR",
    [string] $Out         = "D:\SCARLETT_512\SCARLETT-329\SOM\Data\Report\Sales\Minimarket\Indomaret\Market Share\Raw"
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$py   = "$here\.venv\Scripts\python.exe"

if (-not (Test-Path $py)) { throw "Virtualenv missing. Run setup.ps1 first." }
if (-not (Test-Path "$here\.env")) { throw ".env missing. Copy .env.example to .env and fill it in." }

& $py "$here\indomaret_market_share.py" `
    --periode "recent" `
    --category "all" `
    --range-period $RangePeriod `
    --unit $Unit `
    --out $Out

exit $LASTEXITCODE
