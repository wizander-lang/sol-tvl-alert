"""
Backfill: seeds history.json with daily SOL data from 2021-01-01 to today.

Data sources (all free, no auth, no geo-restrictions):
  TVL:   api.llama.fi/v2/historicalChainTvl/Solana  (DefiLlama)
  Fees:  api.llama.fi/summary/fees/Solana            (DefiLlama)
  Price: CoinCap → DefiLlama coins chart → Binance.US  (tried in order)

Composite heat score = 40% MA deviation + 30% Revenue Yield + 30% TVL/Price
"""

import os
import json
import datetime
import urllib.request
import urllib.error

TVL_MCAP_THRESHOLD = float(os.environ.get("TVL_MCAP_THRESHOLD", "0.075"))
HISTORY_FILE = "history.json"

DEFILLAMA_TVL_URL  = "https://api.llama.fi/v2/historicalChainTvl/Solana"
DEFILLAMA_FEES_URL = "https://api.llama.fi/summary/fees/Solana?dataType=dailyFees"


def fetch_json(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "sol-backfill/2.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_fees_history():
    """Fetch daily Solana protocol fee history from DefiLlama."""
    print("Fetching Solana fee history from DefiLlama...")
    data = fetch_json(DEFILLAMA_FEES_URL)
    fees_by_date = {}
    for ts, fee_usd in data.get("totalDataChart", []):
        d = datetime.datetime.fromtimestamp(
            int(ts), datetime.timezone.utc
        ).date().isoformat()
        fees_by_date[d] = float(fee_usd)
    print("  {} days of fee data ({} to {})".format(
        len(fees_by_date), min(fees_by_date), max(fees_by_date)
    ))
    return fees_by_date


def _try_coincap():
    """CoinCap: free, no auth, returns up to 2000 daily records in one call."""
    print("  Trying CoinCap...")
    url = (
        "https://api.coincap.io/v2/assets/solana/history"
        "?interval=d1&start=1609459200000&end=9999999999999"
    )
    data = fetch_json(url)
    prices = {}
    for rec in data.get("data", []):
        d = rec["date"][:10]
        if rec.get("priceUsd"):
            prices[d] = float(rec["priceUsd"])
    if len(prices) < 100:
        raise ValueError("CoinCap returned only {} records".format(len(prices)))
    print("  CoinCap: {} records ({} to {})".format(len(prices), min(prices), max(prices)))
    return prices


def _try_defillama_coins():
    """DefiLlama coins chart: same CDN as TVL data, no auth needed."""
    print("  Trying DefiLlama coins chart...")
    url = "https://coins.llama.fi/chart/coingecko:solana?start=1609459200&period=1d"
    data = fetch_json(url)
    coin = data.get("coins", {}).get("coingecko:solana", {})
    prices = {}
    for rec in coin.get("prices", []):
        d = datetime.datetime.fromtimestamp(
            rec["timestamp"], datetime.timezone.utc
        ).date().isoformat()
        prices[d] = float(rec["price"])
    if len(prices) < 100:
        raise ValueError("DefiLlama coins returned only {} records".format(len(prices)))
    print("  DefiLlama coins: {} records ({} to {})".format(len(prices), min(prices), max(prices)))
    return prices


def _try_binance_us():
    """Binance.US: US-domiciled exchange, no geo-block, paginate 1000 candles at a time."""
    print("  Trying Binance.US...")
    prices = {}
    start_ms = 1609459200000  # 2021-01-01
    while True:
        url = (
            "https://api.binance.us/api/v3/klines"
            "?symbol=SOLUSDT&interval=1d&startTime={}&limit=1000".format(start_ms)
        )
        candles = fetch_json(url)
        if not candles:
            break
        for c in candles:
            ts    = c[0]
            close = float(c[4])
            d = datetime.datetime.fromtimestamp(
                ts / 1000, datetime.timezone.utc
            ).date().isoformat()
            prices[d] = close
        if len(candles) < 1000:
            break
        start_ms = candles[-1][0] + 86_400_000
    if not prices:
        raise ValueError("Binance.US returned no records")
    print("  Binance.US: {} records ({} to {})".format(len(prices), min(prices), max(prices)))
    return prices


def fetch_sol_prices():
    """Try each price source in order; raise only if all fail."""
    for fn in (_try_coincap, _try_defillama_coins, _try_binance_us):
        try:
            return fn()
        except Exception as e:
            print("  FAILED: {}".format(e))
    raise RuntimeError("All price sources failed — cannot build history")


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

    fees_by_date = fetch_fees_history()

    print("Fetching SOL price history...")
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
        ratio    = (tvl / 1e9) / price
        fees_usd = fees_by_date.get(date_str)
        history.append({
            "date":                 date_str,
            "price":                round(price, 4),
            "tvl":                  round(tvl, 2),
            "fees_usd":             round(fees_usd, 2) if fees_usd is not None else None,
            "ratio":                round(ratio, 6),
            "threshold":            TVL_MCAP_THRESHOLD,
            "price_change_pct_24h": None,
        })

    print("Built {} records ({} skipped)".format(len(history), skipped))
    if history:
        print("Date range: {} to {}".format(history[0]["date"], history[-1]["date"]))
        with_fees = sum(1 for h in history if h["fees_usd"] is not None)
        print("Records with fee data: {}".format(with_fees))

    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2)
    print("Written to " + HISTORY_FILE)


if __name__ == "__main__":
    main()
