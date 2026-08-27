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
    return "Delta India Bot Working Fine!"

API_KEY = '4vtWGaF4x4LWleMfoj1ztriQp7rweE'
API_SECRET = 'dsuv5MuOGueu7OKXBo0U6CFCHryeEgujn3l7YD5rb5ibsWKDMRVU0BrQDhmW'

def fetch_and_print_balance():
    time.sleep(2) # Server initialization delay
    base_url = "https://api.india.delta.exchange"
    path = "/v2/wallet/balances"
    url = base_url + path
    method = "GET"

    while True:
        try:
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
                'User-Agent': 'Mozilla/5.0'
            }

            response = requests.get(url, headers=headers, timeout=10)
            
            # Direct System Print (Forced output in Render Logs)
            print("----------------------------------------", flush=True)
            print(f"FETCH TIME: {time.strftime('%H:%M:%S')}", flush=True)
            print(f"HTTP STATUS: {response.status_code}", flush=True)
            print(f"DELTA RESPONSE: {response.text}", flush=True)
            print("----------------------------------------\n", flush=True)

        except Exception as err:
            print(f"CRITICAL ERROR: {err}", flush=True)
            
        time.sleep(15)

# Background execution trigger before Flask server
t = threading.Thread(target=fetch_and_print_balance)
t.daemon = True
t.start()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
