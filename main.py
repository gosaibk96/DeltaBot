import hmac
import hashlib
import time
import json
import requests
import os
import threading
from datetime import datetime
import pytz

# ============================================================
# GLOBAL SETTINGS (shared across all coins)
# ============================================================

API_KEY = "4vtWGaF4x4LWleMfoj1ztriQp7rweE"
API_SECRET = "dsuv5MuOGueu7OKXBo0U6CFCHryeEgujn3l7YD5rb5ibsWKDMRVU0BrQDhmW"

BASE_URL = "https://api.india.delta.exchange"

POLL_INTERVAL = 2                    # Seconds between each live price/position check
STOP_TRIGGER_METHOD = "mark_price"   # mark_price / last_traded_price / spot_price

RESOLUTION_SECONDS = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600,
    "1d": 86400, "1w": 604800
}

# ============================================================
# PER-COIN SETTINGS - Each coin has its own fully independent configuration.
# ============================================================

SYMBOLS = {
    "DOTUSD": {
        "product_id": 15304,
        "quantity": 10,
        "candle_resolution": "15m",
        "narrow_range_pct": 0.5,
        "rr_ratio": 4,
        "trail_trigger_pct": 0.1,
        "trail_step_pct": 0.1,
    },
    "XRPUSD": {
        "product_id": 14969,
        "quantity": 5,
        "candle_resolution": "1h",
        "narrow_range_pct": 0.5,
        "rr_ratio": 4,
        "trail_trigger_pct": 0.1,
        "trail_step_pct": 0.1,
    },
    "GRAMUSD": {
        "product_id": 141650,
        "quantity": 5,
        "candle_resolution": "1h",
        "narrow_range_pct": 0.5,
        "rr_ratio": 4,
        "trail_trigger_pct": 0.1,
        "trail_step_pct": 0.1,
    },
    "PIEVERSEUSD": {
        "product_id": 131978,
        "quantity": 5,
        "candle_resolution": "1h",
        "narrow_range_pct": 0.5,
        "rr_ratio": 4,
        "trail_trigger_pct": 0.1,
        "trail_step_pct": 0.1,
    },
    "RIVERUSD": {
        "product_id": 115664,
        "quantity": 5,
        "candle_resolution": "1h",
        "narrow_range_pct": 0.5,
        "rr_ratio": 4,
        "trail_trigger_pct": 0.1,
        "trail_step_pct": 0.1,
    },
    "MUSD": {
        "product_id": 84925,
        "quantity": 5,
        "candle_resolution": "1h",
        "narrow_range_pct": 0.5,
        "rr_ratio": 4,
        "trail_trigger_pct": 0.1,
        "trail_step_pct": 0.1,
    },
    "ZROUSD": {
        "product_id": 26457,
        "quantity": 5,
        "candle_resolution": "1h",
        "narrow_range_pct": 0.5,
        "rr_ratio": 4,
        "trail_trigger_pct": 0.1,
        "trail_step_pct": 0.1,
    },
    "FILUSD": {
        "product_id": 19617,
        "quantity": 5,
        "candle_resolution": "1h",
        "narrow_range_pct": 0.5,
        "rr_ratio": 4,
        "trail_trigger_pct": 0.1,
        "trail_step_pct": 0.1,
    },
}

bot_states = {sym: {"status": "Initializing", "last_price": 0.0, "entry": "-", "sl": "-", "tp": "-", "trail_level": 0} for sym in SYMBOLS}

ist = pytz.timezone('Asia/Kolkata')
app = __import__('flask').Flask(__name__)

def get_ist_time():
    return datetime.now(ist).strftime('%d-%b-%Y %I:%M:%S %p')

def get_candle_seconds(resolution):
    return RESOLUTION_SECONDS.get(resolution, 3600)

# ==================== SIGNATURE HELPER ====================
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
        "User-Agent": "render-multi-breakout-bot",
        "Content-Type": "application/json",
    }

# ==================== CANDLE BOUNDARY HELPERS ====================
def get_next_candle_close_time(resolution):
    candle_seconds = get_candle_seconds(resolution)
    now = time.time()
    return (int(now) // candle_seconds + 1) * candle_seconds

# ==================== MARKET DATA FUNCTIONS ====================
def fetch_candle_by_start_time(symbol, resolution, expected_start_time):
    candle_seconds = get_candle_seconds(resolution)
    end_ts = int(time.time())
    start_ts = expected_start_time - (candle_seconds * 3)
    path = "/v2/history/candles"
    url = BASE_URL + path
    params = {
        "resolution": resolution,
        "symbol": symbol,
        "start": start_ts,
        "end": end_ts,
    }
    try:
        resp = requests.get(url, params=params, timeout=(3, 10))
        data = resp.json()
        if data.get("success"):
            for c in data.get("result", []):
                if int(c["time"]) == int(expected_start_time):
                    return c
        return None
    except Exception as e:
        print(f"[{get_ist_time()}][{symbol}] Error fetching candles: {e}", flush=True)
        return None

def get_mark_price(symbol):
    path = f"/v2/tickers/{symbol}"
    url = BASE_URL + path
    try:
        resp = requests.get(url, timeout=(3, 10))
        data = resp.json()
        if data.get("success"):
            return float(data["result"]["mark_price"])
        return None
    except Exception as e:
        print(f"[{get_ist_time()}][{symbol}] Error fetching ticker: {e}", flush=True)
        return None

def get_position_size(product_id):
    method = "GET"
    path = "/v2/positions"
    query_string = f"?product_id={product_id}"
    url = BASE_URL + path
    headers = get_headers(method, path, query_string)
    try:
        resp = requests.get(
            url, params={"product_id": product_id}, headers=headers, timeout=(3, 10)
        )
        data = resp.json()
        if data.get("success"):
            result = data.get("result")
            if result and "size" in result and result["size"] is not None:
                return int(result["size"])
        return 0
    except Exception as e:
        print(f"[{get_ist_time()}] Error fetching position for product_id {product_id}: {e}", flush=True)
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
        print(f"[{get_ist_time()}] Error fetching order: {e}", flush=True)
        return None

# ==================== ORDER FUNCTIONS ====================
def place_market_order(product_id, side, size):
    method = "POST"
    path = "/v2/orders"
    url = BASE_URL + path
    payload_dict = {
        "product_id": product_id,
        "size": size,
        "side": side,
        "order_type": "market_order",
    }
    payload = json.dumps(payload_dict)
    headers = get_headers(method, path, "", payload)
    try:
        resp = requests.post(url, data=payload, headers=headers, timeout=(3, 10))
        return resp.json()
    except Exception as e:
        print(f"[{get_ist_time()}] Error placing market order: {e}", flush=True)
        return None

def get_average_fill_price(order_response):
    if not order_response or not order_response.get("success"):
        return None, None

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

    return (float(fill_price) if fill_price else None), order_id

def place_bracket_sl_tp(product_id, stop_price, take_profit_price):
    method = "POST"
    path = "/v2/orders/bracket"
    url = BASE_URL + path
    payload_dict = {
        "product_id": product_id,
        "stop_loss_order": {
            "order_type": "market_order",
            "stop_price": str(stop_price),
        },
        "take_profit_order": {
            "order_type": "market_order",
            "stop_price": str(take_profit_price),
        },
        "bracket_stop_trigger_method": STOP_TRIGGER_METHOD,
    }
    payload = json.dumps(payload_dict)
    headers = get_headers(method, path, "", payload)
    try:
        resp = requests.post(url, data=payload, headers=headers, timeout=(3, 10))
        return resp.json()
    except Exception as e:
        print(f"[{get_ist_time()}] Error placing bracket order: {e}", flush=True)
        return None

def edit_bracket_stop_loss(order_id, product_id, new_sl_price):
    method = "PUT"
    path = "/v2/orders/bracket"
    url = BASE_URL + path
    payload_dict = {
        "id": order_id,
        "product_id": product_id,
        "bracket_stop_loss_price": str(new_sl_price),
    }
    payload = json.dumps(payload_dict)
    headers = get_headers(method, path, "", payload)
    try:
        resp = requests.put(url, data=payload, headers=headers, timeout=(3, 10))
        return resp.json()
    except Exception as e:
        print(f"[{get_ist_time()}] Error editing bracket stop-loss: {e}", flush=True)
        return None

# ==================== CANDLE EVALUATION ====================
def evaluate_closed_candle(symbol, resolution, narrow_range_pct, candle_start_time):
    candle = fetch_candle_by_start_time(symbol, resolution, candle_start_time)
    if not candle:
        print(f"[{get_ist_time()}][{symbol}] Warning: could not fetch closed candle data.", flush=True)
        return None

    high = float(candle["high"])
    low = float(candle["low"])
    if low <= 0:
        return None

    range_pct = (high - low) / low * 100
    print(f"[{get_ist_time()}][{symbol}] Closed candle -> High={high}, Low={low}, Range%={range_pct:.4f}", flush=True)

    if range_pct < narrow_range_pct:
        print(f"[{get_ist_time()}][{symbol}] -> Narrow-range condition met. Reference set.", flush=True)
        bot_states[symbol]["status"] = f"Setup Active (H:{high}, L:{low})"
        return {"high": high, "low": low}
    else:
        print(f"[{get_ist_time()}][{symbol}] -> Range too wide. No reference set.", flush=True)
        bot_states[symbol]["status"] = f"Monitoring (Range: {round(range_pct, 2)}% > {narrow_range_pct}%)"
        return None

# ==================== TRADE EXECUTION ====================
def execute_breakout_trade(symbol, cfg, side, reference_candle):
    product_id = cfg["product_id"]
    quantity = cfg["quantity"]
    rr_ratio = cfg["rr_ratio"]

    print(f"[{get_ist_time()}][{symbol}] {side.upper()} breakout signal triggered", flush=True)
    order_resp = place_market_order(product_id, side, quantity)
    print(f"[{symbol}] Order response:", order_resp, flush=True)

    entry_price, order_id = get_average_fill_price(order_resp)
    if entry_price is None:
        print(f"[{symbol}] Could not fetch entry price. SL/TP not placed. Check manually!", flush=True)
        return None

    if side == "buy":
        sl_price = reference_candle["low"]
        sl_distance = entry_price - sl_price
        tp_price = entry_price + (rr_ratio * sl_distance)
    else:
        sl_price = reference_candle["high"]
        sl_distance = sl_price - entry_price
        tp_price = entry_price - (rr_ratio * sl_distance)

    print(f"🎯 [{get_ist_time()}][{symbol}] TRADE EXECUTED [{side.upper()}] -> Entry: {entry_price} | SL: {sl_price} | TP: {tp_price}", flush=True)
    
    bot_states[symbol]["status"] = f"{side.upper()} Executed"
    bot_states[symbol]["entry"] = entry_price
    bot_states[symbol]["sl"] = sl_price
    bot_states[symbol]["tp"] = tp_price
    bot_states[symbol]["trail_level"] = 0

    bracket_resp = place_bracket_sl_tp(product_id, sl_price, tp_price)
    print(f"[{symbol}] Bracket response:", bracket_resp, flush=True)

    return {
        "side": side,
        "entry_price": entry_price,
        "sl_price": sl_price,
        "tp_price": tp_price,
        "order_id": order_id,
        "trail_level": 0,
    }

def update_trailing_sl(symbol, cfg, position_state, current_price):
    product_id = cfg["product_id"]
    trigger_pct = cfg["trail_trigger_pct"]
    step_pct = cfg["trail_step_pct"]

    entry_price = position_state["entry_price"]
    step_amount = entry_price * (step_pct / 100)
    trigger_amount = entry_price * (trigger_pct / 100)

    if trigger_amount <= 0:
        return

    if position_state["side"] == "buy":
        favorable_move = current_price - entry_price
        new_level = int(favorable_move // trigger_amount)
        if new_level > position_state["trail_level"] and new_level >= 1:
            new_sl = entry_price + (new_level - 1) * step_amount
            if new_sl > position_state["sl_price"]:
                resp = edit_bracket_stop_loss(position_state["order_id"], product_id, new_sl)
                print(f"📈 [{get_ist_time()}][{symbol}] Trailing SL updated -> {new_sl} | resp: {resp}", flush=True)
                position_state["sl_price"] = new_sl
                position_state["trail_level"] = new_level
                bot_states[symbol]["sl"] = new_sl
                bot_states[symbol]["trail_level"] = new_level
    else:
        favorable_move = entry_price - current_price
        new_level = int(favorable_move // trigger_amount)
        if new_level > position_state["trail_level"] and new_level >= 1:
            new_sl = entry_price - (new_level - 1) * step_amount
            if new_sl < position_state["sl_price"]:
                resp = edit_bracket_stop_loss(position_state["order_id"], product_id, new_sl)
                print(f"📉 [{get_ist_time()}][{symbol}] Trailing SL updated -> {new_sl} | resp: {resp}", flush=True)
                position_state["sl_price"] = new_sl
                position_state["trail_level"] = new_level
                bot_states[symbol]["sl"] = new_sl
                bot_states[symbol]["trail_level"] = new_level

# ==================== BACKGROUND BOT LOOP ====================
def background_bot_loop():
    print("Starting multi-symbol narrow-range breakout bot worker on Render...", flush=True)
    state = {}
    for symbol, cfg in SYMBOLS.items():
        next_close = get_next_candle_close_time(cfg["candle_resolution"])
        state[symbol] = {
            "next_close_time": next_close,
            "reference_candle": None,
            "position": None,
        }
        bot_states[symbol]["status"] = "Waiting for candle close"
        print(f"[{symbol}] TF={cfg['candle_resolution']} | Qty={cfg['quantity']} | Next Close in ~{next_close - time.time():.0f}s", flush=True)

    while True:
        try:
            now = time.time()

            for symbol, cfg in SYMBOLS.items():
                sym_state = state[symbol]
                product_id = cfg["product_id"]
                resolution = cfg["candle_resolution"]
                candle_seconds = get_candle_seconds(resolution)

                if now >= sym_state["next_close_time"] + 2:
                    if sym_state["position"] is None:
                        closed_start = sym_state["next_close_time"] - candle_seconds
                        sym_state["reference_candle"] = evaluate_closed_candle(
                            symbol, resolution, cfg["narrow_range_pct"], closed_start
                        )
                    sym_state["next_close_time"] += candle_seconds

                if sym_state["position"] is not None:
                    size = get_position_size(product_id)
                    if size == 0:
                        print(f"[{get_ist_time()}][{symbol}] Position closed (SL/TP hit).", flush=True)
                        sym_state["position"] = None
                        bot_states[symbol]["status"] = "Flat / Monitoring"
                        bot_states[symbol]["entry"] = "-"
                        bot_states[symbol]["sl"] = "-"
                        bot_states[symbol]["tp"] = "-"
                        bot_states[symbol]["trail_level"] = 0
                    else:
                        price = get_mark_price(symbol)
                        if price is not None:
                            bot_states[symbol]["last_price"] = price
                            update_trailing_sl(symbol, cfg, sym_state["position"], price)

                elif sym_state["reference_candle"] is not None:
                    price = get_mark_price(symbol)
                    if price is not None:
                        bot_states[symbol]["last_price"] = price
                        ref = sym_state["reference_candle"]
                        if price > ref["high"]:
                            result = execute_breakout_trade(symbol, cfg, "buy", ref)
                            sym_state["position"] = result
                            sym_state["reference_candle"] = None
                        elif price < ref["low"]:
                            result = execute_breakout_trade(symbol, cfg, "sell", ref)
                            sym_state["position"] = result
                            sym_state["reference_candle"] = None
                else:
                    price = get_mark_price(symbol)
                    if price is not None:
                        bot_states[symbol]["last_price"] = price

            time.sleep(POLL_INTERVAL)

        except Exception as e:
            print(f"[{get_ist_time()}] Main loop error: {e}", flush=True)
            time.sleep(POLL_INTERVAL)

@app.route("/")
def dashboard():
    rows = ""
    for sym, st in bot_states.items():
        rows += f"<tr><td><b>{sym}</b></td><td>{st['status']}</td><td>{st['last_price']}</td><td>{st['entry']}</td><td>{st['sl']}</td><td>{st['tp']}</td><td>{st['trail_level']}</td></tr>"
    
    return f"""
    <html><head><title>Multi-Coin TSL Bot Dashboard</title><meta http-equiv="refresh" content="5">
    <style>body{{background:#121212;color:#fff;font-family:Arial;padding:20px;}}table{{width:100%;border-collapse:collapse;margin-top:20px;}}th,td{{border:1px solid #333;padding:10px;text-align:center;}}th{{background:#1f1f1f;}}</style>
    </head><body><h1>📈 Multi-Coin Breakout Bot with TSL</h1><table>
    <tr><th>Symbol</th><th>Status</th><th>Last Price</th><th>Entry</th><th>SL</th><th>TP</th><th>Trail Level</th></tr>
    {rows}
    </table></body></html>
    """

@app.route("/ping")
def ping():
    return {"status": "alive", "time": get_ist_time()}, 200

if __name__ == "__main__":
    threading.Thread(target=background_bot_loop, daemon=True).start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
