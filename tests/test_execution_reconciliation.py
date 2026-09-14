import unittest
from unittest.mock import MagicMock, patch
from execution.reconciliation_engine import ReconciliationEngine, ReconciliationReport
from agents.execution_manager import ExecutionManagerAgent, OrderState

class TestExecutionAndReconciliation(unittest.TestCase):
    def setUp(self):
        self.reconciler = ReconciliationEngine()
        self.exec_mgr = ExecutionManagerAgent()

    def test_reconciliation_halts_on_unprotected_position(self):
        # Mock Delta client returning an open position with NO protective stop loss
        with patch("execution.reconciliation_engine.delta_client") as mock_dc:
            mock_dc.get_positions.return_value = {
                "success": True,
                "result": [{"product_symbol": "BTCUSD", "size": 1, "entry_price": 70000.0}]
            }
            # No open orders returned
            mock_dc.get_open_orders.return_value = []
            mock_dc.get_balance.return_value = {"balance": 1000.0}

            report = self.reconciler.reconcile()
            self.assertFalse(report.is_clean)
            self.assertTrue(self.reconciler.is_halted())
            self.assertTrue(any("NO protective Stop-Loss" in d for d in report.discrepancies))

    def test_execution_manager_refuses_order_when_halted(self):
        with patch("agents.execution_manager.reconciliation_engine") as mock_rec:
            mock_rec.is_halted.return_value = True
            mock_rec.halt_reason = "Unprotected position detected"

            proposal = {"symbol": "BTCUSD", "signal": "BUY", "entry_price": 70000.0}
            risk_approval = {"approved": True, "contracts": 1}

            res = self.exec_mgr.execute_order(proposal, risk_approval)
            self.assertFalse(res["success"])
            self.assertEqual(res["order_state"], OrderState.HALTED)
            self.assertIn("EXECUTION HALTED", res["error"])

    def test_execution_manager_state_machine_shadow(self):
        self.exec_mgr.set_mode("SHADOW")
        proposal = {
            "symbol": "BTCUSD",
            "signal": "BUY",
            "entry_price": 70000.0,
            "stop_loss": 69000.0,
            "take_profit_1": 72000.0,
            "take_profit_2": 74000.0
        }
        risk_approval = {
            "approved": True,
            "contracts": 1,
            "contract_val": 0.001,
            "isolated_leverage": 5
        }
        res = self.exec_mgr.execute_order(proposal, risk_approval)
        self.assertTrue(res["success"])
        self.assertEqual(res["order_state"], OrderState.FILLED)

if __name__ == "__main__":
    unittest.main()
