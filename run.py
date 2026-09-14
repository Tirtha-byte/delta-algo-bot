import uvicorn
import requests
from config import delta_config, compounding_config, system_config, research_lab_config

def check_connectivity():
    print("=" * 76)
    print("  BEAST v2 — INSTITUTIONAL DELTA QUANT TRADING ENGINE & RESEARCH LAB")
    print("=" * 76)
    print(f"  Capital Compound Goal : ${compounding_config.STARTING_CAPITAL:.2f} USD -> ${compounding_config.TARGET_CAPITAL:.2f} USD (+8,233%)")
    print(f"  Delta REST Gateway    : {delta_config.BASE_URL}")
    print(f"  Delta Public WS Feed  : {delta_config.PUBLIC_WS_URL}")
    print(f"  Execution Mode        : {system_config.MODE}")
    print(f"  Asset Universe        : Crypto Blue-Chips (BTC, ETH) + 32 US Stocks & Equity RWAs")
    print(f"  Live Decision Pipeline: DATA -> STATE -> REGIME -> SETUP -> TRIGGER -> QUALITY -> RISK -> EXECUTION -> RECONCILIATION")
    print(f"  24/7 Autonomous Lab   : {'ONLINE (WFO + Stress Tester + Candidate Ranker)' if research_lab_config.ENABLED else 'DISABLED'}")
    print("=" * 76)

    try:
        r = requests.get(f"{delta_config.BASE_URL}/v2/products", timeout=5)
        if r.status_code == 200:
            print("  [SUCCESS] Delta Exchange Gateway Online & Reachable.")
        else:
            print(f"  [WARNING] Delta Gateway returned status {r.status_code}")
    except Exception as e:
        print(f"  [ERROR] Could not connect to Delta Gateway: {e}")

    print(f"\n  Starting Cyberpunk Dashboard at: http://{system_config.HOST}:{system_config.PORT}")
    print("=" * 76)

if __name__ == "__main__":
    check_connectivity()
    uvicorn.run("server:app", host=system_config.HOST, port=system_config.PORT, reload=False)
