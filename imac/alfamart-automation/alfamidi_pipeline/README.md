# Alfamidi + Accurate automation -- working notes

Set up 2026-09-14. Reuses the GCS credentials/venv already established for
Alfamart and SCRWMS (`~/scrwms-automation/gcs-service-account.json`,
`~/scrwms-automation/venv`) rather than creating a new service account.

## Alfamidi -- what's different from Alfamart

Alfamart has its own Selenium+TOTP login step that fetches fresh data from
the portal every run (see `../README.md`). **Alfamidi has no equivalent
fetch step here.** The source files this pipeline reads --
`SOM/Minimarket/Alfamidi/{Sell Out,Daily Sell Out,Market Share}` -- are
expected to already be populated by the time this runs. As of this setup,
the working assumption (per Brian) is that these sync in via OneDrive from
salesops-512 (the Windows PC), where the actual portal download happens.
**This hasn't been independently verified** -- if the daily job starts
finding "nothing to process" for real periods, check whether that sync is
actually still happening.

### Pipeline steps (`run_alfamidi_daily.sh`, daily at 07:30 WIB)

1. `alfamidi_market_share.py` -- reads raw `*Market Share by Category by
   Month by Branch_*.xlsx` exports, extracts Brand/PLU/Market Share columns,
   writes `Market Share/Summary.xlsx`.
2. `alfamidi_summary_sell_out.py` -- pairs up
   `detail_performance_by_branch_Selling_Out_{Value,Qty}_*.csv` files,
   merges them (filtering `descp` containing "PLU K", dropping rows where
   value==qty), writes `<YYYYMM>_Sell Out Alfamidi.xlsx`.
3. `alfamidi_csv_converter.py` -- converts every `.xlsx` in the Sell Out
   folder to `.csv` (utf-8-sig).
4. `alfamidi_upload_and_distribute.py` -- cleans up superseded/invalid daily
   snapshot files, uploads the current-period by-store files + the by-branch
   summary to `gs://bucket_som/sales_sell out_minimarket/alfamidi/`, and
   mirrors everything to
   `OneDrive/SOM_iMac/iMac_Sales Ops/Alfamidi/` (same convention as the
   existing Alfamart mirror folder).

Step 4 needs today's 10-day period boundaries (1st/11th/21st), computed via
`period_index()`/`period_bounds()` in **`period_utils.py`**.

### KNOWN GAP: period_utils.py is still a placeholder

The real module lives on salesops-512 at
`D:\SCARLETT_512\SCARLETT-329\SOM\Data\Sent Email\period_utils.py` and has
not yet been copied here. The stub in this folder raises `NotImplementedError`
with instructions rather than guessing at the logic, so:

- Steps 1-3 run normally and produce real local output.
- Step 4 is **skipped** every run (logged clearly in `alfamidi_daily.log`,
  not silently wrong) until the real file replaces the stub at
  `alfamidi_pipeline/period_utils.py`.

Once copied in, no code changes are needed -- the wrapper already calls it.

## Accurate (`~/scrwms-automation/`)

Self-contained, no local SOM files touched at all: reads raw `.xlsx` from
`gs://bucket_som/raw_som/accurate/staging_accurate/raw_accurate/`, converts
to `.csv`, writes to `.../staging_accurate/` (skips files whose `.csv`
already exists there -- incremental, safe to re-run). `run_accurate_staging.sh`
runs it daily at 06:00 WIB via `com.scarlett.accurate.staging.plist`, logging
to `accurate_staging.log`.

## Install (one-time, run in Terminal on this Mac)

```bash
cp ~/scrwms-automation/com.scarlett.accurate.staging.plist ~/Library/LaunchAgents/
cp ~/alfamart-automation/alfamidi_pipeline/com.scarlett.alfamidi.daily.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.scarlett.accurate.staging.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.scarlett.alfamidi.daily.plist
```

Check a run's outcome afterwards in `alfamidi_daily.log` /
`accurate_staging.log` in each script's own folder.

## Why this wasn't built as a Claude cloud/scheduled-task automation

The Cowork device-bridge sandbox that built this (an isolated Linux VM with
these two folders mounted in) has its outbound network proxied, and that
proxy returns `403 Forbidden` for Google's OAuth/GCS endpoints
(`oauth2.googleapis.com`) -- confirmed by a live test run of
`accurate_staging.py` from inside it on 2026-09-14. So neither script's GCS
step can run from that sandbox, scheduled or not; only real macOS `launchd`
on this Mac (outside the bridge, with this Mac's own unrestricted network)
can. Same reasoning as Alfamart's Selenium step -- see `../README.md`.

## Open items

- [ ] Copy the real `period_utils.py` from salesops-512 into this folder.
- [ ] Verify what actually keeps `Sell Out`/`Daily Sell Out`/`Market Share`
      populated with fresh Alfamidi exports (assumed: OneDrive sync from
      salesops-512 -- not yet confirmed).
- [ ] Run the two `launchctl bootstrap` commands above (not yet done as of
      this writing).
