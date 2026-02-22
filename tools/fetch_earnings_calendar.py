"""
fetch_earnings_calendar.py
Fetches upcoming corporate earnings announcements relevant to Indian investors.

Sources:
  - Finnhub /calendar/earnings — US tech/finance companies that move Indian IT/banking
  - Covers: FAANG+, major US banks, oil majors — next 7 days

Why this matters for Indian investors:
  - When Apple, Microsoft, Google or Nvidia beats/misses earnings, Indian IT
    stocks (TCS, Infosys, Wipro, HCL Tech) often move in sympathy
  - US bank earnings affect global risk sentiment → FII flows into India
  - Oil company earnings signal crude oil demand → affects India macro

Saves to .tmp/earnings_calendar.json.

Usage:
    python tools/fetch_earnings_calendar.py
"""

import os
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils import retrying_get

load_dotenv()

FINNHUB_KEY = os.getenv("FINNHUB_API_KEY")
TMP_DIR = Path(".tmp")

# US companies that materially affect Indian market sentiment
# Grouped by why they matter for India
RELEVANT_TICKERS = {
    # Big tech — move Indian IT stocks (TCS, Infosys, Wipro, HCL, Tech M)
    "AAPL": "Apple (US tech bellwether — affects IT sector sentiment)",
    "MSFT": "Microsoft (India IT's largest client vertical — cloud/enterprise)",
    "GOOGL": "Alphabet/Google (ad spend, cloud — affects IT sector)",
    "AMZN": "Amazon (AWS cloud — affects Infosys, Wipro cloud deals)",
    "META": "Meta (digital ad spend — affects IT outsourcing pipeline)",
    "NVDA": "Nvidia (AI/semiconductor — drives IT sector AI narrative)",
    # Major US banks — affect global risk appetite → FII flows
    "JPM": "JPMorgan (global bank health — affects FII risk appetite)",
    "GS": "Goldman Sachs (investment bank — indicator of global deal flow)",
    "BAC": "Bank of America (US consumer health)",
    # Oil majors — crude oil price signal → India macro
    "XOM": "ExxonMobil (oil demand/supply signal — crude affects India)",
    "CVX": "Chevron (oil sector health — India imports 85% of crude)",
}


def fetch_earnings_calendar() -> dict:
    """
    Fetch upcoming earnings for companies relevant to Indian investors.
    Looks ahead 7 days from today.
    """
    print("\nFetching earnings calendar...")

    if not FINNHUB_KEY:
        print("  [warn] FINNHUB_API_KEY not set — skipping earnings calendar")
        return _empty_payload()

    today = datetime.now(timezone.utc)
    from_dt = today.strftime("%Y-%m-%d")
    to_dt = (today + timedelta(days=7)).strftime("%Y-%m-%d")

    try:
        resp = retrying_get(
            "https://finnhub.io/api/v1/calendar/earnings",
            max_attempts=2,
            params={"from": from_dt, "to": to_dt, "token": FINNHUB_KEY},
            timeout=10,
        )
        all_events = resp.json().get("earningsCalendar", [])
    except Exception as e:
        print(f"  [error] Finnhub earnings calendar failed: {e}")
        return _empty_payload()

    # Filter for only relevant tickers
    relevant_events = []
    seen_symbols = set()
    for event in all_events:
        symbol = event.get("symbol", "").upper()
        if symbol in RELEVANT_TICKERS and symbol not in seen_symbols:
            seen_symbols.add(symbol)
            # Parse date for display
            event_date = event.get("date", "")
            try:
                dt = datetime.strptime(event_date, "%Y-%m-%d")
                days_away = (dt.date() - today.date()).days
                if days_away == 0:
                    when = "TODAY"
                elif days_away == 1:
                    when = "Tomorrow"
                else:
                    when = dt.strftime("%a %d %b")
            except Exception:
                when = event_date

            relevant_events.append({
                "symbol": symbol,
                "company": RELEVANT_TICKERS[symbol].split(" (")[0],
                "context": RELEVANT_TICKERS[symbol].split("(")[1].rstrip(")") if "(" in RELEVANT_TICKERS[symbol] else "",
                "date": event_date,
                "when": when,
                "eps_estimate": event.get("epsEstimate"),
                "revenue_estimate": event.get("revenueEstimate"),
                "hour": event.get("hour", ""),  # "bmo" = before market open, "amc" = after market close
            })

    # Sort by date
    relevant_events.sort(key=lambda x: x.get("date", ""))

    if relevant_events:
        print(f"  [ok] Earnings calendar: {len(relevant_events)} relevant events in next 7 days")
        for e in relevant_events:
            print(f"       {e['when']:12s} {e['symbol']:6s} {e['company']}")
    else:
        print("  [ok] Earnings calendar: no relevant earnings in next 7 days")

    return {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "window": f"{from_dt} to {to_dt}",
        "event_count": len(relevant_events),
        "events": relevant_events,
        "note": (
            "US earnings can move Indian markets: big tech beats lift IT stocks, "
            "big tech misses create selling pressure on TCS/Infosys/Wipro. "
            "US bank earnings affect global risk sentiment and FII flows into India."
        ),
    }


def _empty_payload() -> dict:
    return {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "event_count": 0,
        "events": [],
    }


def save(data: dict) -> Path:
    TMP_DIR.mkdir(exist_ok=True)
    out = TMP_DIR / "earnings_calendar.json"
    out.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  Saved → {out}")
    return out


if __name__ == "__main__":
    data = fetch_earnings_calendar()
    save(data)
    print("\nDone.")
