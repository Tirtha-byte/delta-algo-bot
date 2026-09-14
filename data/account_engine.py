import asyncio
import json
import time
import hmac
import hashlib
import threading
from typing import Dict, Any, List, Optional
from collections import deque
import websockets

from config import delta_config, staleness_config

class AccountEngine:
    """
    Private WebSocket Account Engine for Delta Exchange.
    Authenticates via key-auth and maintains authoritative real-time local cache of:
    - Active open orders
    - Margined positions
    - Confirmed trade executions / fills
    """
    def __init__(self, ws_url: Optional[str] = None, api_key: Optional[str] = None, api_secret: Optional[str] = None):
        self.ws_url = ws_url or delta_config.PRIVATE_WS_URL
        self.api_key = api_key or delta_config.API_KEY
        self.api_secret = api_secret or delta_config.API_SECRET

        self.open_orders: Dict[int, Dict[str, Any]] = {}
        self.positions: Dict[str, Dict[str, Any]] = {}
        self.fills: deque = deque(maxlen=500)
        self.wallet_balances: Dict[str, float] = {}

        self.is_connected = False
        self.is_authenticated = False
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self.lock = threading.Lock()

    def reload_credentials(self, api_key: Optional[str] = None, api_secret: Optional[str] = None):
        import os
        from dotenv import load_dotenv
        load_dotenv(override=True)
        self.api_key = api_key or os.getenv("DELTA_API_KEY", "")
        self.api_secret = api_secret or os.getenv("DELTA_API_SECRET", "")

    def generate_auth_payload(self) -> Dict[str, Any]:
        """
        Generate key-auth WebSocket payload.
        Signature: HMAC-SHA256(secret, "GET" + timestamp + "/live")
        """
        timestamp = str(int(time.time()))
        message = "GET" + timestamp + "/live"
        signature = hmac.new(
            self.api_secret.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()

        return {
            "type": "key-auth",
            "payload": {
                "api-key": self.api_key,
                "signature": signature,
                "timestamp": timestamp
            }
        }

    def start(self):
        if not self.api_key or not self.api_secret:
            return
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_event_loop, name="AccountEngine-WS", daemon=True)
        self._thread.start()

    def stop(self):
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
                self.is_connected = False
                self.is_authenticated = False
                async with websockets.connect(
                    self.ws_url,
                    ping_interval=staleness_config.HEARTBEAT_INTERVAL_SEC,
                    ping_timeout=10,
                    open_timeout=10
                ) as ws:
                    self.is_connected = True
                    backoff = staleness_config.RECONNECT_BACKOFF_BASE_SEC

                    # 1. Authenticate immediately
                    auth_msg = self.generate_auth_payload()
                    await ws.send(json.dumps(auth_msg))

                    # 2. Wait for auth confirmation & subscribe to private channels
                    await self._receive_loop(ws)
            except Exception:
                self.is_connected = False
                self.is_authenticated = False
                if not self._running:
                    break
                await asyncio.sleep(backoff)
                backoff = min(staleness_config.RECONNECT_BACKOFF_MAX_SEC, backoff * 1.5)

    async def _receive_loop(self, ws):
        while self._running:
            try:
                raw_msg = await asyncio.wait_for(ws.recv(), timeout=20.0)
                msg = json.loads(raw_msg)
                msg_type = msg.get("type")

                if msg_type == "key-auth":
                    success = msg.get("success", False) or msg.get("status") == "authenticated"
                    self.is_authenticated = success
                    if success:
                        # Subscribe to private account channels
                        sub_msg = {
                            "type": "subscribe",
                            "payload": {
                                "channels": [
                                    {"name": "orders"},
                                    {"name": "positions"},
                                    {"name": "v2/user_trades"}
                                ]
                            }
                        }
                        await ws.send(json.dumps(sub_msg))

                elif msg_type == "orders":
                    self._handle_orders_update(msg)

                elif msg_type == "positions":
                    self._handle_positions_update(msg)

                elif msg_type in ("v2/user_trades", "user_trades"):
                    self._handle_user_trades_update(msg)

            except asyncio.TimeoutError:
                pong_waiter = await ws.ping()
                await asyncio.wait_for(pong_waiter, timeout=5.0)

    def _handle_orders_update(self, msg: Dict[str, Any]):
        orders_data = msg.get("orders") or msg.get("data") or [msg]
        if isinstance(orders_data, dict):
            orders_data = [orders_data]

        with self.lock:
            for o in orders_data:
                order_id = o.get("id")
                if not order_id:
                    continue
                state = o.get("state")
                if state in ("closed", "cancelled", "rejected"):
                    self.open_orders.pop(order_id, None)
                else:
                    self.open_orders[order_id] = o

    def _handle_positions_update(self, msg: Dict[str, Any]):
        positions_data = msg.get("positions") or msg.get("data") or [msg]
        if isinstance(positions_data, dict):
            positions_data = [positions_data]

        with self.lock:
            for p in positions_data:
                sym = p.get("product_symbol") or p.get("symbol")
                if not sym:
                    continue
                size = float(p.get("size", 0.0))
                if abs(size) == 0:
                    self.positions.pop(sym, None)
                else:
                    self.positions[sym] = p

    def _handle_user_trades_update(self, msg: Dict[str, Any]):
        trades_data = msg.get("trades") or msg.get("data") or [msg]
        if isinstance(trades_data, dict):
            trades_data = [trades_data]

        with self.lock:
            for t in trades_data:
                self.fills.append({
                    "symbol": t.get("product_symbol") or t.get("symbol"),
                    "order_id": t.get("order_id"),
                    "fill_price": float(t.get("fill_price") or t.get("price", 0.0)),
                    "size": float(t.get("size", 0.0)),
                    "role": t.get("role", "taker"),
                    "fee": float(t.get("fee", 0.0)),
                    "timestamp": t.get("created_at") or time.time()
                })

    # ================= Public Query API =================
    def get_open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        with self.lock:
            all_orders = list(self.open_orders.values())
            if symbol:
                return [o for o in all_orders if (o.get("product_symbol") == symbol or o.get("symbol") == symbol)]
            return all_orders

    def get_positions(self) -> List[Dict[str, Any]]:
        with self.lock:
            return list(self.positions.values())

    def get_recent_fills(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self.lock:
            return list(self.fills)[-limit:]

account_engine = AccountEngine()
