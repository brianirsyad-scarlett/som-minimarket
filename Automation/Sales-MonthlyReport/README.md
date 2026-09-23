# Sales-MonthlyReport

Rebuilds `Data\Report\Sales\<year>\<year> MM Mon.xlsx` (e.g. `2026 09 Sep.xlsx`)
without opening Excel. It is a port of the Power Query M that is embedded in
those workbooks, so the output is the same file the "Refresh All" button used to
produce — same three sheets, same 29 columns, same Excel tables.

A full month takes about **3–4 minutes** (~1 min reading, ~3 min writing the
770k-row Online sheet) and produces a ~70 MB workbook.

## Setup

```powershell
.\setup.ps1
```

Creates `.venv` and installs polars / fastexcel / python-calamine / openpyxl,
plus pandas / pyarrow for the CSV + Parquet step the daily run chains onto the
end. No credentials, no browser — this job only reads and writes local files.

## Use

```powershell
.\run.ps1 -DryRun                 # row counts only, writes nothing
.\run.ps1                         # rebuild the current month
.\run.ps1 -Month prev             # rebuild the previous month
.\run.ps1 -Month 2026-09
.\run.ps1 -Month 2026-07,2026-08  # backfill
.\run.ps1 -OutDir D:\scratch      # trial run into a scratch folder
```

## Schedule it

```powershell
.\register_schedule.ps1                     # daily 02:00
.\register_schedule.ps1 -At 07:30
.\register_schedule.ps1 -Remove
```

Registered as **Sell In Report**, daily at 02:00. It runs
`run_daily.ps1`, which does two things in order:

1. Rebuild the **previous + current month** workbooks — Odoo and Accurate keep
   back-dating deliveries for a week or two after month end, and refreshing only
   the current month would leave those out of the yearly roll-up.
2. Run `Data\Report\Sales\Sales csv\primary_sales_csv_and_parquet.py`, which
   re-exports every monthly workbook to CSV and rebuilds `combined_sales.parquet`.

The order matters: step 2 only re-converts a sheet when its `.xlsx` is newer than
its `.csv`, so it has to run after step 1 has written the workbooks. Step 2 runs
even if step 1 fails (so a bad source file does not leave the parquet stale too),
but the task's exit code still reports the failure.

Step 2 is invoked with this folder's `.venv` python and `PYTHONUTF8=1` — that
script prints check marks and arrows, which raise `UnicodeEncodeError` on a
redirected stdout without it, killing the run under Task Scheduler.

**Time it after the jobs that refresh the inputs.** The Anchanto quarter file and
the Odoo export are themselves written by other processes; if this job reads one
mid-write it will build from a half-written file.

## What it builds

Ported one-for-one from the M in `customXml/item1.xml → Formulas/Section1.m`:

| Query | Source |
|---|---|
| `E_Stock_Report` | `Report\Sales\E-Stock 2025\E-Stock 2025.xlsx` table `E_Stock_2025` |
| `Odoo_Report` | `Report\Sales\Odoo\Odoo Report.xlsx` table `Odoo_Report` |
| `Accurate_Report` | `Report\Sales\Accurate Report 2025\0. 2025 Accurate.xlsx` table `Accurate_Report` |
| `Anchanto_Report (Qn)` | `Report\Sales\Anchanto Report\Anchanto Report <year> Q<n>.xlsx`, every table |
| `Master Product` | `Master Data Sales\Matrix\Master Data Sales.xlsx` sheet `Product`, skip 3 |
| `Master Region` | same workbook, sheet `Area`, skip 5 |
| `Master Channel` | same workbook, sheet `Channel` |

Then:

- **Offline sheet** — E-Stock + Odoo + Accurate combined, with the Product /
  Region / Channel lookups joined on, filtered to the month and to
  `Channel Group <> "ONLINE"` (rows with no Channel Group are kept, as in the M).
- **Online (1) / (2)** — the same base with Anchanto appended, filtered to
  `Channel Group = "ONLINE"` and cut at day 15. The split exists only because a
  month of Online rows does not fit in one sheet; change it with
  `-SplitDay 10,20` for three sheets.

The quirks of the M are reproduced deliberately, including:

- `Text.Contains` is case-sensitive, and on a null it yields null, which
  `Table.SelectRows` drops. So Odoo's `not Text.Contains([Checked], "Reported")`
  filter really only removes rows with an empty `Checked`, and Accurate's
  `EMPLOYE` filter only removes rows with no customer name.
- `Master Product` de-duplicates on `ItemName` *before* uppercasing it, so names
  differing only by case survive and can become duplicate lookup keys. The script
  logs a warning if that ever starts multiplying rows.
- `type date` drops the time component — Odoo's `SentOn` carries one on ~45% of
  rows, and the month filter is applied to the truncated date.
- Joins match null-to-null, the way `Table.NestedJoin` does.

## Things worth knowing

**The output must keep its Excel tables.** `<year> Report.xlsx` reads this folder
with `Folder.Files` + `Excel.Workbook` and keeps only `Kind = "Table"`. A sheet
without a ListObject is invisible to it. The script writes real tables
(`_2026_09_Sep_Offline`, `_2026_09_Sep_Online__1`, …) and verifies nothing else
is needed. If an Anchanto workbook ever arrives without tables — which happens
when it is rewritten by a script — the M would silently read **zero rows** from
it; this script falls back to reading its sheets and logs a loud warning.

**Nothing extra may be left in `Data\Report\Sales\<year>\`.** That same glob only
skips names containing `$`, `Report` or `.ini`, so a stray `… copy.xlsx` or
`… bak.xlsx` there would be counted twice in the yearly figures. That is why the
staging file (`.tmp\`) and the backups (`backups\`) live in this folder instead.

**Backups.** Every run copies the workbook it is about to replace into
`backups\<name> <timestamp>.xlsx`. Prune that folder now and then — they are
~70 MB each. `-NoBackup` turns it off.

**If the workbook is open in Excel** the final rename fails with a clear message
and the finished file is left in `.tmp\` — close Excel and re-run, or just move
it across by hand.

`run.log` next to this README gets every run appended to it.
