from flask import Flask
import threading
import time
import os
import ccxt

app = Flask(__name__)

@app.route('/')
def home():
    return "Delta Bot is Live!"

# API Details (Apni Keys Yahan Replace Karein)
API_KEY = '4vtWGaF4x4LWleMfoj1ztriQp7rweE'
API_SECRET = 'dsuv5MuOGueu7OKXBo0U6CFCHryeEgujn3l7YD5rb5ibsWKDMRVU0BrQDhmW'

def check_balance():
    try:
        # Delta Exchange Connection Setup
        exchange = ccxt.delta({
            'apiKey': API_KEY,
            'secret': API_SECRET,
            'enableRateLimit': True,
        })

        # Balance Fetch Kar Rahe Hain
        balance = exchange.fetch_balance()
        
        print("\n==============================")
        print("Delta Exchange Balance Details:")
        print("==============================")
        
        # Free Balance Print Karein (Total Available)
        if 'free' in balance:
            for currency, amount in balance['free'].items():
                if amount > 0:
                    print(f"{currency}: {amount}")
        else:
            print("No positive balance found or balance structure empty.")
            
        print("==============================\n")

    except Exception as e:
        print(f"Error fetching balance: {e}")

if __name__ == '__main__':
    # Ek baar Balance Check function run karein
    check_balance()
    
    # Render Web Server Keep-Alive
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
