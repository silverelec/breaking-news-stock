# Workflow: Daily Indian Market Brief

## Objective
Generate and deliver a daily morning email newsletter to the user's inbox before 9:15 AM IST (Indian market open). The email summarizes breaking news from the last 24 hours, its impact on the Indian stock market, and IPO data — written accessibly for an amateur investor.

## Trigger
- **Time**: 8:00 AM IST daily (= 02:30 UTC)
- **Triggered by**: Windows Task Scheduler OR GitHub Actions cron
- **Entry point**: `python tools/run_daily_brief.py`

## Required Inputs
| Source | Tool | Key |
|--------|------|-----|
| Global/India news (24h) | NewsAPI.org | `NEWSAPI_KEY` |
| RSS news fallback (free) | feedparser — ET, BS, Reuters, Moneycontrol, Mint | (none) |
| Market news + sentiment | Finnhub | `FINNHUB_API_KEY` |
| Indian indices (Nifty, Sensex, VIX) | yfinance | (none) |
| US market close (S&P, Nasdaq, Dow) | Polygon.io | `POLYGON_API_KEY` |
| Upcoming earnings (US tech/banks) | Finnhub | `FINNHUB_API_KEY` |
| IPO + GMP + subscription data | Chittorgarh.com (primary), ipowatch.in (fallback) | (none) |
| AI analysis + newsletter writing | Anthropic Claude | `ANTHROPIC_API_KEY` |
| Email delivery | Gmail SMTP | `EMAIL_FROM`, `EMAIL_TO`, `EMAIL_PASSWORD` |

## Pipeline Steps

### Step 1: Fetch News → `.tmp/news.json`
```
python tools/fetch_news.py
```
- Calls Finnhub market news (with retry)
- Calls NewsAPI top-headlines (country=in) — Indian news (with retry)
- Calls NewsAPI top-headlines (category=business) — global news (with retry)
- Calls NewsAPI everything — India economy, RBI, Fed, inflation searches (with retry)
- Calls RSS feeds (Economic Times, Business Standard, Reuters India, Moneycontrol, Mint) — free, no quota
- Deduplicates by title (first 60 chars)
- Detects NewsAPI quota exhaustion (`rateLimited` error code) and skips gracefully
- **Failure behavior**: RSS feeds always run as supplement. If NewsAPI and Finnhub both fail, RSS provides coverage. Pipeline continues.

### Step 2: Fetch Market Data → `.tmp/market_data.json`
```
python tools/fetch_market_data.py
```
- yfinance: Nifty 50 (`^NSEI`), Sensex (`^BSESN`), India VIX (`^INDIAVIX`), USD/INR (`USDINR=X`)
- yfinance: Sector indices (Bank Nifty, IT, Pharma, Auto, FMCG, Realty, Energy, Metal) — with retry (3 attempts, exponential backoff)
- Polygon.io: US market previous close — S&P 500 (SPY), Nasdaq 100 (QQQ), Dow Jones (DIA)
- Gift Nifty (pre-market signal — best effort, may not always be available)
- FII/DII data — tries NSE API first (improved session handling), falls back to Moneycontrol scrape
- Finnhub economic calendar (upcoming RBI, Fed, CPI events) — with retry
- **Failure behavior**: Missing tickers are skipped. Pipeline continues with available data.

### Step 3: Fetch IPO Data → `.tmp/ipo_data.json`
```
python tools/fetch_ipo_data.py
```
- **Primary**: Scrapes Chittorgarh.com GMP page + subscription page. Merged by name.
- **Fallback**: If Chittorgarh returns no data, falls back to ipowatch.in (GMP + listings)
- Both scrapers have retry (2 attempts) for transient network failures
- **Failure behavior**: If all sources fail (HTML changed), returns empty list. Pipeline continues without IPO data.
- **Fixing scraper**: If IPO data stops appearing, inspect the page tables and update column indices in `fetch_ipo_data.py`

### Step 3b: Fetch Earnings Calendar → `.tmp/earnings_calendar.json`
```
python tools/fetch_earnings_calendar.py
```
- Fetches upcoming earnings (next 7 days) for US companies that affect Indian IT/banking/macro
- Covers: AAPL, MSFT, GOOGL, AMZN, META, NVDA, JPM, GS, BAC, XOM, CVX
- Source: Finnhub `/calendar/earnings` endpoint
- **Failure behavior**: Non-fatal. If Finnhub fails, earnings section is empty. Pipeline continues.

### Step 4: Generate Brief → `.tmp/email_content.html`
```
python tools/generate_brief.py
```
- Loads `.tmp/news.json`, `.tmp/market_data.json`, `.tmp/ipo_data.json`
- Constructs prompt with all raw data
- Calls Claude API (`claude-sonnet-4-6`) for analysis and writing
- Claude returns structured JSON with sections: TL;DR, global news, India news, IPO commentary, watch today
- Renders JSON into HTML email using inline template
- **Failure behavior**: If Claude call fails, pipeline STOPS and logs error. Email is not sent.

### Step 5: Send Email
```
python tools/send_email.py
```
- Reads `.tmp/email_content.html`
- Sends via Gmail SMTP (smtp.gmail.com:587 with STARTTLS)
- Subject: `📈 Your Market Brief — Mon DD Mon`
- **Failure behavior**: If SMTP fails (auth error, network), pipeline STOPS and logs error.

## Intermediate Files (`.tmp/`)
All files are regenerated on each run. Safe to delete anytime.

| File | Contents | Used by |
|------|----------|---------|
| `.tmp/news.json` | All news articles (NewsAPI + Finnhub + RSS) | generate_brief.py |
| `.tmp/market_data.json` | Indian indices, US markets, sectors, FII/DII, calendar | generate_brief.py |
| `.tmp/ipo_data.json` | Active IPOs, GMP, subscription data | generate_brief.py |
| `.tmp/earnings_calendar.json` | Upcoming US earnings (next 7 days) | generate_brief.py |
| `.tmp/email_content.html` | Final rendered HTML email | send_email.py |
| `.tmp/run_log.json` | Last 30 pipeline run results | Monitoring |

## Email Structure
1. Header — Date + greeting
2. TL;DR — 3 key takeaways for the day
3. Market Pulse — Nifty/Sensex/VIX/USD-INR + FII/DII data + Gift Nifty
4. Global Headlines — 3–5 global stories with India impact analysis + sentiment tag
5. India Focus — 3–5 India-specific stories with analysis + sentiment tag
6. IPO Corner — Active IPOs with GMP, subscription %, analyst take
7. What to Watch — 3–5 events/data points to watch today
8. Footer — Disclaimer

## Error Handling

| Scenario | Behavior |
|----------|----------|
| NewsAPI fails | Use Finnhub as fallback; if both fail, continue with empty news (Claude will note limited data) |
| yfinance data unavailable | Skip missing tickers; market section will be partial |
| Chittorgarh scrape fails | IPO section is omitted from email |
| Claude API fails | Pipeline aborts — no email sent. Check `ANTHROPIC_API_KEY`. |
| Gmail SMTP auth fails | Pipeline aborts. Verify App Password is correct (not your login password). |
| Gmail SMTP network timeout | Retry once after 30s, then abort. |

## Monitoring
Check `.tmp/run_log.json` to see if recent runs succeeded:
```bash
python -c "import json; runs = json.load(open('.tmp/run_log.json')); print(runs[-1])"
```

## Known Issues & Quirks

### NewsAPI Free Tier Limits
- 100 requests/day max on free tier
- Returns headlines only (no full article text) on free tier
- This is sufficient — Claude analyzes headlines + descriptions
- **Update**: If you hit limits, consider GNews API (add `GNEWS_API_KEY` to .env)

### Chittorgarh Scraping
- The site occasionally changes HTML structure — if IPO data stops appearing, inspect the page and update selectors in `tools/fetch_ipo_data.py`
- GMP data is highly dynamic and fetched as close to send time as possible
- Add `GNEWS_API_KEY` to .env if you want GNews as a news fallback

### Gift Nifty
- yfinance doesn't reliably expose Gift Nifty (GIFT City exchange) futures
- If unavailable, the pre-market note is omitted from the email
- Alternative: scrape NSE India's pre-open session page manually

### FII/DII Data
- NSE India website uses bot protection that may block scraping
- If consistently failing, add manual FII/DII numbers to the prompt or skip this feature
- FII/DII data is always from the previous trading day

### yfinance Rate Limiting
- Added 0.5s delay between ticker requests
- If you get 429 errors, increase delay in `fetch_market_data.py`

## Changelog
| Date | Change |
|------|--------|
| 2026-02-21 | Initial version created |
| 2026-02-21 | Phase 1 reliability: retry logic (`tools/utils.py` retrying_get), failure alert email on pipeline crash, pinned requirements.txt |
| 2026-02-21 | Phase 2 data quality: RSS news fallback (feedparser), Polygon.io US market data, Chittorgarh primary + ipowatch fallback IPO scraper, earnings calendar tool, 8192 token budget for Claude |

## Testing
```bash
# Full test run without sending email
python tools/test_run.py --no-browser

# Full test + open browser preview
python tools/test_run.py

# Full test + send test email to inbox
python tools/test_run.py --send-test

# Test individual steps
python tools/test_run.py --step news
python tools/test_run.py --step market
python tools/test_run.py --step ipo
python tools/test_run.py --step generate
```
