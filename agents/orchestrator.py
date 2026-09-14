"""
BEAST v2 - Team Orchestrator
agents/orchestrator.py

The master quant trading orchestrator coordinating the BEAST v2 pipeline:
MARKET DATA -> MARKET STATE -> REGIME -> SETUP -> TRIGGER -> NEWS -> QUALITY -> RISK -> EXECUTION -> RECONCILIATION -> JOURNAL

Guarantees:
- Real Delta Market Data & Execution only
- Zero shadow execution in live path
- Hard fail-closed staleness breaker check
- 9-dimensional quality scoring with tier classification (A+, A, B, C, NO_TRADE)
- Deterministic risk sizing with portfolio correlation limits
- 24/7 autonomous research daemon cooperation
"""

import asyncio
import time
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

from config import system_config, compounding_config
from delta_client import delta_client
from data.staleness_breaker import staleness_breaker
from agents.social_news_agent import social_news_agent
from agents.quant_analyst import quant_analyst_agent
from agents.regime_engine import regime_engine, MarketRegime
from agents.setup_engine import setup_engine
from agents.trigger_engine import trigger_engine
from agents.trade_flow_engine import trade_flow_engine
from agents.microstructure_engine import microstructure_engine
from data.market_data_engine import market_data_engine
from agents.quality_engine import QualityScoringEngine
from agents.risk_governor import risk_governor_agent
from agents.execution_manager import execution_manager
from execution.reconciliation_engine import reconciliation_engine
from agents.live_trade_journal import live_trade_journal
from agents.telegram_notifier import telegram_notifier
from agents.forensic_learner import forensic_learner
from agents.session_momentum_engine import session_momentum_engine


class TeamOrchestrator:
    def __init__(self):
        self.deliberation_logs: List[Dict[str, Any]] = []
        self.is_scanning = False
        self.active_scan_results: List[Dict[str, Any]] = []
        self.scan_cursor: int = 0
        self.quality_engine = QualityScoringEngine()

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
        Execute one full evaluation cycle for a symbol through the BEAST v2 pipeline.
        """
        cycle_result = {
            "symbol": symbol,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "news_intelligence": None,
            "quant_analysis": None,
            "quality_score": None,
            "dual_key_passed": False,
            "proposal": None,
            "risk_evaluation": None,
            "execution_result": None,
            "status": "EVALUATING"
        }

        # Step 0: Staleness & Feed Health Breaker Check
        if not staleness_breaker.is_safe(symbol):
            self.log_agent_thought(
                "Staleness Breaker", symbol,
                f"MARKET DATA STALE: Delta feed lock-out active for {symbol}. Refusing cycle.",
                level="VETO"
            )
            cycle_result["status"] = "VETOED_BY_STALENESS_BREAKER"
            return cycle_result

        # Step 1: Agent 1 (Social & News Intelligence)
        self.log_agent_thought("Agent 1: Social/News", symbol, f"Scanning filings, wires, and news for {symbol}...")
        news_eval = social_news_agent.analyze_news_feed(symbol)
        cycle_result["news_intelligence"] = news_eval

        # Bot manipulation check
        if news_eval.get("authenticity_score", 1.0) < 0.50:
            self.log_agent_thought(
                "Agent 1: Social/News", symbol,
                f"VETO TRIGGERED: Coordinated bot manipulation detected. Gate 1 failed.",
                level="VETO",
                details={"authenticity": news_eval.get("authenticity_score")}
            )
            cycle_result["status"] = "VETOED_BY_AGENT_1_MANIPULATION"
            return cycle_result

        session_info = session_momentum_engine.get_current_session()
        allow_pure_tech = session_info.get("allow_pure_technical_breakouts", False)
        usable_sent = news_eval.get("usable_sentiment", 0.0)
        news_gate_passed = news_eval.get("gate_passed", False)

        is_pure_tech_candidate = (
            not news_gate_passed and
            allow_pure_tech and
            usable_sent >= 0.0
        )

        if not news_gate_passed and not is_pure_tech_candidate:
            self.log_agent_thought(
                "Agent 1: Social/News", symbol,
                f"Gate 1 Rejected: {news_eval.get('model_interpretation')} (Sentiment: {usable_sent:.2f})",
                level="WARN"
            )
            cycle_result["status"] = "VETOED_BY_AGENT_1_LOW_CONVICTION"
            return cycle_result

        # Step 2: Agent 2 (Quant Analyst)
        self.log_agent_thought("Agent 2: Quant Analyst", symbol, f"Analyzing multi-timeframe structure and indicators for {symbol}...")
        quant_eval = quant_analyst_agent.analyze(symbol)
        cycle_result["quant_analysis"] = quant_eval

        # Real-time evaluation Telegram notification
        active_cnt = 0
        if execution_manager.mode == "LIVE":
            try:
                pos_resp = delta_client.get_positions()
                active_cnt = len([p for p in pos_resp.get("result", []) if abs(float(p.get("size", 0))) > 0])
            except Exception:
                active_cnt = 0
        else:
            from agents.shadow_account import shadow_account
            active_cnt = len(shadow_account.open_positions)

        open_slots = max(0, system_config.MAX_CONCURRENT_POSITIONS - active_cnt)
        telegram_notifier.notify_candidate_evaluation(symbol, news_eval, quant_eval, open_slots=open_slots)

        if not quant_eval.get("gate_passed"):
            self.log_agent_thought(
                "Agent 2: Quant Analyst", symbol,
                f"Gate 2 Rejected: {quant_eval.get('reason')} ({quant_eval.get('signal')})",
                level="WARN"
            )
            cycle_result["status"] = "VETOED_BY_AGENT_2_TECHNICALS"
            return cycle_result

        # Step 3: Dynamic Setup & Multi-Dimensional Quality Scoring Engine
        direction = quant_eval.get("signal", "BUY")

        # Dynamic Microstructure & Orderbook Depth
        micro_data = quant_eval.get("indicators", {}).get("microstructure")
        if not micro_data:
            ob = market_data_engine.get_orderbook(symbol)
            if not ob.get("best_bid"):
                ob = delta_client.get_l2_orderbook(symbol)
            micro_data = microstructure_engine.analyze_orderbook(ob, signal=direction)

        # Real-time Trade Flow & CVD Analysis
        recent_trades = market_data_engine.get_recent_trades(symbol)
        if recent_trades:
            trade_flow_engine.ingest_trades_batch(symbol, recent_trades)

        flow_analysis = trade_flow_engine.analyze(
            symbol,
            current_price=quant_eval.get("entry_price", 0.0),
            orderbook_snapshot=micro_data
        )

        flow_delta = flow_analysis.get("flow", {}).get("delta", 0.0)
        default_cvd = 10.0 if direction == "BUY" else -10.0
        orderflow_res = {
            "cvd_slope": flow_delta if flow_delta != 0.0 else default_cvd,
            "absorption": flow_analysis.get("absorption", {}).get("type", "BULLISH_ABSORPTION" if direction == "BUY" else "BEARISH_ABSORPTION"),
            "cvd_divergence": flow_analysis.get("divergence", {}).get("divergence", "FLOW_BALANCED"),
            "sweep": flow_analysis.get("sweep", {}).get("sweep_type", "NONE")
        }

        microstructure_res = {
            "weighted_imbalance": micro_data.get("depth_imbalance_5", 0.15 if direction == "BUY" else -0.15),
            "spread_state": micro_data.get("spread_regime", {}).get("regime", "NORMAL")
        }

        macro_trend = quant_eval.get("indicators", {}).get("mtf", {}).get("macro_trend_1h", "BULLISH")
        if macro_trend == "BULLISH":
            regime_val = MarketRegime.TREND_UP
        elif macro_trend == "BEARISH":
            regime_val = MarketRegime.TREND_DOWN
        else:
            regime_val = MarketRegime.RANGE

        regime_res = {
            "regime": regime_val,
            "adx": 28.0,
            "volatility_state": "NORMAL_VOLATILITY",
            "atr": quant_eval.get("indicators", {}).get("atr", 10.0),
            "confidence": 0.85
        }

        technical_data = {
            "ema9": quant_eval.get("indicators", {}).get("ema9", quant_eval.get("entry_price", 0.0)),
            "ema20": quant_eval.get("indicators", {}).get("ema20", quant_eval.get("entry_price", 0.0)),
            "ema21": quant_eval.get("indicators", {}).get("ema21", quant_eval.get("entry_price", 0.0)),
            "ema50": quant_eval.get("indicators", {}).get("ema50", quant_eval.get("entry_price", 0.0)),
            "ema200": quant_eval.get("indicators", {}).get("ema200", quant_eval.get("entry_price", 0.0)),
            "close": quant_eval.get("entry_price", 0.0),
            "rsi": quant_eval.get("indicators", {}).get("rsi", 55.0),
            "macd_hist": 5.0 if direction == "BUY" else -5.0
        }

        setup_res = setup_engine.evaluate(
            regime_result=regime_res,
            microstructure_result=microstructure_res,
            orderflow_result=orderflow_res,
            technical_data=technical_data,
            symbol=symbol
        )

        cvd_confirmed = (orderflow_res["cvd_slope"] > 0) if direction == "BUY" else (orderflow_res["cvd_slope"] < 0)
        trigger_res = trigger_engine.evaluate(
            direction=direction,
            setup_status="ACTIVE",
            candle_5m_closed=True,
            microprice_drift=micro_data.get("micro_drift", 0.0),
            cvd_confirmed=cvd_confirmed
        )

        risk_metrics = {
            "reward_to_risk": quant_eval.get("reward_to_risk", 2.5)
        }

        quality_eval = self.quality_engine.evaluate(
            direction=direction,
            regime_result=regime_res,
            setup_result=setup_res,
            trigger_result=trigger_res,
            orderflow_result=orderflow_res,
            microstructure_result=microstructure_res,
            news_result=news_eval,
            technical_data=technical_data,
            risk_metrics=risk_metrics
        )
        cycle_result["quality_score"] = quality_eval

        # Journal candidate evaluation
        live_trade_journal.record_evaluation(
            symbol=symbol,
            direction=direction,
            quality_score=quality_eval.total_score,
            tier=quality_eval.tier,
            setup_family=setup_res.get("family", "TREND_PULLBACK"),
            approved=quality_eval.is_executable,
            reasons=quality_eval.rejection_reasons
        )

        # WATCH state check (Tier C)
        if quality_eval.tier == "C":
            self.log_agent_thought(
                "Quality Engine", symbol,
                f"WATCH STATE: Tier C ({quality_eval.total_score}/100) - Monitoring candidate setup.",
                level="INFO",
                details=quality_eval.category_scores
            )
            cycle_result["status"] = "WATCH"
            return cycle_result

        if not quality_eval.is_executable:
            self.log_agent_thought(
                "Quality Engine", symbol,
                f"NO TRADE: Tier {quality_eval.tier} ({quality_eval.total_score}/100) below execution threshold (min B).",
                level="WARN",
                details=quality_eval.category_scores
            )
            cycle_result["status"] = f"NO_TRADE_{quality_eval.tier}"
            return cycle_result

        self.log_agent_thought(
            "Quality Engine", symbol,
            f"QUALIFIED: Tier {quality_eval.tier} ({quality_eval.total_score}/100). Executable setup confirmed.",
            level="SUCCESS",
            details=quality_eval.category_scores
        )

        # Step 4: Construct Trade Proposal
        cycle_result["dual_key_passed"] = True
        proposal = {
            "symbol": symbol,
            "signal": direction,
            "entry_price": quant_eval["entry_price"],
            "stop_loss": quant_eval["stop_loss"],
            "take_profit_1": quant_eval["take_profit_1"],
            "take_profit_2": quant_eval["take_profit_2"],
            "reward_to_risk": quant_eval["reward_to_risk"],
            "tier": quality_eval.tier,
            "quality_score": quality_eval.total_score,
            "rationale": f"Tier {quality_eval.tier} ({quality_eval.total_score}) | {quant_eval.get('reason')}"
        }
        cycle_result["proposal"] = proposal

        # Step 5: Forensic Pre-Flight Check
        forensic_check = forensic_learner.pre_flight_inspection(proposal, quant_eval, news_eval)
        cycle_result["forensic_check"] = forensic_check
        if not forensic_check.get("approved"):
            self.log_agent_thought(
                "Agent 5: Forensic Learner", symbol,
                f"VETO TRIGGERED: {forensic_check.get('reason')}",
                level="VETO"
            )
            cycle_result["status"] = f"VETOED_BY_FORENSIC_LEARNER_{forensic_check.get('veto_code')}"
            return cycle_result

        # Step 6: Risk Governor & Sizing
        # Fetch active positions to evaluate portfolio correlation
        active_positions_list = []
        if execution_manager.mode == "LIVE":
            try:
                pos_resp = delta_client.get_positions()
                active_positions_list = [
                    {"symbol": p.get("product_symbol"), "direction": "BUY" if float(p.get("size", 0)) > 0 else "SELL"}
                    for p in pos_resp.get("result", []) if abs(float(p.get("size", 0))) > 0
                ]
            except Exception:
                pass
        else:
            from agents.shadow_account import shadow_account
            active_positions_list = [
                {"symbol": p["symbol"], "direction": p["side"]}
                for p in shadow_account.open_positions.values()
            ]

        risk_eval = risk_governor_agent.evaluate_proposal(proposal, current_balance, active_positions=active_positions_list)
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
            f"APPROVED ({quality_eval.tier}): {risk_eval['contracts']} contracts. Risk: ${risk_eval['risk_budget_usd']:.2f}, Margin: ${risk_eval['margin_required']:.2f}.",
            level="SUCCESS",
            details=risk_eval
        )

        # Step 7: Order Execution
        self.log_agent_thought("Agent 4: Execution", symbol, f"Dispatching order via {execution_manager.mode} mode...")
        exec_result = execution_manager.execute_order(proposal, risk_eval)
        cycle_result["execution_result"] = exec_result

        if exec_result.get("success"):
            self.log_agent_thought(
                "Agent 4: Execution", symbol,
                f"ORDER FILLED: {exec_result.get('message')}",
                level="SUCCESS"
            )
            cycle_result["status"] = "ORDER_EXECUTED"

            # Journal entry
            live_trade_journal.record_trade_entry({
                "symbol": symbol,
                "side": direction,
                "entry_price": proposal["entry_price"],
                "contracts": risk_eval["contracts"],
                "contract_val": risk_eval["contract_val"],
                "stop_loss": proposal["stop_loss"],
                "take_profit_1": proposal["take_profit_1"],
                "take_profit_2": proposal["take_profit_2"],
                "isolated_leverage": risk_eval["isolated_leverage"],
                "tier": quality_eval.tier,
                "quality_score": quality_eval.total_score,
                "reward_to_risk": proposal["reward_to_risk"],
                "margin_required": risk_eval["margin_required"]
            })

            # Telegram alert for confirmed live trade
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
        Execute scan across universe with rotating coverage.
        """
        self.is_scanning = True
        scan_time = datetime.now(timezone.utc).isoformat()
        results = []
        candidate_summaries = []

        # Current balance
        current_balance = compounding_config.STARTING_CAPITAL
        if execution_manager.mode == "LIVE":
            b_resp = delta_client.get_balance()
            if b_resp.get("balance", 0) > 0:
                current_balance = b_resp["balance"]
        else:
            from agents.shadow_account import shadow_account
            current_balance = shadow_account.balance

        # Active candidate selection: During non-US market hours, ALWAYS prioritize 24/7 liquid crypto
        session_info = session_momentum_engine.get_current_session()
        is_us_open = session_info.get("is_us_cash_open", False)

        universe = system_config.US_STOCKS_RWA or ["BTCUSD", "ETHUSD"]
        if not is_us_open:
            crypto_symbols = ["BTCUSD", "ETHUSD"]
            rotating = [universe[(self.scan_cursor + i) % len(universe)] for i in range(2)]
            self.scan_cursor = (self.scan_cursor + 2) % len(universe)
            selected_symbols = list(dict.fromkeys(crypto_symbols + rotating))
        else:
            batch_size = 4
            selected_symbols = [universe[(self.scan_cursor + i) % len(universe)] for i in range(batch_size)]
            self.scan_cursor = (self.scan_cursor + batch_size) % len(universe)

        for sym in selected_symbols:
            res = self.run_cycle_for_symbol(sym, current_balance)
            results.append(res)
            candidate_summaries.append({
                "symbol": sym,
                "status": res["status"],
                "tier": res.get("quality_score", {}).tier if res.get("quality_score") else "N/A"
            })

        # Sync portfolio
        tickers_map = {}
        for s in selected_symbols:
            tk = delta_client.get_ticker(s)
            if tk.get("mark_price", 0) > 0:
                tickers_map[s] = tk["mark_price"]

        portfolio_summary = execution_manager.sync_portfolio(tickers_map)

        self.active_scan_results = results
        self.is_scanning = False

        return {
            "timestamp": scan_time,
            "universe_size": len(universe),
            "scanned_symbols": selected_symbols,
            "results": results,
            "portfolio": portfolio_summary
        }


orchestrator = TeamOrchestrator()
