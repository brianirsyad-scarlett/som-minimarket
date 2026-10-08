# Registers a Windows Scheduled Task that copies "Sent Email" to OneDrive.
#
# Runs sync_sent_email.py --once on a repeating schedule (default every 5 minutes,
# all day). The script only uses the Python standard library plus robocopy, so no
# virtualenv is needed - it just needs python on PATH (or pass -Python).
[CmdletBinding()]
param(
    [string] $TaskName = "SOM Sent Email OneDrive Sync",
    [int]    $RepeatMinutes = 5,
    [string] $Python,                # full path to python.exe; default: first python on PATH
    [switch] $Mirror,                # also mirror deletions to OneDrive
    [switch] $Remove
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

if ($Remove) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed scheduled task '$TaskName'." -ForegroundColor Green
    return
}

if (-not $Python) {
    $cmd = Get-Command python.exe -ErrorAction SilentlyContinue
    if (-not $cmd) { throw "python.exe not found on PATH. Install Python or pass -Python <path>." }
    $Python = $cmd.Source
}
if (-not (Test-Path $Python)) { throw "Python not found at '$Python'." }

$script = "$here\sync_sent_email.py"
$arg = "`"$script`" --once"
if ($Mirror) { $arg += " --mirror" }

$action = New-ScheduledTaskAction -Execute $Python -Argument $arg -WorkingDirectory $here

# Start at logon, then repeat every N minutes indefinitely. (Daily trigger + repetition
# is used because "-Once -RepetitionDuration ([TimeSpan]::MaxValue)" is rejected on some builds.)
$trigger = New-ScheduledTaskTrigger -Daily -At "00:00"
$trigger.Repetition = (New-ScheduledTaskTrigger -Once -At "00:00" `
    -RepetitionInterval (New-TimeSpan -Minutes $RepeatMinutes) `
    -RepetitionDuration (New-TimeSpan -Hours 23 -Minutes 59)).Repetition
$logon = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"

# Interactive: the OneDrive folder is in the user profile/session, so run as the logged-on user.
$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel Limited

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -DontStopOnIdleEnd `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger @($trigger, $logon) `
    -Principal $principal `
    -Settings $settings `
    -Description "Copies D:\...\Data\Sent Email to OneDrive via robocopy." `
    -Force | Out-Null

Write-Host "Registered '$TaskName' - every $RepeatMinutes min and at logon (only while you are logged on)." -ForegroundColor Green
Write-Host "Run it now with:" -ForegroundColor Cyan
Write-Host "  Start-ScheduledTask -TaskName `"$TaskName`"" -ForegroundColor Cyan
Write-Host "Remove it with:  .\register_schedule.ps1 -Remove" -ForegroundColor Cyan
