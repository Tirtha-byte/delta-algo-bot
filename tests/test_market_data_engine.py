import os
os.environ["TESTING"] = "1"
import time
import json
import unittest
from data.staleness_breaker import MarketDataStalenessBreaker, HealthState
from data.market_data_engine import LocalOrderBook, MarketDataEngine
from data.account_engine import AccountEngine

class TestMarketDataEngine(unittest.TestCase):

    def setUp(self):
        self.breaker = MarketDataStalenessBreaker()
        self.engine = MarketDataEngine(symbols=["BTCUSD", "ETHUSD", "NVDAXUSD"])

    def test_ob_l1_parsing_and_stoikov_microprice(self):
        """Verify L1 updates and Stoikov microprice / drift calculation."""
        book = LocalOrderBook("BTCUSD")
        # Bid = 70000, ask = 70010, bid_size = 3.0, ask_size = 1.0 (heavy bid pressure)
        # Expected microprice should be closer to ask (70010)
        book.update_l1(bp=70000.0, bs=3.0, ap=70010.0, as_=1.0)
        
        self.assertEqual(book.best_bid, 70000.0)
        self.assertEqual(book.best_ask, 70010.0)
        self.assertEqual(book.spread, 10.0)
        
        # P_micro = 70000 * (1/4) + 70010 * (3/4) = 17500 + 52507.5 = 70007.5
        self.assertAlmostEqual(book.microprice, 70007.5, places=2)
        # Mid = 70005.0. Drift = (70007.5 - 70005.0) / 10 = +0.25
        self.assertAlmostEqual(book.microdrift, 0.25, places=2)
        # L1 imbalance = (3 - 1) / 4 = +0.50
        self.assertAlmostEqual(book.imbalance_l1, 0.50, places=2)

    def test_ob_l2_snapshot_reconstruction(self):
        """Verify L2 orderbook snapshot reconstruction and depth imbalance."""
        book = LocalOrderBook("NVDAXUSD")
        raw_bids = [["220.0", "50"], ["219.5", "30"], ["219.0", "20"]]
        raw_asks = [["220.5", "20"], ["221.0", "30"], ["221.5", "50"]]
        
        book.apply_l2_snapshot(raw_bids, raw_asks)
        
        snap = book.get_snapshot()
        self.assertTrue(snap["success"])
        self.assertEqual(snap["best_bid"], 220.0)
        self.assertEqual(snap["best_ask"], 220.5)
        self.assertEqual(snap["bid_size"], 50.0)
        self.assertEqual(snap["ask_size"], 20.0)
        self.assertEqual(len(snap["raw_bids"]), 3)
        self.assertEqual(len(snap["raw_asks"]), 3)

    def test_ob_updates_incremental_and_sequence_gap_detection(self):
        """Verify incremental orderbook updates and sequence gap protection."""
        book = LocalOrderBook("BTCUSD")
        
        # Snapshot at seq 100
        bids = [["70000.0", "5.0"], ["69990.0", "10.0"]]
        asks = [["70010.0", "3.0"], ["70020.0", "8.0"]]
        book.apply_l2_snapshot(bids, asks)
        book.last_seq = 100
        
        # Incremental update seq 101 (modify top bid, delete ask level)
        delta_bids = [["70000.0", "8.0"]]
        delta_asks = [["70020.0", "0.0"]]  # Size 0 means remove level
        ok = book.apply_incremental_update(delta_bids, delta_asks, seq=101)
        self.assertTrue(ok)
        self.assertEqual(book.bids[70000.0], 8.0)
        self.assertNotIn(70020.0, book.asks)
        
        # Out-of-order sequence gap seq 105 (missed 102, 103, 104)
        ok_gap = book.apply_incremental_update([["70000.0", "9.0"]], [], seq=105)
        self.assertFalse(ok_gap)

    def test_trades_parsing_and_aggressor_role(self):
        """Verify trades stream parses buyer role ('t' = buy, 'm' = sell)."""
        engine = MarketDataEngine(symbols=["BTCUSD"])
        
        # Simulated buyer taker trade (aggressive market buy)
        msg_buy = json.dumps({
            "type": "trades",
            "sy": "BTCUSD",
            "p": "77300.0",
            "s": 15.0,
            "r": "t",
            "t": int(time.time() * 1_000_000)
        })
        engine._dispatch_message(msg_buy)
        
        # Simulated buyer maker trade (aggressive market sell)
        msg_sell = json.dumps({
            "type": "trades",
            "sy": "BTCUSD",
            "p": "77295.0",
            "s": 25.0,
            "r": "m",
            "t": int(time.time() * 1_000_000)
        })
        engine._dispatch_message(msg_sell)
        
        recent = engine.get_recent_trades("BTCUSD")
        self.assertEqual(len(recent), 2)
        self.assertTrue(recent[0]["is_aggressive_buy"])
        self.assertFalse(recent[1]["is_aggressive_buy"])

    def test_ticker_and_mark_price_parsing(self):
        """Verify 24h ticker and mark price extraction from Delta feed."""
        engine = MarketDataEngine(symbols=["ETHUSD"])
        
        ticker_msg = json.dumps({
            "type": "ticker",
            "sy": "ETHUSD",
            "sp": "2450.5",
            "ts": int(time.time() * 1_000_000),
            "d": [{
                "m": "2452.1",
                "m24hc": "+1.85",
                "to": [500000.0, 500000.0]
            }]
        })
        engine._dispatch_message(ticker_msg)
        
        tk = engine.get_ticker("ETHUSD")
        self.assertTrue(tk["success"])
        self.assertEqual(tk["mark_price"], 2452.1)
        self.assertEqual(tk["spot_price"], 2450.5)
        self.assertEqual(tk["price_change_24h"], 1.85)
        self.assertEqual(engine.get_mark_price("ETHUSD"), 2452.1)

    def test_staleness_breaker_transitions_and_order_lockout(self):
        """Verify Staleness Breaker transitions and blocks orders deterministically."""
        breaker = MarketDataStalenessBreaker()
        
        # Case 1: Uninitialized -> INVALID, no trading allowed
        res = breaker.evaluate_symbol("BTCUSD")
        self.assertFalse(res["can_trade"])
        self.assertEqual(res["state"], HealthState.INVALID)
        
        # Case 2: Connected and fresh data -> HEALTHY, can trade
        breaker.record_connection_status(True)
        breaker.record_system_status("live")
        breaker.record_l1("BTCUSD")
        res_fresh = breaker.evaluate_symbol("BTCUSD")
        self.assertTrue(res_fresh["can_trade"])
        self.assertEqual(res_fresh["state"], HealthState.HEALTHY)
        
        # Case 3: Simulate stale data (> 5.0 seconds old)
        breaker.symbol_health["BTCUSD"].last_l1_time = time.time() - 6.0
        breaker.symbol_health["BTCUSD"].last_l2_time = time.time() - 6.0
        breaker.symbol_health["BTCUSD"].last_any_time = time.time() - 6.0
        res_stale = breaker.evaluate_symbol("BTCUSD")
        self.assertFalse(res_stale["can_trade"])
        self.assertEqual(res_stale["state"], HealthState.STALE)
        
        # Case 4: Exchange enters maintenance mode -> INVALID, hard lockout
        breaker.record_system_status("maintenance", maintenance=True)
        res_maint = breaker.evaluate_symbol("BTCUSD")
        self.assertFalse(res_maint["can_trade"])
        self.assertEqual(res_maint["state"], HealthState.INVALID)

    def test_account_engine_auth_payload_generation(self):
        """Verify HMAC-SHA256 signature generation for Delta private WebSocket."""
        account = AccountEngine(
            api_key="test_api_key",
            api_secret="test_secret_key"
        )
        auth = account.generate_auth_payload()
        self.assertEqual(auth["type"], "key-auth")
        self.assertEqual(auth["payload"]["api-key"], "test_api_key")
        self.assertIn("signature", auth["payload"])
        self.assertIn("timestamp", auth["payload"])
        self.assertEqual(len(auth["payload"]["signature"]), 64)  # SHA-256 hex length

    def test_account_engine_cache_updates(self):
        """Verify order and position ingestion in AccountEngine."""
        account = AccountEngine()
        
        # Orders update
        account._handle_orders_update({
            "type": "orders",
            "orders": [
                {"id": 1001, "product_symbol": "BTCUSD", "state": "open", "size": 5},
                {"id": 1002, "product_symbol": "ETHUSD", "state": "open", "size": 10}
            ]
        })
        self.assertEqual(len(account.get_open_orders()), 2)
        self.assertEqual(len(account.get_open_orders("BTCUSD")), 1)
        
        # Close order
        account._handle_orders_update({
            "type": "orders",
            "orders": [{"id": 1001, "product_symbol": "BTCUSD", "state": "closed", "size": 0}]
        })
        self.assertEqual(len(account.get_open_orders()), 1)
        self.assertEqual(account.get_open_orders()[0]["id"], 1002)
        
        # Positions update
        account._handle_positions_update({
            "type": "positions",
            "positions": [{"product_symbol": "BTCUSD", "size": "5.0", "entry_price": "75000"}]
        })
        self.assertEqual(len(account.get_positions()), 1)
        
        # Position closed
        account._handle_positions_update({
            "type": "positions",
            "positions": [{"product_symbol": "BTCUSD", "size": "0.0"}]
        })
        self.assertEqual(len(account.get_positions()), 0)

if __name__ == "__main__":
    unittest.main()
