"""
Backfill: seeds history.json with daily SOL data from 2021-01-01 to today.

Data sources (both free, no auth, no geo-restrictions):
  TVL:        api.llama.fi/v2/historicalChainTvl/Solana   (DefiLlama)
  Price+Mcap: api.coinpaprika.com/v1/coins/sol-solana/... (Coinpaprika)

Signal: TVL / Market Cap
  HIGH ratio = strong on-chain usage vs market valuation = undervalued = buy signal
  LOW ratio  = market pricing in future growth not yet on-chain = overvalued = sell signal
"""

import os
import json
import time
import datetime
import urllib.request

TVL_MCAP_THRESHOLD = float(os.environ.get("TVL_MCAP_THRESHOLD", "0.13"))
HISTORY_FILE = "history.json"

DEFILLAMA_TVL_URL = "https://api.llama.fi/v2/historicalChainTvl/Solana"
# Yahoo Finance chart — public, no auth, no geo-restriction, full history in one call
YAHOO_URL = (
    "https://query1.finance.yahoo.com/v8/finance/chart/SOL-USD"
    "?period1=1577836800&period2=9999999999&interval=1d"
)
YAHOO_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json,text/plain,*/*",
}


def fetch_json(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "sol-backfill/2.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_sol_prices():
    """Fetch full daily SOL-USD price history from Yahoo Finance."""
    print("  Fetching from Yahoo Finance (one call, full history)...")
    data = fetch_json(YAHOO_URL, headers=YAHOO_HEADERS)
    result = data["chart"]["result"][0]
    timestamps = result["timestamp"]
    closes     = result["indicators"]["quote"][0]["close"]

    price_by_date = {}
    for ts, close in zip(timestamps, closes):
        if close is None:
            continue
        d = datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).date().isoformat()
        price_by_date[d] = float(close)

    return price_by_date


def main():
    print("Fetching Solana TVL from DefiLlama...")
    tvl_series = fetch_json(DEFILLAMA_TVL_URL)
    tvl_by_date = {}
    for point in tvl_series:
        d = datetime.datetime.fromtimestamp(
            point["date"], datetime.timezone.utc
        ).date().isoformat()
        tvl_by_date[d] = float(point["tvl"])
    print("  {} days of TVL ({} to {})".format(
        len(tvl_by_date), min(tvl_by_date), max(tvl_by_date)
    ))

    print("Fetching SOL price history from Yahoo Finance...")
    price_by_date = fetch_sol_prices()
    print("  {} days of price data ({} to {})".format(
        len(price_by_date), min(price_by_date), max(price_by_date)
    ))

    history = []
    skipped = 0
    for date_str in sorted(set(tvl_by_date) & set(price_by_date)):
        tvl   = tvl_by_date[date_str]
        price = price_by_date[date_str]
        if price <= 0:
            skipped += 1
            continue
        # Ratio: TVL (in billions USD) / SOL price — a clean 0.0x–0.1x range
        # High = strong on-chain usage per dollar of SOL = undervalued signal
        ratio = (tvl / 1e9) / price
        history.append({
            "date":                 date_str,
            "price":                round(price, 4),
            "tvl":                  round(tvl, 2),
            "market_cap":           None,
            "ratio":                round(ratio, 6),
            "threshold":            TVL_MCAP_THRESHOLD,
            "price_change_pct_24h": None,
        })

    print("Built {} records ({} skipped)".format(len(history), skipped))
    if history:
        print("Date range: {} to {}".format(history[0]["date"], history[-1]["date"]))

    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2)
    print("Written to " + HISTORY_FILE)


if __name__ == "__main__":
    main()
