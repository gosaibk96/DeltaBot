import hmac
import hashlib
import time
import json
import requests
import math
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

SYMBOL = "BTCUSD"
PRODUCT_ID = 27
TICK_SIZE = 0.5
CANDLE_RESOLUTION = "5m"
QUANTITY = 1

CANDLE_RANGE_MIN_POINTS = 0
CANDLE_RANGE_MAX_POINTS = 200

SL_TRAIL_POINTS = 200
RR_RATIO = 4

STOP_TRIGGER_METHOD = "mark_price"

RESOLUTION_SECONDS = {"1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600, "1d": 86400, "1w": 604800}

CANDLE_WATCHER_TICK = 1
POSITION_WATCHER_INTERVAL = 2
CANDLE_FETCH_RETRY_WINDOW = 20
COOLDOWN_CANDLES = 3
WS_RECONNECT_DELAY = 3
PRICE_LOG_INTERVAL = 5

ist = pytz.timezone('Asia/Kolkata')

state_lock = threading.Lock()

shared_state = {
    "latest_price": None,
    "reference_candle": None,
    "position_open": False,
    "position_side": None,
    "cooldown_until": 0,
    "pending_candle_start": None,
    "pending_deadline": None,
    "next_close_time": None,
    "last_price_log_time": 0,
}


def get_ist_time():
    return datetime.now(ist).strftime('%Y-%m-%d %H:%M:%S')


def log(msg):
    print("[" + get_ist_time() + "][" + SYMBOL + "] " + str(msg), flush=True)


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
    return {"api-key": API_KEY, "timestamp": timestamp, "signature": signature, "User-Agent": "render-btcusd-breakout-bot", "Content-Type": "application/json"}


def get_next_candle_close_time(resolution):
    candle_seconds = get_candle_seconds(resolution)
    now = time.time()
    return (int(now) // candle_seconds + 1) * candle_seconds


def fetch_candle_by_start_time(resolution, expected_start_time):
    candle_seconds = get_candle_seconds(resolution)
    end_ts = int(time.time())
    start_ts = expected_start_time - (candle_seconds * 3)
    url = BASE_URL + "/v2/history/candles"
    params = {"resolution": resolution, "symbol": SYMBOL, "start": start_ts, "end": end_ts}
    try:
        resp = requests.get(url, params=params, timeout=(3, 10))
        data = resp.json()
        if data.get("success"):
            for c in data.get("result", []):
                if int(c["time"]) == int(expected_start_time):
                    return c
        return None
    except Exception as e:
        log("Error fetching candles: " + str(e))
        return None


def get_position_size_and_entry():
    method = "GET"
    path = "/v2/positions"
    query_string = "?product_id=" + str(PRODUCT_ID)
    url = BASE_URL + path
    headers = get_headers(method, path, query_string)
    try:
        resp = requests.get(url, params={"product_id": PRODUCT_ID}, headers=headers, timeout=(3, 10))
        data = resp.json()
        if data.get("success"):
            result = data.get("result")
            if result and result.get("size") is not None:
                return int(result["size"]), result.get("entry_price")
        return 0, None
    except Exception as e:
        log("Error fetching position: " + str(e))
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
        log("Error fetching order: " + str(e))
        return None


def get_exit_reason():
    """
    Queries order history for the most recently closed stop order (SL or TP)
    on this product to determine why the position was closed.
    """
    method = "GET"
    path = "/v2/orders/history"
    query_string = "?product_ids=" + str(PRODUCT_ID) + "&order_types=all_stop&page_size=5"
    url = BASE_URL + path
    headers = get_headers(method, path, query_string)
    try:
        resp = requests.get(
            url,
            params={"product_ids": str(PRODUCT_ID), "order_types": "all_stop", "page_size": 5},
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
        log("Error fetching exit reason: " + str(e))
        return "UNKNOWN (error fetching exit reason)", None


def place_entry_order_with_trailing_bracket(side, size, trail_amount_str, tp_price_str):
    method = "POST"
    path = "/v2/orders"
    url = BASE_URL + path
    payload_dict = {
        "product_id": PRODUCT_ID,
        "product_symbol": SYMBOL,
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
        log("Error placing entry order: " + str(e))
        return None


def emergency_close_position(side, quantity):
    close_side = "sell" if side == "buy" else "buy"
    log("EMERGENCY CLOSE triggered -> closing naked position via reduce-only market " + close_side + " order")
    method = "POST"
    path = "/v2/orders"
    url = BASE_URL + path
    payload_dict = {
        "product_id": PRODUCT_ID,
        "product_symbol": SYMBOL,
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
            log("CRITICAL: Emergency close FAILED -> " + str(result) + ". Manual intervention required!")
        return result
    except Exception as e:
        log("CRITICAL: Emergency close request error: " + str(e) + ". Manual intervention required!")
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


def evaluate_candle_data(candle):
    high = float(candle["high"])
    low = float(candle["low"])
    range_points = round(high - low, 8)
    qualifies = CANDLE_RANGE_MIN_POINTS <= range_points <= CANDLE_RANGE_MAX_POINTS

    with state_lock:
        current_price = shared_state["latest_price"]

    if qualifies:
        log("CONDITION MATCH -> Candle Range=" + str(range_points) + " pts is within limit (<=" + str(CANDLE_RANGE_MAX_POINTS) + " pts). Reference SET -> High=" + str(high) + ", Low=" + str(low) + " | Current BTC Price=" + str(current_price))
        return {"high": high, "low": low}
    else:
        log("CONDITION NOT MATCH -> Candle Range=" + str(range_points) + " pts exceeds limit (<=" + str(CANDLE_RANGE_MAX_POINTS) + " pts). No reference set. | Current BTC Price=" + str(current_price))
        return None


def execute_breakout_trade(side, trigger_price):
    if side == "buy":
        tp_price = trigger_price + (RR_RATIO * SL_TRAIL_POINTS)
        trail_amount_value = -SL_TRAIL_POINTS
    else:
        tp_price = trigger_price - (RR_RATIO * SL_TRAIL_POINTS)
        trail_amount_value = SL_TRAIL_POINTS

    tp_price = round_to_tick(tp_price, TICK_SIZE)
    tp_price_str = format_price(tp_price, TICK_SIZE)
    trail_amount_str = format_price(trail_amount_value, TICK_SIZE)

    log("BREAKOUT DETECTED -> Placing " + side.upper() + " market order | qty=" + str(QUANTITY) + " lot(s) | Trailing SL=" + trail_amount_str + " pts | TP=" + tp_price_str)

    order_resp = place_entry_order_with_trailing_bracket(side, QUANTITY, trail_amount_str, tp_price_str)
    if not order_resp or not order_resp.get("success"):
        log("ORDER FAILED -> " + str(order_resp))
        return False, False

    result = order_resp.get("result", {})
    order_id = result.get("id")

    returned_trail = result.get("bracket_trail_amount")
    returned_tp = result.get("bracket_take_profit_price")

    if returned_trail is None or returned_tp is None:
        log("BRACKET NOT CONFIRMED on order " + str(order_id) + " (trail=" + str(returned_trail) + ", tp=" + str(returned_tp) + "). Emergency closing to avoid a naked position.")
        emergency_close_position(side, QUANTITY)
        return False, True

    log("BRACKET CONFIRMED on order " + str(order_id) + " -> Trailing SL=" + str(returned_trail) + ", TP=" + str(returned_tp))

    fill_price = get_average_fill_price(order_id)
    if fill_price:
        log("POSITION OPENED -> " + side.upper() + " @ " + str(fill_price) + " | Trailing SL=" + str(returned_trail) + " pts | TP=" + str(returned_tp))
    else:
        log("POSITION OPENED -> " + side.upper() + " (fill price not confirmed via API yet) | Trailing SL=" + str(returned_trail) + " pts | TP=" + str(returned_tp))

    return True, False


def check_breakout(price):
    now = time.time()
    with state_lock:
        if now < shared_state["cooldown_until"]:
            return
        if shared_state["position_open"]:
            return
        ref = shared_state["reference_candle"]
        if ref is None:
            return

        side = None
        if price > ref["high"]:
            side = "buy"
        elif price < ref["low"]:
            side = "sell"

        if side is None:
            return

        shared_state["position_open"] = True
        shared_state["position_side"] = side
        shared_state["reference_candle"] = None

    entered, bracket_failed = execute_breakout_trade(side, price)

    with state_lock:
        if not entered:
            shared_state["position_open"] = False
            shared_state["position_side"] = None
            if bracket_failed:
                candle_seconds = get_candle_seconds(CANDLE_RESOLUTION)
                shared_state["cooldown_until"] = time.time() + (candle_seconds * COOLDOWN_CANDLES)
                log("Cooldown activated for " + str(COOLDOWN_CANDLES) + " candle(s) due to bracket failure.")


def maybe_log_price(price):
    now = time.time()
    should_log = False
    with state_lock:
        if now - shared_state["last_price_log_time"] >= PRICE_LOG_INTERVAL:
            shared_state["last_price_log_time"] = now
            should_log = True
            position_open = shared_state["position_open"]
            position_side = shared_state["position_side"]
            ref = shared_state["reference_candle"]

    if should_log:
        if position_open:
            log("LIVE BTC PRICE=" + str(price) + " | Status: IN POSITION (" + str(position_side).upper() + ")")
        elif ref is not None:
            log("LIVE BTC PRICE=" + str(price) + " | Status: WAITING FOR BREAKOUT | Reference High=" + str(ref["high"]) + ", Low=" + str(ref["low"]))
        else:
            log("LIVE BTC PRICE=" + str(price) + " | Status: WAITING FOR QUALIFYING CANDLE (no active reference)")


def on_ws_open(ws):
    log("WebSocket connected. Subscribing to mark_price channel...")
    payload = {"type": "subscribe", "payload": {"channels": [{"name": "mark_price", "symbols": ["MARK:" + SYMBOL]}]}}
    ws.send(json.dumps(payload))


def on_ws_message(ws, message):
    try:
        data = json.loads(message)
        msg_type = data.get("type")

        if msg_type == "mark_price":
            raw_price = data.get("price")
            if raw_price is None:
                return
            try:
                price = float(raw_price)
            except (TypeError, ValueError):
                return
            with state_lock:
                shared_state["latest_price"] = price
            maybe_log_price(price)
            check_breakout(price)
    except Exception as e:
        log("WS message parse error: " + str(e) + " | Raw message: " + str(message))


def on_ws_error(ws, error):
    log("WebSocket error: " + str(error))


def on_ws_close(ws, close_status_code, close_msg):
    log("WebSocket closed (code=" + str(close_status_code) + ", msg=" + str(close_msg) + "). Will reconnect...")


def start_price_websocket():
    while True:
        try:
            ws = websocket.WebSocketApp(WS_URL, on_open=on_ws_open, on_message=on_ws_message, on_error=on_ws_error, on_close=on_ws_close)
            ws.run_forever(ping_interval=30, ping_timeout=10)
        except Exception as e:
            log("WebSocket thread exception: " + str(e))
        log("Reconnecting WebSocket in " + str(WS_RECONNECT_DELAY) + " seconds...")
        time.sleep(WS_RECONNECT_DELAY)


def candle_watcher_loop():
    candle_seconds = get_candle_seconds(CANDLE_RESOLUTION)
    with state_lock:
        shared_state["next_close_time"] = get_next_candle_close_time(CANDLE_RESOLUTION)

    log("Candle watcher started. Waiting for current running " + CANDLE_RESOLUTION + " candle to close before marking any reference (per startup rule).")

    while True:
        try:
            now = time.time()
            with state_lock:
                next_close_time = shared_state["next_close_time"]
                pending_start = shared_state["pending_candle_start"]
                pending_deadline = shared_state["pending_deadline"]

            if now >= next_close_time and pending_start is None:
                with state_lock:
                    shared_state["pending_candle_start"] = shared_state["next_close_time"] - candle_seconds
                    shared_state["pending_deadline"] = now + CANDLE_FETCH_RETRY_WINDOW
                    shared_state["next_close_time"] += candle_seconds
                    pending_start = shared_state["pending_candle_start"]
                    pending_deadline = shared_state["pending_deadline"]

            if pending_start is not None:
                candle = fetch_candle_by_start_time(CANDLE_RESOLUTION, pending_start)
                if candle:
                    ref = evaluate_candle_data(candle)
                    with state_lock:
                        shared_state["reference_candle"] = ref
                        shared_state["pending_candle_start"] = None
                        shared_state["pending_deadline"] = None
                elif now > pending_deadline:
                    log("Candle fetch FAILED after " + str(CANDLE_FETCH_RETRY_WINDOW) + "s of retries. Skipping this candle.")
                    with state_lock:
                        shared_state["pending_candle_start"] = None
                        shared_state["pending_deadline"] = None

            time.sleep(CANDLE_WATCHER_TICK)
        except Exception as e:
            log("Candle watcher error: " + str(e))
            time.sleep(CANDLE_WATCHER_TICK)


def position_watcher_loop():
    while True:
        try:
            with state_lock:
                locally_open = shared_state["position_open"]

            size, entry_price = get_position_size_and_entry()

            if size is None:
                time.sleep(POSITION_WATCHER_INTERVAL)
                continue

            if size != 0 and not locally_open:
                with state_lock:
                    shared_state["position_open"] = True
                    shared_state["position_side"] = "buy" if size > 0 else "sell"
                log("Position detected on resume/sync -> size=" + str(size) + ", entry_price=" + str(entry_price))

            elif size == 0 and locally_open:
                with state_lock:
                    shared_state["position_open"] = False
                    shared_state["position_side"] = None
                reason, exit_price = get_exit_reason()
                if exit_price:
                    log("POSITION CLOSED -> Reason: " + reason + " | Exit Price=" + str(exit_price))
                else:
                    log("POSITION CLOSED -> Reason: " + reason)
                log("Ready for next qualifying breakout.")

            time.sleep(POSITION_WATCHER_INTERVAL)
        except Exception as e:
            log("Position watcher error: " + str(e))
            time.sleep(POSITION_WATCHER_INTERVAL)


from flask import Flask
app = Flask(__name__)


@app.route("/ping")
def ping():
    return {"status": "alive", "time": get_ist_time()}, 200


@app.route("/")
def home():
    return {"status": "bot running", "symbol": SYMBOL}, 200


if __name__ == "__main__":
    threading.Thread(target=start_price_websocket, daemon=True).start()
    threading.Thread(target=candle_watcher_loop, daemon=True).start()
    threading.Thread(target=position_watcher_loop, daemon=True).start()

    log("Bot started. Symbol=" + SYMBOL + ", Resolution=" + CANDLE_RESOLUTION + ", Range<= " + str(CANDLE_RANGE_MAX_POINTS) + "pts, SL(trail)=" + str(SL_TRAIL_POINTS) + "pts, RR=1:" + str(RR_RATIO) + ", Qty=" + str(QUANTITY) + " lot(s)")

