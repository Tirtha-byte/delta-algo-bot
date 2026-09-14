import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data.market_data_engine import market_data_engine
from data.staleness_breaker import staleness_breaker


def run_smoke():
    market_data_engine.start()
    time.sleep(3.5)

    ob = market_data_engine.get_orderbook('BTCUSD')
    health = market_data_engine.get_health('BTCUSD')
    print(f"BTCUSD: bid={ob.get('best_bid')}, ask={ob.get('best_ask')}, microprice={ob.get('micro_price', 0):.2f}, latency={health.get('market_data_age_ms')}ms, state={health.get('state')}")

    market_data_engine.stop()
    print("Clean shutdown achieved.")


if __name__ == "__main__":
    run_smoke()
