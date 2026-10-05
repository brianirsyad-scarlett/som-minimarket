# Alfamart B2B report automation -- working notes

Reconstructed from a browser HAR capture + live testing on 2026-09-11.

## Login (must use a real browser)

`b2b.alfamart.co.id/do_login.php` is behind bot-detection: a plain HTTP POST
with correct credentials gets a canned `"Username Tidak Valid"` JS-alert
response regardless of correctness. A real Selenium-driven Chrome browser is
NOT blocked, so login must go through Selenium.

Flow (see `alfamart_common.login_and_get_np_session`):
1. GET `https://b2b.alfamart.co.id/login.php` -> fill `uname`/`upass`, submit
2. -> redirected to `/validasi-otp.php` -> fill 6-digit TOTP code, submit
   (TOTP computed locally from `ALFAMART_TOTP_SECRET`, RFC 6238, stdlib only)
3. -> lands on `/index.php`, logged in
4. Open `https://b2b.alfamart.co.id/get_laporan_new_premium.php` in a new tab
   (the real "Laporan" > "Dashboard & Modular" link) -> SSO handoff sets a
   `session` cookie scoped to `b2b-np.alfamart.co.id`

The `PHPSESSID` (main site) and `session` (np site) cookies are both
**non-persistent browser-session cookies** -- they die when Chrome closes.
There is nothing to cache across runs; every run does a full login + OTP.

## Report API (b2b-np.alfamart.co.id) -- plain HTTP is fine here

Once you have the `session` cookie value, everything else is a normal
`POST .../path` with `Cookie: session=<value>` -- verified working from plain
`urllib`, no browser/WAF issue on this subdomain. All endpoints return JSON
despite `Content-Type: text/html`.

Category list (dynamic, don't hardcode): POST `/perfsales/by-cat-vs-ly` with
any valid period, read `result[].kode_grouping` / `desc_grouping`.

### Sell out by branch, by day (MTD)
POST `/perfsales/modular/bibdbs/by-branch`, twice (unit=v Value, unit=q Qty),
`category=ALL`, `periode_awal_bybranch=<1st of month>`,
`periode_akhir_bybranch=<today>`. Single file covers all branches/categories.

### Sell out by store, by day (MTD)
POST `/perfsales/modular/bibdbs/request-download`, twice (unit=v, unit=q),
`store=ALL`, **`category=ALL`**, same MTD date range.

`category=ALL` works here -- verified by downloading the result. The UI clicks
through categories one at a time, which is why the original HAR looked like
per-category was mandatory, but it isn't. One combined file per unit instead
of 13, so 4 triggered emails per run rather than 28.

The combined by-store file is big: ~357MB / 4.46M rows for a 10-day range
(a full month runs ~1.1GB). Too many rows for Excel (limit 1,048,576) but
fine for GCS/BigQuery, which is where these go.

**Chunking.** Because of that size, by-store is pulled in thirds -- days
1-10, 11-20, 21-EOM. Each run requests the *whole* window even when only part
of it has data yet, so the filename stays stable for the entire period and
each run overwrites one object instead of creating a new one per day (which
would otherwise leave ~30 overlapping files per month, and double-count if
anything reads the prefix as a table).

**Only the in-progress chunk is pulled.** Once a chunk's end date passes the
D+2 horizon its rows are frozen, so re-fetching it would move ~700MB of
identical data daily for nothing. Completed chunks just stay where they are,
in GCS and in the OneDrive `Alfamart` folder.

The tradeoff: a chunk gets its one complete pull on the single day its end
date reaches the D+2 horizon (e.g. the 11-20 window completes on the 22nd).
If the job doesn't run that day, that chunk keeps whatever partial data it
last had. Backfill it by hand with:

    python3 alfamart_trigger.py --chunk 2 --to 2026-09-25

by-branch stays month-to-date -- it's under 1MB and the monthly summary needs
the whole month anyway.

## D+2 data lag

Alfamart posts sell-out data two days late: on the 12th, the 10th is the
newest day with data. Everything is therefore requested up to
`today - 2 days`, not today -- otherwise the filename claims a range two days
wider than the data it contains. The month is derived from that data date,
not from today, so runs on the 1st and 2nd of a month correctly finish off
the previous month.

Separately, the last day or two of data keeps changing after it first
appears: on 2026-09-09 three rows came back higher a few hours later. That's
why the current chunk and the monthly summary are re-pulled and overwritten
rather than written once.

Both trigger endpoints return `{"code":"T","result":"Request Download File
Berhasil \nLink Download File Akan dikirim Via Email Jika File Sudah
Tersedia"}` immediately -- **no data or download link in the response.**
There is no "my downloads" list page either (checked, not present).

## Email delivery -- THE ONLY way to get the file, and it's time-limited

The real destination inbox for this account forwards to Gmail:
`b2b-np@smtp.sat.co.id` -> `brian.rinaldy@lmbg.co.id` (auto-forward rule) ->
`brian.rinaldy@gmail.com`. Subject: `File Excel B2B - <CATEGORY OR SCOPE>`.

Body contains a **presigned Google Cloud Storage URL**
(`https://b2bsat-bucket.storage.googleapis.com/excel/report/<filename>.csv?X-Goog-...`)
that needs **no authentication** to download. Confirmed via a raw-MIME decode
of a real test email: `X-Goog-Expires=86400` -- valid for a full 24 hours
from generation, not the ~400 seconds an earlier (corrupted) plaintext
extraction wrongly suggested. There's no urgency to grab it within minutes.

**Implication for scheduling:** by-store (per-category) emails typically land
within 1-2 minutes of being triggered; by-branch (nationwide) emails are
confirmed (by the user) to be much slower, up to roughly an hour. Given the
24h link validity, `run_alfamart_daily.sh` just polls every ~5 minutes for
up to ~90 minutes after triggering -- comfortably covers both cases without
needing to race an expiry.

### Email delivery mechanics (Outlook, brian.rinaldy@lmbg.co.id)

The portal's registered notification address is `brian.rinaldy@lmbg.co.id`
(Microsoft 365/Outlook), which already had rules filing incoming
"File Excel B2B ..." mail into an `Alfamart` subfolder (alongside sibling
`Alfamidi`/`Anchanto`/`Tokopedia` folders for other platforms). There's also
a `b2b forwarder` rule (condition: subject includes "B2B" -> action: forward
to brian.rinaldy@gmail.com) that delivers a copy to Gmail -- confirmed
working end-to-end with real test emails. Outlook's rule-based "Forward"
action prepends "FW:" to the subject and wraps the original message in the
body (that's normal, not a sign of manual forwarding).

The `<filename>.csv` in the URL already encodes indicator/unit/scope/category/
date-range/principal, e.g.:
`detail_performance_Selling_Out_Qty_BRANCH_NASIONAL_All_Store_WOMEN_PARFUME_EDT_%26_EXTRAIT_All_Item_O-0108_2026-09-01_sd_2026-09-11.csv`
-- safe to use as-is for the saved local filename.

## Outputs and destinations

Everything lands in `gs://bucket_som/sales_sell out_minimarket/alfamart/`
(note the `_`-then-space in "sales_sell out_minimarket" -- that's the real
folder name, not a typo):

| What | Destination prefix | Naming |
|---|---|---|
| by-store Value | `daily_sell_out_value/` | `detail_performance_Selling_Out_Value_BRANCH_NASIONAL_All_Store_All_Category_All_Item_O-0108_<from>_sd_<to>.csv` |
| by-store Qty | `daily_sell_out_qty/` | same, `_Qty_` |
| by-branch combined summary | `sell_out/` | `<YYYYMM>_Sell Out Alfamart_Sell Out.csv` (overwritten each run) |

The by-branch Value+Qty pair isn't uploaded raw -- `alfamart_summary_sell_out.py`
merges them into the monthly summary (a port of the user's
`1_summary_sell_out.py` + `2_csv_converter.py`, minus the intermediate .xlsx):
filter out `descp` containing "PLU K", sum by branch/product/date, outer-join
Value and Qty, drop rows where value == qty, emit
`Date,Branch,Product,Value IDR,Value Qty` as utf-8-sig.

**Why the monthly summary is overwritten every run:** Alfamart restates the
most recent day or two. Re-pulling month-to-date and overwriting picks those
corrections up -- verified on 2026-09-09, where three rows came back higher a
few hours after the first pull.

**Source file format:** pipe-delimited despite the `.csv` extension, with six
preamble lines before the real header (`kode_branch|branch_name|tgl|plu|descp|value`
for by-branch, `periode|kode_store|store|branch|plu|descp|value` for by-store).
Dates are `%d-%b-%y` ("01-sep-26").

**Disk:** each run pulls ~700MB of by-store CSVs into `exports/<date>/`. They
are deleted after a *verified* GCS upload; a failed upload leaves them for
retry. Without that cleanup this fills the disk at ~21GB/month.

## Credentials

`~/.alfamart.env` (chmod 600, not in OneDrive/git):
- `ALFAMART_UNAME`, `ALFAMART_UPASS` -- portal login
- `ALFAMART_TOTP_SECRET` -- base32 seed from the Google Authenticator setup
- `ALFAMART_GMAIL_ADDRESS`, `ALFAMART_GMAIL_APP_PASSWORD` -- IMAP access to
  the Gmail inbox that receives the forwarded download-link emails

## Why this can't be a "scheduled Claude cloud agent"

Login must originate from Selenium/Chrome on the user's own Mac/network (a
cloud sandbox's Chrome + IP would look like a new/foreign device to
Alfamart's login, and cloud agents don't have this Mac's persistent browser
profile or local `~/.alfamart.env` anyway). So the whole pipeline -- trigger
+ email poll + download -- runs as a **local launchd job**, same pattern as
the existing `run_2h_refresh.sh` (SCRWMS) and Tokopedia scripts. Email access
uses a Gmail App Password over IMAP locally, not the live MCP mail connector
(which only exists inside an interactive Claude session).
