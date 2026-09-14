"""
BEAST v2 - Candidate Strategy Ranker
research/candidate_ranker.py

Ranks strategy improvements by combining:
- Out-Of-Sample (OOS) Profit Factor
- Walk-Forward Efficiency (WFE)
- Stress & Monte Carlo Resilience Score
- Risk-adjusted return (Sharpe / Sortino)

Maintains persistent leaderboard in data/research_leaderboard.json.
"""

import os
import json
import time
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, asdict


@dataclass
class StrategyCandidate:
    candidate_id: str
    parameter_set: Dict[str, Any]
    oos_profit_factor: float
    wfe_ratio: float
    resilience_score: float
    sharpe_ratio: float
    win_rate: float
    composite_score: float
    passed_all_gates: bool
    evaluated_at: str


class CandidateRanker:
    def __init__(self, leaderboard_path: str = "data/research_leaderboard.json"):
        self.leaderboard_path = leaderboard_path
        os.makedirs(os.path.dirname(self.leaderboard_path), exist_ok=True)

    def compute_composite_score(
        self,
        oos_pf: float,
        wfe: float,
        resilience: float,
        sharpe: float
    ) -> float:
        """
        Calculates composite fitness score (0 - 100).
        """
        pf_score = min(100.0, max(0.0, (oos_pf - 1.0) * 50.0))
        wfe_score = min(100.0, max(0.0, wfe * 100.0))
        res_score = min(100.0, max(0.0, resilience))
        sharpe_score = min(100.0, max(0.0, sharpe * 35.0))

        total = (
            (pf_score * 0.35) +
            (wfe_score * 0.25) +
            (res_score * 0.20) +
            (sharpe_score * 0.20)
        )
        return round(max(0.0, min(100.0, total)), 2)

    def rank_and_save(self, candidates: List[Dict[str, Any]]) -> List[StrategyCandidate]:
        ranked = []
        now_str = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())

        for idx, c in enumerate(candidates):
            params = c.get("parameter_set", {})
            oos_pf = float(c.get("oos_profit_factor", 1.0))
            wfe = float(c.get("wfe_ratio", 0.5))
            resilience = float(c.get("resilience_score", 60.0))
            sharpe = float(c.get("sharpe_ratio", 1.0))
            wr = float(c.get("win_rate", 50.0))
            passed = bool(c.get("passed_all_gates", oos_pf >= 1.2 and wfe >= 0.5))

            score = self.compute_composite_score(oos_pf, wfe, resilience, sharpe)

            ranked.append(StrategyCandidate(
                candidate_id=f"CAND_{idx+1}_{int(time.time())}",
                parameter_set=params,
                oos_profit_factor=round(oos_pf, 2),
                wfe_ratio=round(wfe, 2),
                resilience_score=round(resilience, 1),
                sharpe_ratio=round(sharpe, 2),
                win_rate=round(wr, 1),
                composite_score=score,
                passed_all_gates=passed,
                evaluated_at=now_str
            ))

        ranked.sort(key=lambda x: (x.passed_all_gates, x.composite_score), reverse=True)

        # Save to disk
        try:
            with open(self.leaderboard_path, "w", encoding="utf-8") as f:
                json.dump([asdict(r) for r in ranked], f, indent=2)
        except Exception as e:
            print(f"[CandidateRanker] Error saving leaderboard: {e}")

        return ranked

    def get_leaderboard(self) -> List[Dict[str, Any]]:
        if not os.path.exists(self.leaderboard_path):
            return []
        try:
            with open(self.leaderboard_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []

    def get_top_candidate(self) -> Optional[Dict[str, Any]]:
        lb = self.get_leaderboard()
        return lb[0] if lb else None


candidate_ranker = CandidateRanker()
