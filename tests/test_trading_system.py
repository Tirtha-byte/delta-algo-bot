import os
os.environ["TESTING"] = "1"
import unittest
from agents.social_news_agent import social_news_agent
from agents.quant_analyst import quant_analyst_agent
from agents.risk_governor import risk_governor_agent
from agents.shadow_account import shadow_account
from agents.orchestrator import orchestrator
from config import compounding_config, system_config

class TestTradingSystem(unittest.TestCase):

    def test_agent_1_tiering_and_epistemic_partitioning(self):
        """Verify Agent 1 partitions Tier 1 (SEC/IR), Tier 2 (News), and Tier 3 (Social)."""
        mock_feed = [
            {"source": "sec.gov", "text": "Form 8-K: Record revenue confirmed", "sentiment": 0.90, "is_sec_filing": True},
            {"source": "reuters.com", "text": "Reuters: Demand accelerates across data centers", "sentiment": 0.80, "is_verified_press": True},
            {"source": "x.com", "text": "To the moon! Buying calls", "sentiment": 0.95, "engagement": 100, "account_age_days": 500}
        ]
        res = social_news_agent.analyze_news_feed("NVDAXUSD", mock_feed)
        
        self.assertEqual(len(res["facts"]), 1)
        self.assertEqual(res["facts"][0]["type"], "FACT")
        self.assertEqual(len(res["reports"]), 1)
        self.assertEqual(res["reports"][0]["type"], "REPORT")
        self.assertEqual(len(res["social_claims"]), 1)
        self.assertEqual(res["social_claims"][0]["type"], "SOCIAL_CLAIM")
        self.assertTrue(res["gate_passed"])
        self.assertGreater(res["usable_sentiment"], 0.3)

    def test_agent_1_bot_manipulation_veto(self):
        """Verify Agent 1 vetoes trades when bot-like spam or young accounts dominate."""
        bot_feed = [
            # 10 identical copy-paste claims from fresh accounts (< 30 days old)
            {"source": "x.com", "text": "BUY NVDA PUMP NOW!!!", "sentiment": 0.99, "account_age_days": 2},
            {"source": "x.com", "text": "BUY NVDA PUMP NOW!!!", "sentiment": 0.99, "account_age_days": 3},
            {"source": "x.com", "text": "BUY NVDA PUMP NOW!!!", "sentiment": 0.99, "account_age_days": 1},
            {"source": "x.com", "text": "BUY NVDA PUMP NOW!!!", "sentiment": 0.99, "account_age_days": 4},
        ]
        res = social_news_agent.analyze_news_feed("NVDAXUSD", bot_feed)
        self.assertLess(res["authenticity_score"], 0.60)
        self.assertFalse(res["gate_passed"])
        self.assertIn("VETOED", res["model_interpretation"])

    def test_agent_2_quant_indicators(self):
        """Verify Quant Analyst calculates Pillar A indicators and Pillar B Qlib-inspired factors."""
        import time
        # Generate 40 synthetic candle records with rising price and volume
        candles = []
        base_price = 200.0
        for i in range(40):
            p = base_price + (i * 0.5)
            candles.append({
                "time": 1700000000 + (i * 900),
                "open": p - 0.2,
                "high": p + 0.8,
                "low": p - 0.4,
                "close": p,
                "volume": 1000 + i * 50
            })
        
        # Benchmark execution latency
        t0 = time.perf_counter()
        ind = quant_analyst_agent._calculate_indicators(candles)
        elapsed_ms = (time.perf_counter() - t0) * 1000

        # Pillar A Foundation checks
        self.assertTrue(ind["valid"])
        self.assertEqual(ind["trend_alignment"], "BULLISH")
        self.assertGreater(ind["rsi"], 0.0)
        self.assertGreater(ind["atr"], 0.0)

        # Pillar B Qlib Alpha Factor checks
        factors = ind["factors"]
        self.assertIn("corr_pv", factors)
        self.assertIn("kmid2", factors)
        self.assertIn("roc_composite", factors)
        self.assertIn("std_norm", factors)
        self.assertIn("ma_ratio_20", factors)

        # In an uptrend with rising volume, corr_pv should be positive
        self.assertGreater(factors["corr_pv"], 0.0)
        self.assertGreater(factors["roc_composite"], 0.0)

        # Synthesis & Composite Alpha check
        alpha, quant_conf, contrib = quant_analyst_agent._compute_composite_alpha(ind, imbalance=0.15)
        self.assertGreaterEqual(alpha, -1.0)
        self.assertLessEqual(alpha, 1.0)
        self.assertGreater(alpha, 0.3)  # Strong upward trend with volume yields bullish alpha
        self.assertGreaterEqual(quant_conf, 0.60)
        self.assertLessEqual(quant_conf, 0.95)

    def test_agent_2_pv_correlation_is_additive_not_veto(self):
        """Verify negative price-volume correlation reduces alpha score but does NOT trigger a hard veto."""
        candles = []
        base_price = 200.0
        # Price rising but volume falling (bearish divergence/absorption)
        for i in range(40):
            p = base_price + (i * 0.5)
            vol = max(100, 5000 - i * 100)
            candles.append({
                "time": 1700000000 + (i * 900),
                "open": p - 0.2, "high": p + 0.8, "low": p - 0.4,
                "close": p, "volume": vol
            })
        ind = quant_analyst_agent._calculate_indicators(candles)
        factors = ind["factors"]
        self.assertLess(factors["corr_pv"], 0.0)  # Negative PV correlation confirmed

        # Compute alpha: negative PV correlation should reduce composite alpha without crashing
        alpha, quant_conf, contrib = quant_analyst_agent._compute_composite_alpha(ind, imbalance=0.0)
        self.assertLess(contrib["pv_corr"], 0.0)  # Contributes negatively
        # But composite alpha remains a continuous valid value in [-1, 1]
        self.assertGreaterEqual(alpha, -1.0)
        self.assertLessEqual(alpha, 1.0)

    def test_agent_2_configurable_alpha_threshold(self):
        """Verify configurable ALPHA_THRESHOLD correctly gates trade decisions across boundaries."""
        ind = {
            "valid": True, "trend_alignment": "BULLISH", "atr": 2.5, "rsi": 55.0,
            "factors": {"roc_composite": 0.02, "corr_pv": 0.6, "kmid2": 0.3, "ma_ratio_20": 0.01}
        }
        alpha, quant_conf, _ = quant_analyst_agent._compute_composite_alpha(ind, imbalance=0.1)
        
        # Test lower threshold: should pass
        quant_analyst_agent.alpha_threshold = 0.30
        is_pass_low = alpha >= quant_analyst_agent.alpha_threshold
        self.assertTrue(is_pass_low)

        # Test extreme high threshold: should filter out
        quant_analyst_agent.alpha_threshold = 0.95
        is_pass_high = alpha >= quant_analyst_agent.alpha_threshold
        self.assertFalse(is_pass_high)

        # Reset to default
        quant_analyst_agent.alpha_threshold = 0.45

    def test_agent_3_risk_micro_contract_sizing(self):
        """Verify Risk Governor strictly limits risk to <= $1.50 (2.5%) on $60 capital."""
        proposal = {
            "symbol": "NVDAXUSD",
            "signal": "BUY",
            "entry_price": 220.0,
            "stop_loss": 215.0,  # $5 price risk
            "take_profit_1": 235.0,
            "reward_to_risk": 3.0
        }
        res = risk_governor_agent.evaluate_proposal(proposal, current_balance=60.0)
        self.assertTrue(res["approved"])
        
        # Max risk budget on $60 is $1.50
        self.assertLessEqual(res["risk_budget_usd"], 1.55)
        # Margin required must not exceed 45% per position ($27.00 on $60)
        self.assertLessEqual(res["margin_required"], 60.0 * compounding_config.MAX_MARGIN_PER_POSITION_PCT)
        # Cushion reserve must equal 10% of account ($6.00 on $60)
        self.assertEqual(res["cushion_reserve_usd"], 6.0)
        self.assertEqual(res["cushion_pct"], 10.0)
        # NVDA is high-volatility tier -> 4x
        self.assertEqual(res["isolated_leverage"], 4)

        # SPY is low-volatility index tier -> 7x
        spy_res = risk_governor_agent.evaluate_proposal({
            "symbol": "SPYXUSD", "signal": "BUY", "entry_price": 500.0,
            "stop_loss": 495.0, "take_profit_1": 515.0, "reward_to_risk": 3.0
        }, current_balance=60.0)
        self.assertEqual(spy_res["isolated_leverage"], 7)

        # AMZN is medium-beta tech tier -> 5x
        amzn_res = risk_governor_agent.evaluate_proposal({
            "symbol": "AMZNXUSD", "signal": "BUY", "entry_price": 180.0,
            "stop_loss": 175.0, "take_profit_1": 195.0, "reward_to_risk": 3.0
        }, current_balance=60.0)
        self.assertEqual(amzn_res["isolated_leverage"], 5)

    def test_10_pct_cushion_and_90_pct_allocation(self):
        """Verify exactly 10% balance is held as cushion and rest is divided between 2 trades (45.0% margin each)."""
        balance = 60.0
        # Trade 1: ETHUSD
        prop_eth = {
            "symbol": "ETHUSD", "signal": "SELL", "entry_price": 2400.0,
            "stop_loss": 2420.0, "take_profit_1": 2320.0, "reward_to_risk": 4.0
        }
        res_eth = risk_governor_agent.evaluate_proposal(prop_eth, current_balance=balance)
        self.assertTrue(res_eth["approved"])
        self.assertEqual(res_eth["cushion_reserve_usd"], balance * 0.10)
        self.assertEqual(res_eth["max_margin_budget_usd"], balance * 0.45)
        self.assertLessEqual(res_eth["margin_required"], balance * 0.45)

        # Trade 2: NVDAXUSD
        prop_nvda = {
            "symbol": "NVDAXUSD", "signal": "BUY", "entry_price": 220.0,
            "stop_loss": 216.0, "take_profit_1": 232.0, "reward_to_risk": 3.0
        }
        res_nvda = risk_governor_agent.evaluate_proposal(prop_nvda, current_balance=balance)
        self.assertTrue(res_nvda["approved"])
        self.assertLessEqual(res_nvda["margin_required"], balance * 0.45)

        # Combined two trades: Total margin must not exceed 90% ($54.00), leaving >= 10% ($6.00) free cushion
        total_margin = res_eth["margin_required"] + res_nvda["margin_required"]
        self.assertLessEqual(total_margin, balance * 0.90)
        remaining_free_cushion = balance - total_margin
        self.assertGreaterEqual(remaining_free_cushion, balance * 0.10)

    def test_shadow_account_bracket_and_trailing_stop(self):
        """Verify Shadow Account opens position and moves stop to breakeven once TP1 is reached."""
        test_shadow = shadow_account.__class__(initial_balance=60.0, data_path="data/test_shadow.json")
        test_shadow.balance = 60.0
        test_shadow.open_positions = {}
        pos = test_shadow.open_position(
            symbol="NVDAXUSD",
            side="BUY",
            contracts=10,
            entry_price=220.0,
            stop_loss=215.0,
            take_profit_1=230.0,
            take_profit_2=240.0,
            contract_val=0.01,
            leverage=4,
            rationale="Unit test long"
        )
        pos_id = pos["id"]
        
        # 1. Price moves to 232.0 (exceeds TP1 of 230.0)
        test_shadow.update_market_prices({"NVDAXUSD": 232.0})
        active_pos = test_shadow.open_positions[pos_id]
        self.assertTrue(active_pos["tp1_hit"])
        self.assertTrue(active_pos["sl_at_breakeven"])
        self.assertEqual(active_pos["stop_loss"], 220.0)  # Moved to breakeven!

        # 2. Price pulls back to 220.0 (touches breakeven stop)
        closed = test_shadow.update_market_prices({"NVDAXUSD": 219.9})
        self.assertEqual(len(closed), 1)
        self.assertEqual(closed[0]["close_reason"], "BREAKEVEN_STOP")
        self.assertGreaterEqual(test_shadow.balance, 59.9)

    def test_mtf_macro_veto_blocks_counter_trend(self):
        """Verify 1-Hour Macro Trend ribbon vetoes counter-trend trades."""
        # 1. Generate 250 synthetic 1h candles in a strong downtrend
        bear_candles = []
        for i in range(250):
            p = 300.0 - (i * 0.5)
            bear_candles.append({
                "time": 1700000000 + (i * 3600),
                "open": p + 0.2, "high": p + 0.5, "low": p - 0.5, "close": p, "volume": 1000
            })
        macro_bear = quant_analyst_agent._analyze_1h_macro(bear_candles)
        self.assertEqual(macro_bear["macro_trend"], "BEARISH")
        self.assertLess(macro_bear["close_1h"], macro_bear["ema50_1h"])
        self.assertLess(macro_bear["ema50_1h"], macro_bear["ema200_1h"])

        # 2. Generate 250 synthetic 1h candles in a strong uptrend
        bull_candles = []
        for i in range(250):
            p = 100.0 + (i * 0.5)
            bull_candles.append({
                "time": 1700000000 + (i * 3600),
                "open": p - 0.2, "high": p + 0.5, "low": p - 0.5, "close": p, "volume": 1000
            })
        macro_bull = quant_analyst_agent._analyze_1h_macro(bull_candles)
        self.assertEqual(macro_bull["macro_trend"], "BULLISH")
        self.assertGreater(macro_bull["close_1h"], macro_bull["ema50_1h"])
        self.assertGreater(macro_bull["ema50_1h"], macro_bull["ema200_1h"])

    def test_5m_structural_stop_placement(self):
        """Verify 5-minute structural swing pivot stop placement with ATR clamp bounds."""
        candles_5m = [
            {"time": 1700000000 + i*300, "open": 220 + (i%3), "high": 222 + (i%3), "low": 218 + (i%3), "close": 221 + (i%3), "volume": 500}
            for i in range(20)
        ]
        atr_15m = 3.0
        entry = 222.0

        # BUY: Stop placed below swing low
        buy_stop = quant_analyst_agent._find_5m_structural_stop(candles_5m, "BUY", entry, atr_15m)
        self.assertIn(buy_stop["stop_type"], ["STRUCTURAL_SWING_LOW", "MIN_BOUNDED_PIVOT", "MAX_BOUNDED_PIVOT"])
        self.assertLess(buy_stop["stop_loss"], entry)
        # Risk distance must respect [0.8*ATR, 2.5*ATR] = [2.4, 7.5]
        self.assertGreaterEqual(buy_stop["risk_dist"], 0.8 * atr_15m - 0.01)
        self.assertLessEqual(buy_stop["risk_dist"], 2.5 * atr_15m + 0.01)

        # SELL: Stop placed above swing high
        sell_stop = quant_analyst_agent._find_5m_structural_stop(candles_5m, "SELL", entry, atr_15m)
        self.assertIn(sell_stop["stop_type"], ["STRUCTURAL_SWING_HIGH", "MIN_BOUNDED_PIVOT", "MAX_BOUNDED_PIVOT"])
        self.assertGreater(sell_stop["stop_loss"], entry)
        self.assertGreaterEqual(sell_stop["risk_dist"], 0.8 * atr_15m - 0.01)
        self.assertLessEqual(sell_stop["risk_dist"], 2.5 * atr_15m + 0.01)

    def test_hybrid_leverage_and_asset_whitelist(self):
        """Verify BTC, ETH, and US Equities are active with correct Hybrid Volatility Leverage tiers."""
        # 1. BTC & ETH are in active universe
        self.assertIn("BTCUSD", system_config.US_STOCKS_RWA)
        self.assertIn("ETHUSD", system_config.US_STOCKS_RWA)
        
        # 2. Hybrid Volatility Leverage Tiers:
        # Tier 1 (High Beta Tech): 4x
        self.assertEqual(compounding_config.VOLATILITY_LEVERAGE_MAP["NVDAXUSD"], 4)
        self.assertEqual(compounding_config.VOLATILITY_LEVERAGE_MAP["TSLAXUSD"], 4)
        # Tier 2 (Crypto Blue-Chips): 5x
        self.assertEqual(compounding_config.VOLATILITY_LEVERAGE_MAP["BTCUSD"], 5)
        self.assertEqual(compounding_config.VOLATILITY_LEVERAGE_MAP["ETHUSD"], 5)
        # Tier 3 (Broad Indices / Low Vol): 7x
        self.assertEqual(compounding_config.VOLATILITY_LEVERAGE_MAP["SPYXUSD"], 7)
        self.assertEqual(compounding_config.VOLATILITY_LEVERAGE_MAP["QQQXUSD"], 7)

        # 3. Random unvetted meme coins are excluded
        excluded_set = {"DOGEUSD", "SHIBUSD", "PEPEUSD", "WIFUSD"}
        self.assertTrue(set(system_config.US_STOCKS_RWA).isdisjoint(excluded_set))

    def test_daily_drawdown_persistence(self):
        """Verify daily drawdown state persists to disk across restarts."""
        risk_governor_agent._save_daily_start_balance(60.0)
        self.assertTrue(os.path.exists(risk_governor_agent.state_file))
        
        # Instantiate a new governor to verify state reload from disk
        from agents.risk_governor import RiskGovernorAgent
        new_gov = RiskGovernorAgent()
        self.assertEqual(new_gov.daily_start_balance, 60.0)

        # Drop balance below 5% max drawdown ceiling ($56.00 on $60.00 = 6.67% loss)
        proposal = {
            "symbol": "NVDAXUSD", "signal": "BUY", "entry_price": 220.0,
            "stop_loss": 215.0, "take_profit_1": 235.0, "reward_to_risk": 3.0
        }
        res = new_gov.evaluate_proposal(proposal, current_balance=56.0)
        self.assertFalse(res["approved"])
        self.assertIn("CIRCUIT BREAKER", res["reason"])

    def test_stoikov_micro_price_calculation(self):
        """Verify Stoikov Micro-Price and 5-level weighted depth imbalance math."""
        from agents.microstructure_engine import microstructure_engine
        
        # Scenario 1: Heavy buy pressure (Bid size 100 vs Ask size 10)
        # Mid = 200.05, Spread = 0.10. Micro-Price should lean heavily toward Ask (200.10)
        micro_p, micro_drift = microstructure_engine.compute_stoikov_microprice(
            best_bid=200.00, best_ask=200.10, bid_size=100.0, ask_size=10.0
        )
        self.assertGreater(micro_p, 200.05)  # Higher than mid!
        self.assertGreater(micro_drift, 0.20)  # Strong positive drift!

        # Scenario 2: 5-level depth imbalance
        raw_bids = [{"price": 200.0 - i*0.01, "size": 50} for i in range(5)]
        raw_asks = [{"price": 200.10 + i*0.01, "size": 10} for i in range(5)]
        depth_imb = microstructure_engine.compute_5level_depth_imbalance(raw_bids, raw_asks)
        self.assertGreater(depth_imb, 0.40)  # Heavy buy depth

        # Scenario 3: Orderbook analysis output
        ob_mock = {
            "best_bid": 200.00, "best_ask": 200.10, "spread": 0.10, "spread_pct": 0.05,
            "raw_bids": raw_bids, "raw_asks": raw_asks
        }
        res_buy = microstructure_engine.analyze_orderbook(ob_mock, signal="BUY")
        self.assertEqual(res_buy["status"], "FAVORABLE")

        res_sell = microstructure_engine.analyze_orderbook(ob_mock, signal="SELL")
        self.assertEqual(res_sell["status"], "ADVERSE")

    def test_adverse_selection_protection(self):
        """Verify execution manager rejects orders when microstructure is ADVERSE."""
        from agents.execution_manager import execution_manager
        # Set to LIVE mode for test check
        orig_mode = execution_manager.mode
        execution_manager.set_mode("LIVE")
        
        adverse_proposal = {
            "symbol": "NVDAXUSD",
            "signal": "BUY",
            "entry_price": 220.0,
            "stop_loss": 215.0,
            "take_profit_1": 235.0,
            "take_profit_2": 245.0,
            "micro_status": "ADVERSE",
            "micro_drift": -0.35
        }
        risk_approval = {
            "approved": True, "contracts": 1, "contract_val": 0.01,
            "isolated_leverage": 4, "risk_budget_usd": 1.50
        }
        res = execution_manager.execute_order(adverse_proposal, risk_approval)
        self.assertFalse(res["success"])
        self.assertIn("Microstructure Abort", res["error"])

        execution_manager.set_mode(orig_mode)

    def test_dual_key_orchestration(self):
        """Verify Orchestrator enforces dual-key consensus."""
        cycle = orchestrator.run_cycle_for_symbol("NVDAXUSD", current_balance=60.0)
        # Should have run both Gate 1 and Gate 2
        self.assertIsNotNone(cycle["news_intelligence"])
        self.assertIsNotNone(cycle["quant_analysis"])

    def test_telegram_candidate_evaluation_and_digest(self):
        """Verify candidate evaluation and scan digest formatting with cooldown logic."""
        from agents.telegram_notifier import telegram_notifier
        # In test environment, sending returns False without network calls
        gate1 = {"usable_sentiment": 0.45, "confidence": 0.85, "gate_passed": True}
        quant_rejected = {
            "gate_passed": False,
            "signal": "NEUTRAL",
            "reason": "Composite alpha (-0.12) within neutral band",
            "indicators": {
                "mtf": {"macro_trend_1h": "BEARISH", "pullback_dist_atr": 1.5},
                "microstructure": {"micro_drift": -0.15, "depth_imbalance_5": -0.22}
            }
        }
        res = telegram_notifier.notify_candidate_evaluation("ETHUSD", gate1, quant_rejected, open_slots=1)
        self.assertFalse(res)  # Cleanly suppresses in test environment

        # Test scan digest with sample summaries
        cands = [
            {"symbol": "ETHUSD", "verdict": "Gate 2 Veto (Neutral band)"},
            {"symbol": "TSLAXUSD", "verdict": "Gate 2 Veto (Neutral band)"}
        ]
        res_digest = telegram_notifier.notify_scan_digest(open_slots=1, active_positions=[], candidate_summaries=cands, force=True)
        self.assertFalse(res_digest)  # Cleanly suppresses in test environment

    def test_orchestrator_rotating_scan(self):
        """Verify orchestrator advances rotating cursor across universe."""
        initial_cursor = orchestrator.scan_cursor
        scan_res = orchestrator.run_full_scan()
        self.assertIn("results", scan_res)
        self.assertIn("portfolio", scan_res)
        self.assertNotEqual(orchestrator.scan_cursor, initial_cursor)

    def test_session_momentum_engine(self):
        """Verify SessionMomentumEngine correctly classifies Time-of-Day windows and parameters."""
        from agents.session_momentum_engine import session_momentum_engine
        from datetime import datetime, timezone

        # 1. Test US Open Power Surge (Monday 14:00 UTC)
        dt_us_open = datetime(2026, 9, 14, 14, 0, tzinfo=timezone.utc)  # Monday 14:00 UTC
        session_us = session_momentum_engine.get_current_session(dt_us_open)
        self.assertEqual(session_us["session_name"], "US_OPEN_POWER_SURGE")
        self.assertTrue(session_us["is_high_momentum"])
        self.assertEqual(session_us["alpha_threshold"], 0.38)
        self.assertEqual(session_us["scan_interval_seconds"], 30)
        self.assertTrue(session_us["allow_pure_technical_breakouts"])

        # 2. Test US Market Power Hour (Monday 19:30 UTC)
        dt_power_hour = datetime(2026, 9, 14, 19, 30, tzinfo=timezone.utc)
        session_power = session_momentum_engine.get_current_session(dt_power_hour)
        self.assertEqual(session_power["session_name"], "US_POWER_HOUR")
        self.assertTrue(session_power["is_high_momentum"])
        self.assertEqual(session_power["alpha_threshold"], 0.38)

        # 3. Test London / Europe Morning (Monday 10:00 UTC)
        dt_london = datetime(2026, 9, 14, 10, 0, tzinfo=timezone.utc)
        session_london = session_momentum_engine.get_current_session(dt_london)
        self.assertEqual(session_london["session_name"], "EUROPE_LONDON_SESSION")
        self.assertFalse(session_london["is_high_momentum"])
        self.assertEqual(session_london["alpha_threshold"], 0.45)

        # 4. Test Asian / US Night Dead Zone (Monday 03:00 UTC)
        dt_dead = datetime(2026, 9, 14, 3, 0, tzinfo=timezone.utc)
        session_dead = session_momentum_engine.get_current_session(dt_dead)
        self.assertEqual(session_dead["session_name"], "OFF_HOURS_DEAD_ZONE")
        self.assertFalse(session_dead["is_high_momentum"])
        self.assertEqual(session_dead["alpha_threshold"], 0.48)

        # 5. Dynamic Alpha Threshold calculation across asset types
        self.assertEqual(session_momentum_engine.get_alpha_threshold("NVDAXUSD", dt_us_open), 0.38)
        self.assertEqual(session_momentum_engine.get_alpha_threshold("NVDAXUSD", dt_dead), 0.48)
        self.assertEqual(session_momentum_engine.get_alpha_threshold("BTCUSD", dt_dead), 0.45)

        # 6. Priority universe ordering
        universe = ["BTCUSD", "ETHUSD", "NVDAXUSD", "TSLAXUSD", "PLTRBUSD", "SPYXUSD", "AMDBUSD"]
        us_prioritized = session_momentum_engine.get_priority_symbols(universe, dt_us_open)
        self.assertEqual(us_prioritized[0], "NVDAXUSD")  # Tech RWA prioritized during US open

    def test_entry_state_dead_cat_bounce_veto(self):
        """Verify buying the first green candle after a violent sell dump triggers a STATE_C_DEAD_CAT_BOUNCE veto."""
        atr = 10.0
        # 6 candles on 5m: Violent dump from 2470 down to 2415, then 1 green candle to 2425
        candles_5m = [
            {"time": 1, "open": 2470.0, "high": 2472.0, "low": 2460.0, "close": 2462.0},
            {"time": 2, "open": 2462.0, "high": 2463.0, "low": 2445.0, "close": 2448.0},
            {"time": 3, "open": 2448.0, "high": 2450.0, "low": 2430.0, "close": 2432.0},
            {"time": 4, "open": 2432.0, "high": 2434.0, "low": 2415.0, "close": 2418.0},
            {"time": 5, "open": 2418.0, "high": 2420.0, "low": 2412.0, "close": 2415.0},
            {"time": 6, "open": 2415.0, "high": 2430.0, "low": 2414.0, "close": 2428.0},  # First rebound candle
        ]
        res = quant_analyst_agent._classify_entry_state(
            candles_5m=candles_5m,
            candles_15m=[],
            signal="BUY",
            mark_price=2428.0,
            atr=atr,
            ema20_15m=2455.0
        )
        self.assertEqual(res["state"], "STATE_C_DEAD_CAT_BOUNCE")
        self.assertFalse(res["approved"])
        self.assertIn("VETO Dead-Cat Bounce", res["reason"])

    def test_entry_state_exhaustion_chase_veto(self):
        """Verify shorting at the bottom tick of an extended sell impulse triggers a STATE_C_EXHAUSTION_CHASE veto."""
        atr = 10.0
        candles_5m = [
            {"time": 1, "open": 2470.0, "high": 2472.0, "low": 2460.0, "close": 2462.0},
            {"time": 2, "open": 2462.0, "high": 2463.0, "low": 2445.0, "close": 2448.0},
            {"time": 3, "open": 2448.0, "high": 2450.0, "low": 2430.0, "close": 2432.0},
            {"time": 4, "open": 2432.0, "high": 2434.0, "low": 2415.0, "close": 2418.0},
            {"time": 5, "open": 2418.0, "high": 2420.0, "low": 2412.0, "close": 2415.0},
            {"time": 6, "open": 2415.0, "high": 2416.0, "low": 2410.0, "close": 2412.0},  # Dumping tail
        ]
        # Mark price is 2412, which is (2455 - 2412) / 10 = 4.3x ATR below 15m EMA20!
        res = quant_analyst_agent._classify_entry_state(
            candles_5m=candles_5m,
            candles_15m=[],
            signal="SELL",
            mark_price=2412.0,
            atr=atr,
            ema20_15m=2455.0
        )
        self.assertEqual(res["state"], "STATE_C_EXHAUSTION_CHASE")
        self.assertFalse(res["approved"])
        self.assertIn("VETO Flush Exhaustion", res["reason"])

    def test_entry_state_genuine_reversal_approval(self):
        """Verify that after a dump, when price forms a 3-bar base, higher low, and reclaims 5m EMA20, State B is approved."""
        atr = 10.0
        # 12 candles on 5m: Initial dump to 2415, then consolidation base, higher low at 2420, and breakout above 5m EMA20
        candles_5m = [
            {"time": 1, "open": 2470.0, "high": 2472.0, "low": 2450.0, "close": 2452.0},
            {"time": 2, "open": 2452.0, "high": 2453.0, "low": 2430.0, "close": 2432.0},
            {"time": 3, "open": 2432.0, "high": 2434.0, "low": 2415.0, "close": 2416.0},
            {"time": 4, "open": 2416.0, "high": 2422.0, "low": 2415.0, "close": 2420.0},
            {"time": 5, "open": 2420.0, "high": 2424.0, "low": 2416.0, "close": 2422.0},
            {"time": 6, "open": 2422.0, "high": 2425.0, "low": 2418.0, "close": 2424.0},  # Base held
            {"time": 7, "open": 2424.0, "high": 2428.0, "low": 2420.0, "close": 2426.0},  # Higher low 1
            {"time": 8, "open": 2426.0, "high": 2432.0, "low": 2422.0, "close": 2430.0},  # Higher low 2
            {"time": 9, "open": 2430.0, "high": 2438.0, "low": 2428.0, "close": 2436.0},  # Reclaiming EMA20
        ]
        res = quant_analyst_agent._classify_entry_state(
            candles_5m=candles_5m,
            candles_15m=[],
            signal="BUY",
            mark_price=2436.0,
            atr=atr,
            ema20_15m=2450.0
        )
        self.assertEqual(res["state"], "STATE_B_GENUINE_REVERSAL")
        self.assertTrue(res["approved"])
        self.assertIn("State B Reversal Confirmed", res["reason"])

    def test_1h_zone_clearance_veto_choked_overhead_supply(self):
        """Verify BUY is vetoed when an unmitigated 1H supply zone chokes clearance (< 1.2R)."""
        atr_1h = 15.0
        # 10 1H candles: Bar 2 creates Supply Zone at [2492.5 - 2500.0] with displacement down to 2420
        candles_1h = [
            {"time": 1, "open": 2480.0, "high": 2485.0, "low": 2475.0, "close": 2482.0},
            {"time": 2, "open": 2482.0, "high": 2500.0, "low": 2480.0, "close": 2495.0}, # Pivot High (Top 2500, Bottom ~2492.5)
            {"time": 3, "open": 2490.0, "high": 2491.0, "low": 2440.0, "close": 2445.0}, # Displacement impulse
            {"time": 4, "open": 2445.0, "high": 2450.0, "low": 2420.0, "close": 2425.0},
            {"time": 5, "open": 2425.0, "high": 2435.0, "low": 2420.0, "close": 2430.0},
            {"time": 6, "open": 2430.0, "high": 2445.0, "low": 2425.0, "close": 2440.0},
            {"time": 7, "open": 2440.0, "high": 2455.0, "low": 2435.0, "close": 2450.0},
            {"time": 8, "open": 2450.0, "high": 2465.0, "low": 2445.0, "close": 2460.0},
            {"time": 9, "open": 2460.0, "high": 2486.0, "low": 2455.0, "close": 2485.0}, # Approaching zone
        ]
        # Entry at 2488.0, risk_dist = 15.0. Overhead supply bottom is at 2492.5.
        # available_reward = 2492.5 - 2488.0 = 4.5. clearance_ratio = 4.5 / 15.0 = 0.30R (< 1.2R)
        res = quant_analyst_agent._analyze_1h_supply_demand_zones(
            candles_1h=candles_1h,
            signal="BUY",
            entry_price=2488.0,
            risk_dist=15.0,
            atr_1h=atr_1h
        )
        self.assertFalse(res["approved"])
        self.assertLess(res["clearance_ratio"], 1.2)
        self.assertIn("1H Zone Clearance VETO", res["reason"])
        self.assertIn("Overhead 1H Supply Zone", res["reason"])

    def test_1h_zone_clearance_approved_adequate_room(self):
        """Verify BUY is approved when overhead 1H supply zone has plenty of room (>= 1.2R)."""
        atr_1h = 15.0
        candles_1h = [
            {"time": 1, "open": 2480.0, "high": 2485.0, "low": 2475.0, "close": 2482.0},
            {"time": 2, "open": 2482.0, "high": 2500.0, "low": 2480.0, "close": 2495.0},
            {"time": 3, "open": 2490.0, "high": 2491.0, "low": 2440.0, "close": 2445.0},
            {"time": 4, "open": 2445.0, "high": 2450.0, "low": 2420.0, "close": 2425.0},
            {"time": 5, "open": 2425.0, "high": 2435.0, "low": 2420.0, "close": 2430.0},
            {"time": 6, "open": 2430.0, "high": 2440.0, "low": 2425.0, "close": 2435.0},
        ]
        # Entry at 2435.0, risk_dist = 15.0. Overhead supply bottom is at 2492.5.
        # available_reward = 2492.5 - 2435.0 = 57.5. clearance_ratio = 57.5 / 15.0 = 3.83R (>= 1.2R)
        res = quant_analyst_agent._analyze_1h_supply_demand_zones(
            candles_1h=candles_1h,
            signal="BUY",
            entry_price=2435.0,
            risk_dist=15.0,
            atr_1h=atr_1h
        )
        self.assertTrue(res["approved"])
        self.assertGreaterEqual(res["clearance_ratio"], 1.2)
        self.assertIn("1H Zone Clearance Approved", res["reason"])

    def test_1h_zone_clearance_depleted_zone_non_blocking(self):
        """Verify that an overhead 1H supply zone tested 3 times is DEPLETED and does NOT block BUY."""
        atr_1h = 15.0
        candles_1h = [
            {"time": 1, "open": 2480.0, "high": 2485.0, "low": 2475.0, "close": 2482.0},
            {"time": 2, "open": 2482.0, "high": 2500.0, "low": 2480.0, "close": 2495.0}, # Pivot High (bottom ~2492.5)
            {"time": 3, "open": 2490.0, "high": 2491.0, "low": 2440.0, "close": 2445.0}, # Drop
            {"time": 4, "open": 2445.0, "high": 2495.0, "low": 2440.0, "close": 2450.0}, # Retest 1
            {"time": 5, "open": 2450.0, "high": 2494.0, "low": 2445.0, "close": 2455.0}, # Retest 2
            {"time": 6, "open": 2455.0, "high": 2493.0, "low": 2450.0, "close": 2460.0}, # Retest 3 (DEPLETED)
            {"time": 7, "open": 2460.0, "high": 2490.0, "low": 2455.0, "close": 2488.0},
        ]
        # Price is at 2488, right under 2492.5, but because it has been tested 3 times,
        # liquidity is absorbed, breakout is anticipated -> non-blocking!
        res = quant_analyst_agent._analyze_1h_supply_demand_zones(
            candles_1h=candles_1h,
            signal="BUY",
            entry_price=2488.0,
            risk_dist=15.0,
            atr_1h=atr_1h
        )
        self.assertTrue(res["approved"])
        self.assertIn("1H Zone Clearance Approved", res["reason"])

    def test_1h_zone_clearance_veto_choked_underlying_demand(self):
        """Verify SELL is vetoed when an unmitigated 1H demand zone chokes clearance (< 1.2R)."""
        atr_1h = 15.0
        candles_1h = [
            {"time": 1, "open": 2420.0, "high": 2425.0, "low": 2415.0, "close": 2418.0},
            {"time": 2, "open": 2418.0, "high": 2420.0, "low": 2390.0, "close": 2395.0}, # Pivot Low (Bottom 2390, Top ~2397.5)
            {"time": 3, "open": 2395.0, "high": 2450.0, "low": 2394.0, "close": 2445.0}, # Rally impulse
            {"time": 4, "open": 2445.0, "high": 2470.0, "low": 2440.0, "close": 2465.0},
            {"time": 5, "open": 2465.0, "high": 2470.0, "low": 2430.0, "close": 2435.0},
            {"time": 6, "open": 2435.0, "high": 2440.0, "low": 2405.0, "close": 2402.0}, # Pulling down near demand
        ]
        # Selling at 2400.0, risk_dist = 12.0. Underlying demand top is at 2397.5.
        # available_reward = 2400.0 - 2397.5 = 2.5. clearance_ratio = 2.5 / 12.0 = 0.21R (< 1.2R)
        res = quant_analyst_agent._analyze_1h_supply_demand_zones(
            candles_1h=candles_1h,
            signal="SELL",
            entry_price=2400.0,
            risk_dist=12.0,
            atr_1h=atr_1h
        )
        self.assertFalse(res["approved"])
        self.assertLess(res["clearance_ratio"], 1.2)
        self.assertIn("1H Zone Clearance VETO", res["reason"])
        self.assertIn("Underlying 1H Demand Zone", res["reason"])

if __name__ == "__main__":
    unittest.main()
