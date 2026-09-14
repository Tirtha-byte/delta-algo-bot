import time
from typing import Dict, Any, List, Optional
from enum import Enum

class TriggerState(str, Enum):
    SETUP_FORMING = "SETUP_FORMING"
    TRIGGER_APPROACHING = "TRIGGER_APPROACHING"
    TRIGGER_CONFIRMED = "TRIGGER_CONFIRMED"
    INVALIDATED = "INVALIDATED"

class CandidateLifecycle(str, Enum):
    SCANNED = "SCANNED"
    WATCH = "WATCH"
    SETUP_FORMING = "SETUP_FORMING"
    TRIGGER_APPROACHING = "TRIGGER_APPROACHING"
    QUALIFIED = "QUALIFIED"
    RISK_APPROVED = "RISK_APPROVED"
    ORDER_SUBMITTED = "ORDER_SUBMITTED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    PROTECTION_CONFIRMED = "PROTECTION_CONFIRMED"
    MANAGED = "MANAGED"
    CLOSED = "CLOSED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    TIMEOUT = "TIMEOUT"
    UNKNOWN = "UNKNOWN"
    ORPHANED = "ORPHANED"

class TriggerEngine:
    """
    BEAST v2 Entry Trigger & Timing Engine.
    Decouples setup quality (Macro & Wave) from execution timing (Micro).
    """
    def __init__(self):
        pass

    def evaluate(
        self,
        direction: str,
        setup_status: str = "ACTIVE",
        candle_5m_closed: bool = True,
        microprice_drift: float = 0.0,
        cvd_confirmed: bool = True
    ) -> Dict[str, Any]:
        """Convenience evaluation for backtester and quality scoring."""
        if setup_status != "ACTIVE":
            state = "INVALIDATED"
        elif candle_5m_closed and cvd_confirmed:
            state = "TRIGGER_CONFIRMED"
        elif cvd_confirmed:
            state = "TRIGGER_APPROACHING"
        else:
            state = "SETUP_FORMING"

        return {
            "state": state,
            "trigger_state": state,
            "candle_5m_closed": candle_5m_closed,
            "cvd_confirmed": cvd_confirmed,
            "is_execution_eligible": (state == "TRIGGER_CONFIRMED")
        }

    def evaluate_trigger(self,
                         symbol: str,
                         setup_result: Dict[str, Any],
                         candles_5m: List[Dict[str, Any]],
                         flow_data: Dict[str, Any],
                         micro_data: Dict[str, Any],
                         atr: float) -> Dict[str, Any]:
        """
        Evaluates immediate entry trigger conditions on 5-minute micro timeframe.
        Returns:
            trigger_score (0-100)
            trigger_state (SETUP_FORMING | TRIGGER_APPROACHING | TRIGGER_CONFIRMED | INVALIDATED)
            is_execution_eligible (bool)
            waiting_for (list of strings explaining unfulfilled triggers)
        """
        direction = setup_result.get("direction", "NEUTRAL")
        lead_score = setup_result.get("lead_score", 0.0)

        if direction not in ("LONG", "SHORT") or lead_score < 60.0:
            return {
                "symbol": symbol,
                "direction": direction,
                "trigger_score": 0.0,
                "trigger_state": TriggerState.SETUP_FORMING,
                "is_execution_eligible": False,
                "waiting_for": ["Statistically valid setup forming"],
                "trigger_checklist": {}
            }

        if not candles_5m or len(candles_5m) < 6:
            return {
                "symbol": symbol,
                "direction": direction,
                "trigger_score": 30.0,
                "trigger_state": TriggerState.SETUP_FORMING,
                "is_execution_eligible": False,
                "waiting_for": ["Insufficient 5m candles for entry trigger verification"],
                "trigger_checklist": {}
            }

        recent_5m = candles_5m[-1]
        c_open = float(recent_5m.get("open", 0.0))
        c_high = float(recent_5m.get("high", 0.0))
        c_low = float(recent_5m.get("low", 0.0))
        c_close = float(recent_5m.get("close", 0.0))

        flow = flow_data.get("flow", {})
        delta = flow.get("delta", 0.0)
        aggressor_ratio = flow.get("aggressor_ratio", 1.0)
        flow_bias = flow.get("flow_bias", "BALANCED")
        divergence = flow_data.get("divergence", {}).get("divergence", "FLOW_BALANCED")

        spread_pct = micro_data.get("spread_pct", 0.0)
        micro_drift = micro_data.get("micro_drift", 0.0)
        depth_imb = micro_data.get("depth_imbalance_5", 0.0)

        waiting_for = []
        checklist = {}

        trigger_points = 0.0

        # Check 1: Spread & Liquidity gate (< 0.20% and not expanded)
        spread_regime = micro_data.get("spread_regime", {})
        is_spread_clean = (spread_pct <= 0.20) and not spread_regime.get("is_expanded", False)
        checklist["spread_clean"] = is_spread_clean
        if is_spread_clean:
            trigger_points += 20.0
        else:
            waiting_for.append(f"Spread normalization (current: {spread_pct:.3f}%)")

        # Check 2: 5M Candle Confirmation (Close > Open for Long, Close < Open for Short)
        if direction == "LONG":
            is_bar_aligned = (c_close > c_open)
            checklist["5m_bar_confirmation"] = is_bar_aligned
            if is_bar_aligned:
                trigger_points += 25.0
            else:
                waiting_for.append("5M bullish candle close confirmation")

            # Check 3: Micro Orderflow & CVD Confirmation
            is_flow_confirmed = (delta > 0 or flow_bias == "BULLISH_FLOW" or divergence == "BULLISH_CONFIRMATION")
            checklist["flow_confirmation"] = is_flow_confirmed
            if is_flow_confirmed:
                trigger_points += 25.0
            else:
                waiting_for.append("Aggressive buyer delta / positive CVD confirmation")

            # Check 4: Microprice Drift Alignment
            is_drift_aligned = (micro_drift >= -0.05 and depth_imb >= -0.10)
            checklist["microprice_alignment"] = is_drift_aligned
            if is_drift_aligned:
                trigger_points += 15.0
            else:
                waiting_for.append(f"Orderbook top drift alignment (drift: {micro_drift:+.2f})")

            # Check 5: 5M Structural Pivot Reclaim
            prev_high_3 = max(float(c.get("high", 0.0)) for c in candles_5m[-4:-1])
            is_pivot_reclaimed = c_close >= (prev_high_3 - (0.1 * atr))
            checklist["pivot_reclaim"] = is_pivot_reclaimed
            if is_pivot_reclaimed:
                trigger_points += 15.0
            else:
                waiting_for.append("5M swing pivot high reclaim")

        else:  # SHORT
            is_bar_aligned = (c_close < c_open)
            checklist["5m_bar_confirmation"] = is_bar_aligned
            if is_bar_aligned:
                trigger_points += 25.0
            else:
                waiting_for.append("5M bearish candle close confirmation")

            is_flow_confirmed = (delta < 0 or flow_bias == "BEARISH_FLOW" or divergence == "BEARISH_CONFIRMATION")
            checklist["flow_confirmation"] = is_flow_confirmed
            if is_flow_confirmed:
                trigger_points += 25.0
            else:
                waiting_for.append("Aggressive seller delta / negative CVD confirmation")

            is_drift_aligned = (micro_drift <= 0.05 and depth_imb <= 0.10)
            checklist["microprice_alignment"] = is_drift_aligned
            if is_drift_aligned:
                trigger_points += 15.0
            else:
                waiting_for.append(f"Orderbook top drift alignment (drift: {micro_drift:+.2f})")

            prev_low_3 = min(float(c.get("low", 0.0)) for c in candles_5m[-4:-1])
            is_pivot_reclaimed = c_close <= (prev_low_3 + (0.1 * atr))
            checklist["pivot_reclaim"] = is_pivot_reclaimed
            if is_pivot_reclaimed:
                trigger_points += 15.0
            else:
                waiting_for.append("5M swing pivot low breakdown")

        trigger_score = min(100.0, round(trigger_points, 1))

        # Classify Trigger State
        if trigger_score >= 80.0 and len(waiting_for) == 0:
            trigger_state = TriggerState.TRIGGER_CONFIRMED
            is_eligible = True
        elif trigger_score >= 60.0:
            trigger_state = TriggerState.TRIGGER_APPROACHING
            is_eligible = False
        else:
            trigger_state = TriggerState.SETUP_FORMING
            is_eligible = False

        return {
            "symbol": symbol,
            "direction": direction,
            "trigger_score": trigger_score,
            "trigger_state": trigger_state,
            "is_execution_eligible": is_eligible,
            "waiting_for": waiting_for,
            "trigger_checklist": checklist
        }

trigger_engine = TriggerEngine()
