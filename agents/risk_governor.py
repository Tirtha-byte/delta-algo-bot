import os
import json
from typing import Dict, Any, Optional
from datetime import datetime, timezone
from config import compounding_config, system_config

class RiskGovernorAgent:
    """
    Agent 3: Risk Governor & Capital Preservation Agent.
    Specialized for turning small bankrolls ($60 USD) into $5,000 USD via disciplined compounding.
    
    Responsibilities:
    - Milestone Stage Progression (Stage 1 -> Stage 2 -> Stage 3)
    - Precise Micro-Contract Sizing with round-trip fee cushion
    - Isolated Leverage Enforcement (default 4x, max 7x)
    - Persistent 5% Daily Drawdown Circuit Breaker with 00:00 UTC rollover
    - Minimum 1:2.5 Risk-to-Reward Verification
    """
    def __init__(self, state_file: str = "data/daily_drawdown.json"):
        self.state_file = state_file
        self.starting_capital = compounding_config.STARTING_CAPITAL
        self.target_capital = compounding_config.TARGET_CAPITAL
        self.stage1_target = compounding_config.STAGE_1_TARGET
        self.stage2_target = compounding_config.STAGE_2_TARGET
        self.max_drawdown_pct = compounding_config.MAX_DAILY_DRAWDOWN_PCT
        self._last_checked_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.daily_start_balance = self._load_daily_start_balance()
        self.circuit_breaker_triggered = False

    def _load_daily_start_balance(self) -> float:
        today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, "r") as f:
                    data = json.load(f)
                    if data.get("date") == today_utc:
                        return float(data.get("daily_start_balance", compounding_config.STARTING_CAPITAL))
            except Exception:
                pass
        return compounding_config.STARTING_CAPITAL

    def _save_daily_start_balance(self, balance: float):
        today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        try:
            os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
            with open(self.state_file, "w") as f:
                json.dump({
                    "date": today_utc,
                    "daily_start_balance": round(balance, 2),
                    "updated_at": datetime.now(timezone.utc).isoformat()
                }, f, indent=2)
        except Exception:
            pass

    def evaluate_proposal(self, proposal: Dict[str, Any], current_balance: float) -> Dict[str, Any]:
        """
        Evaluate a trade proposal from the Orchestrator.
        Computes exact contract sizing, checks leverage constraints, and returns sizing or veto.
        """
        today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self._last_checked_date != today_utc:
            self._last_checked_date = today_utc
            self.daily_start_balance = current_balance
            self._save_daily_start_balance(current_balance)
            self.circuit_breaker_triggered = False
        elif current_balance > self.daily_start_balance * 1.08:
            # Fresh deposit or high-watermark growth: Update daily baseline so circuit breaker operates on new capital
            self.daily_start_balance = current_balance
            self._save_daily_start_balance(current_balance)

        # 1. Circuit Breaker Check
        daily_pnl = current_balance - self.daily_start_balance
        daily_loss_pct = abs(daily_pnl) / self.daily_start_balance if daily_pnl < 0 else 0.0
        
        if daily_loss_pct >= self.max_drawdown_pct:
            self.circuit_breaker_triggered = True
            return {
                "approved": False,
                "reason": f"CIRCUIT BREAKER ACTIVE: Daily loss ({daily_loss_pct*100:.1f}%) hit 5% max drawdown ceiling.",
                "contracts": 0
            }

        # 2. Determine Current Milestone Stage & Dynamic Asset Leverage
        symbol = proposal.get("symbol", "")
        asset_tier_leverage = compounding_config.VOLATILITY_LEVERAGE_MAP.get(
            symbol, compounding_config.DEFAULT_LEVERAGE
        )

        if current_balance < self.stage1_target:
            stage = "STAGE 1: BASE BUILDING ($60 -> $200)"
            risk_pct = 0.025  # 2.5% max risk
            max_leverage = asset_tier_leverage
        elif current_balance < self.stage2_target:
            stage = "STAGE 2: ACCELERATION ($200 -> $1,000)"
            risk_pct = 0.020  # 2.0% risk
            max_leverage = asset_tier_leverage
        else:
            stage = "STAGE 3: INSTITUTIONAL COMPOUNDING ($1,000 -> $5,000)"
            risk_pct = 0.015  # 1.5% risk
            max_leverage = min(4, asset_tier_leverage)

        entry = float(proposal.get("entry_price", 0.0))
        stop_loss = float(proposal.get("stop_loss", 0.0))
        take_profit = float(proposal.get("take_profit_1", 0.0))
        reward_to_risk = float(proposal.get("reward_to_risk", 0.0))

        # 3. Verify Minimum Reward-to-Risk
        if reward_to_risk < compounding_config.MIN_REWARD_TO_RISK:
            return {
                "approved": False,
                "reason": f"R:R ratio ({reward_to_risk:.2f}) below 1:{compounding_config.MIN_REWARD_TO_RISK} threshold",
                "contracts": 0
            }

        # 4. Micro-Contract Sizing Math
        spec = system_config.CONTRACT_SPECS.get(symbol, {"contract_val": 0.01, "name": symbol})
        contract_val = spec["contract_val"]

        price_risk = abs(entry - stop_loss)
        if price_risk <= 0:
            return {"approved": False, "reason": "Invalid stop loss distance", "contracts": 0}

        # Dollar risk permitted on this trade (e.g. $60 * 0.025 = $1.50)
        risk_dollar_budget = current_balance * risk_pct
        
        # Risk per contract with 0.10% round-trip fee cushion
        fee_cushion_per_contract = entry * 0.001 * contract_val
        risk_per_contract = (price_risk * contract_val) + fee_cushion_per_contract
        
        # Calculated contracts
        raw_contracts = risk_dollar_budget / max(risk_per_contract, 1e-4)
        contracts = max(1, int(raw_contracts))

        # Check total position notional and margin required at 4x leverage
        position_notional = contracts * contract_val * entry
        margin_required = position_notional / max_leverage

        # Capital Allocation Guardrail: Max 37.5% balance in initial margin for 1 position (75% / 2 slots)
        # Leaving a permanent 25% liquid cushion preserved in wallet
        max_allowed_margin = current_balance * compounding_config.MAX_MARGIN_PER_POSITION_PCT
        if margin_required > max_allowed_margin:
            # Scale down contracts
            calculated_contracts = int((max_allowed_margin * max_leverage) / (contract_val * entry))
            if calculated_contracts < 1:
                one_contract_margin = (1 * contract_val * entry) / max_leverage
                # Allow indivisible single contract if within tolerance of cushion threshold
                max_single_contract_tolerance = current_balance * (compounding_config.MAX_MARGIN_PER_POSITION_PCT + 0.03)
                if one_contract_margin <= max_single_contract_tolerance:
                    contracts = 1
                else:
                    return {
                        "approved": False,
                        "reason": f"Contract minimum size (margin ${one_contract_margin:.2f}) exceeds max margin tolerance for ${current_balance:.2f} balance",
                        "contracts": 0
                    }
            else:
                contracts = calculated_contracts
            position_notional = contracts * contract_val * entry
            margin_required = position_notional / max_leverage

        actual_risk_dollars = contracts * risk_per_contract
        actual_risk_pct = (actual_risk_dollars / current_balance) * 100

        # Hard safety guardrail: if 1 contract risk exceeds safety ceiling, veto to prevent oversize losses
        safety_risk_ceiling_pct = compounding_config.MAX_RISK_SAFETY_CEILING_PCT * 100
        if actual_risk_pct > safety_risk_ceiling_pct:
            return {
                "approved": False,
                "reason": f"Actual risk ({actual_risk_pct:.1f}% / ${actual_risk_dollars:.2f}) exceeds small-account {safety_risk_ceiling_pct:.2f}% risk safety ceiling",
                "contracts": 0
            }

        # Max potential profit at TP1
        potential_profit_tp1 = contracts * contract_val * abs(take_profit - entry)

        progress_pct = ((current_balance - self.starting_capital) / (self.target_capital - self.starting_capital)) * 100

        return {
            "approved": True,
            "stage": stage,
            "contracts": contracts,
            "contract_val": contract_val,
            "isolated_leverage": max_leverage,
            "margin_required": round(margin_required, 2),
            "max_margin_budget_usd": round(max_allowed_margin, 2),
            "cushion_reserve_usd": round(current_balance * compounding_config.CUSHION_PCT, 2),
            "cushion_pct": round(compounding_config.CUSHION_PCT * 100, 1),
            "position_notional": round(position_notional, 2),
            "risk_budget_usd": round(actual_risk_dollars, 2),
            "risk_pct": round(actual_risk_pct, 2),
            "potential_gain_usd": round(potential_profit_tp1, 2),
            "account_balance": round(current_balance, 2),
            "progress_to_5000": round(max(0.0, progress_pct), 2),
            "circuit_breaker_active": self.circuit_breaker_triggered,
            "reason": f"Sized for {stage}: {contracts} contracts risking ${actual_risk_dollars:.2f} ({actual_risk_pct:.1f}%), margin ${margin_required:.2f}"
        }

risk_governor_agent = RiskGovernorAgent()
