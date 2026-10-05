# Sends the Gmail sender login in .env to this repo's GitHub Actions secrets.
# YOU run this - it sends your credentials to GitHub, so Claude never does.
#
#   1. Open .env in this folder and fill in the two values (see the comments in it).
#   2. .\set_secrets.ps1
#
# SMTP_USER          the Gmail address that SENDS the "items missing in Master Data Sales" email.
# SMTP_APP_PASSWORD  a Google "app password" for that address (16 letters) - NOT its normal password.
#
# No value is ever printed or put on a command line: the two go through a temporary dotenv file
# (gh secret set -f) that is deleted afterwards. A blank value is refused, because GitHub would
# otherwise store an EMPTY secret (that is what happened to AZURE_TENANT_ID / AZURE_CLIENT_ID).
[CmdletBinding()]
param(
    [string] $Repo = "brianirsyad-scarlett/som-sellthrough"
)

$ErrorActionPreference = "Stop"
$envFile = Join-Path $PSScriptRoot ".env"
if (-not (Test-Path $envFile)) { throw "Missing $envFile - copy .env.example to .env and fill it in." }

$h = @{}
foreach ($line in Get-Content $envFile) {
    if ($line -match '^\s*#' -or $line -notmatch '=') { continue }
    $k, $v = $line -split '=', 2
    $h[$k.Trim()] = $v.Trim().Trim('"').Trim("'")
}

$secrets = [ordered]@{
    SMTP_USER         = $h["SMTP_USER"]
    SMTP_APP_PASSWORD = ($h["SMTP_APP_PASSWORD"] -replace '\s', '')    # Google shows it as "abcd efgh ijkl mnop"
}
$missing = @($secrets.Keys | Where-Object { [string]::IsNullOrWhiteSpace($secrets[$_]) })
if ($missing) { throw "Empty in .env: $($missing -join ', ') - nothing was sent." }
if ($secrets["SMTP_USER"] -notmatch '^[^@\s]+@[^@\s]+\.[^@\s]+$') { throw "SMTP_USER must be an email address." }
if ($secrets["SMTP_APP_PASSWORD"].Length -ne 16) {
    Write-Warning "SMTP_APP_PASSWORD is $($secrets['SMTP_APP_PASSWORD'].Length) characters; a Google app password has 16. Sending anyway."
}

$tmp = Join-Path $env:TEMP ("som_secrets_{0}.env" -f [guid]::NewGuid().ToString("N"))
try {
    ($secrets.GetEnumerator() | ForEach-Object { "{0}={1}" -f $_.Key, $_.Value }) | Set-Content -Path $tmp -Encoding ascii
    gh secret set -f $tmp --repo $Repo
    if ($LASTEXITCODE -ne 0) { throw "gh secret set failed (exit $LASTEXITCODE)" }
} finally {
    if (Test-Path $tmp) { Remove-Item $tmp -Force }
}

Write-Host "Secrets now on ${Repo}:" -ForegroundColor Green
gh secret list --repo $Repo
