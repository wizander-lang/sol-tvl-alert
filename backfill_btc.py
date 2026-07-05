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
COINMETRICS_URL   = ("https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"
                     "?assets=btc&metrics=CapMrktCurUSD,CapRealizedUSD"
                     "&start_time=2013-01-01&page_size=10000")
FEAR_GREED_URL    = "https://api.alternative.me/fng/?limit=3000&date_format=iso"


def fetch_json(url, headers=None):
    req = urllib.request.Request(
        url, headers=headers or {"User-Agent": "btc-backfill/1.0"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ── Price ─────────────────────────────────────────────────────────────────────

def fetch_btc_prices():
    print("Fetching BTC price history from CoinCap...")
    data = fetch_json(COINCAP_URL)
    prices = {}
    for rec in data.get("data", []):
        d = rec["date"][:10]
        if rec.get("priceUsd"):
            prices[d] = float(rec["priceUsd"])
    if len(prices) < 100:
        raise RuntimeError("CoinCap returned only {} records".format(len(prices)))
    print("  {} records ({} to {})".format(len(prices), min(prices), max(prices)))
    return prices


# ── MVRV Z-Score ──────────────────────────────────────────────────────────────

def fetch_mvrv():
    """Returns dict of date → MVRV Z-Score. Returns {} on failure (non-fatal)."""
    print("Fetching BTC MVRV components from CoinMetrics community API...")
    try:
        data = fetch_json(COINMETRICS_URL)
        rows = data.get("data", [])
        if not rows:
            print("  CoinMetrics returned no rows — MVRV unavailable, will use fallback.")
            return {}

        mkt_by_date  = {}
        real_by_date = {}
        for row in rows:
            d = row.get("time", "")[:10]
            if row.get("CapMrktCurUSD"):
                mkt_by_date[d]  = float(row["CapMrktCurUSD"])
            if row.get("CapRealizedUSD"):
                real_by_date[d] = float(row["CapRealizedUSD"])

        if not real_by_date:
            print("  CapRealizedUSD missing from community tier — MVRV unavailable.")
            return {}

        # Compute Z-Score = (MktCap - RealizedCap) / StdDev(MktCap - RealizedCap)
        common = sorted(set(mkt_by_date) & set(real_by_date))
        diffs  = [mkt_by_date[d] - real_by_date[d] for d in common]
        if len(diffs) < 30:
            print("  Insufficient overlap for Z-Score.")
            return {}
        std = statistics.stdev(diffs)
        if std == 0:
            return {}
        mvrv_z = {d: (mkt_by_date[d] - real_by_date[d]) / std for d in common}
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
            # date_format=iso gives timestamp as ISO date string or unix
            ts = rec.get("timestamp", "")
            # Alternative.me returns unix timestamp even with date_format=iso
            try:
                d = datetime.datetime.fromtimestamp(
                    int(ts), datetime.timezone.utc
                ).date().isoformat()
            except (ValueError, TypeError):
                d = str(ts)[:10]
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
