"""
BEAST v2 - Deterministic Risk Governor & Portfolio Preservation Agent
agents/risk_governor.py

Responsibilities:
1. Dynamic, account-agnostic position sizing based on real balance, volatility, and stop distance
2. Quality-tier risk scaling (Tier A+: 100%, Tier A: 85%, Tier B: 60%)
3. Micro-contract sizing with exact round-trip Delta fees and slippage cushions
4. Dynamic isolated leverage mapping (4x for high-beta, 5x for standard, 7x for low-vol indices)
5. Capital cushion preservation (25% liquid cushion preserved in wallet)
6. Max margin allocation per position (37.5% balance limit per position)
7. Portfolio correlation & sector exposure management
8. Persistent 5% daily drawdown circuit breaker with 00:00 UTC rollover
9. Strict 1:2.5 minimum reward-to-risk enforcement
"""

import os
import json
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone
from config import compounding_config, system_config

# Asset Correlation Groups for Portfolio Risk
CORRELATION_GROUPS = {
    "CRYPTO_MAJORS": ["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD", "DOGEUSD"],
    "HIGH_BETA_TECH": ["NVDAXUSD", "TSLAXUSD", "PLTRBUSD", "MSTRBUSD", "AMDBUSD", "ARMBUSD", "COINXUSD"],
    "MEGA_CAP_TECH": ["AAPLXUSD", "AMZNXUSD", "GOOGLXUSD", "METAXUSD", "MSFTXUSD"],
    "BROAD_INDICES": ["SPYXUSD", "QQQXUSD", "SOXLBUSD", "EWYBUSD"]
}


class RiskGovernorAgent:
    def __init__(self, state_file: str = "data/daily_drawdown.json"):
        self.state_file = state_file
        self.starting_capital = compounding_config.STARTING_CAPITAL
        self.target_capital = compounding_config.TARGET_CAPITAL
        self.stage1_target = compounding_config.STAGE_1_TARGET
        self.stage2_target = compounding_config.STAGE_2_TARGET
        self.max_drawdown_pct = compounding_config.MAX_DAILY_DRAWDOWN_PCT
        self._last_checked_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.daily_start_balance = self._load_daily_start_balance()
        self.daily_high_water_mark = self.daily_start_balance
        self.circuit_breaker_triggered = False
        self.max_concurrent_positions = 2

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
                    "daily_high_water_mark": round(getattr(self, "daily_high_water_mark", balance), 2),
                    "updated_at": datetime.now(timezone.utc).isoformat()
                }, f, indent=2)
        except Exception:
            pass

    def get_asset_group(self, symbol: str) -> str:
        for group, symbols in CORRELATION_GROUPS.items():
            if symbol in symbols:
                return group
        return "GENERAL"

    def check_portfolio_risk(
        self,
        symbol: str,
        direction: str,
        active_positions: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        """
        Validates whether opening a new position violates portfolio-level diversification
        or maximum active exposure constraints.
        """
        if not active_positions:
            return {"allowed": True, "reason": "No active positions"}

        # 1. Max concurrent positions
        if len(active_positions) >= self.max_concurrent_positions:
            return {
                "allowed": False,
                "reason": f"Max concurrent positions ({self.max_concurrent_positions}) reached"
            }

        # 2. Check if already in position for this exact symbol
        for pos in active_positions:
            if pos.get("symbol") == symbol:
                return {
                    "allowed": False,
                    "reason": f"Position already open for {symbol}"
                }

        # 3. Correlation risk check (prevent doubling down on correlated high-beta assets in same direction)
        target_group = self.get_asset_group(symbol)
        if target_group in ("CRYPTO_MAJORS", "HIGH_BETA_TECH"):
            for pos in active_positions:
                pos_sym = pos.get("symbol", "")
                pos_dir = pos.get("direction", pos.get("signal", "BUY"))
                if self.get_asset_group(pos_sym) == target_group and pos_dir.upper() == direction.upper():
                    return {
                        "allowed": False,
                        "reason": f"Correlated risk breach: already holding {pos_sym} in same group ({target_group}) and direction ({direction})"
                    }

        return {"allowed": True, "reason": "Portfolio limits satisfied"}

    def evaluate_proposal(
        self,
        proposal: Dict[str, Any],
        current_balance: float,
        active_positions: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        """
        Evaluate a trade proposal with deterministic risk math.
        """
        today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self._last_checked_date != today_utc:
            self._last_checked_date = today_utc
            self.daily_start_balance = current_balance
            self.daily_high_water_mark = current_balance
            self._save_daily_start_balance(current_balance)
            self.circuit_breaker_triggered = False
        elif current_balance > self.daily_high_water_mark:
            self.daily_high_water_mark = current_balance
            if current_balance > self.daily_start_balance * 1.08:
                self.daily_start_balance = current_balance
                self._save_daily_start_balance(current_balance)

        # 1. Daily Circuit Breaker Check
        daily_pnl = current_balance - self.daily_start_balance
        daily_loss_pct = abs(daily_pnl) / self.daily_start_balance if daily_pnl < 0 else 0.0

        if daily_loss_pct >= self.max_drawdown_pct:
            self.circuit_breaker_triggered = True
            return {
                "approved": False,
                "reason": f"CIRCUIT BREAKER ACTIVE: Daily loss ({daily_loss_pct*100:.1f}%) hit {self.max_drawdown_pct*100:.1f}% max drawdown ceiling.",
                "contracts": 0
            }

        # 2. Portfolio Correlation & Position Count Check
        symbol = proposal.get("symbol", "")
        direction = proposal.get("signal", proposal.get("direction", "BUY"))
        port_check = self.check_portfolio_risk(symbol, direction, active_positions)
        if not port_check["allowed"]:
            return {
                "approved": False,
                "reason": f"PORTFOLIO VETO: {port_check['reason']}",
                "contracts": 0
            }

        # 3. Dynamic Leverage & Stage Tiering
        asset_tier_leverage = compounding_config.VOLATILITY_LEVERAGE_MAP.get(
            symbol, compounding_config.DEFAULT_LEVERAGE
        )

        if current_balance < self.stage1_target:
            stage = "STAGE 1: BASE BUILDING ($60 -> $200)"
            risk_pct = compounding_config.MAX_RISK_PER_TRADE_PCT  # 2.5%
            max_leverage = asset_tier_leverage
        elif current_balance < self.stage2_target:
            stage = "STAGE 2: ACCELERATION ($200 -> $1,000)"
            risk_pct = 0.020  # 2.0%
            max_leverage = asset_tier_leverage
        else:
            stage = "STAGE 3: INSTITUTIONAL COMPOUNDING ($1,000 -> $5,000)"
            risk_pct = 0.015  # 1.5%
            max_leverage = min(4, asset_tier_leverage)

        # Quality tier scaling
        tier = proposal.get("tier", "A")
        tier_scale = 1.0
        if tier == "A+":
            tier_scale = 1.0
        elif tier == "A":
            tier_scale = 0.85
        elif tier == "B":
            tier_scale = 0.60
        elif tier in ("C", "NO_TRADE"):
            return {
                "approved": False,
                "reason": f"QUALITY VETO: Quality tier {tier} does not meet execution threshold (min B)",
                "contracts": 0
            }

        entry = float(proposal.get("entry_price", 0.0))
        stop_loss = float(proposal.get("stop_loss", 0.0))
        take_profit = float(proposal.get("take_profit_1", proposal.get("take_profit", 0.0)))
        reward_to_risk = float(proposal.get("reward_to_risk", 0.0))

        # 4. Verify Minimum Reward-to-Risk
        if reward_to_risk < compounding_config.MIN_REWARD_TO_RISK:
            return {
                "approved": False,
                "reason": f"R:R ratio ({reward_to_risk:.2f}) below 1:{compounding_config.MIN_REWARD_TO_RISK} threshold",
                "contracts": 0
            }

        # 5. Contract Sizing Math
        spec = system_config.CONTRACT_SPECS.get(symbol, {"contract_val": 0.01, "name": symbol})
        contract_val = spec["contract_val"]

        price_risk = abs(entry - stop_loss)
        if price_risk <= 0:
            return {"approved": False, "reason": "Invalid stop loss distance", "contracts": 0}

        # Dollar risk permitted on this trade scaled by tier
        risk_dollar_budget = (current_balance * risk_pct) * tier_scale

        # Round-trip fee cushion (0.10% total)
        fee_cushion_per_contract = entry * 0.0010 * contract_val
        # Slippage cushion (0.05% of entry)
        slippage_cushion_per_contract = entry * 0.0005 * contract_val
        risk_per_contract = (price_risk * contract_val) + fee_cushion_per_contract + slippage_cushion_per_contract

        # Calculated raw contracts
        raw_contracts = risk_dollar_budget / max(risk_per_contract, 1e-4)
        contracts = max(1, int(raw_contracts))

        # Position notional and required margin
        position_notional = contracts * contract_val * entry
        margin_required = position_notional / max_leverage

        # Capital Allocation Guardrail: Max 37.5% margin per position, 25% liquid cushion
        max_allowed_margin = current_balance * compounding_config.MAX_MARGIN_PER_POSITION_PCT
        if margin_required > max_allowed_margin:
            calculated_contracts = int((max_allowed_margin * max_leverage) / (contract_val * entry))
            if calculated_contracts < 1:
                one_contract_margin = (1 * contract_val * entry) / max_leverage
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

        # Hard safety ceiling guardrail
        safety_risk_ceiling_pct = compounding_config.MAX_RISK_SAFETY_CEILING_PCT * 100
        if actual_risk_pct > safety_risk_ceiling_pct:
            return {
                "approved": False,
                "reason": f"Actual risk ({actual_risk_pct:.1f}% / ${actual_risk_dollars:.2f}) exceeds small-account {safety_risk_ceiling_pct:.2f}% risk safety ceiling",
                "contracts": 0
            }

        # Potential profit at TP1
        potential_profit_tp1 = contracts * contract_val * abs(take_profit - entry)
        progress_pct = ((current_balance - self.starting_capital) / max(self.target_capital - self.starting_capital, 1.0)) * 100

        cushion_reserve = round(current_balance * compounding_config.CUSHION_PCT, 2)
        cushion_pct = round(compounding_config.CUSHION_PCT * 100, 1)

        return {
            "approved": True,
            "stage": stage,
            "contracts": contracts,
            "contract_val": contract_val,
            "isolated_leverage": max_leverage,
            "margin_required": round(margin_required, 2),
            "max_margin_budget_usd": round(max_allowed_margin, 2),
            "cushion_reserve_usd": cushion_reserve,
            "cushion_pct": cushion_pct,
            "position_notional": round(position_notional, 2),
            "risk_budget_usd": round(actual_risk_dollars, 2),
            "risk_pct": round(actual_risk_pct, 2),
            "potential_gain_usd": round(potential_profit_tp1, 2),
            "account_balance": round(current_balance, 2),
            "progress_to_5000": round(max(0.0, progress_pct), 2),
            "circuit_breaker_active": self.circuit_breaker_triggered,
            "reason": f"Sized for {stage} ({tier}): {contracts} contracts risking ${actual_risk_dollars:.2f} ({actual_risk_pct:.1f}%), margin ${margin_required:.2f}"
        }


risk_governor_agent = RiskGovernorAgent()
