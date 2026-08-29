# Fixed Candles Fetching Function (Correct Delta API Params)
def fetch_candles():
    try:
        resolution_map = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "1h"}
        res_val = resolution_map.get(TIMEFRAME, "1m")
        
        # Calculate time range in UNIX timestamp (seconds)
        to_time = int(time.time())
        from_time = to_time - (100 * 60) # 100 candles of 1 minute
        
        # Delta API expects 'symbol', 'resolution', 'from', and 'to'
        path = f"/v2/chart/candles?symbol={SYMBOL}&resolution={res_val}&from={from_time}&to={to_time}"
        res = requests.get(BASE_URL + path, timeout=10)
        data = res.json()
        
        if data.get('success') and 'result' in data and len(data['result']) > 0:
            df = pd.DataFrame(data['result'])
            # Reverse to make oldest candle first for technical indicators
            df = df.iloc[::-1].reset_index(drop=True)
            df['close'] = df['close'].astype(float)
            df['high'] = df['high'].astype(float)
            df['low'] = df['low'].astype(float)
            df['volume'] = df['volume'].astype(float)
            return df
        else:
            print("Candle API Warning:", data, flush=True)
            return None
    except Exception as e:
        print(f"Error fetching candles: {e}", flush=True)
        return None
