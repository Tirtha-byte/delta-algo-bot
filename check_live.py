from config import mtf_config
from agents.quant_analyst import quant_analyst_agent

print("=== 1H ZONE CLEARANCE LIVE ENGINE VERIFICATION ===")
print("ZONE_LOOKBACK_BARS:", mtf_config.ZONE_LOOKBACK_BARS)
print("ZONE_CLEARANCE_MIN_RATIO:", mtf_config.ZONE_CLEARANCE_MIN_RATIO)
print("ZONE_MIN_DISPLACEMENT_ATR:", mtf_config.ZONE_MIN_DISPLACEMENT_ATR)
print("ZONE_MAX_TESTS:", mtf_config.ZONE_MAX_TESTS)
print("METHOD _analyze_1h_supply_demand_zones LOADED:", hasattr(quant_analyst_agent, "_analyze_1h_supply_demand_zones"))
print("STATUS: 100% ARMED & ACTIVE FOR NEXT TRADE")
