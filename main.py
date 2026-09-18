import hmac
import hashlib
import time
import json
import requests
import os
import threading
import websocket
from decimal import Decimal
from datetime import datetime
import pytz
from flask import Flask, render_template_string

API_KEY = os.environ.get("API_KEY", "your_api_key_here")
API_SECRET = os.environ.get("API_SECRET", "your_api_secret_here")

BASE_URL = "https://api.india.delta.exchange"
WS_URL = "wss://socket.india.delta.exchange"

CANDLE_RANGE_MIN_POINTS = 0
RR_RATIO = 4

STOP_TRIGGER_METHOD = "mark_price"

RESOLUTION_SECONDS = {"1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600, "1d": 86400, "1w": 604800}

CANDLE_WATCHER_TICK = 1
POSITION_WATCHER_INTERVAL = 2
CANDLE_FETCH_RETRY_WINDOW = 20
COOLDOWN_CANDLES = 3
WS_RECONNECT_DELAY = 3
PRICE_LOG_INTERVAL = 5

SYMBOLS_CONFIG = [
    {"symbol": "BTCUSD",     "product_id": 27,     "tick_size": 0.5,    "resolution": "15m", "quantity": 1, "range_points": 200,    "tsl_points": 200},
    {"symbol": "ETHUSD",     "product_id": 3136,   "tick_size": 0.05,   "resolution": "15m", "quantity": 1, "range_points": 8,      "tsl_points": 8},
    {"symbol": "XAUTUSD",    "product_id": 131253, "tick_size": 0.01,   "resolution": "15m", "quantity": 10, "range_points": 8,      "tsl_points": 8},
    {"symbol": "SLVONUSD",   "product_id": 124058, "tick_size": 0.01,   "resolution": "15m", "quantity": 5, "range_points": 0.20,      "tsl_points": 0.20},
    {"symbol": "XRPUSD",     "product_id": 14969,  "tick_size": 0.0001, "resolution": "15m", "quantity": 1, "range_points": 0.0065, "tsl_points": 0.0065},
    {"symbol": "NEARUSD",    "product_id": 16615,  "tick_size": 0.0001, "resolution": "1h", "quantity": 1, "range_points": 0.0150, "tsl_points": 0.0150},
    {"symbol": "SUIUSD",     "product_id": 17328,  "tick_size": 0.0001, "resolution": "1h", "quantity": 1, "range_points": 0.0035, "tsl_points": 0.0035},
    {"symbol": "EVAAUSD",    "product_id": 98745,  "tick_size": 0.0001, "resolution": "1h", "quantity": 1, "range_points": 0.0025, "tsl_points": 0.0025},
    {"symbol": "COAIUSD",    "product_id": 98572,  "tick_size": 0.0001, "resolution": "1h", "quantity": 1, "range_points": 0.0015, "tsl_points": 0.0015},
    {"symbol": "ASTERUSD",   "product_id": 96160,  "tick_size": 0.0001, "resolution": "1h", "quantity": 1, "range_points": 0.0035, "tsl_points": 0.0035},
    {"symbol": "MUSD",       "product_id": 84925,  "tick_size": 0.0001, "resolution": "1h", "quantity": 1, "range_points": 0.0060, "tsl_points": 0.0060},
    {"symbol": "VIRTUALUSD", "product_id": 54903,  "tick_size": 0.0001, "resolution": "1h", "quantity": 1, "range_points": 0.0030, "tsl_points": 0.0030},
    {"symbol": "ZROUSD",     "product_id": 26457,  "tick_size": 0.0001, "resolution": "1h", "quantity": 1, "range_points": 0.0055, "tsl_points": 0.0055},
    {"symbol": "RUNEUSD",    "product_id": 21522,  "tick_size": 0.0001, "resolution": "1h", "quantity": 1, "range_points": 0.0025, "tsl_points": 0.0025},
    {"symbol": "APTUSD",     "product_id": 20196,  "tick_size": 0.0001, "resolution": "1h", "quantity": 1, "range_points": 0.0030, "tsl_points": 0.0030},
    {"symbol": "FILUSD",     "product_id": 19617,  "tick_size": 0.0001, "resolution": "1h", "quantity": 1, "range_points": 0.0040, "tsl_points": 0.0040},
    {"symbol": "LDOUSD",     "product_id": 19616,  "tick_size": 0.0001, "resolution": "1h", "quantity": 1, "range_points": 0.0020, "tsl_points": 0.0020},
    {"symbol": "ONDOUSD",    "product_id": 19300,  "tick_size": 0.0001, "resolution": "1h", "quantity": 1, "range_points": 0.0020, "tsl_points": 0.0020},
]

symbol_lookup = {cfg["symbol"]: cfg for cfg in SYMBOLS_CONFIG}
ist = pytz.timezone('Asia/Kolkata')
state_lock = threading.Lock()

state = {}
trade_logs = []

for cfg in SYMBOLS_CONFIG:
    state[cfg["symbol"]] = {
        "latest_price": None,
        "reference_candle": None,
        "position_open": False,
        "position_side": None,
        "entry_price": None,
        "entry_time": None,
        "cooldown_until": 0,
        "last_price_log_time": 0,
        "next_close_time": None,
        "pending_candle_start": None,
        "pending_deadline": None,
        "total_trades": 0,
        "wins": 0,
        "losses": 0,
        "net_pnl": 0.0
    }

def get_ist_time():
    return datetime.now(ist).strftime('%Y-%m-%d %H:%M:%S')

def log(symbol, msg):
    print("[" + get_ist_time() + "][" + symbol + "] " + str(msg), flush=True)

def get_candle_seconds(resolution):
    return RESOLUTION_SECONDS.get(resolution, 3600)

def round_to_tick(price, tick_size):
    if tick_size <= 0:
        return price
    return round(price / tick_size) * tick_size

def format_price(value, tick_size):
    d = Decimal(str(tick_size))
    exponent = d.as_tuple().exponent
    decimals = -exponent if exponent < 0 else 0
    return "{:.{}f}".format(float(value), decimals)

def generate_signature(secret, message):
    message = bytes(message, "utf-8")
    secret = bytes(secret, "utf-8")
    return hmac.new(secret, message, hashlib.sha256).hexdigest()

def get_headers(method, path, query_string="", payload=""):
    timestamp = str(int(time.time()))
    signature_data = method + timestamp + path + query_string + payload
    signature = generate_signature(API_SECRET, signature_data)
    return {"api-key": API_KEY, "timestamp": timestamp, "signature": signature, "User-Agent": "render-multisymbol-breakout-bot", "Content-Type": "application/json"}

def get_next_candle_close_time(resolution):
    candle_seconds = get_candle_seconds(resolution)
    now = time.time()
    return (int(now) // candle_seconds + 1) * candle_seconds

def fetch_candle_by_start_time(resolution, symbol, expected_start_time):
    candle_seconds = get_candle_seconds(resolution)
    end_ts = int(time.time())
    start_ts = expected_start_time - (candle_seconds * 3)
    url = BASE_URL + "/v2/history/candles"
    params = {"resolution": resolution, "symbol": symbol, "start": start_ts, "end": end_ts}
    try:
        resp = requests.get(url, params=params, timeout=(3, 10))
        data = resp.json()
        if data.get("success"):
            for c in data.get("result", []):
                if int(c["time"]) == int(expected_start_time):
                    return c
        return None
    except Exception as e:
        log(symbol, "⚠️ Error fetching candles: " + str(e))
        return None

def get_position_size_and_entry(product_id):
    method = "GET"
    path = "/v2/positions"
    query_string = "?product_id=" + str(product_id)
    url = BASE_URL + path
    headers = get_headers(method, path, query_string)
    try:
        resp = requests.get(url, params={"product_id": product_id}, headers=headers, timeout=(3, 10))
        data = resp.json()
        if data.get("success"):
            result = data.get("result")
            if result and result.get("size") is not None:
                return int(result["size"]), result.get("entry_price")
        return 0, None
    except Exception as e:
        return 0, None

def get_order_by_id(order_id):
    method = "GET"
    path = "/v2/orders/" + str(order_id)
    url = BASE_URL + path
    headers = get_headers(method, path)
    try:
        resp = requests.get(url, headers=headers, timeout=(3, 10))
        return resp.json()
    except Exception as e:
        return None

def get_exit_reason_and_details(product_id):
    method = "GET"
    path = "/v2/orders/history"
    query_string = "?product_ids=" + str(product_id) + "&page_size=10"
    url = BASE_URL + path
    headers = get_headers(method, path, query_string)
    try:
        resp = requests.get(url, params={"product_ids": str(product_id), "page_size": 10}, headers=headers, timeout=(3, 10))
        data = resp.json()
        if data.get("success"):
            orders = data.get("result", [])
            closed_orders = [o for o in orders if o.get("state") == "closed"]
            if closed_orders:
                closed_orders.sort(key=lambda o: int(o.get("created_at", 0)), reverse=True)
                latest = closed_orders[0]
                stop_type = latest.get("stop_order_type")
                order_type = latest.get("order_type")
                fill_price = latest.get("average_fill_price") or latest.get("execution_price")
                realized_pnl = latest.get("realized_pnl") or 0.0
                
                if stop_type == "stop_loss_order" or "stop" in str(order_type).lower():
                    return "🛡️ TSL HIT", fill_price, float(realized_pnl)
                elif stop_type == "take_profit_order" or "profit" in str(order_type).lower():
                    return "🎯 TP HIT", fill_price, float(realized_pnl)
                else:
                    return "⚡ CLOSED", fill_price, float(realized_pnl)
        return "⚡ CLOSED", None, 0.0
    except Exception as e:
        return "⚡ CLOSED", None, 0.0

def place_entry_order_with_trailing_bracket(product_id, product_symbol, side, size, trail_amount_str, tp_price_str):
    method = "POST"
    path = "/v2/orders"
    url = BASE_URL + path
    payload_dict = {
        "product_id": product_id,
        "product_symbol": product_symbol,
        "size": size,
        "side": side,
        "order_type": "market_order",
        "bracket_trail_amount": trail_amount_str,
        "bracket_take_profit_price": tp_price_str,
        "bracket_stop_trigger_method": STOP_TRIGGER_METHOD,
    }
    payload = json.dumps(payload_dict)
    headers = get_headers(method, path, "", payload)
    try:
        resp = requests.post(url, data=payload, headers=headers, timeout=(3, 10))
        return resp.json()
    except Exception as e:
        return None

def emergency_close_position(product_id, product_symbol, side, quantity):
    close_side = "sell" if side == "buy" else "buy"
    method = "POST"
    path = "/v2/orders"
    url = BASE_URL + path
    payload_dict = {
        "product_id": product_id,
        "product_symbol": product_symbol,
        "size": quantity,
        "side": close_side,
        "order_type": "market_order",
        "reduce_only": "true",
    }
    payload = json.dumps(payload_dict)
    headers = get_headers(method, path, "", payload)
    try:
        resp = requests.post(url, data=payload, headers=headers, timeout=(3, 10))
        return resp.json()
    except Exception as e:
        return None

def get_average_fill_price(order_id, retries=8):
    fill_price = None
    while fill_price is None and retries > 0:
        time.sleep(0.5)
        fresh = get_order_by_id(order_id)
        if fresh and fresh.get("success"):
            fill_price = fresh.get("result", {}).get("average_fill_price")
        retries -= 1
    return float(fill_price) if fill_price else None

def evaluate_candle_data(cfg, candle):
    symbol = cfg["symbol"]
    max_range = cfg["range_points"]
    high = float(candle["high"])
    low = float(candle["low"])
    range_points = round(high - low, 8)
    qualifies = CANDLE_RANGE_MIN_POINTS <= range_points <= max_range

    with state_lock:
        current_price = state[symbol]["latest_price"]

    if qualifies:
        log(symbol, "✅ MATCH -> Range=" + str(range_points) + " pts. High=" + str(high) + ", Low=" + str(low) + " | Price=" + str(current_price))
        return {"high": high, "low": low}
    else:
        log(symbol, "⚠️ RANGE EXCEEDED -> Range=" + str(range_points) + " pts (Limit: " + str(max_range) + ")")
    return None

def execute_breakout_trade(cfg, side, trigger_price):
    symbol = cfg["symbol"]
    product_id = cfg["product_id"]
    tick_size = cfg["tick_size"]
    quantity = cfg["quantity"]
    tsl_points = cfg["tsl_points"]

    if side == "buy":
        tp_price = trigger_price + (RR_RATIO * tsl_points)
        raw_trail = -abs(tsl_points)
    else:
        tp_price = trigger_price - (RR_RATIO * tsl_points)
        raw_trail = abs(tsl_points)

    tp_price = round_to_tick(tp_price, tick_size)
    tp_price_str = format_price(tp_price, tick_size)
    trail_amount_str = format_price(raw_trail, tick_size)

    order_resp = place_entry_order_with_trailing_bracket(product_id, symbol, side, quantity, trail_amount_str, tp_price_str)
    if not order_resp or not order_resp.get("success"):
        log(symbol, "❌ ENTRY FAILED")
        return False, False, None

    result = order_resp.get("result", {})
    order_id = result.get("id")
    returned_trail = result.get("bracket_trail_amount")
    returned_tp = result.get("bracket_take_profit_price")

    if returned_trail is None or returned_tp is None:
        emergency_close_position(product_id, symbol, side, quantity)
        return False, True, None

    fill_price = get_average_fill_price(order_id)
    actual_entry = fill_price if fill_price else trigger_price
    side_icon = "🟢 [BUY]" if side == "buy" else "🔴 [SELL]"
    log(symbol, "🚀 ENTRY SUCCESS " + side_icon + " @ " + str(actual_entry))
    return True, False, actual_entry

def check_breakout(cfg, price):
    symbol = cfg["symbol"]
    now = time.time()
    with state_lock:
        s = state[symbol]
        if now < s["cooldown_until"] or s["position_open"]:
            return
        ref = s["reference_candle"]
        if ref is None:
            return

        side = "buy" if price > ref["high"] else ("sell" if price < ref["low"] else None)
        if side is None:
            return

        s["position_open"] = True
        s["position_side"] = side
        s["entry_time"] = get_ist_time()
        s["reference_candle"] = None

    side_label = "BUY" if side == "buy" else "SELL"
    log(symbol, "⚡ BREAKOUT (" + side_label + ") @ " + str(price))
    entered, bracket_failed, entry_price = execute_breakout_trade(cfg, side, price)

    with state_lock:
        s = state[symbol]
        if not entered:
            s["position_open"] = False
            s["position_side"] = None
            s["entry_time"] = None
            if bracket_failed:
                s["cooldown_until"] = time.time() + (get_candle_seconds(cfg["resolution"]) * COOLDOWN_CANDLES)
        else:
            s["entry_price"] = entry_price

def maybe_log_price(cfg, price):
    symbol = cfg["symbol"]
    now = time.time()
    with state_lock:
        s = state[symbol]
        if now - s["last_price_log_time"] >= PRICE_LOG_INTERVAL:
            s["last_price_log_time"] = now
            if s["position_open"]:
                side_str = "🟢 [BUY]" if s["position_side"] == "buy" else "🔴 [SELL]"
                status_str = "IN POSITION " + side_str
            else:
                if s["reference_candle"] is not None:
                    status_str = "⏳ WAITING FOR BREAKOUT"
                else:
                    status_str = "🔄 WFC (WAITING FOR CLOSE / RUNNING)"
            log(symbol, "PRICE=" + str(price) + " | Status: " + status_str)

def on_ws_open(ws):
    mark_symbols = ["MARK:" + cfg["symbol"] for cfg in SYMBOLS_CONFIG]
    payload = {"type": "subscribe", "payload": {"channels": [{"name": "mark_price", "symbols": mark_symbols}]}}
    ws.send(json.dumps(payload))

def on_ws_message(ws, message):
    try:
        data = json.loads(message)
        if data.get("type") != "mark_price":
            return
        raw_symbol = data.get("symbol") or data.get("sy")
        raw_price = data.get("price") or data.get("p")
        if not raw_symbol or not raw_price:
            return
        symbol = raw_symbol.replace("MARK:", "")
        cfg = symbol_lookup.get(symbol)
        if not cfg:
            return
        price = float(raw_price)
        with state_lock:
            state[symbol]["latest_price"] = price
        maybe_log_price(cfg, price)
        check_breakout(cfg, price)
    except Exception as e:
        pass

def on_ws_error(ws, error):
    pass

def on_ws_close(ws, code, msg):
    pass

def start_price_websocket():
    while True:
        try:
            ws = websocket.WebSocketApp(WS_URL, on_open=on_ws_open, on_message=on_ws_message, on_error=on_ws_error, on_close=on_ws_close)
            ws.run_forever(ping_interval=30, ping_timeout=10)
        except Exception:
            pass
        time.sleep(WS_RECONNECT_DELAY)

def candle_watcher_loop():
    for cfg in SYMBOLS_CONFIG:
        with state_lock:
            state[cfg["symbol"]]["next_close_time"] = get_next_candle_close_time(cfg["resolution"])
    while True:
        try:
            now = time.time()
            for cfg in SYMBOLS_CONFIG:
                symbol = cfg["symbol"]
                resolution = cfg["resolution"]
                candle_seconds = get_candle_seconds(resolution)
                with state_lock:
                    s = state[symbol]
                    next_close_time = s["next_close_time"]
                    pending_start = s["pending_candle_start"]
                    pending_deadline = s["pending_deadline"]

                if now >= next_close_time and pending_start is None:
                    with state_lock:
                        s["pending_candle_start"] = s["next_close_time"] - candle_seconds
                        s["pending_deadline"] = now + CANDLE_FETCH_RETRY_WINDOW
                        s["next_close_time"] += candle_seconds
                        pending_start = s["pending_candle_start"]
                        pending_deadline = s["pending_deadline"]

                if pending_start is not None:
                    candle = fetch_candle_by_start_time(resolution, symbol, pending_start)
                    if candle:
                        ref = evaluate_candle_data(cfg, candle)
                        with state_lock:
                            state[symbol]["reference_candle"] = ref
                            state[symbol]["pending_candle_start"] = None
                            state[symbol]["pending_deadline"] = None
                    elif now > pending_deadline:
                        with state_lock:
                            state[symbol]["pending_candle_start"] = None
                            state[symbol]["pending_deadline"] = None
            time.sleep(CANDLE_WATCHER_TICK)
        except Exception:
            time.sleep(CANDLE_WATCHER_TICK)

def position_watcher_loop():
    while True:
        try:
            for cfg in SYMBOLS_CONFIG:
                symbol = cfg["symbol"]
                product_id = cfg["product_id"]
                quantity = cfg["quantity"]

                with state_lock:
                    locally_open = state[symbol]["position_open"]
                    side = state[symbol]["position_side"]
                    entry = state[symbol]["entry_price"]
                    ent_time = state[symbol]["entry_time"]

                size, api_entry = get_position_size_and_entry(product_id)
                if size is None:
                    continue

                if size != 0 and not locally_open:
                    with state_lock:
                        state[symbol]["position_open"] = True
                        state[symbol]["position_side"] = "buy" if size > 0 else "sell"
                        state[symbol]["entry_price"] = api_entry
                        if not state[symbol]["entry_time"]:
                            state[symbol]["entry_time"] = get_ist_time()
                elif size == 0 and locally_open:
                    exit_reason, exit_price, api_pnl = get_exit_reason_and_details(product_id)
                    exit_val = exit_price if exit_price else (state[symbol]["latest_price"] or 0)
                    
                    pnl = api_pnl
                    if pnl == 0.0 and entry:
                        if side == "buy":
                            pnl = (exit_val - entry) * quantity
                        else:
                            pnl = (entry - exit_val) * quantity

                    is_win = pnl > 0
                    exit_time_str = get_ist_time()

                    with state_lock:
                        state[symbol]["position_open"] = False
                        state[symbol]["position_side"] = None
                        state[symbol]["entry_price"] = None
                        state[symbol]["total_trades"] += 1
                        if is_win:
                            state[symbol]["wins"] += 1
                        else:
                            state[symbol]["losses"] += 1
                        state[symbol]["net_pnl"] += pnl

                        trade_logs.insert(0, {
                            "entry_time": ent_time if ent_time else exit_time_str,
                            "exit_time": exit_time_str,
                            "coin": symbol,
                            "type": (str(side).upper() if side else "TRADE") + " | " + exit_reason,
                            "entry": round(entry, 4) if entry else 0,
                            "exit": round(exit_val, 4),
                            "pnl": round(pnl, 2)
                        })
                        if len(trade_logs) > 50:
                            trade_logs.pop()
                        state[symbol]["entry_time"] = None

                    log(symbol, "🏁 EXIT -> " + exit_reason + " | Exit Price: " + str(round(exit_val, 4)) + " | Net PnL: " + str(round(pnl, 2)))

            time.sleep(POSITION_WATCHER_INTERVAL)
        except Exception:
            time.sleep(POSITION_WATCHER_INTERVAL)

# ==================== WEB DASHBOARD UI ====================
app = Flask(__name__)

DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Multi-Coin Dashboard</title>
    <meta http-equiv="refresh" content="5">
    <style>
        body { background-color: #0d1117; color: #c9d1d9; font-family: Arial, sans-serif; margin: 0; padding: 20px; }
        h1 { text-align: center; color: #58a6ff; margin-bottom: 10px; }
        .top-summary { text-align: center; background-color: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 15px; margin-bottom: 30px; }
        .top-summary h2 { margin: 0; color: #8b949e; font-size: 16px; }
        .total-pnl { font-size: 32px; font-weight: bold; margin: 5px 0; }
        .total-trades { font-size: 14px; color: #8b949e; }
        
        .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 15px; margin-bottom: 40px; }
        .card { background-color: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 15px; box-shadow: 0 4px 6px rgba(0,0,0,0.3); }
        .card h3 { margin: 0 0 10px 0; color: #f0f6fc; border-bottom: 1px solid #30363d; padding-bottom: 8px; }
        .row { display: flex; justify-content: space-between; margin: 6px 0; font-size: 14px; }
        .pnl-pos { color: #3fb950; font-weight: bold; }
        .pnl-neg { color: #f85149; font-weight: bold; }
        
        .table-container { background-color: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 20px; overflow-x: auto; }
        h2.section-title { color: #f0f6fc; margin-top: 0; text-align: center; }
        table { width: 100%; border-collapse: collapse; margin-top: 10px; text-align: left; }
        th, td { padding: 12px; border-bottom: 1px solid #30363d; font-size: 14px; }
        th { color: #8b949e; background-color: #21262d; }
    </style>
</head>
<body>
    <h1>Multi-Coin Dashboard</h1>
    
    {% set ns = namespace(total_pnl=0.0, total_trades=0) %}
    {% for symbol, data in states.items() %}
        {% set ns.total_pnl = ns.total_pnl + data.net_pnl %}
        {% set ns.total_trades = ns.total_trades + data.total_trades %}
    {% endfor %}

    <div class="top-summary">
        <h2>Total Portfolio Net P&L</h2>
        <div class="total-pnl {% if ns.total_pnl >= 0 %}pnl-pos{% else %}pnl-neg{% endif %}">${{ "%.2f"|format(ns.total_pnl) }}</div>
        <div class="total-trades">Total Trades Executed: {{ ns.total_trades }}</div>
    </div>
    
    <div class="grid">
        {% for symbol, data in states.items() %}
        <div class="card">
            <h3>{{ symbol }}</h3>
            <div class="row"><span>Total Trades:</span> <span>{{ data.total_trades }}</span></div>
            <div class="row"><span>Wins / Losses:</span> <span>{{ data.wins }} / {{ data.losses }}</span></div>
            <div class="row"><span>Net P&L:</span> <span class="{% if data.net_pnl >= 0 %}pnl-pos{% else %}pnl-neg{% endif %}">${{ "%.2f"|format(data.net_pnl) }}</span></div>
        </div>
        {% endfor %}
    </div>

    <div class="table-container">
        <h2 class="section-title">📜 Live Executed Trade Logs</h2>
        <table>
            <thead>
                <tr>
                    <th>Entry Time</th>
                    <th>Exit Time</th>
                    <th>Coin</th>
                    <th>Type / Hit</th>
                    <th>Entry</th>
                    <th>Exit</th>
                    <th>P&L</th>
                </tr>
            </thead>
            <tbody>
                {% if logs %}
                    {% for log in logs %}
                    <tr>
                        <td>{{ log.entry_time }}</td>
                        <td>{{ log.exit_time }}</td>
                        <td>{{ log.coin }}</td>
                        <td>{{ log.type }}</td>
                        <td>{{ log.entry }}</td>
                        <td>{{ log.exit }}</td>
                        <td class="{% if log.pnl >= 0 %}pnl-pos{% else %}pnl-neg{% endif %}">${{ log.pnl }}</td>
                    </tr>
                    {% endfor %}
                {% else %}
                    <tr><td colspan="7" style="text-align: center; color: #8b949e;">No trades executed yet.</td></tr>
                {% endif %}
            </tbody>
        </table>
    </div>
</body>
</html>
"""

@app.route("/")
def home():
    with state_lock:
        current_states = {sym: dict(st) for sym, st in state.items()}
        current_logs = list(trade_logs)
    return render_template_string(DASHBOARD_HTML, states=current_states, logs=current_logs)

@app.route("/ping")
def ping():
    return {"status": "alive", "time": get_ist_time()}, 200

if __name__ == "__main__":
    threading.Thread(target=start_price_websocket, daemon=True).start()
    threading.Thread(target=candle_watcher_loop, daemon=True).start()
    threading.Thread(target=position_watcher_loop, daemon=True).start()

    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
