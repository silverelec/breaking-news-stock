"""
fetch_news.py
Fetches financial news from NewsAPI (global + India), Finnhub market news,
and RSS feeds from major Indian/global financial publications.
Saves combined results to .tmp/news.json.

Usage:
    python tools/fetch_news.py
    python tools/fetch_news.py --hours 48   # look back further
"""

import os
import sys
import json
import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from dotenv import load_dotenv

# Allow importing from project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config.watchlist import SEARCH_QUERIES
from tools.utils import retrying_get

load_dotenv()

NEWSAPI_KEY = os.getenv("NEWSAPI_KEY")
FINNHUB_KEY = os.getenv("FINNHUB_API_KEY")
TMP_DIR = Path(".tmp")

# RSS feeds: (source_name, url)
# Used as free supplement/fallback when NewsAPI quota runs out
RSS_FEEDS = [
    ("Economic Times Markets", "https://economictimes.indiatimes.com/markets/rss.cms"),
    ("Business Standard", "https://www.business-standard.com/rss/markets-106.rss"),
    ("Reuters India", "https://feeds.reuters.com/reuters/INbusinessNews"),
    ("Moneycontrol", "https://www.moneycontrol.com/rss/MCtopnews.xml"),
    ("Mint Markets", "https://www.livemint.com/rss/markets"),
]


def fetch_newsapi_global(hours_back: int = 24) -> list[dict]:
    """Fetch top global business/financial headlines from NewsAPI."""
    if not NEWSAPI_KEY:
        print("  [warn] NEWSAPI_KEY not set — skipping NewsAPI global fetch")
        return []

    url = "https://newsapi.org/v2/top-headlines"
    params = {
        "category": "business",
        "language": "en",
        "pageSize": 20,
        "apiKey": NEWSAPI_KEY,
    }
    try:
        resp = retrying_get(url, params=params, timeout=10)
        data = resp.json()
        # Detect quota exhaustion
        if data.get("code") == "rateLimited":
            print("  [warn] NewsAPI quota exhausted — skipping global fetch")
            return []
        articles = data.get("articles", [])
        results = []
        for a in articles:
            if a.get("title") and "[Removed]" not in a.get("title", ""):
                results.append({
                    "source": "newsapi_global",
                    "title": a.get("title", ""),
                    "description": a.get("description", ""),
                    "url": a.get("url", ""),
                    "published_at": a.get("publishedAt", ""),
                    "source_name": a.get("source", {}).get("name", ""),
                })
        print(f"  [ok] NewsAPI global: {len(results)} articles")
        return results
    except Exception as e:
        print(f"  [error] NewsAPI global failed: {e}")
        return []


def fetch_newsapi_india(hours_back: int = 24) -> list[dict]:
    """Fetch top Indian business headlines from NewsAPI."""
    if not NEWSAPI_KEY:
        return []

    url = "https://newsapi.org/v2/top-headlines"
    params = {
        "category": "business",
        "country": "in",
        "pageSize": 20,
        "apiKey": NEWSAPI_KEY,
    }
    try:
        resp = retrying_get(url, params=params, timeout=10)
        data = resp.json()
        if data.get("code") == "rateLimited":
            print("  [warn] NewsAPI quota exhausted — skipping India fetch")
            return []
        articles = data.get("articles", [])
        results = []
        for a in articles:
            if a.get("title") and "[Removed]" not in a.get("title", ""):
                results.append({
                    "source": "newsapi_india",
                    "title": a.get("title", ""),
                    "description": a.get("description", ""),
                    "url": a.get("url", ""),
                    "published_at": a.get("publishedAt", ""),
                    "source_name": a.get("source", {}).get("name", ""),
                })
        print(f"  [ok] NewsAPI India: {len(results)} articles")
        return results
    except Exception as e:
        print(f"  [error] NewsAPI India failed: {e}")
        return []


def fetch_newsapi_everything(query: str, hours_back: int = 24) -> list[dict]:
    """Search NewsAPI 'everything' endpoint for specific finance topics."""
    if not NEWSAPI_KEY:
        return []

    from_dt = (datetime.now(timezone.utc) - timedelta(hours=hours_back)).strftime("%Y-%m-%dT%H:%M:%SZ")
    url = "https://newsapi.org/v2/everything"
    params = {
        "q": query,
        "language": "en",
        "sortBy": "publishedAt",
        "from": from_dt,
        "pageSize": 10,
        "apiKey": NEWSAPI_KEY,
    }
    try:
        resp = retrying_get(url, params=params, timeout=10)
        data = resp.json()
        if data.get("code") == "rateLimited":
            print(f"  [warn] NewsAPI quota exhausted — skipping search '{query}'")
            return []
        articles = data.get("articles", [])
        results = []
        for a in articles:
            if a.get("title") and "[Removed]" not in a.get("title", ""):
                results.append({
                    "source": f"newsapi_search:{query}",
                    "title": a.get("title", ""),
                    "description": a.get("description", ""),
                    "url": a.get("url", ""),
                    "published_at": a.get("publishedAt", ""),
                    "source_name": a.get("source", {}).get("name", ""),
                })
        print(f"  [ok] NewsAPI search '{query}': {len(results)} articles")
        return results
    except Exception as e:
        print(f"  [error] NewsAPI search '{query}' failed: {e}")
        return []


def fetch_finnhub_news() -> list[dict]:
    """Fetch market news from Finnhub (general market category)."""
    if not FINNHUB_KEY:
        print("  [warn] FINNHUB_API_KEY not set — skipping Finnhub fetch")
        return []

    url = "https://finnhub.io/api/v1/news"
    params = {
        "category": "general",
        "token": FINNHUB_KEY,
    }
    try:
        resp = retrying_get(url, params=params, timeout=10)
        articles = resp.json()
        results = []
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        for a in articles[:30]:
            pub_ts = a.get("datetime", 0)
            pub_dt = datetime.fromtimestamp(pub_ts, tz=timezone.utc)
            if pub_dt >= cutoff and a.get("headline"):
                results.append({
                    "source": "finnhub",
                    "title": a.get("headline", ""),
                    "description": a.get("summary", ""),
                    "url": a.get("url", ""),
                    "published_at": pub_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "source_name": a.get("source", "Finnhub"),
                })
        print(f"  [ok] Finnhub: {len(results)} articles")
        return results
    except Exception as e:
        print(f"  [error] Finnhub failed: {e}")
        return []


def fetch_rss_feeds(hours_back: int = 24) -> list[dict]:
    """
    Fetch news from RSS feeds of major Indian/global financial publications.
    Free — no API key needed. Used as supplement and fallback when NewsAPI quota runs out.
    """
    try:
        import feedparser
    except ImportError:
        print("  [warn] feedparser not installed — skipping RSS feeds")
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_back)
    all_articles = []

    for source_name, feed_url in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            count = 0
            for entry in feed.entries[:15]:
                # Parse publication date — feedparser normalises to time.struct_time
                pub_dt = None
                if hasattr(entry, "published_parsed") and entry.published_parsed:
                    try:
                        import calendar
                        ts = calendar.timegm(entry.published_parsed)
                        pub_dt = datetime.fromtimestamp(ts, tz=timezone.utc)
                    except Exception:
                        pass

                # Include if within time window (or if date unavailable — include anyway)
                if pub_dt and pub_dt < cutoff:
                    continue

                title = getattr(entry, "title", "").strip()
                if not title:
                    continue

                description = getattr(entry, "summary", "") or getattr(entry, "description", "")
                # Strip basic HTML tags from description
                if description and "<" in description:
                    from bs4 import BeautifulSoup
                    description = BeautifulSoup(description, "lxml").get_text(separator=" ", strip=True)[:300]

                all_articles.append({
                    "source": "rss",
                    "title": title,
                    "description": description[:300] if description else "",
                    "url": getattr(entry, "link", ""),
                    "published_at": pub_dt.strftime("%Y-%m-%dT%H:%M:%SZ") if pub_dt else "",
                    "source_name": source_name,
                })
                count += 1

            print(f"  [ok] RSS {source_name}: {count} articles")
        except Exception as e:
            print(f"  [warn] RSS {source_name} failed: {e}")

    return all_articles


def fetch_gnews_india() -> list[dict]:
    """Fallback: fetch India business news from GNews."""
    url = "https://gnews.io/api/v4/top-headlines"
    params = {
        "topic": "business",
        "country": "in",
        "lang": "en",
        "max": 10,
    }
    gnews_key = os.getenv("GNEWS_API_KEY", "")
    if gnews_key:
        params["apikey"] = gnews_key

    try:
        resp = retrying_get(url, params=params, timeout=10)
        if resp.status_code == 403:
            print("  [warn] GNews requires API key — skipping")
            return []
        articles = resp.json().get("articles", [])
        results = []
        for a in articles:
            results.append({
                "source": "gnews_india",
                "title": a.get("title", ""),
                "description": a.get("description", ""),
                "url": a.get("url", ""),
                "published_at": a.get("publishedAt", ""),
                "source_name": a.get("source", {}).get("name", ""),
            })
        print(f"  [ok] GNews India: {len(results)} articles")
        return results
    except Exception as e:
        print(f"  [warn] GNews fallback failed: {e}")
        return []


def deduplicate(articles: list[dict]) -> list[dict]:
    """Remove duplicate articles by title similarity (exact match on first 60 chars)."""
    seen = set()
    unique = []
    for a in articles:
        key = a["title"][:60].lower().strip()
        if key not in seen:
            seen.add(key)
            unique.append(a)
    return unique


def fetch_all_news(hours_back: int = 24) -> dict:
    """Main entry point — fetches all news sources and returns combined JSON."""
    print(f"\nFetching news (last {hours_back}h)...")

    all_articles = []
    all_articles.extend(fetch_finnhub_news())
    all_articles.extend(fetch_newsapi_india(hours_back))
    all_articles.extend(fetch_newsapi_global(hours_back))
    for query in SEARCH_QUERIES:
        all_articles.extend(fetch_newsapi_everything(query, hours_back))
    all_articles.extend(fetch_gnews_india())

    # RSS feeds: always included as a supplement (free, no quota)
    print("  Fetching RSS feeds...")
    all_articles.extend(fetch_rss_feeds(hours_back))

    unique_articles = deduplicate(all_articles)
    print(f"  Total unique articles: {len(unique_articles)}")

    payload = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "hours_back": hours_back,
        "article_count": len(unique_articles),
        "articles": unique_articles,
    }
    return payload


def save(data: dict) -> Path:
    TMP_DIR.mkdir(exist_ok=True)
    out = TMP_DIR / "news.json"
    out.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  Saved → {out}")
    return out


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch financial news")
    parser.add_argument("--hours", type=int, default=24, help="Hours to look back (default: 24)")
    args = parser.parse_args()

    data = fetch_all_news(hours_back=args.hours)
    save(data)
    print("\nDone.")
