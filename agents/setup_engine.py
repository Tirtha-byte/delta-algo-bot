import time
from typing import Dict, Any, List, Optional
from enum import Enum
import numpy as np

class SetupFamily(str, Enum):
    TREND_PULLBACK = "TREND_PULLBACK"
    BREAKOUT_RETEST = "BREAKOUT_RETEST"
    LIQUIDITY_SWEEP_REVERSAL = "LIQUIDITY_SWEEP_REVERSAL"
    ABSORPTION_REVERSAL = "ABSORPTION_REVERSAL"
    FAILED_BREAKOUT = "FAILED_BREAKOUT"
    MEAN_REVERSION = "MEAN_REVERSION"
    NONE = "NONE"

class SetupEngine:
    """
    BEAST v2 Quantitative Setup Engine.
    Evaluates independent LONG_SETUP_SCORE (0-100) and SHORT_SETUP_SCORE (0-100).
    Eliminates scalar alpha cancellation.
    Identifies 6 discrete setup families.
    """
    def __init__(self):
        pass

    def evaluate(
        self,
        regime_result: Dict[str, Any],
        microstructure_result: Dict[str, Any],
        orderflow_result: Dict[str, Any],
        technical_data: Dict[str, Any],
        symbol: str = "BTCUSD"
    ) -> Dict[str, Any]:
        """Convenience wrapper for evaluate_setups."""
        c = technical_data.get("close", 0.0)
        atr = float(regime_result.get("atr", 10.0))
        ema20 = float(technical_data.get("ema21", c))
        dist_atr = abs(c - ema20) / max(atr, 1e-4)

        tech_15m = {
            "valid": True,
            "current_close": c,
            "ema20": ema20,
            "ema50": float(technical_data.get("ema50", c)),
            "ema200": float(technical_data.get("ema200", c)),
            "dist_ema20_atr": dist_atr,
            "atr": atr,
            "rsi": float(technical_data.get("rsi", 50.0))
        }
        cvd_s = float(orderflow_result.get("cvd_slope", 0.0))
        flow_data = {
            "flow": {
                "flow_bias": "BUY_DOMINANT" if cvd_s > 0 else ("SELL_DOMINANT" if cvd_s < 0 else "BALANCED"),
                "delta": cvd_s,
                "aggressor_ratio": 1.3 if cvd_s > 0 else 0.7
            },
            "absorption": {
                "detected": orderflow_result.get("absorption", "NONE") != "NONE",
                "type": orderflow_result.get("absorption", "NONE")
            },
            "sweep": {"detected": False}
        }
        micro_data = {
            "weighted_imbalance": float(microstructure_result.get("weighted_imbalance", 0.0)),
            "spread_state": microstructure_result.get("spread_state", "NORMAL")
        }
        return self.evaluate_setups(
            symbol=symbol,
            regime_data=regime_result,
            flow_data=flow_data,
            micro_data=micro_data,
            technicals_15m=tech_15m
        )

    def evaluate_setups(self,
                        symbol: str,
                        regime_data: Dict[str, Any],
                        flow_data: Dict[str, Any],
                        micro_data: Dict[str, Any],
                        technicals_15m: Dict[str, Any],
                        candles_5m: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """
        Calculates independent Long and Short scores across the 6 setup families.
        """
        regime = regime_data.get("regime", "UNCERTAIN")
        tech = technicals_15m
        if not tech.get("valid"):
            return {
                "symbol": symbol,
                "long_setup_score": 0.0,
                "short_setup_score": 0.0,
                "best_setup": SetupFamily.NONE,
                "direction": "NEUTRAL",
                "details": {"reason": "Insufficient technical data"}
            }

        c = tech["current_close"]
        ema20 = tech["ema20"]
        ema50 = tech["ema50"]
        ema200 = tech["ema200"]
        dist_atr = tech["dist_ema20_atr"]
        rsi = tech.get("rsi", 50.0)
        atr = tech["atr"]

        flow = flow_data.get("flow", {})
        flow_bias = flow.get("flow_bias", "BALANCED")
        delta = flow.get("delta", 0.0)
        aggressor_ratio = flow.get("aggressor_ratio", 1.0)
        absorption = flow_data.get("absorption", {})
        sweep = flow_data.get("sweep", {})
        divergence = flow_data.get("divergence", {})

        depth_imb = micro_data.get("depth_imbalance_5", 0.0)
        micro_drift = micro_data.get("micro_drift", 0.0)

        # Classify pullback depth
        if dist_atr <= 1.0:
            pullback_state = "NORMAL_PULLBACK"
        elif dist_atr <= 2.0:
            pullback_state = "DEEP_PULLBACK"
        elif dist_atr <= 2.8:
            pullback_state = "EXTENSION"
        else:
            pullback_state = "EXHAUSTION"

        long_scores: Dict[SetupFamily, float] = {}
        short_scores: Dict[SetupFamily, float] = {}

        # -------------------------------------------------------------
        # SETUP 1: TREND_PULLBACK (High-Probability Value Zone)
        # -------------------------------------------------------------
        trend_separation = abs(ema20 - ema50) / max(ema50, 1e-4)

        # Long: Bullish trend, orderly pullback to EMA20, RSI reset, positive flow
        if regime in ("TREND_UP", "BREAKOUT") and c >= ema50 and ema20 >= ema50:
            score_long_pb = 50.0
            if pullback_state in ("NORMAL_PULLBACK", "DEEP_PULLBACK"): score_long_pb += 20.0
            if 38.0 <= rsi <= 56.0: score_long_pb += 15.0  # Healthy momentum reset
            if delta > 0 or flow_bias == "BULLISH_FLOW": score_long_pb += 10.0
            if micro_drift > 0: score_long_pb += 5.0
            if trend_separation >= 0.0010: score_long_pb += 10.0  # Clear trend slope separation
            if dist_atr > 1.8: score_long_pb = min(score_long_pb, 45.0)  # Prohibit chasing extended moves
            long_scores[SetupFamily.TREND_PULLBACK] = min(100.0, score_long_pb)
        else:
            long_scores[SetupFamily.TREND_PULLBACK] = 15.0

        # Short: Bearish trend, orderly rally to EMA20, RSI reset (44-62), negative flow
        if regime in ("TREND_DOWN", "BREAKOUT") and c <= ema50 and ema20 <= ema50:
            score_short_pb = 50.0
            if pullback_state in ("NORMAL_PULLBACK", "DEEP_PULLBACK"): score_short_pb += 20.0
            if 44.0 <= rsi <= 62.0: score_short_pb += 15.0  # Momentum reset
            if delta < 0 or flow_bias == "BEARISH_FLOW": score_short_pb += 10.0
            if micro_drift < 0: score_short_pb += 5.0
            if trend_separation >= 0.0010: score_short_pb += 10.0  # Clear trend slope separation
            if dist_atr > 1.8: score_short_pb = min(score_short_pb, 45.0)  # Prohibit chasing extended moves
            short_scores[SetupFamily.TREND_PULLBACK] = min(100.0, score_short_pb)
        else:
            short_scores[SetupFamily.TREND_PULLBACK] = 15.0

        # -------------------------------------------------------------
        # SETUP 2: BREAKOUT_RETEST
        # -------------------------------------------------------------
        sw_type = sweep.get("sweep_type", "NONE")
        if sw_type == "VALID_BREAKOUT":
            if delta > 0:
                long_scores[SetupFamily.BREAKOUT_RETEST] = 85.0
                short_scores[SetupFamily.BREAKOUT_RETEST] = 10.0
            else:
                short_scores[SetupFamily.BREAKOUT_RETEST] = 85.0
                long_scores[SetupFamily.BREAKOUT_RETEST] = 10.0
        else:
            long_scores[SetupFamily.BREAKOUT_RETEST] = 20.0
            short_scores[SetupFamily.BREAKOUT_RETEST] = 20.0

        # -------------------------------------------------------------
        # SETUP 3: LIQUIDITY_SWEEP_REVERSAL (Turtle Soup)
        # -------------------------------------------------------------
        if sw_type == "LIQUIDITY_SWEEP":
            # If highs were swept and rejected -> Bearish reversal
            if c > ema20:
                short_scores[SetupFamily.LIQUIDITY_SWEEP_REVERSAL] = 88.0
                long_scores[SetupFamily.LIQUIDITY_SWEEP_REVERSAL] = 15.0
            else:
                # Lows swept and rejected -> Bullish reversal
                long_scores[SetupFamily.LIQUIDITY_SWEEP_REVERSAL] = 88.0
                short_scores[SetupFamily.LIQUIDITY_SWEEP_REVERSAL] = 15.0
        else:
            long_scores[SetupFamily.LIQUIDITY_SWEEP_REVERSAL] = 20.0
            short_scores[SetupFamily.LIQUIDITY_SWEEP_REVERSAL] = 20.0

        # -------------------------------------------------------------
        # SETUP 4: ABSORPTION_REVERSAL
        # -------------------------------------------------------------
        abs_type = absorption.get("absorption", "NONE")
        if abs_type == "BULLISH_ABSORPTION":
            conf = absorption.get("confidence", 0.7)
            long_scores[SetupFamily.ABSORPTION_REVERSAL] = min(100.0, 70.0 + (conf * 25.0))
            short_scores[SetupFamily.ABSORPTION_REVERSAL] = 10.0
        elif abs_type == "BEARISH_ABSORPTION":
            conf = absorption.get("confidence", 0.7)
            short_scores[SetupFamily.ABSORPTION_REVERSAL] = min(100.0, 70.0 + (conf * 25.0))
            long_scores[SetupFamily.ABSORPTION_REVERSAL] = 10.0
        else:
            long_scores[SetupFamily.ABSORPTION_REVERSAL] = 20.0
            short_scores[SetupFamily.ABSORPTION_REVERSAL] = 20.0

        # -------------------------------------------------------------
        # SETUP 5: FAILED_BREAKOUT
        # -------------------------------------------------------------
        div_type = divergence.get("divergence", "FLOW_BALANCED")
        if sw_type == "FAILED_BREAKOUT" or (div_type == "BEARISH_DIVERGENCE" and c > ema20):
            short_scores[SetupFamily.FAILED_BREAKOUT] = 82.0
            long_scores[SetupFamily.FAILED_BREAKOUT] = 15.0
        elif sw_type == "FAILED_BREAKOUT" or (div_type == "BULLISH_DIVERGENCE" and c < ema20):
            long_scores[SetupFamily.FAILED_BREAKOUT] = 82.0
            short_scores[SetupFamily.FAILED_BREAKOUT] = 15.0
        else:
            long_scores[SetupFamily.FAILED_BREAKOUT] = 15.0
            short_scores[SetupFamily.FAILED_BREAKOUT] = 15.0

        # -------------------------------------------------------------
        # SETUP 6: MEAN_REVERSION (Strictly forbidden in strong trends and exhaustion)
        # -------------------------------------------------------------
        if regime in ("RANGE", "LOW_VOLATILITY"):
            if c < ema20 and (dist_atr >= 1.8 or rsi <= 32.0):
                long_scores[SetupFamily.MEAN_REVERSION] = 78.0
                short_scores[SetupFamily.MEAN_REVERSION] = 10.0
            elif c > ema20 and (dist_atr >= 1.8 or rsi >= 68.0):
                short_scores[SetupFamily.MEAN_REVERSION] = 78.0
                long_scores[SetupFamily.MEAN_REVERSION] = 10.0
            else:
                long_scores[SetupFamily.MEAN_REVERSION] = 20.0
                short_scores[SetupFamily.MEAN_REVERSION] = 20.0
        else:
            # Hard lock: NEVER mean revert in strong trend or exhaustion regimes (anti knife-catching)
            long_scores[SetupFamily.MEAN_REVERSION] = 0.0
            short_scores[SetupFamily.MEAN_REVERSION] = 0.0

        # Find best setup for Long & Short
        best_long_family = max(long_scores.items(), key=lambda x: x[1])
        best_short_family = max(short_scores.items(), key=lambda x: x[1])

        max_long_score = round(best_long_family[1], 1)
        max_short_score = round(best_short_family[1], 1)

        # Decision
        if max_long_score >= 70.0 and max_long_score > max_short_score + 15.0:
            direction = "LONG"
            chosen_setup = best_long_family[0]
            lead_score = max_long_score
        elif max_short_score >= 70.0 and max_short_score > max_long_score + 15.0:
            direction = "SHORT"
            chosen_setup = best_short_family[0]
            lead_score = max_short_score
        else:
            direction = "NEUTRAL"
            chosen_setup = SetupFamily.NONE
            lead_score = max(max_long_score, max_short_score)

        return {
            "symbol": symbol,
            "direction": direction,
            "best_setup": chosen_setup,
            "lead_score": lead_score,
            "long_setup_score": max_long_score,
            "short_setup_score": max_short_score,
            "best_long_setup": best_long_family[0],
            "best_short_setup": best_short_family[0],
            "pullback_state": pullback_state,
            "all_long_scores": {k.value: v for k, v in long_scores.items()},
            "all_short_scores": {k.value: v for k, v in short_scores.items()}
        }

setup_engine = SetupEngine()
