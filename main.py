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
    return "Multi-Coin Supertrend Bot Active & Logging!"

# =====================================================================
# ⚙️ USER CONFIGURATION & API KEYS
# =====================================================================
API_KEY = '4vtWGaF4x4LWleMfoj1ztriQp7rweE'
API_SECRET = 'dsuv5MuOGueu7OKXBo0U6CFCHryeEgujn3l7YD5rb5ibsWKDMRVU0BrQDhmW'
BASE_URL = "https://api.india.delta.exchange"

# COMMON SUPERTREND SETTINGS (SABHI COINS KE LIYE SAME)
ST_PERIOD = 10
ST_MULTIPLIER = 1.5

# 🚀 AAPKI SELECTED 6 INSTRUMENTS KI LIST
COINS_TO_TRADE = [
    {"symbol": "BTCUSD",   "product_id": 27,     "timeframe": "1m",  "lot_size": 0},
    {"symbol": "XAUTUSD",   "product_id": 131253,    "timeframe": "1m",  "lot_size": 2},   # Gold (Tether Gold)
    {"symbol": "ETHUSD",   "product_id": 28,     "timeframe": "1m",  "lot_size": 1},   # ETF / ETH
    {"symbol": "SOLUSD",   "product_id": 120,    "timeframe": "1m", "lot_size": 0},
    {"symbol": "COINXUSD", "product_id": 125551, "timeframe": "1m", "lot_size": 2}, # Index
    {"symbol": "LINKUSD",  "product_id": 142,    "timeframe": "1m", "lot_size": 0},
]
# =====================================================================

TF_SECONDS_MAP = {
    "1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400, "1d": 86400
}

def generate_signature(method, timestamp, path, payload=""):
    signature_data = method + timestamp + path + payload
    return hmac.new(
        API_SECRET.strip().encode('utf-8'),
        signature_data.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()

# ⏰ SERVER KO JAGANE WAALA POINT (KEEP-ALIVE PING)
def keep_awake():
    while True:
        time.sleep(120)  # Har 2 minute me ping karega
        if RENDER_APP_URL:
            try:
                requests.get(RENDER_APP_URL, timeout=5)
                print("⏰ Keep-Alive Ping Sent to Render Server!", flush=True)
            except Exception as e:
                print(f"Keep-Alive Error: {e}", flush=True)

def fetch_candles(symbol, timeframe):
    try:
        end_time = int(time.time())
        candle_seconds = TF_SECONDS_MAP.get(timeframe, 60)
        start_time = end_time - (120 * candle_seconds)
        
        path = f"/v2/history/candles?symbol={symbol}&resolution={timeframe}&start={start_time}&end={end_time}"
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
        print(f"[{symbol}] API Warning: {res.text}", flush=True)
        return None
    except Exception as e:
        print(f"[{symbol}] Candle Exception: {e}", flush=True)
        return None

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

def place_order(product_id, lot_size, side, reduce_only=False):
    try:
        path = "/v2/orders"
        timestamp = str(int(time.time()))
        payload_dict = {
            "product_id": product_id,
            "size": lot_size,
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

def run_coin_strategy(coin):
    symbol = coin["symbol"]
    product_id = coin["product_id"]
    timeframe = coin["timeframe"]
    lot_size = coin["lot_size"]
    
    current_position = None
    last_signal_direction = None

    print(f"✅ INITIALIZING: {symbol} | TF: {timeframe} | Lots: {lot_size}", flush=True)

    while True:
        try:
            df = fetch_candles(symbol, timeframe)
            if df is not None and len(df) > ST_PERIOD + 2:
                df = calculate_supertrend(df)
                
                live_price = df.iloc[-1]['close']
                closed_candle = df.iloc[-2]
                closed_price = closed_candle['close']
                st_val = closed_candle['supertrend']
                closed_direction = closed_candle['st_direction']

                if last_signal_direction is None:
                    last_signal_direction = closed_direction

                # 📊 TERMINAL LOG PRINTING (Har cycle par log update hoga)
                t_str = time.strftime('%H:%M:%S')
                print(f"[{t_str}] {symbol} ({timeframe}) | Live: {live_price} | ST: {round(st_val,2)} | Pos: {current_position}", flush=True)

                # 🛑 STEP 1: INSTANT TOUCH EXIT
                if current_position == "BUY" and live_price <= st_val:
                    print(f"⚡ EXIT [LONG]: {symbol} ({timeframe}) touched Supertrend!", flush=True)
                    place_order(product_id, lot_size, "sell", reduce_only=True)
                    current_position = None
                    last_signal_direction = 1
                    time.sleep(5)
                    continue

                elif current_position == "SELL" and live_price >= st_val:
                    print(f"⚡ EXIT [SHORT]: {symbol} ({timeframe}) touched Supertrend!", flush=True)
                    place_order(product_id, lot_size, "buy", reduce_only=True)
                    current_position = None
                    last_signal_direction = -1
                    time.sleep(5)
                    continue

                # 🟢🔴 STEP 2: FRESH ENTRY
                if current_position is None:
                    if closed_price > st_val and last_signal_direction == -1:
                        print(f"🟢 BUY ENTRY: {symbol} ({timeframe}) Closed Above Supertrend!", flush=True)
                        res = place_order(product_id, lot_size, "buy", reduce_only=False)
                        if res.get('success'):
                            current_position = "BUY"
                            last_signal_direction = 1
                            time.sleep(5)

                    elif closed_price < st_val and last_signal_direction == 1:
                        print(f"🔴 SELL ENTRY: {symbol} ({timeframe}) Closed Below Supertrend!", flush=True)
                        res = place_order(product_id, lot_size, "sell", reduce_only=False)
                        if res.get('success'):
                            current_position = "SELL"
                            last_signal_direction = -1
                            time.sleep(5)

        except Exception as e:
            print(f"[{symbol}] Loop Exception: {e}", flush=True)

        time.sleep(10) # 10 seconds ka interval per check

# ALL THREADS START
for coin in COINS_TO_TRADE:
    threading.Thread(target=run_coin_strategy, args=(coin,), daemon=True).start()

# KEEP-ALIVE THREAD START
threading.Thread(target=keep_awake, daemon=True).start()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
