"""
BEAST v2 - Live Trade Journal
agents/live_trade_journal.py

Append-only, immutable structured recording of:
1. Candidate evaluations (decision logs, veto reasons, tier rankings)
2. Live executed trades with complete lifecycle telemetry:
   - Entry: timestamp, symbol, side, price, size, SL, TP, initial R:R, quality score, setup family
   - Exit: timestamp, exit price, PnL, R-multiple, MFE, MAE, holding duration, exit reason
"""

import os
import json
import time
import threading
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, asdict


class LiveTradeJournal:
    def __init__(self, data_dir: str = "data"):
        self.data_dir = data_dir
        os.makedirs(self.data_dir, exist_ok=True)
        self.evaluations_file = os.path.join(self.data_dir, "evaluations.jsonl")
        self.executions_file = os.path.join(self.data_dir, "executed_trades.jsonl")
        self.lock = threading.Lock()

    def record_evaluation(
        self,
        symbol: str,
        direction: str,
        quality_score: float,
        tier: str,
        setup_family: str,
        approved: bool,
        reasons: List[str],
        metadata: Optional[Dict[str, Any]] = None
    ):
        """Record candidate evaluation event in append-only JSONL."""
        record = {
            "timestamp": time.time(),
            "symbol": symbol,
            "direction": direction,
            "quality_score": quality_score,
            "tier": tier,
            "setup_family": setup_family,
            "approved": approved,
            "reasons": reasons,
            "metadata": metadata or {}
        }
        with self.lock:
            try:
                with open(self.evaluations_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(record) + "\n")
            except Exception as e:
                print(f"[Journal] Error appending evaluation: {e}")

    def record_trade_entry(self, trade_data: Dict[str, Any]):
        """Record open of a live trade."""
        record = {
            "type": "ENTRY",
            "timestamp": time.time(),
            "trade_id": trade_data.get("trade_id", f"TRD_{int(time.time()*1000)}"),
            "symbol": trade_data.get("symbol"),
            "side": trade_data.get("side", trade_data.get("signal", "BUY")),
            "entry_price": float(trade_data.get("entry_price", 0.0)),
            "contracts": int(trade_data.get("contracts", 1)),
            "contract_val": float(trade_data.get("contract_val", 0.01)),
            "stop_loss": float(trade_data.get("stop_loss", 0.0)),
            "take_profit_1": float(trade_data.get("take_profit_1", 0.0)),
            "take_profit_2": float(trade_data.get("take_profit_2", 0.0)),
            "leverage": int(trade_data.get("isolated_leverage", 5)),
            "quality_tier": trade_data.get("tier", "A"),
            "quality_score": float(trade_data.get("quality_score", 80.0)),
            "setup_family": trade_data.get("setup_family", "TREND_PULLBACK"),
            "initial_rr": float(trade_data.get("reward_to_risk", 2.5)),
            "margin_usd": float(trade_data.get("margin_required", 0.0))
        }
        with self.lock:
            try:
                with open(self.executions_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(record) + "\n")
            except Exception as e:
                print(f"[Journal] Error appending trade entry: {e}")

    def record_trade_exit(self, exit_data: Dict[str, Any]):
        """Record close of a live trade with telemetry (PnL, R-multiple, MFE, MAE)."""
        record = {
            "type": "EXIT",
            "timestamp": time.time(),
            "trade_id": exit_data.get("trade_id"),
            "symbol": exit_data.get("symbol"),
            "side": exit_data.get("side"),
            "entry_price": float(exit_data.get("entry_price", 0.0)),
            "exit_price": float(exit_data.get("exit_price", 0.0)),
            "pnl_usd": float(exit_data.get("pnl", exit_data.get("realized_pnl", 0.0))),
            "pnl_pct": float(exit_data.get("pnl_pct", 0.0)),
            "r_multiple": float(exit_data.get("r_multiple", 0.0)),
            "mfe": float(exit_data.get("mfe", 0.0)),
            "mae": float(exit_data.get("mae", 0.0)),
            "holding_seconds": float(exit_data.get("holding_seconds", 0.0)),
            "exit_reason": exit_data.get("reason", "CLOSED_NORMAL"),
            "forensic_category": exit_data.get("forensic_category", "VALID_MARKET_LOSS" if float(exit_data.get("pnl", 0)) < 0 else "VALID_MARKET_WIN")
        }
        with self.lock:
            try:
                with open(self.executions_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(record) + "\n")
            except Exception as e:
                print(f"[Journal] Error appending trade exit: {e}")

    def get_recent_evaluations(self, limit: int = 50) -> List[Dict[str, Any]]:
        results = []
        if not os.path.exists(self.evaluations_file):
            return results
        with self.lock:
            try:
                with open(self.evaluations_file, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                    for line in lines[-limit:]:
                        if line.strip():
                            results.append(json.loads(line))
            except Exception:
                pass
        return results

    def get_performance_stats(self) -> Dict[str, Any]:
        """Compute live trade journal summary statistics."""
        trades = []
        if not os.path.exists(self.executions_file):
            return {"total_trades": 0, "win_rate": 0.0, "total_pnl": 0.0, "profit_factor": 0.0}

        with self.lock:
            try:
                with open(self.executions_file, "r", encoding="utf-8") as f:
                    for line in f:
                        if line.strip():
                            d = json.loads(line)
                            if d.get("type") == "EXIT":
                                trades.append(d)
            except Exception:
                pass

        if not trades:
            return {"total_trades": 0, "win_rate": 0.0, "total_pnl": 0.0, "profit_factor": 0.0}

        wins = [t for t in trades if t.get("pnl_usd", 0) > 0]
        losses = [t for t in trades if t.get("pnl_usd", 0) < 0]
        total_pnl = sum(t.get("pnl_usd", 0) for t in trades)
        gross_profit = sum(t.get("pnl_usd", 0) for t in wins)
        gross_loss = abs(sum(t.get("pnl_usd", 0) for t in losses))

        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0)
        win_rate = (len(wins) / len(trades)) * 100 if trades else 0.0

        return {
            "total_trades": len(trades),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(win_rate, 1),
            "total_pnl": round(total_pnl, 2),
            "profit_factor": round(profit_factor, 2),
            "avg_r_multiple": round(sum(t.get("r_multiple", 0) for t in trades) / len(trades), 2) if trades else 0.0
        }


live_trade_journal = LiveTradeJournal()
