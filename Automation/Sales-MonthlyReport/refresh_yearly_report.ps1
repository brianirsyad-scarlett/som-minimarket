# Refreshes Data\Report\Sales\<year>\<year> Report.xlsx via Excel COM.
#
# Like the Sell In workbooks, this one keeps its Power Query and a Power Pivot
# data model (its queries load to the model, not to sheet tables), so
# refreshing it means driving Excel. The Report query globs the year folder, so
# it must run after the monthly workbooks are rebuilt, and the folder must hold
# nothing but the monthly workbooks and the report itself - a stray file is
# double-counted silently.
#
#   -CountOnly   open read-only and print the data-model row counts, no refresh
param([int]$Year = (Get-Date).Year, [switch]$CountOnly)
$ErrorActionPreference = "Stop"

$dir    = "D:\SCARLETT_512\SCARLETT-329\SOM\Data\Report\Sales\$Year"
$path   = Join-Path $dir "$Year Report.xlsx"
$bakDir = Join-Path $PSScriptRoot "backups"
$keep   = 3

function Stamp { Get-Date -Format 'HH:mm:ss' }

function Get-ModelCounts($wb) {
    $h = [ordered]@{}
    foreach ($t in $wb.Model.ModelTables) { $h[$t.Name] = [long]$t.RecordCount }
    $h
}

if (-not (Test-Path $path)) { "$(Stamp)  ERROR Workbook not found: $path"; exit 1 }

$stray = Get-ChildItem $dir -File | Where-Object {
    $_.Name -notmatch "^$Year \d{2} [A-Za-z]{3}\.xlsx$" -and $_.Name -ne "$Year Report.xlsx" }
if ($stray) {
    "$(Stamp)  ERROR stray file(s) in $dir would be double-counted: $($stray.Name -join ', ')"
    exit 1
}

if (-not $CountOnly) {
    New-Item -ItemType Directory -Force $bakDir | Out-Null
    $bak = Join-Path $bakDir ("$Year Report.xlsx." + (Get-Date -Format yyyyMMdd_HHmmss) + ".bak")
    Copy-Item $path $bak
    "$(Stamp)  Backup: $bak"
    # ~350 MB each; keep only the newest few.
    Get-ChildItem $bakDir -Filter "$Year Report.xlsx.*.bak" | Sort-Object LastWriteTime -Descending |
        Select-Object -Skip $keep | Remove-Item -Force
}

$excel = New-Object -ComObject Excel.Application
$excel.Visible = $false
$excel.DisplayAlerts = $false
$excel.AskToUpdateLinks = $false
$failed = $false

try {
    "$(Stamp)  Opening $(Split-Path $path -Leaf)"
    $wb = $excel.Workbooks.Open($path, 0, [bool]$CountOnly)
    if (-not $CountOnly -and $wb.ReadOnly) { throw "Workbook opened read-only (open in another Excel?)" }

    $before = Get-ModelCounts $wb
    if ($CountOnly) {
        foreach ($k in $before.Keys) { "  {0}: {1:N0} rows" -f $k, $before[$k] }
        $wb.Close($false)
    }
    else {
        # RefreshAll returns immediately while a background query is still
        # running, which would save a half-refreshed book.
        foreach ($conn in $wb.Connections) {
            try {
                if ($conn.Type -eq 1) { $conn.OLEDBConnection.BackgroundQuery = $false }
                elseif ($conn.Type -eq 2) { $conn.ODBCConnection.BackgroundQuery = $false }
            } catch { }
        }

        "$(Stamp)  Refreshing..."
        $wb.RefreshAll()
        $excel.CalculateUntilAsyncQueriesDone()

        $after = Get-ModelCounts $wb
        foreach ($k in $after.Keys) {
            $b = if ($before.Contains($k)) { $before[$k] } else { 0 }
            $flag = ""
            if ($after[$k] -eq 0 -and $b -gt 0) { $flag = "  <-- EMPTIED"; $failed = $true }
            elseif ($b -gt 0 -and $after[$k] -lt $b * 0.98) { $flag = "  <-- dropped >2%"; $failed = $true }
            "  {0}: {1:N0} -> {2:N0} rows{3}" -f $k, $b, $after[$k], $flag
        }

        if ($failed) {
            # Leave the previous good file in place rather than saving a collapsed one.
            $wb.Close($false)
            "$(Stamp)  ERROR row counts collapsed - not saved"
        }
        else {
            $wb.Save()
            $wb.Close($true)
            "$(Stamp)  Saved"
        }
    }
}
catch {
    $failed = $true
    "$(Stamp)  ERROR $($_.Exception.Message)"
}
finally {
    $excel.Quit()
    [void][Runtime.InteropServices.Marshal]::ReleaseComObject($excel)
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
}

if ($failed) { "Exit code: 1"; exit 1 }
"Exit code: 0"
exit 0
