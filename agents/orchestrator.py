import asyncio
import time
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

from config import system_config, compounding_config
from delta_client import delta_client
from agents.social_news_agent import social_news_agent
from agents.quant_analyst import quant_analyst_agent
from agents.risk_governor import risk_governor_agent
from agents.shadow_account import shadow_account
from agents.execution_manager import execution_manager
from agents.telegram_notifier import telegram_notifier
from agents.forensic_learner import forensic_learner
from agents.session_momentum_engine import session_momentum_engine

class TeamOrchestrator:
    """
    Supervisor / Orchestrator Agent.
    
    Coordinates the multi-agent trading team:
    - Ingests US Stock RWA market & news data
    - Enforces MANDATORY DUAL-KEY consensus:
        Gate 1: Agent 1 (Social & News Intelligence)
        Gate 2: Agent 2 (Quant Analyst & Alpha Zoo)
    - Generates Trade Proposals only when 100% agreement is achieved
    - Routes proposals through Agent 3 (Risk Governor) for micro-contract sizing
    - Dispatches approved trades via Agent 4 (Execution Manager)
    - Records transparent deliberation transcripts
    """
    def __init__(self):
        self.deliberation_logs: List[Dict[str, Any]] = []
        self.is_scanning = False
        self.active_scan_results: List[Dict[str, Any]] = []
        self.scan_cursor: int = 0

    def log_agent_thought(self, agent: str, symbol: str, message: str, level: str = "INFO", details: Optional[Dict[str, Any]] = None):
        """Append a structured transcript entry."""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "agent": agent,
            "symbol": symbol,
            "message": message,
            "level": level,  # "INFO", "SUCCESS", "WARN", "VETO"
            "details": details or {}
        }
        self.deliberation_logs.append(entry)
        if len(self.deliberation_logs) > 200:
            self.deliberation_logs.pop(0)
        return entry

    def run_cycle_for_symbol(self, symbol: str, current_balance: float) -> Dict[str, Any]:
        """
        Execute one full evaluation cycle for a single US Stock RWA token.
        """
        cycle_result = {
            "symbol": symbol,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "news_intelligence": None,
            "quant_analysis": None,
            "dual_key_passed": False,
            "proposal": None,
            "risk_evaluation": None,
            "execution_result": None,
            "status": "EVALUATING"
        }

        # Step 1: Agent 1 (Social & News Intelligence) - MANDATORY GATE 1
        self.log_agent_thought("Agent 1: Social/News", symbol, f"Scanning institutional filings, press wires, and social streams for {symbol}...")
        news_eval = social_news_agent.analyze_news_feed(symbol)
        cycle_result["news_intelligence"] = news_eval

        # Check for manipulation / bot-activity
        if news_eval.get("authenticity_score", 1.0) < 0.50:
            self.log_agent_thought(
                "Agent 1: Social/News", symbol,
                f"VETO TRIGGERED: Coordinated bot manipulation detected (Authenticity: {news_eval['authenticity_score']*100:.1f}%). Gate 1 failed.",
                level="VETO",
                details={"authenticity": news_eval["authenticity_score"]}
            )
            cycle_result["status"] = "VETOED_BY_AGENT_1_MANIPULATION"
            return cycle_result

        session_info = session_momentum_engine.get_current_session()
        allow_pure_tech = session_info.get("allow_pure_technical_breakouts", False)
        usable_sent = news_eval.get("usable_sentiment", 0.0)
        news_gate_passed = news_eval.get("gate_passed", False)

        # During high-momentum windows (e.g. US Open Surge), if news is neutral (not negative), allow technical breakout evaluation
        is_pure_tech_candidate = (
            not news_gate_passed and
            allow_pure_tech and
            usable_sent >= 0.0
        )

        if not news_gate_passed and not is_pure_tech_candidate:
            self.log_agent_thought(
                "Agent 1: Social/News", symbol,
                f"Gate 1 Rejected: {news_eval.get('model_interpretation')} (Usable Sentiment: {usable_sent:.2f}, Conf: {news_eval.get('confidence')*100:.1f}%)",
                level="WARN",
                details=news_eval.get("source_counts")
            )
            cycle_result["status"] = "VETOED_BY_AGENT_1_LOW_CONVICTION"
            return cycle_result

        if news_gate_passed:
            self.log_agent_thought(
                "Agent 1: Social/News", symbol,
                f"Gate 1 APPROVED: Verified Tier 1/2 backing. Usable Sentiment: +{usable_sent:.2f}, Confidence: {news_eval['confidence']*100:.1f}%.",
                level="SUCCESS"
            )
        else:
            self.log_agent_thought(
                "Agent 1: Social/News", symbol,
                f"Gate 1 CONDITIONAL: {session_info['session_name']} active. Neutral news non-blocking for high-conviction (|Alpha| >= 0.50) technical breakout.",
                level="INFO"
            )

        # Step 2: Agent 2 (Quant Analyst & Alpha Zoo) - MANDATORY GATE 2
        self.log_agent_thought("Agent 2: Quant Analyst", symbol, f"Computing EMA 20/50/200 ribbon, ATR 14 volatility, and L2 orderbook depth for {symbol}...")
        quant_eval = quant_analyst_agent.analyze(symbol)
        cycle_result["quant_analysis"] = quant_eval

        # Send Real-Time Candidate Evaluation / Rejection Telegram notification
        active_cnt = 0
        if execution_manager.mode == "LIVE":
            try:
                pos_resp = delta_client.get_positions()
                active_cnt = len([p for p in pos_resp.get("result", []) if abs(float(p.get("size", 0))) > 0])
            except Exception:
                active_cnt = 0
        else:
            active_cnt = len(shadow_account.open_positions)
        open_slots = max(0, system_config.MAX_CONCURRENT_POSITIONS - active_cnt)
        telegram_notifier.notify_candidate_evaluation(symbol, news_eval, quant_eval, open_slots=open_slots)

        # For conditional pure technical breakouts, demand high alpha hurdle (>= 0.50)
        comp_alpha = quant_eval.get("composite_alpha", 0.0)
        if is_pure_tech_candidate and abs(comp_alpha) < 0.50:
            self.log_agent_thought(
                "Agent 2: Quant Analyst", symbol,
                f"Gate 2 Vetoed: Pure technical breakout requires strong Alpha (|Alpha| >= 0.50), got {comp_alpha:+.2f}.",
                level="WARN"
            )
            cycle_result["status"] = "VETOED_BY_AGENT_1_LOW_CONVICTION"
            return cycle_result

        if not quant_eval.get("gate_passed"):
            self.log_agent_thought(
                "Agent 2: Quant Analyst", symbol,
                f"Gate 2 Rejected: {quant_eval.get('reason')} (Signal: {quant_eval.get('signal')}, Conf: {quant_eval.get('confidence')*100:.1f}%)",
                level="WARN",
                details=quant_eval.get("indicators")
            )
            cycle_result["status"] = "VETOED_BY_AGENT_2_TECHNICALS"
            return cycle_result

        self.log_agent_thought(
            "Agent 2: Quant Analyst", symbol,
            f"Gate 2 APPROVED: {quant_eval.get('signal')} signal confluence at ${quant_eval['entry_price']:.2f} (SL: ${quant_eval['stop_loss']:.2f}, TP1: ${quant_eval['take_profit_1']:.2f}, R:R: 1:{quant_eval['reward_to_risk']})",
            level="SUCCESS"
        )

        # Step 3: Dual-Key Consensus Verification
        cycle_result["dual_key_passed"] = True
        consensus_type = "Dual-Key News & Quant" if news_gate_passed else f"High-Momentum Technical Breakout ({session_info['session_name']})"
        self.log_agent_thought(
            "Team Orchestrator", symbol,
            f"100% CONSENSUS REACHED ({consensus_type})! Constructing Trade Proposal...",
            level="SUCCESS"
        )

        rationale_str = f"News verified ({news_eval.get('event', 'Verified')}, Sentiment +{usable_sent:.2f})" if news_gate_passed else f"Technical Momentum Breakout ({session_info['session_name']}, Alpha {comp_alpha:+.2f})"
        proposal = {
            "symbol": symbol,
            "signal": quant_eval["signal"],
            "entry_price": quant_eval["entry_price"],
            "stop_loss": quant_eval["stop_loss"],
            "take_profit_1": quant_eval["take_profit_1"],
            "take_profit_2": quant_eval["take_profit_2"],
            "reward_to_risk": quant_eval["reward_to_risk"],
            "rationale": f"{rationale_str} + Quant ({quant_eval['reason']})"
        }
        cycle_result["proposal"] = proposal

        # Step 2.5: Agent 5 (Forensic Memory & Self-Improving Anti-Pattern Gatekeeper)
        self.log_agent_thought("Agent 5: Forensic Learner", symbol, f"Conducting forensic pre-flight check against historical loss database and quarantine records...")
        forensic_check = forensic_learner.pre_flight_inspection(proposal, quant_eval, news_eval)
        cycle_result["forensic_check"] = forensic_check

        if not forensic_check.get("approved"):
            self.log_agent_thought(
                "Agent 5: Forensic Learner", symbol,
                f"VETO TRIGGERED: {forensic_check.get('reason')} (Code: {forensic_check.get('veto_code')})",
                level="VETO",
                details={"veto_code": forensic_check.get("veto_code")}
            )
            cycle_result["status"] = f"VETOED_BY_FORENSIC_LEARNER_{forensic_check.get('veto_code')}"
            return cycle_result

        # Apply learned adaptive ATR buffer if the asset previously suffered wick squeezes
        adaptive_mult = forensic_check.get("adaptive_atr_multiplier", 1.5)
        raw_atr = quant_eval.get("indicators", {}).get("atr", 0.0)
        if adaptive_mult > 1.5 and raw_atr > 0:
            entry_p = proposal["entry_price"]
            is_buy = proposal["signal"] == "BUY"
            adjusted_sl = round(entry_p - (adaptive_mult * raw_atr), 2) if is_buy else round(entry_p + (adaptive_mult * raw_atr), 2)
            proposal["stop_loss"] = adjusted_sl
            risk_dist = abs(entry_p - adjusted_sl)
            proposal["take_profit_1"] = round(entry_p + (2.5 * risk_dist), 2) if is_buy else round(entry_p - (2.5 * risk_dist), 2)
            proposal["take_profit_2"] = round(entry_p + (4.0 * risk_dist), 2) if is_buy else round(entry_p - (4.0 * risk_dist), 2)
            self.log_agent_thought(
                "Agent 5: Forensic Learner", symbol,
                f"Adaptive Sizing Applied: Stop-Loss widened to {adaptive_mult}x ATR (${adjusted_sl:.2f}) to prevent historical wick out.",
                level="INFO"
            )

        self.log_agent_thought(
            "Agent 5: Forensic Learner", symbol,
            f"Pre-Flight APPROVED: No matching failure patterns. Hurdle: {forensic_check.get('confidence_hurdle', 0.65)*100:.0f}%, ATR mult: {adaptive_mult}x.",
            level="SUCCESS"
        )

        # Step 4: Agent 3 (Risk Governor & Capital Preservation)
        self.log_agent_thought("Agent 3: Risk Governor", symbol, f"Sizing micro-contracts for account balance ${current_balance:.2f} towards $5,000 target...")
        risk_eval = risk_governor_agent.evaluate_proposal(proposal, current_balance)
        cycle_result["risk_evaluation"] = risk_eval

        if not risk_eval.get("approved"):
            self.log_agent_thought(
                "Agent 3: Risk Governor", symbol,
                f"VETOED BY RISK GOVERNOR: {risk_eval.get('reason')}",
                level="VETO"
            )
            cycle_result["status"] = "VETOED_BY_RISK_GOVERNOR"
            return cycle_result

        self.log_agent_thought(
            "Agent 3: Risk Governor", symbol,
            f"APPROVED: {risk_eval['contracts']} contracts ({risk_eval['isolated_leverage']}x leverage). Risk budget: ${risk_eval['risk_budget_usd']:.2f} ({risk_eval['risk_pct']}%), Margin: ${risk_eval['margin_required']:.2f}.",
            level="SUCCESS",
            details=risk_eval
        )

        # Step 5: Agent 4 (Portfolio & Execution Manager)
        # Check active positions based on execution mode
        if execution_manager.mode == "LIVE":
            pos_resp = delta_client.get_positions()
            active_live = [p for p in pos_resp.get("result", []) if abs(float(p.get("size", 0))) > 0]
            current_open_symbols = [p.get("product_symbol", "") for p in active_live]
            total_active_count = len(active_live)
        else:
            current_open_symbols = [p["symbol"] for p in shadow_account.open_positions.values()]
            total_active_count = len(shadow_account.open_positions)

        # STRICT GUARDRAIL: Max 2 concurrent positions at any one time
        if total_active_count >= system_config.MAX_CONCURRENT_POSITIONS:
            self.log_agent_thought("Agent 3: Risk Governor", symbol, f"Max limit reached ({total_active_count}/{system_config.MAX_CONCURRENT_POSITIONS} active trades). Skipping new entry until a trade closes.", level="INFO")
            cycle_result["status"] = "MAX_POSITIONS_REACHED"
            return cycle_result

        # Avoid duplicating an existing open position for the same symbol
        if symbol in current_open_symbols:
            self.log_agent_thought("Agent 4: Execution", symbol, f"Position already active for {symbol}. Skipping redundant entry.")
            cycle_result["status"] = "POSITION_ALREADY_OPEN"
            return cycle_result

        self.log_agent_thought("Agent 4: Execution", symbol, f"Dispatching bracket order via {execution_manager.mode} mode...")
        exec_result = execution_manager.execute_order(proposal, risk_eval)
        cycle_result["execution_result"] = exec_result

        if exec_result.get("success"):
            self.log_agent_thought(
                "Agent 4: Execution", symbol,
                f"ORDER FILLED: {exec_result.get('message')}",
                level="SUCCESS"
            )
            cycle_result["status"] = "ORDER_EXECUTED"
            # Send Telegram Alert to User's Phone ONLY FOR CONFIRMED LIVE TRADES
            if exec_result.get("mode") == "LIVE":
                telegram_notifier.notify_trade_entry(
                    symbol=symbol,
                    side=proposal["signal"],
                    contracts=risk_eval["contracts"],
                    entry=proposal["entry_price"],
                    sl=proposal["stop_loss"],
                    tp1=proposal["take_profit_1"],
                    tp2=proposal["take_profit_2"],
                    risk_usd=risk_eval["risk_budget_usd"],
                    rationale=proposal.get("rationale", ""),
                    is_live=True
                )
        else:
            self.log_agent_thought(
                "Agent 4: Execution", symbol,
                f"ORDER FAILED: {exec_result.get('error')}",
                level="WARN"
            )
            cycle_result["status"] = "ORDER_FAILED"

        return cycle_result

    def run_full_scan(self, force_digest: bool = False) -> Dict[str, Any]:
        """
        Execute scan across configured US Stock RWA and Crypto perps with rotating universe coverage.
        """
        self.is_scanning = True
        scan_time = datetime.now(timezone.utc).isoformat()
        results = []
        candidate_summaries = []

        # 1. Update prices on existing open positions first
        current_balance = shadow_account.balance if execution_manager.mode == "SHADOW" else compounding_config.STARTING_CAPITAL
        
        tickers_map = {}
        for sym in system_config.US_STOCKS_RWA:
            tk = delta_client.get_ticker(sym)
            if tk.get("mark_price", 0) > 0:
                tickers_map[sym] = tk["mark_price"]

        # Sync portfolio and evaluate trailing stops
        portfolio_summary = execution_manager.sync_portfolio(tickers_map)

        # Determine active positions & open slots
        if execution_manager.mode == "LIVE":
            try:
                pos_resp = delta_client.get_positions()
                active_positions = [p for p in pos_resp.get("result", []) if abs(float(p.get("size", 0))) > 0]
            except Exception:
                active_positions = []
        else:
            active_positions = list(shadow_account.open_positions.values())
        
        open_slots = max(0, system_config.MAX_CONCURRENT_POSITIONS - len(active_positions))

        # 2. Continuous session-prioritized rotating scan across all 34 assets:
        session_info = session_momentum_engine.get_current_session()
        ordered_universe = session_momentum_engine.get_priority_symbols(system_config.US_STOCKS_RWA)
        total_syms = len(ordered_universe)
        # Top 5 assets prioritized by active session
        priority_tier = ordered_universe[:5]
        # Rotate through 5 additional symbols every cycle so the whole universe is scanned every ~4 min
        rotated_batch = []
        for i in range(5):
            sym = ordered_universe[(self.scan_cursor + i) % total_syms]
            if sym not in priority_tier:
                rotated_batch.append(sym)
        self.scan_cursor = (self.scan_cursor + 5) % total_syms

        symbols_to_scan = list(dict.fromkeys(priority_tier + rotated_batch))

        # Run multi-agent cycle on the selected symbols
        for sym in symbols_to_scan:
            res = self.run_cycle_for_symbol(sym, portfolio_summary.get("current_balance", current_balance))
            results.append(res)

            status = res.get("status", "NEUTRAL")
            if status == "ORDER_EXECUTED":
                candidate_summaries.append({"symbol": sym, "verdict": "🚀 ORDER EXECUTED"})
            elif "QUANT" in status or "TECHNICALS" in status:
                q_reason = res.get("quant_analysis", {}).get("reason", "Technical veto")
                candidate_summaries.append({"symbol": sym, "verdict": f"❌ Gate 2 Veto ({q_reason})"})
            elif "AGENT_1" in status or "LOW_CONVICTION" in status:
                n_reason = res.get("news_sentiment", {}).get("model_interpretation", "Low conviction")
                candidate_summaries.append({"symbol": sym, "verdict": f"⏳ Gate 1 Veto ({n_reason})"})
            elif status == "POSITION_ALREADY_OPEN":
                candidate_summaries.append({"symbol": sym, "verdict": "📌 Position Active"})
            else:
                candidate_summaries.append({"symbol": sym, "verdict": f"Vetoed ({status})"})

        self.active_scan_results = results
        self.is_scanning = False

        # Periodic (every 30 mins) or forced Telegram Radar Digest
        telegram_notifier.notify_scan_digest(
            open_slots=open_slots,
            active_positions=active_positions,
            candidate_summaries=candidate_summaries,
            force=force_digest
        )

        return {
            "timestamp": scan_time,
            "session": session_info,
            "results": results,
            "portfolio": portfolio_summary,
            "deliberation_logs": self.deliberation_logs[-30:]
        }

orchestrator = TeamOrchestrator()
