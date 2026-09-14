import os
os.environ["TESTING"] = "1"
import unittest
from agents.trigger_engine import trigger_engine, TriggerState

class TestTriggerEngine(unittest.TestCase):

    def test_trigger_confirmed_when_all_conditions_met(self):
        setup_result = {"direction": "LONG", "lead_score": 85.0}
        candles_5m = [
            {"open": 100.0, "high": 101.0, "low": 99.5, "close": 100.5},
            {"open": 100.5, "high": 101.5, "low": 100.0, "close": 101.0},
            {"open": 101.0, "high": 102.0, "low": 100.8, "close": 101.8},
            {"open": 101.8, "high": 102.5, "low": 101.5, "close": 102.3},
            {"open": 102.3, "high": 103.0, "low": 102.0, "close": 102.8},
            {"open": 102.8, "high": 103.5, "low": 102.5, "close": 103.4}, # Bullish bar
        ]
        flow_data = {
            "flow": {"delta": 40.0, "flow_bias": "BULLISH_FLOW"},
            "divergence": {"divergence": "BULLISH_CONFIRMATION"}
        }
        micro_data = {"spread_pct": 0.05, "micro_drift": 0.12, "depth_imbalance_5": 0.25}

        res = trigger_engine.evaluate_trigger("BTCUSD", setup_result, candles_5m, flow_data, micro_data, atr=1.0)
        self.assertEqual(res["trigger_state"], TriggerState.TRIGGER_CONFIRMED)
        self.assertTrue(res["is_execution_eligible"])
        self.assertEqual(len(res["waiting_for"]), 0)
        self.assertGreaterEqual(res["trigger_score"], 85.0)

    def test_trigger_waiting_for_confirmation(self):
        """If setup is good (85) but 5m candle is bearish and spread is wide, state is TRIGGER_APPROACHING / WAIT."""
        setup_result = {"direction": "LONG", "lead_score": 85.0}
        candles_5m = [
            {"open": 100.0, "high": 101.0, "low": 99.5, "close": 100.5},
            {"open": 100.5, "high": 101.5, "low": 100.0, "close": 101.0},
            {"open": 101.0, "high": 102.0, "low": 100.8, "close": 101.8},
            {"open": 101.8, "high": 102.5, "low": 101.5, "close": 102.3},
            {"open": 102.3, "high": 103.0, "low": 102.0, "close": 102.8},
            {"open": 103.5, "high": 103.6, "low": 102.0, "close": 102.2}, # Bearish red candle!
        ]
        flow_data = {
            "flow": {"delta": -10.0, "flow_bias": "BEARISH_FLOW"}, # Negative delta
            "divergence": {"divergence": "FLOW_BALANCED"}
        }
        micro_data = {"spread_pct": 0.28, "micro_drift": -0.20, "depth_imbalance_5": -0.15} # Wide spread & adverse

        res = trigger_engine.evaluate_trigger("ETHUSD", setup_result, candles_5m, flow_data, micro_data, atr=1.0)
        self.assertFalse(res["is_execution_eligible"])
        self.assertNotEqual(res["trigger_state"], TriggerState.TRIGGER_CONFIRMED)
        self.assertGreater(len(res["waiting_for"]), 0)

if __name__ == "__main__":
    unittest.main()
