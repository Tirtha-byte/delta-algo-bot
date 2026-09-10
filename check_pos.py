from delta_client import delta_client

res = delta_client.get_positions()
for p in res.get('result', []):
    if abs(float(p.get('size', 0))) > 0:
        print(f"SYMBOL: {p.get('product_symbol')} | SIZE: {p.get('size')} | ENTRY: {p.get('entry_price')} | PNL: {p.get('unrealized_pnl')}")
