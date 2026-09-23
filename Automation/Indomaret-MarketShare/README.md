# Indomaret Market Share — automated download

**Architecturally different from the Alfamart/Alfamidi automations** — Indomaret's
B2B portal (`connect.indomaret-bisnis.com`) is a React SPA whose "Market Share"
report is an embedded **Power BI** report, not a server-rendered form. There is
no browser needed at all here: every step is a plain JSON HTTPS call, done with
Python's `requests` library.

Reports land in:
`D:\SCARLETT_512\SCARLETT-329\SOM\Data\Report\Sales\Minimarket\Indomaret\Market Share\Raw`

Your credentials live only in `.env` on this PC.

## How it works

1. `POST api-idmsso.indomaret-bisnis.com/.../auth/login` — email + password.
2. `POST .../auth/otp/validate` — email + password + TOTP code → `accessToken`.
3. `POST api-dcp.indomaret-bisnis.com/.../dashboard/detail` — using the
   `accessToken`, asks the Indomaret backend for a **Power BI embed token**
   scoped to the Market Share report (this is the same token the browser's
   Power BI JS SDK would request when the report page loads).
4. `POST https://wabi-south-east-asia-d-primary-redirect.analysis.windows.net/export/xlsx`
   — using the embed token, replays the exact "Export data" query Power BI's
   own UI sends, with the month/category/etc. filters swapped in.

The exported sheet's cell A1 contains Power BI's own `"Applied filters: ..."`
text and the data table starts at row 3 — this is the same shape your existing
`Market Share\1_market_share_converter.py` already expects from a manual
export (it currently scans the parent `Market Share` folder, not `Raw` — point
it at `Raw`, or move files up, if you want it to pick these up automatically).

### One assumption worth knowing about

Chrome's HAR export strips `Authorization` header **values** for security, so I
could not directly observe the header format used for steps 3–4. The response
headers do confirm `Authorization` is the header name in play (it's listed in
`Access-Control-Allow-Headers`/`Access-Control-Expose-Headers`), and
`Authorization: Bearer <token>` is the standard, near-universal convention for
this kind of JWT-based API — so that's what this script sends. **If the first
run fails with a 401/403 on the embed-token or export step**, send me
`run.log` — it's a one-line fix to try a different header format.

Everything else (login payload shapes, the embed-token call, and the entire
Power BI export query — including all the DAX measure definitions) is an exact,
verified match against your captured traffic — see `verify` output during
development, or just diff `build_export_body()`'s output against the HAR
yourself.

---

## 1. Setup (once)

```powershell
cd "D:\SCARLETT_512\SCARLETT-329\SOM\Automation\Indomaret-MarketShare"
.\setup.ps1
```

No Chromium download here (unlike Alfamart/Alfamidi) — just a `.venv` and
`requests`/`pyotp`/`python-dotenv`.

## 2. Fill in `.env`

| Key | Value |
|---|---|
| `INDOMARET_EMAIL` | your DCP/B2B login email |
| `INDOMARET_PASSWORD` | your password |
| `INDOMARET_TOTP_SECRET` | the **base32 seed** behind your authenticator QR code |

Same caveat as Alfamart: `INDOMARET_TOTP_SECRET` is the enrollment seed
(`JBSWY3DPEHPK3PXP`-style), not the rotating 6-digit code. Without it, only
manual runs work (you'll be prompted for the code each time).

### Lock down the file

```powershell
icacls ".env" /inheritance:r /grant:r "$env:USERNAME:(R,W)"
```

## 3. Test it

```powershell
.\run.ps1 -Periode 2026-09 -Category "BEAUTY LIQUID SOAP"
```

No `-Headful` flag needed/available — there's no browser to watch. Check
`run.log` for what happened.

## 4. First-run backfill: January 2026 → current month, all categories

```powershell
.\run_backfill_jan_to_now.ps1
```

Logs in once, then downloads every month from Jan-2026 through the current
month × all 6 categories, overwriting anything already in `Raw`.

## 5. Schedule it

Nightly at **00:00**, previous month + current month, all categories:

```powershell
.\register_schedule.ps1
```

```powershell
.\register_schedule.ps1 -At 01:30   # different time
.\register_schedule.ps1 -Remove     # unregister
```

---

## Usage reference

```powershell
.\.venv\Scripts\python.exe indomaret_market_share.py --list
```

```powershell
.\.venv\Scripts\python.exe indomaret_market_share.py --periode 2026-09 --category all
.\.venv\Scripts\python.exe indomaret_market_share.py --periode 2026-01:2026-09 --category all --range-period YTD
```

### Parameters

| Flag | Default | Options |
|---|---|---|
| `--periode` | current month | `YYYY-MM`, a range `YYYY-MM:YYYY-MM`, a comma list, `all` (Jan-2026 → current month), or `recent` (previous + current month, used by the daily schedule) |
| `--category` | `all` | 6 categories (quote multi-word ones), or `all` |
| `--range-period` | `YoY` | `YoY`, `YTD` |
| `--unit` | `IDR` | `IDR`, `Qty` |
| `--brand` | `SCARLETT` | the brand filter (this report is scoped to your own brand) |

Categories (from the report's own slicer): BEAUTY LIQUID SOAP, BODY COLOGNE FOR
WOMEN, BODY LOTION FOR WOMEN, FACIAL WASH & SCRUB FOR WOMEN, SERUM ESSENCE,
SUNSCREEN.

---

## Troubleshooting

`run.log` in this folder records every run.

| Message | Fix |
|---|---|
| `Login rejected` | Wrong email/password in `.env` |
| `2FA rejected` | Wrong TOTP seed, or this PC's clock has drifted |
| `Could not get an embed token (HTTP 401/403)` | The `Authorization: Bearer` assumption above may be wrong — send me `run.log` |
| `Expected xlsx, got 'application/json'` | The export query was rejected — usually means the report/model IDs changed, or an invalid filter combination |
| Embed token expiring mid-run | Handled automatically — the script refreshes it once and retries |

If Indomaret redesigns this report, `WORKSPACE_ID`/`REPORT_ID`/`MODEL_ID` and
the DAX measure text inside `_EXPORT_TEMPLATE_TEXT` are what changed — you'd
need a fresh HAR capture to rebuild the template.
