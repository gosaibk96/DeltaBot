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
    return "Supertrend + VWAP Strategy Active!"

# =====================================================================
# ⚙️ USER CONFIGURATION (YAHAN SE VALUES EASY CHANGE KAREIN)
# =====================================================================
API_KEY = '4vtWGaF4x4LWleMfoj1ztriQp7rweE'
API_SECRET = 'dsuv5MuOGueu7OKXBo0U6CFCHryeEgujn3l7YD5rb5ibsWKDMRVU0BrQDhmW'
BASE_URL = "https://api.india.delta.exchange"

SYMBOL = "BTCUSD"
PRODUCT_ID = 27       # Delta India BTCUSD Perpetual Contract ID
TIMEFRAME = "1m"      # Timeframe: 1m, 5m, 15m
LOT_SIZE = 1         # 1 Lot = 0.001 BTC
LEVERAGE = 10         # Leverage

# Strategy Parameters
ST_PERIOD = 10
ST_MULTIPLIER = 1.5
# =====================================================================

def generate_signature(method, timestamp, path, payload=""):
    signature_data = method + timestamp + path + payload
    return hmac.new(
        API_SECRET.strip().encode('utf-8'),
        signature_data.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()

# 1. Fetch Candle Data
def fetch_candles():
    # Fetching historical candles for Supertrend & VWAP
    path = f"/v2/chart/candles?symbol={SYMBOL}&resolution={TIMEFRAME}"
    res = requests.get(BASE_URL + path, timeout=10)
    data = res.json()
    if 'result' in data:
        df = pd.DataFrame(data['result'])
        df = df.iloc[::-1].reset_index(drop=True) # Sort oldest to newest
        df['close'] = df['close'].astype(float)
        df['high'] = df['high'].astype(float)
        df['low'] = df['low'].astype(float)
        df['volume'] = df['volume'].astype(float)
        return df
    return None

# 2. Calculate Indicators (Supertrend & VWAP)
def calculate_indicators(df):
    # VWAP Calculation
    df['tp'] = (df['high'] + df['low'] + df['close']) / 3
    df['vwap'] = (df['tp'] * df['volume']).cumsum() / df['volume'].cumsum()

    # Supertrend Calculation
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

# 3. Order & Position Execution Functions
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

# 4. Strategy Main Loop
def strategy_loop():
    time.sleep(5)
    current_position = None  # None, "BUY", or "SELL"

    print("🚀 Strategy 1: Supertrend + VWAP Loop Started!", flush=True)

    while True:
        try:
            df = fetch_candles()
            if df is not None and len(df) > ST_PERIOD:
                df = calculate_indicators(df)
                
                last_candle = df.iloc[-1]
                close_price = last_candle['close']
                st_val = last_candle['supertrend']
                vwap_val = last_candle['vwap']

                print(f"[{time.strftime('%H:%M:%S')}] Close: {close_price} | ST: {round(st_val,2)} | VWAP: {round(vwap_val,2)} | Pos: {current_position}", flush=True)

                # --- EXIT CONDITION (VWAP Touch / Cross) ---
                if current_position == "BUY" and close_price <= vwap_val:
                    print("🛑 EXIT BUY POSITION: Price touched/below VWAP!", flush=True)
                    place_market_order("sell")
                    current_position = None

                elif current_position == "SELL" and close_price >= vwap_val:
                    print("🛑 EXIT SELL POSITION: Price touched/above VWAP!", flush=True)
                    place_market_order("buy")
                    current_position = None

                # --- ENTRY CONDITIONS ---
                elif current_position is None:
                    # BUY Trigger: Close > Supertrend
                    if close_price > st_val:
                        print("🟢 ENTRY BUY: Close > Supertrend!", flush=True)
                        res = place_market_order("buy")
                        if res.get('success'):
                            current_position = "BUY"

                    # SELL Trigger: Close < Supertrend
                    elif close_price < st_val:
                        print("🔴 ENTRY SELL: Close < Supertrend!", flush=True)
                        res = place_market_order("sell")
                        if res.get('success'):
                            current_position = "SELL"

        except Exception as e:
            print(f"Loop Exception: {e}", flush=True)

        time.sleep(10) # 10 second loop frequency

threading.Thread(target=strategy_loop, daemon=True).start()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
