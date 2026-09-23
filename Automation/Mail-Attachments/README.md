# Mail Attachment Harvester

Saves attachments out of Outlook into `Data\Mail Attachments` (or wherever
`MAIL_DEST_ROOT` points), so incoming report files land on disk automatically.

## Read this first — which Outlook do you use?

**This script only works with classic Outlook (Office 16).** It talks MAPI/COM,
and **New Outlook (`Microsoft.OutlookForWindows`) has no COM object model at
all** — no `Outlook.Application`, no MAPI, no VBA. It is a web wrapper. There is
no way to automate it locally, and no switch that changes that.

This PC has both installed, and the day-to-day client is New Outlook. Classic
Outlook 2016 is present but its profile is empty.

So there are two routes, and this folder is the second one:

1. **Power Automate → OneDrive → `onedrive_ingest.ps1`** (preferred) — a
   first-party Microsoft flow drops attachments into OneDrive, which already
   syncs to `D:\OneDrive - PT. Opto Lumbung Sejahtera`. No COM, works with New
   Outlook, nothing to keep logged on.
2. **This script** — only if you set classic Outlook 2016 up as a second,
   automation-only client. It can coexist with New Outlook.

## Why this exists

The Gmail and Microsoft 365 connectors are blocked at the tenant level for this
account, so there is no cloud path to the mailbox. This script goes the other
way round: it attaches to the **classic Outlook desktop client signed in on this
PC** over MAPI/COM — the same mechanism the Sell In MT/GT refresh uses for
Excel.

Consequences of that design:

- No credentials anywhere. The script stores and transmits nothing. It reads the
  profile Windows already has open.
- Nothing leaves this machine.
- It only works while you are logged on, with Outlook set up. A scheduled task
  running in session 0 cannot see your mail profile.

## One-time setup

### 1. Add your account to *classic* Outlook

Classic Outlook 2016 is installed at
`C:\Program Files\Microsoft Office\root\Office16\OUTLOOK.EXE` but has **no
account configured** — it still opens on the first-run wizard. Launching it from
the Start menu may open New Outlook instead; run that path directly, and if it
offers to switch you to New Outlook, decline.

1. Run `OUTLOOK.EXE` from the path above.
2. Work through the setup wizard and add your `@lmbg.co.id` account.
3. Complete the MFA prompt.
4. Wait for the initial sync to finish before running anything here.

Classic and New Outlook coexist — you can keep using New Outlook day to day and
leave classic running minimised purely as the automation host.

If the wizard refuses the account, that is tenant policy blocking desktop
clients. Use route 1 (Power Automate) instead rather than working around it.

### 2. Install dependencies

```powershell
.\setup.ps1
```

Creates `.venv`, installs `pywin32` + `python-dotenv`, and copies `.env.example`
to `.env`.

### 3. Configure

Edit `.env` — folder to scan, lookback window, destination, allowed extensions.
Every setting is documented inline.

Edit `rules.json` to route attachments into subfolders by sender, subject, or
filename. It ships with three commented examples; first match wins, and anything
that matches nothing goes to the root. Delete the examples once you have your
own.

### 4. Dry run first

```powershell
.\run.ps1 -DryRun -Detailed
```

Writes nothing. Prints every attachment it would save and every one it skipped
with the reason. Tune `.env` and `rules.json` until the output looks right.

### 5. Schedule it

```powershell
.\register_schedule.ps1
```

Hourly from 07:00 for 12 hours, only while logged on. Adjust with `-At`,
`-RepeatMinutes`, `-ForHours`; remove with `-Remove`.

## Safety behaviour

This runs unattended against mail from outside the company, so it is
deliberately restrictive:

- **Executables are blocked in code and cannot be re-enabled from `.env`** —
  `.exe .dll .scr .msi .ps1 .vbs .js .lnk .hta .jar .iso` and friends. If
  `MAIL_ALLOWED_EXT` lists one, it is stripped with a warning.
- **Macro-enabled Office files** (`.xlsm .xlsb .docm`) are blocked unless you
  add them to `MAIL_ALLOWED_EXT` on purpose.
- Everything not on the allowlist is ignored.
- Filenames are sanitised — path separators, traversal, control characters and
  reserved device names cannot escape the destination folder.
- Existing files are never overwritten; collisions become `name (2).xlsx`.
- The mailbox is read-only unless `MAIL_MARK_READ=true`.

Blocked attachments are logged as warnings in `run.log`. Worth reading
occasionally — a blocked executable usually means someone is being phished.

> Related: `Data\Sent Email\D0@WN10AD_SE3TUP_(9595)_PASSW0RD_0PEN` is a pirated
> installer bundle with a deliberately scanner-evading name. It predates this
> script. Get it scanned.

## Idempotency

Saved attachments are recorded in `state.json` by SHA-256, so overlapping
lookback windows never produce duplicates — the same file re-sent under a
different name is also recognised. Delete `state.json` to force a full re-save.

## Troubleshooting

| Symptom | Cause |
|---|---|
| `Outlook has no mail account configured` | Step 1 not done. |
| `The RPC server is unavailable` | Outlook is stuck on a dialog, or the task ran while logged off. |
| Scheduled runs fail, manual runs work | Task is not set to Interactive logon — re-run `register_schedule.ps1`. |
| Nothing saved, everything "not allowlisted" | Extension missing from `MAIL_ALLOWED_EXT`; check with `-Detailed`. |
| Thousands of tiny images | `MAIL_SKIP_INLINE` got turned off. |
