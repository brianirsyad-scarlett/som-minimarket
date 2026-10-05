# Power Automate flows: report email → GitHub issue

The Alfamart and Alfamidi portals never return files. They email a signed
download link per report, to the scarlett.co.id mailbox, which IT forwards to
the lmbg.co.id Outlook mailbox, where a rule files them into
`Inbox\Alfamart` and `Inbox\Alfamidi`.

These two flows turn each of those emails into an issue in this repo. The
`b2b-collect` workflow then downloads the links.

**Why Power Automate:** the tenant blocks outside apps from reading Microsoft 365
mail, but Power Automate is Microsoft's own. The Outlook and GitHub connectors
are both **Standard**, so they're included in the M365 licence with no Premium
needed. "Create an issue" is a generally available action (not "Preview"). The
tenant has no Power Platform data policies (checked 2026-09-24), so nothing
blocks the GitHub connector.

---

## Before you start

1. Open **make.powerautomate.com**.
2. Top right, **Environment** must be the one marked **(default)**.
   Trial environments are deleted after 30 days, along with their flows.

## Flow 1: Alfamart

1. **+ Create → Automated cloud flow**
   - Name: `B2B Alfamart to GitHub`
   - Trigger: search **When a new email arrives (V3)** (Office 365 Outlook) → **Create**
2. In the trigger, open **Show all / Advanced parameters**:

   | Field | Value |
   |---|---|
   | Folder | folder icon → **Inbox → Alfamart** |
   | From | `b2b-np@smtp.sat.co.id` |
   | Include Attachments | No |
   | Only with Attachments | No |

3. **+ New step** → search **GitHub** → **Create an issue**.
   The first time, it asks you to sign in to GitHub and authorise
   *Microsoft Power Automate*. Allow it, **including private repositories**,
   because this repo is private.

   | Field | Value |
   |---|---|
   | Repository Owner | `brianirsyad-scarlett` |
   | Repository Name | `som-minimarket-automation` |
   | Title | type `B2B|alfamart|` → Dynamic content **Subject** → type `|` → Dynamic content **Received Time** |
   | Body | Dynamic content **Body** |

   The title must start with exactly `B2B|alfamart|`. That prefix is how the
   collector tells report issues apart from anything else in the repo.

4. **Save**.

## Flow 2: Alfamidi

Same as Flow 1, with:

| Field | Value |
|---|---|
| Name | `B2B Alfamidi to GitHub` |
| Folder | **Inbox → Alfamidi** |
| From | `b2b_midi@smtp.sat.co.id` |
| Title prefix | `B2B|alfamidi|` |

Don't match on plain `sat.co.id`: that's Sumber Alfaria Trijaya, the parent of
**both** chains.

---

## Checking it works

After the next fire (07:05 WIB, in GitHub), emails arrive about
10–65 minutes later. You should see:

1. **Power Automate → My flows → the flow → Run history**: one successful run
   per email.
2. **GitHub → som-minimarket-automation → Issues**: open issues titled
   `B2B|alfamart|File Excel B2B - …`.
3. After 08:05 WIB: those issues are **closed**, each with a comment
   `Saved to gs://bucket_som/sales_parquet/raw/minimarket/…`.

## Optional: backfill emails that are already in the folder

The flows only catch **new** mail. To push emails that are already sitting in
the folder, for example to rescue today's links before they expire (about
24 h after they were sent):

1. **+ Create → Instant cloud flow** → *Manually trigger a flow*.
2. **Get emails (V3)**: Folder `Inbox\Alfamart`, Top `150`, Fetch Only Unread
   `No`, Include Attachments `No`.
3. **Apply to each** (value from Get emails) → **Create an issue** with the
   same fields as Flow 1, using that item's **Subject**, **Received Time** and
   **Body**.
4. Run it once, then again with the folder set to `Inbox\Alfamidi` and the
   title prefix `B2B|alfamidi|`.

Duplicates are harmless: the collector skips anything already in the bucket.
Expired links are closed with a note and reported as problems, so a backfill
of old mail will show red once. That's expected.
