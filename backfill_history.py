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
# Coinpaprika returns up to 366 records per call; chunk by year
COINPAPRIKA_OHLCV_URL = (
    "https://api.coinpaprika.com/v1/coins/sol-solana/ohlcv/historical"
    "?start={start}&end={end}&limit=366"
)


def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "sol-backfill/2.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_coinpaprika_ohlcv():
    """Fetch daily SOL OHLCV + market cap from Coinpaprika in yearly chunks."""
    price_by_date = {}
    mcap_by_date  = {}

    start = datetime.date(2021, 1, 1)
    today = datetime.date.today()

    while start <= today:
        end = min(datetime.date(start.year, 12, 31), today)
        url = COINPAPRIKA_OHLCV_URL.format(start=start.isoformat(), end=end.isoformat())
        print("  Coinpaprika: {} → {}".format(start, end))
        rows = fetch_json(url)
        for row in rows:
            d = row["time_open"][:10]          # "2021-01-01T..." → "2021-01-01"
            if row.get("close") and row["close"] > 0:
                price_by_date[d] = float(row["close"])
            if row.get("market_cap") and row["market_cap"] > 0:
                mcap_by_date[d]  = float(row["market_cap"])
        start = datetime.date(start.year + 1, 1, 1)
        time.sleep(1)   # polite rate limiting

    return price_by_date, mcap_by_date


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

    print("Fetching SOL price + market cap from Coinpaprika...")
    price_by_date, mcap_by_date = fetch_coinpaprika_ohlcv()
    print("  {} days of price, {} days of mcap".format(
        len(price_by_date), len(mcap_by_date)
    ))

    history = []
    skipped = 0
    all_dates = sorted(set(tvl_by_date) & set(price_by_date) & set(mcap_by_date))
    for date_str in all_dates:
        tvl   = tvl_by_date[date_str]
        price = price_by_date[date_str]
        mcap  = mcap_by_date[date_str]
        if mcap <= 0:
            skipped += 1
            continue
        history.append({
            "date":                 date_str,
            "price":                round(price, 4),
            "tvl":                  round(tvl, 2),
            "market_cap":           round(mcap, 2),
            "ratio":                round(tvl / mcap, 6),
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
