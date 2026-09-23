# som-minimarket

Automated daily collection of sell-out and market-share data from three
Indonesian minimarket chains' supplier portals, on one Windows machine driven by
Task Scheduler.

| Code | Chain | Portal |
|---|---|---|
| **SAT** | Alfamart | B2B portal (login + TOTP 2FA) |
| **MIDI** | Alfamidi | B2B portal |
| **IDM** | Indomaret | Connect / Power BI embedded reports |

## Layout

The repo is rooted at the working folder so the scheduled tasks run these exact
files in place. Almost everything else in that folder is business data, which is
why `.gitignore` is an **allowlist** - read the note at its top before editing.

```
Automation/
  Alfamart-MarketShare/     SAT market share      (Playwright)
  Alfamidi-MarketShare/     MIDI market share     (Playwright)
  Indomaret-MarketShare/    IDM market share      (requests + Power BI export API)
  Indomaret-DailyReports/   IDM sell out / daily sell out / stock
  Alfamidi-PerfSales/, Mail-Attachments/, Mail-ReportLinks/, Sales-MonthlyReport/
  status.ps1                one view of every pipeline + data freshness
  preflight.ps1             "will the next fire actually run?"
  run_all_now.ps1           manual re-run  (-Only SAT,MIDI  -Wait  -Collect)
  fix_task_wake_settings.ps1
Data/Sent Email/            SAT + MIDI sell-out (B2B): fire requests, collect via IMAP
Data/Report/Sales/Minimarket/<chain>/<report>/[0-9]_*.py
                            per-report processing steps that live beside the data
```

## Daily schedule

| Time | Job |
|---|---|
| 00:00 / 00:15 / 00:35 / 01:00 | SAT, MIDI, IDM daily reports, IDM market share |
| 09:00 | B2B fire - request SAT + MIDI sell-out reports |
| 09:10 | B2B verify - re-fire inside the portal's 1-hour cooldown to prove the fire registered |
| 11:00, 12:00, 13:00 | B2B collect - download the emailed report links |

The B2B portals don't return files; they email a signed download link that lives
about 24 hours. Firing and collecting are therefore separate jobs.

## Setup

Each `Automation/*` pipeline: copy `.env.example` to `.env`, fill it in, then
`setup.ps1` and `register_schedule.ps1`.

The B2B pipeline reads `~/.b2b_email.env` (outside the repo):

```
B2B_GMAIL_USER=            # mailbox the portals send to
B2B_GMAIL_APP_PASSWORD=    # Gmail app password, for IMAP
ALFAMART_TOTP_SECRET=      # base32 seed for the Alfamart 2FA
ALFAMART_B2B_USER=
ALFAMART_B2B_PASS=
B2B_MIDI_USER=
B2B_MIDI_PASS=
```

Then `Data/Sent Email/register_b2b_schedule.ps1`.

## Things that have bitten this before

- **Tasks must use the Interactive principal.** S4U and SYSTEM tasks register
  fine and then silently never run on this machine.
- **Tasks need WakeToRun.** Without it a sleeping laptop skips the run and
  `NextRunTime` quietly rolls to tomorrow - no error anywhere.
- **"Collect ok" is not "data arrived".** Check `status.ps1`'s freshness section,
  which reads the age of the newest downloaded file, not the task's exit code.
- **The collector searches All Mail + Trash + Spam**, found by IMAP special-use
  flag. The mailbox is forwarded on by a rule that deletes the original, and the
  folder names are localised.

## Credentials

Never commit `.env`, `~/.b2b_email.env`, logs (they contain session tokens) or
`.har` captures (they contain cookies). The allowlist blocks all of them, and a
second blocklist at the bottom of `.gitignore` catches them even if the
allowlist is loosened.
