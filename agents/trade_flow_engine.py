import time
from typing import Dict, Any, List, Optional, Tuple
from collections import deque
import numpy as np

class TradeFlowEngine:
    """
    BEAST v2 Real Trade Flow & Cumulative Volume Delta (CVD) Engine.
    Uses actual Delta Exchange trade maker/taker classifications:
    - 't' (taker buyer): Aggressive Buy lifting the ask.
    - 'm' (maker buyer): Aggressive Sell hitting the bid.
    
    Responsibilities:
    1. Rolling Aggressive Buy/Sell Volume & Delta
    2. CVD (Cumulative Volume Delta) & Linear Slope
    3. Trade Intensity & Large Trade Activity Detector
    4. Absorption Engine (detects passive limit walls absorbing aggressive market orders)
    5. Liquidity Sweep Engine (rapid multi-level book exhaustion)
    6. Price-vs-CVD Divergence Detector
    """
    def __init__(self):
        # Per-symbol state tracking
        self._trade_buffers: Dict[str, deque] = {}        # symbol -> deque of trade dicts
        self._cvd_history: Dict[str, deque] = {}          # symbol -> deque of (ts, cvd, price)
        self._cumulative_delta: Dict[str, float] = {}     # symbol -> float

    def ingest_trade(self, symbol: str, price: float, size: float,
                     is_aggressive_buy: bool, timestamp: Optional[float] = None):
        """Ingest real-time trade event."""
        now = timestamp or time.time()
        if not self._trade_buffers.get(symbol):
            self._trade_buffers[symbol] = deque(maxlen=2000)
        if not self._cvd_history.get(symbol):
            self._cvd_history[symbol] = deque(maxlen=300)
        if symbol not in self._cumulative_delta:
            self._cumulative_delta[symbol] = 0.0


        # Update cumulative delta
        delta = size if is_aggressive_buy else -size
        self._cumulative_delta[symbol] += delta
        current_cvd = self._cumulative_delta[symbol]

        trade_item = {
            "price": price,
            "size": size,
            "is_aggressive_buy": is_aggressive_buy,
            "delta": delta,
            "cvd": current_cvd,
            "timestamp": now
        }
        self._trade_buffers[symbol].append(trade_item)
        self._cvd_history[symbol].append((now, current_cvd, price))

    def ingest_trades_batch(self, symbol: str, trades_list: List[Dict[str, Any]]):
        """Batch ingest raw trades (from market_data_engine)."""
        for t in trades_list:
            p = float(t.get("price", 0.0))
            s = float(t.get("size", 0.0))
            is_buy = bool(t.get("is_aggressive_buy", t.get("role") == "t"))
            ts = t.get("timestamp") or t.get("local_time") or time.time()
            if isinstance(ts, (int, float)) and ts > 1e12:
                ts = ts / 1_000_000.0  # convert microseconds to seconds
            self.ingest_trade(symbol, p, s, is_buy, ts)

    def calculate_rolling_flow(self, symbol: str, window_trades: int = 50) -> Dict[str, Any]:
        """
        Calculates rolling flow metrics over the last N trades.
        """
        buf = self._trade_buffers.get(symbol)
        if not buf or len(buf) < 5:
            return {
                "symbol": symbol,
                "aggressive_buy_volume": 0.0,
                "aggressive_sell_volume": 0.0,
                "delta": 0.0,
                "cvd": self._cumulative_delta.get(symbol, 0.0),
                "cvd_slope": 0.0,
                "cvd_acceleration": 0.0,
                "trade_intensity": 0.0,
                "average_trade_size": 0.0,
                "large_trade_count": 0,
                "aggressor_ratio": 1.0,
                "flow_bias": "NEUTRAL"
            }

        recent = list(buf)[-window_trades:]
        buy_vol = sum(t["size"] for t in recent if t["is_aggressive_buy"])
        sell_vol = sum(t["size"] for t in recent if not t["is_aggressive_buy"])
        net_delta = buy_vol - sell_vol
        current_cvd = self._cumulative_delta.get(symbol, 0.0)

        # Average trade size & large trade threshold (3x avg)
        sizes = [t["size"] for t in recent]
        avg_size = float(np.mean(sizes)) if sizes else 0.0
        large_threshold = avg_size * 2.5
        large_count = sum(1 for s in sizes if s >= large_threshold)

        # Trade intensity (trades per second over window)
        time_span = max(0.5, recent[-1]["timestamp"] - recent[0]["timestamp"])
        intensity = len(recent) / time_span

        # Aggressor Ratio: BuyVol / (SellVol + epsilon)
        aggressor_ratio = round(buy_vol / (sell_vol + 1e-9), 3)

        # CVD Slope calculation using linear regression on recent CVD history
        cvd_pts = [t["cvd"] for t in recent]
        if len(cvd_pts) >= 5:
            x = np.arange(len(cvd_pts))
            slope, _ = np.polyfit(x, cvd_pts, 1)
        else:
            slope = 0.0

        # Flow Bias
        if net_delta > 0 and aggressor_ratio > 1.25 and slope > 0:
            flow_bias = "BULLISH_FLOW"
        elif net_delta < 0 and aggressor_ratio < 0.80 and slope < 0:
            flow_bias = "BEARISH_FLOW"
        else:
            flow_bias = "BALANCED"

        return {
            "symbol": symbol,
            "aggressive_buy_volume": round(buy_vol, 2),
            "aggressive_sell_volume": round(sell_vol, 2),
            "delta": round(net_delta, 2),
            "cvd": round(current_cvd, 2),
            "cvd_slope": round(float(slope), 3),
            "trade_intensity": round(intensity, 2),
            "average_trade_size": round(avg_size, 2),
            "large_trade_count": large_count,
            "aggressor_ratio": aggressor_ratio,
            "flow_bias": flow_bias
        }

    def detect_absorption(self, symbol: str, current_price: float,
                          orderbook_snapshot: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Detect situations where large aggressive market flow fails to push price
        because it is absorbed by deep passive limit orders (Absorption).
        
        Bullish Absorption:
        - Large aggressive selling (negative delta)
        - Price fails to make proportional downside progress (holds or bounces)
        - Bid liquidity replenishes or stays thick
        
        Bearish Absorption:
        - Large aggressive buying (positive delta)
        - Price fails to make proportional upside progress
        - Ask liquidity replenishes or stays thick
        """
        buf = self._trade_buffers.get(symbol)
        if not buf or len(buf) < 15:
            return {"absorption": "NONE", "confidence": 0.0, "evidence": "Insufficient trade buffer"}

        recent = list(buf)[-30:]
        buy_vol = sum(t["size"] for t in recent if t["is_aggressive_buy"])
        sell_vol = sum(t["size"] for t in recent if not t["is_aggressive_buy"])
        start_price = recent[0]["price"]
        price_change_pct = ((current_price - start_price) / start_price) * 100.0 if start_price > 0 else 0.0

        depth_imb = orderbook_snapshot.get("depth_imbalance_5", 0.0) if orderbook_snapshot else 0.0

        # Bullish Absorption: Heavy sell volume (> 65% of volume), but price held or went UP!
        total_vol = buy_vol + sell_vol
        if total_vol > 0 and (sell_vol / total_vol) >= 0.65 and price_change_pct >= -0.05:
            confidence = min(0.95, 0.50 + ((sell_vol / total_vol) * 0.3) + max(0.0, depth_imb * 0.2))
            return {
                "absorption": "BULLISH_ABSORPTION",
                "confidence": round(confidence, 2),
                "evidence": f"Heavy sell aggressors ({sell_vol:.1f} vs {buy_vol:.1f} buy) failed to depress price ({price_change_pct:+.2f}%). Bids absorbing.",
                "sell_volume": round(sell_vol, 1),
                "buy_volume": round(buy_vol, 1),
                "price_change_pct": round(price_change_pct, 3)
            }

        # Bearish Absorption: Heavy buy volume (> 65% of volume), but price failed to rise!
        if total_vol > 0 and (buy_vol / total_vol) >= 0.65 and price_change_pct <= 0.05:
            confidence = min(0.95, 0.50 + ((buy_vol / total_vol) * 0.3) + max(0.0, -depth_imb * 0.2))
            return {
                "absorption": "BEARISH_ABSORPTION",
                "confidence": round(confidence, 2),
                "evidence": f"Heavy buy aggressors ({buy_vol:.1f} vs {sell_vol:.1f} sell) failed to elevate price ({price_change_pct:+.2f}%). Asks absorbing.",
                "buy_volume": round(buy_vol, 1),
                "sell_volume": round(sell_vol, 1),
                "price_change_pct": round(price_change_pct, 3)
            }

        return {
            "absorption": "NONE",
            "confidence": 0.0,
            "evidence": "Normal market flow matching price displacement"
        }

    def detect_sweeps(self, symbol: str, current_price: float,
                      spread: float = 0.0) -> Dict[str, Any]:
        """
        Detect rapid consumption of multiple orderbook levels + abnormally high aggressor volume.
        Classifies sweep into:
        - VALID_BREAKOUT
        - FAILED_BREAKOUT
        - LIQUIDITY_SWEEP
        - LIQUIDITY_VACUUM
        - NONE
        """
        buf = self._trade_buffers.get(symbol)
        if not buf or len(buf) < 10:
            return {"sweep_type": "NONE", "confidence": 0.0, "details": "Insufficient trade buffer"}

        recent = list(buf)[-15:]
        time_span = max(0.1, recent[-1]["timestamp"] - recent[0]["timestamp"])
        prices = [t["price"] for t in recent]
        high_p = max(prices)
        low_p = min(prices)
        price_disp = high_p - low_p

        sizes = [t["size"] for t in recent]
        tot_vol = sum(sizes)
        avg_vol = np.mean(sizes) if sizes else 1.0

        # Rapid sweep conditions: multiple trades in < 2 seconds with high volume and clear displacement
        is_fast_burst = time_span <= 2.5 and len(recent) >= 8
        is_large_vol = tot_vol > (avg_vol * 12)

        if not (is_fast_burst or is_large_vol):
            return {"sweep_type": "NONE", "confidence": 0.0, "details": "Normal execution pacing"}

        buy_count = sum(1 for t in recent if t["is_aggressive_buy"])
        sell_count = len(recent) - buy_count

        # Sweep Direction
        if buy_count >= 0.75 * len(recent):
            # Upside sweep: test if price is holding at highs (breakout) or snapped back (liquidity sweep)
            if current_price >= high_p - (0.2 * price_disp):
                sweep_type = "VALID_BREAKOUT"
                details = f"Bullish breakout sweep: {tot_vol:.1f} qty swept across ${price_disp:.2f} in {time_span:.1f}s. Price holding highs."
            else:
                sweep_type = "LIQUIDITY_SWEEP"
                details = f"Bullish liquidity sweep (Turtle Soup): Highs swept at ${high_p:.2f} followed by sharp rejection."
            confidence = 0.85
        elif sell_count >= 0.75 * len(recent):
            # Downside sweep
            if current_price <= low_p + (0.2 * price_disp):
                sweep_type = "VALID_BREAKOUT"
                details = f"Bearish breakout sweep: {tot_vol:.1f} qty swept across ${price_disp:.2f} in {time_span:.1f}s. Price holding lows."
            else:
                sweep_type = "LIQUIDITY_SWEEP"
                details = f"Bearish liquidity sweep: Lows swept at ${low_p:.2f} followed by sharp rejection."
            confidence = 0.85
        else:
            sweep_type = "LIQUIDITY_VACUUM"
            details = f"Two-sided choppy liquidity vacuum ({tot_vol:.1f} volume, {time_span:.1f}s duration)."
            confidence = 0.60

        return {
            "sweep_type": sweep_type,
            "confidence": confidence,
            "details": details,
            "displaced_range": round(price_disp, 2),
            "volume_swept": round(tot_vol, 1),
            "duration_sec": round(time_span, 2)
        }

    def detect_cvd_divergence(self, symbol: str, current_price: float,
                              lookback_bars: int = 20) -> Dict[str, Any]:
        """
        Detect Price-vs-CVD Divergence:
        - Price UP + CVD UP -> BULLISH_CONFIRMATION
        - Price UP + CVD DOWN -> BEARISH_DIVERGENCE (distribution into strength)
        - Price DOWN + CVD DOWN -> BEARISH_CONFIRMATION
        - Price DOWN + CVD UP -> BULLISH_DIVERGENCE (accumulation into weakness)
        """
        hist = self._cvd_history.get(symbol)
        if not hist or len(hist) < 10:
            return {"divergence": "FLOW_BALANCED", "description": "Insufficient CVD history"}

        pts = list(hist)[-min(lookback_bars, len(hist)):]
        t_start, cvd_start, price_start = pts[0]
        t_end, cvd_end, price_end = pts[-1]

        delta_price = price_end - price_start
        delta_cvd = cvd_end - cvd_start

        pct_price = (delta_price / price_start) * 100.0 if price_start > 0 else 0.0

        if pct_price > 0.08:
            if delta_cvd > 0:
                return {
                    "divergence": "BULLISH_CONFIRMATION",
                    "description": f"Bullish Flow Confirmation: Price (+{pct_price:.2f}%) accompanied by positive CVD (+{delta_cvd:.1f})."
                }
            else:
                return {
                    "divergence": "BEARISH_DIVERGENCE",
                    "description": f"Bearish Flow Divergence: Price rallied (+{pct_price:.2f}%) but CVD declined ({delta_cvd:.1f}). Exhaustion warning."
                }
        elif pct_price < -0.08:
            if delta_cvd < 0:
                return {
                    "divergence": "BEARISH_CONFIRMATION",
                    "description": f"Bearish Flow Confirmation: Price ({pct_price:.2f}%) accompanied by negative CVD ({delta_cvd:.1f})."
                }
            else:
                return {
                    "divergence": "BULLISH_DIVERGENCE",
                    "description": f"Bullish Flow Divergence: Price dropped ({pct_price:.2f}%) but CVD rose (+{delta_cvd:.1f}). Smart accumulation."
                }

        return {
            "divergence": "FLOW_BALANCED",
            "description": "CVD and Price in equilibrium"
        }

    def analyze(self, symbol: str, current_price: float,
                orderbook_snapshot: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Comprehensive Trade Flow Analysis."""
        flow = self.calculate_rolling_flow(symbol)
        absorption = self.detect_absorption(symbol, current_price, orderbook_snapshot)
        sweep = self.detect_sweeps(symbol, current_price)
        divergence = self.detect_cvd_divergence(symbol, current_price)

        return {
            "symbol": symbol,
            "flow": flow,
            "absorption": absorption,
            "sweep": sweep,
            "divergence": divergence
        }

trade_flow_engine = TradeFlowEngine()
