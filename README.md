# som-minimarket-automation (draft)

Cloud version of the minimarket sell-out and market-share collection for
**SAT** (Alfamart), **MIDI** (Alfamidi) and **IDM** (Indomaret), on GitHub Actions.

**Status.** For **Alfamart and Alfamidi sell out** the cloud is the only
requester, collector and **production publisher** since 2026-09-24: raw files
land in the draft `gs://bucket_som/sales_parquet/raw/minimarket/…`, and
`publish_production.py` writes the production `sales_sell out_minimarket/<brand>/…`
files the laptop used to. The laptop's B2B tasks are disabled.
**Market share and Indomaret are still drafts**: cloud output goes only to the
draft path, and the laptop remains their production source.

## What it collects

| | Market Share | Sell Out by Branch | Sell Out by Store |
|---|---|---|---|
| **Alfamart** | `market-share.yml` | `b2b-fire.yml` → email → `b2b-collect.yml` | same |
| **Alfamidi** | `market-share.yml` | `b2b-fire.yml` → email → `b2b-collect.yml` | same |
| **Indomaret** | `market-share.yml` | `indomaret-daily.yml` (report 2) | `indomaret-daily.yml` (report 3) |

Draft layout: `sales_parquet/raw/minimarket/<chain>/<market_share|sell_out_branch|sell_out_store>/`

## Schedule (WIB)

| Time | Workflow |
|---|---|
| 02:00 | Market share, all three chains |
| 02:30 | Indomaret sell out by branch + by store |
| 07:05 | Alfamart + Alfamidi: request sell-out reports - every request clicked twice; the 2nd reply proves the 1st registered |
| 08:05, 09:05 | Alfamart + Alfamidi: collect the emailed links, then **publish production** |

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
- Indomaret **Stock** (report 10). It's one argument away (`--report 2,3,10`)
  if wanted.

## Sources

`sources/` holds copies of the laptop's proven scripts, changed only where
the cloud needs it:

| File | Change |
|---|---|
| `indomaret_daily_reports.py` | `--out` folder; `--report 2,3` in one login |
| `alfamart_b2b_auto.py` | TOTP seed read from the environment first; SSO token no longer printed in full |
| `alfamidi_b2b_auto.py` | SSO token no longer printed in full |
| `alfamart_b2b_auto.py`, `alfamidi_b2b_auto.py` | `--confirm`: click each request twice and log the portal's 2nd reply |
| everything else | unchanged |
