# Saves new Outlook attachments into the SOM Data tree.
# Start with -DryRun to see what it would pick up without writing anything.
[CmdletBinding()]
param(
    [switch] $DryRun,                # report only, write nothing
    [int]    $Days,                  # override MAIL_LOOKBACK_DAYS
    [string] $Folder,                # override MAIL_FOLDER, e.g. "Inbox/Sales Reports"
    [switch] $Detailed               # log every skipped attachment and why
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$py   = "$here\.venv\Scripts\python.exe"

if (-not (Test-Path $py)) { throw "Virtualenv missing. Run setup.ps1 first." }

$argsList = @("$here\mail_attachments.py")
if ($DryRun)   { $argsList += "--dry-run" }
if ($Detailed) { $argsList += "--verbose" }
if ($Days)     { $argsList += @("--days", $Days) }
if ($Folder)   { $argsList += @("--folder", $Folder) }

& $py @argsList
exit $LASTEXITCODE
