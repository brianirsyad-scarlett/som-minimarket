# som-odoo-report-automation

Runs the local Odoo report pipeline on GitHub Actions instead of locally:
pulls data from Odoo, rebuilds quarter reports, rebuilds the all-time master
report, and uploads everything to GCS. Two workflows:

- **Odoo Quarterly Export** (`odoo-export.yml`) - runs 4x/day, refreshes only
  the current quarter.
- **Odoo Weekly Backfill** (`odoo-weekly-backfill.yml`) - runs weekly, re-runs
  every quarter from 2026 Q1 through the current one, since Odoo/Accurate
  keep back-dating entries into already-closed quarters for a while.

Both mirror these three local scripts (copies live in this repo, with paths
adapted for GitHub's Linux runners instead of the local D: drive):

1. `odoo_quarterly_export.py` - pulls Sales Order + Sales Analysis from Odoo.
2. `build_odoo_report.py --replace-production` - rebuilds
   `<seq>. <year> Qn Odoo Report.xlsx` using the two files from step 1 plus
   `Master Data Customer Odoo.xlsx` and `Invoice Lumbung.xlsx` (downloaded
   from GCS first).
3. `build_master_report.py --replace-production` - combines every quarter's
   report into the all-time `Odoo Report.xlsx`.

The local versions of these scripts and their source files are untouched and
still run as before - this is a parallel path, not a replacement, until it's
proven reliable.

## No manual quarter-list editing needed

`quarter_files.py` computes which quarter/year files exist from the calendar
date (2026 Q1 onward, following `"<seq>. <year> Qn"`, seeded by one fixed
2025 legacy annual file that predates this pipeline). `build_master_report.py`
and `download_from_gcs.py` both use it instead of a hardcoded file list, so
when Q4 2026 (or any later quarter) starts, both workflows pick it up
automatically - nothing in this repo needs editing.

## Schedules (cron is UTC; times below are Asia/Jakarta, WIB, UTC+7)

- Quarterly export: every 3 hours starting 00:00 WIB daily
  (`cron: "0 2,5,8,11,14,17,20,23 * * *"`)
- Weekly backfill: Monday 00:00 WIB (`cron: "0 17 * * 0"`, i.e. Sunday 17:00 UTC)

## GCS layout this pipeline reads and writes

- `gs://bucket_som/sales_parquet/raw/primary/odoo/` - every quarter/year
  Odoo Report file, this run's raw Sales Order/Sales Analysis exports, and
  the combined `Odoo Report.xlsx`.
- `gs://bucket_som/sales_parquet/raw/master data/Master Data Customer Odoo.xlsx`
- `gs://bucket_som/sales_parquet/raw/primary/invoice/Invoice Lumbung.xlsx`

## Setup

1. In this repo's Settings -> Secrets and variables -> Actions, add:
   - `ODOO_URL`
   - `ODOO_DB`
   - `ODOO_USERNAME`
   - `ODOO_PASSWORD`
   - `GCP_SA_KEY` - the full contents of the GCS service-account JSON key
     (the same one the local pipeline uses at
     `Data\Sent Email\sales-som datawarehouse 490008.json`)

   (Values are never committed to this repo - see `.env.example` for the
   format if running locally instead.)

2. Both workflows also run on demand: Actions tab -> pick the workflow ->
   "Run workflow".

3. Outputs land in GCS as described above. They're also attached as a
   workflow artifact as a backup/manual-download option (30-day retention).

## Notes

- Requires Odoo to be reachable over the public internet from GitHub-hosted
  runners (confirmed OK for this instance).
- The weekly backfill re-exports and rebuilds *every* quarter each run - as
  more quarters accumulate this will take proportionally longer.
