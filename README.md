# som-minimarket-automation

Cloud version of the minimarket sell-out and market-share collection for
**SAT** (Alfamart), **MIDI** (Alfamidi) and **IDM** (Indomaret), on GitHub Actions.

**Status (2026-09-24).** The cloud downloads everything into the draft
`gs://bucket_som/sales_parquet/raw/minimarket/…` and **publishes production**
from it - no laptop and no manual step:

| Production output | Written by |
|---|---|
| `sales_sell out_minimarket/alfamart|alfamidi/…` | `publish_production.py` (after each collect) |
| `sales_sell out_minimarket/indomaret/{sell_out,daily_sell_out,stock}/` | `publish_indomaret.py` (after the Indomaret download) |
| `sales_sell out_minimarket/alfamart|alfamidi/stock/` | `publish_production.py` (branch stock, newest Value+Qty pair per month) |
| `sales_parquet/Minimarket_Sales.parquet` | `build_parquets.py --sales` (after both of the above) |
| `sales_parquet/Minimarket_Market_Share.parquet` | `build_parquets.py --market-share` (after the market-share download) |

The laptop's B2B tasks are disabled, and its parquet mirror no longer uploads
the two minimarket parquets. Its four minimarket downloads still run as a
fallback copy on disk only.

## What it collects

| | Market Share | Sell Out by Branch | Sell Out by Store |
|---|---|---|---|
| **Alfamart** | `market-share.yml` | `b2b-fire.yml` → email → `b2b-collect.yml` | same |
| **Alfamidi** | `market-share.yml` | `b2b-fire.yml` → email → `b2b-collect.yml` | same |
| **Indomaret** | `market-share.yml` | `indomaret-daily.yml` (report 2) | `indomaret-daily.yml` (report 3) |

**Stock by branch:** Alfamart and Alfamidi through the same by-branch request with
indicator `b` = Stok (`--stock`, current + previous month, emailed like sell out);
Indomaret report 10 (`DAILY_STOCK_BRANCH_<D>`, a 3-day window per DC).

Draft layout: `sales_parquet/raw/minimarket/<chain>/<market_share|sell_out_branch|sell_out_store|stock>/`

## Schedule (WIB)

| Time | Workflow |
|---|---|
| 02:00 | Market share, all three chains, then **publish** `Minimarket_Market_Share.parquet` |
| 02:30 | Indomaret sell out by branch + by store, then **publish production** + `Minimarket_Sales.parquet` |
| 07:05 | Alfamart + Alfamidi: request sell-out reports - every request clicked twice; the 2nd reply proves the 1st registered |
| 08:05, 09:05 | Alfamart + Alfamidi: collect the emailed links, then **publish production** + `Minimarket_Sales.parquet` |

GitHub often starts scheduled runs late. None of these depend on an exact minute.

## How the Alfamart / Alfamidi part works

The portals don't return files. They email a signed link per report, valid
about 24 hours:

```
b2b-fire.yml ──► portal ──► email ──► Outlook (lmbg.co.id)
                                        │ Power Automate: "Create an issue"
                                        ▼
                              GitHub issue "B2B|<brand>|…"
                                        │ b2b-collect.yml (08:05 / 09:05 WIB)
                                        ▼
                  download → gs://…/draft path → close issue
                                        │ publish_production.py
                                        ▼
            production: sales_sell out_minimarket/<brand>/{sell_out, daily_sell_out_qty, daily_sell_out_value}/
```

Issues work as a free queue: they cost no Actions minutes, so a day's
~120–250 emails become 2 short runs instead of one run per email. That's what
keeps this private repo inside GitHub Free's 2,000 minutes a month. The flows
are set up by hand, see **[docs/power-automate-flows.md](docs/power-automate-flows.md)**.

## Indomaret files are rolling windows

Checked against the DATE column (the first rows only show the last day):

- `DAILY_STORE_PERFORMANCE_<D>` (by store) is the **7 days** ending on D. Production
  keeps every day's window as-is, as it always has.
- `DAILY_SELLING_OUT_<D>` (by branch) is the **14 days** ending on D. Because
  `Minimarket_Sales.parquet` adds up every file, production holds each date
  once: a `FULL_MONTH` file per closed month, and one month-to-date file per open
  month, rebuilt daily with the newest numbers per date. When the `FULL_MONTH`
  arrives, the month-to-date file is moved to the draft's `superseded/`.

## Setup

1. **Secrets:** from this folder on the laptop, run

   ```powershell
   .\set_secrets.ps1 -GcpKeyPath "C:\path\to\service-account.json"
   ```

   It copies the eight portal logins from the laptop's existing `.env` files,
   plus the same service-account key your other `som-*` repos use as
   `GCP_SA_KEY`. No values are printed.
2. **Power Automate:** build the two flows in
   [docs/power-automate-flows.md](docs/power-automate-flows.md).
3. **Test:** Actions → pick a workflow → **Run workflow**.

## Designed around failures already seen

- **Every step fails loudly.** An upload with zero files fails, and so does a
  fire whose request never reached the portal. The last collect of the day
  fails if a brand sent no email at all. The earlier `b2b_*` repos printed
  `An error occurred`, exited 0 and showed "success" for weeks.
- **Linux runners only.** macOS minutes count 10× in private repos.
- **One Indomaret login per run.** Two logins inside one 30-second TOTP window
  can be refused as a reused code.
- **Safe Links decoded exactly once.** A second decode corrupts the signed
  URL's credential and breaks the download.
- **No live link is ever printed** in logs or issue comments.

## Not ported yet

- The **OneDrive → iMac copy** of Alfamart/Alfamidi sell out. The cloud can
  write to GCS, but writing into a OneDrive folder would need a Microsoft app
  registration that a non-admin can't create. (The laptop's summary and
  converter steps ARE ported, in `publish_production.py`.)

## Sources

`sources/` holds copies of the laptop's proven scripts, changed only where
the cloud needs it:

| File | Change |
|---|---|
| `indomaret_daily_reports.py` | `--out` folder; `--report 2,3` in one login |
| `alfamart_b2b_auto.py` | TOTP seed read from the environment first; SSO token no longer printed in full |
| `alfamidi_b2b_auto.py` | SSO token no longer printed in full |
| `alfamart_b2b_auto.py`, `alfamidi_b2b_auto.py` | `--confirm`: click each request twice and log the portal's 2nd reply |
| `minimarket_sell_out_converter.py` | none - copied verbatim; `build_parquets.py` calls its `process_files()` |
| everything else | unchanged |
