import time
import threading
from datetime import datetime
import os
import pytz
import pandas as pd
import requests
import hmac
import hashlib
import json

# ================= CONFIGURATION =================
API_KEY = "4vtWGaF4x4LWleMfoj1ztriQp7rweE"
API_SECRET = "dsuv5MuOGueu7OKXBo0U6CFCHryeEgujn3l7YD5rb5ibsWKDMRVU0BrQDhmW"
BASE_URL = "https://api.india.delta.exchange"
SYMBOL = "DOTUSD"
PRODUCT_ID = 15304 
QUANTITY = 1 
CANDLE_RESOLUTION = "5m" 
RESOLUTION_SECONDS = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800, 
    "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600, "1d": 86400, "1w": 604800
}
NARROW_RANGE_PCT = 0.5 
RR_RATIO = 4 
POLL_INTERVAL = 2 
STOP_TRIGGER_METHOD = "mark_price"

bot_state = {
    "status": "Initializing", 
    "last_price": 0.0, 
    "entry_time": "-", 
    "exit_time": "-", 
    "pnl": 0.0
}

ist = pytz.timezone('Asia/Kolkata')
app = __import__('flask').Flask(__name__)

def get_ist_time():
    return datetime.now(ist).strftime('%d-%b-%Y %I:%M:%S %p')

def get_candle_seconds():
    return RESOLUTION_SECONDS.get(CANDLE_RESOLUTION, 300)

def generate_signature(secret, message):
    message = bytes(message, "utf-8")
    secret = bytes(secret, "utf-8")
    return hmac.new(secret, message, hashlib.sha256).hexdigest()

def get_headers(method, path, query_string="", payload=""):
    timestamp = str(int(time.time()))
    signature_data = method + timestamp + path + query_string + payload
    signature = generate_signature(API_SECRET, signature_data)
    return {
        "api-key": API_KEY,
        "timestamp": timestamp,
        "signature": signature,
        "User-Agent": "render-breakout-bot",
        "Content-Type": "application/json",
    }

def get_last_closed_candle():
    candle_seconds = get_candle_seconds()
    end_ts = int(time.time())
    start_ts = end_ts - (candle_seconds * 5)
    path = "/v2/history/candles"
    url = BASE_URL + path
    params = {"resolution": CANDLE_RESOLUTION, "symbol": SYMBOL, "start": start_ts, "end": end_ts}
    try:
        resp = requests.get(url, params=params, timeout=(3, 10))
        data = resp.json()
        if data.get("success") and len(data.get("result", [])) >= 2:
            candles = sorted(data["result"], key=lambda c: c["time"])
            return candles[-2]
    except Exception as e:
        print("Error fetching candles:", e, flush=True)
    return None

def get_mark_price():
    path = f"/v2/tickers/{SYMBOL}"
    url = BASE_URL + path
    try:
        resp = requests.get(url, timeout=(3, 10))
        data = resp.json()
        if data.get("success"):
            return float(data["result"]["mark_price"])
    except Exception as e:
        print("Error fetching ticker:", e, flush=True)
    return None

def get_position_size():
    method = "GET"
    path = "/v2/positions"
    query_string = f"?product_id={PRODUCT_ID}"
    url = BASE_URL + path
    headers = get_headers(method, path, query_string)
    try:
        resp = requests.get(url, params={"product_id": PRODUCT_ID}, headers=headers, timeout=(3, 10))
        data = resp.json()
        if data.get("success"):
            result = data.get("result")
            if result and "size" in result and result["size"] is not None:
                return int(result["size"])
    except Exception as e:
        print("Error fetching position:", e, flush=True)
    return 0

def get_order_by_id(order_id):
    method = "GET"
    path = f"/v2/orders/{order_id}"
    url = BASE_URL + path
    headers = get_headers(method, path)
    try:
        resp = requests.get(url, headers=headers, timeout=(3, 10))
        return resp.json()
    except Exception as e:
        print("Error fetching order:", e, flush=True)
    return None

def place_market_order(side, size):
    method = "POST"
    path = "/v2/orders"
    url = BASE_URL + path
    payload_dict = {"product_id": PRODUCT_ID, "size": size, "side": side, "order_type": "market_order"}
    payload = json.dumps(payload_dict)
    headers = get_headers(method, path, "", payload)
    try:
        resp = requests.post(url, data=payload, headers=headers, timeout=(3, 10))
        return resp.json()
    except Exception as e:
        print("Error placing market order:", e, flush=True)
    return None

def get_average_fill_price(order_response):
    if not order_response or not order_response.get("success"):
        return None
    order = order_response.get("result", {})
    fill_price = order.get("average_fill_price")
    order_id = order.get("id")
    retries = 5
    while fill_price is None and retries > 0 and order_id:
        time.sleep(0.5)
        fresh = get_order_by_id(order_id)
        if fresh and fresh.get("success"):
            fill_price = fresh.get("result", {}).get("average_fill_price")
        retries -= 1
    return float(fill_price) if fill_price else None

def place_bracket_sl_tp(stop_price, take_profit_price):
    method = "POST"
    path = "/v2/orders/bracket"
    url = BASE_URL + path
    payload_dict = {
        "product_id": PRODUCT_ID,
        "stop_loss_order": {"order_type": "market_order", "stop_price": str(stop_price)},
        "take_profit_order": {"order_type": "market_order", "stop_price": str(take_profit_price)},
        "bracket_stop_trigger_method": STOP_TRIGGER_METHOD,
    }
    payload = json.dumps(payload_dict)
    headers = get_headers(method, path, "", payload)
    try:
        resp = requests.post(url, data=payload, headers=headers, timeout=(3, 10))
        return resp.json()
    except Exception as e:
        print("Error placing bracket order:", e, flush=True)
    return None

def background_bot_loop():
    print("Starting narrow-range breakout bot worker...", flush=True)
    reference_candle = None
    last_candle_time = None
    
    while True:
        try:
            candle = get_last_closed_candle()
            if candle and candle.get("time") != last_candle_time:
                last_candle_time = candle["time"]
                high = float(candle["high"])
                low = float(candle["low"])
                if low > 0:
                    range_pct = (high - low) / low * 100
                    if range_pct < NARROW_RANGE_PCT:
                        reference_candle = {"high": high, "low": low}
                        bot_state["status"] = f"Setup Active (H:{high}, L:{low})"
                        print(f"[{get_ist_time()}] New narrow-range candle -> High={high}, Low={low}, Range%={range_pct:.4f}", flush=True)

            if reference_candle:
                position_size = get_position_size()
                if position_size == 0:
                    price = get_mark_price()
                    if price is not None:
                        bot_state["last_price"] = price
                        if price > reference_candle["high"]:
                            print(f"[{get_ist_time()}] BUY breakout signal at {price}", flush=True)
                            order_resp = place_market_order("buy", QUANTITY)
                            entry_price = get_average_fill_price(order_resp)
                            if entry_price is not None:
                                bot_state["entry_time"] = get_ist_time()
                                bot_state["status"] = "BUY Executed"
                                sl_price = reference_candle["low"]
                                sl_distance = entry_price - sl_price
                                tp_price = entry_price + (RR_RATIO * sl_distance)
                                place_bracket_sl_tp(sl_price, tp_price)
                            reference_candle = None
                        elif price < reference_candle["low"]:
                            print(f"[{get_ist_time()}] SELL breakout signal at {price}", flush=True)
                            order_resp = place_market_order("sell", QUANTITY)
                            entry_price = get_average_fill_price(order_resp)
                            if entry_price is not None:
                                bot_state["entry_time"] = get_ist_time()
                                bot_state["status"] = "SELL Executed"
                                sl_price = reference_candle["high"]
                                sl_distance = sl_price - entry_price
                                tp_price = entry_price - (RR_RATIO * sl_distance)
                                place_bracket_sl_tp(sl_price, tp_price)
                            reference_candle = None
            else:
                price = get_mark_price()
                if price:
                    bot_state["last_price"] = price

            time.sleep(POLL_INTERVAL)
        except Exception as e:
            print("Main loop error:", e, flush=True)
            time.sleep(POLL_INTERVAL)

@app.route("/")
def dashboard():
    return f"""
    <html><head><title>Delta Bot Dashboard</title><meta http-equiv="refresh" content="5">
    <style>body{{background:#121212;color:#fff;font-family:Arial;padding:20px;}}table{{width:100%;border-collapse:collapse;margin-top:20px;}}th,td{{border:1px solid #333;padding:12px;text-align:center;}}th{{background:#1f1f1f;}}</style>
    </head><body><h1>📈 Delta Breakout Bot ({SYMBOL})</h1><table>
    <tr><th>Status</th><th>Last Price</th><th>Entry Time</th><th>Exit Time</th></tr>
    <tr><td>{bot_state['status']}</td><td>{bot_state['last_price']}</td><td>{bot_state['entry_time']}</td><td>{bot_state['exit_time']}</td></tr>
    </table></body></html>
    """

@app.route("/ping")
def ping():
    return {"status": "alive", "time": get_ist_time()}, 200

if __name__ == "__main__":
    threading.Thread(target=background_bot_loop, daemon=True).start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
