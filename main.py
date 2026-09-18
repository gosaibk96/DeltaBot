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

# ============================================================
# PER-SYMBOL CONFIG -> Har symbol ka resolution / quantity /
# range_points / tsl_points / tick_size independently change kar sakte ho.
# resolution    = timeframe jis par reference candle dekha jayega (e.g. "1h", "15m", "1d")
# quantity      = lot size jo order place hote waqt use hoga
# range_points  = max allowed candle (High-Low) range jisse reference set hoga
# tsl_points    = trailing stop-loss distance (TP = RR_RATIO x tsl_points)
# ============================================================
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
for cfg in SYMBOLS_CONFIG:
    state[cfg["symbol"]] = {
        "latest_price": None,
        "reference_candle": None,
        "position_open": False,
        "position_side": None,
        "cooldown_until": 0,
        "last_price_log_time": 0,
        "next_close_time": None,
        "pending_candle_start": None,
        "pending_deadline": None,
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
        log(symbol, "Error fetching candles: " + str(e))
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
        print("[" + get_ist_time() + "] Error fetching position for product_id " + str(product_id) + ": " + str(e), flush=True)
        return None, None


def get_order_by_id(order_id):
    method = "GET"
    path = "/v2/orders/" + str(order_id)
    url = BASE_URL + path
    headers = get_headers(method, path)
    try:
        resp = requests.get(url, headers=headers, timeout=(3, 10))
        return resp.json()
    except Exception as e:
        print("[" + get_ist_time() + "] Error fetching order: " + str(e), flush=True)
        return None


def get_exit_reason(product_id):
    method = "GET"
    path = "/v2/orders/history"
    query_string = "?product_ids=" + str(product_id) + "&order_types=all_stop&page_size=5"
    url = BASE_URL + path
    headers = get_headers(method, path, query_string)
    try:
        resp = requests.get(
            url,
            params={"product_ids": str(product_id), "order_types": "all_stop", "page_size": 5},
            headers=headers,
            timeout=(3, 10),
        )
        data = resp.json()
        if data.get("success"):
            orders = data.get("result", [])
            closed_orders = [o for o in orders if o.get("state") == "closed"]
            if closed_orders:
                closed_orders.sort(key=lambda o: int(o.get("created_at", 0)), reverse=True)
                latest = closed_orders[0]
                stop_type = latest.get("stop_order_type")
                fill_price = latest.get("average_fill_price")
                if stop_type == "stop_loss_order":
                    return "TRAILING SL HIT", fill_price
                elif stop_type == "take_profit_order":
                    return "TAKE PROFIT HIT", fill_price
        return "UNKNOWN (manual close / liquidation / no stop record found)", None
    except Exception as e:
        return "UNKNOWN (error fetching exit reason: " + str(e) + ")", None


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
        log(product_symbol, "Error placing entry order: " + str(e))
        return None


def emergency_close_position(product_id, product_symbol, side, quantity):
    close_side = "sell" if side == "buy" else "buy"
    log(product_symbol, "EMERGENCY CLOSE triggered -> closing naked position via reduce-only market " + close_side + " order")
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
        result = resp.json()
        if not result.get("success"):
            log(product_symbol, "CRITICAL: Emergency close FAILED -> " + str(result) + ". Manual intervention required!")
        return result
    except Exception as e:
        log(product_symbol, "CRITICAL: Emergency close request error: " + str(e) + ". Manual intervention required!")
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
        log(symbol, "CONDITION MATCH -> Candle Range=" + str(range_points) + " pts is within limit (<=" + str(max_range) + " pts). Reference SET -> High=" + str(high) + ", Low=" + str(low) + " | Current Price=" + str(current_price))
        return {"high": high, "low": low}
    else:
        log(symbol, "CONDITION NOT MATCH -> Candle Range=" + str(range_points) + " pts exceeds limit (<=" + str(max_range) + " pts). No reference set. | Current Price=" + str(current_price))
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

    log(symbol, "BREAKOUT DETECTED -> Placing " + side.upper() + " market order | qty=" + str(quantity) + " lot(s) | Trailing SL=" + trail_amount_str + " pts | TP=" + tp_price_str)

    order_resp = place_entry_order_with_trailing_bracket(product_id, symbol, side, quantity, trail_amount_str, tp_price_str)
    if not order_resp or not order_resp.get("success"):
        log(symbol, "ORDER FAILED -> " + str(order_resp))
        return False, False

    result = order_resp.get("result", {})
    order_id = result.get("id")

    returned_trail = result.get("bracket_trail_amount")
    returned_tp = result.get("bracket_take_profit_price")

    if returned_trail is None or returned_tp is None:
        log(symbol, "BRACKET NOT CONFIRMED on order " + str(order_id) + " (trail=" + str(returned_trail) + ", tp=" + str(returned_tp) + "). Emergency closing to avoid a naked position.")
        emergency_close_position(product_id, symbol, side, quantity)
        return False, True

    log(symbol, "BRACKET CONFIRMED on order " + str(order_id) + " -> Trailing SL=" + str(returned_trail) + ", TP=" + str(returned_tp))

    fill_price = get_average_fill_price(order_id)
    if fill_price:
        log(symbol, "POSITION OPENED -> " + side.upper() + " @ " + str(fill_price) + " | Trailing SL=" + str(returned_trail) + " pts | TP=" + str(returned_tp))
    else:
        log(symbol, "POSITION OPENED -> " + side.upper() + " (fill price not confirmed via API yet) | Trailing SL=" + str(returned_trail) + " pts | TP=" + str(returned_tp))

    return True, False


def check_breakout(cfg, price):
    symbol = cfg["symbol"]
    now = time.time()
    with state_lock:
        s = state[symbol]
        if now < s["cooldown_until"]:
            return
        if s["position_open"]:
            return
        ref = s["reference_candle"]
        if ref is None:
            return

        side = None
        if price > ref["high"]:
            side = "buy"
        elif price < ref["low"]:
            side = "sell"

        if side is None:
            return

        s["position_open"] = True
        s["position_side"] = side
        s["reference_candle"] = None

    entered, bracket_failed = execute_breakout_trade(cfg, side, price)

    with state_lock:
        s = state[symbol]
        if not entered:
            s["position_open"] = False
            s["position_side"] = None
            if bracket_failed:
                candle_seconds = get_candle_seconds(cfg["resolution"])
                s["cooldown_until"] = time.time() + (candle_seconds * COOLDOWN_CANDLES)
                log(symbol, "Cooldown activated for " + str(COOLDOWN_CANDLES) + " candle(s) due to bracket failure.")


def maybe_log_price(cfg, price):
    symbol = cfg["symbol"]
    now = time.time()
    should_log = False
    position_open = False
    position_side = None
    ref = None
    with state_lock:
        s = state[symbol]
        if now - s["last_price_log_time"] >= PRICE_LOG_INTERVAL:
            s["last_price_log_time"] = now
            should_log = True
            position_open = s["position_open"]
            position_side = s["position_side"]
            ref = s["reference_candle"]

    if should_log:
        if position_open:
            log(symbol, "LIVE PRICE=" + str(price) + " | Status: IN POSITION (" + str(position_side).upper() + ")")
        elif ref is not None:
            log(symbol, "LIVE PRICE=" + str(price) + " | Status: WAITING FOR BREAKOUT | Reference High=" + str(ref["high"]) + ", Low=" + str(ref["low"]))
        else:
            log(symbol, "LIVE PRICE=" + str(price) + " | Status: WAITING FOR QUALIFYING CANDLE (no active reference)")


def on_ws_open(ws):
    print("[" + get_ist_time() + "] WebSocket connected. Subscribing to mark_price channel for all symbols...", flush=True)
    mark_symbols = ["MARK:" + cfg["symbol"] for cfg in SYMBOLS_CONFIG]
    payload = {"type": "subscribe", "payload": {"channels": [{"name": "mark_price", "symbols": mark_symbols}]}}
    ws.send(json.dumps(payload))


def on_ws_message(ws, message):
    try:
        data = json.loads(message)
        msg_type = data.get("type")
        if msg_type != "mark_price":
            return

        raw_symbol = data.get("symbol")
        if raw_symbol is None:
            raw_symbol = data.get("sy")

        raw_price = data.get("price")
        if raw_price is None:
            raw_price = data.get("p")

        if raw_symbol is None or raw_price is None:
            return

        symbol = raw_symbol.replace("MARK:", "")
        cfg = symbol_lookup.get(symbol)
        if cfg is None:
            return

        try:
            price = float(raw_price)
        except (TypeError, ValueError):
            return

        with state_lock:
            state[symbol]["latest_price"] = price

        maybe_log_price(cfg, price)
        check_breakout(cfg, price)
    except Exception as e:
        print("[" + get_ist_time() + "] WS message parse error: " + str(e) + " | Raw message: " + str(message), flush=True)


def on_ws_error(ws, error):
    print("[" + get_ist_time() + "] WebSocket error: " + str(error), flush=True)


def on_ws_close(ws, close_status_code, close_msg):
    print("[" + get_ist_time() + "] WebSocket closed (code=" + str(close_status_code) + ", msg=" + str(close_msg) + "). Will reconnect...", flush=True)


def start_price_websocket():
    while True:
        try:
            ws = websocket.WebSocketApp(WS_URL, on_open=on_ws_open, on_message=on_ws_message, on_error=on_ws_error, on_close=on_ws_close)
            ws.run_forever(ping_interval=30, ping_timeout=10)
        except Exception as e:
            print("[" + get_ist_time() + "] WebSocket thread exception: " + str(e), flush=True)
        print("[" + get_ist_time() + "] Reconnecting WebSocket in " + str(WS_RECONNECT_DELAY) + " seconds...", flush=True)
        time.sleep(WS_RECONNECT_DELAY)


def candle_watcher_loop():
    for cfg in SYMBOLS_CONFIG:
        symbol = cfg["symbol"]
        with state_lock:
            state[symbol]["next_close_time"] = get_next_candle_close_time(cfg["resolution"])

    print("[" + get_ist_time() + "] Candle watcher started. Each symbol runs on its own configured resolution. Waiting for current running candle to close per symbol before marking any reference.", flush=True)

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
                        s = state[symbol]
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
                        log(symbol, "Candle fetch FAILED after " + str(CANDLE_FETCH_RETRY_WINDOW) + "s of retries. Skipping this candle cycle.")
                        with state_lock:
                            state[symbol]["pending_candle_start"] = None
                            state[symbol]["pending_deadline"] = None

            time.sleep(CANDLE_WATCHER_TICK)
        except Exception as e:
            print("[" + get_ist_time() + "] Candle watcher error: " + str(e), flush=True)
            time.sleep(CANDLE_WATCHER_TICK)


def position_watcher_loop():
    while True:
        try:
            for cfg in SYMBOLS_CONFIG:
                symbol = cfg["symbol"]
                product_id = cfg["product_id"]

                with state_lock:
                    locally_open = state[symbol]["position_open"]

                size, entry_price = get_position_size_and_entry(product_id)

                if size is None:
                    continue

                if size != 0 and not locally_open:
                    with state_lock:
                        state[symbol]["position_open"] = True
                        state[symbol]["position_side"] = "buy" if size > 0 else "sell"
                    log(symbol, "Position detected on resume/sync -> size=" + str(size) + ", entry_price=" + str(entry_price))

                elif size == 0 and locally_open:
                    with state_lock:
                        state[symbol]["position_open"] = False
                        state[symbol]["position_side"] = None
                    reason, exit_price = get_exit_reason(product_id)
                    if exit_price:
                        log(symbol, "POSITION CLOSED -> Reason: " + reason + " | Exit Price=" + str(exit_price))
                    else:
                        log(symbol, "POSITION CLOSED -> Reason: " + reason)
                    log(symbol, "Ready for next qualifying breakout.")

            time.sleep(POSITION_WATCHER_INTERVAL)
        except Exception as e:
            print("[" + get_ist_time() + "] Position watcher error: " + str(e), flush=True)
            time.sleep(POSITION_WATCHER_INTERVAL)


from flask import Flask
app = Flask(__name__)


@app.route("/ping")
def ping():
    return {"status": "alive", "time": get_ist_time()}, 200


@app.route("/")
def home():
    return {"status": "bot running", "symbols": [cfg["symbol"] for cfg in SYMBOLS_CONFIG]}, 200


if __name__ == "__main__":
    threading.Thread(target=start_price_websocket, daemon=True).start()
    threading.Thread(target=candle_watcher_loop, daemon=True).start()
    threading.Thread(target=position_watcher_loop, daemon=True).start()

    print("[" + get_ist_time() + "] Multi-symbol bot started. RR=1:" + str(RR_RATIO), flush=True)
    for cfg in SYMBOLS_CONFIG:
        print("  -> " + cfg["symbol"] + " | Resolution=" + cfg["resolution"] + " | Qty=" + str(cfg["quantity"]) + " lot(s) | Range=" + str(cfg["range_points"]) + " pts | TSL=" + str(cfg["tsl_points"]) + " pts | tick_size=" + str(cfg["tick_size"]), flush=True)

    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
