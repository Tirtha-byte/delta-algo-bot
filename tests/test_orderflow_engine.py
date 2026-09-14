import os
os.environ["TESTING"] = "1"
import unittest
import time
from agents.microstructure_engine import microstructure_engine
from agents.trade_flow_engine import trade_flow_engine

class TestOrderflowEngine(unittest.TestCase):

    def test_microstructure_advanced_metrics(self):
        """Verify weighted book imbalance, book slope, velocity, and spread percentiles."""
        raw_bids = [
            {"price": "100.0", "size": "100"},
            {"price": "99.9", "size": "80"},
            {"price": "99.8", "size": "60"},
            {"price": "99.7", "size": "40"},
            {"price": "99.6", "size": "20"},
        ]
        raw_asks = [
            {"price": "100.1", "size": "20"},
            {"price": "100.2", "size": "30"},
            {"price": "100.3", "size": "40"},
            {"price": "100.4", "size": "50"},
            {"price": "100.5", "size": "60"},
        ]

        # 1. 15-level weighted imbalance
        imb_15 = microstructure_engine.compute_weighted_book_imbalance(raw_bids, raw_asks, levels=15)
        self.assertGreater(imb_15, 0.25)  # Strong bid wall

        # 2. Book slope
        slopes = microstructure_engine.compute_book_slope(raw_bids, raw_asks)
        self.assertGreater(slopes["bid_slope"], slopes["ask_slope"])
        self.assertGreater(slopes["slope_ratio"], 1.0)

        # 3. Microprice dynamics (velocity & acceleration)
        d1 = microstructure_engine.compute_microprice_dynamics("TEST", 100.0, ts=1000.0)
        d2 = microstructure_engine.compute_microprice_dynamics("TEST", 100.2, ts=1001.0)
        d3 = microstructure_engine.compute_microprice_dynamics("TEST", 100.5, ts=1002.0)
        d4 = microstructure_engine.compute_microprice_dynamics("TEST", 100.9, ts=1003.0)
        self.assertGreater(d4["velocity"], 0.0)
        self.assertGreater(d4["acceleration"], 0.0)

        # 4. Spread regime
        for s in [0.05, 0.06, 0.05, 0.07, 0.05, 0.06, 0.05, 0.06, 0.05, 0.06]:
            microstructure_engine.compute_spread_regime("TEST", s)
        spr_norm = microstructure_engine.compute_spread_regime("TEST", 0.055)
        self.assertEqual(spr_norm["regime"], "NORMAL")
        spr_exp = microstructure_engine.compute_spread_regime("TEST", 0.25)
        self.assertEqual(spr_exp["regime"], "EXPANDED")

    def test_trade_flow_aggressor_and_cvd(self):
        """Verify rolling buy/sell volume, delta, and CVD progression."""
        engine = trade_flow_engine
        engine._trade_buffers.pop("BTCUSD", None)
        engine._cumulative_delta.pop("BTCUSD", None)

        # Ingest 10 buy trades of size 2.0
        now = time.time()
        for i in range(10):
            engine.ingest_trade("BTCUSD", 70000.0 + i, 2.0, is_aggressive_buy=True, timestamp=now + i)

        flow = engine.calculate_rolling_flow("BTCUSD", window_trades=10)
        self.assertEqual(flow["aggressive_buy_volume"], 20.0)
        self.assertEqual(flow["aggressive_sell_volume"], 0.0)
        self.assertEqual(flow["delta"], 20.0)
        self.assertEqual(flow["cvd"], 20.0)
        self.assertGreater(flow["cvd_slope"], 0.0)
        self.assertEqual(flow["flow_bias"], "BULLISH_FLOW")

    def test_absorption_detection(self):
        """Verify Bullish Absorption: Heavy aggressive selling into firm bid support."""
        engine = trade_flow_engine
        engine._trade_buffers.pop("ETHUSD", None)
        now = time.time()

        # Ingest 20 trades: 16 sells (aggressive sell) but price doesn't drop
        for i in range(20):
            is_buy = (i < 4)
            p = 2500.0 if not is_buy else 2500.5
            engine.ingest_trade("ETHUSD", p, 10.0, is_aggressive_buy=is_buy, timestamp=now + i)

        ob_mock = {"depth_imbalance_5": 0.3}
        abs_res = engine.detect_absorption("ETHUSD", current_price=2500.0, orderbook_snapshot=ob_mock)
        self.assertEqual(abs_res["absorption"], "BULLISH_ABSORPTION")
        self.assertGreaterEqual(abs_res["confidence"], 0.60)

    def test_sweep_detection(self):
        """Verify rapid multi-level sweep classification."""
        engine = trade_flow_engine
        engine._trade_buffers.pop("NVDAXUSD", None)
        now = time.time()

        # Ingest fast burst of 10 buys sweeping price up in 1 second
        for i in range(10):
            engine.ingest_trade("NVDAXUSD", 220.0 + (i * 0.2), 15.0, is_aggressive_buy=True, timestamp=now + (i * 0.1))

        sweep_res = engine.detect_sweeps("NVDAXUSD", current_price=221.8)
        self.assertIn(sweep_res["sweep_type"], ("VALID_BREAKOUT", "LIQUIDITY_SWEEP"))
        self.assertGreaterEqual(sweep_res["confidence"], 0.70)

    def test_cvd_divergence(self):
        """Verify Price vs CVD Divergence detection."""
        engine = trade_flow_engine
        engine._cvd_history.pop("MSTRBUSD", None)
        from collections import deque
        engine._cvd_history["MSTRBUSD"] = deque(maxlen=300)
        now = time.time()

        # Case 1: Price goes up, but CVD goes down (Bearish Divergence)
        for i in range(15):
            cvd_val = 50.0 - (i * 2.0)  # Falling CVD
            price_val = 300.0 + (i * 0.5)  # Rising price
            engine._cvd_history["MSTRBUSD"].append((now + i, cvd_val, price_val))

        div = engine.detect_cvd_divergence("MSTRBUSD", current_price=307.0)
        self.assertEqual(div["divergence"], "BEARISH_DIVERGENCE")


if __name__ == "__main__":
    unittest.main()
