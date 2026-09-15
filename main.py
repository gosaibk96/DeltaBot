import hmac
import hashlib
import time
import json
import requests
import math
import os
import threading
from decimal import Decimal
from datetime import datetime
import pytz

# ============================================================
# GLOBAL SETTINGS
# ============================================================

API_KEY = os.environ.get("API_KEY", "your_api_key_here")
API_SECRET = os.environ.get("API_SECRET", "your_api_secret_here")

BASE_URL = "https://api.india.delta.exchange"

POLL_INTERVAL = 2
STOP_TRIGGER_METHOD = "mark_price"
CANDLE_FETCH_RETRY_WINDOW = 20
COOLDOWN_CANDLES = 3

RESOLUTION_SECONDS = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600,
    "1d": 86400, "1w": 604800
}

# ============================================================
# EASILY CHANGEABLE STRATEGY SETTINGS
# Change these values anytime to tune the strategy. No other
# part of the code needs to be touched for these adjustments.
# ============================================================

SYMBOL = "XRPUSD"
PRODUCT_ID = 14969
TICK_SIZE = 0.0001
CANDLE_RESOLUTION = "15m"
QUANTITY = 2

# Closed candle's (High - Low) must fall strictly between these two
# FIXED POINT values (not percentage) to qualify as a narrow-range setup.
CANDLE_RANGE_MIN_POINTS = 0.0070   # candle range must be greater than this
CANDLE_RANGE_MAX_POINTS = 0.0140   # candle range must be smaller than this

RR_RATIO = 4  # Take Profit = Entry + (RR_RATIO * Risk), Risk = |Entry - SL|

# ============================================================

bot_state_data = {
    "status": "Initializing", "last_price": 0.0, "entry": "-", "sl": "-",
    "tp": "-", "trail_mode": "-", "wins": 0, "losses": 0, "net_pnl": 0.0
}
trade_history = []

ist = pytz.timezone('Asia/Kolkata')
app = __import__('flask').Flask(__name__)

def get_ist_time():
    return datetime.now(ist).strftime('%Y-%m-%d %H:%M:%S')

def get_candle_seconds(resolution):
    return RESOLUTION_SECONDS.get(resolution, 3600)

def round_to_tick(price, tick_size):
    if tick_size <= 0:
        return price
    rounded = round(price / tick_size) * tick_size
    decimals = max(0, -int(math.floor(math.log10(tick_size))) if tick_size < 1 else 0)
    return round(rounded, decimals + 2)

def format_price(value, tick_size):
    d = Decimal(str(tick_size))
    exponent = d.as_tuple().exponent
    decimals = -exponent if exponent < 0 else 0
    return f"{float(value):.{decimals}f}"

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
        "User-Agent": "render-xrpusd-breakout-bot",
        "Content-Type": "application/json",
    }

# ==================== CANDLE BOUNDARY HELPERS ====================
def get_next_candle_close_time(resolution):
    candle_seconds = get_candle_seconds(resolution)
    now = time.time()
    return (int(now) // candle_seconds + 1) * candle_seconds

# ==================== MARKET DATA FUNCTIONS ====================
def fetch_candle_by_start_time(resolution, expected_start_time):
    candle_seconds = get_candle_seconds(resolution)
    end_ts = int(time.time())
    start_ts = expected_start_time - (candle_seconds * 3)
    path = "/v2/history/candles"
    url = BASE_URL + path
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
        print(f"[{get_ist_time()}][{SYMBOL}] Error fetching candles: {e}", flush=True)
        return None

def get_mark_price():
    path = f"/v2/tickers/{SYMBOL}"
    url = BASE_URL + path
    try:
        resp = requests.get(url, timeout=(3, 10))
        data = resp.json()
        if data.get("success"):
            return float(data["result"]["mark_price"])
        return None
    except Exception as e:
        print(f"[{get_ist_time()}][{SYMBOL}] Error fetching ticker: {e}", flush=True)
        return None

def get_position_size():
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
        print(f"[{get_ist_time()}][{SYMBOL}] Error fetching position: {e}", flush=True)
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
        print(f"[{get_ist_time()}][{SYMBOL}] Error fetching order: {e}", flush=True)
        return None

# ==================== ORDER FUNCTIONS ====================
def place_market_order_with_bracket(side, size, bracket_sl_price, bracket_tp_price):
    """Places the entry market order WITH a fixed bracket_stop_loss_price
    and bracket_take_profit_price in the SAME request. This guarantees the
    position is protected from the very first moment. The fixed SL is later
    switched to a broker-native trailing stop via edit_bracket_order."""
    method = "POST"
    path = "/v2/orders"
    url = BASE_URL + path
    payload_dict = {
        "product_id": PRODUCT_ID,
        "size": size,
        "side": side,
        "order_type": "market_order",
        "bracket_stop_loss_price": str(bracket_sl_price),
        "bracket_take_profit_price": str(bracket_tp_price),
        "bracket_stop_trigger_method": STOP_TRIGGER_METHOD,
    }
    payload = json.dumps(payload_dict)
    headers = get_headers(method, path, "", payload)
    try:
        resp = requests.post(url, data=payload, headers=headers, timeout=(3, 10))
        return resp.json()
    except Exception as e:
        print(f"[{get_ist_time()}][{SYMBOL}] Error placing market order with bracket: {e}", flush=True)
        return None

def get_average_fill_price(order_response):
    if not order_response or not order_response.get("success"):
        return None, None

    order = order_response.get("result", {})
    fill_price = order.get("average_fill_price")
    order_id = order.get("id")

    retries = 8
    while fill_price is None and retries > 0 and order_id:
        time.sleep(0.5)
        fresh = get_order_by_id(order_id)
        if fresh and fresh.get("success"):
            fill_price = fresh.get("result", {}).get("average_fill_price")
        retries -= 1

    return (float(fill_price) if fill_price else None), order_id

def emergency_close_position(side, quantity):
    close_side = "sell" if side == "buy" else "buy"
    print(f"[{get_ist_time()}][{SYMBOL}] EMERGENCY CLOSE triggered -> closing naked position via market {close_side} order", flush=True)
    resp = place_market_order_with_bracket(close_side, quantity, "0", "0")
    if not resp or not resp.get("success"):
        print(f"[{get_ist_time()}][{SYMBOL}] CRITICAL: Emergency close FAILED -> {resp}. Manual intervention required!", flush=True)
    return resp

def edit_bracket_order(order_id, sl_price=None, tp_price=None, trail_amount=None):
    """Edits bracket params on the entry-order bracket. Sends product_symbol
    alongside product_id (Delta's PUT /orders/bracket schema requires id,
    product_id AND product_symbol together). sl_price and trail_amount are
    mutually exclusive on Delta's API - a bracket SL is either a fixed
    trigger price or a trailing amount, never both."""
    method = "PUT"
    path = "/v2/orders/bracket"
    url = BASE_URL + path
    payload_dict = {
        "id": order_id,
        "product_id": PRODUCT_ID,
        "product_symbol": SYMBOL,
    }
    if trail_amount is not None:
        payload_dict["bracket_trail_amount"] = str(trail_amount)
    elif sl_price is not None:
        payload_dict["bracket_stop_loss_price"] = str(sl_price)
    if tp_price is not None:
        payload_dict["bracket_take_profit_price"] = str(tp_price)

    payload = json.dumps(payload_dict)
    headers = get_headers(method, path, "", payload)
    try:
        resp = requests.put(url, data=payload, headers=headers, timeout=(3, 10))
        return resp.json()
    except Exception as e:
        print(f"[{get_ist_time()}][{SYMBOL}] Error editing bracket order: {e}", flush=True)
        return None

def refresh_position_bracket_display(order_id):
    """Best-effort refresh of the dashboard SL value from the live order
    object. NOT CONFIRMED whether Delta's API updates bracket_stop_loss_price
    on the order object in real time as a trailing stop moves - if it does
    not, this will simply keep showing the last known value."""
    resp = get_order_by_id(order_id)
    if resp and resp.get("success"):
        result = resp.get("result", {})
        sl = result.get("bracket_stop_loss_price")
        if sl is not None:
            bot_state_data["sl"] = sl

# ==================== CANDLE EVALUATION ====================
def evaluate_candle_data(candle):
    high = float(candle["high"])
    low = float(candle["low"])
    range_points = round(high - low, 8)

    qualifies = CANDLE_RANGE_MIN_POINTS < range_points < CANDLE_RANGE_MAX_POINTS

    print(f"[{get_ist_time()}][{SYMBOL}] Closed Candle -> High: {high}, Low: {low}, "
          f"Range(points): {range_points}, Required range: "
          f"({CANDLE_RANGE_MIN_POINTS} - {CANDLE_RANGE_MAX_POINTS}), Qualifies: {qualifies}", flush=True)

    if qualifies:
        bot_state_data["status"] = f"Setup Active (H:{high}, L:{low}, Range:{range_points})"
        return {"high": high, "low": low}
    else:
        bot_state_data["status"] = f"Monitoring (Range: {range_points} pts, not in {CANDLE_RANGE_MIN_POINTS}-{CANDLE_RANGE_MAX_POINTS})"
        return None

# ==================== TRADE EXECUTION ====================
def execute_breakout_trade(side, reference_candle, estimated_entry_price):
    if side == "buy":
        initial_sl = round_to_tick(reference_candle["low"], TICK_SIZE)
        est_sl_distance = estimated_entry_price - initial_sl
        est_tp = round_to_tick(estimated_entry_price + (RR_RATIO * est_sl_distance), TICK_SIZE)
    else:
        initial_sl = round_to_tick(reference_candle["high"], TICK_SIZE)
        est_sl_distance = initial_sl - estimated_entry_price
        est_tp = round_to_tick(estimated_entry_price - (RR_RATIO * est_sl_distance), TICK_SIZE)

    initial_sl_str = format_price(initial_sl, TICK_SIZE)
    est_tp_str = format_price(est_tp, TICK_SIZE)

    print(f"[{get_ist_time()}][{SYMBOL}] Placing {side.upper()} market order | qty={QUANTITY} | "
          f"fixed SL={initial_sl_str} | fixed TP={est_tp_str}", flush=True)

    order_resp = place_market_order_with_bracket(side, QUANTITY, initial_sl_str, est_tp_str)
    if not order_resp or not order_resp.get("success"):
        print(f"[{get_ist_time()}][{SYMBOL}] Market order with bracket FAILED -> {order_resp}", flush=True)
        return None, False

    result = order_resp.get("result", {})
    order_id = result.get("id")

    # Safety check: confirm the exchange actually attached the bracket.
    returned_sl = result.get("bracket_stop_loss_price")
    returned_tp = result.get("bracket_take_profit_price")
    if returned_sl is None or returned_tp is None:
        print(f"[{get_ist_time()}][{SYMBOL}] BRACKET NOT CONFIRMED on order {order_id} "
              f"(SL={returned_sl}, TP={returned_tp}). Emergency closing.", flush=True)
        emergency_close_position(side, QUANTITY)
        return None, True  # bracket_failed -> triggers cooldown

    print(f"[{get_ist_time()}][{SYMBOL}] Bracket CONFIRMED on order {order_id} -> SL={returned_sl}, TP={returned_tp}", flush=True)

    entry_price, order_id = get_average_fill_price(order_resp)
    if entry_price is None:
        print(f"[{get_ist_time()}][{SYMBOL}] WARNING: Could not fetch exact fill price after retries. "
              f"Using estimated entry ({estimated_entry_price}). Fixed bracket SL/TP already confirmed active.", flush=True)
        entry_price = estimated_entry_price
    else:
        # Attempt TP correction for slippage (best-effort; order may already
        # be "closed" since it's a market order, in which case this can fail
        # with open_order_not_found - the originally confirmed TP stays active).
        if side == "buy":
            exact_sl_distance = entry_price - initial_sl
            exact_tp = round_to_tick(entry_price + (RR_RATIO * exact_sl_distance), TICK_SIZE)
        else:
            exact_sl_distance = initial_sl - entry_price
            exact_tp = round_to_tick(entry_price - (RR_RATIO * exact_sl_distance), TICK_SIZE)

        exact_tp_str = format_price(exact_tp, TICK_SIZE)
        if exact_tp_str != est_tp_str:
            correction = edit_bracket_order(order_id, tp_price=exact_tp_str)
            if correction and correction.get("success"):
                print(f"[{get_ist_time()}][{SYMBOL}] TP corrected for slippage -> {exact_tp_str} (RR now exact {RR_RATIO})", flush=True)
                est_tp = exact_tp
            else:
                print(f"[{get_ist_time()}][{SYMBOL}] TP correction FAILED (order likely already closed) -> {correction}. "
                      f"Original confirmed TP {est_tp_str} remains active.", flush=True)

    # Switch from fixed-price SL to a broker-native TRAILING stop.
    # trail_amount = exact distance between actual entry and the candle
    # Low/High, so the trailing stop starts at the same point as the
    # original fixed SL and then trails automatically from there.
    if side == "buy":
        trail_amount = round(entry_price - initial_sl, 8)
    else:
        trail_amount = round(initial_sl - entry_price, 8)
    trail_amount_str = format_price(trail_amount, TICK_SIZE)

    trail_result = edit_bracket_order(order_id, trail_amount=trail_amount_str)
    if trail_result and trail_result.get("success"):
        print(f"[{get_ist_time()}][{SYMBOL}] TRAILING ACTIVATED -> bracket_trail_amount={trail_amount_str} "
              f"(broker will now trail SL automatically)", flush=True)
        trail_mode = "Trailing (native)"
    else:
        print(f"[{get_ist_time()}][{SYMBOL}] TRAILING activation FAILED -> {trail_result}. "
              f"Fixed SL at {initial_sl_str} remains active (position still protected, just not trailing).", flush=True)
        trail_mode = "Fixed (trail failed)"

    print(f"[{get_ist_time()}][{SYMBOL}] ENTRY {side.upper()} @ {entry_price} | Initial SL={initial_sl_str} | "
          f"TP={format_price(est_tp, TICK_SIZE)} | Trail mode: {trail_mode}", flush=True)

    bot_state_data["status"] = f"{side.upper()} Executed"
    bot_state_data["entry"] = entry_price
    bot_state_data["sl"] = initial_sl
    bot_state_data["tp"] = est_tp
    bot_state_data["trail_mode"] = trail_mode

    return {
        "side": side.upper(),
        "entry_price": entry_price,
        "initial_sl": initial_sl,
        "tp_price": est_tp,
        "order_id": order_id,
        "trail_mode": trail_mode,
        "entry_time": get_ist_time()
    }, False

# ==================== BACKGROUND WORKER LOOP ====================
def background_bot_loop():
    print(f"Starting {SYMBOL} narrow-range breakout bot worker (point-based range: "
          f"{CANDLE_RANGE_MIN_POINTS}-{CANDLE_RANGE_MAX_POINTS}, resolution={CANDLE_RESOLUTION})...", flush=True)

    candle_seconds = get_candle_seconds(CANDLE_RESOLUTION)
    sym_state = {
        "next_close_time": get_next_candle_close_time(CANDLE_RESOLUTION),
        "reference_candle": None,
        "position": None,
        "cooldown_until": 0,
        "pending_candle_start": None,
        "pending_deadline": None,
    }
    bot_state_data["status"] = "Waiting for candle close"

    while True:
        try:
            now = time.time()
            live_price = None

            if now < sym_state["cooldown_until"] and sym_state["position"] is None:
                price = get_mark_price()
                if price is not None:
                    bot_state_data["last_price"] = price
                    live_price = price
                remaining = int(sym_state["cooldown_until"] - now)
                bot_state_data["status"] = f"Paused (bracket failure) - cooling down ({remaining}s left)"
                print(f"[{get_ist_time()}][{SYMBOL}] COOLDOWN active -> {remaining}s remaining | LivePrice: {live_price}", flush=True)
                time.sleep(POLL_INTERVAL)
                continue

            if now >= sym_state["next_close_time"] and sym_state["pending_candle_start"] is None and sym_state["position"] is None:
                sym_state["pending_candle_start"] = sym_state["next_close_time"] - candle_seconds
                sym_state["pending_deadline"] = now + CANDLE_FETCH_RETRY_WINDOW
                sym_state["next_close_time"] += candle_seconds

            if sym_state["pending_candle_start"] is not None:
                candle = fetch_candle_by_start_time(CANDLE_RESOLUTION, sym_state["pending_candle_start"])
                if candle:
                    sym_state["reference_candle"] = evaluate_candle_data(candle)
                    sym_state["pending_candle_start"] = None
                    sym_state["pending_deadline"] = None
                elif now > sym_state["pending_deadline"]:
                    print(f"[{get_ist_time()}][{SYMBOL}] Candle fetch FAILED after {CANDLE_FETCH_RETRY_WINDOW}s of retries. Skipping this candle.", flush=True)
                    bot_state_data["status"] = "Warning: Candle fetch failed"
                    sym_state["pending_candle_start"] = None
                    sym_state["pending_deadline"] = None

            if sym_state["position"] is not None:
                pos = sym_state["position"]
                price = get_mark_price()
                if price is not None:
                    bot_state_data["last_price"] = price
                    live_price = price

                refresh_position_bracket_display(pos["order_id"])

                size = get_position_size()
                if size == 0:
                    exit_time = get_ist_time()
                    exit_price = price if price is not None else pos["entry_price"]

                    if pos["side"] == "BUY":
                        pnl = (exit_price - pos["entry_price"]) * QUANTITY
                        is_win = exit_price >= pos["tp_price"] or pnl > 0
                    else:
                        pnl = (pos["entry_price"] - exit_price) * QUANTITY
                        is_win = exit_price <= pos["tp_price"] or pnl > 0

                    if is_win:
                        bot_state_data["wins"] += 1
                    else:
                        bot_state_data["losses"] += 1

                    bot_state_data["net_pnl"] += pnl

                    trade_history.insert(0, {
                        "time": exit_time,
                        "entry_time": pos["entry_time"],
                        "symbol": SYMBOL,
                        "type": pos["side"],
                        "entry": pos["entry_price"],
                        "exit": exit_price,
                        "pnl": round(pnl, 4)
                    })

                    print(f"[{get_ist_time()}][{SYMBOL}] Position CLOSED. Entry={pos['entry_price']}, Exit={exit_price}, PnL={round(pnl,4)}", flush=True)

                    sym_state["position"] = None
                    bot_state_data["status"] = "Flat / Monitoring"
                    bot_state_data["entry"] = "-"
                    bot_state_data["sl"] = "-"
                    bot_state_data["tp"] = "-"
                    bot_state_data["trail_mode"] = "-"

            elif sym_state["reference_candle"] is not None:
                price = get_mark_price()
                if price is not None:
                    bot_state_data["last_price"] = price
                    live_price = price
                    ref = sym_state["reference_candle"]
                    if price > ref["high"]:
                        result, bracket_failed = execute_breakout_trade("buy", ref, price)
                        if result:
                            sym_state["position"] = result
                        elif bracket_failed:
                            sym_state["cooldown_until"] = now + (candle_seconds * COOLDOWN_CANDLES)
                        sym_state["reference_candle"] = None
                    elif price < ref["low"]:
                        result, bracket_failed = execute_breakout_trade("sell", ref, price)
                        if result:
                            sym_state["position"] = result
                        elif bracket_failed:
                            sym_state["cooldown_until"] = now + (candle_seconds * COOLDOWN_CANDLES)
                        sym_state["reference_candle"] = None
            else:
                price = get_mark_price()
                if price is not None:
                    bot_state_data["last_price"] = price
                    live_price = price

            ref = sym_state["reference_candle"]
            ref_str = f"H:{ref['high']} L:{ref['low']}" if ref else "None"
            pos_str = sym_state["position"]["side"] if sym_state["position"] else "NONE"
            pending_str = " [FETCHING CANDLE...]" if sym_state["pending_candle_start"] is not None else ""
            print(f"[{get_ist_time()}][{SYMBOL}] RefCandle -> {ref_str}{pending_str} | LivePrice: {live_price} | Position: {pos_str}", flush=True)

            time.sleep(POLL_INTERVAL)
        except Exception as e:
            print(f"[{get_ist_time()}][{SYMBOL}] Main loop error: {e}", flush=True)
            time.sleep(POLL_INTERVAL)

# ==================== FLASK WEB DASHBOARD & APIS ====================
@app.route("/")
def dashboard():
    st = bot_state_data
    total_trades = st["wins"] + st["losses"]
    pnl_color = "#00e676" if st["net_pnl"] >= 0 else "#ff5252"

    logs_rows = ""
    for t in trade_history[:15]:
        pnl_cls = "color:#00e676;" if t["pnl"] >= 0 else "color:#ff5252;"
        logs_rows += f"""
        <tr>
            <td>{t['entry_time']}</td>
            <td>{t['time']}</td>
            <td><b>{t['symbol']}</b></td>
            <td>{t['type']}</td>
            <td>{t['entry']}</td>
            <td>{t['exit']}</td>
            <td style="{pnl_cls}">${t['pnl']}</td>
        </tr>
        """

    return f"""
    <html>
    <head>
        <title>{SYMBOL} Breakout Bot Dashboard</title>
        <meta http-equiv="refresh" content="5">
        <style>
            body {{ background: #121212; color: #fff; font-family: Arial, sans-serif; padding: 20px; }}
            h1, h2 {{ text-align: center; color: #e0e0e0; }}
            .portfolio-box {{ background: #1f1f1f; border-radius: 10px; padding: 15px; text-align: center; margin-bottom: 25px; border: 1px solid #333; }}
            .card {{ background: #1e1e1e; border: 1px solid #333; border-radius: 8px; padding: 20px; max-width: 500px; margin: 0 auto 30px auto; box-shadow: 0 4px 6px rgba(0,0,0,0.3); }}
            .card h3 {{ margin-top: 0; border-bottom: 1px solid #444; padding-bottom: 8px; color: #ffab40; }}
            .row {{ display: flex; justify-content: space-between; margin: 10px 0; font-size: 15px; }}
            table {{ width: 100%; border-collapse: collapse; background: #181818; border-radius: 8px; overflow: hidden; }}
            th, td {{ border: 1px solid #333; padding: 10px; text-align: center; font-size: 14px; }}
            th {{ background: #222; color: #b0bec5; }}
            tr:nth-child(even) {{ background: #161616; }}
        </style>
    </head>
    <body>
        <h1>{SYMBOL} Breakout Bot Dashboard</h1>

        <div class="portfolio-box">
            <h3>Net P&L</h3>
            <h2 style="color: {pnl_color}; margin: 5px 0;">${st['net_pnl']:.2f}</h2>
            <p style="margin: 0; color: #888;">Total Trades Executed: {total_trades}</p>
        </div>

        <div class="card">
            <h3>{SYMBOL}</h3>
            <div class="row"><span>Status:</span> <b>{st['status']}</b></div>
            <div class="row"><span>Last Price:</span> <b>{st['last_price']}</b></div>
            <div class="row"><span>Entry Price:</span> <b>{st['entry']}</b></div>
            <div class="row"><span>SL / TP:</span> <b>{st['sl']} / {st['tp']}</b></div>
            <div class="row"><span>Trail Mode:</span> <b>{st['trail_mode']}</b></div>
            <div class="row"><span>Candle Range Filter:</span> <b>{CANDLE_RANGE_MIN_POINTS} - {CANDLE_RANGE_MAX_POINTS} pts</b></div>
            <div class="row"><span>Timeframe:</span> <b>{CANDLE_RESOLUTION}</b></div>
            <div class="row"><span>RR Ratio:</span> <b>1:{RR_RATIO}</b></div>
            <div class="row"><span>Wins / Losses:</span> <b>{st['wins']} / {st['losses']}</b></div>
        </div>

        <h2>Live Executed Trade Logs</h2>
        <table>
            <tr>
                <th>Entry Time</th>
                <th>Exit Time</th>
                <th>Coin</th>
                <th>Type</th>
                <th>Entry Price</th>
                <th>Exit Price</th>
                <th>P&L</th>
            </tr>
            {logs_rows if logs_rows else '<tr><td colspan="7" style="color: #777;">No trades executed yet.</td></tr>'}
        </table>
    </body>
    </html>
    """

@app.route("/ping")
def ping():
    return {"status": "alive", "time": get_ist_time()}, 200

@app.route("/api/status")
def api_status():
    return {
        "success": True,
        "timestamp": get_ist_time(),
        "symbol": SYMBOL,
        "candle_range_min_points": CANDLE_RANGE_MIN_POINTS,
        "candle_range_max_points": CANDLE_RANGE_MAX_POINTS,
        "candle_resolution": CANDLE_RESOLUTION,
        "rr_ratio": RR_RATIO,
        "bot_state": bot_state_data
    }, 200

@app.route("/api/trades")
def api_trades():
    return {
        "success": True,
        "timestamp": get_ist_time(),
        "trade_history": trade_history
    }, 200


if __name__ == "__main__":
    threading.Thread(target=background_bot_loop, daemon=True).start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
