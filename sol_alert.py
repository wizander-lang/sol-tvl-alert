"""
Solana TVL / Market Cap Alert
-------------------------------
Pulls Solana's chain TVL (DefiLlama, free, no key) and SOL market cap
(CoinGecko, free, no key), computes TVL/Mcap, and emails the user if the
ratio is at/above a threshold while price is down.
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

TVL_MCAP_THRESHOLD = float(os.environ.get("TVL_MCAP_THRESHOLD", "0.13"))

DEFILLAMA_TVL_URL = "https://api.llama.fi/v2/historicalChainTvl/Solana"
COINGECKO_MARKETS_URL = (
    "https://api.coingecko.com/api/v3/coins/markets"
    "?vs_currency=usd&ids=solana"
)

EMAIL_FROM = os.environ.get("ALERT_EMAIL_FROM")
EMAIL_TO = os.environ.get("ALERT_EMAIL_TO")
EMAIL_PASSWORD = os.environ.get("ALERT_EMAIL_APP_PASSWORD")
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587

CALLMEBOT_PHONE = os.environ.get("CALLMEBOT_PHONE")
CALLMEBOT_APIKEY = os.environ.get("CALLMEBOT_APIKEY")
CALLMEBOT_URL = "https://api.callmebot.com/whatsapp.php"

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
    latest = data[-1]
    return float(latest["tvl"])


def get_market_data():
    data = fetch_json(COINGECKO_MARKETS_URL)
    if not isinstance(data, list) or not data:
        raise RuntimeError("Unexpected CoinGecko response shape")
    entry = data[0]
    return {
        "market_cap": float(entry["market_cap"]),
        "price": float(entry["current_price"]),
        "price_change_pct_24h": entry.get("price_change_percentage_24h"),
    }


def send_email(subject, body):
    if not all([EMAIL_FROM, EMAIL_TO, EMAIL_PASSWORD]):
        print("Email credentials not fully set; skipping send.")
        print(body)
        return
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO
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
        if os.path.exists(HISTORY_FILE):
            with open(HISTORY_FILE, "r") as f:
                history = json.load(f)
        else:
            history = []
    except (json.JSONDecodeError, OSError) as e:
        print("Could not read existing history file, starting fresh: " + str(e), file=sys.stderr)
        history = []

    history.append(record)

    try:
        with open(HISTORY_FILE, "w") as f:
            json.dump(history, f, indent=2)
    except OSError as e:
        print("Could not write history file (non-fatal): " + str(e), file=sys.stderr)


def main():
    try:
        tvl = get_latest_tvl()
        market = get_market_data()
    except RuntimeError as e:
        print("ERROR: " + str(e), file=sys.stderr)
        sys.exit(1)

    mcap = market["market_cap"]
    if mcap <= 0:
        print("ERROR: market cap is zero or negative; aborting.", file=sys.stderr)
        sys.exit(1)

    ratio = tvl / mcap
    today = datetime.date.today().isoformat()
    price = market["price"]
    change = market["price_change_pct_24h"]

    print("Date: " + today)
    print("SOL TVL: " + str(tvl))
    print("SOL Market Cap: " + str(mcap))
    print("SOL Price: " + str(price))
    print("24h change: " + str(change))
    print("TVL/Mcap ratio: " + str(ratio))
    print("Threshold: " + str(TVL_MCAP_THRESHOLD))

    append_history({
        "date": today,
        "price": round(price, 4),
        "tvl": round(tvl, 2),
        "market_cap": round(mcap, 2),
        "ratio": round(ratio, 6),
        "threshold": TVL_MCAP_THRESHOLD,
        "price_change_pct_24h": change,
    })

    if ratio >= TVL_MCAP_THRESHOLD:
        subject = "SOL TVL/Mcap signal: " + str(round(ratio, 3))
        body = (
            "Solana's TVL/Market Cap ratio has reached " + str(round(ratio, 4)) +
            ", at or above your threshold of " + str(TVL_MCAP_THRESHOLD) + ".\n\n" +
            "TVL: " + str(tvl) + "\n" +
            "Market Cap: " + str(mcap) + "\n" +
            "Price: " + str(price) + "\n" +
            "24h price change: " + str(change) + "%\n\n" +
            "Cross-check against your Bitcoin macro dashboard before acting.\n"
        )
        send_email(subject, body)
        send_whatsapp("SOL alert: TVL/Mcap = " + str(round(ratio, 3)) + ". Price $" + str(price))
        print("Threshold met - email + WhatsApp attempted.")
    else:
        print("Threshold not met - no alert sent today (history still logged).")


if __name__ == "__main__":
    main()
