from flask import Flask
import threading
import time
import os
import requests
import hmac
import hashlib

app = Flask(__name__)

@app.route('/')
def home():
    return "Delta India Bot Active"

# Keys daalein
API_KEY = '4vtWGaF4x4LWleMfoj1ztriQp7rweE'
API_SECRET = 'dsuv5MuOGueu7OKXBo0U6CFCHryeEgujn3l7YD5rb5ibsWKDMRVU0BrQDhmW'

def get_delta_india_balance():
    # Delta India Base URL
    base_url = "https://api.india.delta.exchange"
    path = "/v2/wallet/balances"
    url = base_url + path
    method = "GET"

    # Precise Timestamp (Seconds)
    timestamp = str(int(time.time()))
    
    # Signature Payload (Method + Timestamp + Path)
    payload = method + timestamp + path
    
    # HMAC SHA256 Generation
    signature = hmac.new(
        API_SECRET.encode('utf-8'),
        payload.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()

    # Exact Headers Required by Delta India
    headers = {
        'api-key': API_KEY.strip(),
        'signature': signature,
        'timestamp': timestamp,
        'Content-Type': 'application/json',
        'User-Agent': 'Mozilla/5.0'
    }

    try:
        response = requests.get(url, headers=headers, timeout=10)
        data = response.json()

        print("\n==============================")
        print("TIMESTAMP:", timestamp)
        print("RESPONSE STATUS:", response.status_code)
        
        if response.status_code == 200 and data.get('success'):
            print("✅ SUCCESS! BALANCE FETCHED:")
            balances = data.get('result', [])
            for item in balances:
                print(f"Asset: {item.get('asset_symbol')} | Balance: {item.get('balance')} | Available: {item.get('available_balance')}")
            if not balances:
                print("Wallet Balance is 0")
        else:
            print("❌ DELTA ERROR RESPONSE:", data)
            
        print("==============================\n")

    except Exception as e:
        print(f"❌ CONNECTION EXCEPTION: {e}")

def run_loop():
    time.sleep(3)
    while True:
        get_delta_india_balance()
        time.sleep(20)

if __name__ == '__main__':
    threading.Thread(target=run_loop, daemon=True).start()
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
