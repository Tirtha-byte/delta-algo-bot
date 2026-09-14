import unittest
import os
import shutil
from agents.live_trade_journal import LiveTradeJournal
from agents.forensic_learner import ForensicLearnerAgent

class TestJournalAndForensic(unittest.TestCase):
    def setUp(self):
        self.test_dir = "data/test_journal"
        os.makedirs(self.test_dir, exist_ok=True)
        self.journal = LiveTradeJournal(data_dir=self.test_dir)
        self.learner = ForensicLearnerAgent(data_path=os.path.join(self.test_dir, "test_memory.json"))

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def test_journal_record_evaluation_and_stats(self):
        self.journal.record_evaluation(
            symbol="BTCUSD",
            direction="BUY",
            quality_score=88.5,
            tier="A",
            setup_family="TREND_PULLBACK",
            approved=True,
            reasons=["All filters passed"]
        )
        evals = self.journal.get_recent_evaluations(10)
        self.assertEqual(len(evals), 1)
        self.assertEqual(evals[0]["symbol"], "BTCUSD")

        # Record trade entry and exit
        self.journal.record_trade_entry({
            "trade_id": "T1", "symbol": "BTCUSD", "side": "BUY",
            "entry_price": 70000.0, "contracts": 1
        })
        self.journal.record_trade_exit({
            "trade_id": "T1", "symbol": "BTCUSD", "side": "BUY",
            "entry_price": 70000.0, "exit_price": 71500.0,
            "pnl": 15.0, "r_multiple": 2.5
        })

        stats = self.journal.get_performance_stats()
        self.assertEqual(stats["total_trades"], 1)
        self.assertEqual(stats["wins"], 1)
        self.assertEqual(stats["win_rate"], 100.0)

    def test_software_error_does_not_quarantine_asset(self):
        trade_record = {
            "symbol": "ETHUSD",
            "side": "BUY",
            "entry_price": 2500.0,
            "exit_price": 2480.0,
            "pnl": -2.0,
            "error_type": "SOFTWARE_ERROR",
            "reason": "Null pointer bug in client"
        }
        autopsy = self.learner.conduct_autopsy(trade_record)
        self.assertEqual(autopsy["forensic_category"], "SOFTWARE_ERROR")
        self.assertFalse(autopsy["quarantine_applied"])
        is_q, _, _ = self.learner.is_asset_quarantined("ETHUSD")
        self.assertFalse(is_q)

if __name__ == "__main__":
    unittest.main()
