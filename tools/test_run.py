"""
test_run.py
End-to-end test harness for the daily market brief pipeline.

Runs the full pipeline without sending an email. Opens the HTML preview
in your default browser so you can inspect the output before going live.

Usage:
    python tools/test_run.py              # Full pipeline, open browser preview
    python tools/test_run.py --no-browser # Skip opening browser
    python tools/test_run.py --send-test  # Actually send test email after preview
    python tools/test_run.py --step news  # Run only one step
"""

import sys
import json
import argparse
import webbrowser
from pathlib import Path
from datetime import datetime, timezone

# Fix Windows terminal Unicode encoding
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

sys.path.insert(0, str(Path(__file__).parent))


def check_env():
    """Verify all required env vars are set before running."""
    from dotenv import load_dotenv
    import os
    load_dotenv()

    issues = []
    required = {
        "ANTHROPIC_API_KEY": "Required for Claude AI analysis",
        "EMAIL_FROM": "Required for sending email",
        "EMAIL_TO": "Required for sending email",
        "EMAIL_PASSWORD": "Required for Gmail SMTP auth",
    }
    optional = {
        "NEWSAPI_KEY": "Recommended for news fetching",
        "FINNHUB_API_KEY": "Recommended for economic calendar",
        "POLYGON_API_KEY": "Optional for US market data",
    }

    print("\n-- Environment Check ------------------------------------------")
    for key, desc in required.items():
        val = os.getenv(key, "")
        status = "[OK]  " if val else "[MISS]"
        print(f"  {status} {key:25s} {'' if val else f'← {desc}'}")
        if not val:
            issues.append(key)

    for key, desc in optional.items():
        val = os.getenv(key, "")
        status = "[OK]  " if val else "[WARN]"
        print(f"  {status} {key:25s} {'' if val else f'← {desc}'}")

    if issues:
        print(f"\n  Missing required variables: {', '.join(issues)}")
        print("  Add them to your .env file and re-run.\n")
        return False
    print()
    return True


def run_step_news(hours: int = 24) -> dict:
    from fetch_news import fetch_all_news, save as save_news
    print("\n[Step 1] Fetching news...")
    data = fetch_all_news(hours_back=hours)
    save_news(data)
    return data


def run_step_market() -> dict:
    from fetch_market_data import fetch_all_market_data, save as save_market
    print("\n[Step 2] Fetching market data...")
    data = fetch_all_market_data()
    save_market(data)
    return data


def run_step_ipo() -> dict:
    from fetch_ipo_data import fetch_all_ipo_data, save as save_ipo
    print("\n[Step 3] Fetching IPO data...")
    data = fetch_all_ipo_data()
    save_ipo(data)
    return data


def run_step_generate(news_data: dict, market_data: dict, ipo_data: dict) -> str:
    from generate_brief import generate_brief, save as save_html
    print("\n[Step 4] Generating brief with Claude...")
    html = generate_brief(news_data, market_data, ipo_data)
    save_html(html)
    return html


def print_summary(news_data: dict, market_data: dict, ipo_data: dict):
    """Print a quick data summary after fetching."""
    print("\n-- Data Summary -----------------------------------------------")
    print(f"  News articles: {news_data.get('article_count', 0)}")

    indices = market_data.get("indices", {})
    for ticker, d in indices.items():
        chg = d.get("change_pct", 0)
        sign = "+" if chg > 0 else ""
        print(f"  {d.get('name', ticker)}: {d.get('close', 'N/A')} ({sign}{chg:.2f}%)")

    ipos = ipo_data.get("ipos", [])
    print(f"  IPOs found: {len(ipos)}")
    for ipo in ipos[:3]:
        name = ipo.get("name", "Unknown")
        gmp = ipo.get("gmp", "N/A")
        print(f"    → {name} | GMP: {gmp}")
    print()


def main():
    parser = argparse.ArgumentParser(description="Test the market brief pipeline")
    parser.add_argument("--no-browser", action="store_true",
                        help="Don't open browser preview")
    parser.add_argument("--send-test", action="store_true",
                        help="Send a test email after generating preview")
    parser.add_argument("--step", choices=["news", "market", "ipo", "generate", "all"],
                        default="all", help="Run only a specific step")
    parser.add_argument("--hours", type=int, default=24,
                        help="Hours back for news (default: 24)")
    args = parser.parse_args()

    print(f"\n{'='*60}")
    print(f"MARKET BRIEF — TEST RUN")
    print(f"{'='*60}")

    # Check environment
    if not check_env():
        sys.exit(1)

    tmp = Path(".tmp")
    tmp.mkdir(exist_ok=True)

    # Load cached data if running a single step
    def load_cached(filename: str, step_name: str) -> dict | None:
        path = tmp / filename
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            print(f"  [cached] Using existing {path} — run --step all to refresh")
            return data
        print(f"  [miss] {path} not found — run --step {step_name} first")
        return None

    if args.step == "news":
        run_step_news(args.hours)
        return

    if args.step == "market":
        run_step_market()
        return

    if args.step == "ipo":
        run_step_ipo()
        return

    # Full pipeline or generate step
    if args.step in ("all", "generate"):
        if args.step == "all":
            news_data = run_step_news(args.hours)
            market_data = run_step_market()
            ipo_data = run_step_ipo()
        else:
            news_data = load_cached("news.json", "news") or {"articles": []}
            market_data = load_cached("market_data.json", "market") or {"indices": {}}
            ipo_data = load_cached("ipo_data.json", "ipo") or {"ipos": []}

        print_summary(news_data, market_data, ipo_data)
        html = run_step_generate(news_data, market_data, ipo_data)

        preview_path = tmp / "email_content.html"
        abs_path = preview_path.resolve()

        print(f"\n-- Preview ----------------------------------------------------")
        print(f"  HTML saved: {preview_path}")
        print(f"  Size: {len(html):,} bytes")

        if not args.no_browser:
            url = abs_path.as_uri()
            print(f"  Opening in browser: {url}")
            webbrowser.open(url)

        if args.send_test:
            print("\n-- Sending test email -----------------------------------------")
            from send_email import send_email
            try:
                send_email(html, is_test=True)
                print("  Test email sent! Check your inbox.")
            except Exception as e:
                print(f"  [error] {e}")

    print(f"\n{'='*60}")
    print(f"TEST COMPLETE")
    print(f"{'='*60}\n")
    print("Next steps:")
    print("  1. Review the email preview in your browser")
    print("  2. Run with --send-test to send to your inbox")
    print("  3. Set up scheduling (see SCHEDULING_SETUP.md)")


if __name__ == "__main__":
    main()
