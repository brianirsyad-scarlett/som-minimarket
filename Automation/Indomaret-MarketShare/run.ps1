# Runs the downloader. Defaults to the current month, every category.
[CmdletBinding()]
param(
    [string]   $Periode     = (Get-Date -Format "yyyy-MM"),  # single month, "2026-01:2026-09" range, "all", or "recent"
    [string]   $Category    = "all",
    [string]   $RangePeriod = "YoY",
    [string]   $Unit        = "IDR",
    [string]   $Out         = "D:\SCARLETT_512\SCARLETT-329\SOM\Data\Report\Sales\Minimarket\Indomaret\Market Share\Raw"
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$py   = "$here\.venv\Scripts\python.exe"

if (-not (Test-Path $py)) { throw "Virtualenv missing. Run setup.ps1 first." }
if (-not (Test-Path "$here\.env")) { throw ".env missing. Copy .env.example to .env and fill it in." }

& $py "$here\indomaret_market_share.py" `
    --periode $Periode `
    --category $Category `
    --range-period $RangePeriod `
    --unit $Unit `
    --out $Out

exit $LASTEXITCODE
