# Pre-flight check for the daily B2B fire (09:00 since 2026-09-22).
#
# Answers one question: will the morning pass actually run, and will it work when
# it does? Every check here corresponds to something that has genuinely broken
# this pipeline before, not a hypothetical:
#
#   Interactive principal  - S4U/SYSTEM tasks register fine and never execute
#   WakeToRun + battery    - 2026-09-21: the laptop slept through the whole 07:00 pass
#   wake timers on AC      - WakeToRun is inert if the power plan forbids wake timers
#   python + scripts + .env- a task whose Execute path is wrong fails at launch
#   scripts actually import- a syntax error only shows up at run time
#
#   .\preflight.ps1
[CmdletBinding()]
param()

$SOM  = "D:\SCARLETT_512\SCARLETT-329\SOM"
$MAIL = Join-Path $SOM "Data\Sent Email"
$py   = "C:\Users\BrianRinaldyIrsyad\AppData\Local\Python\bin\python.exe"
$fail = 0
$warn = 0

function Ok   ($m) { Write-Host "   OK    $m" -ForegroundColor Green }
function Bad  ($m) { Write-Host "   FAIL  $m" -ForegroundColor Red;    $script:fail++ }
function Warn ($m) { Write-Host "   WARN  $m" -ForegroundColor Yellow; $script:warn++ }

# Read the scheduled time off the task rather than hardcoding it - the fire has
# already moved once (07:00 -> 09:00 on 2026-09-22) and a stale label in a
# readiness check is worse than no label.
$fireTask = Get-ScheduledTask -TaskName "B2B_Fire_0700" -ErrorAction SilentlyContinue
$fireAt = if ($fireTask) {
    ([datetime]($fireTask.Triggers | Select-Object -First 1).StartBoundary).ToString("HH:mm")
} else { "??:??" }

Write-Host ""
Write-Host ("  B2B fire pre-flight (fires {0})   {1}" -f $fireAt, (Get-Date -Format "yyyy-MM-dd HH:mm")) -ForegroundColor Cyan
Write-Host ""

# --- the three morning tasks ------------------------------------------------
Write-Host "  Scheduled tasks:" -ForegroundColor Cyan
foreach ($n in "B2B_Fire_0700","B2B_Verify_0710","B2B_Collect_Morning") {
    $t = Get-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue
    if (-not $t) { Bad "$n is NOT REGISTERED"; continue }

    $i = Get-ScheduledTaskInfo -TaskName $n
    $problems = @()
    if ($t.State -eq 'Disabled')                     { $problems += "disabled" }
    if ($t.Principal.LogonType -ne 'Interactive')    { $problems += "principal is $($t.Principal.LogonType), must be Interactive" }
    if (-not $t.Settings.WakeToRun)                  { $problems += "WakeToRun off - will sleep through it" }
    if ($t.Settings.DisallowStartIfOnBatteries)      { $problems += "blocked on battery" }
    if (-not $i.NextRunTime)                         { $problems += "no NextRunTime" }

    $next = if ($i.NextRunTime) { $i.NextRunTime.ToString("MM-dd HH:mm") } else { "none" }
    if ($problems) { Bad ("{0,-20} next {1}  -> {2}" -f $n, $next, ($problems -join "; ")) }
    else           { Ok  ("{0,-20} next {1}" -f $n, $next) }
}
Write-Host ""

# --- power ------------------------------------------------------------------
Write-Host "  Power:" -ForegroundColor Cyan
$ac = (Get-CimInstance -ClassName BatteryStatus -Namespace root\wmi -ErrorAction SilentlyContinue |
       Select-Object -First 1).PowerOnline
if ($null -eq $ac) { Warn "could not read AC state" }
elseif ($ac)       { Ok  "on AC power" }
else               { Warn "on BATTERY - wake timers are disabled on DC, so a sleeping laptop will NOT wake for the $fireAt fire. Plug it in." }

# WakeToRun does nothing if the power plan forbids wake timers.
$rt = powercfg /query SCHEME_CURRENT SUB_SLEEP RTCWAKE 2>$null
$acIdx = ($rt | Select-String "Current AC Power Setting Index:\s*(0x[0-9a-f]+)").Matches.Groups[1].Value
if ($acIdx -and [int]$acIdx -ne 0) { Ok "wake timers enabled on AC" }
else { Bad "wake timers DISABLED on AC - WakeToRun cannot fire the task" }
Write-Host ""

# --- the things the task actually launches ----------------------------------
Write-Host "  Runtime:" -ForegroundColor Cyan
if (Test-Path $py) { Ok "python  $py" } else { Bad "python MISSING at $py" }

foreach ($f in "run_daily_all.py","run_pipeline.py","alfamart_b2b_auto.py","alfamidi_b2b_auto.py") {
    if (Test-Path (Join-Path $MAIL $f)) { Ok "script  $f" } else { Bad "script MISSING  $f" }
}
# Credentials live in the HOME directory, not next to the scripts:
# alfamart_b2b_auto.py reads Path.home()/".b2b_email.env".
$envPath = Join-Path $env:USERPROFILE ".b2b_email.env"
if (Test-Path $envPath) {
    $keys = @(Get-Content $envPath | Where-Object { $_ -match '^\s*[A-Z]' } |
              ForEach-Object { ($_ -split '=')[0].Trim() })
    $need = @("B2B_GMAIL_USER","B2B_GMAIL_APP_PASSWORD","ALFAMART_TOTP_SECRET")
    $missing = @($need | Where-Object { $keys -notcontains $_ })
    if ($missing) { Bad "creds   .b2b_email.env is missing: $($missing -join ', ')" }
    else          { Ok  "creds   .b2b_email.env has all $($need.Count) required keys" }
} else {
    Bad "creds   MISSING $envPath - login will fail"
}
Write-Host ""

# --- do the edited scripts still load? --------------------------------------
# A syntax or import error is invisible until the scheduled run otherwise.
Write-Host "  Scripts load cleanly:" -ForegroundColor Cyan
if (Test-Path $py) {
    foreach ($f in "run_daily_all.py","run_pipeline.py") {
        $out = & $py (Join-Path $MAIL $f) --help 2>&1
        if ($LASTEXITCODE -eq 0) { Ok "$f --help" }
        else { Bad "$f failed to load: $($out | Select-Object -Last 1)" }
    }
}
Write-Host ""

# --- verdict ----------------------------------------------------------------
if ($fail -gt 0) {
    Write-Host ("  NOT READY - {0} blocking problem(s), {1} warning(s)" -f $fail, $warn) -ForegroundColor Red
} elseif ($warn -gt 0) {
    Write-Host ("  READY, with {0} warning(s) above" -f $warn) -ForegroundColor Yellow
} else {
    Write-Host "  READY - the $fireAt fire should run" -ForegroundColor Green
}
Write-Host ""
