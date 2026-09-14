import os
import json
import time
from typing import Dict, Any, List, Optional
import numpy as np
import pandas as pd
from delta_client import delta_client
from config import compounding_config, quant_config, mtf_config
from agents.microstructure_engine import microstructure_engine
from agents.session_momentum_engine import session_momentum_engine

class QuantAnalystAgent:
    """
    Agent 2: Quant Analyst & Triple-Horizon Multi-Timeframe Quantitative Engine.
    
    Triple-Horizon Architecture:
    - Horizon 1 (1-Hour Macro Tide):
      * 250-bar converged EMA 50 and EMA 200 Macro Trend Ribbon
      * Strict Directional Filter: Prohibits BUYs in Bearish 1h, prohibits SELLs in Bullish 1h
      * Completely eliminates counter-trend traps
      
    - Horizon 2 (15-Minute Structural Wave & Qlib Alphas):
      * 250-bar converged EMA 20/50/200 Ribbon
      * ATR 14 volatility and RSI 14 boundary momentum
      * Pure Vectorized Qlib Factors:
        - Multi-Horizon Momentum (ROC 5/10/20/30 with decay)
        - Price-Volume Correlation (CORR_PV_10, CORR_PV_20)
        - Candle Morphology (KMID2 body fraction, KUP, KLOW wick absorption)
        - Volatility Compression Ratio (STD_NORM = std10 / std30)
      * Value Pullback Filter: Penalizes chasing extended candles > 2.0x ATR from EMA20
      * Composite Alpha Gating (|Alpha| >= 0.45)
      
    - Horizon 3 (5-Minute Micro Trigger & Structural Stop-Loss):
      * Scans last 10 5m candles for swing low/high pivots
      * Pins Stop-Loss beyond market structure instead of arbitrary 1.5x ATR distance
      * Calculates 1:2.5 minimum R:R with structural buffer
    """
    def __init__(self):
        self.min_rr = quant_config.MIN_REWARD_TO_RISK
        self.alpha_threshold = quant_config.ALPHA_THRESHOLD

    def _analyze_1h_macro(self, candles_1h: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Analyze 1-Hour Horizon (The Macro Tide) for Trend Regime."""
        if not candles_1h or len(candles_1h) < 20:
            return {"valid": False, "macro_trend": "NEUTRAL", "reason": "Insufficient 1h history"}

        df = pd.DataFrame(candles_1h)
        df["close"] = df["close"].astype(float)

        # 1h EMAs: EMA50 and EMA200 (converged with up to 250 bars)
        df["ema50"] = df["close"].ewm(span=50, adjust=False).mean()
        span_200 = 200 if len(df) >= 200 else len(df)
        df["ema200"] = df["close"].ewm(span=span_200, adjust=False).mean()

        latest = df.iloc[-1]
        close_1h = float(latest["close"])
        ema50_1h = float(latest["ema50"])
        ema200_1h = float(latest["ema200"])

        # Strict Macro Regime Classification
        if close_1h > ema50_1h and ema50_1h >= ema200_1h:
            macro_trend = "BULLISH"
        elif close_1h < ema50_1h and ema50_1h <= ema200_1h:
            macro_trend = "BEARISH"
        elif close_1h > ema200_1h:
            macro_trend = "BULLISH"
        elif close_1h < ema200_1h:
            macro_trend = "BEARISH"
        else:
            macro_trend = "NEUTRAL"

        return {
            "valid": True,
            "macro_trend": macro_trend,
            "close_1h": close_1h,
            "ema50_1h": round(ema50_1h, 2),
            "ema200_1h": round(ema200_1h, 2)
        }

    def _find_5m_structural_stop(self, candles_5m: List[Dict[str, Any]], signal: str, entry_price: float, atr_15m: float) -> Dict[str, Any]:
        """
        Compute Structural Swing Stop-Loss using 5-minute micro pivot lows/highs.
        Replaces arbitrary 1.5x ATR mathematical distance with real market structure.
        """
        min_stop_dist = mtf_config.MIN_STOP_ATR_MULT * atr_15m
        max_stop_dist = mtf_config.MAX_STOP_ATR_MULT * atr_15m

        if not candles_5m or len(candles_5m) < 10:
            risk_dist = 1.5 * atr_15m
            sl = round(entry_price - risk_dist, 2) if signal == "BUY" else round(entry_price + risk_dist, 2)
            return {"stop_loss": sl, "stop_type": "ATR_FALLBACK", "risk_dist": risk_dist}

        recent_5m = candles_5m[-mtf_config.SWING_PIVOT_LOOKBACK:]
        df5 = pd.DataFrame(recent_5m)
        tr1 = df5["high"].astype(float) - df5["low"].astype(float)
        atr_5m = float(tr1.mean()) if not tr1.empty else (entry_price * 0.005)

        if signal == "BUY":
            swing_low = min(float(c.get("low", entry_price)) for c in recent_5m)
            proposed_sl = round(swing_low - (0.2 * atr_5m), 2)
            raw_dist = entry_price - proposed_sl

            if raw_dist < min_stop_dist:
                final_sl = round(entry_price - min_stop_dist, 2)
                stop_type = "MIN_BOUNDED_PIVOT"
            elif raw_dist > max_stop_dist:
                final_sl = round(entry_price - max_stop_dist, 2)
                stop_type = "MAX_BOUNDED_PIVOT"
            else:
                final_sl = proposed_sl
                stop_type = "STRUCTURAL_SWING_LOW"
            risk_dist = entry_price - final_sl
        else:
            swing_high = max(float(c.get("high", entry_price)) for c in recent_5m)
            proposed_sl = round(swing_high + (0.2 * atr_5m), 2)
            raw_dist = proposed_sl - entry_price

            if raw_dist < min_stop_dist:
                final_sl = round(entry_price + min_stop_dist, 2)
                stop_type = "MIN_BOUNDED_PIVOT"
            elif raw_dist > max_stop_dist:
                final_sl = round(entry_price + max_stop_dist, 2)
                stop_type = "MAX_BOUNDED_PIVOT"
            else:
                final_sl = proposed_sl
                stop_type = "STRUCTURAL_SWING_HIGH"
            risk_dist = final_sl - entry_price

        return {
            "stop_loss": final_sl,
            "stop_type": stop_type,
            "risk_dist": max(risk_dist, 1e-4)
        }

    def _classify_entry_state(
        self,
        candles_5m: List[Dict[str, Any]],
        candles_15m: List[Dict[str, Any]],
        signal: str,
        mark_price: float,
        atr: float,
        ema20_15m: float
    ) -> Dict[str, Any]:
        """
        Post-Impulse Displacement & Entry-State Classification Engine.
        Classifies every trade candidate into:
        - STATE_A: Healthy Trend Continuation (Calm pullback, orderly structure) -> APPROVED
        - STATE_B: Genuine Structural Reversal (Impulse -> Base >=3 bars -> Higher Low -> 5M Reclaim) -> APPROVED
        - STATE_C: Impulse Trap (Dead-Cat Bounce / Chasing the Flush Exhaustion) -> HARD VETO
        """
        if signal not in ("BUY", "SELL"):
            return {"state": "STATE_A_HEALTHY_CONTINUATION", "approved": True, "reason": "No active directional signal"}

        if not candles_5m or len(candles_5m) < 6:
            return {"state": "STATE_A_HEALTHY_CONTINUATION", "approved": True, "reason": "Insufficient 5m history for impulse detection"}

        lookback_len = min(12, len(candles_5m))
        lookback_5m = candles_5m[-lookback_len:]

        highs = [float(c["high"]) for c in lookback_5m]
        lows = [float(c["low"]) for c in lookback_5m]
        max_high = max(highs)
        min_low = min(lows)
        idx_high = highs.index(max_high)
        idx_low = lows.index(min_low)
        total_span = max_high - min_low
        total_span_atr = total_span / (atr + 1e-9)

        recent_6 = candles_5m[-6:]
        c_open_6 = float(recent_6[0]["open"])
        c_close_now = float(recent_6[-1]["close"])
        disp_6_atr = (c_close_now - c_open_6) / (atr + 1e-9)

        shock_ranges = [(float(c["high"]) - float(c["low"])) / (atr + 1e-9) for c in recent_6]
        max_shock = max(shock_ranges) if shock_ranges else 0.0

        is_sell_impulse = (total_span_atr >= mtf_config.IMPULSE_DISPLACEMENT_THRESHOLD_ATR and idx_high < idx_low) or (disp_6_atr <= -mtf_config.IMPULSE_DISPLACEMENT_THRESHOLD_ATR) or (max_shock >= mtf_config.IMPULSE_SHOCK_CANDLE_ATR and c_close_now < c_open_6)
        is_buy_impulse = (total_span_atr >= mtf_config.IMPULSE_DISPLACEMENT_THRESHOLD_ATR and idx_low < idx_high) or (disp_6_atr >= mtf_config.IMPULSE_DISPLACEMENT_THRESHOLD_ATR) or (max_shock >= mtf_config.IMPULSE_SHOCK_CANDLE_ATR and c_close_now > c_open_6)

        # 1. Sell Impulse Active (Violent Dump)
        if is_sell_impulse:
            bars_since_trough = (len(lookback_5m) - 1) - idx_low

            if signal == "BUY":
                # Must have at least 3 bars since trough to form base
                base_held = (bars_since_trough >= mtf_config.IMPULSE_BASE_MIN_CANDLES) and (c_close_now > min_low)

                # Check if higher low has formed in the recovery bars
                hl_formed = False
                if bars_since_trough >= 3:
                    trough_bar_low = min_low
                    recovery_lows = lows[idx_low + 1:]
                    if recovery_lows and min(recovery_lows) > trough_bar_low - (0.05 * atr):
                        hl_formed = True

                # Check 5M EMA20 reclaim
                df5 = pd.DataFrame(candles_5m[-20:])
                df5["close"] = df5["close"].astype(float)
                span_len = min(20, len(df5))
                ema20_5m = float(df5["close"].ewm(span=span_len, adjust=False).mean().iloc[-1])
                ema_reclaimed = c_close_now >= ema20_5m

                if base_held and hl_formed and ema_reclaimed:
                    return {
                        "state": "STATE_B_GENUINE_REVERSAL",
                        "approved": True,
                        "reason": f"State B Reversal Confirmed: Base held ({bars_since_trough} bars), higher low formed, 5m EMA20 reclaimed (${ema20_5m:.2f})",
                        "displacement_atr": round(total_span_atr, 2),
                        "max_shock_atr": round(max_shock, 2)
                    }
                else:
                    reasons = []
                    if not base_held: reasons.append(f"base incomplete ({bars_since_trough}/{mtf_config.IMPULSE_BASE_MIN_CANDLES} bars)")
                    if not hl_formed: reasons.append("no higher low")
                    if not ema_reclaimed: reasons.append(f"below 5m EMA20 (${ema20_5m:.2f})")
                    return {
                        "state": "STATE_C_DEAD_CAT_BOUNCE",
                        "approved": False,
                        "reason": f"VETO Dead-Cat Bounce: Post-impulse dump ({total_span_atr:.1f}x ATR). Required structural repair missing: {', '.join(reasons)}.",
                        "displacement_atr": round(total_span_atr, 2),
                        "max_shock_atr": round(max_shock, 2)
                    }

            if signal == "SELL":
                dist_below_ema20 = (ema20_15m - mark_price) / (atr + 1e-9)
                if dist_below_ema20 > mtf_config.EXHAUSTION_CHASE_MAX_ATR:
                    if bars_since_trough <= 1 or abs(mark_price - min_low) < (0.35 * atr):
                        return {
                            "state": "STATE_C_EXHAUSTION_CHASE",
                            "approved": False,
                            "reason": f"VETO Flush Exhaustion: Shorting at bottom of sell impulse ({total_span_atr:.1f}x ATR, {dist_below_ema20:.1f}x ATR below 15m EMA20). Must wait for relief bounce & lower high.",
                            "displacement_atr": round(total_span_atr, 2),
                            "max_shock_atr": round(max_shock, 2)
                        }

        # 2. Buy Impulse Active (Parabolic Surge)
        if is_buy_impulse:
            bars_since_peak = (len(lookback_5m) - 1) - idx_high

            if signal == "SELL":
                base_held = (bars_since_peak >= mtf_config.IMPULSE_BASE_MIN_CANDLES) and (c_close_now < max_high)

                lh_formed = False
                if bars_since_peak >= 3:
                    peak_bar_high = max_high
                    pullback_highs = highs[idx_high + 1:]
                    if pullback_highs and max(pullback_highs) < peak_bar_high + (0.05 * atr):
                        lh_formed = True

                df5 = pd.DataFrame(candles_5m[-20:])
                df5["close"] = df5["close"].astype(float)
                span_len = min(20, len(df5))
                ema20_5m = float(df5["close"].ewm(span=span_len, adjust=False).mean().iloc[-1])
                ema_lost = c_close_now <= ema20_5m

                if base_held and lh_formed and ema_lost:
                    return {
                        "state": "STATE_B_GENUINE_REVERSAL",
                        "approved": True,
                        "reason": f"State B Reversal Confirmed: Top base held ({bars_since_peak} bars), lower high formed, 5m EMA20 lost (${ema20_5m:.2f})",
                        "displacement_atr": round(total_span_atr, 2),
                        "max_shock_atr": round(max_shock, 2)
                    }
                else:
                    reasons = []
                    if not base_held: reasons.append(f"distribution incomplete ({bars_since_peak}/{mtf_config.IMPULSE_BASE_MIN_CANDLES} bars)")
                    if not lh_formed: reasons.append("no lower high")
                    if not ema_lost: reasons.append(f"above 5m EMA20 (${ema20_5m:.2f})")
                    return {
                        "state": "STATE_C_BLOWOFF_TRAP",
                        "approved": False,
                        "reason": f"VETO Parabolic Blow-off Short: Post-impulse surge (+{total_span_atr:.1f}x ATR). Structure breakdown missing: {', '.join(reasons)}.",
                        "displacement_atr": round(total_span_atr, 2),
                        "max_shock_atr": round(max_shock, 2)
                    }

            if signal == "BUY":
                dist_above_ema20 = (mark_price - ema20_15m) / (atr + 1e-9)
                if dist_above_ema20 > mtf_config.EXHAUSTION_CHASE_MAX_ATR:
                    if bars_since_peak <= 1 or abs(max_high - mark_price) < (0.35 * atr):
                        return {
                            "state": "STATE_C_EXHAUSTION_CHASE",
                            "approved": False,
                            "reason": f"VETO Parabolic Exhaustion: Buying at top tick of buy impulse (+{total_span_atr:.1f}x ATR, {dist_above_ema20:.1f}x ATR above 15m EMA20). Must wait for orderly pullback & higher low.",
                            "displacement_atr": round(total_span_atr, 2),
                            "max_shock_atr": round(max_shock, 2)
                        }

        # 3. Calm / Normal Market
        return {
            "state": "STATE_A_HEALTHY_CONTINUATION",
            "approved": True,
            "reason": f"State A Continuation: Orderly volatility (disp: {disp_6_atr:+.1f}x ATR, shock: {max_shock:.1f}x ATR).",
            "displacement_atr": round(disp_6_atr, 2),
            "max_shock_atr": round(max_shock, 2)
        }

    def _analyze_1h_supply_demand_zones(
        self,
        candles_1h: List[Dict[str, Any]],
        signal: str,
        entry_price: float,
        risk_dist: float,
        atr_1h: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        1-Hour Demand & Supply Zone Clearance Engine.
        
        Identifies structural 1H swing highs (Supply) and swing lows (Demand) over recent
        history (lookback up to ZONE_LOOKBACK_BARS). Evaluates zone mitigation across subsequent
        candles:
        - FRESH_UNMITIGATED (0 tests): Maximum resistance/support.
        - TESTED (1-2 tests): Partially absorbed, but still active.
        - DEPLETED (>= 3 tests): Liquidity fully absorbed/exhausted. High breakout probability.
          DEPLETED ZONES NEVER VETO TRADES.
          
        Clearance Evaluation:
        - For BUY: Identifies nearest active overhead Supply Zone.
          If clearance_ratio = (supply_bottom - entry_price) / risk_dist < ZONE_CLEARANCE_MIN_RATIO:
          VETO (choked clearance into overhead resistance wall).
        - For SELL: Identifies nearest active underlying Demand Zone.
          If clearance_ratio = (entry_price - demand_top) / risk_dist < ZONE_CLEARANCE_MIN_RATIO:
          VETO (choked clearance into underlying demand floor).
        """
        if signal not in ("BUY", "SELL"):
            return {
                "approved": True,
                "reason": "No directional signal",
                "opposing_zone": None,
                "clearance_ratio": 999.0
            }

        if not candles_1h or len(candles_1h) < 5:
            return {
                "approved": True,
                "reason": "Insufficient 1h history for zone clearance check",
                "opposing_zone": None,
                "clearance_ratio": 999.0
            }

        # 1. Compute 1H ATR if not supplied
        if atr_1h is None or atr_1h <= 0:
            df_1h = pd.DataFrame(candles_1h)
            df_1h["high"] = df_1h["high"].astype(float)
            df_1h["low"] = df_1h["low"].astype(float)
            df_1h["close"] = df_1h["close"].astype(float)
            tr = np.maximum(
                df_1h["high"] - df_1h["low"],
                np.maximum(
                    abs(df_1h["high"] - df_1h["close"].shift(1)),
                    abs(df_1h["low"] - df_1h["close"].shift(1))
                )
            )
            atr_1h = float(tr.tail(14).mean()) if len(tr) >= 14 else float(tr.mean())
            if np.isnan(atr_1h) or atr_1h <= 0:
                atr_1h = entry_price * 0.008

        # 2. Slice recent lookback window
        lookback = min(len(candles_1h), mtf_config.ZONE_LOOKBACK_BARS)
        recent_1h = candles_1h[-lookback:]
        n = len(recent_1h)

        highs = [float(c["high"]) for c in recent_1h]
        lows = [float(c["low"]) for c in recent_1h]
        opens = [float(c.get("open", c["close"])) for c in recent_1h]
        closes = [float(c["close"]) for c in recent_1h]

        supply_zones = []
        demand_zones = []
        min_disp = mtf_config.ZONE_MIN_DISPLACEMENT_ATR * atr_1h

        # 3. Detect Swing Highs (Supply) and Swing Lows (Demand) with displacement
        # Search from bar 1 to n - 2 for validated pivots
        for i in range(1, n - 1):
            h_i = highs[i]
            l_i = lows[i]

            # Supply Pivot: Local high
            is_swing_high = (h_i >= highs[i - 1]) and (h_i >= highs[i + 1])
            if i >= 2:
                is_swing_high = is_swing_high and (h_i >= highs[i - 2])

            if is_swing_high:
                # Measure subsequent displacement away from pivot high
                subsequent_lows = lows[i + 1:]
                min_subsequent = min(subsequent_lows) if subsequent_lows else h_i
                displacement = h_i - min_subsequent

                if displacement >= min_disp:
                    z_top = h_i
                    z_bottom = max(h_i - (0.5 * atr_1h), min(opens[i], closes[i]))
                    supply_zones.append({
                        "index": i,
                        "type": "SUPPLY",
                        "top": round(z_top, 2),
                        "bottom": round(z_bottom, 2),
                        "displacement": round(displacement, 2),
                        "pivot_time": recent_1h[i].get("time", 0)
                    })

            # Demand Pivot: Local low
            is_swing_low = (l_i <= lows[i - 1]) and (l_i <= lows[i + 1])
            if i >= 2:
                is_swing_low = is_swing_low and (l_i <= lows[i - 2])

            if is_swing_low:
                # Measure subsequent displacement away from pivot low
                subsequent_highs = highs[i + 1:]
                max_subsequent = max(subsequent_highs) if subsequent_highs else l_i
                displacement = max_subsequent - l_i

                if displacement >= min_disp:
                    z_bottom = l_i
                    z_top = min(l_i + (0.5 * atr_1h), max(opens[i], closes[i]))
                    demand_zones.append({
                        "index": i,
                        "type": "DEMAND",
                        "top": round(z_top, 2),
                        "bottom": round(z_bottom, 2),
                        "displacement": round(displacement, 2),
                        "pivot_time": recent_1h[i].get("time", 0)
                    })

        # 4. Count Retests & Determine Mitigation / Depletion for each zone
        for sz in supply_zones:
            idx = sz["index"]
            tests = 0
            invalidated = False
            for j in range(idx + 1, n):
                # Candle j retested if high reached into or above zone bottom
                if highs[j] >= sz["bottom"]:
                    tests += 1
                # If a candle closed strongly above zone top + 0.3 ATR, the zone was breached
                if closes[j] > sz["top"] + (0.3 * atr_1h):
                    invalidated = True
            sz["test_count"] = tests
            sz["invalidated"] = invalidated
            if invalidated:
                sz["status"] = "BREACHED"
            elif tests >= mtf_config.ZONE_MAX_TESTS:
                sz["status"] = "DEPLETED"
            elif tests > 0:
                sz["status"] = "TESTED"
            else:
                sz["status"] = "FRESH_UNMITIGATED"

        for dz in demand_zones:
            idx = dz["index"]
            tests = 0
            invalidated = False
            for j in range(idx + 1, n):
                # Candle j retested if low reached into or below zone top
                if lows[j] <= dz["top"]:
                    tests += 1
                # If a candle closed strongly below zone bottom - 0.3 ATR, the zone was breached
                if closes[j] < dz["bottom"] - (0.3 * atr_1h):
                    invalidated = True
            dz["test_count"] = tests
            dz["invalidated"] = invalidated
            if invalidated:
                dz["status"] = "BREACHED"
            elif tests >= mtf_config.ZONE_MAX_TESTS:
                dz["status"] = "DEPLETED"
            elif tests > 0:
                dz["status"] = "TESTED"
            else:
                dz["status"] = "FRESH_UNMITIGATED"

        # 5. Clearance Check against Opposing Active Zones
        min_clearance = mtf_config.ZONE_CLEARANCE_MIN_RATIO
        effective_risk = max(risk_dist, 1e-4)

        if signal == "BUY":
            # Opposing zone is active overhead SUPPLY (status != DEPLETED and not invalidated)
            overhead_supplies = [
                sz for sz in supply_zones
                if not sz["invalidated"]
                and sz["status"] != "DEPLETED"
                and sz["bottom"] > (entry_price - 0.1 * atr_1h)
            ]
            if not overhead_supplies:
                return {
                    "approved": True,
                    "reason": "1H Zone Clearance Approved: No active overhead 1h supply zone blocking path",
                    "opposing_zone": None,
                    "clearance_ratio": 999.0
                }

            # Nearest overhead supply zone
            nearest_supply = min(overhead_supplies, key=lambda z: z["bottom"])
            avail_reward = max(0.0, nearest_supply["bottom"] - entry_price)
            clearance_ratio = avail_reward / effective_risk

            if clearance_ratio < min_clearance:
                return {
                    "approved": False,
                    "reason": (
                        f"1H Zone Clearance VETO: Overhead 1H Supply Zone [{nearest_supply['bottom']:.2f} - {nearest_supply['top']:.2f}] "
                        f"offers only {clearance_ratio:.2f}R clearance (< {min_clearance:.1f}R required). "
                        f"Status: {nearest_supply['status']} ({nearest_supply['test_count']} tests)."
                    ),
                    "opposing_zone": nearest_supply,
                    "clearance_ratio": round(clearance_ratio, 2)
                }
            else:
                return {
                    "approved": True,
                    "reason": (
                        f"1H Zone Clearance Approved: Overhead 1H Supply Zone at {nearest_supply['bottom']:.2f} "
                        f"offers {clearance_ratio:.2f}R clearance (>= {min_clearance:.1f}R)."
                    ),
                    "opposing_zone": nearest_supply,
                    "clearance_ratio": round(clearance_ratio, 2)
                }

        elif signal == "SELL":
            # Opposing zone is active underlying DEMAND (status != DEPLETED and not invalidated)
            underlying_demands = [
                dz for dz in demand_zones
                if not dz["invalidated"]
                and dz["status"] != "DEPLETED"
                and dz["top"] < (entry_price + 0.1 * atr_1h)
            ]
            if not underlying_demands:
                return {
                    "approved": True,
                    "reason": "1H Zone Clearance Approved: No active underlying 1h demand zone blocking path",
                    "opposing_zone": None,
                    "clearance_ratio": 999.0
                }

            # Nearest underlying demand zone
            nearest_demand = max(underlying_demands, key=lambda z: z["top"])
            avail_reward = max(0.0, entry_price - nearest_demand["top"])
            clearance_ratio = avail_reward / effective_risk

            if clearance_ratio < min_clearance:
                return {
                    "approved": False,
                    "reason": (
                        f"1H Zone Clearance VETO: Underlying 1H Demand Zone [{nearest_demand['bottom']:.2f} - {nearest_demand['top']:.2f}] "
                        f"offers only {clearance_ratio:.2f}R clearance (< {min_clearance:.1f}R required). "
                        f"Status: {nearest_demand['status']} ({nearest_demand['test_count']} tests)."
                    ),
                    "opposing_zone": nearest_demand,
                    "clearance_ratio": round(clearance_ratio, 2)
                }
            else:
                return {
                    "approved": True,
                    "reason": (
                        f"1H Zone Clearance Approved: Underlying 1H Demand Zone at {nearest_demand['top']:.2f} "
                        f"offers {clearance_ratio:.2f}R clearance (>= {min_clearance:.1f}R)."
                    ),
                    "opposing_zone": nearest_demand,
                    "clearance_ratio": round(clearance_ratio, 2)
                }

        return {
            "approved": True,
            "reason": "1H Zone Clearance Approved",
            "opposing_zone": None,
            "clearance_ratio": 999.0
        }

    def _calculate_indicators(self, candles: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Compute Pillar A indicators and Pillar B Qlib-inspired factors."""
        if not candles or len(candles) < 30:
            return {"valid": False}

        df = pd.DataFrame(candles)
        df["close"] = df["close"].astype(float)
        df["high"] = df["high"].astype(float)
        df["low"] = df["low"].astype(float)
        df["open"] = df["open"].astype(float) if "open" in df.columns else df["close"]
        df["volume"] = df["volume"].astype(float)

        # ================= PILLAR A: TRADITIONAL FOUNDATION =================
        # 1. EMAs (20, 50, 200) - full convergence with 250 bars
        df["ema20"] = df["close"].ewm(span=20, adjust=False).mean()
        df["ema50"] = df["close"].ewm(span=50, adjust=False).mean()
        if len(df) >= 200:
            df["ema200"] = df["close"].ewm(span=200, adjust=False).mean()
        else:
            df["ema200"] = df["close"].ewm(span=len(df), adjust=False).mean()

        # 2. RSI (14)
        delta = df["close"].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / (loss + 1e-9)
        df["rsi"] = 100 - (100 / (1 + rs))

        # 3. ATR (14)
        tr1 = df["high"] - df["low"]
        tr2 = (df["high"] - df["close"].shift()).abs()
        tr3 = (df["low"] - df["close"].shift()).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        df["atr"] = tr.rolling(window=14).mean()

        latest = df.iloc[-1]
        current_close = float(latest["close"])
        ema20_val = float(latest["ema20"])
        ema50_val = float(latest["ema50"])
        ema200_val = float(latest["ema200"])
        rsi_val = float(latest["rsi"]) if not np.isnan(latest["rsi"]) else 50.0
        atr_val = float(latest["atr"]) if not np.isnan(latest["atr"]) else (current_close * 0.015)

        trend_alignment = (
            "BULLISH" if current_close > ema20_val > ema50_val
            else "BEARISH" if current_close < ema20_val < ema50_val
            else "CONSOLIDATING"
        )

        # ================= PILLAR B: QLIB-INSPIRED ALPHA FACTORS =================
        # Family 1: Rolling Price-Volume Correlation (CORR_PV)
        pv_corr_10 = df["close"].rolling(10).corr(df["volume"]).fillna(0.0).iloc[-1]
        pv_corr_20 = df["close"].rolling(20).corr(df["volume"]).fillna(0.0).iloc[-1]
        corr_pv = float((pv_corr_10 * 0.6) + (pv_corr_20 * 0.4))
        corr_pv = max(-1.0, min(1.0, corr_pv))

        # Family 2: Candle Morphology (KMID2, KUP, KLOW)
        candle_span = df["high"] - df["low"] + 1e-9
        df["kmid2"] = (df["close"] - df["open"]) / candle_span
        df["kup"] = (df["high"] - df[["open", "close"]].max(axis=1)) / (df["open"] + 1e-9)
        df["klow"] = (df[["open", "close"]].min(axis=1) - df["low"]) / (df["open"] + 1e-9)

        kmid2_val = float(df["kmid2"].iloc[-1])
        kup_val = float(df["kup"].iloc[-1])
        klow_val = float(df["klow"].iloc[-1])

        # Family 3: Multi-Horizon Momentum with Exponential Decay (ROC 5/10/20/30)
        roc5 = float((current_close - df["close"].iloc[-6]) / df["close"].iloc[-6]) if len(df) > 5 else 0.0
        roc10 = float((current_close - df["close"].iloc[-11]) / df["close"].iloc[-11]) if len(df) > 10 else 0.0
        roc20 = float((current_close - df["close"].iloc[-21]) / df["close"].iloc[-21]) if len(df) > 20 else 0.0
        roc30 = float((current_close - df["close"].iloc[-31]) / df["close"].iloc[-31]) if len(df) > 30 else 0.0

        roc_composite = float((0.40 * roc5) + (0.30 * roc10) + (0.20 * roc20) + (0.10 * roc30))

        # Family 4: Volatility Compression Ratio (std10 / std30)
        std10 = df["close"].rolling(10).std().iloc[-1]
        std30 = df["close"].rolling(30).std().iloc[-1]
        std_norm = float(std10 / (std30 + 1e-9)) if not np.isnan(std10) and not np.isnan(std30) else 1.0

        # Family 5: Moving Average Divergence
        ma_ratio_20 = float((current_close - ema20_val) / (ema20_val + 1e-9))

        return {
            "valid": True,
            "current_close": current_close,
            "ema20": round(ema20_val, 2),
            "ema50": round(ema50_val, 2),
            "ema200": round(ema200_val, 2),
            "rsi": round(rsi_val, 2),
            "atr": round(atr_val, 3),
            "trend_alignment": trend_alignment,
            "factors": {
                "corr_pv": round(corr_pv, 3),
                "kmid2": round(kmid2_val, 3),
                "kup": round(kup_val, 4),
                "klow": round(klow_val, 4),
                "roc_composite": round(roc_composite, 4),
                "roc5": round(roc5, 4),
                "roc10": round(roc10, 4),
                "roc20": round(roc20, 4),
                "roc30": round(roc30, 4),
                "std_norm": round(std_norm, 3),
                "ma_ratio_20": round(ma_ratio_20, 4)
            }
        }

    def _compute_composite_alpha(self, indicators: Dict[str, Any], imbalance: float) -> tuple[float, float, Dict[str, float]]:
        """Synthesize Pillar A and Pillar B into a single Composite Alpha score."""
        factors = indicators.get("factors", {})

        # 1. Momentum alpha component
        roc_comp = factors.get("roc_composite", 0.0)
        alpha_mom = float(np.tanh(roc_comp * 35.0))

        # 2. Volume-Price correlation alpha component
        corr_pv = factors.get("corr_pv", 0.0)
        alpha_pv = float(corr_pv)

        # 3. Candle Morphology alpha component
        kmid2 = factors.get("kmid2", 0.0)
        kup = factors.get("kup", 0.0)
        klow = factors.get("klow", 0.0)
        morphology_signal = kmid2 + (klow * 0.5) - (kup * 0.5)
        alpha_morph = float(np.clip(morphology_signal, -1.0, 1.0))

        # 4. Trend alignment alpha component
        trend_str = indicators.get("trend_alignment", "CONSOLIDATING")
        ma_ratio_20 = factors.get("ma_ratio_20", 0.0)
        base_trend = 0.6 if trend_str == "BULLISH" else (-0.6 if trend_str == "BEARISH" else 0.0)
        alpha_trend = float(np.clip(base_trend + (ma_ratio_20 * 10.0), -1.0, 1.0))

        # 5. Orderbook flow alpha component
        alpha_ob = float(np.clip(imbalance * 1.5, -1.0, 1.0))

        # Weighted Linear Combination
        composite_alpha = (
            (quant_config.WEIGHT_MOMENTUM * alpha_mom) +
            (quant_config.WEIGHT_PV_CORR * alpha_pv) +
            (quant_config.WEIGHT_MORPHOLOGY * alpha_morph) +
            (quant_config.WEIGHT_TREND_ALIGNMENT * alpha_trend) +
            (quant_config.WEIGHT_ORDERBOOK * alpha_ob)
        )
        composite_alpha = float(np.clip(composite_alpha, -1.0, 1.0))

        # Quant Confidence Score (heuristic strength indicator)
        quant_confidence = float(min(0.95, max(0.50, 0.60 + (0.35 * abs(composite_alpha)))))

        contributions = {
            "momentum": round(alpha_mom * quant_config.WEIGHT_MOMENTUM, 3),
            "pv_corr": round(alpha_pv * quant_config.WEIGHT_PV_CORR, 3),
            "morphology": round(alpha_morph * quant_config.WEIGHT_MORPHOLOGY, 3),
            "trend": round(alpha_trend * quant_config.WEIGHT_TREND_ALIGNMENT, 3),
            "orderbook": round(alpha_ob * quant_config.WEIGHT_ORDERBOOK, 3)
        }

        return round(composite_alpha, 3), round(quant_confidence, 3), contributions

    def analyze(self, symbol: str) -> Dict[str, Any]:
        """Perform Triple-Horizon Multi-Timeframe quantitative evaluation."""
        # 1. Fetch live orderbook depth
        ob = delta_client.get_l2_orderbook(symbol)
        ticker = delta_client.get_ticker(symbol)
        mark_price = ticker.get("mark_price", 0.0)

        if mark_price <= 0:
            return {
                "symbol": symbol,
                "signal": "NEUTRAL",
                "reason": "Invalid or zero mark price on exchange",
                "gate_passed": False
            }

        # Fail-closed check: bid/ask spread
        spread_pct = ob.get("spread_pct", 0.0)
        if spread_pct > 0.25:
            return {
                "symbol": symbol,
                "signal": "NEUTRAL",
                "reason": f"Fail-closed: Spread too wide ({spread_pct:.3f}%)",
                "gate_passed": False
            }

        # 2. Fetch Multi-Timeframe Candles (1h, 15m, 5m)
        mtf_data = delta_client.get_multitimeframe_candles(symbol)
        candles_1h = mtf_data.get("1h", [])
        candles_15m = mtf_data.get("15m", [])
        candles_5m = mtf_data.get("5m", [])

        # Fallback if MTF fetch returns empty
        if not candles_15m:
            candles_15m = delta_client.get_candles(symbol, resolution="15m", count=250)

        # Horizon 1: 1-Hour Macro Regime Analysis
        macro = self._analyze_1h_macro(candles_1h)
        macro_trend = macro.get("macro_trend", "NEUTRAL")

        # Horizon 2: 15-Minute Structural Wave & Alphas
        ind = self._calculate_indicators(candles_15m)
        imbalance = ob.get("imbalance", 0.0)

        if not ind.get("valid"):
            atr = mark_price * 0.018
            rsi = 54.0
            trend = "BULLISH" if imbalance > 0.1 else "CONSOLIDATING"
            composite_alpha = round(imbalance * 0.5, 3)
            quant_confidence = 0.55
            contributions = {"orderbook": round(imbalance * 0.5, 3)}
            factors = {"corr_pv": 0.0, "kmid2": 0.0, "roc_composite": 0.0, "std_norm": 1.0}
            dist_ema20 = 0.0
        else:
            atr = ind["atr"]
            rsi = ind["rsi"]
            trend = ind["trend_alignment"]
            factors = ind["factors"]
            composite_alpha, quant_confidence, contributions = self._compute_composite_alpha(ind, imbalance)
            
            # Value Pullback Guardrail: check distance to 15m EMA20
            ema20_val = ind["ema20"]
            dist_ema20 = abs(ind["current_close"] - ema20_val) / (atr + 1e-9)
            if dist_ema20 > mtf_config.PULLBACK_MAX_DIST_ATR:
                # Penalize alpha for chasing extended candles
                composite_alpha = round(composite_alpha * 0.75, 3)

        # 3. Multi-Factor & MTF Gate Decision Logic with Dynamic Session Awareness
        session_info = session_momentum_engine.get_current_session()
        active_alpha_threshold = session_momentum_engine.get_alpha_threshold(symbol)

        signal = "NEUTRAL"
        reason = f"Composite alpha ({composite_alpha:+.2f}) within neutral band [-{active_alpha_threshold:.2f}, +{active_alpha_threshold:.2f}] (Session: {session_info['session_name']})"

        # Signal Generation based on Dynamic Session Alpha
        if composite_alpha >= active_alpha_threshold:
            signal = "BUY"
        elif composite_alpha <= -active_alpha_threshold:
            signal = "SELL"

        # ================= TRIPLE-HORIZON MACRO VETO ENFORCEMENT =================
        if signal == "BUY" and macro_trend == "BEARISH":
            signal = "NEUTRAL"
            reason = f"MTF Macro VETO: 1-Hour Trend is BEARISH (Close < EMA50/200). Longs prohibited to prevent counter-trend traps."
        elif signal == "SELL" and macro_trend == "BULLISH":
            signal = "NEUTRAL"
            reason = f"MTF Macro VETO: 1-Hour Trend is BULLISH (Close > EMA50/200). Shorts prohibited to prevent counter-trend traps."
        elif signal == "BUY":
            reason = (
                f"MTF Bullish Confluence | 1H: {macro_trend} | Alpha: {composite_alpha:+.2f} >= {active_alpha_threshold:.2f} | "
                f"Session: {session_info['session_name']} | Mom: {contributions['momentum']:+.2f}, PV_Corr: {factors['corr_pv']:+.2f}, RSI: {rsi:.1f}"
            )
        elif signal == "SELL":
            reason = (
                f"MTF Bearish Confluence | 1H: {macro_trend} | Alpha: {composite_alpha:+.2f} <= -{active_alpha_threshold:.2f} | "
                f"Session: {session_info['session_name']} | Mom: {contributions['momentum']:+.2f}, PV_Corr: {factors['corr_pv']:+.2f}, RSI: {rsi:.1f}"
            )

        # ================= POST-IMPULSE ENTRY-STATE CLASSIFICATION =================
        ema20_15m_val = ind["ema20"] if ind.get("valid") else mark_price
        entry_state = self._classify_entry_state(
            candles_5m=candles_5m,
            candles_15m=candles_15m,
            signal=signal,
            mark_price=mark_price,
            atr=atr,
            ema20_15m=ema20_15m_val
        )

        if signal in ("BUY", "SELL") and not entry_state["approved"]:
            signal = "NEUTRAL"
            reason = f"Entry-State VETO [{entry_state['state']}]: {entry_state['reason']}"

        # 4. Horizon 3: Structural Swing Stop & Precision Targets
        entry_price = mark_price
        stop_type = "NONE"

        if signal in ("BUY", "SELL"):
            stop_info = self._find_5m_structural_stop(candles_5m, signal, entry_price, atr)
            stop_loss = stop_info["stop_loss"]
            risk_dist = stop_info["risk_dist"]
            stop_type = stop_info["stop_type"]

            # Check if fine-tuned quant parameters exist
            tp1_mult = 1.0
            tp2_mult = 2.2
            scale_pct = 0.70
            min_tp_pct = 0.0018
            best_params_path = os.path.join(os.path.dirname(__file__), "..", "data", "best_quant_params.json")
            if os.path.exists(best_params_path):
                try:
                    with open(best_params_path, "r", encoding="utf-8") as f:
                        opt = json.load(f)
                        p = opt.get("parameters", {})
                        tp1_mult = p.get("tp1_mult", tp1_mult)
                        tp2_mult = p.get("tp2_mult", tp2_mult)
                        scale_pct = p.get("scale_pct", scale_pct)
                        min_tp_pct = p.get("min_tp_pct", min_tp_pct)
                except Exception:
                    pass

            target_tp1_dist = max(tp1_mult * risk_dist, entry_price * min_tp_pct)
            if signal == "BUY":
                take_profit_1 = round(entry_price + target_tp1_dist, 2)
                take_profit_2 = round(entry_price + (tp2_mult * risk_dist), 2)
                # Blended R:R with scale-out at TP1 and runner at TP2
                rr_ratio = round((scale_pct * (target_tp1_dist / risk_dist)) + ((1.0 - scale_pct) * tp2_mult), 2)
            else:
                take_profit_1 = round(entry_price - target_tp1_dist, 2)
                take_profit_2 = round(entry_price - (tp2_mult * risk_dist), 2)
                rr_ratio = round((scale_pct * (target_tp1_dist / risk_dist)) + ((1.0 - scale_pct) * tp2_mult), 2)
        else:
            stop_loss = 0.0
            take_profit_1 = 0.0
            take_profit_2 = 0.0
            rr_ratio = 0.0

        # ================= 1-HOUR SUPPLY & DEMAND ZONE CLEARANCE =================
        zone_clearance = {
            "approved": True,
            "reason": "No active directional signal",
            "opposing_zone": None,
            "clearance_ratio": 999.0
        }
        if signal in ("BUY", "SELL"):
            zone_clearance = self._analyze_1h_supply_demand_zones(
                candles_1h=candles_1h,
                signal=signal,
                entry_price=entry_price,
                risk_dist=risk_dist,
                atr_1h=None
            )
            if not zone_clearance["approved"]:
                signal = "NEUTRAL"
                reason = zone_clearance["reason"]
                stop_loss = 0.0
                take_profit_1 = 0.0
                take_profit_2 = 0.0
                rr_ratio = 0.0

        gate_passed = (signal in ("BUY", "SELL")) and (rr_ratio >= self.min_rr) and (quant_confidence >= 0.70)

        # 5. Microstructure Engine Analysis (Stoikov Micro-Price & 5-Level Depth Imbalance)
        micro_data = microstructure_engine.analyze_orderbook(ob, signal=signal)

        return {
            "symbol": symbol,
            "signal": signal,
            "entry_price": entry_price,
            "stop_loss": stop_loss,
            "take_profit_1": take_profit_1,
            "take_profit_2": take_profit_2,
            "reward_to_risk": rr_ratio,
            "confidence": quant_confidence,
            "composite_alpha": composite_alpha,
            "alpha_threshold": active_alpha_threshold,
            "session": session_info,
            "entry_state": entry_state,
            "zone_clearance": zone_clearance,
            "micro_status": micro_data.get("status", "NEUTRAL"),
            "micro_drift": micro_data.get("micro_drift", 0.0),
            "micro_price": micro_data.get("micro_price", mark_price),
            "depth_imbalance_5": micro_data.get("depth_imbalance_5", 0.0),
            "indicators": {
                "rsi": round(rsi, 2),
                "atr": round(atr, 3),
                "trend": trend,
                "orderbook_imbalance": round(imbalance, 3),
                "spread_pct": round(spread_pct, 4),
                "qlib_factors": factors,
                "alpha_contributions": contributions,
                "microstructure": micro_data,
                "mtf": {
                    "macro_trend_1h": macro_trend,
                    "macro_ema50_1h": macro.get("ema50_1h", 0.0),
                    "macro_ema200_1h": macro.get("ema200_1h", 0.0),
                    "pullback_dist_atr": round(dist_ema20, 2),
                    "stop_type": stop_type,
                    "entry_state": entry_state["state"],
                    "entry_state_reason": entry_state["reason"],
                    "displacement_atr": entry_state.get("displacement_atr", 0.0),
                    "max_shock_atr": entry_state.get("max_shock_atr", 0.0),
                    "zone_clearance": {
                        "approved": zone_clearance["approved"],
                        "clearance_ratio": zone_clearance["clearance_ratio"],
                        "reason": zone_clearance["reason"],
                        "opposing_zone": zone_clearance.get("opposing_zone")
                    }
                }
            },
            "reason": reason,
            "gate_passed": gate_passed
        }

quant_analyst_agent = QuantAnalystAgent()
