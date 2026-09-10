import uvicorn
import requests
from config import delta_config, compounding_config, system_config

def check_connectivity():
    print("=" * 68)
    print("  DELTA EXCHANGE US STOCKS RWA MULTI-AGENT TRADING ENGINE")
    print("=" * 68)
    print(f"  Target Goal        : ${compounding_config.STARTING_CAPITAL:.2f} USD -> ${compounding_config.TARGET_CAPITAL:.2f} USD (+8,233%)")
    print(f"  Delta Gateway      : {delta_config.BASE_URL}")
    print(f"  Asset Universe     : Crypto Blue-Chips (BTC, ETH) + 32 US Stocks & Equity RWAs")
    print(f"  Tracked Tokens     : {', '.join(system_config.US_STOCKS_RWA[:6])} ...")
    mode_label = "Delta Exchange Live API" if system_config.MODE == "LIVE" else "Vibe Shadow Account Simulation"
    print(f"  Execution Mode     : {system_config.MODE} ({mode_label})")
    print(f"  Consensus Engine   : MANDATORY DUAL-KEY (Agent 1 News + Agent 2 Quant)")
    print("=" * 68)

    try:
        r = requests.get(f"{delta_config.BASE_URL}/v2/products", timeout=5)
        if r.status_code == 200:
            print("  [SUCCESS] Delta Exchange Gateway Online & Reachable.")
        else:
            print(f"  [WARNING] Delta Gateway returned status {r.status_code}")
    except Exception as e:
        print(f"  [ERROR] Could not connect to Delta Gateway: {e}")

    print(f"\n  Starting Cyberpunk Dashboard at: http://{system_config.HOST}:{system_config.PORT}")
    print("=" * 68)

if __name__ == "__main__":
    check_connectivity()
    uvicorn.run("server:app", host=system_config.HOST, port=system_config.PORT, reload=False)
