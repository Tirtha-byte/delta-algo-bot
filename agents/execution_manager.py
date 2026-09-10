import time
from typing import Dict, Any, Optional
from delta_client import delta_client
from agents.shadow_account import shadow_account
from config import system_config

class ExecutionManagerAgent:
    """
    Agent 4: Portfolio & Execution Manager.
    Dispatches orders to either the Vibe Shadow Account (simulation) or Live Delta Exchange API.
    Features:
    - Delta Orderbook Native TP2 Placement (Resting Runner Target)
    - Mental TP1 Validation with 30-Second Persistence Buffer
    - Dynamic ATR Chandelier Ratchet Engine
    """
    def __init__(self):
        self.mode = system_config.MODE  # "SHADOW" or "LIVE"
        self.last_live_positions: Dict[str, Dict[str, Any]] = {}
        # Tracks mental targets, peak prices, and chandelier stop state per asset
        self.active_trade_trackers: Dict[str, Dict[str, Any]] = {}

    def set_mode(self, mode: str):
        if mode in ("SHADOW", "LIVE"):
            self.mode = mode

    def execute_order(self, proposal: Dict[str, Any], risk_approval: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute an approved trade.
        """
        if not risk_approval.get("approved"):
            return {
                "success": False,
                "error": f"Risk Governor Rejected: {risk_approval.get('reason')}"
            }

        symbol = proposal["symbol"]
        side = proposal["signal"]  # BUY or SELL
        contracts = risk_approval["contracts"]
        contract_val = risk_approval["contract_val"]
        leverage = risk_approval["isolated_leverage"]
        entry = proposal["entry_price"]
        stop_loss = proposal["stop_loss"]
        tp1 = proposal["take_profit_1"]
        tp2 = proposal["take_profit_2"]
        rationale = proposal.get("rationale", "Multi-agent dual-key consensus")

        if self.mode == "SHADOW":
            # Execute in Vibe Shadow Account Engine
            pos = shadow_account.open_position(
                symbol=symbol,
                side=side,
                contracts=contracts,
                entry_price=entry,
                stop_loss=stop_loss,
                take_profit_1=tp1,
                take_profit_2=tp2,
                contract_val=contract_val,
                leverage=leverage,
                rationale=rationale
            )
            return {
                "success": True,
                "mode": "SHADOW",
                "execution_venue": "Vibe Shadow Account Simulation",
                "position": pos,
                "message": f"Simulated {side} {contracts} contracts {symbol} @ ${entry:.2f} (SL: ${stop_loss:.2f}, TP1: ${tp1:.2f})"
            }
        else:
            # Execute Live on Delta Exchange with TP2 resting on exchange orderbook
            prod = delta_client.get_product(symbol)
            if not prod:
                return {"success": False, "error": f"Product {symbol} not found on Delta Exchange"}

            # 1. Microstructure Adverse Selection Pre-Flight Check
            micro_status = proposal.get("micro_status", "NEUTRAL")
            if micro_status == "ADVERSE":
                return {
                    "success": False,
                    "error": f"Microstructure Abort: Orderbook state is ADVERSE ({proposal.get('micro_drift', 0):+.2f} drift against {side}). Entry cancelled to prevent adverse selection."
                }

            # Set isolated leverage
            delta_client.set_leverage(prod["id"], leverage)

            # 2. Smart Maker-First Attempt at Best Bid (BUY) or Best Ask (SELL)
            cl_ord_id = f"BOT_{symbol}_{int(time.time())}"
            ob = delta_client.get_l2_orderbook(symbol)
            best_bid = float(ob.get("best_bid", 0.0))
            best_ask = float(ob.get("best_ask", 0.0))
            spread_pct = float(ob.get("spread_pct", 0.0))
            
            maker_filled = False
            execution_type = "MARKET"
            resp = None

            # Attempt post-only limit order if spread is between 0.01% and 0.25%
            if best_bid > 0 and best_ask > 0 and 0.01 <= spread_pct <= 0.25:
                maker_limit = best_bid if side == "BUY" else best_ask
                maker_resp = delta_client.place_bracket_order(
                    symbol=symbol,
                    size=contracts,
                    side=side,
                    order_type="limit_order",
                    limit_price=maker_limit,
                    stop_loss_price=stop_loss,
                    take_profit_price=tp2,
                    cl_ord_id=cl_ord_id,
                    post_only=True
                )
                if maker_resp.get("success"):
                    order_info = maker_resp.get("result", {})
                    order_id = order_info.get("id")
                    # Poll for 3 seconds to check if filled as maker
                    start_wait = time.time()
                    while time.time() - start_wait < 3.0:
                        time.sleep(1.0)
                        # Check fresh book for adverse drift
                        fresh_ob = delta_client.get_l2_orderbook(symbol)
                        from agents.microstructure_engine import microstructure_engine
                        fresh_micro = microstructure_engine.analyze_orderbook(fresh_ob, signal=side)
                        if fresh_micro.get("status") == "ADVERSE":
                            # Orderbook turned adverse! Cancel resting maker order immediately
                            if order_id:
                                try:
                                    import json
                                    c_body = json.dumps({"product_id": prod["id"], "id": order_id})
                                    delta_client.session.delete(
                                        f"{delta_client.base_url}/v2/orders",
                                        data=c_body,
                                        headers=delta_client._get_auth_headers("DELETE", "/v2/orders", c_body),
                                        timeout=delta_client.timeout
                                    )
                                except Exception:
                                    pass
                            return {
                                "success": False,
                                "error": f"Maker order cancelled: Microstructure shifted to ADVERSE ({fresh_micro.get('micro_drift', 0):+.2f})"
                            }

                        open_orders = delta_client.get_open_orders(symbol)
                        if not any(o.get("id") == order_id for o in open_orders):
                            # Filled as maker!
                            maker_filled = True
                            resp = maker_resp
                            execution_type = "MAKER_POST_ONLY"
                            entry = maker_limit
                            break

                    if not maker_filled and order_id:
                        # Cancel unfilled maker order before stepping to market
                        try:
                            import json
                            c_body = json.dumps({"product_id": prod["id"], "id": order_id})
                            delta_client.session.delete(
                                f"{delta_client.base_url}/v2/orders",
                                data=c_body,
                                headers=delta_client._get_auth_headers("DELETE", "/v2/orders", c_body),
                                timeout=delta_client.timeout
                            )
                        except Exception:
                            pass

            # 3. Controlled Fallback to Market Order if maker not filled
            if not maker_filled:
                resp = delta_client.place_bracket_order(
                    symbol=symbol,
                    size=contracts,
                    side=side,
                    order_type="market_order",
                    stop_loss_price=stop_loss,
                    take_profit_price=tp2,
                    cl_ord_id=cl_ord_id
                )
                execution_type = "MARKET_FALLBACK"

            if resp and resp.get("success"):
                price_risk = abs(entry - stop_loss)
                calculated_atr = price_risk / 1.5 if price_risk > 0 else (entry * 0.015)
                self.active_trade_trackers[symbol] = {
                    "cl_ord_id": cl_ord_id,
                    "is_bot_order": True,
                    "execution_type": execution_type,
                    "entry_price": entry,
                    "side": side,
                    "tp1": tp1,
                    "tp2": tp2,
                    "initial_stop_loss": stop_loss,
                    "current_stop_loss": stop_loss,
                    "atr": calculated_atr,
                    "peak_price": entry,
                    "tp1_first_reached_at": None,
                    "tp1_partial_closed": False,
                    "breakeven_activated": False,
                    "stop_edit_retries": 0
                }
            return {
                "success": resp.get("success", False) if resp else False,
                "mode": "LIVE",
                "execution_venue": f"Delta Exchange Live API ({execution_type})",
                "response": resp,
                "message": f"Live order submitted to Delta ({execution_type}): {side} {contracts} {symbol} (SL: ${stop_loss:.2f}, TP1: ${tp1:.2f}, Delta TP2: ${tp2:.2f})"
            }

    def sync_portfolio(self, current_tickers: Dict[str, float]) -> Dict[str, Any]:
        """
        Synchronize portfolio state and execute Dynamic ATR Chandelier Ratchet trailing stops.
        """
        if self.mode == "SHADOW":
            closed_events = shadow_account.update_market_prices(current_tickers)
            summary = shadow_account.get_performance_summary()
            summary["closed_events"] = closed_events
            return summary
        else:
            # Live Delta positions & dynamic wallet balance
            positions = delta_client.get_positions()
            balances = delta_client.get_wallet_balances()
            
            live_usd_balance = 0.0
            if balances.get("success"):
                for b in balances.get("result", []):
                    bal = float(b.get("balance", 0))
                    if b.get("asset_symbol") in ("USD", "USDT") and bal > 0:
                        live_usd_balance += bal
                        
            from config import compounding_config
            if live_usd_balance <= 0:
                live_usd_balance = compounding_config.STARTING_CAPITAL

            if not positions.get("success", False):
                # API error or timeout - preserve current state and avoid false position close alerts
                return {
                    "current_balance": live_usd_balance,
                    "open_positions": list(self.last_live_positions.values()) if self.last_live_positions else [],
                    "open_positions_count": len(self.last_live_positions) if self.last_live_positions else 0
                }

            current_pos_list = positions.get("result", [])
            current_active: Dict[str, Dict[str, Any]] = {}
            for p in current_pos_list:
                size = float(p.get("size", 0))
                sym = p.get("product_symbol")
                if abs(size) > 0 and sym:
                    current_active[sym] = p

            # Check if any previously active live positions closed on Delta
            if self.last_live_positions:
                for sym, prev_p in self.last_live_positions.items():
                    if sym not in current_active and abs(float(prev_p.get("size", 0))) > 0:
                        # Position closed on live Delta Exchange!
                        prev_size = float(prev_p.get("size", 0))
                        prev_side = "BUY" if prev_size > 0 else "SELL"
                        realized_pnl = float(prev_p.get("realized_pnl", 0))
                        self.active_trade_trackers.pop(sym, None)
                        
                        # Trigger Self-Improving Forensic Autopsy for Live trade
                        try:
                            from agents.forensic_learner import forensic_learner
                            entry_p = float(prev_p.get("entry_price", 0))
                            exit_p = current_tickers.get(sym, entry_p)
                            forensic_learner.conduct_autopsy({
                                "symbol": sym,
                                "side": prev_side,
                                "realized_pnl": realized_pnl,
                                "entry_price": entry_p,
                                "exit_price": exit_p,
                                "opened_at": prev_p.get("created_at")
                            })
                        except Exception as e:
                            print(f"[ExecutionManager] Live autopsy error: {e}")

                        try:
                            from agents.telegram_notifier import telegram_notifier
                            telegram_notifier.notify_position_closed(
                                symbol=sym,
                                side=prev_side,
                                pnl=realized_pnl,
                                return_pct=0.0,
                                reason="DELTA_EXCHANGE_FILL",
                                balance=live_usd_balance
                            )
                        except Exception:
                            pass

            self.last_live_positions = current_active

            # ================= Dynamic ATR Chandelier Ratchet Engine =================
            now = time.time()
            for sym, p in current_active.items():
                mark = current_tickers.get(sym) or float(p.get("mark_price", 0))
                if mark <= 0:
                    continue

                size = float(p.get("size", 0))
                is_long = size > 0
                entry = float(p.get("entry_price", 0))

                # Auto-initialize tracker if position was opened outside session
                if sym not in self.active_trade_trackers:
                    risk_dist = entry * 0.015
                    self.active_trade_trackers[sym] = {
                        "cl_ord_id": f"MANUAL_{sym}",
                        "is_bot_order": False,
                        "entry_price": entry,
                        "side": "BUY" if is_long else "SELL",
                        "tp1": round(entry + (2.5 * risk_dist), 2) if is_long else round(entry - (2.5 * risk_dist), 2),
                        "tp2": round(entry + (4.0 * risk_dist), 2) if is_long else round(entry - (4.0 * risk_dist), 2),
                        "initial_stop_loss": round(entry - risk_dist, 2) if is_long else round(entry + risk_dist, 2),
                        "current_stop_loss": round(entry - risk_dist, 2) if is_long else round(entry + risk_dist, 2),
                        "atr": risk_dist,
                        "peak_price": mark,
                        "tp1_first_reached_at": None,
                        "breakeven_activated": False,
                        "stop_edit_retries": 0
                    }

                tracker = self.active_trade_trackers[sym]
                atr = tracker.get("atr", entry * 0.015)

                # Update Peak Price
                if is_long:
                    tracker["peak_price"] = max(tracker.get("peak_price", entry), mark)
                else:
                    tracker["peak_price"] = min(tracker.get("peak_price", entry), mark)

                # Step 1: Check Mental TP1 with 30-Second Persistence Buffer
                tp1_crossed = (mark >= tracker["tp1"]) if is_long else (mark <= tracker["tp1"])
                if tp1_crossed and not tracker["breakeven_activated"]:
                    if tracker.get("tp1_first_reached_at") is None:
                        tracker["tp1_first_reached_at"] = now
                    elif (now - tracker["tp1_first_reached_at"]) >= 30:
                        # 30-Second Persistence Validated!
                        # If position has >= 2 contracts, close 50% reduce-only at TP1 to bank profit
                        pos_size = abs(int(size))
                        if pos_size >= 2 and not tracker.get("tp1_partial_closed"):
                            close_qty = pos_size // 2
                            delta_client.emergency_flatten(sym, close_qty, "BUY" if is_long else "SELL")
                            tracker["tp1_partial_closed"] = True
                            print(f"[ExecutionManager] TP1 REACHED: Closed 50% ({close_qty}/{pos_size} contracts) of {sym} at TP1 (${tracker['tp1']:.2f})!")

                        # Move SL to Breakeven (+0.2% fee cushion) on remainder
                        fee_buffer = entry * 0.002
                        be_stop = round(entry + fee_buffer, 2) if is_long else round(entry - fee_buffer, 2)

                        open_orders = delta_client.get_open_orders(sym)
                        sl_order = next((o for o in open_orders if o.get("stop_order_type") == "stop_loss_order"), None)
                        if sl_order:
                            edit_res = delta_client.edit_order(
                                order_id=sl_order["id"],
                                product_id=sl_order["product_id"],
                                size=sl_order["size"],
                                stop_price=be_stop,
                                symbol=sym
                            )
                            if edit_res.get("success"):
                                tracker["stop_edit_retries"] = 0
                                tracker["breakeven_activated"] = True
                                tracker["current_stop_loss"] = be_stop
                                try:
                                    from agents.telegram_notifier import telegram_notifier
                                    telegram_notifier.send_message(
                                        f"🛡️ <b>BREAKEVEN SECURED: {sym}</b>\n\n"
                                        f"Mental TP1 (${tracker['tp1']:.2f}) verified across 30s buffer!\n"
                                        f"Delta Stop-Loss raised to <b>${be_stop:.2f}</b> (Entry + fees).\n"
                                        f"<b>Risk is now $0.00</b> with full breathing room while riding for TP2 (${tracker['tp2']:.2f})!"
                                    )
                                except Exception:
                                    pass
                            else:
                                tracker["stop_edit_retries"] = tracker.get("stop_edit_retries", 0) + 1
                                print(f"[ExecutionManager] Stop edit failed for {sym}, attempt {tracker['stop_edit_retries']}/3: {edit_res.get('error')}")
                                if tracker["stop_edit_retries"] >= 3:
                                    side_to_close = "SELL" if is_long else "BUY"
                                    print(f"[ExecutionManager] CRITICAL: Stop loss modification failed 3 times for {sym}! Triggering EMERGENCY FLATTEN.")
                                    flatten_res = delta_client.emergency_flatten(sym, abs(size), side_to_close)
                                    try:
                                        from agents.telegram_notifier import telegram_notifier
                                        telegram_notifier.send_message(
                                            f"🚨 <b>EMERGENCY FLATTEN TRIGGERED: {sym}</b>\n\n"
                                            f"Delta stop-loss modification failed 3 consecutive times.\n"
                                            f"Position {sym} ({abs(size)} contracts) was market flattened for capital protection.\n"
                                            f"Status: {flatten_res.get('message', 'Submitted')}"
                                        )
                                    except Exception:
                                        pass

                # Step 2: Dynamic Chandelier Ratchet (as price advances past TP1 toward TP2)
                if tracker.get("breakeven_activated"):
                    # Chandelier Stop: Peak - (1.2 * ATR) for Long, Peak + (1.2 * ATR) for Short
                    chandelier_stop = round(tracker["peak_price"] - (1.2 * atr), 2) if is_long else round(tracker["peak_price"] + (1.2 * atr), 2)
                    current_sl = tracker.get("current_stop_loss", entry)

                    # RATCHET RULE: Stop can ONLY move in direction of profit, and must advance by >= 0.25 ATR
                    should_ratchet = (chandelier_stop > current_sl + (atr * 0.25)) if is_long else (chandelier_stop < current_sl - (atr * 0.25))

                    if should_ratchet:
                        open_orders = delta_client.get_open_orders(sym)
                        sl_order = next((o for o in open_orders if o.get("stop_order_type") == "stop_loss_order"), None)
                        if sl_order:
                            edit_res = delta_client.edit_order(
                                order_id=sl_order["id"],
                                product_id=sl_order["product_id"],
                                size=sl_order["size"],
                                stop_price=chandelier_stop,
                                symbol=sym
                            )
                            if edit_res.get("success"):
                                tracker["stop_edit_retries"] = 0
                                tracker["current_stop_loss"] = chandelier_stop
                                try:
                                    from agents.telegram_notifier import telegram_notifier
                                    telegram_notifier.send_message(
                                        f"📈 <b>CHANDELIER RATCHET ADVANCED: {sym}</b>\n\n"
                                        f"Peak reached ${tracker['peak_price']:.2f}.\n"
                                        f"Delta Stop-Loss dynamically raised to <b>${chandelier_stop:.2f}</b>.\n"
                                        f"Locking in progressive profits on the way to TP2 (${tracker['tp2']:.2f})!"
                                    )
                                except Exception:
                                    pass
                            else:
                                tracker["stop_edit_retries"] = tracker.get("stop_edit_retries", 0) + 1
                                print(f"[ExecutionManager] Stop edit failed for {sym}, attempt {tracker['stop_edit_retries']}/3: {edit_res.get('error')}")
                                if tracker["stop_edit_retries"] >= 3:
                                    side_to_close = "SELL" if is_long else "BUY"
                                    print(f"[ExecutionManager] CRITICAL: Stop loss modification failed 3 times for {sym}! Triggering EMERGENCY FLATTEN.")
                                    flatten_res = delta_client.emergency_flatten(sym, abs(size), side_to_close)
                                    try:
                                        from agents.telegram_notifier import telegram_notifier
                                        telegram_notifier.send_message(
                                            f"🚨 <b>EMERGENCY FLATTEN TRIGGERED: {sym}</b>\n\n"
                                            f"Delta stop-loss modification failed 3 consecutive times.\n"
                                            f"Position {sym} ({abs(size)} contracts) was market flattened for capital protection.\n"
                                            f"Status: {flatten_res.get('message', 'Submitted')}"
                                        )
                                    except Exception:
                                        pass

            # Tag positions in returned list
            for p in current_pos_list:
                s = p.get("product_symbol")
                if s in self.active_trade_trackers:
                    p["is_bot_trade"] = self.active_trade_trackers[s].get("is_bot_order", False)
                    p["cl_ord_id"] = self.active_trade_trackers[s].get("cl_ord_id", f"MANUAL_{s}")
                else:
                    p["is_bot_trade"] = False
                    p["cl_ord_id"] = f"MANUAL_{s}"

            return {
                "mode": "LIVE",
                "current_balance": live_usd_balance,
                "positions": current_pos_list,
                "balances": balances.get("result", [])
            }

execution_manager = ExecutionManagerAgent()
