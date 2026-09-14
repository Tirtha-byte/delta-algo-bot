import os
os.environ["TESTING"] = "1"
import unittest
from agents.regime_engine import regime_engine, MarketRegime

class TestRegimeEngine(unittest.TestCase):

    def _generate_candles(self, trend_slope=0.5, shock=False, count=40):
        candles = []
        base_p = 100.0
        for i in range(count):
            p = base_p + (i * trend_slope)
            spread = 1.0 if not shock or i < count - 1 else 15.0
            candles.append({
                "time": 1700000000 + (i * 900),
                "open": p - 0.2,
                "high": p + spread,
                "low": p - 0.2,
                "close": p + (spread * 0.8),
                "volume": 1000 + i * 20
            })
        return candles

    def test_trend_up_classification(self):
        candles = self._generate_candles(trend_slope=1.2, count=45)
        res = regime_engine.classify(candles)
        self.assertIn(res["regime"], (MarketRegime.TREND_UP, MarketRegime.BREAKOUT, MarketRegime.EXHAUSTION))
        self.assertGreater(res["confidence"], 0.65)

    def test_trend_down_classification(self):
        candles = self._generate_candles(trend_slope=-1.2, count=45)
        res = regime_engine.classify(candles)
        self.assertIn(res["regime"], (MarketRegime.TREND_DOWN, MarketRegime.BREAKOUT, MarketRegime.EXHAUSTION))

    def test_range_classification(self):
        candles = self._generate_candles(trend_slope=0.01, count=40)
        res = regime_engine.classify(candles)
        self.assertIn(res["regime"], (MarketRegime.RANGE, MarketRegime.LOW_VOLATILITY))

    def test_event_shock_classification(self):
        candles = self._generate_candles(trend_slope=0.1, shock=True, count=40)
        res = regime_engine.classify(candles)
        self.assertEqual(res["regime"], MarketRegime.EVENT_SHOCK)
        self.assertGreater(res["confidence"], 0.90)

if __name__ == "__main__":
    unittest.main()
