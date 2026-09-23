# Creates the virtualenv and installs dependencies. Run once.
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$py   = "$here\.venv\Scripts\python.exe"

if (-not (Test-Path $py)) {
    Write-Host "Creating virtualenv..." -ForegroundColor Cyan
    python -m venv "$here\.venv"
}

& $py -m pip install --upgrade pip --quiet
& $py -m pip install -r "$here\requirements.txt"

if (-not (Test-Path "$here\.env")) {
    Copy-Item "$here\.env.example" "$here\.env"
    Write-Host "Created .env from the example - review it." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Setup done." -ForegroundColor Green
Write-Host "Next: create the Power Automate flow (README.md step 1), then:" -ForegroundColor Cyan
Write-Host "  .\run.ps1 -DryRun -Detailed" -ForegroundColor Cyan
