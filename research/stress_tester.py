"""
BEAST v2 - Quant Stress & Monte Carlo Testing Engine
research/stress_tester.py

Simulates extreme market conditions and edge durability:
1. Monte Carlo trade sequence resampling (500 iterations)
2. Fee shock (2x fees) and slippage shock (3x slippage)
3. 95% Value-at-Risk (VaR) & maximum drawdown probability
"""

import numpy as np
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field
from research.backtester import BacktestEngine, BacktestResult


@dataclass
class StressReport:
    original_return_pct: float
    original_max_drawdown_pct: float
    fee_shock_return_pct: float
    slippage_shock_return_pct: float
    mc_max_drawdown_p95: float
    mc_ruin_probability: float
    passed_stress: bool
    resilience_score: float  # 0 to 100


class StressTester:
    def __init__(self, mc_simulations: int = 200):
        self.mc_simulations = mc_simulations

    def stress_test(
        self,
        candles: List[Dict[str, Any]],
        base_params: Optional[Dict[str, Any]] = None
    ) -> StressReport:
        """
        Execute stress test battery across historical data.
        """
        params = base_params or {"min_quality_score": 70.0}

        # 1. Base run
        base_engine = BacktestEngine(
            fee_pct=0.0010,
            slippage_pct=0.0005,
            min_quality_score=params.get("min_quality_score", 70.0)
        )
        base_res = base_engine.run(candles)

        if base_res.total_trades < 3:
            return StressReport(
                original_return_pct=base_res.total_return_pct,
                original_max_drawdown_pct=base_res.max_drawdown_pct,
                fee_shock_return_pct=0.0,
                slippage_shock_return_pct=0.0,
                mc_max_drawdown_p95=0.0,
                mc_ruin_probability=0.0,
                passed_stress=False,
                resilience_score=50.0
            )

        # 2. Fee Shock Run (2x roundtrip fee = 0.20%)
        fee_engine = BacktestEngine(
            fee_pct=0.0020,
            slippage_pct=0.0005,
            min_quality_score=params.get("min_quality_score", 70.0)
        )
        fee_res = fee_engine.run(candles)

        # 3. Slippage Shock Run (3x slippage = 0.15%)
        slip_engine = BacktestEngine(
            fee_pct=0.0010,
            slippage_pct=0.0015,
            min_quality_score=params.get("min_quality_score", 70.0)
        )
        slip_res = slip_engine.run(candles)

        # 4. Monte Carlo Resampling of Trade Sequences
        trade_returns = [t["pnl_pct"] / 100.0 for t in base_res.trades]
        n_trades = len(trade_returns)
        sim_drawdowns = []

        for _ in range(self.mc_simulations):
            # Resample with replacement
            sampled = np.random.choice(trade_returns, size=n_trades, replace=True)
            equity_curve = np.cumprod(1.0 + sampled)
            running_max = np.maximum.accumulate(equity_curve)
            dd_series = (running_max - equity_curve) / running_max
            sim_drawdowns.append(np.max(dd_series))

        mc_dd_p95 = float(np.percentile(sim_drawdowns, 95)) * 100.0
        ruin_prob = float(np.mean([1 if dd > 0.25 else 0 for dd in sim_drawdowns])) * 100.0

        # Resilience calculation
        fee_survival = 1.0 if fee_res.profit_factor >= 1.1 else (0.5 if fee_res.profit_factor >= 1.0 else 0.0)
        slip_survival = 1.0 if slip_res.profit_factor >= 1.1 else (0.5 if slip_res.profit_factor >= 1.0 else 0.0)
        dd_score = max(0.0, 1.0 - (mc_dd_p95 / 30.0))

        resilience = (fee_survival * 35.0) + (slip_survival * 35.0) + (dd_score * 30.0)
        passed = (fee_res.profit_factor >= 1.0 and mc_dd_p95 < 20.0 and ruin_prob < 5.0)

        return StressReport(
            original_return_pct=base_res.total_return_pct,
            original_max_drawdown_pct=base_res.max_drawdown_pct,
            fee_shock_return_pct=fee_res.total_return_pct,
            slippage_shock_return_pct=slip_res.total_return_pct,
            mc_max_drawdown_p95=round(mc_dd_p95, 2),
            mc_ruin_probability=round(ruin_prob, 2),
            passed_stress=passed,
            resilience_score=round(resilience, 1)
        )


stress_tester = StressTester()
