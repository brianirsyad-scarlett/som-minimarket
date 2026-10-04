# som-sellthrough

OneDrive -> GCS -> `Distributor_Sales.parquet`, every 30 minutes on GitHub Actions.

1. `sync.py` copies the ten regional Sell Through workbooks from OneDrive to
   `gs://bucket_som/sales_parquet/raw/sell_through/Sell_Through_Offline_GT/`, replacing a file only
   when OneDrive holds a newer copy than the bucket.
2. `sell_through.py` compiles them and publishes `gs://bucket_som/sales_parquet/Distributor_Sales.parquet`
   (what BigQuery's `raw_offline_distributor_sales` reads). Production is backed up to
   `sales_parquet/backup_small/` first (30 kept), and a build that shrinks the table by more than 10%,
   changes its columns, or has too few valid dates is not published. An identical table is not republished.

3. `national_distribution.py` rebuilds the **previous, current and next calendar quarter** workbooks
   (`National Distribution Raw_<first>_<last>.xlsx`, created if missing) in the same bucket
   folder from that parquet plus `sales_parquet/raw/master data/Master Data Sales.xlsx` (table `Product`).
   It is a pandas port of the Power Query inside the laptop's workbooks, checked cell for cell against
   Excel's own output (189,524 rows, 0 differences). The previous file is backed up to
   `raw/sell_through/cloud/backup/` (3 kept). The window rolls forward on its own at each quarter change.
   The multi-quarter *Summary* workbook (Data Model, PivotTables, slicers) cannot be refreshed here: it needs desktop Excel.

## Repository secrets

Settings -> Secrets and variables -> Actions. **This repo is public: never commit a key or token.**
While `GCP_SA_KEY` or `MS_TOKEN_CACHE` is missing, every run ends green after the first step.

| Secret | Value |
|---|---|
| `GCP_SA_KEY` | Service-account key JSON with write access to `bucket_som` (same value as in `som-sell-in-report-automation`) |
| `MS_TOKEN_CACHE` | Full text of `token_cache.json` from `login.py` (Microsoft sign-in, `Files.Read`) |
| `AZURE_TENANT_ID` | Azure tenant ID of the app registration used by `login.py` |
| `AZURE_CLIENT_ID` | Application (client) ID of that app registration |

The rotating Microsoft refresh token is kept in `gs://bucket_som/_state/som-sellthrough/` between runs.
After a re-login, update `MS_TOKEN_CACHE`; the next run picks the new value up automatically.

## Starting it every 30 minutes

GitHub's own `cron` is only a backup: it can start hours late or not at all (it did not fire once in the first
2.5 hours here). The on-time start is the Power Automate flow "GitHub timer", the same one the other `som-*`
repos use: at each slot it creates an issue titled `RUN|sync.yml`, labels it `run`, and `dispatch.yml` starts
the workflow and closes the issue. Slots are rows in the flow's Schedule table (every 30 minutes).
Every run ends by re-enabling the workflow through the API, which stops GitHub's 60-day inactivity shutdown.

Run by hand: Actions -> "Sell Through to GCS" -> Run workflow (tick *force* to rebuild even if nothing changed).
