# iMac scheduler for the SOM GitHub pipelines

GitHub's own cron starts these workflows hours late (Anchanto due 00:30 started
03:36 on 2026-09-24). The always-on iMac starts them on time instead; GitHub's
schedule stays as a backup, and a late scheduled run skips itself when today's
output already exists, so nothing is built twice.

| Pipeline | iMac starts it at (WIB) |
|---|---|
| Odoo | 00:05, 03:05, 06:05, 09:05, 12:05, 15:05, 18:05, 21:05 |
| Anchanto | 00:32 |
| Accurate | 00:47 |
| Sell In + PCC | 01:30, 02:30, 03:30, 05:00, 07:00 (the gate builds once, when all inputs are in) |

## Setup (once, on the iMac, in Terminal)

```
brew install gh
gh auth login
gh repo clone brianirsyad-scarlett/som-sell-in-report-automation ~/som-sell-in-report-automation
bash ~/som-sell-in-report-automation/mac_scheduler/install.sh
bash ~/som-scheduler/trigger.sh accurate      # test: starts an Accurate run now
tail ~/som-scheduler/logs/*.log
```

Uninstall: `bash ~/som-sell-in-report-automation/mac_scheduler/install.sh --uninstall`

The iMac's clock must be on Asia/Jakarta time (System Settings -> General -> Date & Time).
launchd runs a job missed while the Mac slept as soon as it wakes.
