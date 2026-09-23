# One-time setup: creates a local virtualenv, installs deps, installs Chromium.
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here

Write-Host "Creating virtual environment..." -ForegroundColor Cyan
if (-not (Test-Path "$here\.venv")) { python -m venv "$here\.venv" }

$py = "$here\.venv\Scripts\python.exe"

Write-Host "Installing Python packages..." -ForegroundColor Cyan
& $py -m pip install --upgrade pip --quiet
& $py -m pip install -r "$here\requirements.txt"

Write-Host "Installing Chromium for Playwright..." -ForegroundColor Cyan
& $py -m playwright install chromium

if (-not (Test-Path "$here\.env")) {
    Copy-Item "$here\.env.example" "$here\.env"
    Write-Host ""
    Write-Host "Created .env - open it and fill in your password:" -ForegroundColor Yellow
    Write-Host "  $here\.env" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Setup complete." -ForegroundColor Green
Write-Host "Next: edit .env, then run  .\run.ps1 -Headful  to test." -ForegroundColor Green
