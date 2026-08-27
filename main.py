from flask import Flask
import threading
import time
import os
import ccxt

app = Flask(__name__)

@app.route('/')
def home():
    return "Delta India Bot is Live!"

# Apni Delta India API Details Yahan Daalein
API_KEY = '4vtWGaF4x4LWleMfoj1ztriQp7rweE'
API_SECRET = 'dsuv5MuOGueu7OKXBo0U6CFCHryeEgujn3l7YD5rb5ibsWKDMRVU0BrQDhmW'

def check_balance():
    # Delta Exchange India Setup
    exchange = ccxt.delta({
        'apiKey': API_KEY,
        'secret': API_SECRET,
        'enableRateLimit': True,
        'urls': {
            'api': {
                'public': 'https://api.india.delta.exchange',
                'private': 'https://api.india.delta.exchange',
            }
        }
    })

    while True:
        try:
            print("\n==============================")
            print("Fetching Delta India Balance...")
            
            # Balance Request
            balance = exchange.fetch_balance()
            
            print("SUCCESS! Balance Data:")
            print("Total Assets:", balance.get('total', {}))
            print("==============================\n")

        except Exception as e:
            print(f"Error: {e}")
            
        # Har 15 second me repeat karega
        time.sleep(15)

if __name__ == '__main__':
    threading.Thread(target=check_balance).start()
    
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
