"""
BEAST v2 - End-to-End System Integration Test
tests/test_beast_v2_e2e.py

Validates the full pipeline:
DATA -> REGIME -> SETUP -> TRIGGER -> NEWS -> QUALITY -> RISK -> EXECUTION -> RECONCILIATION -> JOURNAL -> RESEARCH LAB
"""

import unittest
from unittest.mock import patch, MagicMock

from data.staleness_breaker import MarketDataStalenessBreaker, HealthState
from agents.regime_engine import RegimeEngine
from agents.setup_engine import SetupEngine
from agents.trigger_engine import TriggerEngine
from agents.social_news_agent import SocialNewsAgent
from agents.quality_engine import QualityScoringEngine
from agents.risk_governor import RiskGovernorAgent
from agents.execution_manager import ExecutionManagerAgent, OrderState
from execution.reconciliation_engine import ReconciliationEngine
from agents.live_trade_journal import LiveTradeJournal
from research.backtester import BacktestEngine
from research.candidate_ranker import CandidateRanker


class TestBeastV2EndToEnd(unittest.TestCase):
    def setUp(self):
        self.staleness = MarketDataStalenessBreaker()
        self.regime = RegimeEngine()
        self.setup = SetupEngine()
        self.trigger = TriggerEngine()
        self.news = SocialNewsAgent()
        self.quality = QualityScoringEngine()
        self.risk = RiskGovernorAgent(state_file="data/test_e2e_drawdown.json")
        self.execution = ExecutionManagerAgent()
        self.reconciliation = ReconciliationEngine()
        self.journal = LiveTradeJournal(data_dir="data/test_e2e_journal")
        self.ranker = CandidateRanker(leaderboard_path="data/test_e2e_lb.json")

    def tearDown(self):
        import os, shutil
        if os.path.exists("data/test_e2e_drawdown.json"):
            os.remove("data/test_e2e_drawdown.json")
        if os.path.exists("data/test_e2e_journal"):
            shutil.rmtree("data/test_e2e_journal")
        if os.path.exists("data/test_e2e_lb.json"):
            os.remove("data/test_e2e_lb.json")

    def test_full_beast_v2_pipeline_execution(self):
        # 1. Staleness check
        self.assertTrue(self.staleness.is_safe("BTCUSD"))

        # 2. Synthetic historical candles for regime & setup
        candles = []
        base = 70000.0
        for i in range(40):
            candles.append({
                "time": 1700000000 + i * 900,
                "open": base,
                "high": base + 50.0,
                "low": base - 20.0,
                "close": base + 30.0,
                "volume": 200.0
            })
            base += 30.0

        regime_res = self.regime.evaluate(candles)
        self.assertIn("regime", regime_res)
        self.assertIn("atr", regime_res)

        # 3. Microstructure & Orderflow
        micro_res = {"weighted_imbalance": 0.20, "spread_state": "NORMAL"}
        of_res = {"cvd_slope": 15.0, "absorption": "BULLISH_ABSORPTION", "cvd_divergence": "NONE", "sweep": "NONE"}
        tech_data = {
            "close": candles[-1]["close"],
            "ema9": candles[-1]["close"] - 10,
            "ema21": candles[-1]["close"] - 30,
            "ema50": candles[-1]["close"] - 80,
            "rsi": 58.0,
            "macd_hist": 12.0
        }

        # 4. Setup Engine
        setup_res = self.setup.evaluate(
            regime_result=regime_res,
            microstructure_result=micro_res,
            orderflow_result=of_res,
            technical_data=tech_data,
            symbol="BTCUSD"
        )
        self.assertGreater(setup_res["long_setup_score"], 50.0)

        # 5. Trigger Engine
        trig_res = self.trigger.evaluate(
            direction="BUY",
            setup_status="ACTIVE",
            candle_5m_closed=True,
            microprice_drift=0.10,
            cvd_confirmed=True
        )
        self.assertEqual(trig_res["state"], "TRIGGER_CONFIRMED")

        # 6. Quality Scoring
        news_res = {"news_state": "NEUTRAL", "usable_sentiment": 0.10, "confidence": 0.70}
        quality_res = self.quality.evaluate(
            direction="BUY",
            regime_result=regime_res,
            setup_result=setup_res,
            trigger_result=trig_res,
            orderflow_result=of_res,
            microstructure_result=micro_res,
            news_result=news_res,
            technical_data=tech_data,
            risk_metrics={"reward_to_risk": 2.8}
        )
        self.assertTrue(quality_res.is_executable)
        self.assertIn(quality_res.tier, ("A+", "A", "B"))

        # 7. Journal evaluation
        self.journal.record_evaluation(
            symbol="BTCUSD",
            direction="BUY",
            quality_score=quality_res.total_score,
            tier=quality_res.tier,
            setup_family="TREND_PULLBACK",
            approved=True,
            reasons=[]
        )
        recent_evals = self.journal.get_recent_evaluations(5)
        self.assertEqual(len(recent_evals), 1)

        # 8. Deterministic Risk Sizing
        proposal = {
            "symbol": "BTCUSD",
            "signal": "BUY",
            "entry_price": candles[-1]["close"],
            "stop_loss": candles[-1]["close"] - (1.8 * regime_res["atr"]),
            "take_profit_1": candles[-1]["close"] + (2.5 * regime_res["atr"]),
            "take_profit_2": candles[-1]["close"] + (4.5 * regime_res["atr"]),
            "reward_to_risk": 2.8,
            "tier": quality_res.tier
        }
        risk_res = self.risk.evaluate_proposal(proposal, current_balance=1000.0)
        self.assertTrue(risk_res["approved"])
        self.assertGreater(risk_res["contracts"], 0)

        # 9. Execution State Machine (in Shadow mode for dry unit test)
        self.execution.set_mode("SHADOW")
        exec_res = self.execution.execute_order(proposal, risk_res)
        self.assertTrue(exec_res["success"])
        self.assertEqual(exec_res["order_state"], OrderState.FILLED)

        # 10. Reconciliation Check
        rec_report = self.reconciliation.reconcile()
        self.assertIsNotNone(rec_report)

        # 11. Research Backtester on same logic
        bt = BacktestEngine(min_quality_score=60.0)
        bt_res = bt.run(candles)
        self.assertIsNotNone(bt_res)


if __name__ == "__main__":
    unittest.main()
