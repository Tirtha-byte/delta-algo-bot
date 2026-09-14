import numpy as np
import pandas as pd
from typing import Dict, Any, List, Optional, Tuple
from enum import Enum


class MarketRegime(str, Enum):
    TREND_UP = "TREND_UP"
    TREND_DOWN = "TREND_DOWN"
    RANGE = "RANGE"
    BREAKOUT = "BREAKOUT"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    LOW_VOLATILITY = "LOW_VOLATILITY"
    EXHAUSTION = "EXHAUSTION"
    EVENT_SHOCK = "EVENT_SHOCK"
    UNCERTAIN = "UNCERTAIN"

class RegimeEngine:
    """
    BEAST v2 Quantitative Market Regime Classification Engine.
    Evaluates:
    - 1H Macro EMA 50/200 & 15M EMA 20/50/200 alignment and slopes
    - ADX(14), +DI, -DI directional strength
    - Realized Volatility & ATR Percentiles
    - Range Structure & Expansion
    - Flow confirmation & Shock Detection
    """
    def __init__(self):
        pass

    def calculate_adx(self, df: pd.DataFrame, period: int = 14) -> Tuple[float, float, float]:
        """
        Calculates Welles Wilder's ADX, +DI, and -DI.
        Returns (adx, plus_di, minus_di).
        """
        if len(df) < period + 2:
            return 20.0, 20.0, 20.0

        high = df["high"].astype(float).values
        low = df["low"].astype(float).values
        close = df["close"].astype(float).values

        tr_list = []
        plus_dm_list = []
        minus_dm_list = []

        for i in range(1, len(df)):
            h_diff = high[i] - high[i - 1]
            l_diff = low[i - 1] - low[i]

            plus_dm = h_diff if (h_diff > l_diff and h_diff > 0) else 0.0
            minus_dm = l_diff if (l_diff > h_diff and l_diff > 0) else 0.0

            tr = max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))

            tr_list.append(tr)
            plus_dm_list.append(plus_dm)
            minus_dm_list.append(minus_dm)

        if len(tr_list) < period:
            return 20.0, 20.0, 20.0

        # Smoothed sums
        tr_smooth = np.convolve(tr_list, np.ones(period)/period, mode='valid')
        pdm_smooth = np.convolve(plus_dm_list, np.ones(period)/period, mode='valid')
        mdm_smooth = np.convolve(minus_dm_list, np.ones(period)/period, mode='valid')

        if len(tr_smooth) == 0 or tr_smooth[-1] <= 0:
            return 20.0, 20.0, 20.0

        pdi = 100.0 * (pdm_smooth / tr_smooth)
        mdi = 100.0 * (mdm_smooth / tr_smooth)

        dx = 100.0 * (np.abs(pdi - mdi) / (pdi + mdi + 1e-9))
        adx_series = np.convolve(dx, np.ones(period)/period, mode='valid')

        final_adx = float(adx_series[-1]) if len(adx_series) > 0 else float(dx[-1])
        return round(final_adx, 2), round(float(pdi[-1]), 2), round(float(mdi[-1]), 2)

    def calculate_technicals(self, candles: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Vectorized technical features across candles."""
        if not candles or len(candles) < 20:
            return {"valid": False}

        df = pd.DataFrame(candles)
        for col in ("open", "high", "low", "close", "volume"):
            if col in df.columns:
                df[col] = df[col].astype(float)

        close = df["close"]
        high = df["high"]
        low = df["low"]

        # EMAs
        ema20 = close.ewm(span=20, adjust=False).mean()
        ema50 = close.ewm(span=50, adjust=False).mean()
        span_200 = 200 if len(close) >= 200 else len(close)
        ema200 = close.ewm(span=span_200, adjust=False).mean()

        # Slopes over 5 bars
        slope_ema20 = (float(ema20.iloc[-1]) - float(ema20.iloc[-5])) / float(ema20.iloc[-5]) if len(ema20) >= 5 else 0.0
        slope_ema50 = (float(ema50.iloc[-1]) - float(ema50.iloc[-5])) / float(ema50.iloc[-5]) if len(ema50) >= 5 else 0.0

        # ATR 14
        tr1 = high - low
        tr2 = (high - close.shift(1)).abs()
        tr3 = (low - close.shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr_series = tr.rolling(14).mean()
        atr = float(atr_series.iloc[-1]) if not atr_series.empty and not pd.isna(atr_series.iloc[-1]) else (float(close.iloc[-1]) * 0.015)

        # Historical ATR percentile (over last 50 bars)
        atr_hist = atr_series.dropna().tail(50)
        atr_percentile = float((atr_hist <= atr).mean() * 100.0) if len(atr_hist) >= 10 and float(atr_hist.std()) > 1e-4 else 50.0


        # Realized Volatility (std dev of log returns, annualized)
        log_ret = np.log(close / close.shift(1)).dropna()
        realized_vol = float(log_ret.tail(30).std() * np.sqrt(365 * 24 * 4)) if len(log_ret) >= 10 else 0.40

        # ADX
        adx, plus_di, minus_di = self.calculate_adx(df, period=14)

        current_close = float(close.iloc[-1])
        c_ema20 = float(ema20.iloc[-1])
        c_ema50 = float(ema50.iloc[-1])
        c_ema200 = float(ema200.iloc[-1])

        # Distance from EMA20 in ATR
        dist_ema20_atr = abs(current_close - c_ema20) / (atr + 1e-9)

        # Range structure: highest high vs lowest low in last 20 bars
        recent_highs = high.tail(20)
        recent_lows = low.tail(20)
        range_height = float(recent_highs.max() - recent_lows.min())
        range_height_atr = range_height / (atr + 1e-9)

        # Single-bar shock
        last_bar_range = float(high.iloc[-1] - low.iloc[-1])
        shock_ratio = last_bar_range / (atr + 1e-9)

        return {
            "valid": True,
            "current_close": current_close,
            "ema20": round(c_ema20, 2),
            "ema50": round(c_ema50, 2),
            "ema200": round(c_ema200, 2),
            "slope_ema20": round(slope_ema20, 5),
            "slope_ema50": round(slope_ema50, 5),
            "atr": round(atr, 2),
            "atr_percentile": round(atr_percentile, 1),
            "realized_vol": round(realized_vol, 3),
            "adx": round(adx, 1),
            "plus_di": round(plus_di, 1),
            "minus_di": round(minus_di, 1),
            "dist_ema20_atr": round(dist_ema20_atr, 2),
            "range_height_atr": round(range_height_atr, 2),
            "shock_ratio": round(shock_ratio, 2)
        }

    def evaluate(self, candles: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Convenience wrapper returning flat dictionary with regime, atr, adx, and volatility_state.
        """
        res = self.classify(candles)
        metrics = res.get("metrics", {})
        regime_val = res["regime"].value if hasattr(res["regime"], "value") else str(res["regime"])
        return {
            "regime": regime_val,
            "confidence": res.get("confidence", 0.5),
            "reason": res.get("reason", ""),
            "atr": metrics.get("atr", 10.0),
            "adx": metrics.get("adx", 20.0),
            "volatility_state": "HIGH_VOLATILITY" if regime_val == "HIGH_VOLATILITY" else "NORMAL_VOLATILITY",
            "metrics": metrics
        }

    def classify(self, candles_15m: List[Dict[str, Any]],
                 candles_1h: Optional[List[Dict[str, Any]]] = None,
                 flow_info: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Classifies current regime into one of the 9 canonical states:
        TREND_UP, TREND_DOWN, RANGE, BREAKOUT, HIGH_VOLATILITY, LOW_VOLATILITY,
        EXHAUSTION, EVENT_SHOCK, UNCERTAIN
        """
        tech = self.calculate_technicals(candles_15m)
        if not tech.get("valid"):
            return {
                "regime": MarketRegime.UNCERTAIN,
                "confidence": 0.50,
                "reason": "Insufficient candle history for regime classification",
                "metrics": {}
            }

        c = tech["current_close"]
        ema20 = tech["ema20"]
        ema50 = tech["ema50"]
        ema200 = tech["ema200"]
        s20 = tech["slope_ema20"]
        s50 = tech["slope_ema50"]
        adx = tech["adx"]
        pdi = tech["plus_di"]
        mdi = tech["minus_di"]
        dist_atr = tech["dist_ema20_atr"]
        atr_pct = tech["atr_percentile"]
        shock_ratio = tech["shock_ratio"]
        range_atr = tech["range_height_atr"]

        # 1. EVENT SHOCK
        if shock_ratio >= 2.5:
            return {
                "regime": MarketRegime.EVENT_SHOCK,
                "confidence": 0.95,
                "reason": f"Violent price displacement shock ({shock_ratio:.1f}x ATR single bar)",
                "metrics": tech
            }

        # 2. EXHAUSTION (Overextended trend losing steam)
        if dist_atr >= 2.2 and (c > ema20 and s20 > 0.002 or c < ema20 and s20 < -0.002):
            return {
                "regime": MarketRegime.EXHAUSTION,
                "confidence": 0.85,
                "reason": f"Extreme price extension from EMA20 ({dist_atr:.1f}x ATR). Vulnerable to mean reversion or snap back.",
                "metrics": tech
            }

        # 3. HIGH VOLATILITY / LIQUIDITY DISLOCATION
        if atr_pct >= 88.0:
            return {
                "regime": MarketRegime.HIGH_VOLATILITY,
                "confidence": 0.85,
                "reason": f"ATR in top 88th percentile ({atr_pct:.0f}%). Wide swings.",
                "metrics": tech
            }

        # 4. BREAKOUT REGIME
        # Compression followed by directional expansion
        if range_atr < 2.5 and adx < 18 and dist_atr < 0.6:
            # Squeezing range
            return {
                "regime": MarketRegime.LOW_VOLATILITY,
                "confidence": 0.80,
                "reason": f"Volatility compression (Range height: {range_atr:.1f}x ATR, ADX: {adx:.1f}). Coiling for breakout.",
                "metrics": tech
            }

        # 5. TREND UP
        if c > ema20 and ema20 >= ema50 and s20 > 0.0005 and pdi > mdi and adx >= 20.0:
            # Verify macro alignment if 1h candles supplied
            macro_aligned = True
            if candles_1h and len(candles_1h) >= 20:
                macro_tech = self.calculate_technicals(candles_1h)
                if macro_tech.get("valid") and macro_tech["current_close"] < macro_tech["ema50"]:
                    macro_aligned = False

            confidence = 0.90 if (macro_aligned and s50 > 0) else 0.75
            return {
                "regime": MarketRegime.TREND_UP,
                "confidence": confidence,
                "reason": f"Bullish trend ribbon (Close > EMA20 > EMA50, ADX: {adx:.1f}, +DI: {pdi:.1f} > -DI: {mdi:.1f})",
                "metrics": tech
            }

        # 6. TREND DOWN
        if c < ema20 and ema20 <= ema50 and s20 < -0.0005 and mdi > pdi and adx >= 20.0:
            macro_aligned = True
            if candles_1h and len(candles_1h) >= 20:
                macro_tech = self.calculate_technicals(candles_1h)
                if macro_tech.get("valid") and macro_tech["current_close"] > macro_tech["ema50"]:
                    macro_aligned = False

            confidence = 0.90 if (macro_aligned and s50 < 0) else 0.75
            return {
                "regime": MarketRegime.TREND_DOWN,
                "confidence": confidence,
                "reason": f"Bearish trend ribbon (Close < EMA20 < EMA50, ADX: {adx:.1f}, -DI: {mdi:.1f} > +DI: {pdi:.1f})",
                "metrics": tech
            }

        # 7. RANGE / MEAN-REVERTING
        if adx < 20.0 or abs(s20) < 0.0004:
            return {
                "regime": MarketRegime.RANGE,
                "confidence": 0.80,
                "reason": f"Sideways range consolidation (ADX: {adx:.1f} < 20, flat EMA slope: {s20:+.5f})",
                "metrics": tech
            }

        # 8. BREAKOUT ATTEMPT
        if (c > ema20 and s20 > 0.001) or (c < ema20 and s20 < -0.001):
            return {
                "regime": MarketRegime.BREAKOUT,
                "confidence": 0.70,
                "reason": f"Momentum breakout expansion underway (Slope: {s20:+.4f})",
                "metrics": tech
            }

        return {
            "regime": MarketRegime.UNCERTAIN,
            "confidence": 0.50,
            "reason": "Indeterminate mixed signals across ribbon and volatility",
            "metrics": tech
        }

regime_engine = RegimeEngine()
