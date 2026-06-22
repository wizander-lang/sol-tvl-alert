"""
Backfill: seeds history.json with daily SOL data from 2021-01-01 to today.

CoinGecko free tier returns daily granularity for ranges ≤ 90 days, so we
chunk the request into 80-day windows. DefiLlama returns the full TVL series
in a single call. The two are joined on ISO date string.
"""

import os
import json
import datetime
import urllib.request

TVL_MCAP_THRESHOLD = float(os.environ.get("TVL_MCAP_THRESHOLD", "0.13"))
HISTORY_FILE = "history.json"

DEFILLAMA_URL = "https://api.llama.fi/v2/historicalChainTvl/Solana"
# days=max returns full history in one call (free tier friendly).
# Granularity: daily for last ~365 days, weekly for older data — sufficient for trend analysis.
COINGECKO_URL = (
    "https://api.coingecko.com/api/v3/coins/solana/market_chart"
    "?vs_currency=usd&days=max"
)


def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "sol-backfill/2.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main():
    print("Fetching Solana TVL history from DefiLlama...")
    tvl_series = fetch_json(DEFILLAMA_URL)
    tvl_by_date = {}
    for point in tvl_series:
        d = datetime.datetime.fromtimestamp(
            point["date"], datetime.timezone.utc
        ).date().isoformat()
        tvl_by_date[d] = float(point["tvl"])
    print("  Got TVL for {} days ({} to {})".format(
        len(tvl_by_date),
        min(tvl_by_date),
        max(tvl_by_date),
    ))

    print("Fetching full SOL price/mcap history from CoinGecko (days=max)...")
    data = fetch_json(COINGECKO_URL)

    all_prices = {}
    all_mcaps = {}
    for ts_ms, price in data.get("prices", []):
        d = datetime.datetime.fromtimestamp(
            ts_ms / 1000, datetime.timezone.utc
        ).date().isoformat()
        all_prices[d] = float(price)
    for ts_ms, mcap in data.get("market_caps", []):
        d = datetime.datetime.fromtimestamp(
            ts_ms / 1000, datetime.timezone.utc
        ).date().isoformat()
        all_mcaps[d] = float(mcap)

    print("  Got price/mcap for {} days".format(len(all_prices)))

    history = []
    skipped = 0
    for date_str in sorted(set(all_prices) & set(all_mcaps)):
        tvl = tvl_by_date.get(date_str)
        mcap = all_mcaps[date_str]
        price = all_prices[date_str]
        if tvl is None or mcap <= 0:
            skipped += 1
            continue
        history.append({
            "date": date_str,
            "price": round(price, 4),
            "tvl": round(tvl, 2),
            "market_cap": round(mcap, 2),
            "ratio": round(tvl / mcap, 6),
            "threshold": TVL_MCAP_THRESHOLD,
            "price_change_pct_24h": None,
        })

    print("Built {} records ({} skipped — no TVL match)".format(len(history), skipped))
    if history:
        print("Date range: {} to {}".format(history[0]["date"], history[-1]["date"]))

    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2)
    print("Written to " + HISTORY_FILE)


if __name__ == "__main__":
    main()
