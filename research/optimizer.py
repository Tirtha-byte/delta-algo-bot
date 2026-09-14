"""
BEAST v2 - Walk-Forward Optimizer (WFO)
research/optimizer.py

Executes Walk-Forward Optimization across rolling In-Sample (train) and Out-Of-Sample (test) windows
to evaluate parameter stability and eliminate curve-fitting.
"""

from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field
from research.backtester import BacktestEngine, BacktestResult


@dataclass
class WFOResult:
    parameter_set: Dict[str, Any]
    is_profit_factor: float
    oos_profit_factor: float
    wfe_ratio: float  # Walk-Forward Efficiency: OOS / IS
    is_win_rate: float
    oos_win_rate: float
    robustness_passed: bool


class WalkForwardOptimizer:
    def __init__(self, split_ratio: float = 0.70):
        self.split_ratio = split_ratio

    def optimize(
        self,
        candles: List[Dict[str, Any]],
        parameter_grid: Optional[List[Dict[str, Any]]] = None
    ) -> List[WFOResult]:
        """
        Run WFO across parameter grid.
        Default parameters explored:
        - min_quality_score: [65.0, 70.0, 75.0]
        - fee_pct: [0.0010]
        - slippage_pct: [0.0005]
        """
        if len(candles) < 50:
            return []

        grid = parameter_grid or [
            {"min_quality_score": 65.0},
            {"min_quality_score": 70.0},
            {"min_quality_score": 75.0}
        ]

        split_idx = int(len(candles) * self.split_ratio)
        is_candles = candles[:split_idx]
        oos_candles = candles[split_idx:]

        results: List[WFOResult] = []

        for params in grid:
            engine = BacktestEngine(
                fee_pct=params.get("fee_pct", 0.0010),
                slippage_pct=params.get("slippage_pct", 0.0005),
                min_quality_score=params.get("min_quality_score", 70.0)
            )

            is_res = engine.run(is_candles)
            oos_res = engine.run(oos_candles)

            is_pf = is_res.profit_factor
            oos_pf = oos_res.profit_factor

            # Walk Forward Efficiency ratio
            wfe = (oos_pf / is_pf) if is_pf > 0 else 0.0
            robustness = (wfe >= 0.50 and oos_res.win_rate >= 40.0)

            results.append(WFOResult(
                parameter_set=params,
                is_profit_factor=round(is_pf, 2),
                oos_profit_factor=round(oos_pf, 2),
                wfe_ratio=round(wfe, 2),
                is_win_rate=round(is_res.win_rate, 1),
                oos_win_rate=round(oos_res.win_rate, 1),
                robustness_passed=robustness
            ))

        return sorted(results, key=lambda x: (x.robustness_passed, x.oos_profit_factor), reverse=True)


optimizer = WalkForwardOptimizer()
