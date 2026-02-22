"""
fetch_ipo_data.py
Scrapes IPO data using a two-source strategy:
  - Primary: Chittorgarh.com (GMP, subscription, open IPOs)
  - Fallback: ipowatch.in (if Chittorgarh fails)

Data collected:
  - Active/upcoming IPOs (name, dates, issue price, IPO type, size)
  - GMP (Grey Market Premium) — speculative pre-listing price signal
  - Subscription status (oversubscription metrics)

Saves to .tmp/ipo_data.json.

Usage:
    python tools/fetch_ipo_data.py

Note: Web scraping is brittle. If a site changes their HTML, update the
selectors below. The script is built defensively — if all scraping fails,
it returns an empty list so the rest of the pipeline continues.
"""

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils import retrying_get

TMP_DIR = Path(".tmp")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


# ─── Primary Source: Chittorgarh.com ───────────────────────────────────────


def scrape_chittorgarh_gmp() -> list[dict]:
    """
    Scrapes Chittorgarh.com GMP page for Grey Market Premium data.
    Returns: list of dicts with name, gmp, issue_price, listing_gain_pct.
    """
    url = "https://www.chittorgarh.com/ipo/ipo_gmp.asp"
    try:
        resp = retrying_get(url, max_attempts=2, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(resp.text, "lxml")

        ipos = []
        tables = soup.find_all("table")
        if not tables:
            print("  [warn] No tables on Chittorgarh GMP page")
            return []

        for table in tables:
            rows = table.find_all("tr")
            if len(rows) < 2:
                continue
            # Detect header row to understand column order
            header_cells = [th.get_text(strip=True).lower() for th in rows[0].find_all(["th", "td"])]
            if not any(k in " ".join(header_cells) for k in ["ipo", "gmp", "price"]):
                continue

            for row in rows[1:]:
                cells = row.find_all("td")
                if len(cells) < 3:
                    continue
                cell_texts = [c.get_text(strip=True) for c in cells]
                name = cell_texts[0]
                if not name or name.lower() in ("ipo name", "name", ""):
                    continue
                ipos.append({
                    "name": name,
                    "issue_price": cell_texts[1] if len(cell_texts) > 1 else "N/A",
                    "gmp": cell_texts[2] if len(cell_texts) > 2 else "N/A",
                    "listing_gain_pct": cell_texts[3] if len(cell_texts) > 3 else "N/A",
                    "dates": cell_texts[4] if len(cell_texts) > 4 else "",
                })

        print(f"  [ok] Chittorgarh GMP: {len(ipos)} IPOs")
        return ipos

    except Exception as e:
        print(f"  [error] Chittorgarh GMP scrape failed: {e}")
        return []


def scrape_chittorgarh_subscription() -> list[dict]:
    """
    Scrapes Chittorgarh.com subscription page for oversubscription data.
    Returns: list of dicts with name, subscription (e.g. '3.5x'), close_date.
    """
    url = "https://www.chittorgarh.com/report/ipo-subscription-status/93/"
    try:
        resp = retrying_get(url, max_attempts=2, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(resp.text, "lxml")

        ipos = []
        tables = soup.find_all("table")
        if not tables:
            print("  [warn] No tables on Chittorgarh subscription page")
            return []

        for table in tables:
            rows = table.find_all("tr")
            if len(rows) < 2:
                continue
            header_cells = [th.get_text(strip=True).lower() for th in rows[0].find_all(["th", "td"])]
            if not any(k in " ".join(header_cells) for k in ["ipo", "subscription", "subscr"]):
                continue

            for row in rows[1:]:
                cells = row.find_all("td")
                if len(cells) < 2:
                    continue
                cell_texts = [c.get_text(strip=True) for c in cells]
                name = cell_texts[0]
                if not name or name.lower() in ("ipo", "name", ""):
                    continue
                # Subscription figure is usually the last or a specific column
                subscription = ""
                for ct in reversed(cell_texts):
                    if ct and ("x" in ct.lower() or "times" in ct.lower() or ct.replace(".", "").replace(",", "").isdigit()):
                        subscription = ct
                        break
                ipos.append({
                    "name": name,
                    "subscription": subscription or "N/A",
                })

        print(f"  [ok] Chittorgarh subscription: {len(ipos)} IPOs")
        return ipos

    except Exception as e:
        print(f"  [error] Chittorgarh subscription scrape failed: {e}")
        return []


def fetch_from_chittorgarh() -> list[dict]:
    """Main Chittorgarh fetch — GMP + subscription merged by name."""
    gmp_data = scrape_chittorgarh_gmp()
    time.sleep(1)
    sub_data = scrape_chittorgarh_subscription()

    if not gmp_data and not sub_data:
        return []

    # Build subscription lookup
    sub_by_name = {}
    for s in sub_data:
        key = s["name"].lower()[:12]
        sub_by_name[key] = s.get("subscription", "N/A")

    merged = []
    seen = set()
    for g in gmp_data:
        key = g["name"].lower()[:12]
        if key in seen:
            continue
        seen.add(key)
        merged.append({
            "name": g["name"],
            "issue_price": g.get("issue_price", "N/A"),
            "gmp": g.get("gmp", "N/A"),
            "listing_gain_pct": g.get("listing_gain_pct", "N/A"),
            "dates": g.get("dates", ""),
            "subscription": sub_by_name.get(key, "N/A"),
        })

    # Add subscription-only entries not in GMP list
    for s in sub_data:
        key = s["name"].lower()[:12]
        if key not in seen:
            seen.add(key)
            merged.append({
                "name": s["name"],
                "issue_price": "N/A",
                "gmp": "N/A",
                "subscription": s.get("subscription", "N/A"),
            })

    return merged


# ─── Fallback Source: ipowatch.in ──────────────────────────────────────────


def scrape_ipowatch_listings() -> list[dict]:
    """Scrapes the ipowatch.in homepage for upcoming/active IPO listings."""
    url = "https://ipowatch.in/"
    try:
        resp = retrying_get(url, max_attempts=2, headers={**HEADERS, "Referer": "https://ipowatch.in/"}, timeout=15)
        soup = BeautifulSoup(resp.text, "lxml")

        ipos = []
        tables = soup.find_all("table")
        if not tables:
            print("  [warn] No tables found on ipowatch.in homepage")
            return []

        table = tables[0]
        rows = table.find_all("tr")
        for row in rows[1:]:
            cells = row.find_all("td")
            if len(cells) < 4:
                continue
            cell_texts = [c.get_text(strip=True) for c in cells]
            ipos.append({
                "name": cell_texts[0],
                "dates": cell_texts[1] if len(cell_texts) > 1 else "",
                "type": cell_texts[2] if len(cell_texts) > 2 else "",
                "size": cell_texts[3] if len(cell_texts) > 3 else "",
                "price_band": cell_texts[4] if len(cell_texts) > 4 else "",
            })

        print(f"  [ok] ipowatch.in listings: {len(ipos)} IPOs")
        return ipos

    except Exception as e:
        print(f"  [error] ipowatch.in listing scrape failed: {e}")
        return []


def scrape_ipowatch_gmp() -> list[dict]:
    """Scrapes the ipowatch.in GMP page for Grey Market Premium data."""
    url = "https://ipowatch.in/ipo-grey-market-premium-latest-ipo-gmp/"
    try:
        resp = retrying_get(url, max_attempts=2, headers={**HEADERS, "Referer": "https://ipowatch.in/"}, timeout=15)
        soup = BeautifulSoup(resp.text, "lxml")

        ipos = []
        tables = soup.find_all("table")
        if not tables:
            print("  [warn] No tables on ipowatch.in GMP page")
            return []

        for table in tables:
            rows = table.find_all("tr")
            for row in rows[1:]:
                cells = row.find_all("td")
                if len(cells) < 3:
                    continue
                cell_texts = [c.get_text(strip=True) for c in cells]
                name = cell_texts[0]
                if not name:
                    continue
                ipos.append({
                    "name": name,
                    "gmp": cell_texts[1] if len(cell_texts) > 1 else "N/A",
                    "issue_price": cell_texts[2] if len(cell_texts) > 2 else "N/A",
                    "listing_gain_pct": cell_texts[3] if len(cell_texts) > 3 else "N/A",
                    "dates": cell_texts[4] if len(cell_texts) > 4 else "",
                })

        print(f"  [ok] ipowatch.in GMP page: {len(ipos)} IPOs")
        return ipos

    except Exception as e:
        print(f"  [error] ipowatch.in GMP scrape failed: {e}")
        return []


def fetch_from_ipowatch() -> list[dict]:
    """Fallback ipowatch.in fetch — listings + GMP merged by name."""
    listings = scrape_ipowatch_listings()
    time.sleep(1)
    gmp_data = scrape_ipowatch_gmp()

    listing_by_name = {}
    for l in listings:
        key = l["name"].lower()[:12]
        listing_by_name[key] = l

    merged = []
    seen = set()
    for g in gmp_data:
        key = g["name"].lower()[:12]
        if key in seen:
            continue
        seen.add(key)
        ipo = {**g}
        if key in listing_by_name:
            lst = listing_by_name[key]
            ipo.setdefault("dates", lst.get("dates", ""))
            ipo["type"] = lst.get("type", "")
            ipo["size"] = lst.get("size", "")
            if not ipo.get("issue_price") or ipo["issue_price"] == "N/A":
                ipo["issue_price"] = lst.get("price_band", "N/A")
        merged.append(ipo)

    for l in listings:
        key = l["name"].lower()[:12]
        if key not in seen:
            seen.add(key)
            merged.append({
                "name": l["name"],
                "gmp": "N/A",
                "issue_price": l.get("price_band", "N/A"),
                "dates": l.get("dates", ""),
                "type": l.get("type", ""),
                "size": l.get("size", ""),
            })

    return merged


# ─── Main entry ─────────────────────────────────────────────────────────────


def fetch_all_ipo_data() -> dict:
    """
    Main entry — tries Chittorgarh first, falls back to ipowatch.in.
    Returns combined dict ready for .tmp/ipo_data.json.
    """
    print("\nFetching IPO data...")

    # Try primary source: Chittorgarh.com
    print("  Trying Chittorgarh.com (primary)...")
    combined = fetch_from_chittorgarh()
    source = "chittorgarh.com"

    # Fallback: ipowatch.in
    if not combined:
        print("  Chittorgarh returned no data — trying ipowatch.in (fallback)...")
        combined = fetch_from_ipowatch()
        source = "ipowatch.in (fallback)"

    if not combined:
        print("  [warn] No IPO data from any source — continuing with empty list")

    payload = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "ipo_count": len(combined),
        "ipos": combined,
        "note": "GMP = Grey Market Premium (unofficial, speculative). "
                "Positive GMP means shares trade above issue price in grey market.",
    }
    return payload


def save(data: dict) -> Path:
    TMP_DIR.mkdir(exist_ok=True)
    out = TMP_DIR / "ipo_data.json"
    out.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  Saved → {out}")
    return out


if __name__ == "__main__":
    data = fetch_all_ipo_data()
    save(data)
    print("\nDone.")
