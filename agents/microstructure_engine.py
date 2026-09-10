from typing import Dict, Any, List

class MicrostructureEngine:
    """
    Deterministic Quantitative Order Book Microstructure Engine.
    Implements:
    1. Sasha Stoikov's Micro-Price (Martingale fair-value estimator)
    2. Micro-Price Drift normalized by spread
    3. Multi-Level (5-level) Exponentially Weighted Depth Imbalance
    4. Adverse selection detector for Smart Maker Execution
    Zero AI agent dependencies. 100% deterministic NumPy/Python math.
    """
    def __init__(self):
        # Weights for top 5 levels: sum = 1.0
        self.depth_weights = [0.50, 0.25, 0.125, 0.0625, 0.0625]

    def compute_stoikov_microprice(self, best_bid: float, best_ask: float,
                                   bid_size: float, ask_size: float) -> tuple[float, float]:
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
            w = self.depth_weights[i] if i < len(self.depth_weights) else 0.05
            b_size = float(raw_bids[i].get("size", 0)) if i < len(raw_bids) else 0.0
            a_size = float(raw_asks[i].get("size", 0)) if i < len(raw_asks) else 0.0
            weighted_bid_vol += w * b_size
            weighted_ask_vol += w * a_size

        total_weighted_vol = weighted_bid_vol + weighted_ask_vol
        if total_weighted_vol <= 0:
            return 0.0

        imbalance = (weighted_bid_vol - weighted_ask_vol) / total_weighted_vol
        return round(imbalance, 3)

    def analyze_orderbook(self, ob_data: Dict[str, Any], signal: str = "NEUTRAL") -> Dict[str, Any]:
        """
        Analyze orderbook microstructure and output confirmatory telemetry and execution state.
        """
        best_bid = float(ob_data.get("best_bid", 0.0))
        best_ask = float(ob_data.get("best_ask", 0.0))
        raw_bids = ob_data.get("raw_bids", [])
        raw_asks = ob_data.get("raw_asks", [])

        bid_size_l1 = float(raw_bids[0].get("size", 0)) if raw_bids else 0.0
        ask_size_l1 = float(raw_asks[0].get("size", 0)) if raw_asks else 0.0

        micro_price, micro_drift = self.compute_stoikov_microprice(
            best_bid, best_ask, bid_size_l1, ask_size_l1
        )
        depth_imb_5 = self.compute_5level_depth_imbalance(raw_bids, raw_asks)

        # Microstructure Confirmation Tagging
        # Drift threshold: +/- 0.15 is significant, +/- 0.25 is strong
        if signal == "BUY":
            if micro_drift >= 0.15 and depth_imb_5 > 0.0:
                status = "FAVORABLE"
                desc = f"Bullish Micro-Drift ({micro_drift:+.2f}) & 5L Buy Depth ({depth_imb_5:+.2f}) confirms Long"
            elif micro_drift <= -0.25 and depth_imb_5 < -0.15:
                status = "ADVERSE"
                desc = f"Adverse Micro-Drift ({micro_drift:+.2f}) & Sell Depth ({depth_imb_5:+.2f}) warns of orderbook dump"
            else:
                status = "NEUTRAL"
                desc = f"Micro-Drift ({micro_drift:+.2f}) balanced at top of book"
        elif signal == "SELL":
            if micro_drift <= -0.15 and depth_imb_5 < 0.0:
                status = "FAVORABLE"
                desc = f"Bearish Micro-Drift ({micro_drift:+.2f}) & 5L Sell Depth ({depth_imb_5:+.2f}) confirms Short"
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
            "micro_price": micro_price,
            "micro_drift": micro_drift,
            "depth_imbalance_5": depth_imb_5,
            "status": status,
            "description": desc,
            "spread_pct": ob_data.get("spread_pct", 0.0),
            "best_bid": best_bid,
            "best_ask": best_ask
        }

microstructure_engine = MicrostructureEngine()
