import os
import time
import json
import hmac
import hashlib
import socket
import urllib3.util.connection as urllib3_cn
import requests
from typing import Dict, Any, List, Optional
from config import delta_config, system_config

# Force IPv4 resolution to prevent IPv6 from tripping Delta Exchange IP whitelist
try:
    urllib3_cn.allowed_gai_family = lambda: socket.AF_INET
except Exception:
    pass

class DeltaClient:
    """
    Delta Exchange v2 API Client (supports Delta Global & Delta India).
    Handles public market feeds, L2 orderbook, candles, and signed private operations.
    """
    def __init__(self, base_url: Optional[str] = None, api_key: Optional[str] = None, api_secret: Optional[str] = None):
        from dotenv import load_dotenv
        load_dotenv(override=True)
        self.base_url = base_url or os.getenv("DELTA_BASE_URL", "https://api.india.delta.exchange")
        self.api_key = api_key or os.getenv("DELTA_API_KEY", "")
        self.api_secret = api_secret or os.getenv("DELTA_API_SECRET", "")
        self.timeout = delta_config.REQUEST_TIMEOUT
        self.session = requests.Session()
        self.products_cache: Dict[str, Dict[str, Any]] = {}
        self._load_products()

    def reload_credentials(self):
        from dotenv import load_dotenv
        load_dotenv(override=True)
        self.base_url = os.getenv("DELTA_BASE_URL", "https://api.india.delta.exchange")
        self.api_key = os.getenv("DELTA_API_KEY", "")
        self.api_secret = os.getenv("DELTA_API_SECRET", "")
        self._load_products()

    def _generate_signature(self, method: str, timestamp: str, path: str, query_or_body: str = "") -> str:
        message = method + timestamp + path + query_or_body
        signature = hmac.new(
            self.api_secret.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()
        return signature

    def _get_auth_headers(self, method: str, path: str, query_or_body: str = "") -> Dict[str, str]:
        timestamp = str(int(time.time()))
        signature = self._generate_signature(method, timestamp, path, query_or_body)
        return {
            "api-key": self.api_key,
            "signature": signature,
            "timestamp": timestamp,
            "Content-Type": "application/json",
            "User-Agent": "AgyDeltaRwaTrader/1.0"
        }

    def _load_products(self):
        """Preload and cache product metadata for quick ID & contract value lookups."""
        try:
            url = f"{self.base_url}/v2/products"
            resp = self.session.get(url, timeout=self.timeout)
            if resp.status_code == 200:
                data = resp.json().get("result", [])
                for p in data:
                    sym = p.get("symbol")
                    if sym:
                        self.products_cache[sym] = {
                            "id": p.get("id"),
                            "symbol": sym,
                            "contract_type": p.get("contract_type"),
                            "contract_value": float(p.get("contract_value", 0.01)),
                            "tick_size": float(p.get("tick_size", 0.01)),
                            "initial_margin": float(p.get("initial_margin", 4)),
                            "maintenance_margin": float(p.get("maintenance_margin", 2)),
                            "quoting_asset": p.get("quoting_asset", {}).get("symbol", "USD")
                        }
        except Exception as e:
            print(f"[DeltaClient] Error caching products: {e}")

    def get_product(self, symbol: str) -> Optional[Dict[str, Any]]:
        if not self.products_cache:
            self._load_products()
        return self.products_cache.get(symbol)

    def get_ticker(self, symbol: str) -> Dict[str, Any]:
        """Fetch live ticker and mark price for an asset."""
        try:
            url = f"{self.base_url}/v2/tickers/{symbol}"
            resp = self.session.get(url, timeout=self.timeout)
            if resp.status_code == 200:
                res = resp.json().get("result", {})
                mark = float(res.get("mark_price", 0.0))
                spot = float(res.get("spot_price", mark))
                vol = float(res.get("volume_24h", 0.0) or 0.0)
                chg = float(res.get("price_change_24h", 0.0) or 0.0)
                return {
                    "symbol": symbol,
                    "mark_price": mark,
                    "spot_price": spot,
                    "volume_24h": vol,
                    "price_change_24h": chg,
                    "timestamp": time.time(),
                    "success": True
                }
        except Exception as e:
            print(f"[DeltaClient] Ticker error for {symbol}: {e}")
        return {"symbol": symbol, "mark_price": 0.0, "success": False}

    def get_l2_orderbook(self, symbol: str) -> Dict[str, Any]:
        """Fetch L2 orderbook depth to analyze bid/ask liquidity and skew."""
        try:
            url = f"{self.base_url}/v2/l2orderbook/{symbol}"
            resp = self.session.get(url, timeout=self.timeout)
            if resp.status_code == 200:
                res = resp.json().get("result", {})
                bids = res.get("buy", [])
                asks = res.get("sell", [])
                
                best_bid = float(bids[0]["price"]) if bids else 0.0
                best_ask = float(asks[0]["price"]) if asks else 0.0
                spread = best_ask - best_bid if best_ask and best_bid else 0.0
                spread_pct = (spread / best_ask) * 100 if best_ask else 0.0
                
                # Depth volume (top 10 levels)
                bid_vol = sum(float(b.get("size", 0)) for b in bids[:10])
                ask_vol = sum(float(a.get("size", 0)) for a in asks[:10])
                imbalance = (bid_vol - ask_vol) / (bid_vol + ask_vol) if (bid_vol + ask_vol) > 0 else 0.0
                
                return {
                    "symbol": symbol,
                    "best_bid": best_bid,
                    "best_ask": best_ask,
                    "spread": spread,
                    "spread_pct": spread_pct,
                    "bid_depth_10": bid_vol,
                    "ask_depth_10": ask_vol,
                    "imbalance": imbalance,  # +1.0 (heavy bid wall) to -1.0 (heavy ask wall)
                    "raw_bids": bids[:10],
                    "raw_asks": asks[:10],
                    "success": True
                }
        except Exception as e:
            print(f"[DeltaClient] L2 orderbook error for {symbol}: {e}")
        return {"symbol": symbol, "best_bid": 0.0, "best_ask": 0.0, "imbalance": 0.0, "success": False}

    def get_candles(self, symbol: str, resolution: str = "15m", count: int = 100) -> List[Dict[str, Any]]:
        """
        Fetch historical OHLCV candles.
        Resolutions supported: 1m, 3m, 5m, 15m, 30m, 1h, 2h, 4h, 1d.
        """
        try:
            res_seconds = {
                "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
                "1h": 3600, "2h": 7200, "4h": 14400, "1d": 86400
            }.get(resolution, 900)
            
            end = int(time.time())
            start = end - (res_seconds * count)
            
            url = f"{self.base_url}/v2/history/candles?resolution={resolution}&symbol={symbol}&start={start}&end={end}"
            resp = self.session.get(url, timeout=self.timeout)
            if resp.status_code == 200:
                candles = resp.json().get("result", [])
                # Delta candles come newest first or oldest first; ensure chronological order (oldest to newest)
                if candles and candles[0].get("time", 0) > candles[-1].get("time", 0):
                    candles.reverse()
                return candles
        except Exception as e:
            print(f"[DeltaClient] Candle error for {symbol}: {e}")
        return []

    def get_multitimeframe_candles(self, symbol: str) -> Dict[str, List[Dict[str, Any]]]:
        """
        Fetch synchronized candles across 3 critical horizons:
        - 1-Hour (Macro Tide, count=250 for true EMA 200 convergence)
        - 15-Minute (Structural Wave, count=250 for Qlib Alphas)
        - 5-Minute (Micro Trigger & Structural Stop Pivot, count=60)
        """
        candles_1h = self.get_candles(symbol, resolution="1h", count=250)
        candles_15m = self.get_candles(symbol, resolution="15m", count=250)
        candles_5m = self.get_candles(symbol, resolution="5m", count=60)

        # Fallback handling if intermediate/macro is sparse
        if not candles_15m and candles_1h:
            candles_15m = candles_1h
        if not candles_5m and candles_15m:
            candles_5m = candles_15m

        return {
            "1h": candles_1h,
            "15m": candles_15m,
            "5m": candles_5m
        }

    # ================= Private API Methods =================
    def get_wallet_balances(self) -> Dict[str, Any]:
        """Fetch account wallet balances (USD / USDT)."""
        if not self.api_key or not self.api_secret:
            return {"error": "API credentials missing", "success": False}
        path = "/v2/wallet/balances"
        headers = self._get_auth_headers("GET", path)
        try:
            resp = self.session.get(f"{self.base_url}{path}", headers=headers, timeout=self.timeout)
            if resp.status_code == 200:
                return {"result": resp.json().get("result", []), "success": True}
            return {"error": resp.text, "status_code": resp.status_code, "success": False}
        except Exception as e:
            return {"error": str(e), "success": False}

    def set_leverage(self, product_id: int, leverage: int = 4) -> Dict[str, Any]:
        """Set isolated leverage for a specific product."""
        if not self.api_key or not self.api_secret:
            return {"error": "API credentials missing", "success": False}
        path = f"/v2/products/{product_id}/orders/leverage"
        body = json.dumps({"leverage": str(leverage)})
        headers = self._get_auth_headers("POST", path, body)
        try:
            resp = self.session.post(f"{self.base_url}{path}", data=body, headers=headers, timeout=self.timeout)
            if resp.status_code in (200, 201):
                return {"result": resp.json(), "success": True}
            return {"error": resp.text, "status_code": resp.status_code, "success": False}
        except Exception as e:
            return {"error": str(e), "success": False}

    def _format_price_for_product(self, price: float, prod: Dict[str, Any]) -> str:
        tick_size = float(prod.get("tick_size") or 0.01)
        rounded = round(price / tick_size) * tick_size
        if tick_size >= 1.0:
            return str(int(round(rounded)))
        elif tick_size >= 0.1:
            return f"{rounded:.1f}"
        elif tick_size >= 0.01:
            return f"{rounded:.2f}"
        elif tick_size >= 0.001:
            return f"{rounded:.3f}"
        else:
            return f"{rounded:.4f}"

    def place_bracket_order(self, symbol: str, size: int, side: str, order_type: str = "market_order",
                            limit_price: Optional[float] = None, stop_loss_price: Optional[float] = None,
                            take_profit_price: Optional[float] = None,
                            cl_ord_id: Optional[str] = None,
                            post_only: bool = False) -> Dict[str, Any]:
        """
        Place an order with built-in Stop-Loss and Take-Profit brackets.
        Ensures trades fail closed with exchange-native risk containment.
        Tags order with client_order_id (BOT_...) to separate from manual discretionary trades.
        Supports post_only=True for smart maker execution without taker fees.
        """
        if not self.api_key or not self.api_secret:
            return {"error": "API credentials missing", "success": False}
            
        prod = self.get_product(symbol)
        if not prod:
            return {"error": f"Unknown symbol {symbol}", "success": False}
            
        path = "/v2/orders"
        payload: Dict[str, Any] = {
            "product_id": prod["id"],
            "size": size,
            "side": side.lower(),
            "order_type": order_type
        }
        if cl_ord_id:
            payload["client_order_id"] = cl_ord_id
        if post_only:
            payload["post_only"] = "true"
        if limit_price and order_type == "limit_order":
            payload["limit_price"] = self._format_price_for_product(limit_price, prod)
        if stop_loss_price:
            payload["bracket_stop_loss_price"] = self._format_price_for_product(stop_loss_price, prod)
        if take_profit_price:
            payload["bracket_take_profit_price"] = self._format_price_for_product(take_profit_price, prod)
            
        body = json.dumps(payload)
        headers = self._get_auth_headers("POST", path, body)
        try:
            resp = self.session.post(f"{self.base_url}{path}", data=body, headers=headers, timeout=self.timeout)
            if resp.status_code in (200, 201):
                return {"result": resp.json().get("result", {}), "success": True}
            return {"error": resp.text, "status_code": resp.status_code, "success": False}
        except Exception as e:
            return {"error": str(e), "success": False}

    def emergency_flatten(self, symbol: str, size: int, side: str) -> Dict[str, Any]:
        """Emergency market close (reduce-only) to flatten a position immediately."""
        if not self.api_key or not self.api_secret:
            return {"error": "API credentials missing", "success": False}
        prod = self.get_product(symbol)
        if not prod:
            return {"error": f"Unknown symbol {symbol}", "success": False}
        path = "/v2/orders"
        # Opposite side to close
        close_side = "sell" if side.upper() == "BUY" else "buy"
        payload = {
            "product_id": prod["id"],
            "size": abs(int(size)),
            "side": close_side,
            "order_type": "market_order",
            "reduce_only": True
        }
        body = json.dumps(payload)
        headers = self._get_auth_headers("POST", path, body)
        try:
            resp = self.session.post(f"{self.base_url}{path}", data=body, headers=headers, timeout=self.timeout)
            if resp.status_code in (200, 201):
                return {"result": resp.json().get("result", {}), "success": True}
            return {"error": resp.text, "status_code": resp.status_code, "success": False}
        except Exception as e:
            return {"error": str(e), "success": False}

    def get_positions(self) -> Dict[str, Any]:
        """Fetch open margined positions."""
        if not self.api_key or not self.api_secret:
            return {"error": "API credentials missing", "success": False}
        path = "/v2/positions/margined"
        headers = self._get_auth_headers("GET", path)
        try:
            resp = self.session.get(f"{self.base_url}{path}", headers=headers, timeout=self.timeout)
            if resp.status_code == 200:
                return {"result": resp.json().get("result", []), "success": True}
            return {"error": resp.text, "status_code": resp.status_code, "success": False}
        except Exception as e:
            return {"error": str(e), "success": False}

    def get_open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """Fetch all currently open/pending orders on Delta Exchange."""
        if not self.api_key or not self.api_secret:
            return []
        path = "/v2/orders?state=open"
        headers = self._get_auth_headers("GET", path)
        try:
            resp = self.session.get(f"{self.base_url}{path}", headers=headers, timeout=self.timeout)
            if resp.status_code == 200:
                orders = resp.json().get("result", [])
                if symbol:
                    orders = [o for o in orders if o.get("product_symbol") == symbol]
                return orders
        except Exception as e:
            print(f"[DeltaClient] Error fetching open orders: {e}")
        return []

    def edit_order(self, order_id: int, product_id: int, size: int, stop_price: float, symbol: Optional[str] = None) -> Dict[str, Any]:
        """Modify an existing order (such as ratcheting/trailing the Stop-Loss)."""
        if not self.api_key or not self.api_secret:
            return {"error": "API credentials missing", "success": False}
        path = "/v2/orders"
        prod = self.get_product(symbol) if symbol else None
        formatted_price = self._format_price_for_product(stop_price, prod) if prod else f"{stop_price:.2f}"
        payload = {
            "id": int(order_id),
            "product_id": int(product_id),
            "size": int(size),
            "stop_price": formatted_price
        }
        body = json.dumps(payload)
        headers = self._get_auth_headers("PUT", path, body)
        try:
            resp = self.session.put(f"{self.base_url}{path}", data=body, headers=headers, timeout=self.timeout)
            if resp.status_code in (200, 201):
                return {"result": resp.json(), "success": True}
            return {"error": resp.text, "status_code": resp.status_code, "success": False}
        except Exception as e:
            return {"error": str(e), "success": False}

    def get_wallet_balances(self) -> Dict[str, Any]:
        """Fetch user wallet balances from Delta Exchange."""
        if not self.api_key or not self.api_secret:
            return {"error": "API credentials missing", "success": False, "result": []}
        path = "/v2/wallet/balances"
        headers = self._get_auth_headers("GET", path)
        try:
            resp = self.session.get(f"{self.base_url}{path}", headers=headers, timeout=self.timeout)
            if resp.status_code == 200:
                return {"result": resp.json().get("result", []), "success": True}
            return {"error": resp.text, "status_code": resp.status_code, "success": False, "result": []}
        except Exception as e:
            return {"error": str(e), "success": False, "result": []}

    def get_balance(self) -> Dict[str, Any]:
        """Convenience method returning aggregated USD/USDT wallet balance."""
        wallets = self.get_wallet_balances()
        total = 0.0
        if wallets.get("success"):
            for b in wallets.get("result", []):
                bal = float(b.get("balance", 0.0))
                if b.get("asset_symbol") in ("USD", "USDT") and bal > 0:
                    total += bal
        return {"balance": round(total, 2), "success": wallets.get("success", False)}

delta_client = DeltaClient()
