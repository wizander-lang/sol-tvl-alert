"""
Solana TVL / Market Cap Alert
-------------------------------
Pulls Solana's chain TVL (DefiLlama, free, no key) and SOL market cap
(CoinGecko, free, no key), computes TVL/Mcap, and emails the user if the
ratio is at/above a threshold while price is down — a usage-price
decoupling signal historically associated with accumulation zones.

Runs once per invocation. Intended to be triggered daily by a GitHub
Actions cron job (see .github/workflows/sol-alert.yml).
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

# ---------- Configuration ----------
# Threshold: ratio is "high" (favorable) at/above this value.
# 0.12 = TVL is 12% of market cap, based on real June 2026 data. Re-tune this
# after a few weeks of real logged history.json values — see the dashboard.
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

# CallMeBot WhatsApp — free, unofficial, hobbyist service. Optional: if these
# aren't set, the script just skips WhatsApp and still sends email.
# Setup: message the CallMeBot contact "I allow callmebot to send me messages"
# from your own WhatsApp, then put your phone + the API key it sends you below.
CALLMEBOT_PHONE = os.environ.get("CALLMEBOT_PHONE")  # e.g. "447911123456" (no +, no spaces)
CALLMEBOT_APIKEY = os.environ.get("CALLMEBOT_APIKEY")
CALLMEBOT_URL = "https://api.callmebot.com/whatsapp.php"

HISTORY_FILE = "history.json"


def fetch_json(url: str, timeout: int = 15):
    """Fetch and parse JSON from a URL, with a clear error on failure."""
    req = urllib.request.Request(url, headers={"User-Agent": "sol-alert-script/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP error {e.code} fetching {url}: {e.reason}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Network error fetching {url}: {e.reason}") from e
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Bad JSON from {url}: {e}") from e


def get_latest_tvl() -> float:
    """DefiLlama returns a time series: [{date, tvl}, ...]. We want the last point."""
    data = fetch_json(DEFILLAMA_TVL_URL)
    if not isinstance(data, list) or not data:
        raise RuntimeError(f"Unexpected DefiLlama response shape: {str(data)[:200]}")
    latest = data[-1]
    return float(latest["tvl"])


def get_market_data() -> dict:
    """CoinGecko /coins/markets returns a list with one entry for our single id."""
    data = fetch_json(COINGECKO_MARKETS_URL)
    if not isinstance(data, list) or not data:
        raise RuntimeError(f"Unexpected CoinGecko response shape: {str(data)[:200]}")
    entry = data[0]
    return {
        "market_cap": float(entry["market_cap"]),
        "price": float(entry["current_price"]),
        "price_change_pct_24h": entry.get("price_change_percentage_24h"),
    }


def send_email(subject: str, body: str):
    if not all([EMAIL_FROM, EMAIL_TO, EMAIL_PASSWORD]):
        print("Email credentials not fully set; skipping send. Body was:\n" + body)
        return
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(EMAIL_FROM, EMAIL_PASSWORD)
        server.sendmail(EMAIL_FROM, [EMAIL_TO], msg.as_string())


def send_whatsapp(message: str):
    """Best-effort WhatsApp send via CallMeBot. Never raises — a WhatsApp
    failure should never take down the email alert or the history write."""
    if not all([CALLMEBOT_PHONE, CALLMEBOT_APIKEY]):
        print("CallMeBot credentials not set; skipping WhatsApp send.")
        return
    params = {
        "phone": CALLMEBOT_PHONE,
        "text": message,
        "apikey": CALLMEBOT_APIKEY,
    }
    url = CALLMEBOT_URL + "?" + urllib.parse.urlencode(params)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "sol-alert-script/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            print(f"CallMeBot response: {resp.status}")
    except Exception as e:  # noqa: BLE001 — intentionally broad, this is non-critical
        print(f"WhatsApp send failed (non-fatal): {e}", file=sys.stderr)


def append_history(record: dict):
    """Append today's reading to history.json. Never raises on its own —
    a history-write failure shouldn't stop email/WhatsApp from going out."""
    try:
        if os.path.exists(HISTORY_FILE):
            with open(HISTORY_FILE, "r") as f:
                history = json.load(f)
        else:
            history = []
    except (json.JSONDecodeError, OSError) as e:
        print(f"Could not read existing history file, starting fresh: {e}", file=sys.stderr)
        history = []

    history.append(record)

    try:
        with open(HISTORY_FILE, "w") as f:
            json.dump(history, f, indent=2)
    except OSError as e:
        print(f"Could not write history file (non-fatal): {e}", file=sys.stderr)


def main():
    try:
        tvl = get_latest_tvl()
        market = get_market_data()
    except RuntimeError as e:
        # Fail loudly in the GitHub Actions log, but don't email the user
        # about a transient API hiccup.
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    mcap = market["market_cap"]
    if mcap <= 0:
        print("ERROR: market cap is zero or negative; aborting.", file=sys.stderr)
        sys.exit(1)

    ratio = tvl / mcap
    today = datetime.date.today().isoformat()

    print(f"Date: {today}")
    print(f"SOL TVL: ${tvl:,.0f}")
    print(f"SOL Market Cap: ${mcap:,.0f}")
    print(f"SOL Price: ${market['price']:,.2f} (24h change: {market['price_change_pct_24h']}%)")
    print(f"TVL/Mcap ratio: {ratio:.4f}")
    print(f"Threshold: {TVL_MCAP_THRESHOLD:.4f}")

    # Always log today's reading, threshold met or not — this is what
    # the dashboard reads to draw the historical chart.
    append_history({
        "date": today,
        "price": round(market["price"], 4),
        "tvl": round(tvl, 2),
        "market_cap": round(mcap, 2),
        "ratio": round(ratio, 6),
        "threshold": TVL_MCAP_THRESHOLD,
        "price_change_pct_24h": market["price_change_pct_24h"],
    })

    if ratio >= TVL_MCAP_THRESHOLD:
        subject = f"SOL TVL/Mcap signal: {ratio:.3f} (threshold {TVL_MCAP_THRESHOLD:.3f})"
        body = (
            f"Solana's TVL/Market Cap ratio has reached {ratio:.4f}, "
            f"at or above your threshold of {TVL_MCAP_THRESHOLD:.4f}.\n\n"
            f"TVL: ${tvl:,.0f}\n"
            f"Market Cap: ${mcap:,.0f}\n"
            f"Price: ${market['price']:,.2f}\n"
            f"24h price change: {market['price_change_pct_24h']}%\n\n"
            "A rising or elevated TVL/Mcap ratio while price is flat or falling "
            "can indicate usage holding up while valuation compresses — "
            "historically a constructive sign, not a standalone buy signal. "
            "Cross-check against your Bitcoin macro dashboard before acting.\n"
        )
        send_email(subject, body)
        send_whatsapp(
            f"SOL alert: TVL/Mcap = {ratio:.3f} (threshold {TVL_MCAP_THRESHOLD:.3f}). "
            f"Price
