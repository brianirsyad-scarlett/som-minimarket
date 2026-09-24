"""Email a pipeline failure via Gmail SMTP.

Credentials come from the environment (GitHub secrets): SMTP_USER and
SMTP_APP_PASSWORD - a Gmail app password. Recipients: NOTIFY_TO, comma-separated.

    python notify.py failure "Shopee BQ pipeline failed" "<details / run URL>"
    python notify.py test
"""

from __future__ import annotations

import os
import smtplib
import sys
from email.message import EmailMessage

DEFAULT_TO = "brian.rinaldy@scarlett.co.id,brian.rinaldy@lmbg.co.id"


def send(subject: str, body: str) -> None:
    user, pw = os.environ.get("SMTP_USER"), os.environ.get("SMTP_APP_PASSWORD")
    if not (user and pw):
        print("SMTP_USER / SMTP_APP_PASSWORD not set - skipping failure email.")
        return
    to = [a.strip() for a in os.environ.get("NOTIFY_TO", DEFAULT_TO).split(",") if a.strip()]
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, user, ", ".join(to)
    msg.set_content(body)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=60) as s:
        s.login(user, pw)
        s.send_message(msg)
    print(f"sent '{subject}' to {', '.join(to)}")


def main(argv) -> int:
    run_url = os.environ.get("RUN_URL", "")
    if argv[0] == "failure":
        send(f"[SOM pipeline] {argv[1]}", f"{argv[2] if len(argv) > 2 else ''}\n\n{run_url}")
    elif argv[0] == "test":
        send("[SOM pipeline] Test - Shopee BQ pipeline alert",
             f"This is a test of the Shopee BQ parquet pipeline failure alert.\n\n{run_url}")
    else:
        raise SystemExit(f"unknown command {argv[0]!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
