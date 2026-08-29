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

RENDER_APP_URL = os.environ.get('RENDER_EXTERNAL_URL', '')

@app.route('/')
def home():
    return "Supertrend Bot Active & Order-Loop Fixed!"

# =====================================================================
# ⚙️ USER CONFIGURATION
# =====================================================================
API_KEY = '4vtWGaF4x4LWleMfoj1ztriQp7rweE'
API_SECRET = 'dsuv5MuOGueu7OKXBo0U6CFCHryeEgujn3l7YD5rb5ibsWKDMRVU0BrQDhmW'
BASE_URL = "https://api.india.delta.exchange"

SYMBOL = "BTCUSD"
PRODUCT_ID = 27       # BTCUSD Perpetual Contract ID
TIMEFRAME = "1m"
LOT_SIZE = 1         # 1 Lot = 0.001 BTC
LEVERAGE = 10

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

# Self-Ping Mechanism for Render
def keep_awake():
    while True:
        time.sleep(240)
        if RENDER_APP_URL:
            try:
                requests.get(RENDER_APP_URL, timeout=5)
                print("⏰ Keep-Alive Ping Sent!", flush=True)
            except Exception as e:
                print(f"Keep-Alive Error: {e}", flush=True)

# 1. Fetch Candle Data
def fetch_candles():
    try:
        end_time = int(time.time())
        start_time = end_time - (100 * 60)
        
        path = f"/v2/history/candles?symbol={SYMBOL}&resolution={TIMEFRAME}&start={start_time}&end={end_time}"
        res = requests.get(BASE_URL + path, timeout=10)
        
        if res.status_code == 200:
            data = res.json()
            if data.get('success') and 'result' in data and len(data['result']) > 0:
                df = pd.DataFrame(data['result'])
                df = df.iloc[::-1].reset_index(drop=True)
                df['close'] = df['close'].astype(float)
                df['high'] = df['high'].astype(float)
                df['low'] = df['low'].astype(float)
                df['volume'] = df['volume'].astype(float)
                return df
        print("API Warning:", res.text, flush=True)
        return None
    except Exception as e:
        print(f"Candle Fetch Exception: {e}", flush=True)
        return None

# 2. Supertrend Calculation
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

# 3. Flexible Order Execution
def place_order(side, reduce_only=False):
    try:
        path = "/v2/orders"
        timestamp = str(int(time.time()))
        payload_dict = {
            "product_id": PRODUCT_ID,
            "size": LOT_SIZE,
            "side": side,
            "order_type": "market_order"
        }
        if reduce_only:
            payload_dict["reduce_only"] = True

        payload = json.dumps(payload_dict)
        headers = {
            'api-key': API_KEY.strip(),
            'signature': generate_signature("POST", timestamp, path, payload),
            'timestamp': timestamp,
            'Content-Type': 'application/json'
        }
        res = requests.post(BASE_URL + path, headers=headers, data=payload, timeout=10)
        return res.json()
    except Exception as e:
        print(f"Order Exception: {e}", flush=True)
        return {}

# 4. Strategy Engine
def strategy_loop():
    time.sleep(5)
    current_position = None     # Current Active Position: None, "BUY", "SELL"
    last_signal_direction = None

    print("🚀 BOT RUNNING (ORDER REPEAT BUG FIXED)!", flush=True)

    while True:
        try:
            df = fetch_candles()
            if df is not None and len(df) > ST_PERIOD + 2:
                df = calculate_supertrend(df)
                
                live_price = df.iloc[-1]['close']
                closed_candle = df.iloc[-2]
                closed_price = closed_candle['close']
                st_val = closed_candle['supertrend']
                closed_direction = closed_candle['st_direction']

                # First Run Initialization
                if last_signal_direction is None:
                    last_signal_direction = closed_direction
                    print(f"⏳ INITIALIZED: Base Trend Direction: {closed_direction}. Waiting...", flush=True)

                print(f"[{time.strftime('%H:%M:%S')}] Live: {live_price} | Close: {closed_price} | ST: {round(st_val,2)} | Pos: {current_position}", flush=True)

                # =====================================================
                # 🛑 STEP 1: INSTANT TOUCH EXIT ONLY
                # =====================================================
                if current_position == "BUY" and live_price <= st_val:
                    print("⚡ TOUCH EXIT: Price touched Supertrend! Closing Long Position...", flush=True)
                    place_order("sell", reduce_only=True)
                    current_position = None
                    last_signal_direction = 1  # Lock: Requires Fresh Red Candle Close for SELL
                    time.sleep(5)  # Cooldown safety
                    continue

                elif current_position == "SELL" and live_price >= st_val:
                    print("⚡ TOUCH EXIT: Price touched Supertrend! Closing Short Position...", flush=True)
                    place_order("buy", reduce_only=True)
                    current_position = None
                    last_signal_direction = -1  # Lock: Requires Fresh Green Candle Close for BUY
                    time.sleep(5)  # Cooldown safety
                    continue

                # =====================================================
                # 🟢🔴 STEP 2: FRESH ENTRY (STRICT CONFIRMATION)
                # =====================================================
                if current_position is None:
                    # BUY Signal: Closed > Supertrend AND Fresh Reversal (was Red)
                    if closed_price > st_val and last_signal_direction == -1:
                        print("🟢 FRESH BREAKOUT: Candle Closed Above ST! Opening BUY...", flush=True)
                        res = place_order("buy", reduce_only=False)
                        if res.get('success'):
                            current_position = "BUY"
                            last_signal_direction = 1
                            time.sleep(5)

                    # SELL Signal: Closed < Supertrend AND Fresh Reversal (was Green)
                    elif closed_price < st_val and last_signal_direction == 1:
                        print("🔴 FRESH BREAKOUT: Candle Closed Below ST! Opening SELL...", flush=True)
                        res = place_order("sell", reduce_only=False)
                        if res.get('success'):
                            current_position = "SELL"
                            last_signal_direction = -1
                            time.sleep(5)

        except Exception as e:
            print(f"Strategy Loop Exception: {e}", flush=True)

        time.sleep(5)

threading.Thread(target=strategy_loop, daemon=True).start()
threading.Thread(target=keep_awake, daemon=True).start()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
