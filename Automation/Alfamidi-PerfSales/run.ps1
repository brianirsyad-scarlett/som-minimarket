# Queues the Alfamidi Performance Sales reports. The files themselves arrive by
# email; ..\Mail-ReportLinks collects them.
#
# Start with -DryRun to see the request plan without sending anything.
[CmdletBinding()]
param(
    [ValidateSet("branch", "store", "both")]
    [string] $Report = "both",
    [ValidateSet("current", "previous", "both")]
    [string] $Months = "both",
    [ValidateSet("q", "v", "both")]
    [string] $Unit = "both",
    [ValidateSet("a", "b")]
    [string] $Indicator = "a",  # a = Selling Out, b = Stok (Stok files land in Stock\)
    [int]    $Periods,          # by-store only: how many 10-day periods back
    [string] $Categories,       # comma-separated codes; default = all found on the page
    [double] $Delay,            # seconds between requests
    [switch] $DryRun,
    [switch] $Headful,          # watch the browser
    [switch] $Detailed
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$py   = "$here\.venv\Scripts\python.exe"

if (-not (Test-Path $py)) { throw "Virtualenv missing. Run setup.ps1 first." }
if (-not (Test-Path "$here\.env")) { throw ".env missing. Copy .env.example to .env and fill it in." }

$argsList = @("$here\alfamidi_perfsales.py", "--report", $Report, "--months", $Months,
              "--unit", $Unit, "--indicator", $Indicator)
if ($Periods)    { $argsList += @("--periods", $Periods) }
if ($Categories) { $argsList += @("--categories", $Categories) }
if ($Delay)      { $argsList += @("--delay", $Delay) }
if ($DryRun)     { $argsList += "--dry-run" }
if ($Headful)    { $argsList += "--headful" }
if ($Detailed)   { $argsList += "--verbose" }

& $py @argsList
exit $LASTEXITCODE
