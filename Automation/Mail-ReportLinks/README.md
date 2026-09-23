# Emailed Report Links

For portal reports that aren't downloaded in-session: you ask the portal to
generate one, and some minutes later it emails a **download link** that expires
(observed: ~24 hours). This picks the link out of the email and fetches the file.

Companion to `Alfamart-MarketShare` / `Alfamidi-MarketShare`, which still pull
the Market Share modulars directly from the web. This handles only the
generate-then-email reports.

## The chain

```
New Outlook  →  Power Automate flow  →  OneDrive folder  →  OneDrive sync  →  this script
  (mailbox)      (standard connectors)   (Apps/ReportLinks)   (to D:\OneDrive…)   (downloads the file)
```

### Why it goes the long way round

- **New Outlook has no COM/MAPI object model.** `Microsoft.OutlookForWindows` is
  a web wrapper — there is no local automation surface at all, unlike classic
  Outlook. Nothing on this PC can read that mailbox directly.
- **The Gmail / M365 cloud connectors are blocked at tenant level**, so there is
  no cloud path either.
- Power Automate is Microsoft's own tool running inside your tenant, which is
  why it's generally available where third-party OAuth apps are not.

## Surviving the 90-day trial

This design is built to keep running once the Power Automate Premium trial
lapses. Verified against Microsoft Learn on 2026-09-18:

**What the M365 seeded licence gives you permanently** (*Deep dive on specific
licenses* → *Compare seeded licenses*):

| Entitlement | Microsoft 365 seeded |
|---|---|
| Standard connectors | **Included** |
| Premium connectors | **Not included** |
| Custom connectors | **Not included** |
| Daily action limit | **6,000 per user** |

`Office 365 Outlook` and `OneDrive for Business` are both **standard**. So the
two-action flow below keeps working indefinitely on your normal M365 licence —
no trial, no purchase. Even the *Power Automate Free* licence permits
standard-connector cloud flows.

At two actions per report email, 6,000/day is enormous headroom — you would need
~3,000 report emails in a day to approach it.

**The action to avoid is `HTTP`**, which is premium. That is the whole reason
this flow only *saves the email body* and lets the local script do the
downloading. A flow that followed the link itself would die on day 91.

### The real trap: which environment the flow lives in

Licensing is not the biggest risk — **environment lifetime is**.

> A trial (standard) environment: *"After 30 days, the environment is disabled
> and deleted."*

That is **30 days, not 90**, and it takes your flows with it. If Power Automate
ever prompted you to "start a trial" while creating something, you may have been
put in a trial environment.

**Build the flow in the `Default` environment.** Check the environment picker in
the top-right of `make.powerautomate.com` before you create anything — it must
say *Default*, not something ending in *(trial)*.

### Verify your own entitlement

Don't take the table above on faith — tenants differ and admins tighten things:

1. `make.powerautomate.com` → **Settings (gear)** → **View my licenses**.
2. Confirm the environment picker says **Default**.
3. When building the flow, watch for the **Premium** badge on any action. If you
   see one, that action will stop working — the design here needs none.

### Other ways it can stop

Licensing is not the only failure mode, and these are quieter:

- **The connection expires.** Password or MFA policy changes invalidate the
  Office 365 Outlook connection. The flow then fails on every run.
- **Microsoft disables the flow** after prolonged repeated failures.
- **An admin tightens policy** on the connector.

None of these announce themselves on this PC — an empty drop folder looks
exactly like "no reports were due". That is what the watchdog below is for.

### The watchdog

If no report email has arrived for `WATCHDOG_DAYS` (default 3), the run logs a
loud `WATCHDOG` error naming the things to check, and **exits 3** so Task
Scheduler's *Last Run Result* turns red instead of reporting a reassuring `0x0`.

Set `WATCHDOG_DAYS` a little longer than your longest normal gap between
reports. Too short and you learn to ignore it; too long and a dead flow goes
unnoticed for weeks.

It stays silent until the first email has ever arrived, so a fresh install
doesn't cry wolf before you've finished setting it up.

### If Power Automate ever becomes unavailable

The zero-licensing fallback is `..\Mail-Attachments\`, which drives **classic
Outlook 2016** over COM. Office 16 is already installed and licensed here, so it
has no expiry at all. It needs classic Outlook configured with your account and
only runs while you are logged on — but it depends on no cloud service.

## 1. Create the Power Automate flow (once)

1. Go to `make.powerautomate.com` → **Create** → **Automated cloud flow**.
2. Trigger: **When a new email arrives (V3)** (Office 365 Outlook).
   - *Folder*: `Inbox`
   - *From*: the Alfamart / Alfamidi sender address
   - *Include Attachments*: `No`
   - *Only with Attachments*: `No`
3. Action: **Create file** (OneDrive for Business).
   - *Folder Path*: `/Apps/ReportLinks`
   - *File Name*:
     `@{formatDateTime(utcNow(),'yyyyMMdd-HHmmss')}-@{triggerOutputs()?['body/from']}.html`
   - *File Content*: the trigger's **Body** field
4. Save, then send yourself a test email from that address to confirm a file
   lands in OneDrive.

Two actions, no premium connectors. Make one flow per sender, or use a single
flow with both addresses in *From* (comma-separated) — either is fine, and
neither gets near any flow count limit on a seeded M365 licence.

## Dropping an email in by hand

You don't need the flow to test, or to rescue links before it exists. Anything
you put in `DROP_DIR` gets picked up:

| Format | Where it comes from |
|---|---|
| `.html` | What Power Automate writes (the decoded body) |
| `.eml` | What Outlook's **Save as** produces |
| `.txt` | A body pasted into a text file |

`.eml` is parsed as real MIME, not read as text. That matters: Outlook saves
messages quoted-printable, so a long signed URL is chopped across lines with
`=` soft breaks and every `=` in the query string becomes `=3D`. Read naively,
the link comes back truncated — plausible in the log, 404 on fetch. The sender
is also taken from the `From:` header, which makes source matching more reliable
than guessing from the filename.

To save one from New Outlook: open the message → **⋯** (more actions) →
**Save as** → move the `.eml` into
`D:\OneDrive - PT. Opto Lumbung Sejahtera\Apps\ReportLinks`, then run
`.\run.ps1 -DryRun -Detailed`.

## 2. Set up this script

```powershell
cd "D:\SCARLETT_512\SCARLETT-329\SOM\Automation\Mail-ReportLinks"
.\setup.ps1
```

Then review `.env` — mainly `DROP_DIR` (must match the OneDrive folder above,
as it appears on this PC) and `DEST_ROOT`.

## 3. Tune the link matching

`sources.json` decides which email goes where and which URLs count:

| Field | Meaning |
|---|---|
| `match_contains` | Any of these in the filename or body identifies the source |
| `link_regex` | Only URLs matching this are downloaded — keeps footer/banner links out |
| `dest` | Subfolder under `DEST_ROOT` |

The shipped `link_regex` values are a first guess covering the portal domains
plus the usual signed-URL hosts (GCS, S3). **They will almost certainly need
adjusting once we see a real email** — the Indomaret automation, for instance,
hands out `storage.googleapis.com` pre-signed URLs.

## 4. Dry run

```powershell
.\run.ps1 -DryRun -Detailed
```

Downloads nothing. Prints every email body found, which source matched, and
every candidate link. Send yourself one real report email first, then run this
and check the right link is being picked up.

## 5. Schedule

```powershell
.\register_schedule.ps1
```

Hourly from 06:30 for 18 hours, only while logged on (OneDrive sync only runs in
your session). ~24 attempts inside a 24-hour link lifetime.

## What the Alfamidi emails actually look like

Confirmed by downloading a real report, 2026-09-18.

| | |
|---|---|
| Sender | `b2b_midi@smtp.sat.co.id` |
| Subject | `File Excel B2B - <Category>` |
| Link host | the vendor's Google Cloud Storage bucket (`<bucket>.storage.googleapis.com`) |
| Link type | **GCS v4 signed URL — self-contained, no login needed** |
| Expiry | `X-Goog-Expires=86400` — exactly 24h |
| File | **`.csv`**, not `.xlsx` (~250 KB, ~3,800 rows) |

> Don't match sources on `sat.co.id`. That's Sumber Alfaria Trijaya, parent of
> **both** Alfamart and Alfamidi — it would swallow both. Key on `b2b_midi`.

### Safe Links

Defender rewrites every inbound link, so the `href` is **not** the download:

```
https://idn01.safelinks.protection.outlook.com/?url=<double-encoded real URL>&data=…
```

`unwrap_safelinks()` recovers the target. Two details matter:

- Outlook also emits an `originalsrc` attribute with the untouched URL, which is
  preferred when present. Power Automate bodies may not have it, hence the
  unwrapper.
- Exactly **one** decode is correct. The inner URL is double-encoded, so a
  second decode turns the `%2F` and `%40` inside the GCS credential into
  literals and invalidates the signature.

Both are pinned in `test_links.py` against the real captured wrapper.

## Behaviour worth knowing

- **Idempotent.** Fetched URLs are recorded in `state.json` by hash, so re-runs
  never re-download. Delete it to force a refetch.
- **Bodies are archived, not deleted** — they move to `_processed/` in the drop
  folder, and only once something was actually downloaded. A network blip leaves
  the body in place for the next run.
- **Entity unescaping.** Power Automate writes the HTML body verbatim, so signed
  URLs arrive with `&amp;` in the query string. Fetching that literally returns a
  signature error. The script unescapes before requesting — worth knowing if you
  ever hand-test a link copied from the raw body.
- **Executables are blocked**, same hard list as the attachment harvester. An
  emailed download link is exactly as untrusted as an attachment.
- Existing files are never overwritten; collisions become `name (2).xlsx`.

## Links behind a login

If `run.log` says *"got a login page, not a file"*, that link needs an
authenticated portal session rather than being a self-contained signed URL.

That case is **not wired up yet** — deliberately, because the fix depends on
which portal it is. The existing `alfamart_market_share.py` already performs a
full Playwright login with TOTP, so the fix is to reuse that session's cookies
here rather than reinvent it. Send me a sample and I'll wire it.

## Troubleshooting

| Message | Cause |
|---|---|
| `WATCHDOG: no report email has arrived…` (exit 3) | Flow stopped — see *Other ways it can stop* above |
| `Drop folder does not exist` | `DROP_DIR` wrong, or OneDrive hasn't synced the folder yet |
| `Nothing waiting in …` | Flow didn't fire, or OneDrive is paused — check the flow's run history |
| `Could not read sources.json` | Invalid JSON. A BOM is tolerated; a stray comma is not |
| `no candidate download links found` | `link_regex` too narrow; re-run with `-Detailed` |
| `link dead or expired (HTTP 403/410)` | Older than its TTL — regenerate the report |
| `got a login page, not a file` | See *Links behind a login* above |
| Downloads an unrelated banner image | `link_regex` too broad |
