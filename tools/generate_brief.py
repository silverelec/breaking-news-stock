"""
generate_brief.py
Uses Claude API to analyze market data, news, and IPO data, then generates
a beautifully formatted HTML email newsletter for an amateur Indian investor.

Reads:
    .tmp/news.json
    .tmp/market_data.json
    .tmp/ipo_data.json

Writes:
    .tmp/email_content.html

Usage:
    python tools/generate_brief.py
"""

import os
import sys
import json
import calendar as cal_module
from datetime import datetime, timezone, timedelta
from pathlib import Path
from dotenv import load_dotenv
import anthropic

# Allow importing from project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config.watchlist import WATCHLIST

IST = timezone(timedelta(hours=5, minutes=30))
import csv
from collections import defaultdict


def to_ist(utc_str: str) -> str:
    """Convert a UTC ISO string to readable IST string like '22 Feb, 11:30 PM IST'."""
    if not utc_str:
        return ""
    try:
        dt = datetime.fromisoformat(utc_str.replace("Z", "+00:00"))
        ist_dt = dt.astimezone(IST)
        return ist_dt.strftime("%-d %b, %I:%M %p IST").replace(" 0", " ")
    except Exception:
        return ""

load_dotenv()

ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY")
RECIPIENT_NAME = os.getenv("RECIPIENT_NAME", "Investor")
TMP_DIR = Path(".tmp")
MEMORY_DIR = Path("memory")

# ── Memory helpers ────────────────────────────────────────────────────────────

def load_yesterday_summary() -> dict | None:
    """Load yesterday's brief summary from memory. Returns None if missing or too old."""
    path = MEMORY_DIR / "daily_summary.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        summary_date = datetime.fromisoformat(data.get("date", "1970-01-01"))
        # Discard if older than 5 calendar days (covers weekends + one missed day)
        if (datetime.now(timezone.utc).date() - summary_date.date()).days > 5:
            return None
        return data
    except Exception:
        return None


def save_daily_summary(brief: dict, market_data: dict) -> None:
    """Extract key signals from today's run and persist for tomorrow's brief."""
    MEMORY_DIR.mkdir(exist_ok=True)
    today = datetime.now(timezone.utc).date().isoformat()
    indices = market_data.get("indices", {})
    fii_dii = market_data.get("fii_dii") or {}
    sectors = market_data.get("sector_performance", {})

    # Find Nifty 50 (exclude Bank Nifty)
    nifty_close, nifty_change = None, None
    for data in indices.values():
        name = data.get("name", "")
        if "NIFTY" in name.upper() and "BANK" not in name.upper():
            nifty_close = data.get("close")
            nifty_change = data.get("change_pct")
            break

    summary = {
        "date": today,
        "tldr": brief.get("tldr", []),
        "nifty_close": nifty_close,
        "nifty_change_pct": nifty_change,
        "top_sector_gainers": sectors.get("top_gainers", [])[:3],
        "top_sector_losers": sectors.get("top_losers", [])[:3],
        "fii_net_crores": fii_dii.get("fii_net_crores"),
        "dii_net_crores": fii_dii.get("dii_net_crores"),
    }

    path = MEMORY_DIR / "daily_summary.json"
    path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"  [ok] Daily summary saved → {path}")


def update_sector_sentiment(market_data: dict) -> None:
    """Append today's sector snapshot to the rolling 7-day CSV in memory/."""
    MEMORY_DIR.mkdir(exist_ok=True)
    csv_path = MEMORY_DIR / "sector_sentiment.csv"
    today = datetime.now(timezone.utc).date().isoformat()

    sectors = market_data.get("sector_performance", {})
    fii_dii = market_data.get("fii_dii") or {}
    indices = market_data.get("indices", {})

    # Find Nifty % change
    nifty_pct = None
    for data in indices.values():
        name = data.get("name", "")
        if "NIFTY" in name.upper() and "BANK" not in name.upper():
            nifty_pct = data.get("change_pct")
            break

    def _sector_name(item: dict) -> str:
        return item.get("sector") or item.get("name") or "?"

    gainers_str = "|".join(
        f"{_sector_name(g)}:{g.get('change_pct', 0):+.1f}%"
        for g in sectors.get("top_gainers", [])[:4]
    ) or "N/A"
    losers_str = "|".join(
        f"{_sector_name(l)}:{l.get('change_pct', 0):+.1f}%"
        for l in sectors.get("top_losers", [])[:4]
    ) or "N/A"

    fieldnames = ["date", "nifty_pct", "fii_net", "dii_net", "top_gainers", "top_losers"]

    # Read existing rows, skip any row already written for today
    existing_rows = []
    if csv_path.exists():
        with open(csv_path, "r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            existing_rows = [row for row in reader if row.get("date") != today]

    existing_rows.append({
        "date": today,
        "nifty_pct": f"{nifty_pct:+.2f}%" if nifty_pct is not None else "N/A",
        "fii_net": fii_dii.get("fii_net_crores", "N/A"),
        "dii_net": fii_dii.get("dii_net_crores", "N/A"),
        "top_gainers": gainers_str,
        "top_losers": losers_str,
    })

    # Keep only last 7 trading days
    existing_rows = existing_rows[-7:]

    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(existing_rows)

    print(f"  [ok] Sector sentiment updated → {csv_path} ({len(existing_rows)} days on record)")


def load_sector_trend() -> str | None:
    """Read the rolling 7-day sector CSV and return a formatted trend block for Claude."""
    csv_path = MEMORY_DIR / "sector_sentiment.csv"
    if not csv_path.exists():
        return None
    try:
        rows = []
        with open(csv_path, "r", encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))

        if len(rows) < 2:
            return None  # Not enough history yet

        # Count sector appearances in gainers vs losers across all rows
        gainer_count: dict[str, int] = defaultdict(int)
        loser_count: dict[str, int] = defaultdict(int)

        for row in rows:
            for entry in row.get("top_gainers", "").split("|"):
                sector = entry.split(":")[0].strip()
                if sector and sector != "N/A":
                    gainer_count[sector] += 1
            for entry in row.get("top_losers", "").split("|"):
                sector = entry.split(":")[0].strip()
                if sector and sector != "N/A":
                    loser_count[sector] += 1

        n_days = len(rows)
        threshold = max(2, round(n_days * 0.4))  # appeared in ≥40% of days, min 2

        consistent_gainers = sorted(
            [(s, c) for s, c in gainer_count.items() if c >= threshold],
            key=lambda x: -x[1],
        )
        consistent_losers = sorted(
            [(s, c) for s, c in loser_count.items() if c >= threshold],
            key=lambda x: -x[1],
        )

        lines = [f"=== {n_days}-DAY SECTOR TREND (rolling) ==="]

        if consistent_gainers:
            lines.append("Consistent GAINERS: " + ", ".join(
                f"{s} (positive {c}/{n_days} days)" for s, c in consistent_gainers
            ))
        else:
            lines.append("Consistent GAINERS: None — gains have been scattered this week")

        if consistent_losers:
            lines.append("Consistent LOSERS: " + ", ".join(
                f"{s} (negative {c}/{n_days} days)" for s, c in consistent_losers
            ))
        else:
            lines.append("Consistent LOSERS: None — losses have been scattered this week")

        lines.append("\nDaily breakdown (most recent last):")
        for row in rows:
            lines.append(
                f"  {row['date']}: Nifty {row['nifty_pct']} | "
                f"FII ₹{row['fii_net']} Cr | "
                f"Gainers: {row['top_gainers']} | "
                f"Losers: {row['top_losers']}"
            )

        lines.append(
            "\nUse this trend data in your analysis. If a sector has been consistently "
            "negative or positive for 3+ sessions, call it out as a developing trend — "
            "not just today's noise. E.g., 'Banking has been in the red for 4 straight "
            "sessions now — this is looking like a trend, not a one-day blip.'"
        )

        return "\n".join(lines)
    except Exception as e:
        print(f"  [warn] Could not load sector trend: {e}")
        return None

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Morning Market Brief</title>
<style>
  /* Reset */
  body {{ margin: 0; padding: 0; background-color: #f0f2f5; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Arial, sans-serif; }}
  table {{ border-collapse: collapse; }}
  img {{ border: 0; }}
  a {{ color: #d4a017; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}

  /* Wrapper */
  .wrapper {{ width: 100%; background-color: #f0f2f5; padding: 20px 0; }}
  .container {{ max-width: 600px; margin: 0 auto; background-color: #0f1923; border-radius: 12px; overflow: hidden; }}

  /* Header */
  .header {{ background: linear-gradient(135deg, #0f1923 0%, #1a2d42 100%); padding: 32px 28px 24px; border-bottom: 2px solid #d4a017; }}
  .header-date {{ color: #d4a017; font-size: 12px; font-weight: 600; letter-spacing: 2px; text-transform: uppercase; margin-bottom: 8px; }}
  .header-title {{ color: #ffffff; font-size: 26px; font-weight: 700; margin: 0 0 6px 0; line-height: 1.2; }}
  .header-sub {{ color: #8fa3b8; font-size: 13px; margin: 0; }}

  /* Sections */
  .section {{ padding: 20px 28px; border-bottom: 1px solid #1e2e3d; }}
  .section:last-of-type {{ border-bottom: none; }}
  .section-title {{ color: #d4a017; font-size: 11px; font-weight: 700; letter-spacing: 2px; text-transform: uppercase; margin: 0 0 14px 0; display: flex; align-items: center; gap: 8px; }}
  .section-title-line {{ flex: 1; height: 1px; background: #1e2e3d; }}

  /* TL;DR */
  .tldr-box {{ background: #1a2d42; border-left: 3px solid #d4a017; border-radius: 4px; padding: 14px 16px; }}
  .tldr-item {{ color: #e8edf2; font-size: 14px; line-height: 1.6; margin: 6px 0; display: flex; gap: 10px; }}
  .tldr-bullet {{ color: #d4a017; font-weight: 700; flex-shrink: 0; }}

  /* Market Pulse */
  .market-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }}
  .market-card {{ background: #1a2d42; border-radius: 8px; padding: 12px 14px; }}
  .market-name {{ color: #8fa3b8; font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 4px; }}
  .market-value {{ color: #ffffff; font-size: 18px; font-weight: 700; margin-bottom: 2px; }}
  .market-change-up {{ color: #22c55e; font-size: 13px; font-weight: 600; }}
  .market-change-down {{ color: #ef4444; font-size: 13px; font-weight: 600; }}
  .market-change-neutral {{ color: #8fa3b8; font-size: 13px; }}
  .market-note {{ color: #8fa3b8; font-size: 11px; margin-top: 4px; font-style: italic; }}

  /* News Items */
  .news-item {{ margin-bottom: 18px; padding-bottom: 18px; border-bottom: 1px solid #1e2e3d; }}
  .news-item:last-child {{ border-bottom: none; margin-bottom: 0; padding-bottom: 0; }}
  .news-headline {{ color: #ffffff; font-size: 15px; font-weight: 600; line-height: 1.4; margin: 0 0 4px 0; }}
  .news-time {{ color: #4a6070; font-size: 11px; margin: 0 0 6px 0; }}
  .news-impact {{ color: #c8d6e5; font-size: 13px; line-height: 1.6; margin: 0 0 6px 0; }}
  .news-sentiment {{ display: inline-block; padding: 2px 8px; border-radius: 20px; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 1px; }}
  .bullish {{ background: #14532d; color: #22c55e; }}
  .bearish {{ background: #450a0a; color: #ef4444; }}
  .neutral {{ background: #1e3a5f; color: #60a5fa; }}
  .watchful {{ background: #451a03; color: #fb923c; }}

  /* Stock Watch */
  .stock-watch-grid {{ display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 10px; }}
  .stock-watch-col {{ background: #1a2d42; border-radius: 8px; padding: 12px 14px; }}
  .stock-watch-header {{ font-size: 10px; font-weight: 700; letter-spacing: 1.5px; text-transform: uppercase; margin: 0 0 10px 0; padding-bottom: 8px; border-bottom: 1px solid #243d57; }}
  .swh-green {{ color: #22c55e; }}
  .swh-amber {{ color: #f59e0b; }}
  .swh-red {{ color: #ef4444; }}
  .stock-row {{ margin-bottom: 8px; }}
  .stock-name {{ color: #ffffff; font-size: 13px; font-weight: 600; }}
  .stock-cap {{ color: #4a6070; font-size: 10px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; margin-left: 5px; }}
  .stock-reason {{ color: #8fa3b8; font-size: 11px; line-height: 1.5; margin-top: 2px; }}
  @media only screen and (max-width: 480px) {{
    .stock-watch-grid {{ grid-template-columns: 1fr; }}
  }}

  /* IPO Section */
  .ipo-card {{ background: #1a2d42; border-radius: 8px; padding: 14px 16px; margin-bottom: 10px; }}
  .ipo-name {{ color: #ffffff; font-size: 15px; font-weight: 700; margin-bottom: 8px; }}
  .ipo-meta {{ display: flex; flex-wrap: wrap; gap: 12px; }}
  .ipo-tag {{ color: #8fa3b8; font-size: 12px; }}
  .ipo-tag strong {{ color: #e8edf2; }}
  .ipo-gmp-up {{ color: #22c55e; font-weight: 700; }}
  .ipo-gmp-down {{ color: #ef4444; font-weight: 700; }}
  .ipo-gmp-neutral {{ color: #8fa3b8; font-weight: 700; }}

  /* Watch Today */
  .watch-item {{ color: #c8d6e5; font-size: 13px; line-height: 1.7; display: flex; gap: 10px; margin-bottom: 4px; }}
  .watch-dot {{ color: #d4a017; flex-shrink: 0; }}

  /* Footer */
  .footer {{ background: #0a1219; padding: 20px 28px; text-align: center; }}
  .footer-text {{ color: #4a6070; font-size: 11px; line-height: 1.7; margin: 0; }}

  /* Responsive */
  @media only screen and (max-width: 480px) {{
    .market-grid {{ grid-template-columns: 1fr 1fr; }}
    .header-title {{ font-size: 22px; }}
    .ipo-meta {{ flex-direction: column; gap: 6px; }}
  }}
</style>
</head>
<body>
<div class="wrapper">
<div class="container">

  <!-- HEADER -->
  <div class="header">
    <p class="header-date">{date_str} &nbsp;·&nbsp; Before Market Open</p>
    <h1 class="header-title">Good Morning, {name}! ☀️</h1>
    <p class="header-sub">Your daily Nifty intel — delivered before 9:15 AM IST</p>
  </div>

  <!-- TL;DR -->
  <div class="section">
    <p class="section-title">TL;DR <span class="section-title-line"></span></p>
    <div class="tldr-box">
      {tldr_items}
    </div>
  </div>

  <!-- MARKET PULSE -->
  <div class="section">
    <p class="section-title">Market Pulse <span class="section-title-line"></span></p>
    <div class="market-grid">
      {market_cards}
    </div>
    {gift_nifty_note}
    {fii_dii_note}
  </div>

  <!-- GLOBAL HEADLINES -->
  <div class="section">
    <p class="section-title">Global Headlines &amp; India Impact <span class="section-title-line"></span></p>
    {global_news_items}
  </div>

  <!-- INDIA FOCUS -->
  <div class="section">
    <p class="section-title">India Focus <span class="section-title-line"></span></p>
    {india_news_items}
  </div>

  <!-- IPO CORNER -->
  <div class="section">
    <p class="section-title">IPO Corner <span class="section-title-line"></span></p>
    {ipo_cards}
  </div>

  <!-- STOCK WATCH -->
  <div class="section">
    <p class="section-title">Stock Watch <span class="section-title-line"></span></p>
    <div class="stock-watch-grid">
      {stock_watch_section}
    </div>
  </div>

  <!-- WHAT TO WATCH -->
  <div class="section">
    <p class="section-title">What to Watch Today <span class="section-title-line"></span></p>
    {watch_items}
  </div>

  <!-- FOOTER -->
  <div class="footer">
    <p class="footer-text">
      This is not investment advice. All views are for educational purposes only.<br>
      Past performance is no guarantee of future results. Do your own research.<br><br>
      Generated by your personal AI market brief · {date_str}
    </p>
  </div>

</div>
</div>
</body>
</html>"""


CLAUDE_SYSTEM_PROMPT = """You are Arjun, a sharp and friendly Indian financial analyst writing a daily morning brief for amateur Indian retail investors — people who check their portfolio on Zerodha or Groww but don't follow Bloomberg all day.

=== YOUR PERSONALITY ===
- Warm, direct, conversational — like a smart friend texting you before market open, not a formal analyst
- Opinionated but honest: say "This looks bearish to me because..." not "markets may see pressure"
- Acknowledge uncertainty clearly: "It's too early to tell, but watch for..."
- Never fabricate data. If data wasn't provided, say "data not available today" — do not guess or invent numbers
- Never recommend buying or selling specific stocks. You CAN say sectors look strong or weak
- Write in present tense for current conditions, past tense for what happened overnight

=== JARGON RULE — NON-NEGOTIABLE ===
Every piece of financial jargon MUST be explained in plain language the first time it appears.
Format: term (plain English explanation)
Examples:
- "bond yields (the return on safe US government bonds) rose to 4.5% — this means safe investments are now paying more, making riskier assets like stocks relatively less attractive"
- "the Fed (US central bank) turned hawkish (signalled it will keep interest rates high for longer)"
- "FII outflows (foreign institutional investors pulled money out of Indian stocks)"
- "India VIX (the fear index — measures how much volatility traders expect) is above 15, signalling elevated uncertainty"
- "NIM (net interest margin — the profit banks make on loans vs what they pay depositors) is compressing"

=== YOUR MENTAL MODEL FOR INDIAN MARKETS ===
Use this framework to connect global events to Indian market impact:

US Fed hawkish (rates higher for longer) → FIIs (foreign investors) pull money from India → INR weakens → negative for rate-sensitive sectors (banks, real estate, auto). Dovish Fed → opposite, positive for India.

US Treasury yields rising → global capital flows back to US → negative for all emerging markets including India. Every 0.1% rise in US 10-year yield matters.

Crude oil rising → very negative for India (India imports ~85% of its oil). Hurts: OMCs (IOC, BPCL, HPCL — they sell fuel below cost when prices spike), paints (Asian Paints uses crude derivatives), aviation (IndiGo fuel costs rise), tyre companies. Helps: ONGC, Oil India (they produce oil).

USD strengthening (DXY — the dollar index — going up) → INR weakens → import costs rise (bad for inflation). IT companies get a revenue boost in INR terms (they earn in dollars), but broader sentiment turns negative.

China stimulus / weakness → mixed for India. Weak China reduces competition for commodities but hurts global demand. Strong China can divert FII flows away from India to Chinese markets.

Gold rising → signals risk-off sentiment / uncertainty. Often correlates with geopolitical tension or dollar weakness. Not directly bearish for Nifty but reflects global caution.

FII selling + DII buying → FIIs create selling pressure, DIIs (domestic mutual funds, LIC) provide a floor. Sustained FII outflows over multiple days → bearish trend. DII buying alone usually can't sustain a rally.

RBI rate cut → positive for banks (cheaper funding), NBFCs, real estate, auto (cheaper EMIs for buyers). Rate hold → neutral.

India VIX above 15 → elevated fear, expect choppy sessions. Above 20 → significant uncertainty, consider reducing leveraged positions.

IT sector → driven by US tech spending, BFSI (banking/financial services) client demand, and large deal pipeline. Strong US economy and corporate earnings = good for Indian IT.

Pharma → driven by USFDA approvals or warning letters, US generic drug market pricing, and domestic formulation growth.

Banks → watch credit growth, NPA (non-performing assets = bad loans) trends, RBI policy direction, and NIM (net interest margin) trends.

Auto → rural demand recovery indicators, input costs (steel, aluminium), EV transition dynamics.

FMCG → rural consumption recovery, commodity input costs, volume growth vs price growth mix.

=== CALENDAR AWARENESS ===
- Monday: briefly mention any significant weekend global developments
- Thursday: remind about weekly Nifty options expiry (every Thursday, F&O positions settle — can cause intraday volatility)
- Last week of month: mention monthly F&O expiry (larger impact, monthly positions unwind)

=== ABSOLUTE RULES ===
1. Pick only the 5-8 most market-moving stories — ruthlessly filter out noise
2. Every jargon term explained in plain language (see JARGON RULE above)
3. Every global story must be explicitly connected to Indian market impact
4. Never fabricate data. If something wasn't in the provided data, say so
5. Never recommend buying/selling specific stocks
6. Total output must stay under 1200 words across all sections
7. Be opinionated: "I'd watch this closely", "This is worth ignoring for now", "If this plays out, expect..."

IMPORTANT: Return ONLY valid JSON. No markdown, no code blocks, no explanation outside the JSON."""


def build_claude_prompt(
    news_data: dict,
    market_data: dict,
    ipo_data: dict,
    earnings_data: dict = None,
    yesterday_summary: dict = None,
    sector_trend: str = None,
) -> str:
    """Construct the prompt for Claude with all raw data."""

    # Summarize news — pass top 20 articles to give Claude more signal to filter from
    articles = news_data.get("articles", [])[:20]
    articles_summary = []
    for a in articles:
        articles_summary.append({
            "title": a.get("title", ""),
            "description": a.get("description", "")[:200] if a.get("description") else "",
            "source": a.get("source_name", ""),
            "published_at_ist": to_ist(a.get("published_at", "")),
        })

    # Market data summary — Indian indices
    indices = market_data.get("indices", {})
    market_summary = {}
    for ticker, data in indices.items():
        market_summary[data.get("name", ticker)] = {
            "close": data.get("close"),
            "change_pct": data.get("change_pct"),
        }

    # US market summary — S&P 500, Nasdaq, Dow (previous session close)
    us_markets = market_data.get("us_markets", {})
    us_summary = {}
    for symbol, data in us_markets.items():
        us_summary[data.get("name", symbol)] = {
            "close": data.get("close"),
            "change_pct": data.get("change_pct"),
        }

    # Sector summary
    sectors = market_data.get("sector_performance", {})
    top_gainers = sectors.get("top_gainers", [])[:3]
    top_losers = sectors.get("top_losers", [])[:3]

    fii_dii = market_data.get("fii_dii")
    economic_calendar = market_data.get("economic_calendar", [])[:5]
    # Only pass IPOs with actual GMP data and active/recent dates — skip historical ones
    all_ipos = ipo_data.get("ipos", [])
    active_ipos = [
        ipo for ipo in all_ipos
        if ipo.get("gmp") and ipo.get("gmp") not in ("N/A", "", "₹0", "0")
    ][:6]
    if not active_ipos:
        active_ipos = all_ipos[:4]  # fallback if no GMP data

    # Day-of-week context for Claude
    today_utc = datetime.now(timezone.utc)
    weekday = today_utc.strftime("%A")
    day_of_month = today_utc.day
    last_day = cal_module.monthrange(today_utc.year, today_utc.month)[1]
    is_last_week = day_of_month >= last_day - 6

    calendar_note = ""
    if weekday == "Monday":
        calendar_note = "Today is MONDAY — mention any significant weekend global developments briefly."
    elif weekday == "Thursday":
        calendar_note = "Today is THURSDAY — remind readers about weekly Nifty F&O (futures & options) expiry. F&O expiry means traders must settle their weekly bets today, which can cause intraday volatility especially in the last hour."
    if is_last_week:
        calendar_note += " It is also the LAST WEEK OF THE MONTH — monthly F&O expiry is approaching (or today), which is a larger event than weekly expiry and can cause significant swings."

    # Build watchlist block for Claude
    watchlist_lines = []
    for category, topics in WATCHLIST.items():
        watchlist_lines.append(f"{category}:")
        for topic in topics:
            watchlist_lines.append(f"  - {topic}")
    watchlist_block = "\n".join(watchlist_lines)

    # Earnings calendar (next 7 days)
    upcoming_earnings = (earnings_data or {}).get("events", [])[:8]

    # ── Yesterday's brief memory block ──────────────────────────────────────
    yesterday_block = ""
    if yesterday_summary:
        y_date = yesterday_summary.get("date", "N/A")
        y_close = yesterday_summary.get("nifty_close")
        y_pct = yesterday_summary.get("nifty_change_pct")
        y_fii = yesterday_summary.get("fii_net_crores", "N/A")
        y_dii = yesterday_summary.get("dii_net_crores", "N/A")
        y_gainers = yesterday_summary.get("top_sector_gainers", [])
        y_losers = yesterday_summary.get("top_sector_losers", [])
        y_tldr = yesterday_summary.get("tldr", [])

        def _sname(item: dict) -> str:
            return item.get("sector") or item.get("name") or "?"

        nifty_str = (
            f"{y_close:,.0f} ({y_pct:+.2f}%)" if y_close and y_pct is not None else "N/A"
        )
        gainers_str = ", ".join(
            f"{_sname(g)} ({g.get('change_pct', 0):+.1f}%)" for g in y_gainers
        ) or "N/A"
        losers_str = ", ".join(
            f"{_sname(l)} ({l.get('change_pct', 0):+.1f}%)" for l in y_losers
        ) or "N/A"
        tldr_lines = "\n".join(f"{i+1}. {t}" for i, t in enumerate(y_tldr))

        yesterday_block = f"""
=== YESTERDAY'S BRIEF ({y_date}) ===
Nifty closed at {nifty_str} | FII net: ₹{y_fii} Cr | DII net: ₹{y_dii} Cr
Sector gainers: {gainers_str}
Sector losers: {losers_str}
Yesterday's key points:
{tldr_lines}
Use this for continuity. When today's Nifty or sector moves contrast with yesterday, say so explicitly — e.g. "Nifty fell 1.1% yesterday but is recovering today" or "FIIs were net sellers yesterday and remain so." When a trend continues, flag it as such.
"""

    # ── Sector trend block ───────────────────────────────────────────────────
    sector_trend_block = f"\n{sector_trend}\n" if sector_trend else ""

    prompt = f"""Here is today's raw market data. Your job is to transform it into a sharp, opinionated morning brief.

Today is {weekday}, {today_utc.strftime('%d %B %Y')}.
{calendar_note}
{yesterday_block}{sector_trend_block}
=== PRIORITY WATCHLIST ===
These are the high-signal topics this investor tracks. When any of these appear in the news data, elevate them — they get priority coverage in the brief. If multiple stories compete for the same slot, prefer the ones touching these topics.

{watchlist_block}

=== US MARKET DATA (previous session close) ===
{json.dumps(us_summary, indent=2) if us_summary else "Not available today."}
Note: This shows how US markets closed in their last session. S&P 500 and Nasdaq direction is a strong predictor of Nifty's opening gap.

=== INDIAN MARKET DATA (previous close) ===
{json.dumps(market_summary, indent=2)}

Top sector gainers (yesterday): {json.dumps(top_gainers, indent=2)}
Top sector losers (yesterday): {json.dumps(top_losers, indent=2)}

FII/DII activity (yesterday): {json.dumps(fii_dii, indent=2)}
Note: FII = Foreign Institutional Investors (foreign funds buying/selling Indian stocks). DII = Domestic Institutional Investors (Indian mutual funds, LIC etc.). Positive = net buyers, Negative = net sellers.

=== UPCOMING EARNINGS (next 7 days — US companies that affect Indian stocks) ===
{json.dumps(upcoming_earnings, indent=2) if upcoming_earnings else "No major earnings in the next 7 days."}
Note: When Apple/Microsoft/Google/Nvidia reports earnings, Indian IT stocks (TCS, Infosys, Wipro, HCL) often react in sympathy. Mention upcoming earnings in watch_today if relevant — e.g., "Nvidia reports tomorrow — watch for IT sector moves."

Upcoming economic events: {json.dumps(economic_calendar, indent=2)}

=== NEWS (last 24 hours — pick only the 5-8 most market-moving stories) ===
{json.dumps(articles_summary, indent=2)}

=== IPO DATA ===
{json.dumps(active_ipos, indent=2)}
Note: GMP = Grey Market Premium — unofficial price at which IPO shares trade before listing. Higher GMP = stronger expected listing gain. This is speculative, not guaranteed.

=== YOUR TASK ===
Return ONLY this JSON structure. No other text, no markdown, no code blocks.

{{
  "tldr": [
    "Sentence 1: The single biggest market-moving development right now and its direct Nifty implication",
    "Sentence 2: The most important India-specific story with its sector/market impact",
    "Sentence 3: The one thing to watch or be aware of today — practical and specific"
  ],
  "global_news": [
    {{
      "headline": "Punchy headline under 10 words",
      "published_at_ist": "Copy exactly from the article's published_at_ist field — do not modify",
      "india_impact": "3-4 sentences. Explain what happened (past tense). Then explain the mechanism connecting it to Indian markets using plain English — no jargon without explanation. Then give your honest take on the likely impact on Nifty/sectors. Use the mental model: Fed rates, Treasury yields, crude oil, USD strength, China dynamics as appropriate.",
      "sentiment": "bullish|bearish|neutral|watchful"
    }}
  ],
  "india_news": [
    {{
      "headline": "Punchy headline under 10 words",
      "published_at_ist": "Copy exactly from the article's published_at_ist field — do not modify",
      "analysis": "3-4 sentences. Explain what happened. Explain which sectors or stocks are affected and why — mechanistically, not just 'this is good/bad'. Give your take. Explain any jargon used.",
      "sentiment": "bullish|bearish|neutral|watchful"
    }}
  ],
  "ipo_commentary": [
    {{
      "name": "IPO name",
      "issue_price": "Price band from data, e.g. Rs 216-227",
      "gmp": "GMP value from data — if Rs 15 above a Rs 227 issue price, say 'Rs 15 above issue price (6.6% estimated listing gain)'",
      "subscription": "Subscription status from data, or open/close dates",
      "take": "1-2 sentences: Is this worth applying for? What does the GMP signal? Be direct — 'strong GMP suggests solid listing' or 'flat GMP, wait for listing day action instead'."
    }}
  ],
  "watch_today": [
    "3-5 items. Each must be one plain sentence with a clear action/implication. Format: [What's happening] → [What you should do or look out for]. Examples: 'Nifty closed at 25,571 yesterday — if it opens above 25,600 and holds, momentum is bullish; below 25,450 would be a warning sign.' / 'Crude oil at $83/barrel — if you hold IndiGo or HPCL, watch for early selling pressure today.' / 'US markets rallied 0.8% overnight — Nifty likely to open gap-up around 25,650-25,700.' / 'No major India events scheduled today — direction will be driven by global cues and FII flow.' Keep language simple — no jargon unless explained."
  ],
  "stock_watch": {{
    "tailwinds": [
      {{
        "name": "Stock name (e.g. TCS, ICICI Bank)",
        "cap": "Large Cap or Mid Cap",
        "reason": "1 plain sentence: why today's news is a positive catalyst for this stock. Be specific about the mechanism."
      }}
    ],
    "on_radar": [
      {{
        "name": "Stock name",
        "cap": "Large Cap or Mid Cap",
        "reason": "1 plain sentence: why this stock is worth watching closely today — could go either way, or has news-driven volatility expected."
      }}
    ],
    "headwinds": [
      {{
        "name": "Stock name",
        "cap": "Large Cap or Mid Cap",
        "reason": "1 plain sentence: why today's news creates pressure or risk for this stock. Be specific."
      }}
    ]
  }},
  "sector_spotlight": "2-3 sentences on the single most interesting sector story today — what's moving, why, and what it means for retail investors holding those stocks."
}}

stock_watch RULES:
- 2-4 stocks per category (tailwinds, on_radar, headwinds). Skip a category if nothing relevant.
- Only include stocks that are directly connected to today's news or market data. No guessing.
- Mix large cap and mid cap where relevant. Mention cap size.
- This is educational context, not investment advice. Focus on the connection between news and stock impact.
- If no specific stocks are relevant today, use an empty array [] for that category.

QUALITY CHECKLIST before finalising:
- Every jargon term explained in plain English on first use? YES/NO — if NO, fix it
- Every global story explicitly connected to Indian market mechanism? YES/NO — if NO, fix it
- Did I fabricate any data not provided? If yes, replace with 'data not available today'
- Total word count under 1200? If over, trim the least important items
- Am I being genuinely opinionated (not wishy-washy)? Good: 'This is clearly bearish for IT stocks'. Bad: 'IT stocks may see some pressure'"""

    return prompt


def render_market_cards(indices: dict) -> str:
    """Convert market index data to HTML cards."""
    cards = []
    for ticker, data in indices.items():
        name = data.get("name", ticker)
        close = data.get("close", 0)
        change_pct = data.get("change_pct", 0)

        if change_pct > 0:
            change_class = "market-change-up"
            arrow = "▲"
        elif change_pct < 0:
            change_class = "market-change-down"
            arrow = "▼"
        else:
            change_class = "market-change-neutral"
            arrow = "—"

        # Format value based on type
        if "VIX" in name:
            value_str = f"{close:.2f}"
        elif "USD" in name or "INR" in name:
            value_str = f"₹{close:.2f}"
        else:
            value_str = f"{close:,.2f}"

        cards.append(f"""<div class="market-card">
  <div class="market-name">{name}</div>
  <div class="market-value">{value_str}</div>
  <div class="{change_class}">{arrow} {abs(change_pct):.2f}%</div>
</div>""")
    return "\n".join(cards)


def render_news_items(news_list: list[dict], key_analysis: str = "india_impact") -> str:
    """Render news items as HTML."""
    items = []
    for item in news_list:
        sentiment = item.get("sentiment", "neutral")
        sentiment_label = {"bullish": "Bullish", "bearish": "Bearish",
                           "neutral": "Neutral", "watchful": "Watch"}.get(sentiment, "Neutral")
        analysis = item.get(key_analysis) or item.get("analysis", "")
        timestamp = item.get("published_at_ist", "")
        time_html = f'<p class="news-time">{timestamp}</p>' if timestamp else ""
        items.append(f"""<div class="news-item">
  <p class="news-headline">{item.get('headline', '')}</p>
  {time_html}
  <p class="news-impact">{analysis}</p>
  <span class="news-sentiment {sentiment}">{sentiment_label}</span>
</div>""")
    return "\n".join(items) if items else '<p style="color:#8fa3b8;font-size:13px;">No major news today.</p>'


def render_ipo_cards(ipos: list[dict]) -> str:
    """Render IPO cards as HTML."""
    if not ipos:
        return '<p style="color:#8fa3b8;font-size:13px;">No active IPOs at the moment.</p>'

    cards = []
    for ipo in ipos:
        gmp_raw = str(ipo.get("gmp", ""))
        if gmp_raw and gmp_raw not in ("N/A", "—", ""):
            if "above" in gmp_raw.lower() or ("+" in gmp_raw and "-" not in gmp_raw):
                gmp_class = "ipo-gmp-up"
            elif "below" in gmp_raw.lower() or gmp_raw.startswith("-"):
                gmp_class = "ipo-gmp-down"
            else:
                gmp_class = "ipo-gmp-neutral"
        else:
            gmp_class = "ipo-gmp-neutral"

        cards.append(f"""<div class="ipo-card">
  <div class="ipo-name">{ipo.get('name', 'Unknown IPO')}</div>
  <div class="ipo-meta">
    <span class="ipo-tag">Price: <strong>{ipo.get('issue_price', 'N/A')}</strong></span>
    <span class="ipo-tag">GMP: <strong class="{gmp_class}">{ipo.get('gmp', 'N/A')}</strong></span>
    <span class="ipo-tag">Subscribed: <strong>{ipo.get('subscription', 'N/A')}</strong></span>
  </div>
  <p style="color:#8fa3b8;font-size:12px;margin:8px 0 0 0;">{ipo.get('take', '')}</p>
</div>""")
    return "\n".join(cards)


def render_tldr(tldr_items: list[str]) -> str:
    """Render TL;DR bullets."""
    bullets = []
    for i, item in enumerate(tldr_items[:3], 1):
        bullets.append(f'<div class="tldr-item"><span class="tldr-bullet">{i}.</span> {item}</div>')
    return "\n".join(bullets)


def render_watch_items(items: list[str]) -> str:
    """Render 'What to watch' items."""
    rendered = []
    for item in items:
        rendered.append(f'<div class="watch-item"><span class="watch-dot">›</span> {item}</div>')
    return "\n".join(rendered)


def render_stock_watch(stock_watch: dict) -> str:
    """Render the three-column Stock Watch section."""
    if not stock_watch:
        return '<p style="color:#8fa3b8;font-size:13px;">No stock-specific signals today.</p>'

    columns = [
        ("tailwinds",  "LOOKS INTERESTING",  "swh-green"),
        ("on_radar",   "KEEP AN EYE ON",     "swh-amber"),
        ("headwinds",  "APPROACH WITH CARE", "swh-red"),
    ]

    html_cols = []
    for key, label, css_class in columns:
        stocks = stock_watch.get(key, [])
        rows_html = ""
        for s in stocks:
            cap_badge = f'<span class="stock-cap">{s.get("cap", "")}</span>' if s.get("cap") else ""
            rows_html += f"""<div class="stock-row">
  <div><span class="stock-name">{s.get("name", "")}</span>{cap_badge}</div>
  <div class="stock-reason">{s.get("reason", "")}</div>
</div>"""

        if not rows_html:
            rows_html = '<p style="color:#4a6070;font-size:12px;margin:0;">Nothing specific today</p>'

        html_cols.append(f"""<div class="stock-watch-col">
  <p class="stock-watch-header {css_class}">{label}</p>
  {rows_html}
</div>""")

    return "\n".join(html_cols)


def generate_brief(news_data: dict, market_data: dict, ipo_data: dict, earnings_data: dict = None) -> str:
    """Main function: calls Claude and renders the HTML email."""
    if not ANTHROPIC_KEY:
        raise ValueError("ANTHROPIC_API_KEY not set in .env")

    # Load persistent memory (silent no-op on first run)
    yesterday_summary = load_yesterday_summary()
    sector_trend = load_sector_trend()
    if yesterday_summary:
        print(f"  [ok] Yesterday's summary loaded (date: {yesterday_summary.get('date')})")
    if sector_trend:
        print(f"  [ok] Sector trend loaded")

    print("\nCalling Claude to generate brief...")
    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    prompt = build_claude_prompt(
        news_data, market_data, ipo_data, earnings_data or {},
        yesterday_summary=yesterday_summary,
        sector_trend=sector_trend,
    )

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=8192,
        system=CLAUDE_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )

    raw_response = message.content[0].text.strip()
    print("  [ok] Claude response received")

    # Parse JSON response
    try:
        # Handle case where Claude wraps in code block despite instructions
        if raw_response.startswith("```"):
            raw_response = raw_response.split("```")[1]
            if raw_response.startswith("json"):
                raw_response = raw_response[4:]
        brief = json.loads(raw_response)
    except json.JSONDecodeError as e:
        print(f"  [error] Failed to parse Claude JSON: {e}")
        print(f"  Raw response preview: {raw_response[:500]}")
        raise

    print("  [ok] Brief JSON parsed successfully")

    # Persist today's summary for tomorrow's brief
    try:
        save_daily_summary(brief, market_data)
    except Exception as e:
        print(f"  [warn] Could not save daily summary: {e}")

    # Build HTML
    today = datetime.now(timezone.utc)
    date_str = today.strftime("%A, %d %B %Y")

    indices = market_data.get("indices", {})
    gift_nifty = market_data.get("gift_nifty")
    fii_dii = market_data.get("fii_dii")

    # Gift Nifty note
    if gift_nifty:
        gift_note = f'<p style="color:#8fa3b8;font-size:12px;margin-top:12px;font-style:italic;">📡 Gift Nifty: {gift_nifty.get("last", "N/A")} — {gift_nifty.get("note", "")}</p>'
    else:
        gift_note = ""

    # FII/DII note
    if fii_dii:
        fii_val = fii_dii.get("fii_net_crores", "N/A")
        dii_val = fii_dii.get("dii_net_crores", "N/A")
        fii_color = "#22c55e" if str(fii_val).replace(",", "").replace(".", "").lstrip("-").isdigit() and float(str(fii_val).replace(",", "")) > 0 else "#ef4444"
        dii_color = "#22c55e" if str(dii_val).replace(",", "").replace(".", "").lstrip("-").isdigit() and float(str(dii_val).replace(",", "")) > 0 else "#ef4444"
        fii_note = f'<p style="color:#8fa3b8;font-size:12px;margin-top:8px;">FII net: <strong style="color:{fii_color}">₹{fii_val} Cr</strong> &nbsp;|&nbsp; DII net: <strong style="color:{dii_color}">₹{dii_val} Cr</strong> &nbsp;<span style="font-style:italic;">(yesterday\'s data)</span></p>'
    else:
        fii_note = ""

    html = HTML_TEMPLATE.format(
        date_str=date_str,
        name=RECIPIENT_NAME,
        tldr_items=render_tldr(brief.get("tldr", [])),
        market_cards=render_market_cards(indices),
        gift_nifty_note=gift_note,
        fii_dii_note=fii_note,
        global_news_items=render_news_items(brief.get("global_news", []), "india_impact"),
        india_news_items=render_news_items(brief.get("india_news", []), "analysis"),
        ipo_cards=render_ipo_cards(brief.get("ipo_commentary", [])),
        stock_watch_section=render_stock_watch(brief.get("stock_watch", {})),
        watch_items=render_watch_items(brief.get("watch_today", [])),
    )

    return html


def save(html: str) -> Path:
    TMP_DIR.mkdir(exist_ok=True)
    out = TMP_DIR / "email_content.html"
    out.write_text(html, encoding="utf-8")
    print(f"  Saved → {out}")
    return out


if __name__ == "__main__":
    print("Loading data from .tmp/...")
    news_path = TMP_DIR / "news.json"
    market_path = TMP_DIR / "market_data.json"
    ipo_path = TMP_DIR / "ipo_data.json"
    earnings_path = TMP_DIR / "earnings_calendar.json"

    for p in [news_path, market_path, ipo_path]:
        if not p.exists():
            print(f"  [error] Missing: {p}. Run the fetch scripts first.")
            exit(1)

    news_data = json.loads(news_path.read_text(encoding="utf-8"))
    market_data = json.loads(market_path.read_text(encoding="utf-8"))
    ipo_data = json.loads(ipo_path.read_text(encoding="utf-8"))
    earnings_data = json.loads(earnings_path.read_text(encoding="utf-8")) if earnings_path.exists() else {}

    html = generate_brief(news_data, market_data, ipo_data, earnings_data)
    save(html)
    print("\nDone. Open .tmp/email_content.html in your browser to preview.")
