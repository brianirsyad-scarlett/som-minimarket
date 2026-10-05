#!/usr/bin/env python3
"""
alfamart_notify.py -- send a run-status email so unattended failures surface.

Uses the same Gmail address + app password the download step uses, sending to
the user's own inbox. Called by run_alfamart_daily.sh at the end of every run.

A message is sent on success too, deliberately: if the Mac is asleep, launchd
never fires, or the job dies before reaching this point, no mail arrives at all
-- so the absence of a daily message is itself the alert.

Usage:
    alfamart_notify.py --status OK --body "..."
    alfamart_notify.py --status FAILED --body "..."
"""

import argparse
import datetime as dt
import os
import smtplib
import sys
from email.message import EmailMessage


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", required=True)
    ap.add_argument("--body", default="")
    args = ap.parse_args()

    addr = os.environ.get("ALFAMART_GMAIL_ADDRESS")
    app_pw = os.environ.get("ALFAMART_GMAIL_APP_PASSWORD")
    if not addr or not app_pw:
        print("notify: no Gmail credentials set, skipping email", file=sys.stderr)
        return

    stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    msg = EmailMessage()
    msg["Subject"] = f"[Alfamart automation] {args.status} - {stamp}"
    msg["From"] = addr
    msg["To"] = addr
    msg.set_content(
        f"Alfamart B2B daily pull finished with status: {args.status}\n"
        f"Host time: {stamp} WIB\n\n"
        f"{args.body}\n\n"
        f"Log: ~/Library/Logs/alfamart-daily.log\n"
    )

    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=60) as s:
            s.starttls()
            s.login(addr, app_pw)
            s.send_message(msg)
        print(f"notify: sent '{args.status}' email to {addr}")
    except Exception as e:
        # Never let a notification failure change the run's own exit status.
        print(f"notify: FAILED to send email: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
