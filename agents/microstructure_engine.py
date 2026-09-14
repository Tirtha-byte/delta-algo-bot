import time
from typing import Dict, Any, List, Optional, Tuple
from collections import deque
import numpy as np

class MicrostructureEngine:
    """
    Deterministic Quantitative Order Book Microstructure Engine.
    Implements:
    1. Sasha Stoikov's Micro-Price (Martingale fair-value estimator)
    2. Micro-Price Drift normalized by spread
    3. Multi-Level (5-level and 15-level) Exponentially Weighted Depth Imbalance
    4. Book Slope (Bid/Ask liquidity elasticity: dQ / dP)
    5. Micro-Price Velocity and Acceleration (dMicro/dt, d^2Micro/dt^2)
    6. Spread Distribution Tracking (Percentiles, Spread Expansion/Compression)
    7. Adverse selection detector for Smart Maker Execution
    Zero AI agent dependencies. 100% deterministic NumPy/Python math.
    """
    def __init__(self):
        # Weights for top 5 levels: sum = 1.0
        self.depth_weights_5 = [0.50, 0.25, 0.125, 0.0625, 0.0625]
        # Exponential weights for 15 levels
        decay = np.exp(-0.25 * np.arange(15))
        self.depth_weights_15 = (decay / decay.sum()).tolist()

        # Rolling history per symbol for dynamics
        self._microprice_history: Dict[str, deque] = {}  # symbol -> deque of (ts, microprice)
        self._spread_history: Dict[str, deque] = {}      # symbol -> deque of float

    def compute_stoikov_microprice(self, best_bid: float, best_ask: float,
                                   bid_size: float, ask_size: float) -> Tuple[float, float]:
        """
        Stoikov (Cornell, 2018) Micro-Price formula:
        P_micro = P_bid * (Q_ask / (Q_bid + Q_ask)) + P_ask * (Q_bid / (Q_bid + Q_ask))
        MicroDrift = (P_micro - P_mid) / (Spread + 1e-6)
        """
        if best_bid <= 0 or best_ask <= 0:
            return 0.0, 0.0

        mid_price = (best_bid + best_ask) / 2.0
        spread = best_ask - best_bid
        total_top_qty = bid_size + ask_size

        if total_top_qty <= 0 or spread <= 0:
            return round(mid_price, 4), 0.0

        micro_price = (best_bid * (ask_size / total_top_qty)) + (best_ask * (bid_size / total_top_qty))
        micro_drift = (micro_price - mid_price) / (spread + 1e-9)

        return round(micro_price, 4), round(micro_drift, 3)

    def compute_5level_depth_imbalance(self, raw_bids: List[Dict[str, Any]],
                                       raw_asks: List[Dict[str, Any]]) -> float:
        """
        Calculates exponentially weighted orderbook depth imbalance across top 5 levels.
        Weighted Imbalance = sum(w_i * (Q_bid_i - Q_ask_i)) / sum(w_i * (Q_bid_i + Q_ask_i))
        Range: [-1.0 (heavy sell depth), +1.0 (heavy buy depth)]
        """
        weighted_bid_vol = 0.0
        weighted_ask_vol = 0.0

        num_levels = min(5, max(len(raw_bids), len(raw_asks)))
        if num_levels == 0:
            return 0.0

        for i in range(num_levels):
            w = self.depth_weights_5[i] if i < len(self.depth_weights_5) else 0.05
            b_size = float(raw_bids[i].get("size", 0)) if i < len(raw_bids) else 0.0
            a_size = float(raw_asks[i].get("size", 0)) if i < len(raw_asks) else 0.0
            weighted_bid_vol += w * b_size
            weighted_ask_vol += w * a_size

        total_weighted_vol = weighted_bid_vol + weighted_ask_vol
        if total_weighted_vol <= 0:
            return 0.0

        imbalance = (weighted_bid_vol - weighted_ask_vol) / total_weighted_vol
        return round(imbalance, 3)

    def compute_weighted_book_imbalance(self, raw_bids: List[Dict[str, Any]],
                                        raw_asks: List[Dict[str, Any]],
                                        levels: int = 15) -> float:
        """
        Calculates exponentially weighted orderbook depth imbalance across top 15 levels.
        """
        num_levels = min(levels, max(len(raw_bids), len(raw_asks)))
        if num_levels == 0:
            return 0.0

        weighted_bid_vol = 0.0
        weighted_ask_vol = 0.0

        for i in range(num_levels):
            w = self.depth_weights_15[i] if i < len(self.depth_weights_15) else 0.01
            b_size = float(raw_bids[i].get("size", 0)) if i < len(raw_bids) else 0.0
            a_size = float(raw_asks[i].get("size", 0)) if i < len(raw_asks) else 0.0
            weighted_bid_vol += w * b_size
            weighted_ask_vol += w * a_size

        tot = weighted_bid_vol + weighted_ask_vol
        if tot <= 0:
            return 0.0
        return round((weighted_bid_vol - weighted_ask_vol) / tot, 3)

    def compute_book_slope(self, raw_bids: List[Dict[str, Any]],
                           raw_asks: List[Dict[str, Any]]) -> Dict[str, float]:
        """
        Calculates orderbook slope (liquidity elasticity: cumulative volume divided by price distance).
        Higher bid slope indicates strong bid walls resisting downside pressure.
        """
        if not raw_bids or not raw_asks:
            return {"bid_slope": 0.0, "ask_slope": 0.0, "slope_ratio": 1.0}

        try:
            best_bid = float(raw_bids[0].get("price", 0.0))
            best_ask = float(raw_asks[0].get("price", 0.0))

            # Cumulative volume / price delta across top 5
            bid_vol_5 = sum(float(b.get("size", 0.0)) for b in raw_bids[:5])
            ask_vol_5 = sum(float(a.get("size", 0.0)) for a in raw_asks[:5])

            bid_deep = float(raw_bids[min(4, len(raw_bids) - 1)].get("price", best_bid))
            ask_deep = float(raw_asks[min(4, len(raw_asks) - 1)].get("price", best_ask))

            delta_p_bid = max(0.0001, abs(best_bid - bid_deep))
            delta_p_ask = max(0.0001, abs(ask_deep - best_ask))

            bid_slope = bid_vol_5 / delta_p_bid
            ask_slope = ask_vol_5 / delta_p_ask
            ratio = round(bid_slope / (ask_slope + 1e-9), 3)

            return {
                "bid_slope": round(bid_slope, 2),
                "ask_slope": round(ask_slope, 2),
                "slope_ratio": ratio
            }
        except Exception:
            return {"bid_slope": 0.0, "ask_slope": 0.0, "slope_ratio": 1.0}

    def compute_microprice_dynamics(self, symbol: str, microprice: float,
                                    ts: Optional[float] = None) -> Dict[str, float]:
        """
        Calculates microprice velocity (dMicro/dt) and acceleration (d^2Micro/dt^2).
        """
        now = ts or time.time()
        if symbol not in self._microprice_history:
            self._microprice_history[symbol] = deque(maxlen=60)

        hist = self._microprice_history[symbol]
        hist.append((now, microprice))

        if len(hist) < 3:
            return {"velocity": 0.0, "acceleration": 0.0}

        # Calculate velocity over last 2 steps
        t1, p1 = hist[-2]
        t2, p2 = hist[-1]
        dt = max(0.001, t2 - t1)
        velocity = (p2 - p1) / dt

        # Calculate acceleration
        if len(hist) >= 4:
            t0, p0 = hist[-3]
            dt0 = max(0.001, t1 - t0)
            prev_velocity = (p1 - p0) / dt0
            acceleration = (velocity - prev_velocity) / dt
        else:
            acceleration = 0.0

        return {
            "velocity": round(velocity, 4),
            "acceleration": round(acceleration, 4)
        }

    def compute_spread_regime(self, symbol: str, current_spread: float) -> Dict[str, Any]:
        """
        Tracks rolling spread percentiles and detects spread expansion vs compression.
        """
        if symbol not in self._spread_history:
            self._spread_history[symbol] = deque(maxlen=200)

        hist = self._spread_history[symbol]
        hist.append(current_spread)

        if len(hist) < 10:
            return {
                "spread_percentile": 50.0,
                "regime": "NORMAL",
                "is_expanded": False,
                "is_compressed": False
            }

        arr = np.array(hist)
        p = float(np.percentile(arr, 50))
        p90 = float(np.percentile(arr, 90))
        p20 = float(np.percentile(arr, 20))

        # Percentile rank of current spread
        pct = float((arr <= current_spread).mean() * 100.0)

        is_expanded = current_spread > p90
        is_compressed = current_spread < p20

        regime = "EXPANDED" if is_expanded else ("COMPRESSED" if is_compressed else "NORMAL")

        return {
            "spread_percentile": round(pct, 1),
            "regime": regime,
            "is_expanded": is_expanded,
            "is_compressed": is_compressed
        }

    def analyze_orderbook(self, ob_data: Dict[str, Any], signal: str = "NEUTRAL") -> Dict[str, Any]:
        """
        Analyze complete orderbook microstructure and output confirmatory telemetry and execution state.
        """
        symbol = ob_data.get("symbol", "UNKNOWN")
        best_bid = float(ob_data.get("best_bid", 0.0))
        best_ask = float(ob_data.get("best_ask", 0.0))
        raw_bids = ob_data.get("raw_bids", [])
        raw_asks = ob_data.get("raw_asks", [])

        bid_size_l1 = float(raw_bids[0].get("size", 0)) if raw_bids else float(ob_data.get("bid_size", 0.0))
        ask_size_l1 = float(raw_asks[0].get("size", 0)) if raw_asks else float(ob_data.get("ask_size", 0.0))

        micro_price, micro_drift = self.compute_stoikov_microprice(
            best_bid, best_ask, bid_size_l1, ask_size_l1
        )
        depth_imb_5 = self.compute_5level_depth_imbalance(raw_bids, raw_asks)
        depth_imb_15 = self.compute_weighted_book_imbalance(raw_bids, raw_asks, levels=15)
        slopes = self.compute_book_slope(raw_bids, raw_asks)
        dynamics = self.compute_microprice_dynamics(symbol, micro_price)
        spread = max(0.0, best_ask - best_bid)
        spread_regime = self.compute_spread_regime(symbol, spread)

        # Microstructure Confirmation Tagging
        if signal == "BUY":
            if micro_drift >= 0.15 and depth_imb_5 > 0.0:
                status = "FAVORABLE"
                desc = f"Bullish Micro-Drift ({micro_drift:+.2f}) & 5L Depth ({depth_imb_5:+.2f}) confirms Long"
            elif micro_drift <= -0.25 and depth_imb_5 < -0.15:
                status = "ADVERSE"
                desc = f"Adverse Micro-Drift ({micro_drift:+.2f}) & Sell Depth ({depth_imb_5:+.2f}) warns of orderbook dump"
            else:
                status = "NEUTRAL"
                desc = f"Micro-Drift ({micro_drift:+.2f}) balanced at top of book"
        elif signal == "SELL":
            if micro_drift <= -0.15 and depth_imb_5 < 0.0:
                status = "FAVORABLE"
                desc = f"Bearish Micro-Drift ({micro_drift:+.2f}) & 5L Depth ({depth_imb_5:+.2f}) confirms Short"
            elif micro_drift >= 0.25 and depth_imb_5 > 0.15:
                status = "ADVERSE"
                desc = f"Adverse Micro-Drift ({micro_drift:+.2f}) & Buy Depth ({depth_imb_5:+.2f}) warns of upward pressure"
            else:
                status = "NEUTRAL"
                desc = f"Micro-Drift ({micro_drift:+.2f}) balanced at top of book"
        else:
            status = "NEUTRAL"
            desc = "No directional trade signal"

        return {
            "symbol": symbol,
            "micro_price": micro_price,
            "micro_drift": micro_drift,
            "depth_imbalance_5": depth_imb_5,
            "depth_imbalance_15": depth_imb_15,
            "imbalance": depth_imb_5,  # Backward compatibility
            "book_slope": slopes,
            "velocity": dynamics["velocity"],
            "acceleration": dynamics["acceleration"],
            "spread_regime": spread_regime,
            "status": status,
            "description": desc,
            "spread": spread,
            "spread_pct": ob_data.get("spread_pct", 0.0),
            "best_bid": best_bid,
            "best_ask": best_ask
        }

microstructure_engine = MicrostructureEngine()
