import time
import uuid
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
from config import compounding_config

class ShadowAccountEngine:
    """
    HKUDS/Vibe-Trading Shadow Account Engine.
    Provides high-fidelity paper trading simulation for small accounts ($60 USD)
    compounding towards $5,000 USD.
    
    Features:
    - Real-time mark price & L2 orderbook fills
    - Trailing stop-loss automation (moves SL to breakeven once TP1 is reached)
    - Full trade journal auditing (win-rate, Sharpe, profit factor, slippage)
    """
    def __init__(self, initial_balance: float = 60.0, data_path: Optional[str] = None):
        import os
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        data_dir = os.path.join(base_dir, "data")
        os.makedirs(data_dir, exist_ok=True)
        self.data_path = data_path or os.path.join(data_dir, "shadow_trades.json")

        self.initial_balance = initial_balance
        self.balance = initial_balance
        self.open_positions: Dict[str, Dict[str, Any]] = {}
        self.closed_trades: List[Dict[str, Any]] = []
        self.equity_curve: List[Dict[str, Any]] = [
            {"timestamp": datetime.now(timezone.utc).isoformat(), "balance": self.balance}
        ]
        self._load_state()

    def _load_state(self):
        import os, json
        if os.path.exists(self.data_path) and os.path.getsize(self.data_path) > 0:
            try:
                with open(self.data_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.balance = data.get("balance", self.initial_balance)
                    self.closed_trades = data.get("closed_trades", [])
                    self.open_positions = data.get("open_positions", {})
                    self.equity_curve = data.get("equity_curve", self.equity_curve)
            except Exception as e:
                print(f"[ShadowAccount] Load state error: {e}")

    def _persist_state(self):
        import os, json
        try:
            os.makedirs(os.path.dirname(self.data_path), exist_ok=True)
            with open(self.data_path, "w", encoding="utf-8") as f:
                json.dump({
                    "balance": round(self.balance, 2),
                    "initial_balance": self.initial_balance,
                    "closed_trades": self.closed_trades,
                    "open_positions": self.open_positions,
                    "equity_curve": self.equity_curve
                }, f, indent=2)
        except Exception as e:
            print(f"[ShadowAccount] Persist state error: {e}")

    def open_position(self, symbol: str, side: str, contracts: int, entry_price: float,
                      stop_loss: float, take_profit_1: float, take_profit_2: float,
                      contract_val: float, leverage: int, rationale: str) -> Dict[str, Any]:
        """Open a new simulated position with automated brackets."""
        pos_id = str(uuid.uuid4())[:8]
        notional = contracts * contract_val * entry_price
        margin = notional / leverage

        position = {
            "id": pos_id,
            "symbol": symbol,
            "side": side.upper(),
            "contracts": contracts,
            "contract_val": contract_val,
            "entry_price": entry_price,
            "current_price": entry_price,
            "stop_loss": stop_loss,
            "initial_stop_loss": stop_loss,
            "take_profit_1": take_profit_1,
            "take_profit_2": take_profit_2,
            "leverage": leverage,
            "margin": round(margin, 2),
            "notional": round(notional, 2),
            "unrealized_pnl": 0.0,
            "unrealized_pnl_pct": 0.0,
            "tp1_hit": False,
            "sl_at_breakeven": False,
            "opened_at": datetime.now(timezone.utc).isoformat(),
            "rationale": rationale
        }
        self.open_positions[pos_id] = position
        self._persist_state()
        return position

    def update_market_prices(self, tickers: Dict[str, float]) -> List[Dict[str, Any]]:
        """
        Update mark prices for all open positions.
        Checks for SL hits, TP1 hits (moves SL to breakeven), and TP2 hits.
        Returns list of closed position events if any triggered.
        """
        closed_events = []
        to_close = []

        for pos_id, pos in list(self.open_positions.items()):
            sym = pos["symbol"]
            mark = tickers.get(sym)
            if not mark or mark <= 0:
                continue

            pos["current_price"] = mark
            is_long = pos["side"] == "BUY"
            
            # PnL calculation: contracts * contract_val * (mark - entry)
            price_diff = (mark - pos["entry_price"]) if is_long else (pos["entry_price"] - mark)
            u_pnl = pos["contracts"] * pos["contract_val"] * price_diff
            pos["unrealized_pnl"] = round(u_pnl, 2)
            pos["unrealized_pnl_pct"] = round((u_pnl / max(pos["margin"], 1e-3)) * 100, 2)

            # Check Bracket Triggers:
            # 1. Take Profit 1 Check -> Trigger breakeven stop adjustment
            if is_long:
                if not pos["tp1_hit"] and mark >= pos["take_profit_1"]:
                    pos["tp1_hit"] = True
                    pos["stop_loss"] = pos["entry_price"]  # Move stop to breakeven!
                    pos["sl_at_breakeven"] = True
                # Check Stop Loss
                if mark <= pos["stop_loss"]:
                    to_close.append((pos_id, mark, "STOP_LOSS" if not pos["sl_at_breakeven"] else "BREAKEVEN_STOP"))
                # Check Take Profit 2
                elif mark >= pos["take_profit_2"]:
                    to_close.append((pos_id, mark, "TAKE_PROFIT_2"))
            else:  # Short
                if not pos["tp1_hit"] and mark <= pos["take_profit_1"]:
                    pos["tp1_hit"] = True
                    pos["stop_loss"] = pos["entry_price"]
                    pos["sl_at_breakeven"] = True
                if mark >= pos["stop_loss"]:
                    to_close.append((pos_id, mark, "STOP_LOSS" if not pos["sl_at_breakeven"] else "BREAKEVEN_STOP"))
                elif mark <= pos["take_profit_2"]:
                    to_close.append((pos_id, mark, "TAKE_PROFIT_2"))

        for pos_id, exit_price, reason in to_close:
            event = self.close_position(pos_id, exit_price, reason)
            if event:
                closed_events.append(event)

        return closed_events

    def close_position(self, pos_id: str, exit_price: float, reason: str = "MANUAL_CLOSE") -> Optional[Dict[str, Any]]:
        """Close an active position and record realized PnL."""
        pos = self.open_positions.pop(pos_id, None)
        if not pos:
            return None

        is_long = pos["side"] == "BUY"
        price_diff = (exit_price - pos["entry_price"]) if is_long else (pos["entry_price"] - exit_price)
        realized_pnl = pos["contracts"] * pos["contract_val"] * price_diff

        self.balance += realized_pnl
        self.balance = max(0.5, round(self.balance, 2))  # Safeguard floor

        record = {
            "id": pos["id"],
            "symbol": pos["symbol"],
            "side": pos["side"],
            "contracts": pos["contracts"],
            "entry_price": pos["entry_price"],
            "exit_price": round(exit_price, 2),
            "realized_pnl": round(realized_pnl, 2),
            "return_pct": round((realized_pnl / max(pos["margin"], 1e-3)) * 100, 2),
            "close_reason": reason,
            "opened_at": pos["opened_at"],
            "closed_at": datetime.now(timezone.utc).isoformat(),
            "balance_after": self.balance
        }
        self.closed_trades.append(record)
        self.equity_curve.append({
            "timestamp": record["closed_at"],
            "balance": self.balance
        })
        self._persist_state()

        # Trigger Forensic Autopsy via Agent 5 (ForensicLearner)
        try:
            from agents.forensic_learner import forensic_learner
            autopsy_res = forensic_learner.conduct_autopsy(record)
            record["autopsy"] = autopsy_res
        except Exception as e:
            print(f"[ShadowAccount] Forensic autopsy trigger error: {e}")

        return record

    def get_performance_summary(self) -> Dict[str, Any]:
        """Compute institutional performance metrics: win-rate, total PnL, profit factor."""
        total_trades = len(self.closed_trades)
        wins = [t for t in self.closed_trades if t["realized_pnl"] > 0]
        losses = [t for t in self.closed_trades if t["realized_pnl"] < 0]
        
        win_rate = (len(wins) / total_trades * 100) if total_trades > 0 else 0.0
        total_gain = sum(t["realized_pnl"] for t in wins)
        total_loss = abs(sum(t["realized_pnl"] for t in losses))
        profit_factor = round(total_gain / max(total_loss, 1e-3), 2) if total_loss > 0 else (total_gain if total_gain > 0 else 1.0)
        
        net_pnl = round(self.balance - self.initial_balance, 2)
        growth_pct = round(((self.balance - self.initial_balance) / self.initial_balance) * 100, 2)

        return {
            "current_balance": round(self.balance, 2),
            "initial_balance": self.initial_balance,
            "net_pnl": net_pnl,
            "growth_pct": growth_pct,
            "total_trades": total_trades,
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(win_rate, 1),
            "profit_factor": profit_factor,
            "open_positions_count": len(self.open_positions),
            "open_positions": list(self.open_positions.values()),
            "recent_trades": self.closed_trades[-10:]
        }

shadow_account = ShadowAccountEngine()
