"""
send_email.py
Sends the daily market brief HTML email via Gmail SMTP.

Reads:
    .tmp/email_content.html

Requires in .env:
    EMAIL_FROM      — your Gmail address (e.g., you@gmail.com)
    EMAIL_TO        — recipient address (can be same as FROM)
    EMAIL_PASSWORD  — Gmail App Password (16-char, NOT your login password)
                      Setup: Google Account → Security → 2-Step Verification → App Passwords

Usage:
    python tools/send_email.py
    python tools/send_email.py --file .tmp/email_content.html
    python tools/send_email.py --test  # sends with [TEST] prefix in subject
"""

import os
import argparse
import smtplib
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

EMAIL_FROM = os.getenv("EMAIL_FROM")
EMAIL_TO = os.getenv("EMAIL_TO")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
TMP_DIR = Path(".tmp")

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587


def validate_config():
    """Check that all required env vars are set."""
    missing = []
    if not EMAIL_FROM:
        missing.append("EMAIL_FROM")
    if not EMAIL_TO:
        missing.append("EMAIL_TO")
    if not EMAIL_PASSWORD:
        missing.append("EMAIL_PASSWORD")
    if missing:
        raise ValueError(
            f"Missing required environment variables: {', '.join(missing)}\n"
            "Add them to your .env file.\n"
            "EMAIL_PASSWORD should be a Gmail App Password, not your login password.\n"
            "Setup: Google Account → Security → 2-Step Verification → App Passwords"
        )


def build_subject(is_test: bool = False) -> str:
    """Build email subject line with today's date."""
    today = datetime.now(timezone.utc).strftime("%a %d %b")
    subject = f"📈 Your Market Brief — {today}"
    if is_test:
        subject = f"[TEST] {subject}"
    return subject


def send_email(html_content: str, is_test: bool = False) -> bool:
    """
    Send the HTML email via Gmail SMTP.
    Returns True on success, raises on failure.
    """
    validate_config()

    subject = build_subject(is_test)

    # Build MIME message
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO

    # Plain text fallback (minimal — email clients that can't render HTML)
    plain_text = (
        "Your daily Indian market brief is ready.\n"
        "Please view this email in an HTML-capable email client.\n"
        f"\nGenerated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
    )
    msg.attach(MIMEText(plain_text, "plain"))
    msg.attach(MIMEText(html_content, "html"))

    print(f"\nSending email...")
    print(f"  From: {EMAIL_FROM}")
    print(f"  To:   {EMAIL_TO}")
    print(f"  Subject: {subject}")

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.set_debuglevel(0)
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login(EMAIL_FROM, EMAIL_PASSWORD)
        server.sendmail(EMAIL_FROM, EMAIL_TO, msg.as_string())

    print(f"  [ok] Email sent successfully!")
    return True


def send_from_file(html_path: Path, is_test: bool = False) -> bool:
    """Read HTML from file and send."""
    if not html_path.exists():
        raise FileNotFoundError(
            f"HTML file not found: {html_path}\n"
            "Run tools/generate_brief.py first."
        )
    html_content = html_path.read_text(encoding="utf-8")
    return send_email(html_content, is_test=is_test)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Send market brief email")
    parser.add_argument("--file", type=Path, default=TMP_DIR / "email_content.html",
                        help="Path to HTML email file (default: .tmp/email_content.html)")
    parser.add_argument("--test", action="store_true",
                        help="Send with [TEST] prefix in subject line")
    args = parser.parse_args()

    try:
        send_from_file(args.file, is_test=args.test)
    except ValueError as e:
        print(f"\n[config error] {e}")
        exit(1)
    except FileNotFoundError as e:
        print(f"\n[file error] {e}")
        exit(1)
    except smtplib.SMTPAuthenticationError:
        print("\n[auth error] Gmail authentication failed.")
        print("Make sure EMAIL_PASSWORD is a Gmail App Password (not your login password).")
        print("Setup: Google Account → Security → 2-Step Verification → App Passwords")
        exit(1)
    except Exception as e:
        print(f"\n[error] Failed to send email: {e}")
        exit(1)

    print("\nDone.")
