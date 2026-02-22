"""
config/watchlist.py
Central watchlist of high-signal news topics for the daily market brief.

This drives two things:
  1. fetch_news.py  — search queries to pull relevant articles
  2. generate_brief.py — priority context so Claude elevates these topics when filtering
"""

# ── Grouped search queries for NewsAPI "everything" endpoint ──────────────────
# Each entry becomes one API call. Grouped to stay within the free-tier limit
# (100 req/day; pipeline runs once → these add 6 calls on top of the 4 base calls).

SEARCH_QUERIES = [
    "RBI monetary policy repo rate CPI WPI inflation India",
    "India GDP IIP industrial production GST budget current account",
    "US Fed FOMC rate decision nonfarm payrolls CPI jobs report",
    "crude oil brent DXY dollar index China PMI VIX global",
    "India pharma FDA IT sector banking NPA credit auto sales monsoon",
    "India Pakistan geopolitical trade tariffs US China elections",
]


# ── Structured watchlist (used to build the Claude priority prompt) ───────────

WATCHLIST = {
    "India Macro": [
        "RBI Monetary Policy (repo rate, stance)",
        "CPI Inflation (monthly)",
        "WPI Inflation (monthly)",
        "GDP Growth (quarterly)",
        "IIP – Industrial Production (monthly)",
        "GST Collections (monthly)",
        "Union Budget (annual, February)",
        "FII/DII Flow Data (daily/monthly)",
        "Current Account Deficit",
        "INR/USD Exchange Rate",
        "Monsoon Progress (June–September)",
    ],
    "Corporate": [
        "Quarterly Earnings Season (4 times/year)",
        "Credit Growth Data from RBI",
    ],
    "Global": [
        "US Fed Rate Decision & FOMC Minutes",
        "US CPI & Jobs Report (Non-Farm Payrolls)",
        "Crude Oil Prices (Brent)",
        "US Dollar Index (DXY)",
        "China PMI & Economic Data",
        "Global PMI Data",
        "CBOE VIX (fear index)",
    ],
    "Geopolitical / One-off": [
        "India-Pakistan / border tensions",
        "Global trade tariffs & sanctions",
        "US-China trade developments",
        "Elections (India general + state elections)",
    ],
    "Sector-specific": [
        "IT: US tech spending, visa policies",
        "Banking: RBI credit policy, NPA data",
        "Pharma: US FDA approvals/warnings",
        "Auto: Monthly sales numbers",
        "Real Estate: Home sales, RBI rate signals",
    ],
}
