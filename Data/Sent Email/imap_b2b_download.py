"""
Shared IMAP downloader for the Alfamidi / Alfamart B2B "File Excel B2B" emails.

The vendor portal never attaches the file - it emails a signed GCS URL in the
body. Gmail's API-side plaintext/HTML extraction has a decoding bug that
mangles the signature query params, so this fetches the raw RFC822 message
over IMAP instead and lets Python's own `email` + `quopri` machinery decode
it correctly.

Reads credentials from C:\\Users\\<user>\\.b2b_email.env:
    B2B_GMAIL_USER=you@example.com
    B2B_GMAIL_APP_PASSWORD=<16-char app password>

--dest is the BRAND ROOT folder, not a specific report folder: by-branch files
are routed to <dest>\\Sell Out, by-store-by-category files to
<dest>\\Daily Sell Out, matching the pre-existing folder convention.

Usage:
    python imap_b2b_download.py --brand alfamidi --dest "D:\\...\\Minimarket\\Alfamidi" --since-days 1
    python imap_b2b_download.py --brand alfamart --dest "D:\\...\\Minimarket\\Alfamart" --since-days 1
"""

import argparse
import email
import email.header
import email.utils
import html
import imaplib
import os
import re
import sys
from datetime import date, timedelta
from pathlib import Path

import requests

ENV_PATH = Path.home() / ".b2b_email.env"

BRAND_MARKERS = {
    "alfamidi": "b2b_midi@smtp.sat.co.id",
    "alfamart": "b2b-np@smtp.sat.co.id",
}

BY_BRANCH_MARK = "detail_performance_by_branch_Selling_Out"
BY_STORE_MARK = "_All_Store"


def route_subfolder(filename):
    """
    Sell Out          -> by-branch daily raw files + the monthly summary
    Daily Sell Out     -> by-store-by-category daily detail files
    """
    if BY_BRANCH_MARK in filename:
        return "Sell Out"
    if BY_STORE_MARK in filename:
        return "Daily Sell Out"
    return None


def load_env():
    if not ENV_PATH.exists():
        raise SystemExit(f"Missing credentials file: {ENV_PATH}")
    env = {}
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
    user = env.get("B2B_GMAIL_USER")
    pw = env.get("B2B_GMAIL_APP_PASSWORD")
    if pw:
        pw = pw.replace(" ", "")  # Google displays it as 4 groups of 4
    if not user or not pw:
        raise SystemExit(f"{ENV_PATH} is missing B2B_GMAIL_USER / B2B_GMAIL_APP_PASSWORD")
    return user, pw


def imap_connect(user, app_password, folder="INBOX"):
    m = imaplib.IMAP4_SSL("imap.gmail.com")
    m.login(user, app_password)
    # The folder is a parameter because the mid-loop reconnect below has to
    # reopen the SAME folder - message ids are per-folder, so silently falling
    # back to INBOX would make every id in flight meaningless.
    typ, _ = m.select(quote_folder(folder))
    if typ != "OK":
        raise SystemExit(f"Cannot open mailbox folder {folder!r}")
    return m


def quote_folder(name):
    """IMAP mailbox names with spaces or brackets must be quoted."""
    return '"' + name.replace('"', '\\"') + '"'


def list_folders(m):
    typ, data = m.list()
    if typ != "OK":
        return []
    names = []
    for line in data:
        s = line.decode(errors="replace") if isinstance(line, bytes) else str(line)
        # (\HasNoChildren) "/" "INBOX"   ->  INBOX
        hit = re.search(r'"([^"]*)"\s*$', s.strip())
        names.append(hit.group(1) if hit else s.split()[-1].strip('"'))
    return names


def find_special(m, flag):
    r"""A folder by its IMAP special-use flag, e.g. \All, \Trash, \Junk.

    Found by flag rather than name because this mailbox is Indonesian - All Mail
    is "[Gmail]/Semua Email", Trash is "[Gmail]/Sampah" - so the English names
    would silently match nothing.
    """
    typ, data = m.list()
    if typ != "OK":
        return None
    for line in data:
        s = line.decode(errors="replace") if isinstance(line, bytes) else str(line)
        if flag in s:
            hit = re.search(r'"([^"]*)"\s*$', s.strip())
            if hit:
                return hit.group(1)
    return None


def find_all_mail(m):
    r"""Gmail's "All Mail" folder (\All).

    It holds every message regardless of which label or folder a rule filed it
    under - but it EXCLUDES Trash and Spam, which is why it alone was not
    enough. See the folder list built in main().
    """
    return find_special(m, r"\All")


def select_folder(m, folder):
    """Select a folder read-only. Returns True if it exists and opened."""
    typ, _ = m.select(quote_folder(folder), readonly=True)
    return typ == "OK"


def find_message_ids(m, marker, since_days):
    since = (date.today() - timedelta(days=since_days)).strftime("%d-%b-%Y")
    # Gmail IMAP extension search: subject + body text + date window.
    typ, data = m.search(None, 'SINCE', since, 'SUBJECT', '"File Excel B2B"', 'BODY', marker)
    if typ != "OK":
        raise SystemExit(f"IMAP search failed: {typ} {data}")
    return data[0].split()


def diagnose(m, marker, since_days):
    """Work out WHERE the emails are and WHICH search condition drops them.

    Two independent things can silently return "0 candidates":

      1. Wrong folder. The downloader only ever selected INBOX, so a mail rule
         filing the reports into an "Alfamart" / "Alfamidi" folder hides them
         completely - login fine, search fine, nothing found.
      2. Stale criteria. The search ANDs subject + body-marker + date, so the
         portal renaming its subject or changing its sending address looks
         exactly the same from here.

    So this scans every folder, and within the best folder widens the criteria
    one condition at a time.
    """
    since = (date.today() - timedelta(days=since_days)).strftime("%d-%b-%Y")
    folders = list_folders(m)

    print(f"\n--- Folder scan, SINCE {since} ---")
    print(f"  {'in window':>9} {'+subject':>9} {'+marker':>8} {'ALL 3':>6}   folder")
    best, best_n = None, -1
    for f in folders:
        if not select_folder(m, f):
            print(f"  {'(cannot open)':>34}   {f}")
            continue

        def n_of(*criteria):
            typ, data = m.search(None, *criteria)
            return len(data[0].split()) if typ == "OK" else -1

        n_win  = n_of('SINCE', since)
        n_subj = n_of('SINCE', since, 'SUBJECT', '"File Excel B2B"')
        n_mark = n_of('SINCE', since, 'BODY', marker)
        n_all  = n_of('SINCE', since, 'SUBJECT', '"File Excel B2B"', 'BODY', marker)
        flag = "  <-- has matches" if n_all > 0 else ("  <-- subject matches here" if n_subj > 0 else "")
        print(f"  {n_win:>9} {n_subj:>9} {n_mark:>8} {n_all:>6}   {f}{flag}")
        # Prefer a folder that matches all three, else one with any traffic.
        score = n_all * 1000 + n_subj
        if score > best_n:
            best, best_n = f, score

    # Show what is actually there, so a renamed subject or a new sender address
    # is visible rather than merely inferred.
    if best and select_folder(m, best):
        typ, data = m.search(None, 'SINCE', since)
        ids = data[0].split() if typ == "OK" else []
        print(f"\n--- Most recent in '{best}' (subject / from) ---")
        for mid in ids[-15:]:
            typ, d = m.fetch(mid, '(BODY.PEEK[HEADER.FIELDS (SUBJECT FROM DATE)])')
            if typ != "OK" or not d or not isinstance(d[0], tuple):
                continue
            hdr = email.message_from_bytes(d[0][1])
            subj = str(email.header.make_header(email.header.decode_header(hdr.get("Subject") or "")))
            frm = str(email.header.make_header(email.header.decode_header(hdr.get("From") or "")))
            print(f"  {hdr.get('Date','?')[:31]:<31} | {subj[:52]:<52} | {frm[:40]}")
    print()


def extract_url_and_filename(raw_bytes):
    msg = email.message_from_bytes(raw_bytes)
    plain_text = None
    html_text = None
    parts = msg.walk() if msg.is_multipart() else [msg]
    for part in parts:
        ct = part.get_content_type()
        if ct not in ("text/plain", "text/html"):
            continue
        charset = part.get_content_charset() or "iso-8859-1"
        payload = part.get_payload(decode=True)
        if payload is None:
            continue
        text = payload.decode(charset, errors="replace")
        if ct == "text/plain" and plain_text is None:
            plain_text = text
        elif ct == "text/html" and html_text is None:
            html_text = text

    # Quoted-printable + correct MIME decode already happened above (via
    # get_payload(decode=True)); text/plain has no entities, text/html does.
    for body_text, needs_unescape in ((plain_text, False), (html_text, True)):
        if not body_text:
            continue
        if needs_unescape:
            body_text = html.unescape(body_text)
        m = re.search(r"(https://[^\s<>\"]+?\.csv\?[^\s<>\"]+)", body_text)
        if m:
            url = m.group(1)
            filename = url.split("/")[-1].split("?")[0]
            return url, filename
    return None, None


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--brand", choices=["alfamidi", "alfamart"], required=True)
    ap.add_argument("--dest", required=True,
                    help="Brand root folder (e.g. .../Alfamidi). By-branch files are "
                         "routed to <dest>/Sell Out, by-store-by-category files to "
                         "<dest>/Daily Sell Out.")
    ap.add_argument("--since-days", type=int, default=1)
    ap.add_argument("--mark-read", action="store_true",
                    help="Mark matched emails as read after successful download")
    ap.add_argument("--diagnose", action="store_true",
                    help="Do not download anything. Scan every folder and widen the "
                         "search one condition at a time, to show WHY the normal search "
                         "returns 0 candidates.")
    ap.add_argument("--folder", default=None,
                    help="Mailbox folder to search. Default: Gmail's All Mail (found by "
                         "its \\All flag), which covers every label and folder, so a mail "
                         "rule filing reports into an 'Alfamart' folder cannot hide them. "
                         "Pass INBOX or a folder name to override.")
    args = ap.parse_args()

    user, app_password = load_env()
    marker = BRAND_MARKERS[args.brand]
    brand_root = Path(args.dest)

    print(f"--- IMAP login as {user} ---")
    m = imap_connect(user, app_password)

    if args.diagnose:
        diagnose(m, marker, args.since_days)
        m.logout()
        return

    # Folder history of this one bug:
    #   INBOX only          -> missed everything once a rule filed the mail
    #   + All Mail          -> fixed that, but All Mail EXCLUDES Trash and Spam
    #   + Trash and Spam    -> the IT-side rule that forwards this mailbox on to
    #                          lmbg.co.id deletes the original, so from
    #                          2026-09-21 the reports were sitting in Trash and
    #                          were invisible all over again.
    # Gmail is the ORIGINAL recipient here; lmbg.co.id is downstream of it.
    if args.folder:
        folders = [args.folder]
    else:
        folders = []
        for f in (find_all_mail(m), find_special(m, r"\Trash"), find_special(m, r"\Junk")):
            if f and f not in folders:
                folders.append(f)
        if not folders:
            folders = ["INBOX"]

    seen_filenames = set()
    downloaded = skipped = 0

    for folder in folders:
        typ, _ = m.select(quote_folder(folder))
        if typ != "OK":
            print(f"  (cannot open {folder!r}, skipping)")
            continue

        ids = find_message_ids(m, marker, args.since_days)
        print(f"{folder}: {len(ids)} candidate message(s) for {args.brand} "
              f"in the last {args.since_days} day(s).")

        for mid in ids:
            try:
                typ, data = m.fetch(mid, "(RFC822)")
            except imaplib.IMAP4.abort:
                # Large downloads between fetches can leave the IMAP connection
                # idle long enough for Gmail to drop it; reconnect once and retry
                # this message rather than losing the whole run. Reopen the SAME
                # folder - message ids are per-folder.
                print("  [IMAP connection dropped, reconnecting...]")
                try:
                    m.logout()
                except Exception:
                    pass
                m = imap_connect(user, app_password, folder)
                try:
                    typ, data = m.fetch(mid, "(RFC822)")
                except imaplib.IMAP4.abort as exc:
                    print(f"  [{mid.decode()}] fetch failed after reconnect: {exc}, skipping")
                    continue
            if typ != "OK" or not data or not data[0]:
                print(f"  [{mid.decode()}] fetch failed, skipping")
                continue
            raw = data[0][1]
            url, filename = extract_url_and_filename(raw)
            if not url:
                continue
            if filename in seen_filenames:
                continue  # duplicate email for the same report; first one wins
            seen_filenames.add(filename)

            subfolder = route_subfolder(filename)
            if subfolder is None:
                print(f"  [{filename}] doesn't match a known by-branch/by-store pattern, "
                      f"saving to brand root as a fallback")
                dest = brand_root
            else:
                dest = brand_root / subfolder
            dest.mkdir(parents=True, exist_ok=True)
            out_path = dest / filename
            if out_path.exists():
                skipped += 1
                print(f"  SKIP {filename}  (already downloaded)")
                if args.mark_read:
                    m.store(mid, "+FLAGS", "\\Seen")
                continue

            try:
                r = requests.get(url, timeout=300)
                r.raise_for_status()
                out_path.write_bytes(r.content)
                downloaded += 1
                print(f"  OK   {filename}  ({len(r.content):,} bytes)")
                if args.mark_read:
                    m.store(mid, "+FLAGS", "\\Seen")
            except requests.RequestException as exc:
                print(f"  FAIL {filename}: {exc}")

    try:
        m.close()
        m.logout()
    except Exception:
        pass
    print(f"\n--- Done: {downloaded} file(s) downloaded, {skipped} already present, "
          f"routed under {brand_root}\\Sell Out and {brand_root}\\Daily Sell Out ---")


if __name__ == "__main__":
    main()
