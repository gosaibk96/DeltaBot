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

API_KEY = "4vtWGaF4x4LWleMfoj1ztriQp7rweE"
API_SECRET = "dsuv5MuOGueu7OKXBo0U6CFCHryeEgujn3l7YD5rb5ibsWKDMRVU0BrQDhmW"

BASE_URL = "https://api.india.delta.exchange"

POLL_INTERVAL = 2
STOP_TRIGGER_METHOD = "mark_price"
TRAIL_STEP_PCT = 0.25
CANDLE_FETCH_RETRY_WINDOW = 20

RESOLUTION_SECONDS = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600,
    "1d": 86400, "1w": 604800
}

# ============================================================
# PER-COIN SETTINGS
# NOTE: "10m" is NOT a supported resolution on Delta Exchange.
# Supported values are: 1m, 3m, 5m, 15m, 30m, 1h, 2h, 4h, 6h, 1d, 1w
# If you need ~10 minutes, use "15m" instead. Set quantity to 0
# for any coin you want to disable.
# ============================================================

SYMBOLS = {
    "XRPUSD": {"product_id": 14969, "quantity": 2, "tick_size": 0.0001, "candle_resolution": "15m", "narrow_range_pct": 0.5, "rr_ratio": 4},
    "SUIUSD": {"product_id": 17328, "quantity": 2, "tick_size": 0.0001, "candle_resolution": "15m", "narrow_range_pct": 0.5, "rr_ratio": 4},
    "EVAAUSD": {"product_id": 98745, "quantity": 2, "tick_size": 0.0001, "candle_resolution": "15m", "narrow_range_pct": 0.5, "rr_ratio": 4},
    "COAIUSD": {"product_id": 98572, "quantity": 2, "tick_size": 0.0001, "candle_resolution": "15m", "narrow_range_pct": 0.5, "rr_ratio": 4},
    "ASTERUSD": {"product_id": 96160, "quantity": 2, "tick_size": 0.0001, "candle_resolution": "15m", "narrow_range_pct": 0.5, "rr_ratio": 4},
    "MUSD": {"product_id": 84925, "quantity": 2, "tick_size": 0.0001, "candle_resolution": "15m", "narrow_range_pct": 0.5, "rr_ratio": 4},
    "ZROUSD": {"product_id": 26457, "quantity": 2, "tick_size": 0.0001, "candle_resolution": "15m", "narrow_range_pct": 0.5, "rr_ratio": 4},
    "RUNEUSD": {"product_id": 21522, "quantity": 2, "tick_size": 0.0001, "candle_resolution": "15m", "narrow_range_pct": 0.5, "rr_ratio": 4},
    "APTUSD": {"product_id": 20196, "quantity": 2, "tick_size": 0.0001, "candle_resolution": "15m", "narrow_range_pct": 0.5, "rr_ratio": 4},
    "FILUSD": {"product_id": 19617, "quantity": 2, "tick_size": 0.0001, "candle_resolution": "15m", "narrow_range_pct": 0.5, "rr_ratio": 4},
    "LDOUSD": {"product_id": 19616, "quantity": 2, "tick_size": 0.0001, "candle_resolution": "15m", "narrow_range_pct": 0.5, "rr_ratio": 4},
}

# ============================================================

trade_history = []
bot_states = {sym: {"status": "Initializing", "last_price": 0.0, "entry": "-", "sl": "-", "tp": "-", "trail_level": 0, "wins": 0, "losses": 0, "net_pnl": 0.0} for sym in SYMBOLS}

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
def place_market_order_with_bracket(product_id, side, size, bracket_sl_price, bracket_tp_price):
    method = "POST"
    path = "/v2/orders"
    url = BASE_URL + path
    payload_dict = {
        "product_id": product_id,
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
        print(f"[{get_ist_time()}] Error placing market order with bracket: {e}", flush=True)
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

def emergency_close_position(symbol, product_id, side, quantity):
    close_side = "sell" if side == "buy" else "buy"
    print(f"[{get_ist_time()}][{symbol}] EMERGENCY CLOSE triggered -> closing naked position via market {close_side} order", flush=True)
    resp = place_market_order_with_bracket(product_id, close_side, quantity, "0", "0")
    if not resp or not resp.get("success"):
        print(f"[{get_ist_time()}][{symbol}] CRITICAL: Emergency close FAILED -> {resp}. Manual intervention required!", flush=True)
    return resp

def edit_bracket_order(symbol, product_id, order_id, sl_price=None, tp_price=None):
    method = "PUT"
    path = "/v2/orders/bracket"
    url = BASE_URL + path
    payload_dict = {
        "id": order_id,
        "product_id": product_id,
    }
    if sl_price is not None:
        payload_dict["bracket_stop_loss_price"] = str(sl_price)
    if tp_price is not None:
        payload_dict["bracket_take_profit_price"] = str(tp_price)

    payload = json.dumps(payload_dict)
    headers = get_headers(method, path, "", payload)
    try:
        resp = requests.put(url, data=payload, headers=headers, timeout=(3, 10))
        return resp.json()
    except Exception as e:
        print(f"[{get_ist_time()}][{symbol}] Error editing bracket order: {e}", flush=True)
        return None

def check_and_trail_sl(symbol, cfg, pos, live_price):
    entry_price = pos["entry_price"]
    tick_size = cfg["tick_size"]
    step_value = entry_price * (TRAIL_STEP_PCT / 100)

    if pos["side"] == "BUY":
        move_pct = (live_price - entry_price) / entry_price * 100
    else:
        move_pct = (entry_price - live_price) / entry_price * 100

    if move_pct < TRAIL_STEP_PCT:
        return

    steps_reached = int(move_pct // TRAIL_STEP_PCT)
    if steps_reached <= pos["trail_steps"]:
        return

    if pos["side"] == "BUY":
        new_sl = pos["initial_sl"] + (steps_reached * step_value)
    else:
        new_sl = pos["initial_sl"] - (steps_reached * step_value)

    new_sl = round_to_tick(new_sl, tick_size)
    new_sl_str = format_price(new_sl, tick_size)

    result = edit_bracket_order(symbol, cfg["product_id"], pos["order_id"], sl_price=new_sl_str)
    if result and result.get("success"):
        pos["trail_steps"] = steps_reached
        pos["current_sl"] = new_sl
        bot_states[symbol]["sl"] = new_sl
        bot_states[symbol]["trail_level"] = steps_reached
        print(f"[{get_ist_time()}][{symbol}] TRAIL STEP {steps_reached} -> SL moved to {new_sl_str}", flush=True)
    else:
        print(f"[{get_ist_time()}][{symbol}] Trail SL update FAILED at step {steps_reached} -> {result}", flush=True)

# ==================== CANDLE EVALUATION ====================
def evaluate_candle_data(symbol, candle, narrow_range_pct):
    high = float(candle["high"])
    low = float(candle["low"])
    if low <= 0:
        print(f"[{get_ist_time()}][{symbol}] Invalid low<=0, skipping candle", flush=True)
        return None

    range_pct = (high - low) / low * 100
    print(f"[{get_ist_time()}][{symbol}] Closed Candle -> High: {high}, Low: {low}, Range%: {round(range_pct, 4)}, Threshold: {narrow_range_pct}, Qualifies: {range_pct < narrow_range_pct}", flush=True)

    if range_pct < narrow_range_pct:
        bot_states[symbol]["status"] = f"Setup Active (H:{high}, L:{low})"
        return {"high": high, "low": low}
    else:
        bot_states[symbol]["status"] = f"Monitoring (Range: {round(range_pct, 2)}%)"
        return None

# ==================== TRADE EXECUTION ====================
def execute_breakout_trade(symbol, cfg, side, reference_candle, estimated_entry_price):
    product_id = cfg["product_id"]
    quantity = cfg["quantity"]
    rr_ratio = cfg["rr_ratio"]
    tick_size = cfg["tick_size"]

    if side == "buy":
        initial_sl = round_to_tick(reference_candle["low"], tick_size)
        est_sl_distance = estimated_entry_price - initial_sl
        est_tp = round_to_tick(estimated_entry_price + (rr_ratio * est_sl_distance), tick_size)
    else:
        initial_sl = round_to_tick(reference_candle["high"], tick_size)
        est_sl_distance = initial_sl - estimated_entry_price
        est_tp = round_to_tick(estimated_entry_price - (rr_ratio * est_sl_distance), tick_size)

    initial_sl_str = format_price(initial_sl, tick_size)
    est_tp_str = format_price(est_tp, tick_size)

    order_resp = place_market_order_with_bracket(product_id, side, quantity, initial_sl_str, est_tp_str)
    if not order_resp or not order_resp.get("success"):
        print(f"[{get_ist_time()}][{symbol}] Market order with bracket FAILED -> {order_resp}", flush=True)
        return None

    entry_price, order_id = get_average_fill_price(order_resp)
    if entry_price is None:
        print(f"[{get_ist_time()}][{symbol}] WARNING: Could not fetch exact fill price after retries. Using estimated entry ({estimated_entry_price}) for tracking.", flush=True)
        entry_price = estimated_entry_price
    else:
        if side == "buy":
            exact_sl_distance = entry_price - initial_sl
            exact_tp = round_to_tick(entry_price + (rr_ratio * exact_sl_distance), tick_size)
        else:
            exact_sl_distance = initial_sl - entry_price
            exact_tp = round_to_tick(entry_price - (rr_ratio * exact_sl_distance), tick_size)

        exact_tp_str = format_price(exact_tp, tick_size)
        if exact_tp_str != est_tp_str:
            correction = edit_bracket_order(symbol, product_id, order_id, tp_price=exact_tp_str)
            if correction and correction.get("success"):
                print(f"[{get_ist_time()}][{symbol}] TP corrected for slippage -> {exact_tp_str}", flush=True)
                est_tp = exact_tp

    print(f"[{get_ist_time()}][{symbol}] ENTRY {side.upper()} @ {entry_price} | SL(candle)={initial_sl_str} | TP={format_price(est_tp, tick_size)}", flush=True)

    bot_states[symbol]["status"] = f"{side.upper()} Executed"
    bot_states[symbol]["entry"] = entry_price
    bot_states[symbol]["sl"] = initial_sl
    bot_states[symbol]["tp"] = est_tp
    bot_states[symbol]["trail_level"] = 0

    return {
        "symbol": symbol,
        "side": side.upper(),
        "entry_price": entry_price,
        "initial_sl": initial_sl,
        "current_sl": initial_sl,
        "tp_price": est_tp,
        "order_id": order_id,
        "trail_steps": 0,
        "entry_time": get_ist_time()
    }

# ==================== BACKGROUND WORKER LOOP ====================
def background_bot_loop():
    print("Starting multi-symbol narrow-range breakout bot worker...", flush=True)
    state = {}
    for symbol, cfg in SYMBOLS.items():
        next_close = get_next_candle_close_time(cfg["candle_resolution"])
        state[symbol] = {
            "next_close_time": next_close,
            "reference_candle": None,
            "position": None,
            "cooldown_until": 0,
            "pending_candle_start": None,
            "pending_deadline": None,
        }
        bot_states[symbol]["status"] = "Waiting for candle close"

    while True:
        try:
            now = time.time()

            for symbol, cfg in SYMBOLS.items():
                if cfg["quantity"] <= 0:
                    continue

                sym_state = state[symbol]
                product_id = cfg["product_id"]
                resolution = cfg["candle_resolution"]
                candle_seconds = get_candle_seconds(resolution)
                live_price = None

                if now < sym_state["cooldown_until"] and sym_state["position"] is None:
                    price = get_mark_price(symbol)
                    if price is not None:
                        bot_states[symbol]["last_price"] = price
                        live_price = price
                    remaining = int(sym_state["cooldown_until"] - now)
                    bot_states[symbol]["status"] = f"Paused - cooling down ({remaining}s left)"
                    continue

                if now >= sym_state["next_close_time"] and sym_state["pending_candle_start"] is None and sym_state["position"] is None:
                    sym_state["pending_candle_start"] = sym_state["next_close_time"] - candle_seconds
                    sym_state["pending_deadline"] = now + CANDLE_FETCH_RETRY_WINDOW
                    sym_state["next_close_time"] += candle_seconds

                if sym_state["pending_candle_start"] is not None:
                    candle = fetch_candle_by_start_time(symbol, resolution, sym_state["pending_candle_start"])
                    if candle:
                        sym_state["reference_candle"] = evaluate_candle_data(symbol, candle, cfg["narrow_range_pct"])
                        sym_state["pending_candle_start"] = None
                        sym_state["pending_deadline"] = None
                    elif now > sym_state["pending_deadline"]:
                        sym_state["pending_candle_start"] = None
                        sym_state["pending_deadline"] = None

                if sym_state["position"] is not None:
                    pos = sym_state["position"]
                    price = get_mark_price(symbol)
                    if price is not None:
                        bot_states[symbol]["last_price"] = price
                        live_price = price
                        check_and_trail_sl(symbol, cfg, pos, price)

                    size = get_position_size(product_id)
                    if size == 0:
                        exit_time = get_ist_time()
                        exit_price = price if price is not None else pos["entry_price"]

                        if pos["side"] == "BUY":
                            pnl = (exit_price - pos["entry_price"]) * cfg["quantity"]
                            is_win = exit_price >= pos["tp_price"] or pnl > 0
                        else:
                            pnl = (pos["entry_price"] - exit_price) * cfg["quantity"]
                            is_win = exit_price <= pos["tp_price"] or pnl > 0

                        if is_win:
                            bot_states[symbol]["wins"] += 1
                        else:
                            bot_states[symbol]["losses"] += 1

                        bot_states[symbol]["net_pnl"] += pnl

                        trade_history.insert(0, {
                            "time": exit_time,
                            "entry_time": pos["entry_time"],
                            "symbol": symbol,
                            "type": pos["side"],
                            "entry": pos["entry_price"],
                            "exit": exit_price,
                            "pnl": round(pnl, 2)
                        })

                        sym_state["position"] = None
                        bot_states[symbol]["status"] = "Flat / Monitoring"
                        bot_states[symbol]["entry"] = "-"
                        bot_states[symbol]["sl"] = "-"
                        bot_states[symbol]["tp"] = "-"
                        bot_states[symbol]["trail_level"] = 0

                elif sym_state["reference_candle"] is not None:
                    price = get_mark_price(symbol)
                    if price is not None:
                        bot_states[symbol]["last_price"] = price
                        live_price = price
                        ref = sym_state["reference_candle"]
                        if price > ref["high"]:
                            result = execute_breakout_trade(symbol, cfg, "buy", ref, price)
                            if result:
                                sym_state["position"] = result
                            sym_state["reference_candle"] = None
                        elif price < ref["low"]:
                            result = execute_breakout_trade(symbol, cfg, "sell", ref, price)
                            if result:
                                sym_state["position"] = result
                            sym_state["reference_candle"] = None
                else:
                    price = get_mark_price(symbol)
                    if price is not None:
                        bot_states[symbol]["last_price"] = price
                        live_price = price

            time.sleep(POLL_INTERVAL)
        except Exception as e:
            print(f"[{get_ist_time()}] Main loop error: {e}", flush=True)
            time.sleep(POLL_INTERVAL)

# ==================== FLASK WEB DASHBOARD & APIS ====================
@app.route("/")
def dashboard():
    total_trades = sum(st["wins"] + st["losses"] for st in bot_states.values())
    total_pnl = sum(st["net_pnl"] for st in bot_states.values())
    pnl_color = "#00e676" if total_pnl >= 0 else "#ff5252"

    cards_html = ""
    for sym, st in bot_states.items():
        t_trades = st["wins"] + st["losses"]
        s_pnl_color = "#00e676" if st["net_pnl"] >= 0 else "#ff5252"
        cards_html += f"""
        <div class="card">
            <h3>{sym}</h3>
            <div class="row"><span>Status:</span> <b>{st['status']}</b></div>
            <div class="row"><span>Last Price:</span> <b>{st['last_price']}</b></div>
            <div class="row"><span>Entry Price:</span> <b>{st['entry']}</b></div>
            <div class="row"><span>SL / TP:</span> <b>{st['sl']} / {st['tp']}</b></div>
            <div class="row"><span>Trail Steps:</span> <b>{st['trail_level']}</b></div>
            <div class="row"><span>Total Trades:</span> <b>{t_trades}</b></div>
            <div class="row"><span>Wins / Losses:</span> <b>{st['wins']} / {st['losses']}</b></div>
            <div class="row"><span>Net P&L:</span> <b style="color:{s_pnl_color};">${st['net_pnl']:.2f}</b></div>
        </div>
        """

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
        <title>Multi-Coin TSL Dashboard</title>
        <meta http-equiv="refresh" content="5">
        <style>
            body {{ background: #121212; color: #fff; font-family: Arial, sans-serif; padding: 20px; }}
            h1, h2 {{ text-align: center; color: #e0e0e0; }}
            .portfolio-box {{ background: #1f1f1f; border-radius: 10px; padding: 15px; text-align: center; margin-bottom: 25px; border: 1px solid #333; }}
            .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 15px; margin-bottom: 30px; }}
            .card {{ background: #1e1e1e; border: 1px solid #333; border-radius: 8px; padding: 15px; box-shadow: 0 4px 6px rgba(0,0,0,0.3); }}
            .card h3 {{ margin-top: 0; border-bottom: 1px solid #444; padding-bottom: 8px; color: #ffab40; }}
            .row {{ display: flex; justify-content: space-between; margin: 8px 0; font-size: 14px; }}
            table {{ width: 100%; border-collapse: collapse; background: #181818; border-radius: 8px; overflow: hidden; }}
            th, td {{ border: 1px solid #333; padding: 10px; text-align: center; font-size: 14px; }}
            th {{ background: #222; color: #b0bec5; }}
            tr:nth-child(even) {{ background: #161616; }}
        </style>
    </head>
    <body>
        <h1>Breakout & Stepped Trailing SL Multi-Coin Dashboard</h1>

        <div class="portfolio-box">
            <h3>Total Portfolio Net P&L</h3>
            <h2 style="color: {pnl_color}; margin: 5px 0;">${total_pnl:.2f}</h2>
            <p style="margin: 0; color: #888;">Total Trades Executed: {total_trades}</p>
        </div>

        <h2>Coin-wise Performance Cards</h2>
        <div class="grid">
            {cards_html}
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
        "bot_states": bot_states
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
