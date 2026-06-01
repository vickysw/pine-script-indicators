"""
Polygon.io OHLCV Data Fetcher
Edit only the CONFIG block below — nothing else.
"""

# ─── CONFIG ────────────────────────────────────────────────
API_KEY   = "nDROcjCeh1S9hoZYcmKUl3d7DI7UNE2g"
SYMBOL    = "C:XAUUSD"    # C:XAUUSD, C:EURUSD, X:BTCUSD
MONTHS    = 24            # how many months of data
TIMEFRAME = "5"           # minutes: 1, 5, 15, 30, 60
OUT_FILE  = "gold_24m.csv" # output filename
# ───────────────────────────────────────────────────────────

import requests
import pandas as pd
from datetime import datetime, timedelta
import time
import sys

end_dt   = datetime.now()
start_dt = end_dt - timedelta(days=int(MONTHS * 30.5))

from_ts  = int(start_dt.timestamp() * 1000)
to_ts    = int(end_dt.timestamp() * 1000)

BASE_URL = (
    f"https://api.polygon.io/v2/aggs/ticker/{SYMBOL}/range/{TIMEFRAME}/minute"
    f"/{start_dt.strftime('%Y-%m-%d')}/{end_dt.strftime('%Y-%m-%d')}"
    f"?adjusted=true&sort=asc&limit=50000&apiKey={API_KEY}"
)

print(f"Fetching {SYMBOL} | {TIMEFRAME}m | {start_dt.date()} to {end_dt.date()}")

all_bars = []
url = BASE_URL

while url:
    resp = requests.get(url)
    if resp.status_code != 200:
        print(f"ERROR: HTTP {resp.status_code} — {resp.text}")
        sys.exit(1)

    data = resp.json()

    if data.get("status") == "ERROR":
        print(f"ERROR: {data.get('error', data)}")
        sys.exit(1)

    bars = data.get("results", [])
    all_bars.extend(bars)
    print(f"  fetched {len(all_bars)} bars so far ...")

    url = data.get("next_url")
    if url:
        url += f"&apiKey={API_KEY}"
        time.sleep(12)  # free tier: 5 req/min

if not all_bars:
    print("ERROR: No data returned. Check SYMBOL or API key.")
    sys.exit(1)

df = pd.DataFrame(all_bars)
df["Datetime"] = pd.to_datetime(df["t"], unit="ms", utc=True)
df = df.rename(columns={"o": "Open", "h": "High", "l": "Low", "c": "Close", "v": "Volume"})
df = df[["Datetime", "Open", "High", "Low", "Close", "Volume"]]
df.set_index("Datetime", inplace=True)
df.sort_index(inplace=True)

df.to_csv(OUT_FILE)
print(f"\nDone! {len(df)} rows saved -> {OUT_FILE}")
print(df.tail(5))
