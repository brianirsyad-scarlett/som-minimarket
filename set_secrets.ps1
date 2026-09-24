# Copies the portal logins this laptop already uses into this repo's GitHub
# Actions secrets. YOU run this - it sends your credentials to GitHub.
#
#   .\set_secrets.ps1 -GcpKeyPath "C:\path\to\service-account.json"
#
# -GcpKeyPath is the service-account JSON your other som-* repos already use as
# GCP_SA_KEY. GitHub never lets a secret be read back, so it has to come from
# the file. Do NOT use gcloud's application_default_credentials.json - that is
# your personal Google login, not a service account.
#
# No value is ever printed or put on a command line: the logins go through a
# temporary dotenv file (gh secret set -f) that is deleted afterwards, and the
# JSON key goes through stdin.
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $GcpKeyPath,
    [string] $Repo = "brianirsyad-scarlett/som-minimarket-automation"
)

$ErrorActionPreference = "Stop"
$SOM = "D:\SCARLETT_512\SCARLETT-329\SOM"

function Read-DotEnv($path) {
    if (-not (Test-Path $path)) { throw "Missing $path" }
    $h = @{}
    foreach ($line in Get-Content $path) {
        if ($line -match '^\s*#' -or $line -notmatch '=') { continue }
        $k, $v = $line -split '=', 2
        $h[$k.Trim()] = $v.Trim()
    }
    $h
}

# One login per chain covers market share AND B2B - verified identical on
# this laptop on 2026-09-24 (usernames, passwords and the Alfamart TOTP seed).
$alfamart  = Read-DotEnv "$SOM\Automation\Alfamart-MarketShare\.env"
$alfamidi  = Read-DotEnv "$SOM\Automation\Alfamidi-MarketShare\.env"
$indomaret = Read-DotEnv "$SOM\Automation\Indomaret-MarketShare\.env"

$secrets = [ordered]@{
    ALFAMART_USERNAME     = $alfamart["ALFAMART_USERNAME"]
    ALFAMART_PASSWORD     = $alfamart["ALFAMART_PASSWORD"]
    ALFAMART_TOTP_SECRET  = $alfamart["ALFAMART_TOTP_SECRET"]
    ALFAMIDI_USERNAME     = $alfamidi["ALFAMIDI_USERNAME"]
    ALFAMIDI_PASSWORD     = $alfamidi["ALFAMIDI_PASSWORD"]
    INDOMARET_EMAIL       = $indomaret["INDOMARET_EMAIL"]
    INDOMARET_PASSWORD    = $indomaret["INDOMARET_PASSWORD"]
    INDOMARET_TOTP_SECRET = $indomaret["INDOMARET_TOTP_SECRET"]
}

$missing = @($secrets.Keys | Where-Object { [string]::IsNullOrWhiteSpace($secrets[$_]) })
if ($missing) { throw "Empty in the .env files: $($missing -join ', ')" }

if (-not (Test-Path $GcpKeyPath)) { throw "GCP key not found: $GcpKeyPath" }
$key = Get-Content $GcpKeyPath -Raw
if ($key -notmatch '"type"\s*:\s*"service_account"') {
    throw "$GcpKeyPath is not a service-account key (no `"type`": `"service_account`")."
}

$tmp = Join-Path $env:TEMP ("som_secrets_{0}.env" -f [guid]::NewGuid().ToString("N"))
try {
    # gh's dotenv reader: KEY="value", with \ and " escaped.
    $lines = foreach ($k in $secrets.Keys) {
        $v = $secrets[$k].Replace('\', '\\').Replace('"', '\"')
        "$k=`"$v`""
    }
    [IO.File]::WriteAllLines($tmp, $lines)     # UTF-8, no BOM
    gh secret set -f $tmp --repo $Repo
    if ($LASTEXITCODE -ne 0) { throw "gh secret set failed for the login secrets" }
} finally {
    Remove-Item $tmp -Force -ErrorAction SilentlyContinue
}

$key | gh secret set GCP_SA_KEY --repo $Repo
if ($LASTEXITCODE -ne 0) { throw "gh secret set failed for GCP_SA_KEY" }

Write-Host ""
Write-Host "Secrets now on $Repo (names only):" -ForegroundColor Green
gh secret list --repo $Repo
