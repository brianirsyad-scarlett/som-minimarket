# What the scheduled task runs, in order:
#
#   0. Verify Odoo and Anchanto actually refreshed today, and force-run whichever
#      did not. Both have silently reported success while producing nothing, and
#      steps 1-2 cannot tell - they rebuild happily from a stale source and every
#      output timestamp still looks current.
#
#   1. Rebuild the previous month and the current month workbooks.
#      The previous month is included because Odoo and Accurate keep back-dating
#      deliveries for a week or two after month end - refreshing only the current
#      month would leave those out of the yearly roll-up.
#
#   2. Re-export every monthly workbook to CSV and rebuild combined_sales.parquet.
#      This has to come second: it only re-converts a sheet when its .xlsx is
#      newer than the .csv, so it must see the workbooks step 1 just wrote.
#
#   3. Refresh the Sell In Offline MT/GT workbooks. These still hold their own
#      Power Query, which reads the monthly workbooks from step 1, so they go
#      last. Driving Excel takes ~15 min for the pair.
#
# Step 2 runs even if step 1 fails, so a bad source file cannot leave the parquet
# stale as well - but the exit code reports the failure either way.
$ErrorActionPreference = "Continue"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$py   = "$here\.venv\Scripts\python.exe"
$csvScript = "D:\SCARLETT_512\SCARLETT-329\SOM\Data\Report\Sales\Sales csv\primary_sales_csv_and_parquet.py"
$log  = "$here\run.log"

if (-not (Test-Path $py)) { throw "Virtualenv missing. Run setup.ps1 first." }

# Captured before anything runs: step 4 uses it to assert every output below was
# actually rewritten by THIS run rather than left over from an earlier one.
$runStart = (Get-Date).ToString("s")

& "$here\preflight_sources.ps1"
$preflightExit = $LASTEXITCODE

& "$here\run.ps1" -Month prev, current
$buildExit = $LASTEXITCODE

"$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  INFO    Rebuilding CSV + combined_sales.parquet" |
    Tee-Object -FilePath $log -Append

if (-not (Test-Path $csvScript)) {
    "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  ERROR   CSV/parquet script not found: $csvScript" |
        Tee-Object -FilePath $log -Append
    exit 1
}

# The script prints check marks and arrows; without this, writing them to a
# redirected (non-console) stdout raises UnicodeEncodeError and kills the run.
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"

# -u keeps stdout unbuffered, so run.log fills in as it goes rather than all at
# the end - it is a long step and you want to see where it is.
& $py -u $csvScript 2>&1 | Tee-Object -FilePath $log -Append
$csvExit = $LASTEXITCODE

"$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  INFO    Refreshing Sell In Offline MT/GT" |
    Tee-Object -FilePath $log -Append

& "D:\SCARLETT_512\SCARLETT-329\SOM\Data\Report\Sales\Sell In\refresh_sell_in.ps1" 2>&1 |
    Tee-Object -FilePath $log -Append
$sellInExit = $LASTEXITCODE

"$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  INFO    Validating chain outputs" |
    Tee-Object -FilePath $log -Append

& $py -u "$here\validate_chain.py" --since $runStart 2>&1 | Tee-Object -FilePath $log -Append
$validateExit = $LASTEXITCODE

"$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  INFO    run_daily finished - preflight=$preflightExit workbooks=$buildExit csv/parquet=$csvExit sellin=$sellInExit validate=$validateExit" |
    Tee-Object -FilePath $log -Append

# Validation last: it is the check that catches a step which "succeeded" without
# producing anything, so its verdict should not be masked by an earlier code.
if ($validateExit -ne 0) { exit $validateExit }
if ($buildExit -ne 0) { exit $buildExit }
if ($csvExit -ne 0) { exit $csvExit }
if ($sellInExit -ne 0) { exit $sellInExit }
exit $preflightExit
