from flask import Flask, render_template_string
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
DATA_FILE = "trade_history.json"

# Clean state reset on startup
if os.path.exists(DATA_FILE):
    try:
        os.remove(DATA_FILE)
    except Exception:
        pass

# =====================================================================
# ⚙️ USER CONFIGURATION & API KEYS
# =====================================================================
API_KEY = '4vtWGaF4x4LWleMfoj1ztriQp7rweE'
API_SECRET = 'dsuv5MuOGueu7OKXBo0U6CFCHryeEgujn3l7YD5rb5ibsWKDMRVU0BrQDhmW'
BASE_URL = "https://api.india.delta.exchange"

ST_PERIOD = 10
ST_MULTIPLIER = 1.5

COINS_TO_TRADE = [
    {"symbol": "BTCUSD",   "product_id": 27,     "timeframe": "5m",  "lot_size": 5},
    {"symbol": "XAUTUSD",  "product_id": 131253, "timeframe": "5m",  "lot_size": 200},
    {"symbol": "ETHUSD",   "product_id": 3136,   "timeframe": "5m",  "lot_size": 10},
    {"symbol": "SOLUSD",   "product_id": 120,    "timeframe": "5m",  "lot_size": 0},
    {"symbol": "COINXUSD", "product_id": 125551, "timeframe": "5m",  "lot_size": 200},
    {"symbol": "LINKUSD",  "product_id": 142,    "timeframe": "5m",  "lot_size": 0},
    {"symbol": "SLVONUSD", "product_id": 124058, "timeframe": "5m",  "lot_size": 10},
]

# Dynamic Contract Multipliers Map (1 Lot Size Value)
CONTRACT_SIZE_MAP = {
    "BTCUSD": 0.001,
    "XAUTUSD": 0.001,
    "ETHUSD": 0.01,
    "COINXUSD": 0.01,
    "SLVONUSD": 0.01,
    "SOLUSD": 0.1,
    "LINKUSD": 1.0
}
# =====================================================================

# =====================================================================
# 📊 DYNAMIC P&L LOGGER
# =====================================================================
def load_data():
    if not os.path.exists(DATA_FILE):
        initial_data = {
            "overall": {"total_trades": 0, "net_pnl": 0.0},
            "coins": {c["symbol"]: {"trades": 0, "wins": 0, "losses": 0, "pnl": 0.0} for c in COINS_TO_TRADE},
            "history": []
        }
        with open(DATA_FILE, "w") as f:
            json.dump(initial_data, f, indent=4)
        return initial_data
    try:
        with open(DATA_FILE, "r") as f:
            data = json.load(f)
            for c in COINS_TO_TRADE:
                if c["symbol"] not in data["coins"]:
                    data["coins"][c["symbol"]] = {"trades": 0, "wins": 0, "losses": 0, "pnl": 0.0}
            return data
    except Exception:
        return {"overall": {"total_trades": 0, "net_pnl": 0.0}, "coins": {}, "history": []}

def log_trade(symbol, trade_type, entry_price, exit_price, current_lot_size):
    try:
        data = load_data()
        
        # Get Contract Multiplier per Lot
        multiplier = CONTRACT_SIZE_MAP.get(symbol, 1.0)
        
        # Price Difference
        if trade_type == "BUY":
            price_diff = exit_price - entry_price
        else:
            price_diff = entry_price - exit_price

        # Accurate P&L Calculation
        pnl = price_diff * current_lot_size * multiplier

        if symbol not in data["coins"]:
            data["coins"][symbol] = {"trades": 0, "wins": 0, "losses": 0, "pnl": 0.0}

        data["coins"][symbol]["trades"] += 1
        if pnl >= 0:
            data["coins"][symbol]["wins"] += 1
        else:
            data["coins"][symbol]["losses"] += 1

        data["coins"][symbol]["pnl"] = round(data["coins"][symbol]["pnl"] + pnl, 2)
        data["overall"]["total_trades"] += 1
        data["overall"]["net_pnl"] = round(data["overall"]["net_pnl"] + pnl, 2)

        data["history"].insert(0, {
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "symbol": symbol,
            "type": trade_type,
            "lots": current_lot_size,
            "entry": round(entry_price, 2),
            "exit": round(exit_price, 2),
            "pnl": round(pnl, 2)
        })

        data["history"] = data["history"][:50]

        with open(DATA_FILE, "w") as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        print(f"[{symbol}] Logging Error: {e}", flush=True)

# =====================================================================
# 🌐 FLASK UI DASHBOARD
# =====================================================================
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Supertrend Multi-Coin Dashboard</title>
    <meta http-equiv="refresh" content="10">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body { font-family: 'Segoe UI', Arial, sans-serif; background-color: #0f172a; color: #f8fafc; margin: 0; padding: 20px; }
        h1, h2 { text-align: center; color: #38bdf8; }
        .summary-box { background: #1e293b; border-radius: 12px; padding: 20px; text-align: center; margin-bottom: 25px; border: 1px solid #334155; }
        .summary-title { font-size: 1.1em; color: #94a3b8; }
        .summary-value { font-size: 2.2em; font-weight: bold; margin-top: 5px; }
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 18px; }
        .card { background: #1e293b; border-radius: 12px; padding: 18px; border: 1px solid #334155; box-shadow: 0 4px 6px rgba(0,0,0,0.3); }
        .coin-name { font-size: 1.2em; font-weight: bold; color: #facc15; border-bottom: 1px solid #334155; padding-bottom: 8px; margin-bottom: 10px; }
        .stat { display: flex; justify-content: space-between; margin: 6px 0; font-size: 0.95em; color: #cbd5e1; }
        .profit { color: #4ade80; font-weight: bold; }
        .loss { color: #f87171; font-weight: bold; }
        table { width: 100%; border-collapse: collapse; margin-top: 15px; background: #1e293b; border-radius: 8px; overflow: hidden; }
        th, td { padding: 10px; text-align: center; border-bottom: 1px solid #334155; font-size: 0.88em; }
        th { background: #334155; color: #38bdf8; }
    </style>
</head>
<body>
    <h1>🚀 Supertrend Bot Dashboard</h1>
    
    <div class="summary-box">
        <div class="summary-title">Total Portfolio Net P&L</div>
        <div class="summary-value {{ 'profit' if data.overall.net_pnl >= 0 else 'loss' }}">
            {{ "${:,.2f}".format(data.overall.net_pnl) }}
        </div>
        <div style="margin-top:6px; color:#94a3b8;">Total Trades Executed: {{ data.overall.total_trades }}</div>
    </div>

    <h2>📊 Coin-wise Performance Cards</h2>
    <div class="grid">
        {% for symbol, stats in data.coins.items() %}
        <div class="card">
            <div class="coin-name">{{ symbol }}</div>
            <div class="stat"><span>Total Trades:</span> <span>{{ stats.trades }}</span></div>
            <div class="stat"><span>Wins / Losses:</span> <span>{{ stats.wins }} / {{ stats.losses }}</span></div>
            <div class="stat">
                <span>Net P&L:</span>
                <span class="{{ 'profit' if stats.pnl >= 0 else 'loss' }}">
                    {{ "${:,.2f}".format(stats.pnl) }}
                </span>
            </div>
        </div>
        {% endfor %}
    </div>

    <h2 style="margin-top: 30px;">📜 Live Executed Trade Logs</h2>
    <table>
        <tr>
            <th>Time</th>
            <th>Coin</th>
            <th>Type</th>
            <th>Lots</th>
            <th>Entry</th>
            <th>Exit</th>
            <th>P&L</th>
        </tr>
        {% for trade in data.history %}
        <tr>
            <td>{{ trade.time }}</td>
            <td><b>{{ trade.symbol }}</b></td>
            <td>{{ trade.type }}</td>
            <td>{{ trade.lots }}</td>
            <td>{{ trade.entry }}</td>
            <td>{{ trade.exit }}</td>
            <td class="{{ 'profit' if trade.pnl >= 0 else 'loss' }}">{{ "${:,.2f}".format(trade.pnl) }}</td>
        </tr>
        {% endfor %}
    </table>
</body>
</html>
"""

@app.route('/')
def home():
    data = load_data()
    return render_template_string(HTML_TEMPLATE, data=data)

# =====================================================================
# ⚙️ HELPER & STRATEGY FUNCTIONS
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

def keep_awake():
    while True:
        time.sleep(120)
        if RENDER_APP_URL:
            try:
                requests.get(RENDER_APP_URL, timeout=5)
                print("⏰ Keep-Alive Ping Sent!", flush=True)
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

    if lot_size <= 0:
        print(f"⏸️ SKIPPED: {symbol} (lot_size is 0)", flush=True)
        return

    current_position = None
    entry_price = 0.0
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

                t_str = time.strftime('%H:%M:%S')
                print(f"[{t_str}] {symbol} ({timeframe}) | Live: {live_price} | ST: {round(st_val,2)} | Pos: {current_position}", flush=True)

                # EXIT LOGIC WITH DYNAMIC LOT LOGGING
                if current_position == "BUY" and live_price <= st_val:
                    print(f"⚡ EXIT [LONG]: {symbol} ({timeframe}) touched Supertrend!", flush=True)
                    res = place_order(product_id, lot_size, "sell", reduce_only=True)
                    print(f"[{symbol}] Exit Response: {res}", flush=True)
                    log_trade(symbol, "BUY", entry_price, live_price, lot_size)
                    current_position = None
                    last_signal_direction = 1
                    time.sleep(5)
                    continue

                elif current_position == "SELL" and live_price >= st_val:
                    print(f"⚡ EXIT [SHORT]: {symbol} ({timeframe}) touched Supertrend!", flush=True)
                    res = place_order(product_id, lot_size, "buy", reduce_only=True)
                    print(f"[{symbol}] Exit Response: {res}", flush=True)
                    log_trade(symbol, "SELL", entry_price, live_price, lot_size)
                    current_position = None
                    last_signal_direction = -1
                    time.sleep(5)
                    continue

                # ENTRY LOGIC
                if current_position is None:
                    if closed_price > st_val and last_signal_direction == -1:
                        print(f"🟢 BUY ENTRY: {symbol} ({timeframe}) Closed Above Supertrend!", flush=True)
                        res = place_order(product_id, lot_size, "buy", reduce_only=False)
                        print(f"[{symbol}] Entry Response: {res}", flush=True)
                        if res.get('success'):
                            current_position = "BUY"
                            entry_price = live_price
                            last_signal_direction = 1
                            time.sleep(5)

                    elif closed_price < st_val and last_signal_direction == 1:
                        print(f"🔴 SELL ENTRY: {symbol} ({timeframe}) Closed Below Supertrend!", flush=True)
                        res = place_order(product_id, lot_size, "sell", reduce_only=False)
                        print(f"[{symbol}] Entry Response: {res}", flush=True)
                        if res.get('success'):
                            current_position = "SELL"
                            entry_price = live_price
                            last_signal_direction = -1
                            time.sleep(5)

        except Exception as e:
            print(f"[{symbol}] Loop Exception: {e}", flush=True)

        time.sleep(10)

# =====================================================================
# 🚀 START THREADS
# =====================================================================
for coin in COINS_TO_TRADE:
    threading.Thread(target=run_coin_strategy, args=(coin,), daemon=True).start()

threading.Thread(target=keep_awake, daemon=True).start()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
