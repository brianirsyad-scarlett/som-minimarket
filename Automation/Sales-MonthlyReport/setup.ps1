# One-time setup: creates a local virtualenv and installs deps.
# No browser and no credentials here - this job only reads and writes local xlsx files,
# so there is no .env to fill in.
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here

Write-Host "Creating virtual environment..." -ForegroundColor Cyan
if (-not (Test-Path "$here\.venv")) { python -m venv "$here\.venv" }

$py = "$here\.venv\Scripts\python.exe"

Write-Host "Installing Python packages..." -ForegroundColor Cyan
& $py -m pip install --upgrade pip --quiet
& $py -m pip install -r "$here\requirements.txt"

Write-Host ""
Write-Host "Setup complete." -ForegroundColor Green
Write-Host "Next: .\run.ps1 -DryRun   (prints row counts, writes nothing)" -ForegroundColor Green
