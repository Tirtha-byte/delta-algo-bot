"""
BEAST v2 - Research Feature & Factor Analyzer
research/feature_analyzer.py

Analyzes predictive capacity of trading features:
1. Information Coefficient (IC): Rank correlation between feature values and forward returns
2. Factor decay and autocorrelation
3. Regime-conditional feature effectiveness
"""

import numpy as np
from typing import Dict, Any, List, Optional


class FeatureAnalyzer:
    def compute_spearman_ic(self, feature_values: List[float], forward_returns: List[float]) -> float:
        """
        Compute Spearman rank correlation (Information Coefficient).
        """
        if len(feature_values) < 5 or len(feature_values) != len(forward_returns):
            return 0.0

        x = np.array(feature_values, dtype=float)
        y = np.array(forward_returns, dtype=float)

        # Handle zero variance edge cases
        if np.std(x) < 1e-6 or np.std(y) < 1e-6:
            return 0.0

        # Rank transform
        x_rank = np.argsort(np.argsort(x))
        y_rank = np.argsort(np.argsort(y))

        # Pearson of ranks = Spearman
        n = len(x)
        cov = np.cov(x_rank, y_rank)[0, 1]
        std_x = np.std(x_rank, ddof=1)
        std_y = np.std(y_rank, ddof=1)

        if std_x * std_y <= 0:
            return 0.0
        return float(cov / (std_x * std_y))

    def analyze_factors(
        self,
        samples: List[Dict[str, Any]],
        forward_periods: int = 4
    ) -> Dict[str, Any]:
        """
        Analyze a series of market state samples with future returns.
        Each sample dict contains:
          - setup_score: float
          - orderflow_cvd_slope: float
          - book_imbalance: float
          - rsi: float
          - forward_return: float
        """
        if not samples:
            return {"status": "INSUFFICIENT_DATA", "factor_ic": {}}

        f_setup = []
        f_cvd = []
        f_imbalance = []
        f_rsi = []
        returns = []

        for s in samples:
            ret = s.get("forward_return")
            if ret is not None:
                returns.append(float(ret))
                f_setup.append(float(s.get("setup_score", 50.0)))
                f_cvd.append(float(s.get("orderflow_cvd_slope", 0.0)))
                f_imbalance.append(float(s.get("book_imbalance", 0.0)))
                f_rsi.append(float(s.get("rsi", 50.0)))

        ic_setup = self.compute_spearman_ic(f_setup, returns)
        ic_cvd = self.compute_spearman_ic(f_cvd, returns)
        ic_imbalance = self.compute_spearman_ic(f_imbalance, returns)
        ic_rsi = self.compute_spearman_ic(f_rsi, returns)

        factor_ic = {
            "setup_score": round(ic_setup, 3),
            "cvd_slope": round(ic_cvd, 3),
            "book_imbalance": round(ic_imbalance, 3),
            "rsi": round(ic_rsi, 3)
        }

        # Recommendations
        recommendations = []
        if ic_setup > 0.05:
            recommendations.append("Setup engine factor has strong positive predictive power (+IC).")
        if ic_cvd > 0.05:
            recommendations.append("CVD slope is leading price; recommend maintaining high orderflow weight.")
        if ic_rsi < -0.05:
            recommendations.append("Mean-reverting tendencies detected in RSI.")

        return {
            "sample_count": len(samples),
            "factor_ic": factor_ic,
            "recommendations": recommendations
        }


feature_analyzer = FeatureAnalyzer()
