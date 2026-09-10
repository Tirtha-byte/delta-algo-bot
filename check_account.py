from delta_client import delta_client

def main():
    bal = delta_client.get_wallet_balances()
    print("=== LIVE WALLET BALANCES ===")
    if bal.get("success"):
        for b in bal["result"]:
            balance = float(b.get("balance", 0))
            if balance > 0:
                print(f"Asset           : {b.get('asset_symbol')}")
                print(f"Total Balance   : ${balance:.4f}")
                print(f"Available       : ${float(b.get('available_balance', 0)):.4f}")
                print(f"INR Equivalent  : Rs {float(b.get('balance_inr', 0)):.2f}")
                print(f"User ID         : {b.get('user_id')}")
                print(f"Blocked Margin  : ${float(b.get('blocked_margin', 0)):.2f}")
                print(f"Position Margin : ${float(b.get('position_margin', 0)):.2f}")
                print(f"Order Margin    : ${float(b.get('order_margin', 0)):.2f}")
    else:
        print("Failed to fetch balance:", bal)

    pos = delta_client.get_positions()
    print("\n=== OPEN POSITIONS ===")
    if pos.get("success"):
        positions = pos.get("result", [])
        print(f"Total Open Positions: {len(positions)}")
        for p in positions:
            print(p)
    else:
        print("Failed to fetch positions:", pos)

    headers = delta_client._get_auth_headers("GET", "/v2/orders?state=open")
    r = delta_client.session.get(delta_client.base_url + "/v2/orders?state=open", headers=headers)
    print("\n=== ACTIVE ORDERS ===")
    if r.status_code == 200:
        orders = r.json().get("result", [])
        print(f"Active Open Orders: {len(orders)}")
    else:
        print("Orders response:", r.status_code)

if __name__ == "__main__":
    from agents.execution_manager import execution_manager
    print("CURRENT EXECUTION MODE:", execution_manager.mode)
    main()
