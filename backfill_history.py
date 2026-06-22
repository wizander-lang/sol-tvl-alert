"""
One-time backfill: seeds history.json with the past N days of real
SOL price + TVL data, so the dashboard shows a trend immediately
instead of starting from a single point.

This is meant to be run ONCE via GitHub Actions (see backfill.yml),
then the workflow file and this script can be deleted - the daily
sol_alert.py script takes over from there.
"""

import os
import json
import datetime
import urllib.request

BACKFILL_DAYS = int(os.environ.get("BACKFILL_DAYS", "90"))
TVL_MCAP_THRESHOLD = float(os.environ.get("TVL_MCAP_THRESHOLD", "0.13"))

COINGECKO_CHART_URL = (
    "https://api.coingecko.com/api/v3/coins/solana/market_chart"
    "?vs_currency=usd&days=" + str(BACKFILL_DAYS) + "&interval=daily"
)
DEFILLAMA_TVL_URL = "https://api.llama.fi/v2/historicalChainTvl/Solana"

HISTORY_FILE = "history.json"


def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "sol-backfill-script/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main():
    print("Fetching " + str(BACKFILL_DAYS) + " days of SOL price/market cap from CoinGecko...")
    chart = fetch_json(COINGECKO_CHART_URL)
    prices = chart["prices"]          # [[ts_ms, price], ...]
    market_caps = chart["market_caps"]  # [[ts_ms, mcap], ...]

    print("Fetching historical Solana TVL from DefiLlama...")
    tvl_series = fetch_json(DEFILLAMA_TVL_URL)  # [{date: unix_seconds, tvl: float}, ...]

    # Build a lookup of date -> tvl, keyed by ISO date string
    tvl_by_date = {}
    for point in tvl_series:
        date_str = datetime.datetime.fromtimestamp(point["date"], datetime.timezone.utc).date().isoformat()
        tvl_by_date[date_str] = float(point["tvl"])

    history = []
    skipped = 0

    for (ts_ms, price), (_, mcap) in zip(prices, market_caps):
        date_str = datetime.datetime.fromtimestamp(ts_ms / 1000, datetime.timezone.utc).date().isoformat()
        tvl = tvl_by_date.get(date_str)
        if tvl is None or mcap <= 0:
            skipped += 1
            continue
        ratio = tvl / mcap
        history.append({
            "date": date_str,
            "price": round(price, 4),
            "tvl": round(tvl, 2),
            "market_cap": round(mcap, 2),
            "ratio": round(ratio, 6),
            "threshold": TVL_MCAP_THRESHOLD,
            "price_change_pct_24h": None,  # not available for historical backfill
        })

    history.sort(key=lambda r: r["date"])

    # de-duplicate by date, keeping the last entry for each day
    deduped = {}
    for record in history:
        deduped[record["date"]] = record
    history = sorted(deduped.values(), key=lambda r: r["date"])

    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2)

    print("Backfilled " + str(len(history)) + " days of history (" + str(skipped) + " skipped due to missing TVL match).")
    if history:
        print("Date range: " + history[0]["date"] + " to " + history[-1]["date"])


if __name__ == "__main__":
    main()
