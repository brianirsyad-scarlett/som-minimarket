# Refreshes a local Power BI Desktop file (.pbix) and saves it.
#
# Power BI Desktop has no command-line or COM refresh, so this drives its UI
# through Windows UI Automation: open the file, press Refresh, wait for the
# Refresh dialog to close, check nothing reported an error, press Save, and
# confirm the file on disk was rewritten. It needs an unlocked, signed-in
# desktop session.
#
# If the file is already open in Power BI Desktop (matched by the process's
# command line - every window of this report is just titled "Sales Report"),
# it refreshes that window and leaves it open. Otherwise it opens the file and
# closes Power BI again afterwards.
#
# Data-source credentials (e.g. the BigQuery service account) live in Power BI
# Desktop's own settings, not here.
#
# -Table "1_Primary Sales" refreshes only that table (Data pane > More options >
# Refresh) instead of the whole model via the ribbon's Refresh button.
param(
    [Parameter(Mandatory)][string]$Path,
    [string]$Table,
    [string]$BackupDir = "D:\SCARLETT_512\SCARLETT-329\Desktop\PowerBI_backups",
    [int]$Keep = 2,
    [int]$OpenTimeoutMin = 10,
    [int]$RefreshTimeoutMin = 90
)
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
$A  = [System.Windows.Automation.AutomationElement]
$TC = [System.Windows.Automation.Condition]::TrueCondition
$PBI = "$env:LOCALAPPDATA\Microsoft\WindowsApps\PBIDesktopStore.exe"
$ErrPattern = "error|failed|couldn't|could not|unable to|expired|credentials|access denied"

function Stamp { Get-Date -Format 'HH:mm:ss' }
function Done($code, $msg) { if ($msg) { "$(Stamp)  $msg" }; "Exit code: $code"; exit $code }

function Get-PbiPid {
    $p = Get-CimInstance Win32_Process -Filter "Name='PBIDesktop.exe'" |
        Where-Object { $_.CommandLine -like "*$Path*" } | Select-Object -First 1
    if ($p) { [int]$p.ProcessId }
}
function Get-Window($procId) {
    foreach ($w in $A::RootElement.FindAll("Children", $TC)) {
        if ($w.Current.ProcessId -eq $procId -and $w.Current.Name) { return $w }
    }
}
function Find-El($root, $name, $type) {
    foreach ($e in $root.FindAll("Descendants", $TC)) {
        if ($e.Current.Name -eq $name -and $e.Current.LocalizedControlType -eq $type) { return $e }
    }
}
function Invoke-El($el) { $el.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke() }
# The table menu's Refresh submenu only opens on a real mouse hover, and a
# minimized window draws its menus off-screen, so -Table needs the real cursor.
Add-Type -Namespace W32 -Name M -MemberDefinition @'
[DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
[DllImport("user32.dll")] public static extern void mouse_event(int f, int dx, int dy, int d, int e);
'@
function Move-To($el) {
    $r = $el.Current.BoundingRectangle
    [W32.M]::SetCursorPos([int]($r.X + $r.Width / 2), [int]($r.Y + $r.Height / 2)) | Out-Null
}
function Click-El($el) { Move-To $el; Start-Sleep -Milliseconds 300; [W32.M]::mouse_event(2, 0, 0, 0, 0); [W32.M]::mouse_event(4, 0, 0, 0, 0) }
function Get-Texts($root) {
    $root.FindAll("Descendants", $TC) | Where-Object { $_.Current.Name -and $_.Current.LocalizedControlType -eq "text" } |
        ForEach-Object { $_.Current.Name }
}

if (-not (Test-Path $Path)) { Done 1 "ERROR file not found: $Path" }
$Path = (Resolve-Path $Path).Path
$name = Split-Path $Path -Leaf
# Same-named reports live in different folders (Desktop\PowerBI, Downloads), so
# the backup name carries the folder too - otherwise pruning one file's backups
# would delete the other's.
$bakName = "$(Split-Path (Split-Path $Path -Parent) -Leaf) - $name"

# --- backup (the file is several hundred MB; keep only the newest few) --------
New-Item -ItemType Directory -Force $BackupDir | Out-Null
$bak = Join-Path $BackupDir ("$bakName." + (Get-Date -Format yyyyMMdd_HHmmss) + ".bak")
Copy-Item $Path $bak
"$(Stamp)  Backup: $bak"
Get-ChildItem $BackupDir -Filter "$bakName.*.bak" | Sort-Object LastWriteTime -Descending |
    Select-Object -Skip $Keep | Remove-Item -Force

# --- attach to an open window, or open the file -------------------------------
$procId = Get-PbiPid
$weOpened = -not $procId
if ($weOpened) {
    "$(Stamp)  Opening $Path"
    Start-Process $PBI -ArgumentList "`"$Path`""
} else {
    "$(Stamp)  Already open in Power BI Desktop (pid $procId) - refreshing that window, will leave it open"
}

$deadline = (Get-Date).AddMinutes($OpenTimeoutMin)
do {
    if (-not $procId) { $procId = Get-PbiPid }
    $w = if ($procId) { Get-Window $procId }
    $btn = if ($w) { Find-El $w "Refresh" "button" }
    if ($btn) { break }
    Start-Sleep 5
} while ((Get-Date) -lt $deadline)
if (-not $btn) { Done 1 "ERROR Power BI did not finish opening within $OpenTimeoutMin min (no Refresh button)" }

$mtime0 = (Get-Item $Path).LastWriteTime
$failed = $null

try {
    # --- refresh ----------------------------------------------------------------
    if ($Table) {
        "$(Stamp)  Refreshing table '$Table' only..."
        $wp = $w.GetCurrentPattern([System.Windows.Automation.WindowPattern]::Pattern)
        if ($wp.Current.WindowVisualState -eq "Minimized") { $wp.SetWindowVisualState("Maximized"); Start-Sleep 3 }
        $item =$w.FindAll("Descendants", $TC) |
            Where-Object { $_.Current.LocalizedControlType -eq "tree item" -and $_.Current.Name -match "^(Calculated )?Table $([regex]::Escape($Table))$" } |
            Select-Object -First 1
        if (-not $item) { throw "table '$Table' not found in the Data pane" }
        $more = Find-El $item "More options" "button"
        if (-not $more) { throw "no More options button on table '$Table'" }
        Invoke-El $more
        Start-Sleep 2
        # "Refresh" is a submenu (Schema and data / Schema / Data); "Data" reloads
        # the rows without touching the table's schema.
        # The submenu items are usually already in the tree once the menu opens;
        # expand Refresh only if they are not.
        $mi = Find-El $w "Refresh" "menu item"
        if (-not $mi) { throw "no Refresh item in the '$Table' menu" }
        Move-To $mi
        Start-Sleep 2
        $data = Find-El $w "Data" "menu item"
        if (-not $data) { throw "no Refresh > Data item in the '$Table' menu" }
        # Slide right along the Refresh row first so the cursor does not cross a
        # neighbouring item and close the submenu on the way to Data.
        $r = $mi.Current.BoundingRectangle
        [W32.M]::SetCursorPos([int]($r.X + $r.Width - 5), [int]($r.Y + $r.Height / 2)) | Out-Null
        Start-Sleep -Milliseconds 500
        try { Invoke-El $data } catch { Click-El $data }
        # A missed click leaves no Refresh dialog, which the wait loop below would
        # read as "finished" - so insist the dialog actually appears.
        $seen = $false
        for ($i = 0; $i -lt 15 -and -not $seen; $i++) { Start-Sleep 2; $seen = [bool](Find-El $w "Refresh" "dialog") }
        if (-not $seen) { throw "Refresh > Data clicked but no Refresh dialog appeared - table was not refreshed" }
    } else {
        "$(Stamp)  Refreshing..."
        Invoke-El $btn
    }
    Start-Sleep 10
    $deadline = (Get-Date).AddMinutes($RefreshTimeoutMin)
    $last = @()
    while ($true) {
        if (-not (Get-Process -Id $procId -ErrorAction SilentlyContinue)) { throw "Power BI Desktop exited during refresh" }
        $dlg = Find-El $w "Refresh" "dialog"
        if (-not $dlg) { break }
        $last = @(Get-Texts $dlg)
        $bad = $last | Where-Object { $_ -match $ErrPattern }
        if ($bad) { throw "refresh reported: $($bad -join ' | ')" }
        if ((Get-Date) -gt $deadline) { throw "refresh still running after $RefreshTimeoutMin min" }
        Start-Sleep 20
    }
    "$(Stamp)  Refresh finished. Last progress shown:"
    for ($i = 0; $i + 1 -lt $last.Count; $i += 2) {
        if ($last[$i + 1] -notmatch "Waiting for other queries") { "    $($last[$i]): $($last[$i + 1])" }
    }

    # An error can also surface as its own dialog after the Refresh one closes.
    # Only dialogs are scanned - report visuals can legitimately say "failed".
    Start-Sleep 5
    $bad = $w.FindAll("Descendants", $TC) | Where-Object { $_.Current.LocalizedControlType -in "dialog", "alert" } |
        ForEach-Object { $_.Current.Name; Get-Texts $_ } | Where-Object { $_ -match $ErrPattern } | Select-Object -Unique
    if ($bad) { throw "Power BI shows: $($bad -join ' | ')" }

    # --- save -------------------------------------------------------------------
    $save = Find-El $w "Save" "button"
    if (-not $save) { throw "no Save button found" }
    Invoke-El $save
    $deadline = (Get-Date).AddMinutes(15)
    do { Start-Sleep 10; $f = Get-Item $Path } while ($f.LastWriteTime -le $mtime0 -and (Get-Date) -lt $deadline)
    if ($f.LastWriteTime -le $mtime0) { throw "Save pressed but the file was not rewritten within 15 min" }
    "$(Stamp)  Saved: {0:N0} bytes at {1}" -f $f.Length, $f.LastWriteTime
}
catch { $failed = $_.Exception.Message }

# --- close only what we opened ------------------------------------------------
# On failure the window stays open, so a human can see what Power BI reported.
if ($weOpened -and -not $failed) {
    try { $w.GetCurrentPattern([System.Windows.Automation.WindowPattern]::Pattern).Close() } catch { }
    for ($i = 0; $i -lt 12 -and (Get-Process -Id $procId -ErrorAction SilentlyContinue); $i++) { Start-Sleep 5 }
    if (Get-Process -Id $procId -ErrorAction SilentlyContinue) { "$(Stamp)  WARNING Power BI did not close (a prompt may be open) - left running" }
    else { "$(Stamp)  Closed Power BI Desktop" }
}

if ($failed) { Done 1 "ERROR $failed (Power BI left open; previous file kept, backup at $bak)" }
Done 0
