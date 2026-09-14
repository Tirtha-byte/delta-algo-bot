import unittest
import os
from agents.risk_governor import RiskGovernorAgent

class TestRiskGovernorBeastV2(unittest.TestCase):
    def setUp(self):
        self.state_file = "data/test_daily_drawdown.json"
        if os.path.exists(self.state_file):
            os.remove(self.state_file)
        self.risk_gov = RiskGovernorAgent(state_file=self.state_file)

    def tearDown(self):
        if os.path.exists(self.state_file):
            os.remove(self.state_file)

    def test_tier_scaling(self):
        proposal_base = {
            "symbol": "BTCUSD",
            "entry_price": 70000.0,
            "stop_loss": 69500.0,
            "take_profit_1": 71500.0,
            "reward_to_risk": 3.0,
            "signal": "BUY"
        }

        # A+ gets full allocation
        res_a_plus = self.risk_gov.evaluate_proposal({**proposal_base, "tier": "A+"}, current_balance=1000.0)
        self.assertTrue(res_a_plus["approved"])

        # B gets scaled allocation (less or equal risk dollars than A+)
        res_b = self.risk_gov.evaluate_proposal({**proposal_base, "tier": "B"}, current_balance=1000.0)
        self.assertTrue(res_b["approved"])
        self.assertLessEqual(res_b["risk_budget_usd"], res_a_plus["risk_budget_usd"])

        # C is vetoed
        res_c = self.risk_gov.evaluate_proposal({**proposal_base, "tier": "C"}, current_balance=1000.0)
        self.assertFalse(res_c["approved"])
        self.assertIn("QUALITY VETO", res_c["reason"])

    def test_portfolio_correlation_veto(self):
        # Position 1: BTC Long
        active = [{"symbol": "BTCUSD", "direction": "BUY"}]

        # Trying to open ETH Long in same direction -> Correlated crypto risk veto
        prop_eth = {
            "symbol": "ETHUSD",
            "entry_price": 2500.0,
            "stop_loss": 2450.0,
            "take_profit_1": 2650.0,
            "reward_to_risk": 3.0,
            "signal": "BUY",
            "tier": "A"
        }
        res_eth = self.risk_gov.evaluate_proposal(prop_eth, current_balance=500.0, active_positions=active)
        self.assertFalse(res_eth["approved"])
        self.assertIn("Correlated risk breach", res_eth["reason"])

    def test_max_concurrent_positions_veto(self):
        # 2 positions already active
        active = [
            {"symbol": "BTCUSD", "direction": "BUY"},
            {"symbol": "NVDAXUSD", "direction": "BUY"}
        ]
        prop_spy = {
            "symbol": "SPYXUSD",
            "entry_price": 500.0,
            "stop_loss": 495.0,
            "take_profit_1": 515.0,
            "reward_to_risk": 3.0,
            "signal": "BUY",
            "tier": "A"
        }
        res_spy = self.risk_gov.evaluate_proposal(prop_spy, current_balance=500.0, active_positions=active)
        self.assertFalse(res_spy["approved"])
        self.assertIn("Max concurrent positions", res_spy["reason"])

    def test_daily_drawdown_circuit_breaker(self):
        self.risk_gov.daily_start_balance = 100.0
        # If current balance is $94.00, that is 6% loss -> triggers 5% breaker
        proposal = {
            "symbol": "BTCUSD",
            "entry_price": 70000.0,
            "stop_loss": 69500.0,
            "take_profit_1": 71500.0,
            "reward_to_risk": 3.0,
            "signal": "BUY"
        }
        res = self.risk_gov.evaluate_proposal(proposal, current_balance=94.0)
        self.assertFalse(res["approved"])
        self.assertTrue(res.get("circuit_breaker_active", True))
        self.assertIn("CIRCUIT BREAKER", res["reason"])

if __name__ == "__main__":
    unittest.main()
