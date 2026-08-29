from flask import Flask
import threading
import time
import os
import requests
import hmac
import hashlib
import json
import pandas as pd
import numpy as np

app = Flask(__name__)

@app.route('/')
def home():
    return "Pure Supertrend Strategy Active!"

# =====================================================================
# ⚙️ CUSTOMIZATION PARAMETERS (Top Variables)
# =====================================================================
API_KEY = '4vtWGaF4x4LWleMfoj1ztriQp7rweE'
API_SECRET = 'dsuv5MuOGueu7OKXBo0U6CFCHryeEgujn3l7YD5rb5ibsWKDMRVU0BrQDhmW'
BASE_URL = "https://api.india.delta.exchange"

SYMBOL = "BTCUSD"
PRODUCT_ID = 27       # BTCUSD Perpetual Contract ID (Delta India)
TIMEFRAME = "1m"      # Timeframe: "1m", "5m", "15m"
LOT_SIZE = 1         # Lot size (1 Lot = 0.001 BTC)
LEVERAGE = 10         # Leverage

ST_PERIOD = 10        # Supertrend Period
ST_MULTIPLIER = 1.5   # Supertrend Multiplier
# =====================================================================

def generate_signature(method, timestamp, path, payload=""):
    signature_data = method + timestamp + path + payload
    return hmac.new(
        API_SECRET.strip().encode('utf-8'),
        signature_data.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()

# 1. Fetch Candle Data (Exact Delta History API Endpoint)
def fetch_candles():
    try:
        end_time = int(time.time())
        start_time = end_time - (100 * 60)  # Fetch last 100 minutes
        
        # Endpoint path with correct query parameters
        path = f"/v2/history/candles?symbol={SYMBOL}&resolution={TIMEFRAME}&start={start_time}&end={end_time}"
        res = requests.get(BASE_URL + path, timeout=10)
        data = res.json()
        
        if data.get('success') and 'result' in data and len(data['result']) > 0:
            df = pd.DataFrame(data['result'])
            # Delta returns candles newest to oldest -> invert DataFrame
            df = df.iloc[::-1].reset_index(drop=True)
            df['close'] = df['close'].astype(float)
            df['high'] = df['high'].astype(float)
            df['low'] = df['low'].astype(float)
            df['volume'] = df['volume'].astype(float)
            return df
        else:
            print("Candle API Error Details:", data, flush=True)
            return None
    except Exception as e:
        print(f"Error fetching candles: {e}", flush=True)
        return None

# 2. Pure Supertrend Indicator Calculation
def calculate_supertrend(df):
    high_low = df['high'] - df['low']
    high_close = np.abs(df['high'] - df['close'].shift())
    low_close = np.abs(df['low'] - df['close'].shift())
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    atr = tr.rolling(ST_PERIOD).mean()

    basic_ub = (df['high'] + df['low']) / 2 + (ST_MULTIPLIER * atr)
    basic_lb = (df['high'] + df['low']) / 2 - (ST_MULTIPLIER * atr)

    upperband = basic_ub.copy()
    lowerband = basic_lb.copy()

    for i in range(1, len(df)):
        if df['close'].iloc[i-1] <= upperband.iloc[i-1]:
            upperband.iloc[i] = min(basic_ub.iloc[i], upperband.iloc[i-1])
        else:
            upperband.iloc[i] = basic_ub.iloc[i]

        if df['close'].iloc[i-1] >= lowerband.iloc[i-1]:
            lowerband.iloc[i] = max(basic_lb.iloc[i], lowerband.iloc[i-1])
        else:
            lowerband.iloc[i] = basic_lb.iloc[i]

    supertrend = pd.Series(index=df.index, dtype=float)
    direction = pd.Series(index=df.index, dtype=int)

    for i in range(1, len(df)):
        if df['close'].iloc[i] > upperband.iloc[i-1]:
            direction.iloc[i] = 1
        elif df['close'].iloc[i] < lowerband.iloc[i-1]:
            direction.iloc[i] = -1
        else:
            direction.iloc[i] = direction.iloc[i-1]

        supertrend.iloc[i] = lowerband.iloc[i] if direction.iloc[i] == 1 else upperband.iloc[i]

    df['supertrend'] = supertrend
    df['st_direction'] = direction
    return df

# 3. Place Order Function
def place_market_order(side):
    path = "/v2/orders"
    timestamp = str(int(time.time()))
    payload = json.dumps({
        "product_id": PRODUCT_ID,
        "size": LOT_SIZE,
        "side": side,
        "order_type": "market_order"
    })
    headers = {
        'api-key': API_KEY.strip(),
        'signature': generate_signature("POST", timestamp, path, payload),
        'timestamp': timestamp,
        'Content-Type': 'application/json'
    }
    res = requests.post(BASE_URL + path, headers=headers, data=payload, timeout=10)
    return res.json()

# 4. Strategy Main Engine
def strategy_loop():
    time.sleep(5)
    current_position = None
    print("🚀 PURE SUPERTREND (10, 1.5) STRATEGY STARTED!", flush=True)

    while True:
        try:
            df = fetch_candles()
            if df is not None and len(df) > ST_PERIOD:
                df = calculate_supertrend(df)
                
                last_candle = df.iloc[-1]
                close_price = last_candle['close']
                st_val = last_candle['supertrend']

                print(f"[{time.strftime('%H:%M:%S')}] BTC Price: {close_price} | Supertrend: {round(st_val, 2)} | Pos: {current_position}", flush=True)

                # BUY Condition: Close > Supertrend
                if close_price > st_val and current_position != "BUY":
                    print("🟢 BUY SIGNAL: Close > Supertrend!", flush=True)
                    res = place_market_order("buy")
                    print("BUY Response:", res, flush=True)
                    if res.get('success'):
                        current_position = "BUY"

                # SELL Condition: Close < Supertrend
                elif close_price < st_val and current_position != "SELL":
                    print("🔴 SELL SIGNAL: Close < Supertrend!", flush=True)
                    res = place_market_order("sell")
                    print("SELL Response:", res, flush=True)
                    if res.get('success'):
                        current_position = "SELL"

        except Exception as e:
            print(f"Strategy Loop Exception: {e}", flush=True)

        time.sleep(10)

threading.Thread(target=strategy_loop, daemon=True).start()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
