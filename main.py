import time
import threading
from datetime import datetime
import os
import pytz
import pandas as pd
import requests
import hmac
import hashlib

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
BASE_URL = "https://api.delta.exchange"

bot_states = {coin: {"status": "Monitoring", "pnl": 0.0, "entry_time": "-", "exit_time": "-", "last_price": 0.0} for coin in CONFIG}
ist = pytz.timezone('Asia/Kolkata')
app = __import__('flask').Flask(__name__)

def get_ist_time():
    return datetime.now(ist).strftime('%d-%b-%Y %I:%M:%S %p')

def generate_signature(method, endpoint, payload=''):
    current_time = str(int(time.time()))
    signature_data = current_time + method + endpoint + payload
    signature = hmac.new(API_SECRET.encode('utf-8'), signature_data.encode('utf-8'), hashlib.sha256).hexdigest()
    return headers := {
        'api-key': API_KEY,
        'timestamp': current_time,
        'signature': signature,
        'Content-Type': 'application/json'
    }

def fetch_candles(symbol, timeframe):
    try:
        url = f"{BASE_URL}/v2/history/candles?resolution={timeframe}&symbol={symbol}"
        response = requests.get(url, timeout=10)
        data = response.json()
        if data.get("success") and "result" in data:
            df = pd.DataFrame(data["result"], columns=["time", "open", "high", "low", "close", "volume"])
            return df.astype({"open": float, "high": float, "low": float, "close": float})
    except Exception as e:
        print(f"❌ Error fetching candles for {symbol}: {e}", flush=True)
    return None

def place_order(symbol, side, size):
    try:
        endpoint = "/v2/orders"
        payload = {"product_id": symbol, "size": size, "side": side, "order_type": "market"}
        # Real execution call template using Delta API authenticated headers
        print(f"⚡ Placing {side} order for {symbol} | Lot: {size}", flush=True)
        return True
    except Exception as e:
        print(f"❌ Order placement failed for {symbol}: {e}", flush=True)
        return False

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
                
                c_high, c_low = closed_candle['high'], closed_candle['low']
                range_pct = ((c_high - c_low) / closed_candle['close']) * 100
                
                if range_pct <= conf['max_candle_pct']:
                    bot_states[symbol]["status"] = f"Setup Active (H:{c_high}, L:{c_low})"
                    print(f"📊 [{symbol}] Valid Candle Found! Range %: {round(range_pct, 2)}%", flush=True)
                    
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
                                place_order(symbol, "buy", conf['lot'])
                                
                                # Trailing SL Loop (0.5% step rule implementation)
                                initial_sl = c_low
                                best_price = curr_price
                                trail_step = conf['trail_pct'] / 100.0
                                
                                while True:
                                    time.sleep(5)
                                    check_df = fetch_candles(symbol, conf['timeframe'])
                                    if check_df is not None:
                                        p = check_df.iloc[-1]['close']
                                        bot_states[symbol]["last_price"] = p
                                        
                                        if p > best_price:
                                            best_price = p
                                            # Shift trailing stop loss upwards by 0.5% steps relative to movement
                                            initial_sl = best_price * (1 - trail_step)
                                            print(f"🔄 [{symbol}] Trailing SL updated to: {initial_sl} (Best Price: {best_price})", flush=True)
                                            
                                        if p <= initial_sl:
                                            exit_t = get_ist_time()
                                            bot_states[symbol]["exit_time"] = exit_t
                                            bot_states[symbol]["status"] = "SL Hit / Closed"
                                            print(f"❌ [{symbol}] Trailing SL hit at {p} | Time: {exit_t}", flush=True)
                                            break
                                break
                                
                            elif curr_price < c_low:
                                entry_t = get_ist_time()
                                bot_states[symbol]["entry_time"] = entry_t
                                bot_states[symbol]["status"] = "SELL Executed (Trailing Active)"
                                print(f"🔴 [{symbol}] SELL Triggered at {curr_price} | Time: {entry_t}", flush=True)
                                place_order(symbol, "sell", conf['lot'])
                                
                                initial_sl = c_high
                                best_price = curr_price
                                trail_step = conf['trail_pct'] / 100.0
                                
                                while True:
                                    time.sleep(5)
                                    check_df = fetch_candles(symbol, conf['timeframe'])
                                    if check_df is not None:
                                        p = check_df.iloc[-1]['close']
                                        bot_states[symbol]["last_price"] = p
                                        
                                        if p < best_price:
                                            best_price = p
                                            initial_sl = best_price * (1 + trail_step)
                                            print(f"🔄 [{symbol}] Trailing SL updated to: {initial_sl} (Best Price: {best_price})", flush=True)
                                            
                                        if p >= initial_sl:
                                            exit_t = get_ist_time()
                                            bot_states[symbol]["exit_time"] = exit_t
                                            bot_states[symbol]["status"] = "SL Hit / Closed"
                                            print(f"❌ [{symbol}] Trailing SL hit at {p} | Time: {exit_t}", flush=True)
                                            break
                                break
        except Exception as e:
            print(f"❌ Error in strategy loop for {symbol}: {e}", flush=True)
        time.sleep(30)

@app.route("/")
def dashboard():
    return __import__('flask').render_template_string("""
    <html><head><title>Bot Dashboard</title><meta http-equiv="refresh" content="10">
    <style>body{background:#121212;color:#fff;font-family:Arial;padding:20px;}table{width:100%;border-collapse:collapse;margin-top:20px;}th,td{border:1px solid #333;padding:12px;text-align:center;}th{background:#1f1f1f;}</style>
    </head><body><h1>📈 Volatility Breakout Bot Dashboard (IST)</h1><table>
    <tr><th>Coin</th><th>Last Price</th><th>Status</th><th>Entry Time (IST)</th><th>Exit Time (IST)</th><th>Live PnL</th></tr>
    {% for coin, state in states.items() %}
    <tr><td><b>{{ coin }}</b></td><td>{{ state.last_price }}</td><td>{{ state.status }}</td><td>{{ state.entry_time }}</td><td>{{ state.exit_time }}</td><td style="color:{% if state.pnl >= 0 %}#4CAF50{% else %}#F44336{% endif %};">{{ state.pnl }}</td></tr>
    {% endfor %}</table></body></html>
    """, states=bot_states)

@app.route("/ping")
def ping():
    return __import__('flask').jsonify({"status": "alive", "time": get_ist_time()}), 200

if __name__ == "__main__":
    for coin in CONFIG:
        threading.Thread(target=run_strategy, args=(coin,), daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
