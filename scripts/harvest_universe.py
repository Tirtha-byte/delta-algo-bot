import os
import json
import time
import sys

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from delta_client import delta_client

DATA_DIR = os.path.join(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")), "data", "historical")
os.makedirs(DATA_DIR, exist_ok=True)

SYMBOLS = [
    "BTCUSD", "ETHUSD", "SOLUSD",
    "NVDAXUSD", "TSLAXUSD", "PLTRBUSD",
    "MSTRBUSD", "AAPLXUSD", "COINXUSD", "AMZNXUSD"
]

RESOLUTIONS = ["5m", "15m"]

def harvest():
    print(f"[Harvester] Ingesting multi-asset historical dataset into {DATA_DIR}...")
    for sym in SYMBOLS:
        for res in RESOLUTIONS:
            try:
                # Fetch 300 candles to ensure a solid window
                candles = delta_client.get_candles(sym, resolution=res, count=300)
                if not candles:
                    print(f"  [WARN] No candles returned for {sym} ({res})")
                    continue
                file_path = os.path.join(DATA_DIR, f"{sym}_{res}.json")
                with open(file_path, "w", encoding="utf-8") as f:
                    json.dump(candles, f, indent=2)
                print(f"  [OK] Saved {len(candles)} candles for {sym} ({res}) -> {file_path}")
            except Exception as e:
                print(f"  [ERROR] Failed to fetch {sym} ({res}): {e}")
            time.sleep(0.3)  # Rate-limit cushion
    print("[Harvester] Universe harvest complete.")

if __name__ == "__main__":
    harvest()
