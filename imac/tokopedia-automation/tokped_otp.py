"""Fetch the Tokopedia/TikTok Shop email verification code via Gmail IMAP.

Requires ~/.tokopedia.env (see .tokopedia.env.template in this folder) with
TOKPED_GMAIL_ADDRESS / TOKPED_GMAIL_APP_PASSWORD exported, and that env file
sourced before the script runs (run_tokped_daily.sh does this).

NOTE: the sender/subject heuristics and code regex below were written without
a real sample of the verification email in hand (getting one requires the
Gmail app password, which only you can generate). If auto-fetch doesn't find
the code on the first live attempt, check ~/Library/Logs/tokped-daily.log and
tokped-automation/last_otp_debug.txt (dumped on a miss) and tighten
SENDER_HINTS/SUBJECT_HINTS/CODE_RE to match what actually arrived.
"""
import email
import imaplib
import os
import re
import time
from email.header import decode_header
from email.utils import parsedate_to_datetime

IMAP_HOST = "imap.gmail.com"
CODE_RE = re.compile(r"\b(\d{4,8})\b")

SENDER_HINTS = ("tiktok", "tokopedia")
SUBJECT_HINTS = ("verification", "verify", "code", "security")

AUTOMATION_DIR = os.path.dirname(os.path.abspath(__file__))


def _load_credentials():
    address = os.environ.get("TOKPED_GMAIL_ADDRESS")
    app_password = os.environ.get("TOKPED_GMAIL_APP_PASSWORD")
    if not address or not app_password:
        raise RuntimeError(
            "TOKPED_GMAIL_ADDRESS / TOKPED_GMAIL_APP_PASSWORD not set. Copy "
            ".tokopedia.env.template to ~/.tokopedia.env, fill in a Gmail App "
            "Password, `chmod 600` it, and make sure it's sourced before running."
        )
    return address, app_password


def _decode(value):
    if not value:
        return ""
    out = []
    for text, enc in decode_header(value):
        if isinstance(text, bytes):
            out.append(text.decode(enc or "utf-8", errors="ignore"))
        else:
            out.append(text)
    return "".join(out)


def _message_epoch(msg):
    try:
        return parsedate_to_datetime(msg.get("Date", "")).timestamp()
    except Exception:
        return 0


def _looks_like_otp_email(msg):
    sender = _decode(msg.get("From", "")).lower()
    subject = _decode(msg.get("Subject", "")).lower()
    return any(h in sender for h in SENDER_HINTS) or any(h in subject for h in SUBJECT_HINTS)


def _extract_code(msg):
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                try:
                    body += part.get_payload(decode=True).decode(
                        part.get_content_charset() or "utf-8", errors="ignore"
                    )
                except Exception:
                    continue
    else:
        try:
            body = msg.get_payload(decode=True).decode(
                msg.get_content_charset() or "utf-8", errors="ignore"
            )
        except Exception:
            body = str(msg.get_payload())

    for text in (_decode(msg.get("Subject", "")), body):
        match = CODE_RE.search(text)
        if match:
            return match.group(1)
    return None


def fetch_verification_code(since_ts=None, timeout=90, poll_interval=5):
    """Poll the inbox for a Tokopedia/TikTok verification email newer than since_ts.

    since_ts defaults to "now" (minus a small buffer for clock skew), so a
    stale code already sitting in the inbox is never mistakenly reused.
    """
    address, app_password = _load_credentials()
    since_ts = (since_ts if since_ts is not None else time.time()) - 30
    deadline = time.time() + timeout

    while time.time() < deadline:
        conn = imaplib.IMAP4_SSL(IMAP_HOST)
        try:
            conn.login(address, app_password)
            conn.select("INBOX")
            status, data = conn.search(
                None, "SINCE", time.strftime("%d-%b-%Y", time.gmtime(since_ts))
            )
            if status == "OK" and data and data[0]:
                for msg_id in data[0].split()[::-1]:  # newest first
                    status, msg_data = conn.fetch(msg_id, "(RFC822)")
                    if status != "OK" or not msg_data or not msg_data[0]:
                        continue
                    msg = email.message_from_bytes(msg_data[0][1])
                    if _message_epoch(msg) < since_ts:
                        continue
                    if not _looks_like_otp_email(msg):
                        continue
                    code = _extract_code(msg)
                    if code:
                        return code
        finally:
            try:
                conn.logout()
            except Exception:
                pass
        time.sleep(poll_interval)

    raise TimeoutError(
        f"No Tokopedia/TikTok verification email found within {timeout}s. "
        "Sender/subject heuristics in tokped_otp.py may need adjusting -- "
        "check the inbox manually for what actually arrived."
    )
