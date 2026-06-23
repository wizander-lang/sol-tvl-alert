"""
Backfill: seeds history.json with daily SOL data from 2021-01-01 to today.
Uses DefiLlama exclusively — no API key required, no rate limits.

  TVL:   api.llama.fi/v2/historicalChainTvl/Solana  (daily, back to 2021)
  Price: coins.llama.fi/chart/coingecko:solana       (daily, back to 2020)

Signal: TVL / Price  (on-chain usage per dollar of SOL price)
A higher ratio = more usage relative to price = historically constructive.
"""

import os
import json
import datetime
import urllib.request

TVL_PRICE_THRESHOLD = float(os.environ.get("TVL_MCAP_THRESHOLD", "0.13"))
HISTORY_FILE = "history.json"

DEFILLAMA_TVL_URL = "https://api.llama.fi/v2/historicalChainTvl/Solana"
# Kraken public API — no auth, no geo-restrictions, 720 candles per call
# interval=1440 = daily candles; since = Unix seconds
KRAKEN_OHLC_URL = (
    "https://api.kraken.com/0/public/OHLC"
    "?pair=SOLUSD&interval=1440&since={since}"
)


def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "sol-backfill/2.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_sol_prices():
    """Fetch daily SOL/USD close prices from Kraken in 720-day chunks."""
    price_by_date = {}
    # Kraken listed SOL in Sep 2021; starting from 2021-01-01 will auto-skip earlier
    since = int(datetime.datetime(2021, 1, 1, tzinfo=datetime.timezone.utc).timestamp())
    now   = int(datetime.datetime.now(datetime.timezone.utc).timestamp())

    while since < now:
        url  = KRAKEN_OHLC_URL.format(since=since)
        data = fetch_json(url)
        if data.get("error"):
            raise RuntimeError("Kraken error: " + str(data["error"]))
        result = data.get("result", {})
        # Kraken returns the pair under various key names; grab the first non-"last" key
        pair_key = next((k for k in result if k != "last"), None)
        if not pair_key:
            break
        rows = result[pair_key]
        if not rows:
            break
        for row in rows:
            ts    = int(row[0])
            close = float(row[4])
            d = datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).date().isoformat()
            price_by_date[d] = close
        # Kraken's "last" field is the timestamp to use for the next page
        last = int(result.get("last", 0))
        if last <= since:
            break
        since = last

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

    print("Fetching SOL daily price from Binance public API...")
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
        ratio = tvl / price
        history.append({
            "date":                 date_str,
            "price":                round(price, 4),
            "tvl":                  round(tvl, 2),
            "market_cap":           None,
            "ratio":                round(ratio, 2),
            "threshold":            TVL_PRICE_THRESHOLD,
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
