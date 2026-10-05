# Tokopedia seller order export — automation

**What it does, every night at 00:00 (local Mac time):**

1. Reuses the saved Chrome session in `browser_profile/` (copied from your original
   `selenium_tokopedia_profile/` folder). Logs in again automatically if that session
   has expired, using the password in the macOS Keychain; if Tokopedia then asks for
   an emailed verification code, it tries to fetch that code from your inbox too
   (see **Email verification auto-fetch** below) before falling back to asking you
   to run `tokped_login.py` by hand.
2. **Phase 1 — trigger**: queues one export report per day for the rolling window
   **[today − 45 days, today]** (45 separate reports), one at a time. Right after
   triggering each day, it watches the Export history list for the one new report
   row that appears and records its EXACT filename for that day — see **Matching
   reports to dates** below for why this replaced a simpler approach that turned out
   to silently mismatch files.
3. **Phase 2 — download**: Tokopedia generates each report asynchronously (queued in
   its own "Export history" list, not an instant download) and at this account's
   order volume a report can take several minutes to finish. This phase polls that
   list and downloads each day's report by its exact recorded filename as soon as
   that filename's Download button is enabled, then renames the file to its target
   date.
4. Anything not ready within the polling window (`--download-timeout`, default 1800s
   = 30 min) is simply left for tomorrow's run to pick up — true for every day in
   the window except the single oldest one, which ages out and won't be retried. You
   can also resume a specific stuck run by hand with `--resume-dir` (see below)
   instead of waiting for tomorrow.
5. Copies whatever got downloaded into `SOM/Anchanto Report/E-Commerce Data/TikTok Seller Data/`
   on OneDrive (alongside the other marketplaces' seller-data exports).
6. **On success**, chains straight into the Anchanto reporting pipeline (same folder):
   `tiktok_converter.py` renames/copies each day's CSV into the `TikTok/` subfolder,
   then `parquet_converter_tiktok.py` rebuilds `TikTok.parquet` from every CSV in
   there (not just today's — it's the full historical dataset).
7. **Then uploads** the rebuilt parquet to GCS, and separately uploads each raw
   per-day export CSV to GCS as an audit trail — see **GCS upload** below.

## GCS upload

After `TikTok.parquet` rebuilds, `gcs_upload_tiktok.py` (in this folder, **not**
OneDrive — same reasoning as the eviction note above, no point exposing a new
script to that failure mode) uploads it to
`gs://bucket_som/sales_parquet/TikTok.parquet`, matching how the other per-source
parquet files already sit there (`Anchanto.parquet`, `BA_Sales.parquet`,
`Minimarket_Sales.parquet`, etc. — this was the first time TikTok's data joined them,
added 2026-09-23).

Auth uses the `metabase-restricted-access@datawarehouse-490008.iam.gserviceaccount.com`
service account key at `~/.config/gcloud/keys/odoo-service-account.json` (the same
one `BigQuery_Anchanto.py` relies on) — confirmed to have read/write access to that
bucket path. The script refuses to upload a 0-byte local file and verifies the
uploaded blob's size matches the local file before declaring success.

This is a completely separate pipeline from `BigQuery_Anchanto.py` (which processes
Anchanto's own `B2C_Order_Report_*.csv` files into a *different* `Anchanto.parquet` —
unrelated data, unrelated schedule). If GCS shows stale TikTok data, check this
script's own run in `~/Library/Logs/tiktok-pipeline-check.log`, not that one.

Separately, `gcs_upload_tiktok_raw.py` (also outside OneDrive, same reasoning)
uploads every raw per-day export CSV straight from `TikTok Seller Data/` to
`gs://bucket_som/sales_parquet/raw/ecommerce/tiktok/<filename>`, mirroring the
`raw/` staging convention `BigQuery_Anchanto.py` already uses for its own source
(`B2C_INPUT_PREFIX`). This is a plain audit trail of the untouched exports — it
doesn't feed `TikTok.parquet` or the BigQuery external table, which both continue
to read from the cleaned data as before. It re-uploads every CSV it finds each
time it runs (idempotent — same size in, same blob out), using an explicit 600s
upload timeout (the client library's 120s default isn't enough for this account's
largest daily export, ~100MB+ — confirmed failing consistently on `2026-08-08.csv`
until this was raised, 2026-09-24) with up to 3 retries per file on any other
transient failure before giving up on just that file and continuing with the rest.

## TikTok.parquet safety net (03:00 / 04:00)

`tiktok_converter.py` and `parquet_converter_tiktok.py` were originally written for a
Windows machine (hardcoded `D:\...` paths) and only later pointed at this Mac's OneDrive
paths — see git history / ask if that ever needs re-pointing back. Two things make the
parquet rebuild step less reliable than the export itself:
- If the 00:00 export fails, is skipped (Mac asleep), or the Mac later has no OneDrive
  connectivity, the chain above never reaches the parquet rebuild.
- **OneDrive can evict a CSV's local copy back to a cloud-only placeholder** even
  minutes after it was downloaded (its Files On-Demand storage optimization), which
  makes `pandas.read_csv` fail with `Errno 60: Operation timed out` (or, if the eviction
  lands mid-read, silently return an empty file → `No columns to parse from file`) for
  whichever files that hits. Keeping the `TikTok` folder marked **"Always Keep on This
  Device"** in Finder (already done) should stop new evictions, but isn't guaranteed if
  disk space ever gets tight.

`com.salesops.tiktok-pipeline-check` (a separate LaunchAgent, `tiktok_pipeline_check.sh`)
runs at **03:00 and 04:00** every night as a backstop: if `TikTok.parquet`'s modified
time is older than today's midnight, it re-runs `gcs_upload_tiktok_raw.py` +
`tiktok_converter.py` + `parquet_converter_tiktok.py` + `gcs_upload_tiktok.py` on its
own (this is also the same script `run_tokped_daily.sh` calls right after the export,
so there's only one place this logic lives). The raw-CSV upload is gated on that same
"not yet updated today" check — not because it depends on the parquet rebuild, but so
it only runs once per day (whichever invocation actually does the day's work), instead
of re-uploading everything again at both 03:00 and 04:00. Logs to
`~/Library/Logs/tiktok-pipeline-check.log`. A few individual CSVs occasionally failing
with the OneDrive eviction error above is not itself a failure worth chasing — check
that log's row count against the previous run's if you want to confirm nothing's
missing.

Before running either of the two OneDrive-hosted scripts, it also checks they haven't
themselves been evicted (0 disk blocks) and tries to force-materialize them (up to
20s) — confirmed necessary 2026-09-23: an evicted `.py` file silently executes as
empty (exit 0, no output) instead of erroring, which is exactly the kind of "looked
fine, did nothing" failure this whole safety net exists to catch. If that
materialization attempt itself fails, the log says so explicitly and the step is
skipped rather than run against what might be empty.

## Matching reports to dates

Tokopedia's Export history list only ever shows a report's own filename (e.g.
`All order-2026-09-16-18:15.csv`, named by the moment it was TRIGGERED, not the
date range it was filtered for) — no target-date text is rendered anywhere in that
row. Two things about that turned out to matter a lot:

- **Reports don't finish generating in the order they were triggered** (a report's
  generation time depends on that specific day's order volume), so an earlier
  version of this script that matched history rows to dates by *list position* /
  *completion order* silently produced files whose filename and actual contents
  were for different days — confirmed by cross-checking the "Created Time" column
  inside downloaded CSVs against their filenames. **If you ever see a `.csv` whose
  filename date doesn't match the order dates inside it, that's this bug in an
  older run — not a config problem.**
- **Tokopedia's filename only has minute-level precision.** Two days triggered
  inside the same clock minute get the literally identical, indistinguishable
  filename. The trigger step now deliberately waits for the clock minute to roll
  over before queuing the next day whenever needed, specifically to avoid this.

The fix: right after clicking Export for a given day, `trigger_all_exports` watches
the history list for the one new row that appears (confirmed live: within about a
second, while still showing as "generating") and records that filename as ground
truth for that day, before moving on. Phase 2 then downloads strictly by that exact
recorded filename — no guessing from position, timing, or order.

## Resuming a stuck/partial run

Every trigger run writes `exports/<run-timestamp>/triggered_map.json` (date → exact
report filename) and shrinks it as each day downloads, deleting it once nothing's
left. If a run times out with days still pending, resume just the downloading (no
new exports triggered — that would only queue duplicates) with:

```bash
python3 tokped_export.py --resume-dir exports/2026-09-16_172850 --download-timeout 1800
```

Lives outside OneDrive (like the other SCRWMS/Alfamart automations) because
launchd + a Chrome profile + OneDrive-synced downloads don't reliably mix.

**Known limitations:**
- **The Mac must be awake at 00:00**, same as any launchd `StartCalendarInterval`
  job — if it's asleep, launchd runs it once on wake instead, which could be hours
  late. If overnight runs are unreliable, either keep the Mac awake overnight
  (`caffeinate`, Energy Saver "Prevent sleep", or Power Nap-style wake-on-schedule)
  or consider moving the time back to something the Mac is reliably awake for.
- **Full 45-day run takes roughly 45–90+ minutes end to end**, confirmed on a real
  47-day backfill: Phase 1 alone took ~46 minutes (the minute-uniqueness wait above
  can add up to ~60s per day), plus however long Phase 2 needs to download whatever
  finished generating. A midnight job that's still running Phase 1/2 well past
  01:00-01:30 is expected, not stuck — check `~/Library/Logs/tokped-daily.log` for
  progress rather than assuming it's hung. If most nights end up timing out with
  many days still pending, worth reconsidering the "45 separate files every day"
  approach (e.g. only re-exporting the last few days nightly, full 45-day sweep
  weekly) rather than just raising `--download-timeout` further.
- If a day's downloaded file ever looks wrong, cross-check the CSV's own "Created
  Time" column against its filename and check that day's section of
  `~/Library/Logs/tokped-daily.log` — see **Matching reports to dates** above.

## Files

| File | Purpose |
|------|---------|
| `tokped_common.py` | shared driver setup, Keychain password lookup, login logic |
| `tokped_otp.py` | fetches the emailed verification code via Gmail IMAP when re-login needs one (optional, see below) |
| `tokped_login.py` | **manual** — run by hand to (re-)establish the saved session; pauses for you to solve CAPTCHA/verification yourself if auto-fetch doesn't apply |
| `tokped_export.py` | the daily exporter (trigger + poll/download); `--start`/`--end YYYY-MM-DD` to override the default 45-day window, `--download-timeout` to change how long phase 2 waits, `--resume-dir` to resume downloads for an existing run without re-triggering |
| `.tokopedia.env.template` | copy to `~/.tokopedia.env` and fill in a Gmail App Password for email-verification auto-fetch (optional) |
| `run_tokped_daily.sh` | wrapper the scheduler calls (lock file + logging); also fine to run by hand |
| `com.salesops.tokped-daily.plist` | the launchd daily schedule (installed to `~/Library/LaunchAgents/`) |
| `tiktok_pipeline_check.sh` | the CSV→parquet→GCS chain (materialization guard included) plus the 03:00/04:00 safety net; called by both `run_tokped_daily.sh` and its own LaunchAgent |
| `com.salesops.tiktok-pipeline-check.plist` | the 03:00/04:00 safety-net schedule (installed to `~/Library/LaunchAgents/`) |
| `gcs_upload_tiktok.py` | uploads `TikTok.parquet` to `gs://bucket_som/sales_parquet/TikTok.parquet` — see **GCS upload** above |
| `gcs_upload_tiktok_raw.py` | uploads each raw per-day export CSV to `gs://bucket_som/sales_parquet/raw/ecommerce/tiktok/` as an audit trail — see **GCS upload** above |
| `browser_profile/` | the Chrome profile/cookies (do not commit or sync this anywhere) |
| `exports/` | local landing zone for downloads before they're copied to OneDrive |

## Credentials

The password is in the macOS **login Keychain**, not in any file:

```bash
# view (will prompt for your Mac password/Touch ID):
security find-generic-password -a "brian.rinaldy@scarlett.co.id" -s "tokopedia-seller-automation" -w

# rotate it after changing the Tokopedia password:
security add-generic-password -a "brian.rinaldy@scarlett.co.id" -s "tokopedia-seller-automation" \
    -w 'newpassword' -A -U
```

## Email verification auto-fetch (optional)

If Tokopedia asks for an emailed verification code when the saved session expires,
`tokped_export.py` will try to fetch it automatically from your inbox via Gmail IMAP
instead of stopping the unattended run. To enable it:

```bash
cd ~/tokopedia-automation
cp .tokopedia.env.template ~/.tokopedia.env
```

1. Turn on 2-Step Verification for `brian.rinaldy@scarlett.co.id` if it isn't already
   (your Google Workspace admin must allow this, and must allow App Passwords/IMAP).
2. Generate an App Password at <https://myaccount.google.com/apppasswords>, named
   something like "tokopedia-automation".
3. `open -e ~/.tokopedia.env` and paste that app password in (not your normal login
   password), then `chmod 600 ~/.tokopedia.env`.
4. Make sure `run_tokped_daily.sh` sources it (see note in that script if not already
   wired in).

**This is unverified against a real code email** — it was built from the visible
screen text ("Enter verification code" placeholder) without a real sample to test
sender/subject matching against, since generating the App Password needs your Google
login. If it doesn't find the code on a real attempt, check
`~/Library/Logs/tokped-daily.log` for what it saw and tighten `SENDER_HINTS` /
`SUBJECT_HINTS` / `CODE_RE` in `tokped_otp.py` to match what actually arrived.
Without this set up, an expired session just falls back to asking you to run
`tokped_login.py` by hand, same as before.

## One-time setup

The scheduler (`com.salesops.tokped-daily.plist`) is already installed and loaded.
Before the first scheduled run, confirm the saved session still works:

```bash
cd ~/tokopedia-automation
python3 tokped_export.py --start 2026-09-01 --end 2026-09-02   # quick 2-day test
tail -f ~/Library/Logs/tokped-daily.log
```

If it reports the session expired and needs manual verification, run:

```bash
python3 tokped_login.py
```

then solve the CAPTCHA/verification in the browser window and press ENTER.

## Managing the schedule

```bash
launchctl list | grep tokped                                   # confirm registered
launchctl unload ~/Library/LaunchAgents/com.salesops.tokped-daily.plist   # pause
launchctl load ~/Library/LaunchAgents/com.salesops.tokped-daily.plist     # resume
```

Change the run time by editing the `Hour`/`Minute` in the `.plist`, then
`unload` + `load` again to pick it up.

## Heads-up

- **The Mac must be awake and logged in** at 00:00 (or it runs once on wake) — Chrome
  needs a real GUI session, this can't run headless without risking CAPTCHA loops.
- **Overlap protection**: `run_tokped_daily.sh` takes a lock (`/tmp/tokped_daily.lock`);
  a run still going when the next one fires is skipped.
- Everything is logged to `~/Library/Logs/tokped-daily.log`, and a one-line status
  (`OK`/`FAILED`) is appended to `TikTok Seller Data/_run_status.txt` each run.
- The old `1_sales_tokped_login.py` / `2_sales_tokped.py` in the OneDrive folder now
  just forward to `tokped_login.py` / `tokped_export.py` here, so old habits/shortcuts
  still work — but this folder is the real source going forward.
