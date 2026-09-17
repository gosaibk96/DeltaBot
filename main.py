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
