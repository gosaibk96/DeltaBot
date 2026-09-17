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
# ============================================================

SYMBOL = "BTCUSD"
PRODUCT_ID = 27
TICK_SIZE = 0.5
CANDLE_RESOLUTION = "1h"
QUANTITY = 1  # in lots

CANDLE_RANGE_MIN_POINTS = 0
CANDLE_RANGE_MAX_POINTS = 200

SL_TRAIL_POINTS = 200
RR_RATIO = 4

STOP_TRIGGER_METHOD = "mark_price"

# ============================================================
RESOLUTION_SECONDS = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600,
    "1d": 86400, "1w": 604800
}

CANDLE_WATCHER_TICK = 1
POSITION_WATCHER_INTERVAL = 2
CANDLE_FETCH_RETRY_WINDOW = 20
COOLDOWN_CANDLES = 3
WS_RECONNECT_DELAY = 3

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


def get_next_candle_close_time(resolution):
    candle_seconds = get_candle_seconds(resolution)
    now = time.time()
    return (int(now) // candle_seconds + 1) * candle_seconds


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
        return None, None


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


def execute_breakout_trade(side, trigger_price):
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
        return False, False

    result = order_resp.get("result", {})
    order_id = result.get("id")

    returned_trail = result.get("bracket_trail_amount")
    returned_tp = result.get("bracket_take_profit_price")

    if returned_trail is None or returned_tp is None:
        log(f"BRACKET NOT CONFIRMED on order {order_id} (trail={returned_trail}, tp={returned_tp}). "
