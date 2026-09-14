import time
import threading
from datetime import datetime
import os
import pytz
import pandas as pd
import requests
from flask import Flask, jsonify, render_template_string

# ================= CONFIGURATION =================
CONFIG = {
    "XRPUSD": {"timeframe": "1h", "lot": 1, "max_candle_pct": 1.0, "trail_pct": 0.5, "rr": 4.0},
    "DOTUSD": {"timeframe": "1h", "lot": 1, "max_candle_pct": 1.0, "trail_pct": 0.5, "rr": 4.0},
    "GRAMUSD": {"timeframe": "1h", "lot": 1, "max_candle_pct": 1.0, "trail_pct": 0.5, "rr": 4.0},
    "PIEVERSEUSD": {"timeframe": "1h", "lot": 1, "max_candle_pct": 1.0, "trail_pct": 0.5, "rr": 4.0},
    "RIVERUSD": {"timeframe": "1h", "lot": 1, "max_candle_pct": 1.0, "trail_pct": 0.5, "rr": 4.0},
    "MUSD": {"timeframe": "1h", "lot": 1, "max_candle_pct": 1.0, "trail_pct": 0.5, "rr": 4.0},
    "ZROUSD": {"timeframe": "1h", "lot": 1, "max_candle_pct": 1.0, "trail_pct": 0.5, "rr": 4.0},
    "FILUSD": {"timeframe": "1h", "lot": 1, "max_candle_pct": 1.0, "trail_pct": 0.5, "rr": 4.0},
}

API_KEY = '4vtWGaF4x4LWleMfoj1ztriQp7rweE'
API_SECRET = 'dsuv5MuOGueu7OKXBo0U6CFCHryeEgujn3l7YD5rb5ibsWKDMRVU0BrQDhmW'

# Global tracking state for Dashboard
bot_states = {coin: {"status": "Monitoring", "pnl": 0.0, "entry_time": "-", "exit_time": "-", "last_price": 0.0} for coin in CONFIG}

ist = pytz.timezone('Asia/Kolkata')

app = Flask(__name__)

def get_ist_time():
    return datetime.now(ist).strftime('%d-%b-%Y %I:%M:%S %p')

def fetch_candles(symbol, timeframe):
    try:
        url = f"https://api.delta.exchange/v2/history/candles?resolution={timeframe}&symbol={symbol}"
        response = requests.get(url, timeout=10)
        data = response.json()
        if data.get("success") and "result" in data:
            df = pd.DataFrame(data["result"], columns=["time", "open", "high", "low", "close", "volume"])
            df = df.astype({"open": float, "high": float, "low": float, "close": float})
            return df
    except Exception as e:
        print(f"❌ Error fetching candles for {symbol}: {e}", flush=True)
    return None

def run_strategy(symbol):
    conf = CONFIG[symbol]
    print(f"🚀 Started worker thread for {symbol} | Timeframe: {conf['timeframe']} | Lot: {conf['lot']}", flush=True)
    
    while True:
        try:
            df = fetch_candles(symbol, conf['timeframe'])
            if df is not None and len(df) > 2:
                closed_candle = df.iloc[-2]
                live_price = df.iloc[-1]['close']
                
                bot_states[symbol]["last_price"] = live_price
                
                c_high = closed_candle['high']
                c_low = closed_candle['low']
                c_range = c_high - c_low
                c_price = closed_candle['close']
                
                range_pct = (c_range / c_price) * 100
                
                if range_pct <= conf['max_candle_pct']:
                    bot_states[symbol]["status"] = f"Setup Active (High: {c_high}, Low: {c_low})"
                    print(f"📊 [{symbol}] Valid Candle Found! Range %: {round(range_pct, 2)}% | High: {c_high} | Low: {c_low}", flush=True)
                    
                    while True:
                        time.sleep(5)
                        live_df = fetch_candles(symbol, conf['timeframe'])
                        if live_df is not None:
                            curr_price = live_df.iloc[-1]['close']
                            bot_states[symbol]["last_price"] = curr_price
                            
                            if curr_price > c_high:
                                entry_t = get_ist_time()
                                bot_states[symbol]["entry_time"] = entry_t
                                bot_states[symbol]["status"] = "BUY Executed (Trailing Active)"
                                print(f"🟢 [{symbol}] BUY Triggered at {curr_price} | Time: {entry_t}", flush=True)
                                # TODO: Place Buy Order & Trailing SL logic here
                                break
                            elif curr_price < c_low:
                                entry_t = get_ist_time()
                                bot_states[symbol]["entry_time"] = entry_t
                                bot_states[symbol]["status"] = "SELL Executed (Trailing Active)"
                                print(f"🔴 [{symbol}] SELL Triggered at {curr_price} | Time: {entry_t}", flush=True)
                                # TODO: Place Sell Order & Trailing SL logic here
                                break
        except Exception as e:
            print(f"❌ Error in strategy loop for {symbol}: {e}", flush=True)
            
        time.sleep(30)

# ================= FLASK DASHBOARD =================
@app.route("/")
def dashboard():
    html = """
    <html>
    <head>
        <title>Volatility Bot Dashboard</title>
        <meta http-equiv="refresh" content="10">
        <style>
            body { font-family: Arial, sans-serif; background: #121212; color: #fff; padding: 20px; }
            table { width: 100%; border-collapse: collapse; margin-top: 20px; }
            th, td { border: 1px solid #333; padding: 12px; text-align: center; }
            th { background: #1f1f1f; }
            tr:nth-child(even) { background: #181818; }
        </style>
    </head>
    <body>
        <h1>📈 Volatility Breakout Bot Dashboard (IST)</h1>
        <table>
            <tr>
                <th>Coin</th>
                <th>Last Price</th>
                <th>Status</th>
                <th>Entry Time (IST)</th>
                <th>Exit Time (IST)</th>
                <th>Live PnL</th>
            </tr>
            {% for coin, state in states.items() %}
            <tr>
                <td><b>{{ coin }}</b></td>
                <td>{{ state.last_price }}</td>
                <td>{{ state.status }}</td>
                <td>{{ state.entry_time }}</td>
                <td>{{ state.exit_time }}</td>
                <td style="color: {% if state.pnl >= 0 %}#4CAF50{% else %}#F44336{% endif %};">{{ state.pnl }}</td>
            </tr>
            {% endfor %}
        </table>
    </body>
    </html>
    """
    return render_template_string(html, states=bot_states)

@app.route("/ping")
def ping():
    return jsonify({"status": "alive", "time": get_ist_time()}), 200

# ================= MAIN ENTRY =================
if __name__ == "__main__":
    for coin in CONFIG:
        t = threading.Thread(target=run_strategy, args=(coin,), daemon=True)
        t.start()
    
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
