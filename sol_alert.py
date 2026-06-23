"""
Solana TVL-per-Price Alert
---------------------------
Data sources (free, no auth):
  TVL:   DefiLlama    api.llama.fi
  Price: Coinpaprika  api.coinpaprika.com

Signal: (TVL in $B) / SOL price
  HIGH ratio = strong on-chain usage per dollar of SOL = undervalued = buy signal
  LOW  ratio = token price running ahead of on-chain utility = overvalued = sell signal
"""

import os
import sys
import json
import smtplib
import datetime
import urllib.request
import urllib.error
import urllib.parse
from email.mime.text import MIMEText

TVL_MCAP_THRESHOLD = float(os.environ.get("TVL_MCAP_THRESHOLD", "0.075"))

DEFILLAMA_TVL_URL    = "https://api.llama.fi/v2/historicalChainTvl/Solana"
DEFILLAMA_FEES_URL   = "https://api.llama.fi/summary/fees/Solana?dataType=dailyFees"
COINPAPRIKA_TICK_URL = "https://api.coinpaprika.com/v1/tickers/sol-solana"

EMAIL_FROM     = os.environ.get("ALERT_EMAIL_FROM")
EMAIL_TO       = os.environ.get("ALERT_EMAIL_TO")
EMAIL_PASSWORD = os.environ.get("ALERT_EMAIL_APP_PASSWORD")
SMTP_HOST      = "smtp.gmail.com"
SMTP_PORT      = 587

CALLMEBOT_PHONE  = os.environ.get("CALLMEBOT_PHONE")
CALLMEBOT_APIKEY = os.environ.get("CALLMEBOT_APIKEY")
CALLMEBOT_URL    = "https://api.callmebot.com/whatsapp.php"

HISTORY_FILE = "history.json"


def fetch_json(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "sol-alert-script/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError("HTTP error " + str(e.code) + " fetching " + url) from e
    except urllib.error.URLError as e:
        raise RuntimeError("Network error fetching " + url + ": " + str(e.reason)) from e
    except json.JSONDecodeError as e:
        raise RuntimeError("Bad JSON from " + url + ": " + str(e)) from e


def get_latest_tvl():
    data = fetch_json(DEFILLAMA_TVL_URL)
    if not isinstance(data, list) or not data:
        raise RuntimeError("Unexpected DefiLlama response shape")
    return float(data[-1]["tvl"])


def get_latest_fees():
    """Today's protocol fees from DefiLlama (returns None on failure, non-fatal)."""
    try:
        data = fetch_json(DEFILLAMA_FEES_URL)
        return float(data.get("total24h") or 0) or None
    except Exception as e:
        print("Fee fetch failed (non-fatal): " + str(e), file=sys.stderr)
        return None


def get_market_data():
    data = fetch_json(COINPAPRIKA_TICK_URL)
    usd = data.get("quotes", {}).get("USD", {})
    price = usd.get("price")
    mcap  = usd.get("market_cap")
    chg   = usd.get("percent_change_24h")
    if not price:
        raise RuntimeError("Missing price in Coinpaprika response")
    return {
        "price":                float(price),
        "market_cap":           float(mcap) if mcap else None,
        "price_change_pct_24h": float(chg) if chg is not None else None,
    }


def send_email(subject, body):
    if not all([EMAIL_FROM, EMAIL_TO, EMAIL_PASSWORD]):
        print("Email credentials not fully set; skipping send.")
        print(body)
        return
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"]    = EMAIL_FROM
    msg["To"]      = EMAIL_TO
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(EMAIL_FROM, EMAIL_PASSWORD)
        server.sendmail(EMAIL_FROM, [EMAIL_TO], msg.as_string())


def send_whatsapp(message):
    if not all([CALLMEBOT_PHONE, CALLMEBOT_APIKEY]):
        print("CallMeBot credentials not set; skipping WhatsApp send.")
        return
    params = {"phone": CALLMEBOT_PHONE, "text": message, "apikey": CALLMEBOT_APIKEY}
    url = CALLMEBOT_URL + "?" + urllib.parse.urlencode(params)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "sol-alert-script/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            print("CallMeBot response: " + str(resp.status))
    except Exception as e:
        print("WhatsApp send failed (non-fatal): " + str(e), file=sys.stderr)


def append_history(record):
    try:
        history = json.load(open(HISTORY_FILE)) if os.path.exists(HISTORY_FILE) else []
    except (json.JSONDecodeError, OSError) as e:
        print("Could not read history file, starting fresh: " + str(e), file=sys.stderr)
        history = []
    history.append(record)
    try:
        with open(HISTORY_FILE, "w") as f:
            json.dump(history, f, indent=2)
    except OSError as e:
        print("Could not write history file (non-fatal): " + str(e), file=sys.stderr)


def main():
    try:
        tvl    = get_latest_tvl()
        market = get_market_data()
        fees_usd = get_latest_fees()
    except RuntimeError as e:
        print("ERROR: " + str(e), file=sys.stderr)
        sys.exit(1)

    price = market["price"]
    mcap  = market["market_cap"]
    chg   = market["price_change_pct_24h"]

    if price <= 0:
        print("ERROR: price is zero or negative; aborting.", file=sys.stderr)
        sys.exit(1)

    # Ratio: TVL (in billions USD) / SOL price — consistent with backfill history
    ratio = (tvl / 1e9) / price
    today = datetime.date.today().isoformat()

    print("Date: "         + today)
    print("SOL TVL: $"     + str(tvl))
    print("SOL Price: $"   + str(price))
    print("24h change: "   + str(chg))
    print("TVL(B)/Price: " + str(ratio))
    print("Fees (24h): $"  + str(fees_usd))
    print("Threshold: "    + str(TVL_MCAP_THRESHOLD))

    append_history({
        "date":                 today,
        "price":                round(price, 4),
        "tvl":                  round(tvl, 2),
        "fees_usd":             round(fees_usd, 2) if fees_usd is not None else None,
        "ratio":                round(ratio, 6),
        "threshold":            TVL_MCAP_THRESHOLD,
        "price_change_pct_24h": chg,
    })

    if ratio >= TVL_MCAP_THRESHOLD:
        subject = "SOL TVL/Price signal: " + str(round(ratio, 4))
        body = (
            "Solana's TVL(B)/Price ratio has reached " + str(round(ratio, 4)) +
            ", at or above your threshold of " + str(TVL_MCAP_THRESHOLD) + ".\n\n" +
            "TVL: $"       + "{:.2f}B".format(tvl / 1e9) + "\n" +
            "Price: $"     + str(price) + "\n" +
            "24h change: " + str(chg)   + "%\n\n" +
            "Cross-check against your Bitcoin macro dashboard before acting.\n"
        )
        send_email(subject, body)
        send_whatsapp("SOL alert: TVL(B)/Price = " + str(round(ratio, 4)) + ". Price $" + str(price))
        print("Threshold met — email + WhatsApp attempted.")
    else:
        print("Threshold not met — no alert sent today (history still logged).")


if __name__ == "__main__":
    main()
