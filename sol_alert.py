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

TVL_MCAP_THRESHOLD = float(os.environ.get("TVL_MCAP_THRESHOLD", "0.12"))

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
    except Exception as e
