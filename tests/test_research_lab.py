import unittest
import os
import shutil
import numpy as np

from research.feature_analyzer import feature_analyzer
from research.backtester import BacktestEngine
from research.optimizer import WalkForwardOptimizer
from research.stress_tester import StressTester
from research.candidate_ranker import CandidateRanker
from research.research_daemon import research_daemon


class TestResearchLab(unittest.TestCase):
    def setUp(self):
        self.test_dir = "data/test_research"
        os.makedirs(self.test_dir, exist_ok=True)
        self.ranker = CandidateRanker(leaderboard_path=os.path.join(self.test_dir, "test_lb.json"))

        # Generate synthetic trending candles for testing backtester and WFO
        self.candles = []
        base_price = 70000.0
        for i in range(80):
            # Upward trending series with minor pullbacks
            drift = 50.0 if i % 6 != 0 else -40.0
            open_p = base_price
            close_p = open_p + drift
            high_p = max(open_p, close_p) + 20.0
            low_p = min(open_p, close_p) - 20.0
            self.candles.append({
                "time": 1700000000 + i * 900,
                "open": open_p,
                "high": high_p,
                "low": low_p,
                "close": close_p,
                "volume": 100.0 + i * 2
            })
            base_price = close_p

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def test_feature_analyzer_ic(self):
        # Perfect positive correlation
        features = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0]
        returns = [0.01, 0.02, 0.03, 0.04, 0.05, 0.06]
        ic = feature_analyzer.compute_spearman_ic(features, returns)
        self.assertAlmostEqual(ic, 1.0, places=2)

    def test_backtester_engine(self):
        engine = BacktestEngine(min_quality_score=60.0)
        res = engine.run(self.candles, symbol="BTCUSD")
        self.assertIsNotNone(res)
        self.assertGreaterEqual(res.total_trades, 0)
        self.assertIsInstance(res.win_rate, float)

    def test_wfo_optimizer(self):
        wfo = WalkForwardOptimizer(split_ratio=0.7)
        results = wfo.optimize(self.candles, parameter_grid=[{"min_quality_score": 60.0}])
        self.assertGreaterEqual(len(results), 1)
        self.assertIsNotNone(results[0].wfe_ratio)

    def test_stress_tester(self):
        st = StressTester(mc_simulations=50)
        report = st.stress_test(self.candles)
        self.assertIsNotNone(report)
        self.assertGreaterEqual(report.resilience_score, 0.0)

    def test_candidate_ranker_and_leaderboard(self):
        candidates = [
            {
                "parameter_set": {"min_quality_score": 75.0},
                "oos_profit_factor": 1.8,
                "wfe_ratio": 0.85,
                "resilience_score": 80.0,
                "sharpe_ratio": 2.1,
                "win_rate": 62.0,
                "passed_all_gates": True
            },
            {
                "parameter_set": {"min_quality_score": 65.0},
                "oos_profit_factor": 1.1,
                "wfe_ratio": 0.40,
                "resilience_score": 50.0,
                "sharpe_ratio": 0.8,
                "win_rate": 45.0,
                "passed_all_gates": False
            }
        ]
        ranked = self.ranker.rank_and_save(candidates)
        self.assertEqual(len(ranked), 2)
        # Top candidate must be the one with higher score & passed gates
        self.assertTrue(ranked[0].passed_all_gates)
        self.assertGreater(ranked[0].composite_score, ranked[1].composite_score)

        top = self.ranker.get_top_candidate()
        self.assertEqual(top["candidate_id"], ranked[0].candidate_id)

    def test_research_daemon_status(self):
        status = research_daemon.get_status()
        self.assertIn("enabled", status)
        self.assertIn("is_running", status)
        self.assertIn("cycle_count", status)


if __name__ == "__main__":
    unittest.main()
