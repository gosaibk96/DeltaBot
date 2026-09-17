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

# ============================================================
# CREDENTIALS (set these in Render -> Environment)
# ============================================================
API_KEY = os.environ.get("API_KEY", "your_api_key_here")
API_SECRET = os.environ.get("API_SECRET", "your_api_secret_here")

BASE_URL = "https://api.india.delta.exchange"
WS_URL = "wss://socket.india.delta.exchange"

# ============================================================
# EASILY CHANGEABLE STRATEGY SETTINGS (BTCUSD)
# Change these values anytime. No other part of the code
# needs to be touched for these adjustments.
# ============================================================

SYMBOL = "BTCUSD"
PRODUCT_ID = 27
TICK_SIZE = 0.5
CANDLE_RESOLUTION = "1h"
QUANTITY = 1  # in lots

# Closed candle's (High - Low) must be <= this value (in points) to qualify.
CANDLE_RANGE_MIN_POINTS = 0
CANDLE_RANGE_MAX_POINTS = 200

# Stop Loss: native broker-side TRAILING stop, sent directly with entry order.
SL_TRAIL_POINTS = 200

# Take Profit: Risk:Reward ratio. Risk = SL_TRAIL_POINTS.
# TP distance = RR_RATIO * SL_TRAIL_POINTS
RR_RATIO = 4

STOP_TRIGGER_METHOD = "mark_price"

# ============================================================
# INTERNAL TIMING / SAFETY SETTINGS
# ============================================================
RESOLUTION_SECONDS = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600,
    "1d": 86400, "1w": 604800
}

CANDLE_WATCHER_TICK = 1          # how often (sec) we check for candle boundary
POSITION_WATCHER_INTERVAL = 2    # how often (sec) we poll REST for position status
CANDLE_FETCH_RETRY_WINDOW = 20   # seconds to keep retrying candle fetch after close
COOLDOWN_CANDLES = 3             # cooldown (in candles) after a bracket failure
WS_RECONNECT_DELAY = 3           # seconds to wait before WS reconnect attempt

ist = pytz.timezone('Asia/Kolkata')

# ============================================================
# SHARED STATE (thread-safe via lock)
# ============================================================
state_lock = threading.Lock()

shared_state = {
    "latest_price": None,
    "reference_candle": None,     # {"high":..., "low":...}
    "position_open": False,
    "position_side": None,        # "buy"/"sell" - local flag, set right after order placed
    "cooldown_until": 0,
    "pending_candle_start": None,
    "pending_deadline": None,
    "next_close_time": None,
}


def get_ist_time():
    return datetime.now(ist).strftime('%Y-%m-%d %H:%M:%S')


def log(msg):
    print(f"[{get_ist_time()}][{SYMBOL}] {msg}", flush=True)


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
    return f"{float(value):.{decimals}f}"


# ==================== SIGNATURE HELPERS ====================
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
        "User-Agent": "render-btcusd-breakout-bot",
        "Content-Type": "application/json",
    }


# ==================== CANDLE BOUNDARY HELPER ====================
def get_next_candle_close_time(resolution):
    candle_seconds = get_candle_seconds(resolution)
    now = time.time()
    return (int(now) // candle_seconds + 1) * candle_seconds


# ==================== REST: MARKET DATA ====================
def fetch_candle_by_start_time(resolution, expected_start_time):
    candle_seconds = get_candle_seconds(resolution)
    end_ts = int(time.time())
    start_ts = expected_start_time - (candle_seconds * 3)
    url = BASE_URL + "/v2/history/candles"
    params = {
        "resolution": resolution,
        "symbol": SYMBOL,
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
        log(f"Error fetching candles: {e}")
        return None


def get_position_size_and_entry():
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
            if result and result.get("size") is not None:
                return int(result["size"]), result.get("entry_price")
        return 0, None
    except Exception as e:
        log(f"Error fetching position: {e}")
        return None, None  # None signals a failed check (do not assume flat)


def get_order_by_id(order_id):
    method = "GET"
    path = f"/v2/orders/{order_id}"
    url = BASE_URL + path
    headers = get_headers(method, path)
    try:
        resp = requests.get(url, headers=headers, timeout=(3, 10))
        return resp.json()
    except Exception as e:
        log(f"Error fetching order: {e}")
        return None


# ==================== REST: ORDER PLACEMENT ====================
def place_entry_order_with_trailing_bracket(side, size, trail_amount_str, tp_price_str):
    """
    Places the entry market order WITH bracket_trail_amount (native trailing SL)
    and bracket_take_profit_price attached in the SAME request. Delta's
    POST /orders endpoint supports both fields directly - no follow-up
    edit call is needed, avoiding the earlier open_order_not_found problem.
    """
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
        log(f"Error placing entry order: {e}")
        return None


def emergency_close_position(side, quantity):
    close_side = "sell" if side == "buy" else "buy"
    log(f"EMERGENCY CLOSE triggered -> closing naked position via reduce-only market {close_side} order")
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
            log(f"CRITICAL: Emergency close FAILED -> {result}. Manual intervention required!")
        return result
    except Exception as e:
        log(f"CRITICAL: Emergency close request error: {e}. Manual intervention required!")
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


# ==================== CANDLE EVALUATION ====================
def evaluate_candle_data(candle):
    high = float(candle["high"])
    low = float(candle["low"])
    range_points = round(high - low, 8)
    qualifies = CANDLE_RANGE_MIN_POINTS <= range_points <= CANDLE_RANGE_MAX_POINTS

    log(f"Closed Candle -> High: {high}, Low: {low}, Range: {range_points} pts, "
        f"Required: <= {CANDLE_RANGE_MAX_POINTS} pts, Qualifies: {qualifies}")

    if qualifies:
        return {"high": high, "low": low}
    return None


# ==================== TRADE EXECUTION ====================
def execute_breakout_trade(side, trigger_price):
    """
    trigger_price = the live price at the moment of breakout (estimate).
    SL is a native trailing stop of SL_TRAIL_POINTS.
    TP is fixed at RR_RATIO * SL_TRAIL_POINTS distance from the trigger price.
    """
    if side == "buy":
        tp_price = trigger_price + (RR_RATIO * SL_TRAIL_POINTS)
    else:
        tp_price = trigger_price - (RR_RATIO * SL_TRAIL_POINTS)

    tp_price = round_to_tick(tp_price, TICK_SIZE)
    tp_price_str = format_price(tp_price, TICK_SIZE)
    trail_amount_str = format_price(SL_TRAIL_POINTS, TICK_SIZE)

    log(f"BREAKOUT DETECTED -> Placing {side.upper()} market order | qty={QUANTITY} lot(s) | "
        f"Trailing SL={trail_amount_str} pts | TP={tp_price_str}")

    order_resp = place_entry_order_with_trailing_bracket(side, QUANTITY, trail_amount_str, tp_price_str)
    if not order_resp or not order_resp.get("success"):
        log(f"ORDER FAILED -> {order_resp}")
        return False, False  # entered=False, bracket_failed=False (order itself failed)

    result = order_resp.get("result", {})
    order_id = result.get("id")

    returned_trail = result.get("bracket_trail_amount")
    returned_tp = result.get("bracket_take_profit_price")

    if returned_trail is None or returned_tp is None:
        log(f"BRACKET NOT CONFIRMED on order {order_id} (trail={returned_trail}, tp={returned_tp}). "
            f"Emergency closing to avoid a naked position.")
        emergency_close_position(side, QUANTITY)
        return False, True  # entered=False, bracket_failed=True -> triggers cooldown

    log(f"BRACKET CONFIRMED on order {order_id} -> Trailing SL={returned_trail}, TP={returned_tp}")

    fill_price = get_average_fill_price(order_id)
    if fill_price:
        log(f"ENTRY {side.upper()} FILLED @ {fill_price} | Trailing SL={returned_trail} pts | TP={returned_tp}")
    else:
        log(f"ENTRY {side.upper()} placed (fill price not confirmed via API yet) | "
            f"Trailing SL={returned_trail} pts | TP={returned_tp}")

    return True, False  # entered=True


# ==================== BREAKOUT CHECK (called on every price tick) ====================
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

        # Lock immediately to prevent duplicate entries from rapid price ticks
        # while the order request is in flight.
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
                log(f"Cooldown activated for {COOLDOWN_CANDLES} candle(s) due to bracket failure.")


# ==================== WEBSOCKET: LIVE PRICE FEED ====================
def on_ws_open(ws):
    log("WebSocket connected. Subscribing to mark_price channel...")
    payload = {
        "type": "subscribe",
        "payload": {
            "channels": [
                {"name": "mark_price", "symbols": [f"MARK:{SYMBOL}"]}
            ]
        }
    }
    ws.send(json.dumps(payload))


def on_ws_message(ws, message):
    try:
        data = json.loads(message)
        if data.get("type") == "mark_price":
            price = float(data.get("p"))
            with state_lock:
                shared_state["latest_price"] = price
            check_breakout(price)
    except Exception as e:
        log(f"WS message parse error: {e}")


def on_ws_error(ws, error):
    log(f"WebSocket error: {error}")


def on_ws_close(ws, close_status_code, close_msg):
    log(f"WebSocket closed (code={close_status_code}, msg={close_msg}). Will reconnect...")


def start_price_websocket():
    while True:
        try:
            ws = websocket.WebSocketApp(
                WS_URL,
                on_open=on_ws_open,
                on_message=on_ws_message,
                on_error=on_ws_error,
                on_close=on_ws_close,
            )
            ws.run_forever(ping_interval=30, ping_timeout=10)
        except Exception as e:
            log(f"WebSocket thread exception: {e}")
        log(f"Reconnecting WebSocket in {WS_RECONNECT_DELAY} seconds...")
        time.sleep(WS_RECONNECT_DELAY)


# ==================== CANDLE WATCHER THREAD ====================
def candle_watcher_loop():
    candle_seconds = get_candle_seconds(CANDLE_RESOLUTION)
    with state_lock:
        shared_state["next_close_time"] = get_next_candle_close_time(CANDLE_RESOLUTION)

    log(f"Candle watcher started. Waiting for current running {CANDLE_RESOLUTION} candle to close "
        f"before marking any reference (per startup rule).")

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
                    log(f"Candle fetch FAILED after {CANDLE_FETCH_RETRY_WINDOW}s of retries. Skipping this candle.")
                    with state_lock:
                        shared_state["pending_candle_start"] = None
                        shared_state["pending_deadline"] = None

            time.sleep(CANDLE_WATCHER_TICK)
        except Exception as e:
            log(f"Candle watcher error: {e}")
            time.sleep(CANDLE_WATCHER_TICK)


# ==================== POSITION WATCHER THREAD ====================
def position_watcher_loop():
    while True:
        try:
            with state_lock:
                locally_open = shared_state["position_open"]

            size, entry_price = get_position_size_and_entry()

            if size is None:
                # REST call failed - do not change state on a failed check.
                time.sleep(POSITION_WATCHER_INTERVAL)
                continue

            if size != 0 and not locally_open:
                # Exchange shows a position but we hadn't marked it locally
                # (e.g. after a restart). Sync state so we don't double-enter.
                with state_lock:
                    shared_state["position_open"] = True
                    shared_state["position_side"] = "buy" if size > 0 else "sell"
                log(f"Position detected on resume/sync -> size={size}, entry_price={entry_price}")

            elif size == 0 and locally_open:
                with state_lock:
                    shared_state["position_open"] = False
                    shared_state["position_side"] = None
                log("Position CLOSED (flat). Ready for next qualifying breakout.")

            time.sleep(POSITION_WATCHER_INTERVAL)
        except Exception as e:
            log(f"Position watcher error: {e}")
            time.sleep(POSITION_WATCHER_INTERVAL)


# ==================== MINIMAL FLASK APP (Render keep-alive only) ====================
from flask import Flask
app = Flask(__name__)


@app.route("/ping")
def ping():
    return {"status": "alive", "time": get_ist_time()}, 200


if __name__ == "__main__":
    threading.Thread(target=start_price_websocket, daemon=True).start()
    threading.Thread(target=candle_watcher_loop, daemon=True).start()
    threading.Thread(target=position_watcher_loop, daemon=True).start()

    log(f"Bot started. Symbol={SYMBOL}, Resolution={CANDLE_RESOLUTION}, "
        f"Range<= {CANDLE_RANGE_MAX_POINTS}pts, SL(trail)={SL_TRAIL_POINTS}pts, "
        f"RR=1:{RR_RATIO}, Qty={QUANTITY} lot(s)")

    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
