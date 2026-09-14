import os
os.environ["TESTING"] = "1"
import unittest
from agents.setup_engine import setup_engine, SetupFamily

class TestSetupEngine(unittest.TestCase):

    def test_trend_pullback_setup(self):
        """Verify Trend Pullback scores highly for healthy pullback in uptrend."""
        regime_data = {"regime": "TREND_UP"}
        flow_data = {
            "flow": {"delta": 50.0, "flow_bias": "BULLISH_FLOW", "aggressor_ratio": 1.5},
            "absorption": {"absorption": "NONE"},
            "sweep": {"sweep_type": "NONE"},
            "divergence": {"divergence": "FLOW_BALANCED"}
        }
        micro_data = {"depth_imbalance_5": 0.2, "micro_drift": 0.1}
        tech_15m = {
            "valid": True,
            "current_close": 105.0,
            "ema20": 104.0,
            "ema50": 101.0,
            "ema200": 95.0,
            "dist_ema20_atr": 0.8,  # Normal pullback
            "rsi": 48.0,            # Healthy reset
            "atr": 1.2
        }

        res = setup_engine.evaluate_setups("BTCUSD", regime_data, flow_data, micro_data, tech_15m)
        self.assertEqual(res["direction"], "LONG")
        self.assertEqual(res["best_setup"], SetupFamily.TREND_PULLBACK)
        self.assertGreaterEqual(res["long_setup_score"], 80.0)
        self.assertLess(res["short_setup_score"], 30.0)

    def test_absorption_reversal_setup(self):
        """Verify Absorption Reversal triggers when heavy sell volume is absorbed."""
        regime_data = {"regime": "RANGE"}
        flow_data = {
            "flow": {"delta": -120.0, "flow_bias": "BEARISH_FLOW"},
            "absorption": {"absorption": "BULLISH_ABSORPTION", "confidence": 0.85},
            "sweep": {"sweep_type": "NONE"},
            "divergence": {"divergence": "BULLISH_DIVERGENCE"}
        }
        micro_data = {"depth_imbalance_5": 0.35, "micro_drift": 0.15}
        tech_15m = {
            "valid": True,
            "current_close": 2400.0,
            "ema20": 2410.0,
            "ema50": 2415.0,
            "ema200": 2420.0,
            "dist_ema20_atr": 1.1,
            "rsi": 38.0,
            "atr": 10.0
        }

        res = setup_engine.evaluate_setups("ETHUSD", regime_data, flow_data, micro_data, tech_15m)
        self.assertEqual(res["direction"], "LONG")
        self.assertEqual(res["best_setup"], SetupFamily.ABSORPTION_REVERSAL)
        self.assertGreaterEqual(res["long_setup_score"], 85.0)

    def test_mean_reversion_barred_in_strong_trend(self):
        """Verify strict rule: Mean Reversion score MUST be 0.0 in strong trend regime."""
        regime_data = {"regime": "TREND_UP"}
        flow_data = {"flow": {}, "absorption": {}, "sweep": {}, "divergence": {}}
        micro_data = {}
        tech_15m = {
            "valid": True,
            "current_close": 110.0,
            "ema20": 100.0,
            "ema50": 90.0,
            "ema200": 80.0,
            "dist_ema20_atr": 3.0,
            "rsi": 78.0,  # Overbought
            "atr": 3.0
        }

        res = setup_engine.evaluate_setups("NVDAXUSD", regime_data, flow_data, micro_data, tech_15m)
        # Even though RSI > 70 and extended, mean reversion is barred in TREND_UP
        self.assertEqual(res["all_short_scores"][SetupFamily.MEAN_REVERSION.value], 0.0)
        self.assertEqual(res["all_long_scores"][SetupFamily.MEAN_REVERSION.value], 0.0)

if __name__ == "__main__":
    unittest.main()
