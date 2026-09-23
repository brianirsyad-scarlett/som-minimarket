# Downloads report files from links the portals emailed.
# Start with -DryRun to see which links it finds before fetching anything.
[CmdletBinding()]
param(
    [switch] $DryRun,     # list links, download nothing
    [switch] $Keep,       # leave processed email bodies in the drop folder
    [switch] $Detailed    # verbose logging
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$py   = "$here\.venv\Scripts\python.exe"

if (-not (Test-Path $py)) { throw "Virtualenv missing. Run setup.ps1 first." }

$argsList = @("$here\report_links.py")
if ($DryRun)   { $argsList += "--dry-run" }
if ($Keep)     { $argsList += "--keep" }
if ($Detailed) { $argsList += "--verbose" }

& $py @argsList
exit $LASTEXITCODE
