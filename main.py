from flask import Flask
import threading
import time
import os

app = Flask(__name__)

# Render ke liye Dummy Website
@app.route('/')
def home():
    return "Delta Exchange Bot is Running 24/7!"

# Aapka Main Trading Logic Yahan Chalega
def run_bot():
    while True:
        # Aap apni strategy yahan likhenge (Pydroid 3 me test karke)
        # Jaise ki Rs me options payoff calculate karna ya order lagana
        print("Bot is checking market...") 
        time.sleep(60) # Har 1 minute me check karega

if __name__ == '__main__':
    # Bot ko background me start karna
    threading.Thread(target=run_bot).start()
    
    # Render ke liye server start karna
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)

