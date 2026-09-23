# First-run backfill: downloads January 2026 through the current month, every
# category, in one login session. Existing files in the output folder are
# overwritten. Equivalent of Alfamidi's run_jan_to_sep_2026_mtd.ps1, but the end
# month is computed at run time instead of being hardcoded.
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
    --periode "all" `
    --category "all" `
    --range-period $RangePeriod `
    --unit $Unit `
    --out $Out

exit $LASTEXITCODE
