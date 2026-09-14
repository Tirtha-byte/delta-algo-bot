from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
import os

class SessionMomentumEngine:
    """
    Time-of-Day (ToD) Session & Volatility Momentum Engine.
    
    Capitalizes on the institutional U-shaped intraday volume curve:
    1. US Open Power Surge (13:30 - 16:30 UTC / 7:00 PM - 10:00 PM IST):
       - Peak NYSE/Nasdaq volume & institutional directional breakouts.
       - Calibrates Alpha threshold from 0.45 -> 0.38.
       - Increases scan frequency to every 30 seconds.
       - Prioritizes high-beta tech RWAs (NVDA, TSLA, PLTR, MSTR, SPY, QQQ).
       
    2. London / New York Overlap (12:00 - 16:00 UTC / 5:30 PM - 9:30 PM IST):
       - Peak global dollar & crypto liquidity.
       
    3. US Market Power Hour (19:00 - 20:00 UTC / 12:30 AM - 1:30 AM IST):
       - Market-on-Close (MOC) institutional rebalancing.
       
    4. Off-Hours / Dead Zone (20:00 - 07:00 UTC / 1:30 AM - 12:30 PM IST):
       - US cash market closed; wider spreads on stock RWAs.
       - Tightens Alpha hurdle (0.48) to prevent chop losses.
       - Prioritizes 24/7 crypto blue-chips (BTCUSD, ETHUSD).
    """
    def __init__(self):
        self.default_alpha_threshold = 0.45
        self.high_momentum_alpha_threshold = float(os.getenv("QUANT_HIGH_MOMENTUM_ALPHA", "0.30"))
        self.dead_zone_alpha_threshold = 0.48

    def get_current_session(self, current_dt: Optional[datetime] = None) -> Dict[str, Any]:
        """Detect active session, liquidity tier, and recommended parameters."""
        dt = current_dt or datetime.now(timezone.utc)
        weekday = dt.weekday()  # 0 = Monday, 4 = Friday, 5 = Saturday, 6 = Sunday
        hour_float = dt.hour + (dt.minute / 60.0)

        is_weekday = weekday < 5

        # 1. US Open Power Surge: 13:30 to 16:30 UTC (9:30 AM - 12:00 PM EST / 7:00 PM - 10:00 PM IST)
        if is_weekday and 13.5 <= hour_float < 16.5:
            return {
                "session_name": "US_OPEN_POWER_SURGE",
                "description": "Peak US Cash Opening Momentum & Institutional Volume",
                "is_high_momentum": True,
                "is_us_cash_open": True,
                "alpha_threshold": self.high_momentum_alpha_threshold,
                "scan_interval_seconds": 30,
                "allow_pure_technical_breakouts": True,
                "priority_asset_class": "US_EQUITY_RWA",
                "ist_window": "7:00 PM - 10:00 PM IST"
            }

        # 2. US Market Power Hour: 19:00 to 20:00 UTC (3:00 PM - 4:00 PM EST / 12:30 AM - 1:30 AM IST)
        if is_weekday and 19.0 <= hour_float < 20.0:
            return {
                "session_name": "US_POWER_HOUR",
                "description": "US Market-on-Close Institutional Rebalancing",
                "is_high_momentum": True,
                "is_us_cash_open": True,
                "alpha_threshold": self.high_momentum_alpha_threshold,
                "scan_interval_seconds": 30,
                "allow_pure_technical_breakouts": True,
                "priority_asset_class": "US_EQUITY_RWA",
                "ist_window": "12:30 AM - 1:30 AM IST"
            }

        # 3. US Regular Cash Session (mid-day lull): 16.5 to 19.0 UTC (10:00 PM - 12:30 AM IST)
        if is_weekday and 16.5 <= hour_float < 19.0:
            return {
                "session_name": "US_MIDDAY_SESSION",
                "description": "US Regular Trading Hours (Mid-day Liquidity)",
                "is_high_momentum": False,
                "is_us_cash_open": True,
                "alpha_threshold": self.default_alpha_threshold,
                "scan_interval_seconds": 60,
                "allow_pure_technical_breakouts": False,
                "priority_asset_class": "BALANCED",
                "ist_window": "10:00 PM - 12:30 AM IST"
            }

        # 4. London / European Session: 07:00 to 13.5 UTC (12:30 PM - 7:00 PM IST)
        if 7.0 <= hour_float < 13.5:
            return {
                "session_name": "EUROPE_LONDON_SESSION",
                "description": "European Trading & London Morning Volume",
                "is_high_momentum": False,
                "is_us_cash_open": False,
                "alpha_threshold": self.default_alpha_threshold,
                "scan_interval_seconds": 60,
                "allow_pure_technical_breakouts": False,
                "priority_asset_class": "CRYPTO",
                "ist_window": "12:30 PM - 7:00 PM IST"
            }

        # 5. Asian Session / US Night Dead-Zone: 20:00 to 07:00 UTC (1:30 AM - 12:30 PM IST)
        return {
            "session_name": "OFF_HOURS_DEAD_ZONE",
            "description": "Off-Hours Asian / US Night Rangebound Consolidation",
            "is_high_momentum": False,
            "is_us_cash_open": False,
            "alpha_threshold": self.dead_zone_alpha_threshold,
            "scan_interval_seconds": 60,
            "allow_pure_technical_breakouts": False,
            "priority_asset_class": "CRYPTO",
            "ist_window": "1:30 AM - 12:30 PM IST"
        }

    def get_alpha_threshold(self, symbol: str, current_dt: Optional[datetime] = None) -> float:
        """Returns the dynamically adjusted alpha hurdle for the symbol."""
        session = self.get_current_session(current_dt)
        is_crypto = symbol in ("BTCUSD", "ETHUSD")
        
        if session["is_high_momentum"]:
            return session["alpha_threshold"]  # 0.38 during US open
        
        if not session["is_us_cash_open"] and not is_crypto:
            # During off-hours for US stocks, demand higher conviction (0.48) to prevent chop
            return session["alpha_threshold"]
            
        return self.default_alpha_threshold  # 0.45

    def get_scan_interval(self, current_dt: Optional[datetime] = None) -> int:
        """Returns scan interval in seconds (30s during US peak, 60s otherwise)."""
        session = self.get_current_session(current_dt)
        return session.get("scan_interval_seconds", 60)

    def is_pure_technical_breakout_allowed(self, current_dt: Optional[datetime] = None) -> bool:
        """Returns True if high institutional liquidity permits pure technical momentum breakouts."""
        session = self.get_current_session(current_dt)
        return session.get("allow_pure_technical_breakouts", False)

    def get_priority_symbols(self, all_symbols: List[str], current_dt: Optional[datetime] = None) -> List[str]:
        """Returns an ordered list of symbols prioritizing the active session's most liquid assets."""
        session = self.get_current_session(current_dt)
        crypto_tier = ["BTCUSD", "ETHUSD"]
        us_tech_tier = ["NVDAXUSD", "TSLAXUSD", "PLTRBUSD", "MSTRBUSD", "SPYXUSD", "QQQXUSD"]
        
        if session["priority_asset_class"] == "US_EQUITY_RWA":
            # Prioritize High-Beta Tech Stocks first, then Crypto, then remainder
            remainder = [s for s in all_symbols if s not in us_tech_tier and s not in crypto_tier]
            return us_tech_tier + crypto_tier + remainder
        elif session["priority_asset_class"] == "CRYPTO":
            # Prioritize Crypto + Crypto-Adjacent first
            crypto_adjacent = ["BTCUSD", "ETHUSD", "MSTRBUSD", "COINXUSD"]
            remainder = [s for s in all_symbols if s not in crypto_adjacent]
            return crypto_adjacent + remainder
        else:
            return all_symbols

session_momentum_engine = SessionMomentumEngine()
