import hmac
import hashlib
import time
import json
import requests

# ============================================================
# CONFIGURATION - Change these values anytime as per your need
# ============================================================

API_KEY = "4vtWGaF4x4LWleMfoj1ztriQp7rweE"
API_SECRET = "dsuv5MuOGueu7OKXBo0U6CFCHryeEgujn3l7YD5rb5ibsWKDMRVU0BrQDhmW"

BASE_URL = "https://api.india.delta.exchange"

SYMBOL = "DOTUSD"
PRODUCT_ID = 15304              # DOTUSD product id (confirm via getProductBySymbol if symbol changes)

QUANTITY = 1                     # Lot size (whole numbers only, e.g. 1, 5, 10)

CANDLE_RESOLUTION = "5m"         # Timeframe: 1m, 3m, 5m, 15m, 30m, 1h, 2h, 4h, 6h, 1d, 1w
RESOLUTION_SECONDS = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600,
    "1d": 86400, "1w": 604800
}

NARROW_RANGE_PCT = 0.5           # Candle (High-Low)/Low*100 must be less than this value
RR_RATIO = 4                     # Target distance = RR_RATIO x SL distance from entry
POLL_INTERVAL = 2                # Seconds between each live price check
STOP_TRIGGER_METHOD = "mark_price"   # mark_price / last_traded_price / spot_price

# ============================================================


def get_candle_seconds():
    return RESOLUTION_SECONDS.get(CANDLE_RESOLUTION, 300)


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
        "User-Agent": "pydroid3-breakout-bot",
        "Content-Type": "application/json",
    }


# ==================== MARKET DATA FUNCTIONS ====================
def get_last_closed_candle():
    """Fetch the most recent fully-closed candle for configured resolution."""
    candle_seconds = get_candle_seconds()
    end_ts = int(time.time())
    start_ts = end_ts - (candle_seconds * 5)
    path = "/v2/history/candles"
    url = BASE_URL + path
    params = {
        "resolution": CANDLE_RESOLUTION,
        "symbol": SYMBOL,
        "start": start_ts,
        "end": end_ts,
    }
    try:
        resp = requests.get(url, params=params, timeout=(3, 10))
        data = resp.json()
        if data.get("success") and len(data.get("result", [])) >= 2:
            candles = sorted(data["result"], key=lambda c: c["time"])
            return candles[-2]  # second-last = last fully closed candle
        return None
    except Exception as e:
        print("Error fetching candles:", e)
        return None


def get_mark_price():
    """Fetch current live mark price."""
    path = f"/v2/tickers/{SYMBOL}"
    url = BASE_URL + path
    try:
        resp = requests.get(url, timeout=(3, 10))
        data = resp.json()
        if data.get("success"):
            return float(data["result"]["mark_price"])
        return None
    except Exception as e:
        print("Error fetching ticker:", e)
        return None


def get_position_size():
    """Return current open position size for the product. 0 means flat."""
    method = "GET"
    path = "/v2/positions"
    query_string = f"?product_id={PRODUCT_ID}"
    url = BASE_URL + path
    headers = get_headers(method, path, query_string)
    try:
        resp = requests.get(
            url, params={"product_id": PRODUCT_ID}, headers=headers, timeout=(3, 10)
        )
        data = resp.json()
        if data.get("success"):
            result = data.get("result")
            if result and "size" in result and result["size"] is not None:
                return int(result["size"])
        return 0
    except Exception as e:
        print("Error fetching position:", e)
        return 0


def get_order_by_id(order_id):
    """Fetch order details by id (used to get average_fill_price)."""
    method = "GET"
    path = f"/v2/orders/{order_id}"
    url = BASE_URL + path
    headers = get_headers(method, path)
    try:
        resp = requests.get(url, headers=headers, timeout=(3, 10))
        return resp.json()
    except Exception as e:
        print("Error fetching order:", e)
        return None


# ==================== ORDER FUNCTIONS ====================
def place_market_order(side, size):
    """Place a market order and return the order response."""
    method = "POST"
    path = "/v2/orders"
    url = BASE_URL + path
    payload_dict = {
        "product_id": PRODUCT_ID,
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
        print("Error placing market order:", e)
        return None


def get_average_fill_price(order_response):
    """Extract average_fill_price from order response, polling briefly if not yet available."""
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
    """Attach a stop-loss and take-profit bracket to the currently open position."""
    method = "POST"
    path = "/v2/orders/bracket"
    url = BASE_URL + path
    payload_dict = {
        "product_id": PRODUCT_ID,
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
        print("Error placing bracket order:", e)
        return None


# ==================== MAIN LOGIC ====================
def main():
    print("Starting narrow-range breakout bot...")
    print(f"Symbol: {SYMBOL} | Qty: {QUANTITY} lot | Timeframe: {CANDLE_RESOLUTION} | "
          f"Narrow%: {NARROW_RANGE_PCT} | RR: 1:{RR_RATIO} | Poll: {POLL_INTERVAL}s")

    reference_candle = None
    last_candle_time = None

    while True:
        try:
            # Step 1: Check for a new closed candle and update reference if narrow-range
            candle = get_last_closed_candle()
            if candle and candle.get("time") != last_candle_time:
                last_candle_time = candle["time"]
                high = float(candle["high"])
                low = float(candle["low"])
                if low > 0:
                    range_pct = (high - low) / low * 100
                    if range_pct < NARROW_RANGE_PCT:
                        reference_candle = {"high": high, "low": low}
                        print(f"[{time.strftime('%H:%M:%S')}] New narrow-range candle -> "
                              f"High={high}, Low={low}, Range%={range_pct:.4f}")

            # Step 2: Check for breakout only if we have a reference and position is flat
            if reference_candle:
                position_size = get_position_size()
                if position_size == 0:
                    price = get_mark_price()
                    if price is not None:

                        if price > reference_candle["high"]:
                            print(f"[{time.strftime('%H:%M:%S')}] BUY breakout signal at {price}")
                            order_resp = place_market_order("buy", QUANTITY)
                            print("Order response:", order_resp)

                            entry_price = get_average_fill_price(order_resp)
                            if entry_price is not None:
                                sl_price = reference_candle["low"]
                                sl_distance = entry_price - sl_price
                                tp_price = entry_price + (RR_RATIO * sl_distance)
                                print(f"Entry={entry_price}, SL={sl_price}, TP={tp_price}")
                                bracket_resp = place_bracket_sl_tp(sl_price, tp_price)
                                print("Bracket response:", bracket_resp)
                            else:
                                print("Could not fetch entry price. SL/TP not placed. Check manually!")

                            reference_candle = None  # wait for next narrow-range candle

                        elif price < reference_candle["low"]:
                            print(f"[{time.strftime('%H:%M:%S')}] SELL breakout signal at {price}")
                            order_resp = place_market_order("sell", QUANTITY)
                            print("Order response:", order_resp)

                            entry_price = get_average_fill_price(order_resp)
                            if entry_price is not None:
                                sl_price = reference_candle["high"]
                                sl_distance = sl_price - entry_price
                                tp_price = entry_price - (RR_RATIO * sl_distance)
                                print(f"Entry={entry_price}, SL={sl_price}, TP={tp_price}")
                                bracket_resp = place_bracket_sl_tp(sl_price, tp_price)
                                print("Bracket response:", bracket_resp)
                            else:
                                print("Could not fetch entry price. SL/TP not placed. Check manually!")

                            reference_candle = None

            time.sleep(POLL_INTERVAL)

        except KeyboardInterrupt:
            print("Bot stopped manually.")
            break
        except Exception as e:
            print("Main loop error:", e)
            time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
