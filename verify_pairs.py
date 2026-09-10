from config import system_config

print(f"Total Tracked Markets: {len(system_config.US_STOCKS_RWA)}")
print(f"Total Contract Specs: {len(system_config.CONTRACT_SPECS)}")
print("Markets List:")
for idx, sym in enumerate(system_config.US_STOCKS_RWA, 1):
    spec = system_config.CONTRACT_SPECS.get(sym, {})
    print(f"{idx:2d}. {sym:<10} | Name: {spec.get('name', sym):<35} | Val: {spec.get('contract_val')} | Tick: {spec.get('tick_size')}")
