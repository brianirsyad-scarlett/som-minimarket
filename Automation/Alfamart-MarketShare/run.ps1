# Runs the downloader. Defaults to the current month so MTD figures stay fresh.
[CmdletBinding()]
param(
    [string]   $Periode  = (Get-Date -Format "yyyy-MM"),  # single month, "2026-01:2026-09" range, or "all"
    [string]   $Category = "3222",
    [string]   $Format   = "MTD",
    [string]   $Branch   = "NAS",
    [string]   $Out      = "D:\SCARLETT_512\SCARLETT-329\SOM\Data\Report\Sales\Minimarket\Alfamart\Market Share\Raw",
    [switch]   $Headful
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$py   = "$here\.venv\Scripts\python.exe"

if (-not (Test-Path $py)) { throw "Virtualenv missing. Run setup.ps1 first." }
if (-not (Test-Path "$here\.env")) { throw ".env missing. Copy .env.example to .env and fill it in." }

$argsList = @(
    "$here\alfamart_market_share.py",
    "--periode",  $Periode,
    "--category", $Category,
    "--format",   $Format,
    "--branch",   $Branch,
    "--out",      $Out
)
if ($Headful) { $argsList += "--headful" }

& $py @argsList
exit $LASTEXITCODE
