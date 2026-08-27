from flask import Flask
import threading
import time
import os
import ccxt

app = Flask(__name__)

@app.route('/')
def home():
    return "Delta Bot is Live!"

# API Details
API_KEY = '4vtWGaF4x4LWleMfoj1ztriQp7rweE'
API_SECRET = 'dsuv5MuOGueu7OKXBo0U6CFCHryeEgujn3l7YD5rb5ibsWKDMRVU0BrQDhmW'

def check_balance():
    # Delta Exchange India / Global Connection
    exchange = ccxt.delta({
        'apiKey': API_KEY,
        'secret': API_SECRET,
        'enableRateLimit': True,
    })

    while True:
        try:
            print("\n==============================")
            print("Fetching Delta Balance...")
            
            # Balance Fetch
            balance = exchange.fetch_balance()
            
            # Total Balance Print
            if 'total' in balance:
                print("Total Assets:", balance['total'])
            else:
                print("Raw Balance Response:", balance)
                
            print("==============================\n")

        except Exception as e:
            print(f"Error fetching balance: {e}")
            
        # Har 15 second me repeat karega
        time.sleep(15)

if __name__ == '__main__':
    # Background Thread Start
    threading.Thread(target=check_balance).start()
    
    # Render Web Server
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
