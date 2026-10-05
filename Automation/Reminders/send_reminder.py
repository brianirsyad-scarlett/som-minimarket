"""One-off email reminders, fired by a Windows scheduled task.

Usage: python send_reminder.py <reminder_name>
Credentials: ~/.b2b_email.env (B2B_GMAIL_USER / B2B_GMAIL_APP_PASSWORD), same as check_customer_master.py.
"""
import smtplib
import sys
from email.message import EmailMessage
from pathlib import Path

TO = ["brian.rinaldy@lmbg.co.id"]

REMINDERS = {
    "marketplace-redownload": (
        "Reminder: re-download TikTok and Shopee orders (Jan 2026 - today)",
        """Good morning,

Today: re-download the TikTok Shop and Shopee order exports, one file per month, Jan 2026 to today, then rebuild the parquets and upload to GCS.

Why (found in BigQuery on 27 Sep 2026):
- TikTok Mar-Jun 2026: ~1.25M orders still show "Shipped" because those months were exported right after month-end and never refreshed. Apr 2026 shows only ~10 bn completed sales instead of ~40-50 bn.
- Shopee Jun-Sep 2026 is incomplete: Aug 2026 has 88k lines vs 1.2M for TikTok (empty export parts).
- 17,736 Shopee "Selesai" orders have Total_Pembayaran = 0 in the export.

Checklist:
1. Each re-downloaded month REPLACES its old file (no side-by-side copies, or orders count twice).
2. Rebuild TikTok.parquet and Shopee_bq.parquet and upload to gs://bucket_som/sales_parquet/.
3. From now on, re-download the previous 2 months on every run.
4. After upload, ask Claude (Big Query Admin session) to rebuild staging_online_marketplace_tiktok_v2 / shopee_v2 / all_v2 and re-check Shipped vs Completed per month.
""",
    ),
}


def credentials():
    env = {}
    for line in (Path.home() / ".b2b_email.env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env["B2B_GMAIL_USER"], env["B2B_GMAIL_APP_PASSWORD"]


def main():
    subject, body = REMINDERS[sys.argv[1]]
    user, pw = credentials()
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, user, ", ".join(TO)
    msg.set_content(body)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=60) as s:
        s.login(user, pw)
        s.send_message(msg)
    print(f"sent '{subject}' to {', '.join(TO)}")


if __name__ == "__main__":
    main()
