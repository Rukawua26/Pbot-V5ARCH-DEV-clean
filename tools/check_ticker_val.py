import os
import ccxt
from dotenv import load_dotenv

load_dotenv()

def check_ticker():
    try:
        exchange = ccxt.binance({
            'apiKey': os.getenv('BINANCE_API_KEY'),
            'secret': os.getenv('BINANCE_API_SECRET'),
            'options': {'defaultType': 'future'}
        })
        sym = "BTC/USDT:USDT"
        print(f"Fetching ticker for {sym}...")
        t = exchange.fetch_ticker(sym)
        
        quote_vol = t.get("quoteVolume", "N/A")
        base_vol = t.get("baseVolume", "N/A")
        ask = t.get("ask", 0)
        bid = t.get("bid", 0)
        last = t.get("last", 0)
        spread = (ask - bid) / last if last > 0 else "N/A"
        
        print(f"Quote Volume: {quote_vol}")
        print(f"Base Volume: {base_vol}")
        print(f"Spread: {spread}")
        
        if isinstance(spread, float):
            print(f"Spread %: {spread * 100:.4f}%")
            print(f"Passes 0.05% filter? {spread <= 0.0005}")

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    check_ticker()
