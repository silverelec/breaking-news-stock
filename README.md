# Breaking News Stock

An agentic AI system that delivers a daily morning email newsletter summarising breaking global and Indian news, market data, and IPO updates — written for an amateur investor, timed to land before the Indian market opens at 9:15 AM IST.

Runs fully automatically via GitHub Actions. No server required.

---

## What you get in your inbox

Every morning at ~8:00 AM IST:

| Section | Contents |
|---|---|
| **TL;DR** | 3 key takeaways for the day |
| **Market Pulse** | Nifty 50, Sensex, India VIX, USD/INR, sector indices, FII/DII flows, US close |
| **Global Headlines** | 3–5 global stories with India market impact analysis |
| **India Focus** | 3–5 India-specific stories with sector-level analysis |
| **IPO Corner** | Active IPOs with GMP, subscription %, and analyst take |
| **What to Watch** | Upcoming events, data releases, and earnings to monitor |

---

## How it works

Built on the **WAT framework** (Workflows → Agent → Tools): deterministic Python scripts handle data fetching, Claude handles analysis and writing.

```
GitHub Actions (02:30 UTC daily)
         ↓
run_daily_brief.py
         ↓
┌─────────────────────────────────────────┐
│ Step 1  fetch_news.py                   │ → .tmp/news.json
│         NewsAPI · Finnhub · RSS feeds   │
└─────────────────────────────────────────┘
         ↓
┌─────────────────────────────────────────┐
│ Step 2  fetch_market_data.py            │ → .tmp/market_data.json
│         yfinance · Polygon · NSE FII/DII│
└─────────────────────────────────────────┘
         ↓
┌─────────────────────────────────────────┐
│ Step 3  fetch_ipo_data.py               │ → .tmp/ipo_data.json
│         Chittorgarh (primary)           │
│         ipowatch.in (fallback)          │
└─────────────────────────────────────────┘
         ↓
┌─────────────────────────────────────────┐
│ Step 3b fetch_earnings_calendar.py      │ → .tmp/earnings_calendar.json
│         Finnhub US earnings (next 7d)   │
└─────────────────────────────────────────┘
         ↓
┌─────────────────────────────────────────┐
│ Step 4  generate_brief.py               │ → .tmp/email_content.html
│         Claude claude-sonnet-4-6                │
└─────────────────────────────────────────┘
         ↓
┌─────────────────────────────────────────┐
│ Step 5  send_email.py                   │ → Gmail SMTP
└─────────────────────────────────────────┘
```

Steps 1–3 fail gracefully (pipeline continues with available data). Steps 4–5 are critical — failure sends a plain-text alert email instead.

---

## Data sources

| Data | Source | Free tier |
|---|---|---|
| Global + India news | [NewsAPI](https://newsapi.org) | 100 req/day |
| Market news + sentiment | [Finnhub](https://finnhub.io) | Yes |
| RSS news fallback | ET, Business Standard, Reuters India, Moneycontrol, Mint | Free |
| Indian indices (Nifty, Sensex, VIX, sectors) | yfinance | Free |
| US market close (S&P, Nasdaq, Dow) | [Polygon.io / Massive](https://massive.com) | Free tier |
| IPO + GMP + subscription | Chittorgarh.com, ipowatch.in | Scraped |
| US earnings calendar | Finnhub | Yes |
| AI analysis + writing | [Anthropic Claude](https://anthropic.com) | Paid |
| Email delivery | Gmail SMTP | Free |

---

## Setup

### 1. Fork this repo

Click **Fork** at the top right of this page.

### 2. Get API keys

Sign up (all free tiers work):

- [newsapi.org](https://newsapi.org) → `NEWSAPI_KEY`
- [finnhub.io](https://finnhub.io) → `FINNHUB_API_KEY`
- [massive.com](https://massive.com) (formerly Polygon.io) → `POLYGON_API_KEY`
- [console.anthropic.com](https://console.anthropic.com) → `ANTHROPIC_API_KEY`
- Gmail App Password → `EMAIL_PASSWORD`
  (Google Account → Security → 2-Step Verification → App Passwords)

### 3. Add secrets to GitHub

Go to your fork → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

| Secret | Value |
|---|---|
| `NEWSAPI_KEY` | NewsAPI key |
| `FINNHUB_API_KEY` | Finnhub key |
| `POLYGON_API_KEY` | Massive/Polygon key |
| `ALPHAVANTAGE_KEY` | Alpha Vantage key (optional) |
| `ANTHROPIC_API_KEY` | Anthropic key |
| `EMAIL_FROM` | Your Gmail address |
| `EMAIL_TO` | Recipient email |
| `EMAIL_PASSWORD` | Gmail App Password |
| `RECIPIENT_NAME` | Your first name |

### 4. Test it

Go to **Actions** → **Daily Market Brief** → **Run workflow**

Check your inbox in ~2 minutes.

### 5. Schedule

The workflow runs automatically at **02:30 UTC = 8:00 AM IST** every day. To change the time, edit the `cron` line in [`.github/workflows/daily_brief.yml`](.github/workflows/daily_brief.yml):

```yaml
- cron: "30 2 * * *"   # MM HH * * * in UTC
```

---

## Local development

```bash
# Install dependencies
pip install -r requirements.txt

# Copy and fill in your keys
cp .env.example .env   # edit .env with your keys

# Run without sending email
python tools/run_daily_brief.py --no-send

# Run with browser preview
python tools/test_run.py

# Send a test email (subject prefixed with [TEST])
python tools/run_daily_brief.py --test

# Test individual steps
python tools/test_run.py --step news
python tools/test_run.py --step market
python tools/test_run.py --step ipo
python tools/test_run.py --step generate
```

---

## Project structure

```
.github/workflows/
  daily_brief.yml          # GitHub Actions schedule + secrets injection
config/
  watchlist.py             # News search queries and topic priorities
tools/
  run_daily_brief.py       # Main orchestrator
  fetch_news.py            # NewsAPI + Finnhub + RSS
  fetch_market_data.py     # yfinance + Polygon + NSE FII/DII
  fetch_ipo_data.py        # Chittorgarh + ipowatch scraper
  fetch_earnings_calendar.py  # Finnhub US earnings
  generate_brief.py        # Claude API + HTML rendering
  send_email.py            # Gmail SMTP
  test_run.py              # Local testing helper
  utils.py                 # Shared HTTP retry helper
workflows/
  daily_market_brief.md    # Full SOP (objectives, inputs, error handling)
.env                       # API keys — never committed
requirements.txt
```

---

## Reliability

- All HTTP calls retry 3× with exponential backoff (`tools/utils.py`)
- Non-critical steps (news, market, IPO) degrade gracefully — email still sends with available data
- If Claude or Gmail fails, a plain-text failure alert is sent instead
- Run history stored in `.tmp/run_log.json` (last 30 runs), uploaded as a GitHub Actions artifact

---

## Customisation

**Change what news is monitored** — edit `config/watchlist.py`. The `SEARCH_QUERIES` list controls NewsAPI searches; `WATCHLIST` controls what Claude treats as high-priority.

**Add more recipients** — set `EMAIL_TO` to a comma-separated list (or modify `tools/send_email.py`).

**Change delivery time** — edit the `cron` line in the workflow file. Use UTC (IST = UTC+5:30).

---

## Tech stack

Python 3.11 · Anthropic Claude claude-sonnet-4-6 · GitHub Actions · yfinance · BeautifulSoup4 · feedparser · Jinja2 · Gmail SMTP
