# Alfamart Market Share — automated download

Logs in to `b2b.alfamart.co.id` (password + Google Authenticator), follows
**Laporan → Dashboard & Modular** into `b2b-np.alfamart.co.id`, and downloads the
Market Share modular report as `.xlsx`.

Reports land in:
`D:\SCARLETT_512\SCARLETT-329\SOM\Data\Report\Sales\Minimarket\Alfamart\Market Share\Raw`

Your credentials live only in `.env` on this PC. They are never sent anywhere
except to Alfamart's own login form.

---

## 1. Setup (once)

```powershell
cd "D:\SCARLETT_512\SCARLETT-329\SOM\Automation\Alfamart-MarketShare"
.\setup.ps1
```

This creates `.venv`, installs Playwright + pyotp, downloads Chromium, and copies
`.env.example` to `.env`.

## 2. Fill in `.env`

Open `.env` and set:

| Key | Value |
|---|---|
| `ALFAMART_USERNAME` | your B2B portal username |
| `ALFAMART_PASSWORD` | your B2B password |
| `ALFAMART_TOTP_SECRET` | the **base32 seed** behind your Authenticator QR code |

### Getting your TOTP seed

`ALFAMART_TOTP_SECRET` is **not** the rotating 6-digit code. It is the one-time
seed string (like `JBSWY3DPEHPK3PXP`) shown when 2FA was first enrolled.

- If you saved the QR code or the "can't scan it?" text key — that's the seed.
- If you didn't, you must **re-enroll 2FA** on the Alfamart portal and copy the
  text key this time before scanning.
- Without it, scheduled runs cannot work. You can still run manually: leave the
  field blank and the script will prompt you for the 6-digit code.

### Lock down the file

```powershell
icacls ".env" /inheritance:r /grant:r "$env:USERNAME:(R,W)"
```

## 3. Test it

Watch it drive the browser the first time:

```powershell
.\run.ps1 -Headful
```

Defaults reproduce your captured sample: Value / By Brand / Actual / MTD /
BEAUTY LIQUID SOAP / BRANCH / NASIONAL, for the current month.

Once it works, drop `-Headful` to run invisibly.

## 4. Schedule it

Registers a nightly task that downloads the **previous month + current month**,
every category, at **00:00**:

```powershell
.\register_schedule.ps1
```

(equivalent to `.\register_schedule.ps1 -Cadence Daily -At 00:00`)

Other options:

```powershell
.\register_schedule.ps1 -At 01:30                    # different time
.\register_schedule.ps1 -Cadence Monthly -At 00:00   # 3rd of each month only
.\register_schedule.ps1 -Remove                      # unregister
```

The task runs `run_daily.ps1`, which re-reads "previous month + current month"
at every run, so MTD figures stay current with no further edits. Anything
already in the `Raw` folder for those two months gets overwritten in place.

---

## Usage reference

```powershell
.\run.ps1 -Periode 2026-09 -Category 3222 -Format MTD -Branch NAS
```

List every valid code:

```powershell
.\.venv\Scripts\python.exe alfamart_market_share.py --list
```

Download all 14 categories in one session:

```powershell
.\.venv\Scripts\python.exe alfamart_market_share.py --category all --periode 2026-09
```

### Parameters

| Flag | Default | Options |
|---|---|---|
| `--periode` | current month | `YYYY-MM`, a range `YYYY-MM:YYYY-MM`, a comma list, `all` (Jan-2026 → current month), or `recent` (previous + current month, used by the daily schedule) |
| `--category` | `3222` | 14 codes, or `all` |
| `--format` | `MTD` | `MTD`, `YTD` |
| `--group` | `BRAND` | `BRAND` (By Brand), `PLU` (By Item) |
| `--year` | `ACT` | `ACT` (Actual), `LAST` (Last Year) |
| `--area` | `DC` | `DC` (Branch), `REG` (Regional) |
| `--branch` | `NAS` | `NAS` + 30 DC codes |
| `--report` | `2` | `1` Total Item by Month, `2` by Category by Month by Branch |

---

## Troubleshooting

`run.log` in this folder records every run.

| Message | Fix |
|---|---|
| `Login rejected` | Wrong username/password in `.env` |
| `2FA rejected` | Wrong seed, or this PC's clock has drifted — sync Windows time |
| `Session expired - returned the login page` | Portal signed you out mid-run; just rerun |
| `Expected the OTP page` | Portal changed its login flow — rerun with `-Headful` to see it |
| `Expected xlsx, got 'text/html'` | Usually an invalid parameter combination for that month |

If Alfamart redesigns the portal, the selectors in `login()` and the field names
in `build_payload()` are the two places that need updating.
