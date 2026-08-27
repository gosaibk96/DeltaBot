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
    return "Delta Bot is Live & Working 24/7!"

# ==========================================
# APNI KEYS YAHAN DAALEIN (Quotes '' ke andar)
API_KEY = '4vtWGaF4x4LWleMfoj1ztriQp7rweE'
API_SECRET = 'dsuv5MuOGueu7OKXBo0U6CFCHryeEgujn3l7YD5rb5ibsWKDMRVU0BrQDhmW'
# ==========================================

def get_delta_balance():
    base_url = "https://api.india.delta.exchange" # Delta India Endpoint
    endpoint = "/v2/wallet/balances"
    url = base_url + endpoint
    
    timestamp = str(int(time.time()))
    signature_data = "GET" + timestamp + endpoint
    signature = hmac.new(
        API_SECRET.encode('utf-8'),
        signature_data.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()

    headers = {
        'api-key': API_KEY,
        'timestamp': timestamp,
        'signature': signature,
        'User-Agent': 'DeltaBot/1.0'
    }

    try:
        response = requests.get(url, headers=headers)
        res_data = response.json()
        
        print("\n==============================")
        if res_data.get('success'):
            print("✅ DELTA INDIA API CONNECTED SUCCESSFULLY!")
            balances = res_data.get('result', [])
            for b in balances:
                asset = b.get('asset_symbol', 'N/A')
                balance = b.get('balance', '0')
                available = b.get('available_balance', '0')
                if float(balance) > 0 or float(available) > 0:
                    print(f"💰 Asset: {asset} | Balance: {balance} | Available: {available}")
            if not balances:
                print("💰 Wallet Balance: 0 (No active funds)")
        else:
            print("❌ Error from Delta:", res_data)
        print("==============================\n")
        
    except Exception as e:
        print(f"❌ Connection Error: {e}")

def run_bot_loop():
    time.sleep(5) # Delay for server startup
    while True:
        get_delta_balance()
        time.sleep(30) # Har 30 seconds me live update dega

if __name__ == '__main__':
    # Start Bot Loop
    threading.Thread(target=run_bot_loop, daemon=True).start()
    
    # Start Render Server
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
