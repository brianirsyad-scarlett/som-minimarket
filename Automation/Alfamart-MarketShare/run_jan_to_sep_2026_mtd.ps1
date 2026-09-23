# Downloads MTD Market Share for every category, Jan-2026 through Sep-2026,
# in one login session. Existing files in the output folder are overwritten.
[CmdletBinding()]
param(
    [string] $Branch = "NAS",
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
    "--periode",  "2026-01:2026-09",
    "--category", "all",
    "--format",   "MTD",
    "--branch",   $Branch,
    "--out",      $Out
)
if ($Headful) { $argsList += "--headful" }

& $py @argsList
exit $LASTEXITCODE
