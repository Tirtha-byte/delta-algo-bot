import asyncio
import json
import time
import threading
from typing import Dict, Any, List, Optional, Tuple
from collections import deque
import websockets

from config import delta_config, staleness_config, system_config
from data.staleness_breaker import staleness_breaker, HealthState

class LocalOrderBook:
    """
    Local orderbook maintaining full L2 depth, top of book (L1),
    microprice calculations, and sequence validation.
    """
    def __init__(self, symbol: str):
        self.symbol = symbol
        self.bids: Dict[float, float] = {}  # price -> size
        self.asks: Dict[float, float] = {}  # price -> size
        self.best_bid: float = 0.0
        self.best_ask: float = 0.0
        self.bid_size: float = 0.0
        self.ask_size: float = 0.0
        self.spread: float = 0.0
        self.spread_pct: float = 0.0
        self.microprice: float = 0.0
        self.microdrift: float = 0.0
        self.imbalance_l1: float = 0.0
        self.imbalance_l5: float = 0.0
        self.imbalance_l10: float = 0.0
        self.last_seq: int = 0
        self.last_checksum: int = 0
        self.last_update_ts: float = 0.0
        self.lock = threading.Lock()

    def update_l1(self, bp: float, bs: float, ap: float, as_: float, exchange_ts_us: Optional[int] = None):
        """Update top of book directly from ob_l1 stream."""
        with self.lock:
            self.best_bid = bp
            self.bid_size = bs
            self.best_ask = ap
            self.ask_size = as_
            self.spread = max(0.0, ap - bp) if ap > 0 and bp > 0 else 0.0
            self.spread_pct = (self.spread / ap) * 100.0 if ap > 0 else 0.0
            self.last_update_ts = time.time()

            # Sasha Stoikov Micro-price & Drift
            total_l1_qty = bs + as_
            if total_l1_qty > 0 and self.spread > 0:
                self.microprice = (bp * (as_ / total_l1_qty)) + (ap * (bs / total_l1_qty))
                mid = (bp + ap) / 2.0
                self.microdrift = (self.microprice - mid) / (self.spread + 1e-9)
                self.imbalance_l1 = (bs - as_) / total_l1_qty
            elif total_l1_qty > 0:
                self.microprice = (bp + ap) / 2.0
                self.microdrift = 0.0
                self.imbalance_l1 = 0.0

        staleness_breaker.record_l1(self.symbol, exchange_ts_us)

    def apply_l2_snapshot(self, bids_data: List[List[str]], asks_data: List[List[str]], exchange_ts_us: Optional[int] = None):
        """Reconstruct L2 orderbook from ob_l2 or ob_updates snapshot."""
        with self.lock:
            self.bids.clear()
            self.asks.clear()

            for b in bids_data:
                try:
                    p = float(b[0])
                    s = float(b[1])
                    if s > 0:
                        self.bids[p] = s
                except (ValueError, IndexError):
                    continue

            for a in asks_data:
                try:
                    p = float(a[0])
                    s = float(a[1])
                    if s > 0:
                        self.asks[p] = s
                except (ValueError, IndexError):
                    continue

            self._recalculate_derived_levels()
            self.last_update_ts = time.time()

        staleness_breaker.record_l2(self.symbol, exchange_ts_us)

    def apply_incremental_update(self, bids_delta: List[List[str]], asks_delta: List[List[str]],
                                  seq: int, cs: Optional[int] = None, exchange_ts_us: Optional[int] = None) -> bool:
        """
        Apply incremental updates with sequence validation.
        Returns False if sequence gap detected.
        """
        with self.lock:
            if self.last_seq > 0 and seq != self.last_seq + 1:
                staleness_breaker.record_sequence_gap(self.symbol, self.last_seq + 1, seq)
                return False

            self.last_seq = seq
            if cs is not None:
                self.last_checksum = cs

            for b in bids_delta:
                try:
                    p = float(b[0])
                    s = float(b[1])
                    if s == 0:
                        self.bids.pop(p, None)
                    else:
                        self.bids[p] = s
                except (ValueError, IndexError):
                    continue

            for a in asks_delta:
                try:
                    p = float(a[0])
                    s = float(a[1])
                    if s == 0:
                        self.asks.pop(p, None)
                    else:
                        self.asks[p] = s
                except (ValueError, IndexError):
                    continue

            self._recalculate_derived_levels()
            self.last_update_ts = time.time()

        staleness_breaker.record_l2(self.symbol, exchange_ts_us)
        return True

    def _recalculate_derived_levels(self):
        sorted_bids = sorted(self.bids.items(), key=lambda x: x[0], reverse=True)
        sorted_asks = sorted(self.asks.items(), key=lambda x: x[0])

        if sorted_bids:
            self.best_bid = sorted_bids[0][0]
            self.bid_size = sorted_bids[0][1]
        if sorted_asks:
            self.best_ask = sorted_asks[0][0]
            self.ask_size = sorted_asks[0][1]

        if self.best_ask > 0 and self.best_bid > 0:
            self.spread = max(0.0, self.best_ask - self.best_bid)
            self.spread_pct = (self.spread / self.best_ask) * 100.0

            total_top_qty = self.bid_size + self.ask_size
            if total_top_qty > 0 and self.spread > 0:
                self.microprice = (self.best_bid * (self.ask_size / total_top_qty)) + (self.best_ask * (self.bid_size / total_top_qty))
                mid = (self.best_bid + self.best_ask) / 2.0
                self.microdrift = (self.microprice - mid) / (self.spread + 1e-9)
                self.imbalance_l1 = (self.bid_size - self.ask_size) / total_top_qty

            # L5 Imbalance
            b5_vol = sum(s for _, s in sorted_bids[:5])
            a5_vol = sum(s for _, s in sorted_asks[:5])
            tot_5 = b5_vol + a5_vol
            self.imbalance_l5 = (b5_vol - a5_vol) / tot_5 if tot_5 > 0 else 0.0

            # L10 Imbalance
            b10_vol = sum(s for _, s in sorted_bids[:10])
            a10_vol = sum(s for _, s in sorted_asks[:10])
            tot_10 = b10_vol + a10_vol
            self.imbalance_l10 = (b10_vol - a10_vol) / tot_10 if tot_10 > 0 else 0.0

    def get_snapshot(self) -> Dict[str, Any]:
        """Return thread-safe structured snapshot of book state."""
        with self.lock:
            sorted_bids = sorted(self.bids.items(), key=lambda x: x[0], reverse=True)[:15]
            sorted_asks = sorted(self.asks.items(), key=lambda x: x[0])[:15]

            return {
                "symbol": self.symbol,
                "best_bid": self.best_bid,
                "best_ask": self.best_ask,
                "bid_size": self.bid_size,
                "ask_size": self.ask_size,
                "spread": self.spread,
                "spread_pct": self.spread_pct,
                "micro_price": self.microprice,
                "micro_drift": self.microdrift,
                "imbalance": self.imbalance_l10,
                "imbalance_l1": self.imbalance_l1,
                "imbalance_l5": self.imbalance_l5,
                "imbalance_l10": self.imbalance_l10,
                "raw_bids": [{"price": str(p), "size": str(s)} for p, s in sorted_bids],
                "raw_asks": [{"price": str(p), "size": str(s)} for p, s in sorted_asks],
                "last_seq": self.last_seq,
                "timestamp": self.last_update_ts,
                "success": bool(self.best_bid > 0 and self.best_ask > 0)
            }


class MarketDataEngine:
    """
    Real-Time Market Data Engine for Delta Exchange.
    Responsibilities:
    - WebSocket Lifecycle (Public feeds)
    - Auto-reconnect with exponential backoff
    - Heartbeat ping/pong
    - Sequence tracking & corrupt book defense
    - Microstructure and trade flow ingestion
    - Thread-safe access for quant analyzers and execution engines
    """
    def __init__(self, symbols: Optional[List[str]] = None, ws_url: Optional[str] = None):
        self.symbols = symbols or list(system_config.US_STOCKS_RWA)
        self.ws_url = ws_url or delta_config.PUBLIC_WS_URL
        self.books: Dict[str, LocalOrderBook] = {s: LocalOrderBook(s) for s in self.symbols}
        self.trades: Dict[str, deque] = {s: deque(maxlen=2000) for s in self.symbols}
        self.mark_prices: Dict[str, float] = {}
        self.spot_prices: Dict[str, float] = {}
        self.tickers_24h: Dict[str, Dict[str, Any]] = {}
        self.funding_rates: Dict[str, float] = {}

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._lock = threading.Lock()

    def start(self):
        """Start the background WebSocket engine in a dedicated daemon thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_event_loop, name="MarketDataEngine-WS", daemon=True)
        self._thread.start()

    def stop(self):
        """Gracefully stop WebSocket listener."""
        self._running = False
        if self._loop and self._loop.is_running():
            def _cancel_all():
                for task in asyncio.all_tasks(self._loop):
                    task.cancel()
                self._loop.stop()
            self._loop.call_soon_threadsafe(_cancel_all)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)

    def _run_event_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._connection_supervisor())
        except (asyncio.CancelledError, RuntimeError):
            pass
        finally:
            try:
                pending = asyncio.all_tasks(self._loop)
                if pending:
                    self._loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            except Exception:
                pass
            try:
                self._loop.close()
            except Exception:
                pass


    async def _connection_supervisor(self):
        backoff = staleness_config.RECONNECT_BACKOFF_BASE_SEC
        while self._running:
            try:
                staleness_breaker.record_connection_status(False)
                async with websockets.connect(
                    self.ws_url,
                    ping_interval=staleness_config.HEARTBEAT_INTERVAL_SEC,
                    ping_timeout=10,
                    open_timeout=10
                ) as ws:
                    staleness_breaker.record_connection_status(True)
                    backoff = staleness_config.RECONNECT_BACKOFF_BASE_SEC
                    await self._subscribe(ws)
                    await self._receive_loop(ws)
            except Exception as e:
                staleness_breaker.record_connection_status(False)
                if not self._running:
                    break
                await asyncio.sleep(backoff)
                backoff = min(staleness_config.RECONNECT_BACKOFF_MAX_SEC, backoff * 1.5)

    async def _subscribe(self, ws):
        # Subscribe in batches or grouped channels
        # Primary channels: ob_l1, ob_l2, ob_updates, trades, ticker, mark_price, system_status
        channels = [
            {"name": "system_status", "symbols": ["all"]},
            {"name": "ob_l1", "symbols": self.symbols},
            {"name": "ob_l2", "symbols": self.symbols[:10]},      # L2 depth for active tier
            {"name": "ob_updates", "symbols": self.symbols[:5]},   # Full book updates for primary assets
            {"name": "trades", "symbols": self.symbols},
            {"name": "ticker", "symbols": self.symbols},
            {"name": "mark_price", "symbols": self.symbols}
        ]
        sub_msg = {"type": "subscribe", "payload": {"channels": channels}}
        await ws.send(json.dumps(sub_msg))

    async def _receive_loop(self, ws):
        while self._running:
            try:
                raw_msg = await asyncio.wait_for(ws.recv(), timeout=20.0)
                self._dispatch_message(raw_msg)
            except asyncio.TimeoutError:
                # Ping heartbeat check
                pong_waiter = await ws.ping()
                await asyncio.wait_for(pong_waiter, timeout=5.0)

    def _dispatch_message(self, raw_msg: str):
        try:
            msg = json.loads(raw_msg)
        except Exception:
            return

        msg_type = msg.get("type")
        if not msg_type:
            return

        if msg_type == "system_status":
            status = msg.get("status", "unknown")
            is_maint = msg.get("maintenance_announcement_time") is not None
            staleness_breaker.record_system_status(status, is_maint)

        elif msg_type == "ob_l1":
            sym = msg.get("sy")
            if sym and sym in self.books:
                try:
                    bp = float(msg.get("bp", 0.0))
                    bs = float(msg.get("bs", 0.0))
                    ap = float(msg.get("ap", 0.0))
                    as_ = float(msg.get("as", 0.0))
                    ts = msg.get("ts")
                    self.books[sym].update_l1(bp, bs, ap, as_, ts)
                except Exception:
                    pass

        elif msg_type == "ob_l2":
            sym = msg.get("sy")
            if sym and sym in self.books:
                bids = msg.get("b", [])
                asks = msg.get("a", [])
                ts = msg.get("ts")
                self.books[sym].apply_l2_snapshot(bids, asks, ts)

        elif msg_type == "ob_updates":
            sym = msg.get("sy")
            if sym and sym in self.books:
                action = msg.get("action")
                bids = msg.get("b", [])
                asks = msg.get("a", [])
                seq = msg.get("seq", 0)
                cs = msg.get("cs")
                ts = msg.get("ts")
                if action == "snapshot":
                    self.books[sym].apply_l2_snapshot(bids, asks, ts)
                    self.books[sym].last_seq = seq
                elif action == "update":
                    self.books[sym].apply_incremental_update(bids, asks, seq, cs, ts)

        elif msg_type == "trades":
            sym = msg.get("sy")
            if sym and sym in self.trades:
                try:
                    price = float(msg.get("p", 0.0))
                    size = float(msg.get("s", 0.0))
                    role = msg.get("r", "m")  # 't' = taker (aggressive buyer), 'm' = maker (aggressive seller)
                    ts = msg.get("t") or msg.get("ts")
                    is_buyer_taker = (role == "t")
                    trade_record = {
                        "symbol": sym,
                        "price": price,
                        "size": size,
                        "role": role,
                        "is_aggressive_buy": is_buyer_taker,
                        "timestamp": ts,
                        "local_time": time.time()
                    }
                    self.trades[sym].append(trade_record)
                    staleness_breaker.record_trade(sym, ts)
                except Exception:
                    pass

        elif msg_type == "ticker":
            sym = msg.get("sy")
            if sym:
                data_list = msg.get("d", [])
                if data_list:
                    d0 = data_list[0]
                    mark = float(d0.get("m", 0.0) or 0.0)
                    chg = float(d0.get("m24hc", 0.0) or 0.0)
                    to = d0.get("to", [0, 0])
                    vol = float(to[0]) if to else 0.0
                    spot = float(msg.get("sp", mark) or mark)
                    with self._lock:
                        self.mark_prices[sym] = mark
                        self.spot_prices[sym] = spot
                        self.tickers_24h[sym] = {
                            "symbol": sym,
                            "mark_price": mark,
                            "spot_price": spot,
                            "price_change_24h": chg,
                            "volume_24h": vol,
                            "timestamp": time.time(),
                            "success": True
                        }

                    staleness_breaker.record_mark_price(sym, msg.get("ts"))

        elif msg_type == "mark_price":
            sym = msg.get("symbol") or msg.get("sy")
            if sym:
                mark = float(msg.get("mark_price", 0.0) or msg.get("m", 0.0) or 0.0)
                if mark > 0:
                    with self._lock:
                        self.mark_prices[sym] = mark
                    staleness_breaker.record_mark_price(sym, msg.get("timestamp") or msg.get("ts"))

        elif msg_type == "funding_rate":
            sym = msg.get("symbol") or msg.get("sy")
            if sym:
                fr = float(msg.get("funding_rate", 0.0) or 0.0)
                with self._lock:
                    self.funding_rates[sym] = fr

    # ================= Public Thread-Safe Query API =================
    def get_orderbook(self, symbol: str) -> Dict[str, Any]:
        """Fetch local high-speed L1/L2 orderbook snapshot."""
        book = self.books.get(symbol)
        if book:
            return book.get_snapshot()
        return {"symbol": symbol, "best_bid": 0.0, "best_ask": 0.0, "imbalance": 0.0, "success": False}

    def get_l1(self, symbol: str) -> Dict[str, Any]:
        book = self.books.get(symbol)
        if book:
            with book.lock:
                return {
                    "symbol": symbol,
                    "best_bid": book.best_bid,
                    "best_ask": book.best_ask,
                    "bid_size": book.bid_size,
                    "ask_size": book.ask_size,
                    "spread": book.spread,
                    "spread_pct": book.spread_pct,
                    "micro_price": book.microprice,
                    "micro_drift": book.microdrift,
                    "success": bool(book.best_bid > 0 and book.best_ask > 0)
                }
        return {"symbol": symbol, "best_bid": 0.0, "best_ask": 0.0, "success": False}

    def get_recent_trades(self, symbol: str, limit: int = 100) -> List[Dict[str, Any]]:
        dq = self.trades.get(symbol)
        if dq:
            return list(dq)[-limit:]
        return []

    def get_ticker(self, symbol: str) -> Dict[str, Any]:
        with self._lock:
            tk = self.tickers_24h.get(symbol)
            if tk:
                return dict(tk)
            mark = self.mark_prices.get(symbol, 0.0)
            spot = self.spot_prices.get(symbol, mark)
            return {
                "symbol": symbol,
                "mark_price": mark,
                "spot_price": spot,
                "volume_24h": 0.0,
                "price_change_24h": 0.0,
                "timestamp": time.time(),
                "success": bool(mark > 0)
            }

    def get_mark_price(self, symbol: str) -> float:
        with self._lock:
            return self.mark_prices.get(symbol, 0.0)

    def get_funding_rate(self, symbol: str) -> float:
        with self._lock:
            return self.funding_rates.get(symbol, 0.0)

    def get_health(self, symbol: str) -> Dict[str, Any]:
        return staleness_breaker.evaluate_symbol(symbol)

market_data_engine = MarketDataEngine()
