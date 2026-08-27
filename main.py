from flask import Flask
import threading
import time
import os
import requests
import hmac
import hashlib
import json

app = Flask(__name__)

@app.route('/')
def home():
    return "Delta India BTC Futures Trade Bot Active!"

# Apni Keys Yahan Daalein
API_KEY = '4vtWGaF4x4LWleMfoj1ztriQp7rweE'
API_SECRET = 'dsuv5MuOGueu7OKXBo0U6CFCHryeEgujn3l7YD5rb5ibsWKDMRVU0BrQDhmW'
BASE_URL = "https://api.india.delta.exchange"

# Signature Generator
def generate_signature(method, timestamp, path, payload=""):
    signature_data = method + timestamp + path + payload
    return hmac.new(
        API_SECRET.strip().encode('utf-8'),
        signature_data.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()

# Order Placement Function
def execute_btc_trade():
    time.sleep(5)  # Render boot sequence delay
    
    print("\n========================================", flush=True)
    print("🚀 EXECUTING 1 LOT BTC FUTURES MARKET BUY ORDER", flush=True)
    
    path = "/v2/orders"
    timestamp = str(int(time.time()))
    
    # Delta India me BTCUSD Perpetual Futures ka Product ID = 27 hai
    # size: 1 ka matlab hai 1 Lot (0.001 BTC)
    payload = json.dumps({
        "product_id": 27,           # BTC Futures Contract
        "size": 1,                  # 1 Lot = 0.001 BTC
        "side": "buy",              # Market Buy (Long)
        "order_type": "market_order"
    })
    
    headers = {
        'api-key': API_KEY.strip(),
        'signature': generate_signature("POST", timestamp, path, payload),
        'timestamp': timestamp,
        'Content-Type': 'application/json',
        'User-Agent': 'Mozilla/5.0'
    }
    
    try:
        response = requests.post(BASE_URL + path, headers=headers, data=payload, timeout=10)
        res_data = response.json()
        
        print(f"HTTP STATUS: {response.status_code}", flush=True)
        print("DELTA API RESPONSE:", json.dumps(res_data, indent=2), flush=True)
        
        if res_data.get('success'):
            print("✅ TEST TRADE PLACED SUCCESSFULLY!", flush=True)
        else:
            print("❌ ORDER PLACEMENT FAILED:", res_data, flush=True)
            
    except Exception as e:
        print(f"CRITICAL ERROR: {e}", flush=True)
        
    print("========================================\n", flush=True)

# Application startup par ek baar order trigger karega
threading.Thread(target=execute_btc_trade, daemon=True).start()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
