# Alfamidi Market Share — automated download

Logs in to `b2b.alfamidiku.com` (username + password only, no 2FA), follows
**Laporan → Dashboard & Modular** into `midi-b2b.et.r.appspot.com`, and downloads
the Market Share modular report as `.xlsx`.

Reports land in:
`D:\SCARLETT_512\SCARLETT-329\SOM\Data\Report\Sales\Minimarket\Alfamidi\Market Share\Raw`

Your credentials live only in `.env` on this PC. They are never sent anywhere
except to Alfamidi's own login form.

This is a sibling of the Alfamart automation in `..\Alfamart-MarketShare\`, adjusted
for Alfamidi's slightly different login and download form (see the docstring at
the top of `alfamidi_market_share.py` for the exact differences).

---

## 1. Setup (once)

```powershell
cd "D:\SCARLETT_512\SCARLETT-329\SOM\Automation\Alfamidi-MarketShare"
.\setup.ps1
```

This creates `.venv`, installs Playwright, downloads Chromium, and copies
`.env.example` to `.env`.

## 2. Fill in `.env`

Open `.env` and set:

| Key | Value |
|---|---|
| `ALFAMIDI_USERNAME` | your B2B portal username |
| `ALFAMIDI_PASSWORD` | your B2B password |

No TOTP/2FA seed needed — this portal doesn't have a second factor.

### Lock down the file

```powershell
icacls ".env" /inheritance:r /grant:r "$env:USERNAME:(R,W)"
```

## 3. Test it

```powershell
.\run.ps1 -Headful
```

Defaults: BODY LOTION / MTD / NASIONAL / By Brand / Actual, current month.

Once it works, drop `-Headful` to run invisibly.

## 4. Download Jan–Sep 2026, all categories, in one go

```powershell
.\run_jan_to_sep_2026_mtd.ps1
```

Logs in once, then downloads 9 months x 6 categories = 54 files, overwriting
anything already in the Raw folder.

## 5. Schedule it

Registers a nightly task that downloads the **previous month + current month**,
every category, at **00:00**:

```powershell
.\register_schedule.ps1
```

(equivalent to `.\register_schedule.ps1 -Cadence Daily -At 00:00`)

```powershell
.\register_schedule.ps1 -At 01:30   # different time
.\register_schedule.ps1 -Remove     # unregister
```

The task runs `run_daily.ps1`, which re-reads "previous month + current month"
at every run, so MTD figures stay current with no further edits.

---

## Usage reference

```powershell
.\.venv\Scripts\python.exe alfamidi_market_share.py --list
```

```powershell
.\.venv\Scripts\python.exe alfamidi_market_share.py --periode 2026-09 --category 3251
.\.venv\Scripts\python.exe alfamidi_market_share.py --periode 2026-01:2026-09 --category all
```

### Parameters

| Flag | Default | Options |
|---|---|---|
| `--periode` | current month | `YYYY-MM`, a range `YYYY-MM:YYYY-MM`, a comma list, `all` (Jan-2026 → current month), or `recent` (previous + current month, used by the daily schedule) |
| `--category` | `3251` | 6 codes, or `all` |
| `--format` | `MTD` | `MTD`, `YTD` |
| `--group` | `BRAND` | `BRAND` (By Brand), `PLU` (By Item) |
| `--year` | `ACT` | `ACT` (Actual), `LAST` (Last Year) |
| `--area-type` | `BRANCH` | `BRANCH`, `REG` (Regional) |
| `--area` | `NAS` | `NAS` + 12 DC/branch codes |
| `--report` | `2` | `1` Total Item by Month, `2` by Category by Month by Branch |

---

## Troubleshooting

`run.log` in this folder records every run.

| Message | Fix |
|---|---|
| `Login rejected` | Wrong username/password in `.env` |
| `Session expired - returned the login page` | Portal signed you out mid-run; just rerun |
| `Could not reach the Market Share page` | Portal changed its menu/handoff — rerun with `-Headful` to see it |
| `Expected xlsx, got 'text/html'` | Usually an invalid parameter combination for that month/category |

If Alfamidi redesigns the portal, `login()`, `open_modular()`, and the field
names in `build_payload()` are the places that need updating.
