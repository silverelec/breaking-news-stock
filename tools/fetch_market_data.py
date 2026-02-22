"""
fetch_market_data.py
Fetches Indian and global market data for the daily brief.

Data collected:
  - Nifty 50, Sensex, Bank Nifty (prev day close + change)
  - India VIX (fear/greed index)
  - USD/INR exchange rate
  - Gift Nifty futures (pre-market signal) — via scraping if yfinance unavailable
  - FII/DII provisional data from NSE (web scrape, multiple endpoint fallback)
  - Nifty sector performance (top 3 gainers, top 3 losers)
  - Upcoming economic events from Finnhub
  - US market previous close (S&P 500, Nasdaq 100, Dow Jones) via Polygon.io

Saves to .tmp/market_data.json.

Usage:
    python tools/fetch_market_data.py
"""

import os
import json
import time
import sys
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils import retrying_get

load_dotenv()

FINNHUB_KEY = os.getenv("FINNHUB_API_KEY")
POLYGON_KEY = os.getenv("POLYGON_API_KEY")
TMP_DIR = Path(".tmp")

# NSE sector indices mapped to readable names
SECTOR_INDICES = {
    "^NSEBANK": "Bank Nifty",
    "^CNXIT": "Nifty IT",
    "^CNXPHARMA": "Nifty Pharma",
    "^CNXAUTO": "Nifty Auto",
    "^CNXFMCG": "Nifty FMCG",
    "^CNXREALTY": "Nifty Realty",
    "^CNXENERGY": "Nifty Energy",
    "^CNXMETAL": "Nifty Metal",
}

# Main indices
MAIN_INDICES = {
    "^NSEI": "Nifty 50",
    "^BSESN": "Sensex",
    "^INDIAVIX": "India VIX",
    "USDINR=X": "USD/INR",
}

# US market ETFs via Polygon.io (proxy for major indices)
US_MARKET_TICKERS = {
    "SPY": "S&P 500",
    "QQQ": "Nasdaq 100",
    "DIA": "Dow Jones",
}


def fetch_yfinance_data(tickers: dict[str, str]) -> dict:
    """Fetch data for a set of tickers using yfinance. Returns dict of ticker → data."""
    try:
        import yfinance as yf
    except ImportError:
        print("  [error] yfinance not installed. Run: pip install yfinance")
        return {}

    results = {}
    for ticker, name in tickers.items():
        for attempt in range(1, 4):  # up to 3 attempts per ticker
            try:
                t = yf.Ticker(ticker)
                hist = t.history(period="2d")
                if hist.empty:
                    print(f"  [warn] No data for {ticker} ({name})")
                    break

                current = hist["Close"].iloc[-1]
                prev = hist["Close"].iloc[-2] if len(hist) >= 2 else current
                change = current - prev
                change_pct = (change / prev * 100) if prev else 0

                results[ticker] = {
                    "name": name,
                    "ticker": ticker,
                    "close": round(float(current), 2),
                    "prev_close": round(float(prev), 2),
                    "change": round(float(change), 2),
                    "change_pct": round(float(change_pct), 2),
                }
                print(f"  [ok] {name}: {current:.2f} ({change_pct:+.2f}%)")
                break
            except Exception as e:
                if attempt < 3:
                    print(f"    [retry {attempt}/3] {name}: {e} — retrying in {2 ** attempt}s")
                    time.sleep(2 ** attempt)
                else:
                    print(f"  [error] {name} ({ticker}): {e}")
        time.sleep(0.5)  # Avoid rate limiting between tickers
    return results


def fetch_gift_nifty() -> dict | None:
    """
    Attempt to get Gift Nifty (NIFTY50 futures on GIFT exchange) as pre-market signal.
    yfinance doesn't reliably have this. We try a few approaches.
    """
    try:
        import yfinance as yf
        for ticker in ["NIFTY_FUT_NSE", "NIFTYBEES.NS"]:
            try:
                t = yf.Ticker(ticker)
                info = t.fast_info
                if hasattr(info, "last_price") and info.last_price:
                    return {
                        "name": "Gift Nifty (approx)",
                        "ticker": ticker,
                        "last": round(float(info.last_price), 2),
                        "note": "Pre-market signal — if above Nifty close, expect gap-up open",
                    }
            except Exception:
                continue
    except Exception:
        pass
    print("  [warn] Gift Nifty data unavailable via yfinance — skipping")
    return None


def fetch_fii_dii_data() -> dict | None:
    """
    Fetch FII/DII provisional data from NSE India.
    Tries multiple endpoints with improved session handling.
    Falls back to Moneycontrol scrape if NSE blocks the request.
    """
    from bs4 import BeautifulSoup

    # Attempt 1: NSE API with full browser session simulation
    try:
        import requests
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Referer": "https://www.nseindia.com/market-data/fii-dii-trade",
            "Origin": "https://www.nseindia.com",
        }
        session = requests.Session()
        # Warm up the session with multiple page hits to acquire cookies
        session.get("https://www.nseindia.com/", headers=headers, timeout=10)
        time.sleep(1)
        session.get("https://www.nseindia.com/market-data/fii-dii-trade", headers=headers, timeout=10)
        time.sleep(1)
        resp = session.get(
            "https://www.nseindia.com/api/fiidiiTradeReact",
            headers=headers,
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            if data:
                latest = data[0] if isinstance(data, list) else data
                fii_net = latest.get("fiiNetValue", latest.get("fii_net", "N/A"))
                dii_net = latest.get("diiNetValue", latest.get("dii_net", "N/A"))
                print(f"  [ok] FII/DII from NSE: FII={fii_net}, DII={dii_net}")
                return {
                    "date": latest.get("date", ""),
                    "fii_net_crores": fii_net,
                    "dii_net_crores": dii_net,
                    "note": "Positive = bought, Negative = sold (in crores INR)",
                    "source": "NSE",
                }
        print(f"  [warn] NSE FII/DII API returned status {resp.status_code} — trying fallback")
    except Exception as e:
        print(f"  [warn] NSE FII/DII attempt failed: {e} — trying fallback")

    # Fallback: Moneycontrol FII/DII page scrape
    try:
        resp = retrying_get(
            "https://www.moneycontrol.com/stocks/marketstats/fii_dii_activity/index.php",
            max_attempts=2,
            timeout=12,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "text/html,application/xhtml+xml",
            },
        )
        soup = BeautifulSoup(resp.text, "lxml")
        # Moneycontrol renders FII/DII in a table — look for net values
        tables = soup.find_all("table")
        for table in tables:
            rows = table.find_all("tr")
            for row in rows:
                cells = [td.get_text(strip=True) for td in row.find_all("td")]
                if len(cells) >= 3 and "FII" in cells[0]:
                    fii_net = cells[-1].replace(",", "").replace("₹", "").strip()
                if len(cells) >= 3 and "DII" in cells[0]:
                    dii_net = cells[-1].replace(",", "").replace("₹", "").strip()
        if fii_net and dii_net:
            print(f"  [ok] FII/DII from Moneycontrol: FII={fii_net}, DII={dii_net}")
            return {
                "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "fii_net_crores": fii_net,
                "dii_net_crores": dii_net,
                "note": "Positive = bought, Negative = sold (in crores INR)",
                "source": "Moneycontrol",
            }
    except Exception as e:
        print(f"  [warn] Moneycontrol FII/DII fallback failed: {e}")

    print("  [warn] FII/DII data unavailable from all sources — skipping")
    return None


def fetch_us_market_polygon() -> dict:
    """
    Fetch US market previous-session close from Polygon.io.
    Returns S&P 500, Nasdaq 100, Dow Jones data (via SPY, QQQ, DIA ETFs).
    Gives context on how US markets closed before Indian market opens.
    """
    if not POLYGON_KEY:
        print("  [warn] POLYGON_API_KEY not set — skipping US market data")
        return {}

    results = {}
    for symbol, name in US_MARKET_TICKERS.items():
        try:
            resp = retrying_get(
                f"https://api.polygon.io/v2/aggs/ticker/{symbol}/prev",
                max_attempts=2,
                params={"adjusted": "true", "apiKey": POLYGON_KEY},
                timeout=10,
            )
            data = resp.json()
            result_list = data.get("results", [])
            if result_list:
                r = result_list[0]
                close = r.get("c", 0)
                open_ = r.get("o", close)
                change = close - open_
                change_pct = (change / open_ * 100) if open_ else 0
                results[symbol] = {
                    "name": name,
                    "ticker": symbol,
                    "close": round(float(close), 2),
                    "change": round(float(change), 2),
                    "change_pct": round(float(change_pct), 2),
                }
                print(f"  [ok] {name} ({symbol}): {close:.2f} ({change_pct:+.2f}%)")
        except Exception as e:
            print(f"  [warn] Polygon {name} ({symbol}) failed: {e}")

    return results


def fetch_finnhub_economic_calendar() -> list[dict]:
    """Fetch upcoming economic events from Finnhub (RBI, Fed, CPI etc.)."""
    if not FINNHUB_KEY:
        print("  [warn] FINNHUB_API_KEY not set — skipping economic calendar")
        return []

    try:
        from datetime import timedelta
        today = datetime.now(timezone.utc)
        from_dt = today.strftime("%Y-%m-%d")
        to_dt = (today + timedelta(days=3)).strftime("%Y-%m-%d")

        resp = retrying_get(
            "https://finnhub.io/api/v1/calendar/economic",
            max_attempts=2,
            params={"from": from_dt, "to": to_dt, "token": FINNHUB_KEY},
            timeout=10,
        )
        events = resp.json().get("economicCalendar", [])

        keywords = ["india", "rbi", "federal", "fed", "cpi", "gdp", "inflation",
                    "interest rate", "fomc", "ecb", "china"]
        relevant = []
        for e in events[:20]:
            event_name = (e.get("event", "") + " " + e.get("country", "")).lower()
            if any(k in event_name for k in keywords):
                relevant.append({
                    "date": e.get("time", ""),
                    "country": e.get("country", ""),
                    "event": e.get("event", ""),
                    "impact": e.get("impact", ""),
                })

        print(f"  [ok] Economic calendar: {len(relevant)} relevant events")
        return relevant
    except Exception as e:
        print(f"  [error] Finnhub economic calendar failed: {e}")
        return []


def fetch_all_market_data() -> dict:
    """Main entry — fetches all market data and returns combined dict."""
    print("\nFetching market data...")

    # Main indices (Nifty, Sensex, VIX, USD/INR)
    indices = fetch_yfinance_data(MAIN_INDICES)

    # Sector performance
    print("  Fetching sector indices...")
    sectors = fetch_yfinance_data(SECTOR_INDICES)

    # Sort sectors by change_pct
    sector_list = sorted(sectors.values(), key=lambda x: x.get("change_pct", 0), reverse=True)
    top_sectors = sector_list[:3] if len(sector_list) >= 3 else sector_list
    bottom_sectors = sector_list[-3:] if len(sector_list) >= 3 else []
    bottom_sectors = list(reversed(bottom_sectors))

    # US market data from Polygon.io
    print("  Fetching US market data (Polygon.io)...")
    us_markets = fetch_us_market_polygon()

    # Gift Nifty pre-market signal
    gift_nifty = fetch_gift_nifty()

    # FII/DII data
    fii_dii = fetch_fii_dii_data()

    # Economic calendar
    economic_events = fetch_finnhub_economic_calendar()

    payload = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "indices": indices,
        "us_markets": us_markets,
        "gift_nifty": gift_nifty,
        "fii_dii": fii_dii,
        "sector_performance": {
            "all": [s for s in sectors.values()],
            "top_gainers": top_sectors,
            "top_losers": bottom_sectors,
        },
        "economic_calendar": economic_events,
    }
    return payload


def save(data: dict) -> Path:
    TMP_DIR.mkdir(exist_ok=True)
    out = TMP_DIR / "market_data.json"
    out.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  Saved → {out}")
    return out


if __name__ == "__main__":
    data = fetch_all_market_data()
    save(data)
    print("\nDone.")
