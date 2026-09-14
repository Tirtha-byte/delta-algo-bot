"""
BEAST v2 - Quality Scoring Engine
agents/quality_engine.py

Calculates comprehensive multidimensional trade quality score (0-100) across 9 dimensions:
1. REGIME: Market regime compatibility, trend strength, volatility state
2. STRUCTURE: Technical structure, EMA alignment, support/resistance positioning
3. SETUP: Setup family score (from SetupEngine)
4. MOMENTUM: Multi-timeframe momentum alignment (RSI, MACD, session momentum)
5. ORDER_FLOW: Delta microstructure, CVD divergence/slope, book imbalance, absorption
6. LIQUIDITY: Spread hygiene, depth percentiles, lack of order book vacuums
7. TRIGGER: Microstructure trigger confirmation (TriggerEngine)
8. CATALYST: News/social sentiment alignment, absence of adverse vetoes
9. RISK_QUALITY: Reward-to-Risk ratio, proximity to invalidation vs target

Tiers:
- A+ (>= 90): Exceptional conviction, full capital allocation
- A  (80 - 89): High conviction, standard allocation
- B  (70 - 79): Moderate conviction, conservative sizing
- C  (60 - 69): Watchlist only, no auto-execution
- NO_TRADE (< 60): Hard rejection
"""

from typing import Dict, Any, Optional
from dataclasses import dataclass, field
import logging

logger = logging.getLogger("beast_v2.quality_engine")


@dataclass
class QualityWeights:
    regime: float = 0.12
    structure: float = 0.12
    setup: float = 0.18
    momentum: float = 0.10
    order_flow: float = 0.16
    liquidity: float = 0.08
    trigger: float = 0.12
    catalyst: float = 0.04
    risk_quality: float = 0.08

    def validate(self):
        total = sum([
            self.regime, self.structure, self.setup, self.momentum,
            self.order_flow, self.liquidity, self.trigger, self.catalyst,
            self.risk_quality
        ])
        if abs(total - 1.0) > 1e-4:
            raise ValueError(f"Quality weights must sum to 1.0, got {total:.4f}")


@dataclass
class QualityScoreResult:
    total_score: float
    tier: str  # "A+", "A", "B", "C", "NO_TRADE"
    is_executable: bool  # True for A+, A, B
    direction: str  # "BUY", "SELL", "NONE"
    category_scores: Dict[str, float] = field(default_factory=dict)
    summary: str = ""
    rejection_reasons: list = field(default_factory=list)


class QualityScoringEngine:
    def __init__(self, weights: Optional[QualityWeights] = None):
        self.weights = weights or QualityWeights()
        self.weights.validate()

    def evaluate(
        self,
        direction: str,  # "BUY" or "SELL"
        regime_result: Dict[str, Any],
        setup_result: Dict[str, Any],
        trigger_result: Dict[str, Any],
        orderflow_result: Dict[str, Any],
        microstructure_result: Dict[str, Any],
        news_result: Optional[Dict[str, Any]] = None,
        technical_data: Optional[Dict[str, Any]] = None,
        risk_metrics: Optional[Dict[str, Any]] = None
    ) -> QualityScoreResult:
        """
        Evaluate full trade quality across 9 categories and output a deterministic 0-100 score.
        """
        rejection_reasons = []
        is_buy = (direction.upper() == "BUY")
        is_sell = (direction.upper() == "SELL")

        if not is_buy and not is_sell:
            return QualityScoreResult(
                total_score=0.0,
                tier="NO_TRADE",
                is_executable=False,
                direction="NONE",
                summary="Invalid direction specified",
                rejection_reasons=["Direction must be BUY or SELL"]
            )

        # 1. REGIME SCORE (0 - 100)
        regime = regime_result.get("regime", "UNCERTAIN")
        adx = float(regime_result.get("adx", 20.0))
        vol_state = regime_result.get("volatility_state", "NORMAL_VOLATILITY")
        regime_score = 50.0

        if is_buy:
            if regime == "TREND_UP":
                regime_score = 90.0 if adx > 25 else 75.0
            elif regime == "BREAKOUT":
                regime_score = 85.0
            elif regime == "RANGE":
                regime_score = 65.0
            elif regime == "TREND_DOWN":
                regime_score = 25.0
                rejection_reasons.append(f"Regime {regime} conflicts with BUY")
            elif regime in ("HIGH_VOLATILITY", "EVENT_SHOCK"):
                regime_score = 40.0
            else:
                regime_score = 50.0
        else:  # is_sell
            if regime == "TREND_DOWN":
                regime_score = 90.0 if adx > 25 else 75.0
            elif regime == "BREAKOUT":
                regime_score = 80.0
            elif regime == "RANGE":
                regime_score = 65.0
            elif regime == "TREND_UP":
                regime_score = 25.0
                rejection_reasons.append(f"Regime {regime} conflicts with SELL")
            elif regime in ("HIGH_VOLATILITY", "EVENT_SHOCK"):
                regime_score = 40.0
            else:
                regime_score = 50.0

        # Adjust for extreme volatility shock
        if vol_state == "HIGH_VOLATILITY":
            regime_score = min(regime_score, 60.0)

        # 2. STRUCTURE SCORE (0 - 100)
        structure_score = 50.0
        tech = technical_data or {}
        ema9 = tech.get("ema9", 0.0)
        ema21 = tech.get("ema21", 0.0)
        ema50 = tech.get("ema50", 0.0)
        price = tech.get("close", 0.0)

        if ema9 and ema21 and ema50 and price:
            if is_buy:
                if price > ema9 > ema21 > ema50:
                    structure_score = 95.0
                elif price > ema21 > ema50:
                    structure_score = 80.0
                elif price > ema50:
                    structure_score = 65.0
                else:
                    structure_score = 35.0
            else:
                if price < ema9 < ema21 < ema50:
                    structure_score = 95.0
                elif price < ema21 < ema50:
                    structure_score = 80.0
                elif price < ema50:
                    structure_score = 65.0
                else:
                    structure_score = 35.0
        else:
            structure_score = 60.0

        # 3. SETUP SCORE (0 - 100)
        if is_buy:
            setup_score = float(setup_result.get("long_setup_score", 0.0))
        else:
            setup_score = float(setup_result.get("short_setup_score", 0.0))

        if setup_score < 50:
            rejection_reasons.append(f"Setup score ({setup_score:.1f}) below minimum 50")

        # 4. MOMENTUM SCORE (0 - 100)
        rsi = float(tech.get("rsi", 50.0))
        macd_hist = float(tech.get("macd_hist", 0.0))
        momentum_score = 50.0

        if is_buy:
            # Healthy bullish momentum: RSI between 45 and 70, positive MACD histogram
            if 48 <= rsi <= 68 and macd_hist > 0:
                momentum_score = 90.0
            elif 40 <= rsi <= 75:
                momentum_score = 70.0
            elif rsi > 80:
                momentum_score = 45.0  # Overbought exhaustion risk
            elif rsi < 35 and macd_hist < 0:
                momentum_score = 30.0
        else:
            # Healthy bearish momentum: RSI between 30 and 52, negative MACD histogram
            if 32 <= rsi <= 52 and macd_hist < 0:
                momentum_score = 90.0
            elif 25 <= rsi <= 60:
                momentum_score = 70.0
            elif rsi < 20:
                momentum_score = 45.0  # Oversold exhaustion risk
            elif rsi > 65 and macd_hist > 0:
                momentum_score = 30.0

        # 5. ORDER FLOW SCORE (0 - 100)
        order_flow_score = 50.0
        cvd_slope = float(orderflow_result.get("cvd_slope", 0.0))
        imbalance = float(microstructure_result.get("weighted_imbalance", 0.0))
        absorption = orderflow_result.get("absorption", "NONE")
        cvd_div = orderflow_result.get("cvd_divergence", "NONE")

        if is_buy:
            of_points = 50.0
            if cvd_slope > 0: of_points += 20.0
            if imbalance > 0.10: of_points += 15.0
            if absorption == "BULLISH_ABSORPTION": of_points += 15.0
            if cvd_div == "BULLISH_ABSORPTION": of_points += 10.0
            if cvd_slope < -5.0: of_points -= 25.0
            order_flow_score = max(10.0, min(100.0, of_points))
        else:
            of_points = 50.0
            if cvd_slope < 0: of_points += 20.0
            if imbalance < -0.10: of_points += 15.0
            if absorption == "BEARISH_ABSORPTION": of_points += 15.0
            if cvd_div == "BEARISH_EXHAUSTION": of_points += 10.0
            if cvd_slope > 5.0: of_points -= 25.0
            order_flow_score = max(10.0, min(100.0, of_points))

        # 6. LIQUIDITY SCORE (0 - 100)
        liquidity_score = 70.0
        spread_state = microstructure_result.get("spread_state", "NORMAL")
        sweep = orderflow_result.get("sweep", "NONE")

        if spread_state == "COMPRESSED":
            liquidity_score = 95.0
        elif spread_state == "NORMAL":
            liquidity_score = 80.0
        elif spread_state == "EXPANDED":
            liquidity_score = 45.0
            rejection_reasons.append("Spread expanded (liquidity thin)")

        if sweep == "LIQUIDITY_VACUUM":
            liquidity_score = min(liquidity_score, 35.0)
            rejection_reasons.append("Liquidity vacuum detected in orderbook")

        # 7. TRIGGER SCORE (0 - 100)
        trig_state = trigger_result.get("state", "SETUP_FORMING")
        candle_closed = trigger_result.get("candle_5m_closed", False)
        cvd_confirmed = trigger_result.get("cvd_confirmed", False)
        trigger_score = 40.0

        if trig_state == "TRIGGER_CONFIRMED":
            trigger_score = 95.0 if (candle_closed and cvd_confirmed) else 85.0
        elif trig_state == "TRIGGER_APPROACHING":
            trigger_score = 65.0
        elif trig_state == "SETUP_FORMING":
            trigger_score = 50.0
        else:  # INVALIDATED
            trigger_score = 15.0
            rejection_reasons.append(f"Trigger state is {trig_state}")

        # 8. CATALYST SCORE (0 - 100)
        catalyst_score = 70.0  # Neutral baseline
        if news_result:
            news_state = news_result.get("news_state", "UNKNOWN")
            confidence = float(news_result.get("confidence", 0.5))
            sentiment = float(news_result.get("usable_sentiment", 0.0))

            if news_state == "RISK" or "VETO" in news_result.get("model_interpretation", ""):
                catalyst_score = 10.0
                rejection_reasons.append(f"News veto active: {news_result.get('model_interpretation')}")
            elif news_state == "CATALYST":
                if (is_buy and sentiment > 0.25) or (is_sell and sentiment < -0.25):
                    catalyst_score = 95.0
                else:
                    catalyst_score = 40.0  # Catalyst counter to direction
            elif news_state in ("NEUTRAL", "UNKNOWN"):
                catalyst_score = 70.0

        # 9. RISK QUALITY SCORE (0 - 100)
        risk_quality_score = 70.0
        if risk_metrics:
            rr = float(risk_metrics.get("reward_to_risk", 2.0))
            if rr >= 3.0:
                risk_quality_score = 95.0
            elif rr >= 2.0:
                risk_quality_score = 85.0
            elif rr >= 1.5:
                risk_quality_score = 75.0
            elif rr >= 1.2:
                risk_quality_score = 65.0
            else:
                risk_quality_score = 25.0
                rejection_reasons.append(f"Reward-to-risk {rr:.2f} below minimum 1.2")

        # Category scores map
        cat_scores = {
            "REGIME": round(regime_score, 1),
            "STRUCTURE": round(structure_score, 1),
            "SETUP": round(setup_score, 1),
            "MOMENTUM": round(momentum_score, 1),
            "ORDER_FLOW": round(order_flow_score, 1),
            "LIQUIDITY": round(liquidity_score, 1),
            "TRIGGER": round(trigger_score, 1),
            "CATALYST": round(catalyst_score, 1),
            "RISK_QUALITY": round(risk_quality_score, 1)
        }

        # Weighted Total
        total_score = (
            cat_scores["REGIME"] * self.weights.regime +
            cat_scores["STRUCTURE"] * self.weights.structure +
            cat_scores["SETUP"] * self.weights.setup +
            cat_scores["MOMENTUM"] * self.weights.momentum +
            cat_scores["ORDER_FLOW"] * self.weights.order_flow +
            cat_scores["LIQUIDITY"] * self.weights.liquidity +
            cat_scores["TRIGGER"] * self.weights.trigger +
            cat_scores["CATALYST"] * self.weights.catalyst +
            cat_scores["RISK_QUALITY"] * self.weights.risk_quality
        )
        total_score = round(max(0.0, min(100.0, total_score)), 1)

        # Tier classification
        has_hard_veto = any("veto" in r.lower() or "invalidated" in r.lower() or "conflict" in r.lower() for r in rejection_reasons)
        
        if has_hard_veto:
            tier = "NO_TRADE"
            is_executable = False
            total_score = min(total_score, 40.0)
        elif total_score >= 90.0 and not rejection_reasons:
            tier = "A+"
            is_executable = True
        elif total_score >= 80.0 and not rejection_reasons:
            tier = "A"
            is_executable = True
        elif total_score >= 70.0 and not rejection_reasons:
            tier = "B"
            is_executable = True
        elif total_score >= 60.0 and not rejection_reasons:
            tier = "C"
            is_executable = False
        else:
            tier = "NO_TRADE"
            is_executable = False

        summary = (
            f"Tier {tier} ({total_score}/100) {direction} | "
            f"Regime: {cat_scores['REGIME']}, Setup: {cat_scores['SETUP']}, "
            f"OF: {cat_scores['ORDER_FLOW']}, Trig: {cat_scores['TRIGGER']}"
        )

        return QualityScoreResult(
            total_score=total_score,
            tier=tier,
            is_executable=is_executable,
            direction=direction,
            category_scores=cat_scores,
            summary=summary,
            rejection_reasons=rejection_reasons
        )
