#!/usr/bin/env python3
"""Post-run guard for the daily sales chain.

The preflight check covers stale *inputs*. This covers everything after: it
asserts each step actually produced something, and that what it produced is
sane. Every failure this catches has a precedent here - a step reporting exit 0
while launching no process at all, a workbook rebuilt from three-day-old data,
a table-less workbook silently contributing zero rows to a roll-up.

Checks, all from file metadata so the whole pass is well under a second:

  1. FRESHNESS  every chain output was written during this run
  2. STRUCTURE  every monthly workbook has real Excel tables, and nothing
                unexpected is sitting in the year folder
  3. VOLUME     row counts are non-zero and have not collapsed since last run
  4. HYGIENE    no orphaned Excel process is still holding a workbook open

Exit code 1 on any failure, which surfaces as a non-zero LastTaskResult on the
scheduled task - and the Sell In Report Generator routine already reports that.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import zipfile
from datetime import datetime, timedelta

SALES = r"D:\SCARLETT_512\SCARLETT-329\SOM\Data\Report\Sales"
HERE = os.path.dirname(os.path.abspath(__file__))
HISTORY = os.path.join(HERE, "chain_history.json")

PARQUET = os.path.join(SALES, "Sales csv", "combined_sales.parquet")
SELL_IN = {
    "Offline MT": os.path.join(SALES, "Sell In", "Sell_In_Offline_MT", "{year}_Offline MT.xlsx"),
    "Offline GT": os.path.join(SALES, "Sell In", "Sell_In_Offline_GT", "{year}_Offline GT.xlsx"),
}

MONTHLY_RE = re.compile(r"^\d{4} \d{2} [A-Z][a-z]{2}\.xlsx$")
YEARLY_RE = re.compile(r"^\d{4} Report\.xlsx$")

# A month can legitimately shrink a little when back-dated deliveries land
# elsewhere, so only flag a real collapse.
DROP_TOLERANCE = 0.02

problems = []
warnings = []
report = []


def fail(msg):
    problems.append(msg)
    report.append(f"  FAIL  {msg}")


def warn(msg):
    warnings.append(msg)
    report.append(f"  WARN  {msg}")


def ok(msg):
    report.append(f"  ok    {msg}")


def table_rows(xlsx_path):
    """Row count across a workbook's Excel tables, read from the zip only."""
    total = 0
    tables = 0
    try:
        with zipfile.ZipFile(xlsx_path) as z:
            for name in z.namelist():
                if not name.startswith("xl/tables/") or not name.endswith(".xml"):
                    continue
                tables += 1
                m = re.search(rb'ref="[A-Z]+(\d+):[A-Z]+(\d+)"', z.read(name))
                if m:
                    total += int(m.group(2)) - int(m.group(1))  # minus header
    except Exception as exc:
        return None, 0, str(exc)
    return total, tables, None


def check_freshness(label, path, since):
    if not os.path.exists(path):
        fail(f"{label}: MISSING ({path})")
        return False
    mtime = datetime.fromtimestamp(os.path.getmtime(path))
    if mtime < since:
        fail(f"{label}: not rewritten this run - last written {mtime:%Y-%m-%d %H:%M:%S}")
        return False
    ok(f"{label}: written {mtime:%H:%M:%S}")
    return True


def check_volume(label, count, history):
    if count is None:
        fail(f"{label}: could not read row count")
        return
    if count == 0:
        fail(f"{label}: ZERO rows")
        return
    prev = history.get(label)
    if prev:
        delta = count - prev
        if delta < 0 and abs(delta) > prev * DROP_TOLERANCE:
            pct = abs(delta) / prev * 100
            fail(f"{label}: dropped {abs(delta):,} rows ({pct:.1f}%) - was {prev:,}, now {count:,}")
            return
        sign = "+" if delta >= 0 else ""
        ok(f"{label}: {count:,} rows ({sign}{delta:,})")
    else:
        ok(f"{label}: {count:,} rows (no baseline yet)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", help="ISO timestamp the run started; defaults to 6h ago")
    ap.add_argument("--year", type=int, default=datetime.now().year)
    args = ap.parse_args()

    since = datetime.fromisoformat(args.since) if args.since else datetime.now() - timedelta(hours=6)
    year = args.year

    history = {}
    if os.path.exists(HISTORY):
        # utf-8-sig so a BOM (anything that rewrites this file from PowerShell
        # leaves one) does not read as corruption. An unreadable history must be
        # loud: silently falling back to "no baseline" would disable every
        # volume check below without changing the exit code.
        try:
            with open(HISTORY, encoding="utf-8-sig") as fh:
                history = json.load(fh).get("counts", {})
        except Exception as exc:
            history = {}
            history_error = f"history unreadable ({type(exc).__name__}: {exc}) - volume checks have no baseline"
        else:
            history_error = None
    else:
        history_error = None

    counts = {}
    report.append(f"Validating chain outputs written since {since:%Y-%m-%d %H:%M:%S}")
    if history_error:
        fail(history_error)

    # ---- 1/2. monthly workbooks: freshness is only expected for prev+current
    report.append("\nSTRUCTURE - monthly workbooks")
    year_dir = os.path.join(SALES, str(year))
    if not os.path.isdir(year_dir):
        fail(f"year folder missing: {year_dir}")
    else:
        for name in sorted(os.listdir(year_dir)):
            path = os.path.join(year_dir, name)
            if not name.lower().endswith(".xlsx"):
                continue
            if name.startswith("~$"):
                warn(f"lock file present (workbook open?): {name}")
                continue
            if YEARLY_RE.match(name):
                continue
            if not MONTHLY_RE.match(name):
                # The roll-up and the Sell In queries glob this folder; anything
                # unexpected here gets read and double-counted.
                fail(f"stray file in year folder (would be double-counted): {name}")
                continue
            rows, tables, err = table_rows(path)
            if err:
                fail(f"{name}: unreadable ({err})")
            elif tables == 0:
                # Contributes zero rows to the roll-up, silently.
                fail(f"{name}: NO Excel tables - contributes zero rows")
            else:
                counts[name] = rows
                check_volume(name, rows, history)

    # ---- 1. chain outputs written this run
    report.append("\nFRESHNESS - outputs rewritten this run")
    now = datetime.now()
    for offset in (0, -1):
        m = (now.month - 1 + offset) % 12 + 1
        y = year if now.month + offset >= 1 else year - 1
        stamp = datetime(y, m, 1)
        fname = f"{stamp:%Y %m %b}.xlsx"
        check_freshness(fname, os.path.join(SALES, str(y), fname), since)

    check_freshness("combined_sales.parquet", PARQUET, since)
    for label, tmpl in SELL_IN.items():
        check_freshness(label, tmpl.format(year=year), since)

    # ---- 3. volumes
    report.append("\nVOLUME - row counts")
    try:
        import pyarrow.parquet as pq
        counts["combined_sales.parquet"] = pq.ParquetFile(PARQUET).metadata.num_rows
    except Exception as exc:
        counts["combined_sales.parquet"] = None
        report.append(f"  (parquet read error: {exc})")
    check_volume("combined_sales.parquet", counts.get("combined_sales.parquet"), history)

    for label, tmpl in SELL_IN.items():
        path = tmpl.format(year=year)
        if os.path.exists(path):
            rows, tables, err = table_rows(path)
            counts[label] = rows
            check_volume(label, rows, history)

    # ---- 4. hygiene
    report.append("\nHYGIENE")
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq EXCEL.EXE", "/NH"],
            capture_output=True, text=True, timeout=30,
        ).stdout
        if "EXCEL.EXE" in out:
            warn("EXCEL.EXE still running - a refresh may have left a workbook locked")
        else:
            ok("no orphaned Excel process")
    except Exception as exc:
        warn(f"could not check Excel processes: {exc}")

    # ---- report
    print("=" * 68)
    print(" CHAIN VALIDATION")
    print("=" * 68)
    print("\n".join(report))
    print("-" * 68)

    # Only record a baseline from a clean run, so a bad run cannot quietly
    # become the new normal that the next comparison is measured against.
    if not problems:
        try:
            with open(HISTORY, "w") as fh:
                json.dump(
                    {"updated": datetime.now().isoformat(timespec="seconds"),
                     "counts": {k: v for k, v in counts.items() if v}},
                    fh, indent=2,
                )
        except Exception as exc:
            print(f"(could not write history: {exc})")

    if problems:
        print(f"RESULT: FAILED - {len(problems)} problem(s), {len(warnings)} warning(s)")
        for p in problems:
            print(f"  - {p}")
        return 1
    print(f"RESULT: PASSED ({len(warnings)} warning(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main())
