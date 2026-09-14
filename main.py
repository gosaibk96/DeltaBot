import hmac
import hashlib
import time
import json
import requests
import math
import os
import threading
from datetime import datetime
import pytz

# ============================================================
# GLOBAL SETTINGS (shared across all coins)
# ============================================================

API_KEY = "your_api_key"
API_SECRET = "your_api_secret"

BASE_URL = "https://api.india.delta.exchange"

POLL_INTERVAL = 2                    # Seconds between each live price/position check
STOP_TRIGGER_METHOD = "mark_price"   # mark_price / last_traded_price / spot_price

RESOLUTION_SECONDS = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600,
    "1d": 86400, "1w": 604800
}

# ============================================================
# PER-COIN SETTINGS
# ============================================================

SYMBOLS = {
    "DOTUSD": {
        "product_id": 15304,
        "quantity": 5,
        "tick_size": 0.001,
        "candle_resolution": "15m",
        "narrow_range_pct": 0.5,
        "rr_ratio": 4,
        "trail_trigger_pct": 0.1,
        "trail_step_pct": 0.1,
    },
    "XRPUSD": {
        "product_id": 14969,
        "quantity": 5,
        "tick_size": 0.0001,
        "candle_resolution": "15m",
        "narrow_range_pct": 0.5,
        "rr_ratio": 4,
        "trail_trigger_pct": 0.1,
        "trail_step_pct": 0.1,
    },
    "GRAMUSD": {
        "product_id": 141650,
        "quantity": 5,
        "tick_size": 0.001,
        "candle_resolution": "15m",
        "narrow_range_pct": 0.5,
        "rr_ratio": 4,
        "trail_trigger_pct": 0.1,
        "trail_step_pct": 0.1,
    },
    "PIEVERSEUSD": {
        "product_id": 131978,
        "quantity": 5,
        "tick_size": 0.0001,
        "candle_resolution": "15m",
        "narrow_range_pct": 0.5,
        "rr_ratio": 4,
        "trail_trigger_pct": 0.1,
        "trail_step_pct": 0.1,
    },
    "RIVERUSD": {
        "product_id": 115664,
        "quantity": 5,
        "tick_size": 0.001,
        "candle_resolution": "15m",
        "narrow_range_pct": 0.5,
        "rr_ratio": 4,
        "trail_trigger_pct": 0.1,
        "trail_step_pct": 0.1,
    },
    "MUSD": {
        "product_id": 84925,
        "quantity": 5,
        "tick_size": 0.0001,
        "candle_resolution": "15m",
        "narrow_range_pct": 0.5,
        "rr_ratio": 4,
        "trail_trigger_pct": 0.1,
        "trail_step_pct": 0.1,
    },
    "ZROUSD": {
        "product_id": 26457,
        "quantity": 5,
        "tick_size": 0.0001,
        "candle_resolution": "15m",
        "narrow_range_pct": 0.5,
        "rr_ratio": 4,
        "trail_trigger_pct": 0.1,
        "trail_step_pct": 0.1,
    },
    "FILUSD": {
        "product_id": 19617,
        "quantity": 5,
        "tick_size": 0.0001,
        "candle_resolution": "15m",
        "narrow_range_pct": 0.5,
        "rr_ratio": 4,
        "trail_trigger_pct": 0.1,
        "trail_step_pct": 0.1,
    },
}

# Global Data Store for Dashboard Tracking
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

def get_next_candle_close_time(resolution):
    candle_seconds = get_candle_seconds(resolution)
    now = time.time()
    return (int(now) // candle_seconds + 1) * candle_seconds

def fetch_candle_by_start_time(symbol, resolution, expected_start_time):
    candle_seconds = get_candle_seconds(resolution)
    end_ts = int(time.time())
    start_ts = expected_start_time - (candle_seconds * 3)
    path = "/v2/history/candles"
    url = BASE_URL + path
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
        resp = requests.get(url, params={"product_id": product_id}, headers=headers, timeout=(3, 10))
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

def place_market_order(product_id, side, size):
    method = "POST"
    path = "/v2/orders"
    url = BASE_URL + path
    payload_dict = {"product_id": product_id, "size": size, "side": side, "order_type": "market_order"}
    payload = json.dumps(payload_dict)
    headers = get_headers(method, path, "", payload)
    try:
        resp = requests.post(url, data=payload, headers=headers, timeout=(3, 10))
        return resp.json()
    except Exception as e:
        print(f"[{get_ist_time()}] Error placing market order: {e}", flush=True)
        return None

def get_average_fill_price(order_response):
    if not order_response or not order_response.get("success"):
        return None, None
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
    return (float(fill_price) if fill_price else None), order_id

def place_bracket_sl_tp(symbol, product_id, stop_price, take_profit_price):
    method = "POST"
    path = "/v2/orders/bracket"
    url = BASE_URL + path
    payload_dict = {
        "product_id": product_id,
        "stop_loss_order": {"order_type": "market_order", "stop_price": str(stop_price)},
        "take_profit_order": {"order_type": "market_order", "stop_price": str(take_profit_price)},
        "bracket_stop_trigger_method": STOP_TRIGGER_METHOD,
    }
    payload = json.dumps(payload_dict)
    headers = get_headers(method, path, "", payload)
    try:
        resp = requests.post(url, data=payload, headers=headers, timeout=(3, 10))
        result = resp.json()
        if not result.get("success"):
            print(f"[{get_ist_time()}][{symbol}] ERROR: Bracket SL/TP placement FAILED -> {result.get('error')}", flush=True)
        return result
    except Exception as e:
        print(f"[{get_ist_time()}][{symbol}] Error placing bracket order: {e}", flush=True)
        return None

def edit_bracket_stop_loss(symbol, order_id, product_id, new_sl_price):
    method = "PUT"
    path = "/v2/orders/bracket"
    url = BASE_URL + path
    payload_dict = {"id": order_id, "product_id": product_id, "bracket_stop_loss_price": str(new_sl_price)}
    payload = json.dumps(payload_dict)
    headers = get_headers(method, path, "", payload)
    try:
        resp = requests.put(url, data=payload, headers=headers, timeout=(3, 10))
        result = resp.json()
        if not result.get("success"):
            print(f"[{get_ist_time()}][{symbol}] ERROR: Trailing SL update FAILED -> {result.get('error')}", flush=True)
        return result
    except Exception as e:
        print(f"[{get_ist_time()}][{symbol}] Error editing bracket stop-loss: {e}", flush=True)
        return None

def evaluate_closed_candle(symbol, resolution, narrow_range_pct, candle_start_time):
    candle = fetch_candle_by_start_time(symbol, resolution, candle_start_time)
    if not candle:
        return None
    high = float(candle["high"])
    low = float(candle["low"])
    if low <= 0:
        return None
    range_pct = (high - low) / low * 100
    if range_pct < narrow_range_pct:
        bot_states[symbol]["status"] = f"Setup Active (H:{high}, L:{low})"
        return {"high": high, "low": low}
    else:
        bot_states[symbol]["status"] = f"Monitoring (Range: {round(range_pct, 2)}%)"
        return None

def execute_breakout_trade(symbol, cfg, side, reference_candle):
    product_id = cfg["product_id"]
    quantity = cfg["quantity"]
    rr_ratio = cfg["rr_ratio"]
    tick_size = cfg["tick_size"]

    order_resp = place_market_order(product_id, side, quantity)
    if not order_resp or not order_resp.get("success"):
        return None

    entry_price, order_id = get_average_fill_price(order_resp)
    if entry_price is None:
        return None

    if side == "buy":
        sl_price = round_to_tick(reference_candle["low"], tick_size)
        sl_distance = entry_price - sl_price
        tp_price = round_to_tick(entry_price + (rr_ratio * sl_distance), tick_size)
    else:
        sl_price = round_to_tick(reference_candle["high"], tick_size)
        sl_distance = sl_price - entry_price
        tp_price = round_to_tick(entry_price - (rr_ratio * sl_distance), tick_size)

    bot_states[symbol]["status"] = f"{side.upper()} Executed"
    bot_states[symbol]["entry"] = entry_price
    bot_states[symbol]["sl"] = sl_price
    bot_states[symbol]["tp"] = tp_price
    bot_states[symbol]["trail_level"] = 0

    place_bracket_sl_tp(symbol, product_id, sl_price, tp_price)

    return {
        "symbol": symbol,
        "side": side.upper(),
        "entry_price": entry_price,
        "sl_price": sl_price,
        "tp_price": tp_price,
        "order_id": order_id,
        "trail_level": 0,
        "entry_time": get_ist_time()
    }

def update_trailing_sl(symbol, cfg, position_state, current_price):
    product_id = cfg["product_id"]
    trigger_pct = cfg["trail_trigger_pct"]
    step_pct = cfg["trail_step_pct"]
    tick_size = cfg["tick_size"]
    entry_price = position_state["entry_price"]
    step_amount = entry_price * (step_pct / 100)
    trigger_amount = entry_price * (trigger_pct / 100)

    if trigger_amount <= 0:
        return

    if position_state["side"] == "BUY":
        favorable_move = current_price - entry_price
        new_level = int(favorable_move // trigger_amount)
        if new_level > position_state["trail_level"] and new_level >= 1:
            new_sl = round_to_tick(entry_price + (new_level - 1) * step_amount, tick_size)
            if new_sl > position_state["sl_price"]:
                resp = edit_bracket_stop_loss(symbol, position_state["order_id"], product_id, new_sl)
                if resp and resp.get("success"):
                    position_state["sl_price"] = new_sl
                    position_state["trail_level"] = new_level
                    bot_states[symbol]["sl"] = new_sl
                    bot_states[symbol]["trail_level"] = new_level
    else:
        favorable_move = entry_price - current_price
        new_level = int(favorable_move // trigger_amount)
        if new_level > position_state["trail_level"] and new_level >= 1:
            new_sl = round_to_tick(entry_price - (new_level - 1) * step_amount, tick_size)
            if new_sl < position_state["sl_price"]:
                resp = edit_bracket_stop_loss(symbol, position_state["order_id"], product_id, new_sl)
                if resp and resp.get("success"):
                    position_state["sl_price"] = new_sl
                    position_state["trail_level"] = new_level
                    bot_states[symbol]["sl"] = new_sl
                    bot_states[symbol]["trail_level"] = new_level

def background_bot_loop():
    print("Starting multi-symbol narrow-range breakout bot worker...", flush=True)
    state = {}
    for symbol, cfg in SYMBOLS.items():
        next_close = get_next_candle_close_time(cfg["candle_resolution"])
        state[symbol] = {"next_close_time": next_close, "reference_candle": None, "position": None}
        bot_states[symbol]["status"] = "Waiting for candle close"

    while True:
        try:
            now = time.time()
            for symbol, cfg in SYMBOLS.items():
                sym_state = state[symbol]
                product_id = cfg["product_id"]
                resolution = cfg["candle_resolution"]
                candle_seconds = get_candle_seconds(resolution)

                if now >= sym_state["next_close_time"] + 2:
                    if sym_state["position"] is None:
                        closed_start = sym_state["next_close_time"] - candle_seconds
                        sym_state["reference_candle"] = evaluate_closed_candle(symbol, resolution, cfg["narrow_range_pct"], closed_start)
                    sym_state["next_close_time"] += candle_seconds

                if sym_state["position"] is not None:
                    size = get_position_size(product_id)
                    if size == 0:
                        pos = sym_state["position"]
                        exit_time = get_ist_time()
                        price = get_mark_price(symbol) or pos["entry_price"]
                        
                        if pos["side"] == "BUY":
                            pnl = (price - pos["entry_price"]) * cfg["quantity"]
                            is_win = price >= pos["tp_price"] or pnl > 0
                        else:
                            pnl = (pos["entry_price"] - price) * cfg["quantity"]
                            is_win = price <= pos["tp_price"] or pnl > 0

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
                            "exit": price,
                            "pnl": round(pnl, 2)
                        })

                        sym_state["position"] = None
                        bot_states[symbol]["status"] = "Flat / Monitoring"
                        bot_states[symbol]["entry"] = "-"
                        bot_states[symbol]["sl"] = "-"
                        bot_states[symbol]["tp"] = "-"
                        bot_states[symbol]["trail_level"] = 0
                    else:
                        price = get_mark_price(symbol)
                        if price is not None:
                            bot_states[symbol]["last_price"] = price
                            update_trailing_sl(symbol, cfg, sym_state["position"], price)

                elif sym_state["reference_candle"] is not None:
                    price = get_mark_price(symbol)
                    if price is not None:
                        bot_states[symbol]["last_price"] = price
                        ref = sym_state["reference_candle"]
                        if price > ref["high"]:
                            result = execute_breakout_trade(symbol, cfg, "buy", ref)
                            if result:
                                sym_state["position"] = result
                                sym_state["reference_candle"] = None
                        elif price < ref["low"]:
                            result = execute_breakout_trade(symbol, cfg, "sell", ref)
                            if result:
                                sym_state["position"] = result
                                sym_state["reference_candle"] = None
                else:
                    price = get_mark_price(symbol)
                    if price is not None:
                        bot_states[symbol]["last_price"] = price

            time.sleep(POLL_INTERVAL)
        except Exception as e:
            print(f"[{get_ist_time()}] Main loop error: {e}", flush=True)
            time.sleep(POLL_INTERVAL)

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
            <div class="row"><span>Active SL / TP:</span> <b>{st['sl']} / {st['tp']}</b></div>
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
        <h1>🚀 Breakout & TSL Multi-Coin Dashboard</h1>
        
        <div class="portfolio-box">
            <h3>Total Portfolio Net P&L</h3>
            <h2 style="color: {pnl_color}; margin: 5px 0;">${total_pnl:.2f}</h2>
            <p style="margin: 0; color: #888;">Total Trades Executed: {total_trades}</p>
        </div>

        <h2>📊 Coin-wise Performance Cards</h2>
        <div class="grid">
            {cards_html}
        </div>

        <h2>📜 Live Executed Trade Logs</h2>
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

if __name__ == "__main__":
    threading.Thread(target=background_bot_loop, daemon=True).start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
