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

# API Keys
API_KEY = '4vtWGaF4x4LWleMfoj1ztriQp7rweE'
API_SECRET = 'dsuv5MuOGueu7OKXBo0U6CFCHryeEgujn3l7YD5rb5ibsWKDMRVU0BrQDhmW'

def get_delta_balance():
    # Official Delta India Base URL and Endpoint
    base_url = "https://api.india.delta.exchange"
    path = "/v2/wallet/balances"
    url = base_url + path
    method = "GET"

    # Signature Construction
    timestamp = str(int(time.time()))
    signature_payload = method + timestamp + path
    
    signature = hmac.new(
        API_SECRET.strip().encode('utf-8'),
        signature_payload.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()

    headers = {
        'api-key': API_KEY.strip(),
        'signature': signature,
        'timestamp': timestamp,
        'User-Agent': 'DeltaBot/1.0'
    }

    try:
        response = requests.get(url, headers=headers, timeout=10)
        res_data = response.json()

        print("\n==============================")
        print("HTTP STATUS CODE:", response.status_code)
        
        if response.status_code == 200:
            print("✅ SUCCESS! RESPONSE FROM DELTA INDIA:")
            print(res_data)
        else:
            print("❌ DELTA ERROR RESPONSE:", res_data)
            
        print("==============================\n")

    except Exception as e:
        print(f"❌ Connection Exception: {e}")

def run_loop():
    time.sleep(3)
    while True:
        get_delta_balance()
        time.sleep(20)

if __name__ == '__main__':
    threading.Thread(target=run_loop, daemon=True).start()
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
