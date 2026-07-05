"""
Bitcoin Heat Index Backfill
----------------------------
Data sources (free, no auth):
  Price:   CoinCap    api.coincap.io
  MVRV:    CoinMetrics community-api.coinmetrics.io  (free tier)
  F&G:     Alternative.me  api.alternative.me

Composite:
  45% 200d MA deviation    (cycle positioning)
  35% MVRV Z-Score         (on-chain holder profitability)
  20% Fear & Greed Index   (sentiment extremes)

If CoinMetrics CapRealizedUSD is unavailable, falls back to:
  60% 200d MA / 40% Fear & Greed
"""

import json
import math
import datetime
import urllib.request
import urllib.error
import statistics

HISTORY_FILE = "history_btc.json"

COINCAP_URL       = ("https://api.coincap.io/v2/assets/bitcoin/history"
                     "?interval=d1&start=1367107200000&end=9999999999999")
DEFILLAMA_BTC_URL = "https://coins.llama.fi/chart/coingecko:bitcoin?start=1367107200&period=1d"
# CapMVRVCur = market_cap / realized_cap ratio, available on free community tier
COINMETRICS_URL   = ("https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"
                     "?assets=btc&metrics=CapMVRVCur"
                     "&start_time=2010-01-01T00:00:00Z&page_size=10000")
# No date_format param — returns plain unix timestamps which we parse reliably
FEAR_GREED_URL    = "https://api.alternative.me/fng/?limit=3000"


def fetch_json(url, headers=None):
    req = urllib.request.Request(
        url, headers=headers or {"User-Agent": "btc-backfill/1.0"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ── Price (fallback chain: CoinCap → DefiLlama → Binance.US) ─────────────────

def _try_coincap():
    print("  Trying CoinCap...")
    data = fetch_json(COINCAP_URL)
    prices = {}
    for rec in data.get("data", []):
        d = rec["date"][:10]
        if rec.get("priceUsd"):
            prices[d] = float(rec["priceUsd"])
    if len(prices) < 100:
        raise ValueError("CoinCap returned only {} records".format(len(prices)))
    print("  CoinCap: {} records ({} to {})".format(len(prices), min(prices), max(prices)))
    return prices


def _try_defillama():
    print("  Trying DefiLlama coins chart...")
    data = fetch_json(DEFILLAMA_BTC_URL)
    coin = data.get("coins", {}).get("coingecko:bitcoin", {})
    prices = {}
    for rec in coin.get("prices", []):
        d = datetime.datetime.fromtimestamp(
            rec["timestamp"], datetime.timezone.utc
        ).date().isoformat()
        prices[d] = float(rec["price"])
    if len(prices) < 100:
        raise ValueError("DefiLlama returned only {} records".format(len(prices)))
    print("  DefiLlama: {} records ({} to {})".format(len(prices), min(prices), max(prices)))
    return prices


def _try_binance_us():
    print("  Trying Binance.US...")
    prices = {}
    start_ms = 1367107200000  # 2013-04-28 (BTC/USD start)
    while True:
        url = ("https://api.binance.us/api/v3/klines"
               "?symbol=BTCUSDT&interval=1d&startTime={}&limit=1000".format(start_ms))
        candles = fetch_json(url)
        if not candles:
            break
        for c in candles:
            d = datetime.datetime.fromtimestamp(
                c[0] / 1000, datetime.timezone.utc
            ).date().isoformat()
            prices[d] = float(c[4])
        if len(candles) < 1000:
            break
        start_ms = candles[-1][0] + 86_400_000
    if not prices:
        raise ValueError("Binance.US returned no records")
    print("  Binance.US: {} records ({} to {})".format(len(prices), min(prices), max(prices)))
    return prices


def fetch_btc_prices():
    print("Fetching BTC price history...")
    for fn in (_try_coincap, _try_defillama, _try_binance_us):
        try:
            return fn()
        except Exception as e:
            print("  FAILED: {}".format(e))
    raise RuntimeError("All price sources failed")


# ── MVRV Z-Score ──────────────────────────────────────────────────────────────

def fetch_mvrv():
    """Returns dict of date → MVRV Z-Score (normalised MVRV ratio). Non-fatal."""
    print("Fetching BTC MVRV ratio from CoinMetrics community API...")
    try:
        data = fetch_json(COINMETRICS_URL)
        rows = data.get("data", [])
        if not rows:
            print("  CoinMetrics returned no rows — MVRV unavailable.")
            return {}

        mvrv_by_date = {}
        for row in rows:
            d = row.get("time", "")[:10]
            v = row.get("CapMVRVCur")
            if v is not None:
                mvrv_by_date[d] = float(v)

        if len(mvrv_by_date) < 50:
            print("  Too few MVRV records ({}).".format(len(mvrv_by_date)))
            return {}

        # Normalise ratio to Z-score: (ratio - mean) / stdev over full history.
        # When ratio << mean → very negative Z → Deep Value. When >> mean → Overheated.
        values = list(mvrv_by_date.values())
        mean   = sum(values) / len(values)
        std    = statistics.stdev(values)
        if std == 0:
            return {}
        mvrv_z = {d: round((v - mean) / std, 4) for d, v in mvrv_by_date.items()}
        print("  MVRV Z-Score: {} records ({} to {})".format(
            len(mvrv_z), min(mvrv_z), max(mvrv_z)))
        return mvrv_z

    except Exception as e:
        print("  CoinMetrics fetch failed (non-fatal): {}".format(e))
        return {}


# ── Fear & Greed ──────────────────────────────────────────────────────────────

def fetch_fear_greed():
    print("Fetching Fear & Greed index from Alternative.me...")
    try:
        data = fetch_json(FEAR_GREED_URL)
        fg = {}
        for rec in data.get("data", []):
            # Without date_format param the API returns plain unix timestamps
            ts = rec.get("timestamp", "")
            d = datetime.datetime.fromtimestamp(
                int(ts), datetime.timezone.utc
            ).date().isoformat()
            fg[d] = int(rec["value"])
        if not fg:
            print("  No F&G data returned.")
            return {}
        print("  {} records ({} to {})".format(len(fg), min(fg), max(fg)))
        return fg
    except Exception as e:
        print("  F&G fetch failed (non-fatal): {}".format(e))
        return {}


# ── 200-week SMA ──────────────────────────────────────────────────────────────

def compute_200w_sma(sorted_dates, price_by_date):
    """Returns dict of date → 200-week SMA (requires 1400 days of history)."""
    result = {}
    window = 200 * 7  # 1400 days
    prices_list = [price_by_date[d] for d in sorted_dates]
    running = 0.0
    for i, d in enumerate(sorted_dates):
        running += prices_list[i]
        if i >= window:
            running -= prices_list[i - window]
        if i >= window - 1:
            result[d] = running / window
    return result


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    prices    = fetch_btc_prices()
    mvrv_z    = fetch_mvrv()
    fg        = fetch_fear_greed()

    has_mvrv  = bool(mvrv_z)
    has_fg    = bool(fg)

    if has_mvrv:
        print("Composite: 45% MA + 35% MVRV + 20% F&G")
    else:
        print("Composite (MVRV fallback): 60% MA + 40% F&G")

    sorted_dates = sorted(prices)
    ma200_map    = {}
    running      = 0.0
    for i, d in enumerate(sorted_dates):
        running += prices[d]
        if i >= 200:
            running -= prices[sorted_dates[i - 200]]
        if i >= 199:
            ma200_map[d] = running / 200

    sma200w = compute_200w_sma(sorted_dates, prices)

    history = []
    skipped = 0
    for d in sorted_dates:
        p = prices[d]
        if p <= 0:
            skipped += 1
            continue
        ma200 = ma200_map.get(d)
        ma_dev = (p / ma200 - 1) * 100 if ma200 else None
        record = {
            "date":         d,
            "price":        round(p, 4),
            "ma_dev":       round(ma_dev, 4) if ma_dev is not None else None,
            "mvrv_zscore":  round(mvrv_z[d], 4) if d in mvrv_z else None,
            "fear_greed":   fg.get(d),
            "sma200w":      round(sma200w[d], 2) if d in sma200w else None,
        }
        history.append(record)

    print("Built {} records ({} skipped)".format(len(history), skipped))
    if history:
        print("Date range: {} to {}".format(history[0]["date"], history[-1]["date"]))
        print("  With MA dev:    {}".format(sum(1 for h in history if h["ma_dev"]     is not None)))
        print("  With MVRV:      {}".format(sum(1 for h in history if h["mvrv_zscore"] is not None)))
        print("  With F&G:       {}".format(sum(1 for h in history if h["fear_greed"] is not None)))
        print("  With 200w SMA:  {}".format(sum(1 for h in history if h["sma200w"]    is not None)))

    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2)
    print("Written to " + HISTORY_FILE)


if __name__ == "__main__":
    main()
