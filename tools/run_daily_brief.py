"""
run_daily_brief.py
Main orchestrator for the daily Indian market brief pipeline.

Pipeline:
    1. fetch_news.py              → .tmp/news.json
    2. fetch_market_data.py       → .tmp/market_data.json
    3. fetch_ipo_data.py          → .tmp/ipo_data.json
    3b. fetch_earnings_calendar.py → .tmp/earnings_calendar.json
    4. generate_brief.py          → .tmp/email_content.html
    5. send_email.py              → Email delivered to inbox

This script is what the scheduler (Task Scheduler or GitHub Actions) runs daily.

Usage:
    python tools/run_daily_brief.py              # Full run + send email
    python tools/run_daily_brief.py --no-send    # Run pipeline but skip sending
    python tools/run_daily_brief.py --test       # Send with [TEST] prefix
"""

import os
import sys
import json
import smtplib
import argparse
import traceback
from datetime import datetime, timezone
from email.mime.text import MIMEText
from pathlib import Path

# Fix Windows terminal Unicode encoding
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

# Ensure tools/ is importable
sys.path.insert(0, str(Path(__file__).parent))

from fetch_news import fetch_all_news, save as save_news
from fetch_market_data import fetch_all_market_data, save as save_market
from fetch_ipo_data import fetch_all_ipo_data, save as save_ipo
from fetch_earnings_calendar import fetch_earnings_calendar, save as save_earnings
from generate_brief import generate_brief, save as save_html, update_sector_sentiment
from send_email import send_email, validate_config

TMP_DIR = Path(".tmp")
LOG_FILE = TMP_DIR / "run_log.json"


def log_run(status: str, steps: list[dict], error: str = None):
    """Append a run record to the log file for monitoring."""
    TMP_DIR.mkdir(exist_ok=True)

    existing = []
    if LOG_FILE.exists():
        try:
            existing = json.loads(LOG_FILE.read_text(encoding="utf-8"))
        except Exception:
            existing = []

    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "steps": steps,
    }
    if error:
        record["error"] = error

    existing.append(record)
    # Keep only last 30 runs
    LOG_FILE.write_text(json.dumps(existing[-30:], indent=2), encoding="utf-8")


def send_failure_alert(error: str, steps: list[dict]):
    """
    Send a plain-text alert email when the pipeline fails at a critical step.
    Uses the same Gmail credentials as the main brief.
    Silent if credentials aren't configured — never blocks the pipeline.
    """
    from_addr = os.getenv("EMAIL_FROM")
    to_addr = os.getenv("EMAIL_TO")
    password = os.getenv("EMAIL_PASSWORD")

    if not all([from_addr, to_addr, password]):
        print("  [warn] Failure alert skipped — email credentials not set")
        return

    today = datetime.now(timezone.utc).strftime("%a %d %b")
    subject = f"[PIPELINE FAILED] Market Brief — {today}"

    failed_steps = [s for s in steps if s.get("status") == "error"]
    step_lines = "\n".join(
        f"  [{s['status'].upper()}] {s['name']}"
        + (f": {s.get('error', '')}" if s.get("error") else "")
        for s in steps
    )

    body = (
        f"Your daily market brief pipeline FAILED on {today}.\n\n"
        f"Error: {error}\n\n"
        f"Pipeline steps:\n{step_lines}\n\n"
        f"Check .tmp/run_log.json for full details.\n"
        f"Fix and re-run: python tools/run_daily_brief.py"
    )

    msg = MIMEText(body, "plain")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_addr

    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(from_addr, password)
            server.sendmail(from_addr, to_addr, msg.as_string())
        print(f"  [ok] Failure alert sent to {to_addr}")
    except Exception as e:
        print(f"  [warn] Could not send failure alert: {e}")


def run_pipeline(send: bool = True, test_mode: bool = False) -> bool:
    """
    Execute the full pipeline. Returns True on success.
    """
    start_time = datetime.now(timezone.utc)
    print(f"\n{'='*60}")
    print(f"DAILY MARKET BRIEF PIPELINE")
    print(f"Started: {start_time.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*60}")

    steps = []

    # ── Step 1: Fetch News ──────────────────────────────────────
    step = {"name": "fetch_news", "status": "pending"}
    try:
        news_data = fetch_all_news(hours_back=24)
        save_news(news_data)
        step["status"] = "ok"
        step["articles"] = news_data.get("article_count", 0)
        print(f"[ok] Step 1 complete: {step['articles']} news articles fetched")
    except Exception as e:
        step["status"] = "error"
        step["error"] = str(e)
        print(f"[err] Step 1 failed: {e}")
        news_data = {"articles": [], "fetched_at": datetime.now(timezone.utc).isoformat()}
    steps.append(step)

    # ── Step 2: Fetch Market Data ───────────────────────────────
    step = {"name": "fetch_market_data", "status": "pending"}
    try:
        market_data = fetch_all_market_data()
        save_market(market_data)
        step["status"] = "ok"
        step["indices"] = list(market_data.get("indices", {}).keys())
        step["us_markets"] = list(market_data.get("us_markets", {}).keys())
        print(f"[ok] Step 2 complete: Market data fetched")
    except Exception as e:
        step["status"] = "error"
        step["error"] = str(e)
        print(f"[err] Step 2 failed: {e}")
        market_data = {"indices": {}, "us_markets": {}, "fetched_at": datetime.now(timezone.utc).isoformat()}
    steps.append(step)

    # ── Step 2b: Update Sector Sentiment Tracker ────────────────
    try:
        update_sector_sentiment(market_data)
    except Exception as e:
        print(f"  [warn] Sector sentiment tracker failed (non-fatal): {e}")

    # ── Step 3: Fetch IPO Data ──────────────────────────────────
    step = {"name": "fetch_ipo_data", "status": "pending"}
    try:
        ipo_data = fetch_all_ipo_data()
        save_ipo(ipo_data)
        step["status"] = "ok"
        step["ipos"] = ipo_data.get("ipo_count", 0)
        step["source"] = ipo_data.get("source", "")
        print(f"[ok] Step 3 complete: {step['ipos']} IPOs fetched (source: {step['source']})")
    except Exception as e:
        step["status"] = "error"
        step["error"] = str(e)
        print(f"[err] Step 3 failed (non-fatal): {e}")
        ipo_data = {"ipos": [], "fetched_at": datetime.now(timezone.utc).isoformat()}
    steps.append(step)

    # ── Step 3b: Fetch Earnings Calendar ────────────────────────
    step = {"name": "fetch_earnings", "status": "pending"}
    try:
        earnings_data = fetch_earnings_calendar()
        save_earnings(earnings_data)
        step["status"] = "ok"
        step["events"] = earnings_data.get("event_count", 0)
        print(f"[ok] Step 3b complete: {step['events']} earnings events fetched")
    except Exception as e:
        step["status"] = "error"
        step["error"] = str(e)
        print(f"[err] Step 3b failed (non-fatal): {e}")
        earnings_data = {"events": [], "event_count": 0, "fetched_at": datetime.now(timezone.utc).isoformat()}
    steps.append(step)

    # ── Step 4: Generate Brief (Claude) ─────────────────────────
    step = {"name": "generate_brief", "status": "pending"}
    try:
        html_content = generate_brief(news_data, market_data, ipo_data, earnings_data)
        save_html(html_content)
        step["status"] = "ok"
        step["html_bytes"] = len(html_content)
        print(f"[ok] Step 4 complete: HTML brief generated ({len(html_content):,} bytes)")
    except Exception as e:
        step["status"] = "error"
        step["error"] = str(e)
        print(f"[err] Step 4 failed: {e}")
        traceback.print_exc()
        steps.append(step)
        log_run("failed", steps, str(e))
        send_failure_alert(str(e), steps)
        return False
    steps.append(step)

    # ── Step 5: Send Email ──────────────────────────────────────
    if send:
        step = {"name": "send_email", "status": "pending"}
        try:
            validate_config()
            send_email(html_content, is_test=test_mode)
            step["status"] = "ok"
            print(f"[ok] Step 5 complete: Email sent")
        except Exception as e:
            step["status"] = "error"
            step["error"] = str(e)
            print(f"[err] Step 5 failed: {e}")
            steps.append(step)
            log_run("failed", steps, str(e))
            send_failure_alert(str(e), steps)
            return False
        steps.append(step)
    else:
        print(f"  Step 5 skipped: --no-send flag active")
        print(f"  Preview: open .tmp/email_content.html in your browser")

    # ── Done ────────────────────────────────────────────────────
    elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()
    print(f"\n{'='*60}")
    print(f"PIPELINE COMPLETE [ok]  ({elapsed:.1f}s)")
    print(f"{'='*60}\n")

    log_run("success", steps)
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run daily market brief pipeline")
    parser.add_argument("--no-send", action="store_true",
                        help="Run pipeline but skip sending the email")
    parser.add_argument("--test", action="store_true",
                        help="Send with [TEST] prefix in email subject")
    args = parser.parse_args()

    success = run_pipeline(
        send=not args.no_send,
        test_mode=args.test,
    )
    sys.exit(0 if success else 1)
