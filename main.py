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
# PER-SYMBOL CONFIG -> Change resolution / quantity / range_points /
# tsl_points / tick_size here anytime for any symbol independently.
# range_points  = max allowed candle (High-Low) range for reference candle
# tsl_points    = trailing stop-loss distance (also used for TP = RR_RATIO x tsl_points)
# ============================================================
SYMBOLS_CONFIG = [
    {"symbol": "BTCUSD",     "product_id": 27,     "tick_size": 0.5,    "resolution": "1h", "quantity": 1, "range_points": 200,    "tsl_points": 200},
    {"symbol": "ETHUSD",     "product_id": 3136,   "tick_size": 0.05,   "resolution": "1h", "quantity": 1, "range_points": 5,      "tsl_points": 5},
    {"symbol": "XAUTUSD",    "product_id": 131253, "tick_size": 0.01,   "resolution": "1h", "quantity": 1, "range_points": 5,      "tsl_points": 5},
    {"symbol": "SLVONUSD",   "product_id": 124058, "tick_size": 0.01,   "resolution": "1h", "quantity": 1, "range_points": 5,      "tsl_points": 5},
    {"symbol": "XRPUSD",     "product_id": 14969,  "tick_size": 0.0001, "resolution": "1h", "quantity": 1, "range_points": 0.0065, "tsl_points": 0.0065},
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
