#!/usr/bin/env python3
"""
alfamart_fetch_downloads.py -- poll Gmail (IMAP) for the "File Excel B2B - ..."
forwarded emails and download each presigned GCS link before it expires
(~400 seconds from generation, see README.md).

Meant to be run repeatedly (every ~60-90s) for a few minutes after
alfamart_trigger.py fires the export requests -- see run_alfamart_daily.sh.

Tracks already-downloaded message IDs in a local state file so re-running is
safe (won't re-download or double-count).

Usage:
    source ~/.alfamart.env && python3 alfamart_fetch_downloads.py --out-dir "/path/to/Alfamart Export"
"""

import argparse
import email
import email.policy
import imaplib
import json
import os
import re
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_PATH = os.path.join(HERE, ".alfamart_downloaded.json")
LINK_RE = re.compile(r"https://b2bsat-bucket\.storage\.googleapis\.com/excel/report/[^\s<>\"]+")


def load_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH) as f:
            return set(json.load(f))
    return set()


def save_state(seen):
    with open(STATE_PATH, "w") as f:
        json.dump(sorted(seen), f)


def imap_connect():
    addr = os.environ.get("ALFAMART_GMAIL_ADDRESS")
    app_pw = os.environ.get("ALFAMART_GMAIL_APP_PASSWORD")
    if not addr or not app_pw:
        sys.exit("ERROR: set ALFAMART_GMAIL_ADDRESS and ALFAMART_GMAIL_APP_PASSWORD in the environment.")
    m = imaplib.IMAP4_SSL("imap.gmail.com")
    m.login(addr, app_pw)
    m.select("INBOX")
    return m


def fetch_candidates(imap, since_gmail_date):
    # Gmail X-GM-RAW search: exact subject phrase, restricted to recent messages.
    query = f'(X-GM-RAW "subject:\\"File Excel B2B\\" after:{since_gmail_date}")'
    status, data = imap.search(None, query)
    if status != "OK":
        raise RuntimeError(f"IMAP search failed: {status} {data}")
    return data[0].split()


def download_link(url, out_dir):
    fname = urllib.request.unquote(url.split("/report/")[1].split("?")[0])
    out_path = os.path.join(out_dir, fname)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        content = resp.read()
    os.makedirs(out_dir, exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(content)
    return out_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--since", default=None,
                     help="Gmail date filter, e.g. 2026/09/11 (default: today)")
    args = ap.parse_args()

    import datetime as dt
    since = args.since or dt.date.today().strftime("%Y/%m/%d")

    seen = load_state()
    imap = imap_connect()
    try:
        ids = fetch_candidates(imap, since)
        print(f"{len(ids)} candidate message(s) since {since}")
        downloaded = 0
        for msg_id in ids:
            status, msg_data = imap.fetch(msg_id, "(RFC822)")
            if status != "OK":
                continue
            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw, policy=email.policy.default)
            gmail_msgid = msg.get("Message-ID", msg_id.decode())
            if gmail_msgid in seen:
                continue

            body = msg.get_body(preferencelist=("plain", "html"))
            text = body.get_content() if body else ""
            m = LINK_RE.search(text)
            if not m:
                continue
            url = m.group(0)
            try:
                path = download_link(url, args.out_dir)
                print(f"  downloaded: {os.path.basename(path)}")
                downloaded += 1
            except Exception as e:
                print(f"  FAILED to download (subject={msg.get('Subject')!r}): {e}")
                continue  # link may have expired; don't mark as seen, retry next poll

            seen.add(gmail_msgid)

        save_state(seen)
        print(f"Downloaded {downloaded} new file(s) this run. Total tracked: {len(seen)}")
    finally:
        imap.logout()


if __name__ == "__main__":
    main()
