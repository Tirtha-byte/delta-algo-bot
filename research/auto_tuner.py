"""
BEAST v2 - Autonomous Quantitative Self-Tuner & Optimization Loop
research/auto_tuner.py

Iteratively backtests, mutates, and fine-tunes quantitative parameters across the
multi-asset universe until reaching >= 70% average win rate and 40+ daily trades throughput.
"""

import os
import sys
import json
import time
import logging
from typing import Dict, Any, List, Tuple, Optional
from dataclasses import dataclass, asdict
import numpy as np

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from agents.regime_engine import RegimeEngine

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("beast_v2.auto_tuner")

DATA_DIR = os.path.join(PROJECT_ROOT, "data", "historical")
OUTPUT_PATH = os.path.join(PROJECT_ROOT, "data", "best_quant_params.json")
LEADERBOARD_PATH = os.path.join(PROJECT_ROOT, "data", "research_leaderboard.json")

SYMBOLS = [
    "BTCUSD", "ETHUSD", "SOLUSD",
    "NVDAXUSD", "TSLAXUSD", "PLTRBUSD",
    "MSTRBUSD", "AAPLXUSD", "COINXUSD", "AMZNXUSD"
]

@dataclass
class QuantParameterSet:
    tp1_mult: float          # 0.4 - 1.0 (Take profit 1 in multiples of ATR)
    tp2_mult: float          # 1.8 - 2.8 (Runner target in multiples of ATR)
    sl_mult: float           # 1.3 - 2.5 (Stop loss in multiples of ATR)
    scale_pct: float         # 0.65 - 0.85 (Fraction closed at TP1)
    trend_sep: float         # 0.0002 - 0.0010 (EMA20 vs EMA50 trend separation)
    pullback_max_atr: float  # 0.4 - 1.2 (Max distance from EMA20 in ATR)
    fee_cushion: float       # 0.0012 - 0.0018 (Buffer for breakeven ratchet)
    min_adx: float           # 16.0 - 26.0 (Trend momentum threshold)
    use_candle_trigger: bool # Require directional close / wick rejection
    min_tp_pct: float = 0.0018 # Dynamic TP floor to ensure gains exceed exchange fees

@dataclass
class SimulationResult:
    total_trades: int
    wins: int
    losses: int
    win_rate: float
    trades_per_day: float
    profit_factor: float
    total_return_pct: float
    max_drawdown_pct: float
    parameters: Dict[str, Any]
    fitness_score: float

class AutoQuantOptimizer:
    def __init__(self, data_dir: str = DATA_DIR):
        self.data_dir = data_dir
        self.regime_engine = RegimeEngine()
        self.universe_data: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
        self._load_universe()

    def _load_universe(self):
        """Pre-load all available 5m and 15m candle datasets into memory for fast iteration."""
        logger.info(f"[AutoTuner] Loading historical market data from {self.data_dir}...")
        for sym in SYMBOLS:
            self.universe_data[sym] = {}
            for res in ["5m", "15m"]:
                fp = os.path.join(self.data_dir, f"{sym}_{res}.json")
                if os.path.exists(fp):
                    try:
                        with open(fp, "r", encoding="utf-8") as f:
                            candles = json.load(f)
                            if len(candles) >= 50:
                                self.universe_data[sym][res] = candles
                    except Exception as e:
                        logger.error(f"Error loading {fp}: {e}")
            loaded_resolutions = list(self.universe_data[sym].keys())
            if loaded_resolutions:
                logger.info(f"  Loaded {sym}: {loaded_resolutions} ({len(self.universe_data[sym].get('5m', []))} 5m bars)")

    def simulate_asset(self, symbol: str, params: QuantParameterSet) -> List[Dict[str, Any]]:
        """Simulate fast execution on 5m candles with 15m trend guidance."""
        candles_5m = self.universe_data.get(symbol, {}).get("5m", [])
        if len(candles_5m) < 40:
            return []

        fee_pct = 0.0010       # 0.10% roundtrip Delta fee
        slippage_pct = 0.0003  # 0.03% market fill slippage
        trades = []
        active_pos = None

        # Warmup index
        window = 30
        for i in range(window, len(candles_5m)):
            curr_candle = candles_5m[i]
            history = candles_5m[max(0, i - 80) : i]
            curr_open = float(curr_candle["open"])
            curr_close = float(curr_candle["close"])
            curr_high = float(curr_candle["high"])
            curr_low = float(curr_candle["low"])
            candle_range = max(curr_high - curr_low, 1e-4)

            # 1. Manage Active Position
            if active_pos is not None:
                side = active_pos["side"]
                entry = active_pos["entry"]
                tp1 = active_pos["tp1"]
                tp2 = active_pos["tp2"]
                sl = active_pos["sl"]
                atr = active_pos["atr"]

                if side == "BUY":
                    if curr_high > active_pos["peak"]:
                        active_pos["peak"] = curr_high

                    # Check TP1 -> Scale out & activate fee-cushioned breakeven
                    if curr_high >= tp1 and not active_pos["tp1_hit"]:
                        active_pos["tp1_hit"] = True
                        active_pos["breakeven"] = True
                        active_pos["sl"] = max(active_pos["sl"], entry * (1.0 + params.fee_cushion))

                    if active_pos["breakeven"]:
                        trail = active_pos["peak"] - (1.0 * atr)
                        if trail > active_pos["sl"]:
                            active_pos["sl"] = trail

                    exit_p = None
                    reason = None
                    if curr_low <= active_pos["sl"]:
                        exit_p = active_pos["sl"]
                        reason = "BREAKEVEN_STOP" if active_pos["breakeven"] else "STOP_LOSS"
                    elif curr_high >= tp2:
                        exit_p = tp2
                        reason = "TAKE_PROFIT_2"

                    if exit_p is not None:
                        if active_pos["tp1_hit"]:
                            pnl_tp1 = (tp1 - entry) / entry - fee_pct
                            pnl_rem = (exit_p - entry) / entry - fee_pct
                            net_pnl = (params.scale_pct * pnl_tp1) + ((1.0 - params.scale_pct) * pnl_rem)
                        else:
                            net_pnl = (exit_p - entry) / entry - fee_pct

                        trades.append({
                            "symbol": symbol, "side": side, "pnl_pct": net_pnl * 100,
                            "reason": reason, "bars": i - active_pos["entry_bar"]
                        })
                        active_pos = None
                        continue

                else:  # SELL
                    if curr_low < active_pos["peak"]:
                        active_pos["peak"] = curr_low

                    if curr_low <= tp1 and not active_pos["tp1_hit"]:
                        active_pos["tp1_hit"] = True
                        active_pos["breakeven"] = True
                        active_pos["sl"] = min(active_pos["sl"], entry * (1.0 - params.fee_cushion))

                    if active_pos["breakeven"]:
                        trail = active_pos["peak"] + (1.0 * atr)
                        if trail < active_pos["sl"]:
                            active_pos["sl"] = trail

                    exit_p = None
                    reason = None
                    if curr_high >= active_pos["sl"]:
                        exit_p = active_pos["sl"]
                        reason = "BREAKEVEN_STOP" if active_pos["breakeven"] else "STOP_LOSS"
                    elif curr_low <= tp2:
                        exit_p = tp2
                        reason = "TAKE_PROFIT_2"

                    if exit_p is not None:
                        if active_pos["tp1_hit"]:
                            pnl_tp1 = (entry - tp1) / entry - fee_pct
                            pnl_rem = (entry - exit_p) / entry - fee_pct
                            net_pnl = (params.scale_pct * pnl_tp1) + ((1.0 - params.scale_pct) * pnl_rem)
                        else:
                            net_pnl = (entry - exit_p) / entry - fee_pct

                        trades.append({
                            "symbol": symbol, "side": side, "pnl_pct": net_pnl * 100,
                            "reason": reason, "bars": i - active_pos["entry_bar"]
                        })
                        active_pos = None
                        continue

                continue

            # 2. Entry Logic (Only if no active position)
            closes = [float(c["close"]) for c in history]
            highs = [float(c["high"]) for c in history]
            lows = [float(c["low"]) for c in history]

            # Fast EMAs on 5m
            ema20 = float(np.mean(closes[-20:]))
            ema50 = float(np.mean(closes[-50:])) if len(closes) >= 50 else float(np.mean(closes))

            # Approximate ATR
            trs = [max(highs[k] - lows[k], abs(highs[k] - closes[k-1]), abs(lows[k] - closes[k-1])) for k in range(1, len(closes))]
            atr = float(np.mean(trs[-14:])) if len(trs) >= 14 else (curr_close * 0.008)

            trend_sep = (ema20 - ema50) / max(ema50, 1e-4)
            dist_to_ema20 = abs(curr_close - ema20) / max(atr, 1e-4)

            # Candle confirmation trigger
            is_bull_trigger = True
            is_bear_trigger = True
            if params.use_candle_trigger:
                is_bull_trigger = (curr_close > curr_open) or ((min(curr_open, curr_close) - curr_low) / candle_range >= 0.25)
                is_bear_trigger = (curr_close < curr_open) or ((curr_high - max(curr_open, curr_close)) / candle_range >= 0.25)

            target_tp1 = max(params.tp1_mult * atr, curr_close * params.min_tp_pct)

            # Long Entry: Upward trend separation + pullback near EMA20 + candle trigger
            if trend_sep >= params.trend_sep and curr_close >= ema50 and dist_to_ema20 <= params.pullback_max_atr and is_bull_trigger:
                entry = curr_close * (1.0 + slippage_pct)
                active_pos = {
                    "side": "BUY", "entry": entry, "peak": entry,
                    "tp1": entry + target_tp1,
                    "tp2": entry + (params.tp2_mult * atr),
                    "sl": entry - (params.sl_mult * atr),
                    "atr": atr, "tp1_hit": False, "breakeven": False,
                    "entry_bar": i
                }

            # Short Entry: Downward trend separation + rally near EMA20 + candle trigger
            elif trend_sep <= -params.trend_sep and curr_close <= ema50 and dist_to_ema20 <= params.pullback_max_atr and is_bear_trigger:
                entry = curr_close * (1.0 - slippage_pct)
                active_pos = {
                    "side": "SELL", "entry": entry, "peak": entry,
                    "tp1": entry - target_tp1,
                    "tp2": entry - (params.tp2_mult * atr),
                    "sl": entry + (params.sl_mult * atr),
                    "atr": atr, "tp1_hit": False, "breakeven": False,
                    "entry_bar": i
                }

        return trades

    def evaluate_parameter_set(self, params: QuantParameterSet) -> SimulationResult:
        """Run simulation across all instruments and aggregate portfolio statistics."""
        all_trades = []
        total_eval_bars = 0

        for sym in SYMBOLS:
            sym_trades = self.simulate_asset(sym, params)
            all_trades.extend(sym_trades)
            total_eval_bars += len(self.universe_data.get(sym, {}).get("5m", []))

        total = len(all_trades)
        wins = [t for t in all_trades if t["pnl_pct"] > 0]
        losses = [t for t in all_trades if t["pnl_pct"] <= 0]
        win_rate = (len(wins) / total * 100) if total > 0 else 0.0

        # Trades per day: 288 5m bars per instrument per day
        effective_days = max(1.0, (total_eval_bars / len(SYMBOLS)) / 288.0) if SYMBOLS else 1.0
        trades_per_day = total / effective_days

        gross_profit = sum(t["pnl_pct"] for t in wins)
        gross_loss = abs(sum(t["pnl_pct"] for t in losses))
        profit_factor = (gross_profit / max(gross_loss, 1e-4)) if gross_loss > 0 else (9.9 if gross_profit > 0 else 0.0)

        total_return_pct = gross_profit - gross_loss

        # Drawdown calculation
        equity = 1000.0
        peak = 1000.0
        max_dd = 0.0
        for t in all_trades:
            equity += equity * (t["pnl_pct"] / 100.0)
            if equity > peak:
                peak = equity
            dd = (peak - equity) / peak
            if dd > max_dd:
                max_dd = dd

        # Multi-objective fitness function:
        # Heavily rewards >= 70% win rate and >= 40 trades/day
        wr_score = min(100.0, win_rate)
        vol_score = min(50.0, trades_per_day)  # Capped at 50/day target
        pf_score = min(3.0, profit_factor) * 15.0

        # Penalty if win rate < 70%
        penalty = 0.0
        if win_rate < 70.0:
            penalty += (70.0 - win_rate) * 2.5
        if trades_per_day < 35.0:
            penalty += (35.0 - trades_per_day) * 1.5

        fitness = max(0.0, (wr_score * 0.50) + (vol_score * 0.30) + (pf_score * 0.20) - penalty)

        return SimulationResult(
            total_trades=total,
            wins=len(wins),
            losses=len(losses),
            win_rate=round(win_rate, 1),
            trades_per_day=round(trades_per_day, 1),
            profit_factor=round(profit_factor, 2),
            total_return_pct=round(total_return_pct, 2),
            max_drawdown_pct=round(max_dd * 100, 2),
            parameters=asdict(params),
            fitness_score=round(fitness, 2)
        )

    def run_optimization_loop(self, max_iterations: int = 15, target_win_rate: float = 70.0, min_trades_day: float = 40.0) -> SimulationResult:
        """
        Autonomous fine-tuning loop:
        Iteratively tests parameter spaces, refines boundaries around top performers,
        and converges on parameter sets delivering >= target_win_rate (70%) and high trade volume.
        """
        logger.info("=" * 70)
        logger.info(f"[AutoTuner] Starting Self-Improving Optimization Loop")
        logger.info(f"Target: Win Rate >= {target_win_rate}% | Trade Velocity >= {min_trades_day} trades/day")
        logger.info("=" * 70)

        # Baseline seed candidates calibrated from market microstructure scans
        candidate_pool: List[QuantParameterSet] = [
            QuantParameterSet(tp1_mult=0.45, tp2_mult=2.5, sl_mult=2.2, scale_pct=0.75, trend_sep=0.0003, pullback_max_atr=0.8, fee_cushion=0.0015, min_adx=18.0, use_candle_trigger=True, min_tp_pct=0.0018),
            QuantParameterSet(tp1_mult=0.50, tp2_mult=2.5, sl_mult=2.5, scale_pct=0.75, trend_sep=0.0003, pullback_max_atr=0.9, fee_cushion=0.0015, min_adx=18.0, use_candle_trigger=True, min_tp_pct=0.0018),
            QuantParameterSet(tp1_mult=0.55, tp2_mult=2.2, sl_mult=2.2, scale_pct=0.70, trend_sep=0.0003, pullback_max_atr=0.8, fee_cushion=0.0015, min_adx=18.0, use_candle_trigger=True, min_tp_pct=0.0018),
            QuantParameterSet(tp1_mult=0.60, tp2_mult=2.5, sl_mult=2.0, scale_pct=0.80, trend_sep=0.0004, pullback_max_atr=1.0, fee_cushion=0.0015, min_adx=18.0, use_candle_trigger=True, min_tp_pct=0.0020),
            QuantParameterSet(tp1_mult=0.70, tp2_mult=2.4, sl_mult=1.8, scale_pct=0.75, trend_sep=0.0004, pullback_max_atr=0.8, fee_cushion=0.0015, min_adx=20.0, use_candle_trigger=True, min_tp_pct=0.0022),
            QuantParameterSet(tp1_mult=0.75, tp2_mult=2.2, sl_mult=1.5, scale_pct=0.70, trend_sep=0.0005, pullback_max_atr=0.8, fee_cushion=0.0015, min_adx=20.0, use_candle_trigger=True, min_tp_pct=0.0022),
        ]

        leaderboard: List[SimulationResult] = []
        best_result: Optional[SimulationResult] = None
        iteration = 0

        while iteration < max_iterations:
            iteration += 1
            logger.info(f"\n--- [Iteration {iteration}/{max_iterations}] Testing {len(candidate_pool)} Candidates ---")

            iteration_results: List[SimulationResult] = []
            for idx, cand in enumerate(candidate_pool):
                res = self.evaluate_parameter_set(cand)
                iteration_results.append(res)
                logger.info(
                    f"  Cand #{idx+1}: WinRate={res.win_rate:4.1f}% | Trades/Day={res.trades_per_day:4.1f} (Tot={res.total_trades}) | "
                    f"PF={res.profit_factor:4.2f} | TP1={cand.tp1_mult:.2f} SL={cand.sl_mult:.2f} Sep={cand.trend_sep:.4f} -> Fitness={res.fitness_score:.1f}"
                )

            # Sort by fitness score descending
            iteration_results.sort(key=lambda x: x.fitness_score, reverse=True)
            top_cand = iteration_results[0]

            if best_result is None or top_cand.fitness_score > best_result.fitness_score:
                best_result = top_cand
                logger.info(f"  >>> [NEW BEST] WinRate={best_result.win_rate}% | Trades/Day={best_result.trades_per_day} | PF={best_result.profit_factor}")

            leaderboard.extend(iteration_results)
            leaderboard.sort(key=lambda x: x.fitness_score, reverse=True)
            leaderboard = leaderboard[:20]  # Keep top 20 on leaderboard

            # Check if convergence criteria are met
            if best_result.win_rate >= target_win_rate and best_result.trades_per_day >= min_trades_day:
                logger.info("\n" + "=" * 70)
                logger.info(f"[SUCCESS] Target Achieved at Iteration {iteration}!")
                logger.info(f"Win Rate: {best_result.win_rate}% (Target: >={target_win_rate}%)")
                logger.info(f"Trade Velocity: {best_result.trades_per_day} trades/day (Target: >={min_trades_day})")
                logger.info(f"Profit Factor: {best_result.profit_factor}")
                logger.info("=" * 70)
                break

            # Mutation & Evolutionary Refinement for next generation
            # Take top 3 performers and mutate parameters around them
            top_performers = leaderboard[:3]
            next_pool: List[QuantParameterSet] = []

            for r in top_performers:
                p = r.parameters
                t_day = r.trades_per_day

                # Retain elite parent
                next_pool.append(QuantParameterSet(**p))

                # Mutation 1: Finer scale-out and TP1 tuning
                next_pool.append(QuantParameterSet(
                    tp1_mult=round(max(0.40, min(1.0, p["tp1_mult"] + float(np.random.choice([-0.05, 0.05])))), 2),
                    tp2_mult=round(max(1.8, min(2.8, p["tp2_mult"] + float(np.random.choice([-0.1, 0.1])))), 2),
                    sl_mult=round(max(1.3, min(2.5, p["sl_mult"] + float(np.random.choice([-0.1, 0.1])))), 2),
                    scale_pct=round(max(0.65, min(0.85, p["scale_pct"] + float(np.random.choice([-0.05, 0.05])))), 2),
                    trend_sep=round(max(0.0002, min(0.0010, p["trend_sep"] * float(np.random.choice([0.85, 1.15])))), 4),
                    pullback_max_atr=round(max(0.4, min(1.2, p["pullback_max_atr"] + float(np.random.choice([-0.1, 0.1])))), 2),
                    fee_cushion=p["fee_cushion"],
                    min_adx=p["min_adx"],
                    use_candle_trigger=p["use_candle_trigger"],
                    min_tp_pct=p.get("min_tp_pct", 0.0018)
                ))

                # Mutation 2: Sensitivity shift (loosen/tighten trend separation to modulate trade velocity)
                next_pool.append(QuantParameterSet(
                    tp1_mult=p["tp1_mult"],
                    tp2_mult=p["tp2_mult"],
                    sl_mult=p["sl_mult"],
                    scale_pct=p["scale_pct"],
                    trend_sep=round(p["trend_sep"] * 0.80 if t_day < min_trades_day else p["trend_sep"] * 1.20, 4),
                    pullback_max_atr=round(min(1.2, p["pullback_max_atr"] + 0.15), 2),
                    fee_cushion=p["fee_cushion"],
                    min_adx=round(max(15.0, p["min_adx"] - 2.0), 1),
                    use_candle_trigger=p["use_candle_trigger"],
                    min_tp_pct=p.get("min_tp_pct", 0.0018)
                ))

                # Mutation 3: Exploration candidate
                next_pool.append(QuantParameterSet(
                    tp1_mult=round(float(np.random.uniform(0.45, 0.75)), 2),
                    tp2_mult=round(float(np.random.uniform(2.0, 2.6)), 2),
                    sl_mult=round(float(np.random.uniform(1.8, 2.5)), 2),
                    scale_pct=round(float(np.random.choice([0.70, 0.75, 0.80])), 2),
                    trend_sep=round(float(np.random.uniform(0.00025, 0.0006)), 4),
                    pullback_max_atr=round(float(np.random.uniform(0.6, 1.0)), 2),
                    fee_cushion=0.0015,
                    min_adx=20.0,
                    use_candle_trigger=True,
                    min_tp_pct=round(float(np.random.choice([0.0018, 0.0022])), 4)
                ))

            candidate_pool = next_pool[:12]

        # Save Best Parameters
        if best_result:
            os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
            with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
                json.dump(asdict(best_result), f, indent=2)
            logger.info(f"[AutoTuner] Converged optimal parameters saved to {OUTPUT_PATH}")

            # Save Leaderboard
            with open(LEADERBOARD_PATH, "w", encoding="utf-8") as f:
                json.dump([asdict(r) for r in leaderboard[:10]], f, indent=2)
            logger.info(f"[AutoTuner] Leaderboard persisted to {LEADERBOARD_PATH}")

        return best_result or leaderboard[0]


if __name__ == "__main__":
    tuner = AutoQuantOptimizer()
    best = tuner.run_optimization_loop(max_iterations=12, target_win_rate=70.0, min_trades_day=40.0)
    print("\n--- FINAL BEST CONVERGED SETUP ---")
    print(json.dumps(asdict(best), indent=2))
