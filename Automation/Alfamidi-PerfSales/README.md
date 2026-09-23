# Alfamidi Performance Sales — report requester

Queues the **Performance Sales** modular reports on `midi-b2b.et.r.appspot.com`.

This script only *asks* for the reports. It never receives a file — the portal
answers:

> Request Download File Berhasil
> Link Download File Akan dikirim Via Email Jika File Sudah Tersedia

The files arrive later as emailed download links, collected by
[`..\Mail-ReportLinks`](../Mail-ReportLinks/README.md).

```
 this script  ──►  portal queues report  ──►  email with link  ──►  Mail-ReportLinks  ──►  Data\…
   (daily)                                      (~24h to live)          (hourly)
```

Different from `..\Alfamidi-MarketShare`, which posts to
`/marketshare/modular/xls` and gets xlsx bytes back in-session. Same login,
same host — different delivery model.

## What it requests

Per the agreed spec:

| Report | `tipe_prf` | Endpoint | Categories |
|---|---|---|---|
| Performance by Item **by Branch** by Day | `5` | `/perfsales/modular/bibdbs/by-branch` | `all` in one file |
| Performance by Item **by Store** by Day | `4` | `/perfsales/modular/bibdbs/request-download` | **one request per category** |

Both in **Qty and Value** — but **the two report types use different
calendars**, and this is not cosmetic:

| Report | Calendar | Source |
|---|---|---|
| by-branch | current month-to-date + full previous month | `period_utils.branch_months()` |
| by-store | three **10-day periods**, newest first (1–10, 11–20, 21–end) | `period_utils.rolling_periods()` |

Both come from `Data\Sent Email\period_utils.py`, which this script **imports
rather than reimplements**. That module is shared with
`3_upload_and_distribute.py`, and the two must agree.

> **Why by-store must use 10-day periods.** That pipeline derives each file's
> period from its start date and deletes anything whose end date overshoots the
> boundary, tagged *"invalid (past period end)"*. A month-to-date by-store file
> starting on the 1st and ending mid-month is exactly that shape — it would be
> downloaded, then thrown away on the next pipeline run. `test_payload.py`
> asserts no by-store window can overshoot.

With today's 6 categories that is **40 requests per run**:

```
by-branch   1 cat  x 2 units x 2 months  =  4
by-store    6 cats x 2 units x 3 periods = 36
                                           ──
                                           40
```

Previous month and closed periods are re-requested every day on purpose —
late-posted transactions get backdated, and this is what catches them.

### Stok

`-Indicator b` requests **Stok** instead of Selling Out. Filenames come out as
`detail_performance_by_branch_Stok_<unit>_…`, matching the pair already in
`Alfamidi\Stock\`, and `Mail-ReportLinks` routes them there.

### Categories are discovered, never hardcoded

The category list is read off the live page at runtime. Alfamidi exposes 6
today; if a 7th appears, the next run requests it with no code change, and logs
`new category since this script was written`. There is a hardcoded fallback
list used **only** if the dropdown can't be parsed — and it warns loudly,
because in that state a new category would be silently missed.

## Setup

```powershell
cd "D:\SCARLETT_512\SCARLETT-329\SOM\Automation\Alfamidi-PerfSales"
.\setup.ps1
```

Then fill `ALFAMIDI_PASSWORD` in `.env` — same credentials as
`..\Alfamidi-MarketShare\.env`.

```powershell
icacls ".env" /inheritance:r /grant:r "$env:USERNAME:(R,W)"
```

## First run

See the plan without sending anything — no emails, no requests:

```powershell
.\run.ps1 -DryRun
```

It still logs in (it has to, to read the real category list), then prints all 28
filenames it would request.

Watch it drive the browser the first time:

```powershell
.\run.ps1 -Headful -Report branch -Months current
```

That fires only 2 requests, which is the gentlest way to confirm the whole chain
works before unleashing all 28.

## Schedule

```powershell
.\register_schedule.ps1
```

Daily at 05:00. **Not hourly** — see the throttle below, and the data only moves
once a day. `..\Mail-ReportLinks` is the piece that runs hourly, because it has
to catch links inside their ~24h lifetime.

## The 1-hour throttle — and why the text match matters

The portal refuses a repeat of the same report within an hour. Captured live
2026-09-18:

```json
{
  "code": "T",
  "result": "Request Download File Sudah diajukan dalam 1 jam terakhir \nsilahkan cek email untuk download file atau menunggu request file sebelumnya selesai"
}
```

Compare it with a genuine queue:

```json
{
  "code": "T",
  "result": "Request Download File Berhasil \nLink Download File Akan dikirim Via Email Jika File Sudah Tersedia"
}
```

**Both carry `code: "T"`.** HTTP status is 200 either way. Nothing structural
separates a queued report from a rejected one — **only the message text does**.

This is not a cosmetic detail. Keying on the code alone would classify a
throttled request as queued and write a token into `requests.json` for a report
that is never generated, leaving an orphan waiting on an email that never
arrives. `classify_reply()` therefore checks the message text *before* the code,
and `test_payload.py` pins both real replies so a future refactor cannot quietly
reverse that order.

Throttled requests are treated as a benign no-op:

- logged at INFO, not ERROR
- **not** recorded in `requests.json`
- exit code stays 0, so the scheduled task does not go red

Re-running by hand shortly after a scheduled run is harmless — everything
reports as throttled and exits cleanly.

## Matching files back to requests

`filename` is client-supplied and the portal honours it, so every request
embeds a 6-character token, recorded in `requests.json`:

```json
{
  "a3f9c1": {
    "filename": "detail_performance_Selling_Out_Value_BODY_LOTION_All_Item_NASIONAL_All_Store_20260901_20260918_a3f9c1",
    "report": "Performance by Item by Store by Day",
    "category": "BODY LOTION",
    "unit": "Value",
    "window": "2026-09 MTD",
    "requested_at": "2026-09-18T05:00:12"
  }
}
```

That token is the only reliable way to tie a file arriving hours later, out of
order, among 28 near-identical siblings, back to the request that asked for it.
`requests.json` is written after **every** request, not at the end, so a run
that dies halfway still leaves its already-sent tokens resolvable.

## Options

```powershell
.\run.ps1 -Report store -Months current -Unit v          # narrow it down
.\run.ps1 -Categories 3251,3252                          # specific categories
.\run.ps1 -Delay 5                                       # gentler pacing
.\run.ps1 -DryRun -Detailed                              # plan + verbose
```

| Flag | Default | Notes |
|---|---|---|
| `-Report` | `both` | `branch`, `store`, `both` |
| `-Months` | `both` | `current`, `previous`, `both` |
| `-Unit` | `both` | `q`, `v`, `both` |
| `-Categories` | all found | comma-separated codes |
| `-Delay` | `2` | seconds between requests |

## Troubleshooting

`run.log` records every run.

| Message | Fix |
|---|---|
| `Login rejected` | Wrong `ALFAMIDI_USERNAME` / `ALFAMIDI_PASSWORD` |
| `Session expired - the server returned the login page` | Portal signed you out mid-run; rerun |
| `Could not reach the Performance Sales page` | Portal moved the page or changed the handoff |
| `Could not read the category dropdown` | Page layout changed — **fix this promptly**, new categories are being missed |
| `throttled` | Normal within an hour of a previous run |
| `refused: …` | The portal rejected the parameters; run `-Detailed` |

If Alfamidi redesigns the form, `build_payload()` and `discover_categories()`
are the two places that need updating. `test_payload.py` pins the field names
against the captured HAR — run it after any change:

```powershell
.\.venv\Scripts\python.exe test_payload.py
```

## Status

Fully exercised live on 2026-09-18 — a complete 28-request run:

- login → handoff → Performance Sales page ✔
- 6 categories discovered off the live page ✔
- **by-branch** (`tipe_prf=5`) — 4 requests accepted ✔
- **by-store** (`tipe_prf=4`) — all 24 requests accepted ✔
- throttle correctly detected and excluded from the manifest ✔
- `requests.json` holds 28 tokens, no duplicates, every category covered
  4× (2 units × 2 months) ✔

**The one gap left:** no emailed link has been collected end-to-end yet, because
the Power Automate flow in `..\Mail-ReportLinks` has not been created. Until it
exists, the links have to be opened by hand. The open question it answers is
whether the link is a self-contained signed URL or needs a portal session — run
`..\Mail-ReportLinks -DryRun -Detailed` once emails are landing in the drop
folder.
