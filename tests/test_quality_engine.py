import unittest
from agents.quality_engine import QualityScoringEngine, QualityWeights, QualityScoreResult

class TestQualityEngine(unittest.TestCase):
    def setUp(self):
        self.engine = QualityScoringEngine()

    def test_ideal_bullish_setup_quality_a_plus(self):
        regime_res = {"regime": "TREND_UP", "adx": 32.0, "volatility_state": "NORMAL_VOLATILITY"}
        setup_res = {"long_setup_score": 92.0, "short_setup_score": 15.0, "family": "TREND_PULLBACK"}
        trigger_res = {"state": "TRIGGER_CONFIRMED", "candle_5m_closed": True, "cvd_confirmed": True}
        orderflow_res = {"cvd_slope": 12.5, "absorption": "BULLISH_ABSORPTION", "cvd_divergence": "NONE", "sweep": "NONE"}
        microstructure_res = {"weighted_imbalance": 0.25, "spread_state": "COMPRESSED"}
        news_res = {"news_state": "CATALYST", "usable_sentiment": 0.45, "confidence": 0.85, "model_interpretation": "APPROVED"}
        tech_data = {"ema9": 70100, "ema21": 70000, "ema50": 69800, "close": 70150, "rsi": 58.0, "macd_hist": 12.0}
        risk_metrics = {"reward_to_risk": 2.8}

        res = self.engine.evaluate(
            direction="BUY",
            regime_result=regime_res,
            setup_result=setup_res,
            trigger_result=trigger_res,
            orderflow_result=orderflow_res,
            microstructure_result=microstructure_res,
            news_result=news_res,
            technical_data=tech_data,
            risk_metrics=risk_metrics
        )

        self.assertIn(res.tier, ("A+", "A"))
        self.assertTrue(res.is_executable)
        self.assertGreaterEqual(res.total_score, 80.0)
        self.assertEqual(len(res.rejection_reasons), 0)

    def test_news_veto_rejects_trade(self):
        regime_res = {"regime": "TREND_UP", "adx": 30.0}
        setup_res = {"long_setup_score": 85.0}
        trigger_res = {"state": "TRIGGER_CONFIRMED", "candle_5m_closed": True, "cvd_confirmed": True}
        orderflow_res = {"cvd_slope": 10.0, "absorption": "NONE", "cvd_divergence": "NONE", "sweep": "NONE"}
        microstructure_res = {"weighted_imbalance": 0.15, "spread_state": "NORMAL"}
        news_res = {"news_state": "RISK", "usable_sentiment": -0.60, "model_interpretation": "ADVERSE VETOED"}

        res = self.engine.evaluate(
            direction="BUY",
            regime_result=regime_res,
            setup_result=setup_res,
            trigger_result=trigger_res,
            orderflow_result=orderflow_res,
            microstructure_result=microstructure_res,
            news_result=news_res
        )

        self.assertFalse(res.is_executable)
        self.assertIn("NO_TRADE", res.tier)
        self.assertTrue(any("veto" in r.lower() for r in res.rejection_reasons))

    def test_low_setup_score_watch_or_no_trade(self):
        regime_res = {"regime": "RANGE", "adx": 18.0}
        setup_res = {"long_setup_score": 42.0, "short_setup_score": 30.0}
        trigger_res = {"state": "SETUP_FORMING"}
        orderflow_res = {"cvd_slope": -2.0, "absorption": "NONE", "cvd_divergence": "NONE", "sweep": "NONE"}
        microstructure_res = {"weighted_imbalance": -0.05, "spread_state": "NORMAL"}

        res = self.engine.evaluate(
            direction="BUY",
            regime_result=regime_res,
            setup_result=setup_res,
            trigger_result=trigger_res,
            orderflow_result=orderflow_res,
            microstructure_result=microstructure_res
        )

        self.assertFalse(res.is_executable)
        self.assertIn(res.tier, ("C", "NO_TRADE"))

if __name__ == "__main__":
    unittest.main()
