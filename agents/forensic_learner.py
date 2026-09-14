"""
BEAST v2 - Forensic Post-Mortem & Self-Improving Memory Agent
agents/forensic_learner.py

Responsibilities:
1. Automated Post-Mortem Autopsy: Diagnoses root causes of every closed trade.
   Categorizes into rigorous quant buckets:
   - VALID_MARKET_LOSS: Valid setup that failed due to normal market variance.
   - VALID_MARKET_WIN: Positive expectancy confirmation.
   - EXECUTION_ERROR: Slippage, spread expansion, or fill delay.
   - DATA_ERROR: WebSocket staleness or disconnect.
   - SOFTWARE_ERROR: System bug (Crucial: NEVER quarantines an asset or penalizes setup stats!).
   - EXCHANGE_ERROR: Delta Exchange reject, gateway error, or maintenance.
2. Anti-Pattern Fingerprint Memory: Prevents repeat mistakes (Counter-trend traps, overextended RSI, etc.)
3. Pre-Flight Inspection & Veto: Evaluates proposals before order execution.
4. Auto-journaling to LiveTradeJournal.
"""

import os
import json
import time
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone, timedelta
from agents.live_trade_journal import live_trade_journal


class ForensicLearnerAgent:
    def __init__(self, data_path: Optional[str] = None):
        if data_path:
            self.data_path = data_path
        else:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            data_dir = os.path.join(base_dir, "data")
            os.makedirs(data_dir, exist_ok=True)
            self.data_path = os.path.join(data_dir, "forensic_memory.json")

        self.memory: Dict[str, Any] = self._load_memory()

    def _load_memory(self) -> Dict[str, Any]:
        default_state = {
            "version": "2.0",
            "total_autopsies": 0,
            "total_losses_analyzed": 0,
            "total_wins_analyzed": 0,
            "prevented_mistakes_count": 0,
            "quarantined_symbols": {},
            "asset_profiles": {},
            "loss_fingerprints": [],
            "recent_autopsies": []
        }
        if os.path.exists(self.data_path) and os.path.getsize(self.data_path) > 0:
            try:
                with open(self.data_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for k, v in default_state.items():
                        if k not in data:
                            data[k] = v
                    return data
            except Exception as e:
                print(f"[ForensicLearner] Error loading {self.data_path}: {e}")
        return default_state

    def save_memory(self):
        try:
            os.makedirs(os.path.dirname(self.data_path), exist_ok=True)
            with open(self.data_path, "w", encoding="utf-8") as f:
                json.dump(self.memory, f, indent=2)
        except Exception as e:
            print(f"[ForensicLearner] Error saving memory: {e}")

    def _get_asset_profile(self, symbol: str) -> Dict[str, Any]:
        if symbol not in self.memory["asset_profiles"]:
            self.memory["asset_profiles"][symbol] = {
                "consecutive_losses": 0,
                "total_trades": 0,
                "wins": 0,
                "losses": 0,
                "win_rate": 0.0,
                "adaptive_atr_multiplier": 1.5,
                "confidence_hurdle": 0.65,
                "last_loss_reason": None,
                "last_autopsy_at": None
            }
        return self.memory["asset_profiles"][symbol]

    def is_asset_quarantined(self, symbol: str) -> (bool, Optional[str], Optional[str]):
        """Check if an asset is currently in cooldown quarantine."""
        q_info = self.memory["quarantined_symbols"].get(symbol)
        if not q_info:
            return False, None, None

        until_str = q_info.get("quarantine_until")
        if not until_str:
            return False, None, None

        try:
            until_dt = datetime.fromisoformat(until_str.replace("Z", "+00:00"))
            if datetime.now(timezone.utc) < until_dt:
                remaining_min = int((until_dt - datetime.now(timezone.utc)).total_seconds() / 60)
                return True, q_info.get("reason", "Consecutive losses quarantine"), f"{remaining_min}m remaining"
            else:
                del self.memory["quarantined_symbols"][symbol]
                self.save_memory()
                return False, None, None
        except Exception:
            return False, None, None

    def conduct_autopsy(self, trade_record: Dict[str, Any], market_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Perform a quant-grade forensic autopsy on a closed trade.
        """
        symbol = trade_record.get("symbol", "UNKNOWN")
        realized_pnl = float(trade_record.get("realized_pnl", trade_record.get("pnl", 0.0)))
        side = trade_record.get("side", "BUY")
        entry = float(trade_record.get("entry_price", 0.0))
        exit_price = float(trade_record.get("exit_price", entry))
        error_type = trade_record.get("error_type")  # e.g., "SOFTWARE_ERROR", "EXCHANGE_ERROR"
        now_iso = datetime.now(timezone.utc).isoformat()

        # CRITICAL SAFETY RULE: Software errors must NEVER penalize asset or quarantine
        if error_type in ("SOFTWARE_ERROR", "DATA_ERROR", "EXCHANGE_ERROR"):
            forensic_cat = error_type
            autopsy_report = {
                "timestamp": now_iso,
                "symbol": symbol,
                "side": side,
                "realized_pnl": realized_pnl,
                "forensic_category": forensic_cat,
                "archetype": error_type,
                "diagnosis": f"Technical non-market fault: {trade_record.get('reason', 'System fault')}",
                "remedy": "Fix system defect. Asset stats not penalized.",
                "quarantine_applied": False
            }
            self.memory["recent_autopsies"].append(autopsy_report)
            self.save_memory()
            return autopsy_report

        profile = self._get_asset_profile(symbol)
        profile["total_trades"] += 1

        is_scratch = abs(realized_pnl) <= 0.05 or trade_record.get("close_reason") == "BREAKEVEN_STOP"

        # CASE 1: PROFITABLE OR SCRATCH/BREAKEVEN TRADE
        if realized_pnl >= 0 or is_scratch:
            if realized_pnl > 0:
                self.memory["total_wins_analyzed"] += 1
                profile["wins"] += 1
                profile["consecutive_losses"] = 0
                profile["confidence_hurdle"] = max(0.65, round(profile["confidence_hurdle"] - 0.05, 2))
            else:
                # Scratch / Breakeven trade does not count as a loss streak
                profile["consecutive_losses"] = 0
            
            profile["win_rate"] = round((profile["wins"] / profile["total_trades"]) * 100, 1)
            self.save_memory()

            # Record in LiveTradeJournal if not running unit tests
            if os.environ.get("TESTING") != "1":
                live_trade_journal.record_trade_exit({
                    "symbol": symbol,
                    "side": side,
                    "entry_price": entry,
                    "exit_price": exit_price,
                    "pnl": realized_pnl,
                    "forensic_category": "VALID_MARKET_WIN" if realized_pnl > 0 else "BREAKEVEN_SCRATCH"
                })

            return {
                "status": "WIN" if realized_pnl > 0 else "BREAKEVEN",
                "symbol": symbol,
                "pnl": realized_pnl,
                "forensic_category": "VALID_MARKET_WIN" if realized_pnl > 0 else "BREAKEVEN_SCRATCH",
                "message": f"Trade resolved for {symbol}. Loss streak reset. Confidence hurdle: {profile['confidence_hurdle']*100:.0f}%."
            }

        # CASE 2: LOSING TRADE (AUTOPSY)
        self.memory["total_autopsies"] += 1
        self.memory["total_losses_analyzed"] += 1
        profile["losses"] += 1
        profile["consecutive_losses"] += 1
        profile["win_rate"] = round((profile["wins"] / profile["total_trades"]) * 100, 1)

        opened_at_str = trade_record.get("opened_at")
        duration_seconds = 0
        if opened_at_str:
            try:
                open_dt = datetime.fromisoformat(opened_at_str.replace("Z", "+00:00"))
                duration_seconds = int((datetime.now(timezone.utc) - open_dt).total_seconds())
            except Exception:
                duration_seconds = 600

        ctx = market_context or {}
        quant_info = ctx.get("quant", {})
        news_info = ctx.get("news", {})

        trend = quant_info.get("trend_alignment", "UNKNOWN")
        close_price = quant_info.get("current_close", entry)
        ema200 = quant_info.get("ema200", close_price)
        rsi = quant_info.get("rsi", 50.0)

        archetype = "VALID_MARKET_LOSS"
        diagnosis = "Standard bracket stop-loss triggered by adverse market movement."
        remedy = "Normal quant variance. Tighten entry confluence."

        # Check 1: Counter-Trend Trap
        if (side == "BUY" and trend == "BEARISH") or (side == "SELL" and trend == "BULLISH"):
            archetype = "COUNTER_TREND_TRAP"
            diagnosis = f"Trade entered against prevailing {trend} trend ribbon."
            remedy = f"Strictly prohibit {side} entries when trend alignment is {trend}."
        elif (side == "BUY" and close_price < ema200 * 0.98) or (side == "SELL" and close_price > ema200 * 1.02):
            archetype = "EMA200_MACRO_RESISTANCE"
            diagnosis = f"Trade took {side} directly into major 200 EMA barrier (${ema200:.2f})."
            remedy = f"Require minimum 1.5% clearance from EMA 200 on all future {side} setups."

        # Check 2: Overextended Momentum
        elif side == "BUY" and rsi > 68.0:
            archetype = "OVEREXTENDED_LONG"
            diagnosis = f"Entered LONG at overbought RSI ({rsi:.1f})."
            remedy = "Hard cap: Never initiate LONG when RSI 14 > 65.0."
        elif side == "SELL" and rsi < 32.0:
            archetype = "OVEREXTENDED_SHORT"
            diagnosis = f"Entered SHORT at oversold RSI ({rsi:.1f})."
            remedy = "Hard cap: Never initiate SHORT when RSI 14 < 35.0."

        # Check 3: Volatility Wick Squeeze
        elif duration_seconds < 1800:
            archetype = "VOLATILITY_WICK_SQUEEZE"
            current_atr_mult = profile.get("adaptive_atr_multiplier", 1.5)
            new_atr_mult = round(min(2.5, current_atr_mult + 0.3), 2)
            profile["adaptive_atr_multiplier"] = new_atr_mult
            diagnosis = f"Stopped out prematurely in {duration_seconds//60}m due to market noise."
            remedy = f"Dynamically widened adaptive ATR stop multiplier for {symbol} to {new_atr_mult}x."

        # Check 4: Unconfirmed Social Hype
        elif news_info.get("source_counts", {}).get("tier1_primary", 0) == 0 and news_info.get("source_counts", {}).get("tier3_social", 0) > 0:
            archetype = "UNCONFIRMED_SOCIAL_HYPE"
            diagnosis = "Trade entered on social community buzz without Tier 1 verification."
            remedy = "Enforce mandatory Tier 1 (SEC/IR) verification before entering."

        # Dynamic Quarantine Protocol (2 or more consecutive losses of market traps)
        quarantine_applied = False
        quarantine_expiry = None
        cooldown_hours = 0
        if profile["consecutive_losses"] >= 2:
            cooldown_hours = 6 if profile["consecutive_losses"] == 2 else 12
            quarantine_dt = datetime.now(timezone.utc) + timedelta(hours=cooldown_hours)
            quarantine_expiry = quarantine_dt.isoformat()
            self.memory["quarantined_symbols"][symbol] = {
                "quarantine_until": quarantine_expiry,
                "reason": f"{profile['consecutive_losses']} consecutive losses ({archetype}). Dynamic cooling-off active.",
                "cooldown_hours": cooldown_hours
            }
            profile["confidence_hurdle"] = min(0.90, round(profile["confidence_hurdle"] + 0.15, 2))
            quarantine_applied = True

        profile["last_loss_reason"] = archetype
        profile["last_autopsy_at"] = now_iso

        fingerprint = {
            "id": f"LP-{len(self.memory['loss_fingerprints']) + 1}",
            "symbol": symbol,
            "archetype": archetype,
            "side": side,
            "indicators": {
                "trend": trend,
                "rsi": rsi,
                "below_ema200": close_price < ema200
            },
            "lesson": remedy,
            "recorded_at": now_iso
        }
        self.memory["loss_fingerprints"].append(fingerprint)

        autopsy_report = {
            "timestamp": now_iso,
            "symbol": symbol,
            "side": side,
            "realized_pnl": realized_pnl,
            "duration_seconds": duration_seconds,
            "forensic_category": "VALID_MARKET_LOSS",
            "archetype": archetype,
            "diagnosis": diagnosis,
            "remedy": remedy,
            "consecutive_losses": profile["consecutive_losses"],
            "quarantine_applied": quarantine_applied,
            "quarantine_until": quarantine_expiry,
            "adaptive_atr_mult": profile.get("adaptive_atr_multiplier", 1.5),
            "confidence_hurdle": profile.get("confidence_hurdle", 0.65)
        }
        self.memory["recent_autopsies"].append(autopsy_report)
        if len(self.memory["recent_autopsies"]) > 30:
            self.memory["recent_autopsies"].pop(0)

        self.save_memory()

        # Log in live journal if not running in unit test mode
        if os.environ.get("TESTING") != "1":
            live_trade_journal.record_trade_exit({
                "symbol": symbol,
                "side": side,
                "entry_price": entry,
                "exit_price": exit_price,
                "pnl": realized_pnl,
                "forensic_category": "VALID_MARKET_LOSS",
                "reason": archetype
            })

        return autopsy_report

    def pre_flight_inspection(self, proposal: Dict[str, Any], quant_eval: Dict[str, Any], news_eval: Dict[str, Any]) -> Dict[str, Any]:
        """
        Inspect trade proposal before execution to prevent repeating past mistakes.
        """
        symbol = proposal.get("symbol", "")
        side = proposal.get("signal", proposal.get("direction", "BUY"))
        profile = self._get_asset_profile(symbol)

        # 1. Check Active Quarantine
        is_q, reason, remaining = self.is_asset_quarantined(symbol)
        if is_q:
            self.memory["prevented_mistakes_count"] += 1
            self.save_memory()
            return {
                "approved": False,
                "veto_code": "ASSET_IN_QUARANTINE",
                "reason": f"Self-Improving Quarantine Active: {reason} ({remaining}). Skipping entry to preserve capital."
            }

        # 2. Check Dynamic Confidence Hurdle
        required_conf = profile.get("confidence_hurdle", 0.65)
        trade_conf = quant_eval.get("confidence", 0.50)
        if trade_conf < required_conf:
            self.memory["prevented_mistakes_count"] += 1
            self.save_memory()
            return {
                "approved": False,
                "veto_code": "CONFIDENCE_BELOW_ADAPTIVE_HURDLE",
                "reason": f"Self-Improving Hurdle: {symbol} requires {required_conf*100:.0f}% confidence due to recent loss history (proposal had {trade_conf*100:.0f}%)."
            }

        # 3. Check Learned Anti-Pattern Matching
        trend = quant_eval.get("trend_alignment", "UNKNOWN")
        rsi = quant_eval.get("rsi", 50.0)
        entry_price = float(proposal.get("entry_price", 0.0))
        ema200 = float(quant_eval.get("indicators", {}).get("ema200", entry_price))

        # Anti-Pattern A: Counter-Trend Trap
        if (side == "BUY" and trend == "BEARISH") or (side == "SELL" and trend == "BULLISH"):
            self.memory["prevented_mistakes_count"] += 1
            self.save_memory()
            return {
                "approved": False,
                "veto_code": "LEARNED_RULE_COUNTER_TREND",
                "reason": f"Self-Improving Rule Triggered: Learned Counter-Trend Trap. Prohibiting {side} on {symbol} when trend is {trend}."
            }

        # Anti-Pattern B: Overextended RSI Extremes
        if side == "BUY" and rsi > 66.0:
            self.memory["prevented_mistakes_count"] += 1
            self.save_memory()
            return {
                "approved": False,
                "veto_code": "LEARNED_RULE_OVEREXTENDED_RSI",
                "reason": f"Self-Improving Rule Triggered: RSI ({rsi:.1f}) is overextended. Past lessons prove high reversal failure."
            }
        elif side == "SELL" and rsi < 34.0:
            self.memory["prevented_mistakes_count"] += 1
            self.save_memory()
            return {
                "approved": False,
                "veto_code": "LEARNED_RULE_OVEREXTENDED_RSI",
                "reason": f"Self-Improving Rule Triggered: RSI ({rsi:.1f}) is oversold. Past lessons prove high short-squeeze failure."
            }

        # Anti-Pattern C: Chasing into EMA 200 barrier
        if ema200 > 0:
            if side == "BUY" and entry_price < ema200 and (ema200 - entry_price) / entry_price < 0.01:
                self.memory["prevented_mistakes_count"] += 1
                self.save_memory()
                return {
                    "approved": False,
                    "veto_code": "LEARNED_RULE_EMA200_BARRIER",
                    "reason": f"Self-Improving Rule Triggered: Entry is within 1.0% of major EMA200 resistance (${ema200:.2f})."
                }

        adaptive_atr = profile.get("adaptive_atr_multiplier", 1.5)
        return {
            "approved": True,
            "veto_code": None,
            "reason": f"Forensic Pre-Flight Verified: No matching loss patterns. Adaptive ATR: {adaptive_atr}x, Hurdle: {required_conf*100:.0f}%.",
            "adaptive_atr_multiplier": adaptive_atr,
            "confidence_hurdle": required_conf
        }

    def get_memory_summary(self) -> Dict[str, Any]:
        return {
            "total_autopsies": self.memory.get("total_autopsies", 0),
            "total_losses_analyzed": self.memory.get("total_losses_analyzed", 0),
            "total_wins_analyzed": self.memory.get("total_wins_analyzed", 0),
            "prevented_mistakes_count": self.memory.get("prevented_mistakes_count", 0),
            "quarantined_symbols": self.memory.get("quarantined_symbols", {}),
            "asset_profiles": self.memory.get("asset_profiles", {}),
            "loss_fingerprints_count": len(self.memory.get("loss_fingerprints", [])),
            "recent_autopsies": self.memory.get("recent_autopsies", [])[-10:]
        }


forensic_learner = ForensicLearnerAgent()
