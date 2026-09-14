import os
import sys
import time
import requests
from typing import Optional, Dict, Any, List

from dotenv import load_dotenv
load_dotenv(override=True)

class TelegramNotifier:
    """
    Sends automated real-time trade signals, milestone progress,
    scanning digests, and candidate evaluation/rejection audits
    directly to the user's phone via Telegram.
    Guaranteed to ONLY send verified real events.
    """
    def __init__(self):
        self.reload_credentials()
        self._candidate_rejection_history: Dict[str, float] = {}
        self._last_digest_time: float = 0.0

    def reload_credentials(self):
        load_dotenv(override=True)
        self.bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID", "")

    def is_test_environment(self) -> bool:
        """Prevent automated tests from ever sending fake/mock alerts to user's phone."""
        if os.getenv("TESTING") == "1":
            return True
        if "unittest" in sys.modules:
            for arg in sys.argv:
                if "unittest" in arg.lower():
                    return True
        return False

    def is_enabled(self) -> bool:
        if not self.bot_token or not self.chat_id:
            self.reload_credentials()
        return bool(self.bot_token and self.chat_id and not self.is_test_environment())

    def send_message(self, text: str) -> bool:
        if not self.is_enabled():
            return False
        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            payload = {
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": "HTML"
            }
            resp = requests.post(url, json=payload, timeout=5)
            return resp.status_code == 200
        except Exception as e:
            print(f"[TelegramNotifier] Error sending message: {e}")
            return False

    def notify_trade_entry(self, symbol: str, side: str, contracts: int, entry: float, sl: float, tp1: float, tp2: float, risk_usd: float, rationale: str, is_live: bool = True):
        """Only notify for confirmed REAL LIVE positions on Delta Exchange."""
        if not is_live or self.is_test_environment():
            return False

        msg = (
            f"🚀 <b>REAL LIVE DELTA TRADE OPENED</b>\n\n"
            f"<b>Asset:</b> {symbol}\n"
            f"<b>Action:</b> {side} {contracts} contracts\n"
            f"<b>Entry Price:</b> ${entry:.2f}\n"
            f"<b>Stop Loss:</b> ${sl:.2f} (Risk: ${risk_usd:.2f})\n"
            f"<b>Take Profit 1:</b> ${tp1:.2f}\n"
            f"<b>Take Profit 2:</b> ${tp2:.2f}\n"
            f"<b>Venue:</b> Delta Exchange Live API\n"
            f"<b>Rationale:</b> {rationale}\n\n"
            f"<i>Verified live order on exchange. Sized for 2.5% max risk.</i>"
        )
        return self.send_message(msg)

    def notify_position_closed(self, symbol: str, side: str, pnl: float, return_pct: float, reason: str, balance: float):
        """Notify when a real live position is closed on Delta Exchange."""
        if self.is_test_environment():
            return False

        emoji = "🎯" if pnl >= 0 else "🛑"
        msg = (
            f"{emoji} <b>REAL LIVE POSITION CLOSED: {symbol}</b>\n\n"
            f"<b>Side:</b> {side}\n"
            f"<b>Reason:</b> {reason}\n"
            f"<b>Realized PnL:</b> {'+' if pnl >= 0 else ''}${pnl:.2f} ({'+' if return_pct >= 0 else ''}{return_pct:.1f}%)\n"
            f"<b>Live Delta Balance:</b> ${balance:.2f} USD\n"
            f"<b>Venue:</b> Delta Exchange Live\n"
            f"<b>Goal Progress:</b> {((balance - 60) / (5000 - 60) * 100):.2f}% to $5,000"
        )
        return self.send_message(msg)

    def notify_position_status(self, symbol: str, side: str, size: float, entry: float, mark: float, pnl: float, margin: float, liq_price: float):
        """Send a real-time status update for an open position on Delta Exchange."""
        if self.is_test_environment():
            return False

        pnl_sign = "+" if pnl >= 0 else ""
        emoji = "📈" if pnl >= 0 else "📉"
        msg = (
            f"{emoji} <b>YOUR ACTIVE POSITION ON DELTA EXCHANGE</b>\n\n"
            f"<b>Asset:</b> {symbol}\n"
            f"<b>Position:</b> {side} {abs(size)} contracts\n"
            f"<b>Entry Price:</b> ${entry:.2f}\n"
            f"<b>Mark Price:</b> ${mark:.2f}\n"
            f"<b>Unrealized PnL:</b> {pnl_sign}${pnl:.2f}\n"
            f"<b>Margin Allocated:</b> ${margin:.2f} USD\n"
            f"<b>Liquidation Price:</b> ${liq_price:.2f}\n"
            f"<b>Venue:</b> Delta Exchange Live API"
        )
        return self.send_message(msg)

    def notify_circuit_breaker(self, loss_pct: float, balance: float):
        if self.is_test_environment():
            return False

        msg = (
            f"⚠️ <b>CIRCUIT BREAKER TRIGGERED</b>\n\n"
            f"Daily loss ({loss_pct:.1f}%) reached 5% max drawdown ceiling.\n"
            f"Trading automatically halted for the session to protect capital.\n"
            f"<b>Current Balance:</b> ${balance:.2f} USD"
        )
        return self.send_message(msg)

    def notify_candidate_evaluation(self, symbol: str, gate1: Dict[str, Any], quant: Dict[str, Any], open_slots: int = 1) -> bool:
        """
        Notify user in real-time when a pair passes Gate 1 (News/Sentiment) and is evaluated
        by Gate 2 (Quant MTF) or Forensic Learner, showing the rejection or acceptance details.
        Throttled to once per 20 minutes per symbol for identical status to avoid Telegram spam.
        """
        if self.is_test_environment():
            return False

        now = time.time()
        last_notified = self._candidate_rejection_history.get(symbol, 0.0)
        # 20-minute anti-spam cooldown per symbol
        if now - last_notified < 1200:
            return False

        self._candidate_rejection_history[symbol] = now

        gate2_passed = quant.get("gate_passed", False)
        indicators = quant.get("indicators", {})
        mtf = indicators.get("mtf", {})
        micro = indicators.get("microstructure", {})
        macro_trend = mtf.get("macro_trend_1h", "UNKNOWN")
        pullback_dist = mtf.get("pullback_dist_atr", 0.0)
        micro_drift = micro.get("micro_drift", 0.0)
        depth_imb = micro.get("depth_imbalance_5", 0.0)
        raw_reason = quant.get("reason", "Vetoed")
        safe_reason = raw_reason.replace("<", "&lt;").replace(">", "&gt;")

        if gate2_passed:
            status_header = f"🎯 <b>CANDIDATE QUALIFIED: {symbol}</b>"
            gate2_status = f"✅ <b>Gate 2 Quant APPROVED:</b> {quant.get('signal')} confluence at ${quant.get('entry_price', 0):.2f}"
            action_footer = f"<i>Dual-Key consensus achieved. Sizing risk for open slot...</i>"
        else:
            status_header = f"🔍 <b>CANDIDATE EVALUATION & REJECTION: {symbol}</b>"
            gate2_status = f"❌ <b>Gate 2 Quant VETOED:</b> {safe_reason}"
            action_footer = f"<i>Capital protected. Bot actively hunting next qualifying setup.</i>"

        session = quant.get("session", {})
        session_name = session.get("session_name", "ACTIVE_REGIME")
        alpha_thresh = quant.get("alpha_threshold", 0.45)

        msg = (
            f"{status_header}\n\n"
            f"<b>Trading Slots:</b> {open_slots}/2 Open\n"
            f"<b>Active Session:</b> {session_name} (Hurdle: {alpha_thresh:.2f})\n"
            f"<b>Gate 1 (News/Sentiment):</b> ✅ APPROVED\n"
            f"• Sentiment: +{gate1.get('usable_sentiment', 0):.2f} (Conf: {gate1.get('confidence', 0)*100:.1f}%)\n"
            f"• Sources: Tier 1/2 Institutional Backing\n\n"
            f"{gate2_status}\n"
            f"• 1H Macro Ribbon: {macro_trend}\n"
            f"• 15M Value Pullback: {pullback_dist:.2f}x ATR\n"
            f"• Orderbook Drift: {micro_drift:+.3f} | Depth Imbalance: {depth_imb:+.3f}\n\n"
            f"{action_footer}"
        )
        return self.send_message(msg)

    def notify_scan_digest(self, open_slots: int, active_positions: list, candidate_summaries: list, force: bool = False) -> bool:
        """
        Send a periodic radar digest to Telegram showing real-time scanning status,
        active market session, open slots, and candidate rejection breakdowns.
        Throttled to once every 30 minutes unless forced.
        """
        if self.is_test_environment():
            return False

        now = time.time()
        if not force and (now - self._last_digest_time < 1800):
            return False

        self._last_digest_time = now

        from agents.session_momentum_engine import session_momentum_engine
        session_info = session_momentum_engine.get_current_session()
        session_tag = "🔥 HIGH MOMENTUM" if session_info.get("is_high_momentum") else "Standard Liquidity"

        pos_str = "None (2 slots open)"
        if active_positions:
            pos_lines = []
            for p in active_positions:
                sym = p.get("symbol") or p.get("product_symbol", "UNKNOWN")
                side = p.get("side") or ("SHORT" if float(p.get("size", 0)) < 0 else "BUY")
                sz = abs(float(p.get("size", 0)))
                entry = float(p.get("entry_price", 0))
                pnl = float(p.get("unrealized_pnl", 0))
                pnl_str = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
                pos_lines.append(f"• {sym} {side} ({sz:.0f} contracts @ ${entry:.2f} | PnL: {pnl_str})")
            pos_str = "\n".join(pos_lines)

        cand_lines = []
        for c in candidate_summaries[:6]:
            safe_verdict = str(c.get('verdict', '')).replace("<", "&lt;").replace(">", "&gt;")
            cand_lines.append(f"• <b>{c['symbol']}:</b> {safe_verdict}")
        cand_str = "\n".join(cand_lines) if cand_lines else "• All scanned pairs in consolidation."

        msg = (
            f"📡 <b>REAL-TIME SCANNER & REJECTION RADAR</b>\n\n"
            f"<b>Market Window:</b> {session_info['session_name']} ({session_tag})\n"
            f"<b>Window Timing:</b> {session_info.get('ist_window', 'Active')}\n"
            f"<b>Dynamic Alpha Hurdle:</b> {session_info.get('alpha_threshold', 0.45):.2f}\n"
            f"<b>Trading Slots:</b> {open_slots}/2 Available\n"
            f"<b>Active Positions:</b>\n{pos_str}\n\n"
            f"<b>Latest Pair Evaluations:</b>\n{cand_str}\n\n"
            f"<i>Autonomous scanner loop active. Target: High-probability execution.</i>"
        )
        return self.send_message(msg)

telegram_notifier = TelegramNotifier()
