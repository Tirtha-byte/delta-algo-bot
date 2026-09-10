import unittest
import os
os.environ["TESTING"] = "1"
import tempfile
import json
from datetime import datetime, timezone, timedelta
from agents.forensic_learner import ForensicLearnerAgent

class TestForensicLearner(unittest.TestCase):

    def setUp(self):
        self.temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
        self.temp_file.close()
        self.learner = ForensicLearnerAgent(data_path=self.temp_file.name)

    def tearDown(self):
        if os.path.exists(self.temp_file.name):
            try:
                os.remove(self.temp_file.name)
            except Exception:
                pass

    def test_autopsy_diagnoses_counter_trend_trap(self):
        """Verify autopsy catches buying into a prevailing bearish trend."""
        loss_record = {
            "symbol": "ETHUSD",
            "side": "BUY",
            "entry_price": 2500.0,
            "exit_price": 2480.0,
            "realized_pnl": -1.50,
            "opened_at": datetime.now(timezone.utc).isoformat()
        }
        market_ctx = {
            "quant": {
                "trend_alignment": "BEARISH",
                "current_close": 2490.0,
                "ema200": 2550.0,
                "rsi": 45.0
            }
        }
        res = self.learner.conduct_autopsy(loss_record, market_ctx)
        self.assertEqual(res["archetype"], "COUNTER_TREND_TRAP")
        self.assertIn("prohibit BUY entries when trend alignment is BEARISH", res["remedy"])
        self.assertEqual(self.learner.memory["total_losses_analyzed"], 1)

    def test_autopsy_widens_atr_multiplier_on_wick_squeeze(self):
        """Verify fast stop-outs trigger adaptive ATR widening to prevent noise stops."""
        quick_open_time = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
        loss_record = {
            "symbol": "NVDAXUSD",
            "side": "BUY",
            "entry_price": 220.0,
            "exit_price": 217.0,
            "realized_pnl": -1.20,
            "opened_at": quick_open_time
        }
        market_ctx = {
            "quant": {
                "trend_alignment": "BULLISH",
                "current_close": 218.0,
                "ema200": 210.0,
                "rsi": 52.0
            }
        }
        res = self.learner.conduct_autopsy(loss_record, market_ctx)
        self.assertEqual(res["archetype"], "VOLATILITY_WICK_SQUEEZE")
        profile = self.learner._get_asset_profile("NVDAXUSD")
        self.assertGreater(profile["adaptive_atr_multiplier"], 1.5)
        self.assertEqual(profile["adaptive_atr_multiplier"], 1.8)

    def test_consecutive_losses_trigger_asset_quarantine(self):
        """Verify 2 consecutive losses automatically lock out the asset in quarantine."""
        loss_record_1 = {"symbol": "TSLAXUSD", "side": "BUY", "entry_price": 250.0, "exit_price": 245.0, "realized_pnl": -1.40}
        loss_record_2 = {"symbol": "TSLAXUSD", "side": "BUY", "entry_price": 245.0, "exit_price": 240.0, "realized_pnl": -1.30}

        self.learner.conduct_autopsy(loss_record_1)
        res2 = self.learner.conduct_autopsy(loss_record_2)

        self.assertTrue(res2["quarantine_applied"])
        is_q, reason, _ = self.learner.is_asset_quarantined("TSLAXUSD")
        self.assertTrue(is_q)
        self.assertIn("TSLAXUSD", self.learner.memory["quarantined_symbols"])

        # Pre-flight check must now VETO TSLAXUSD
        proposal = {"symbol": "TSLAXUSD", "signal": "BUY", "entry_price": 242.0}
        quant_eval = {"confidence": 0.90, "trend_alignment": "BULLISH", "rsi": 50.0}
        news_eval = {"gate_passed": True}
        check = self.learner.pre_flight_inspection(proposal, quant_eval, news_eval)
        self.assertFalse(check["approved"])
        self.assertEqual(check["veto_code"], "ASSET_IN_QUARANTINE")
        self.assertGreater(self.learner.memory["prevented_mistakes_count"], 0)

    def test_pre_flight_blocks_overextended_rsi_mistakes(self):
        """Verify pre-flight gate blocks buying when RSI is overextended."""
        proposal = {"symbol": "PLTRBUSD", "signal": "BUY", "entry_price": 85.0}
        quant_eval = {"confidence": 0.85, "trend_alignment": "BULLISH", "rsi": 72.0}
        news_eval = {"gate_passed": True}
        check = self.learner.pre_flight_inspection(proposal, quant_eval, news_eval)
        self.assertFalse(check["approved"])
        self.assertEqual(check["veto_code"], "LEARNED_RULE_OVEREXTENDED_RSI")

    def test_persistence_across_reloads(self):
        """Verify memory and learned rules correctly persist and restore from JSON."""
        loss_record = {"symbol": "BTCUSD", "side": "BUY", "entry_price": 90000.0, "exit_price": 89000.0, "realized_pnl": -1.50}
        self.learner.conduct_autopsy(loss_record)

        # Create new agent pointing to same file
        reloaded_learner = ForensicLearnerAgent(data_path=self.temp_file.name)
        self.assertEqual(reloaded_learner.memory["total_losses_analyzed"], 1)
        self.assertEqual(reloaded_learner.memory["asset_profiles"]["BTCUSD"]["losses"], 1)

if __name__ == "__main__":
    unittest.main()
