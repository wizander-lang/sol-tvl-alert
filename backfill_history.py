"""
Backfill: seeds history.json with daily SOL data from 2021-01-01 to today.

CoinGecko free tier returns daily granularity for ranges ≤ 90 days, so we
chunk the request into 80-day windows. DefiLlama returns the full TVL series
in a single call. The two are joined on ISO date string.
"""

import os
import json
import time
import datetime
import urllib.request

TVL_MCAP_THRESHOLD = float(os.environ.get("TVL_MCAP_THRESHOLD", "0.13"))
START_DATE = datetime.date(2021, 1, 1)
CHUNK_DAYS = 80
HISTORY_FILE = "history.json"

DEFILLAMA_URL = "https://api.llama.fi/v2/historicalChainTvl/Solana"
COINGECKO_RANGE_URL = (
    "https://api.coingecko.com/api/v3/coins/solana/market_chart/range"
    "?vs_currency=usd&from={f}&to={t}"
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

    # Chunk CoinGecko requests into 80-day windows to guarantee daily resolution
    start_dt = datetime.datetime.combine(
        START_DATE, datetime.time.min, tzinfo=datetime.timezone.utc
    )
    end_dt = datetime.datetime.now(datetime.timezone.utc)
    chunk = datetime.timedelta(days=CHUNK_DAYS)

    all_prices = {}
    all_mcaps = {}
    current = start_dt
    chunk_n = 0
    total = int((end_dt - start_dt).days / CHUNK_DAYS) + 1

    while current < end_dt:
        chunk_end = min(current + chunk, end_dt)
        chunk_n += 1
        print("  CoinGecko chunk {}/{}: {} → {}".format(
            chunk_n, total, current.date(), chunk_end.date()
        ))
        url = COINGECKO_RANGE_URL.format(
            f=int(current.timestamp()), t=int(chunk_end.timestamp())
        )
        data = fetch_json(url)
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
        current = chunk_end
        if current < end_dt:
            time.sleep(2)

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
