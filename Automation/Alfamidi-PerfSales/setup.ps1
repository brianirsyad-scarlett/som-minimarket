# Creates the virtualenv, installs dependencies and the Chromium build. Run once.
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

Write-Host "Installing Chromium for Playwright..." -ForegroundColor Cyan
& $py -m playwright install chromium

if (-not (Test-Path "$here\.env")) {
    Copy-Item "$here\.env.example" "$here\.env"
    Write-Host "Created .env from the example - fill in ALFAMIDI_PASSWORD." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Setup done." -ForegroundColor Green
Write-Host "See the request plan without sending anything:" -ForegroundColor Cyan
Write-Host "  .\run.ps1 -DryRun" -ForegroundColor Cyan
