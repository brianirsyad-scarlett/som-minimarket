# som-shopee-report-automation

Rebuilds `Shopee_bq.parquet` (the BigQuery-safe combined Shopee Star + Shopee
Mall dataset) on GitHub Actions, so that step no longer depends on the local
Mac, OneDrive, or local power/internet.

## How it fits with the local pipeline

The actual Shopee login and report download stays on the local Mac
(`shopee_scheduled_export.py`, not in this repo) - a real, already-trusted
browser session is what keeps those logins from looking suspicious to
Shopee, and that can't be safely replicated on a shared GitHub-hosted
runner. Every night that script:

1. Logs in, downloads the month-to-date and previous-month reports for both
   shops
2. Converts them to CSV
3. Mirrors the raw `.xlsx`/`.csv` files to
   `gs://bucket_som/sales_parquet/raw/online/shopee/<shop>/`

This repo picks up from step 3: it downloads whatever CSVs are currently in
that GCS prefix, rebuilds `Shopee_bq.parquet` with the same cleaning logic
the local pipeline uses, and uploads it to
`gs://bucket_som/sales_parquet/raw/online/shopee/Shopee_bq.parquet`. Because it always rebuilds
from GCS (not from OneDrive), it keeps working even on a night the Mac is
off - it just rebuilds from the most recent raw data that made it to GCS.

## Files

- `download_raw.py` - pulls every `.csv` under the GCS raw prefix for both
  shops into `work/inputs/Shopee Star` / `work/inputs/Shopee Mall`
- `build_bq_parquet.py` - cleaning/column logic ported from the local
  `shopee_parquet_converters.py` (`convert_shopee_bq`), BQ-column-safe output
- `gcs_copy.py` / `gcs_paths.py` - generic GCS get/put helper + path constants
- `notify.py` - emails on pipeline failure (Gmail SMTP)

## Secrets / variables (repo settings -> Secrets and variables -> Actions)

- `GCP_SA_KEY` - full JSON of the GCS service account key (same one the
  local pipeline and the other `som-*-automation` repos use)
- `SMTP_USER` / `SMTP_APP_PASSWORD` - Gmail address + app password for
  failure emails (optional; if unset, failures just show up in the Actions
  run log, no email)
- `NOTIFY_TO` (variable, optional) - comma-separated recipient list

## Schedule

Runs twice a night, 01:00 and 03:00 WIB, plus on manual dispatch. Both runs
are idempotent (full rebuild from whatever's in GCS), so two runs a night is
just a buffer in case the Mac's raw upload is still mid-mirror at 01:00.
