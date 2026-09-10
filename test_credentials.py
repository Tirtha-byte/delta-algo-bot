import os
from dotenv import load_dotenv
from delta_client import DeltaClient

load_dotenv(override=True)

def verify_credentials():
    api_key = os.getenv("DELTA_API_KEY", "")
    api_secret = os.getenv("DELTA_API_SECRET", "")
    base_url = os.getenv("DELTA_BASE_URL", "https://api.india.delta.exchange")

    if not api_key or not api_secret:
        print("[!] DELTA_API_KEY or DELTA_API_SECRET is empty in .env")
        return False

    print(f"[*] Testing Delta credentials against: {base_url}")
    client = DeltaClient(base_url=base_url, api_key=api_key, api_secret=api_secret)

    # 1. Test wallet balances
    bal = client.get_wallet_balances()
    if bal.get("success"):
        print("[+] API Authentication Successful!")
        print(f"[+] Wallet Balances: {bal.get('result')}")
        
        # 2. Test positions
        pos = client.get_positions()
        print(f"[+] Open Margined Positions: {pos.get('result')}")
        return True
    else:
        print(f"[-] Authentication Failed: {bal.get('error')}")
        # Try alternate gateway (Global vs India)
        alt_url = "https://api.delta.exchange" if "india" in base_url else "https://api.india.delta.exchange"
        print(f"[*] Retrying with alternate gateway: {alt_url}...")
        client_alt = DeltaClient(base_url=alt_url, api_key=api_key, api_secret=api_secret)
        bal_alt = client_alt.get_wallet_balances()
        if bal_alt.get("success"):
            print(f"[+] Success on alternate gateway: {alt_url}!")
            print(f"[+] Wallet Balances: {bal_alt.get('result')}")
            return True
        else:
            print(f"[-] Authentication Failed on {alt_url}: {bal_alt.get('error')}")
            return False

if __name__ == "__main__":
    verify_credentials()
