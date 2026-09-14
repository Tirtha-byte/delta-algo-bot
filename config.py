import os
from typing import List, Dict, Any
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

class DeltaConfig(BaseModel):
    # Delta Exchange India (api.india.delta.exchange) or Delta Global (api.delta.exchange)
    BASE_URL: str = os.getenv("DELTA_BASE_URL", "https://api.india.delta.exchange")
    PUBLIC_WS_URL: str = os.getenv("DELTA_PUBLIC_WS_URL", "wss://public-socket.india.delta.exchange")
    PRIVATE_WS_URL: str = os.getenv("DELTA_PRIVATE_WS_URL", "wss://socket.india.delta.exchange")
    WS_URL: str = os.getenv("DELTA_WS_URL", "wss://public-socket.india.delta.exchange")
    API_KEY: str = os.getenv("DELTA_API_KEY", "")
    API_SECRET: str = os.getenv("DELTA_API_SECRET", "")
    REQUEST_TIMEOUT: int = 10

class StalenessConfig(BaseModel):
    DEGRADED_THRESHOLD_MS: int = 2500       # > 2.5s is degraded
    STALE_THRESHOLD_MS: int = 5000          # > 5.0s blocks orders
    CRITICAL_STALE_MS: int = 10000          # > 10s forces reconnect
    MAX_CLOCK_DRIFT_MS: int = 3000          # Clock drift breaker
    HEARTBEAT_INTERVAL_SEC: int = 15        # Ping interval
    RECONNECT_BACKOFF_BASE_SEC: float = 1.0
    RECONNECT_BACKOFF_MAX_SEC: float = 30.0

class ResearchLabConfig(BaseModel):
    ENABLED: bool = True
    DATA_DIR: str = "data/historical"
    CONTINUOUS_FEATURE_INTERVAL_SEC: int = 10  # Lightweight feature checks
    BACKTEST_CYCLE_INTERVAL_MIN: int = 30     # Asynchronous backtest cycle
    WFO_CYCLE_INTERVAL_HOURS: int = 4         # Walk-forward optimization cycle
    STRESS_TEST_INTERVAL_HOURS: int = 6       # Stress test & Monte Carlo cycle
    MAX_CPU_PERCENT: float = 40.0             # CPU ceiling for background lab


class CompoundingConfig(BaseModel):
    STARTING_CAPITAL: float = float(os.getenv("STARTING_CAPITAL", "60.0"))
    TARGET_CAPITAL: float = float(os.getenv("TARGET_CAPITAL", "5000.0"))
    
    # Milestone Stages
    # Stage 1: $60 -> $200 (Capital Preservation, 2.5% max risk per trade, 5x isolated leverage)
    # Stage 2: $200 -> $1,000 (Acceleration, 2.0% risk per trade, pyramiding runners)
    # Stage 3: $1,000 -> $5,000 (Institutional Compounding, 1.5% - 2.0% risk)
    STAGE_1_TARGET: float = 200.0
    STAGE_2_TARGET: float = 1000.0
    
    MAX_RISK_PER_TRADE_PCT: float = 0.025  # 2.5% of account balance
    MAX_RISK_SAFETY_CEILING_PCT: float = 0.0375  # 3.75% hard risk ceiling
    MAX_DAILY_DRAWDOWN_PCT: float = 0.05   # 5.0% daily circuit breaker
    DEFAULT_LEVERAGE: int = 5              # 5x isolated leverage baseline
    MAX_LEVERAGE: int = 7                  # Cap for low-volatility isolated margin
    MIN_REWARD_TO_RISK: float = 1.2        # Calibrated for high-probability scale-outs (70% @ 1.0R, 30% @ 2.2R)

    # Capital Cushion & Position Allocation Engine (90% allocated to max 2 positions, 10% cushion)
    CUSHION_PCT: float = float(os.getenv("CUSHION_PCT", "0.10"))  # 10% cash cushion preserved
    MAX_MARGIN_PER_POSITION_PCT: float = float(os.getenv("MAX_MARGIN_PER_POSITION_PCT", "0.45"))  # 45% margin per trade (90% / 2)

    # Hybrid Dynamic Leverage Engine: Volatility & Asset-Specific Tiers
    VOLATILITY_LEVERAGE_MAP: Dict[str, int] = {
        # Tier 3: Low-Volatility & Broad Indices -> 7x Isolated Leverage
        "SPYXUSD": 7,
        "QQQXUSD": 7,
        "AAPLXUSD": 7,
        "GOOGLXUSD": 7,
        "INTCBUSD": 7,
        "SLVONUSD": 7,
        "EWYBUSD": 7,

        # Tier 1: High-Beta, Hyper-Volatile Tech -> 4x Isolated Leverage (Safe 25% liquidation buffer)
        "TSLAXUSD": 4,
        "NVDAXUSD": 4,
        "PLTRBUSD": 4,
        "MSTRBUSD": 4,
        "COINXUSD": 4,
        "SOXLBUSD": 4,
        "RKLBBUSD": 4,
        "SPCXXUSD": 4,
        "CBRSBUSD": 4,
        
        # Tier 2: Crypto Blue-Chips & Medium-Beta Tech -> 5x Isolated Leverage (Default for all other RWAs)
        "BTCUSD": 5,
        "ETHUSD": 5,
    }

class SystemConfig(BaseModel):
    MODE: str = os.getenv("TRADING_MODE", "SHADOW")  # "SHADOW" or "LIVE"
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8000"))
    SCAN_INTERVAL_SECONDS: int = 60
    MAX_CONCURRENT_POSITIONS: int = 2
    
    # Tracked Assets: Crypto Blue-Chips (BTC/ETH) + 32 Delta Exchange US Stocks & Equity RWAs
    US_STOCKS_RWA: List[str] = [
        "BTCUSD",     # Bitcoin Perpetual (ContractVal: 0.001 BTC, 24/7 liquidity)
        "ETHUSD",     # Ethereum Perpetual (ContractVal: 0.01 ETH, 24/7 liquidity)
        "NVDAXUSD",   # NVIDIA xStock (ContractVal: 0.01 shares)
        "TSLAXUSD",   # Tesla xStock (ContractVal: 0.01 shares)
        "PLTRBUSD",   # Palantir Technologies bStocks (ContractVal: 0.1 shares)
        "MSTRBUSD",   # MicroStrategy bStocks (ContractVal: 0.1 shares)
        "AAPLXUSD",   # Apple xStock (ContractVal: 0.01 shares)
        "AMZNXUSD",   # Amazon xStock (ContractVal: 0.01 shares)
        "GOOGLXUSD",  # Alphabet xStock (ContractVal: 0.01 shares)
        "METAXUSD",   # Meta xStock (ContractVal: 0.01 shares)
        "AMDBUSD",    # Advanced Micro Devices bStocks (ContractVal: 0.01 shares)
        "TSMBUSD",    # TSMC bStocks (ContractVal: 0.01 shares)
        "ARMBUSD",    # ARM bStocks (ContractVal: 0.01 shares)
        "INTCBUSD",   # Intel bStocks (ContractVal: 0.1 shares)
        "MUBUSD",     # Micron Technology bStocks (ContractVal: 0.01 shares)
        "MRVLBUSD",   # Marvell Technology bStocks (ContractVal: 0.1 shares)
        "WDCBUSD",    # Western Digital bStocks (ContractVal: 0.01 shares)
        "SNDKBUSD",   # SanDisk bStocks (ContractVal: 0.01 shares)
        "COINXUSD",   # Coinbase xStock (ContractVal: 0.01 shares)
        "HOODBUSD",   # Robinhood bStocks (ContractVal: 0.1 shares)
        "BABABUSD",   # Alibaba bStocks (ContractVal: 0.1 shares)
        "RKLBBUSD",   # Rocket Lab bStocks (ContractVal: 0.1 shares)
        "SPCXXUSD",   # SpaceX xStock (ContractVal: 0.01 shares)
        "CBRSBUSD",   # Cerebras bStocks (ContractVal: 0.1 shares)
        "CRCLXUSD",   # Circle xStock (ContractVal: 0.1 shares)
        "NBISBUSD",   # Nebius bStocks (ContractVal: 0.1 shares)
        "LITEBUSD",   # Lumentum Holdings bStocks (ContractVal: 0.01 shares)
        "SKHYBUSD",   # SK Hynix bStocks (ContractVal: 0.1 shares)
        "SOXLBUSD",   # Direxion Daily Semiconductor 3X ETF bStocks (ContractVal: 0.1 shares)
        "DRAMBUSD",   # Roundhill Memory ETF bStocks (ContractVal: 0.1 shares)
        "EWYBUSD",    # iShares MSCI South Korea ETF bStocks (ContractVal: 0.1 shares)
        "SPYXUSD",    # SP500 xStock (ContractVal: 0.01 shares)
        "QQQXUSD",    # Nasdaq xStock (ContractVal: 0.01 shares)
        "SLVONUSD",   # iShares Silver Trust ONDO (ContractVal: 0.1 shares)
    ]
    
    # Contract specs on Delta Exchange
    CONTRACT_SPECS: Dict[str, Dict[str, Any]] = {
        "BTCUSD": {"contract_val": 0.001, "tick_size": 0.5, "name": "Bitcoin Perpetual"},
        "ETHUSD": {"contract_val": 0.01, "tick_size": 0.05, "name": "Ethereum Perpetual"},
        "NVDAXUSD": {"contract_val": 0.01, "tick_size": 0.01, "name": "NVIDIA xStock"},
        "TSLAXUSD": {"contract_val": 0.01, "tick_size": 0.01, "name": "Tesla xStock"},
        "PLTRBUSD": {"contract_val": 0.1, "tick_size": 0.01, "name": "Palantir Technologies bStocks"},
        "MSTRBUSD": {"contract_val": 0.1, "tick_size": 0.01, "name": "MicroStrategy bStocks"},
        "AAPLXUSD": {"contract_val": 0.01, "tick_size": 0.01, "name": "Apple xStock"},
        "AMZNXUSD": {"contract_val": 0.01, "tick_size": 0.01, "name": "Amazon xStock"},
        "GOOGLXUSD": {"contract_val": 0.01, "tick_size": 0.01, "name": "Alphabet xStock"},
        "METAXUSD": {"contract_val": 0.01, "tick_size": 0.01, "name": "Meta xStock"},
        "AMDBUSD": {"contract_val": 0.01, "tick_size": 0.01, "name": "Advanced Micro Devices bStocks"},
        "TSMBUSD": {"contract_val": 0.01, "tick_size": 0.01, "name": "TSMC bStocks"},
        "ARMBUSD": {"contract_val": 0.01, "tick_size": 0.01, "name": "ARM bStocks"},
        "INTCBUSD": {"contract_val": 0.1, "tick_size": 0.01, "name": "Intel bStocks"},
        "MUBUSD": {"contract_val": 0.01, "tick_size": 0.01, "name": "Micron Technology bStocks"},
        "MRVLBUSD": {"contract_val": 0.1, "tick_size": 0.01, "name": "Marvell Technology bStocks"},
        "WDCBUSD": {"contract_val": 0.01, "tick_size": 0.01, "name": "Western Digital bStocks"},
        "SNDKBUSD": {"contract_val": 0.01, "tick_size": 0.01, "name": "SanDisk bStocks"},
        "COINXUSD": {"contract_val": 0.01, "tick_size": 0.01, "name": "Coinbase xStock"},
        "HOODBUSD": {"contract_val": 0.1, "tick_size": 0.01, "name": "Robinhood bStocks"},
        "BABABUSD": {"contract_val": 0.1, "tick_size": 0.01, "name": "Alibaba bStocks"},
        "RKLBBUSD": {"contract_val": 0.1, "tick_size": 0.01, "name": "Rocket Lab bStocks"},
        "SPCXXUSD": {"contract_val": 0.01, "tick_size": 0.01, "name": "SpaceX xStock"},
        "CBRSBUSD": {"contract_val": 0.1, "tick_size": 0.01, "name": "Cerebras bStocks"},
        "CRCLXUSD": {"contract_val": 0.1, "tick_size": 0.01, "name": "Circle xStock"},
        "NBISBUSD": {"contract_val": 0.1, "tick_size": 0.01, "name": "Nebius bStocks"},
        "LITEBUSD": {"contract_val": 0.01, "tick_size": 0.01, "name": "Lumentum Holdings bStocks"},
        "SKHYBUSD": {"contract_val": 0.1, "tick_size": 0.01, "name": "SK Hynix bStocks"},
        "SOXLBUSD": {"contract_val": 0.1, "tick_size": 0.01, "name": "Direxion Daily Semiconductor 3X ETF"},
        "DRAMBUSD": {"contract_val": 0.1, "tick_size": 0.01, "name": "Roundhill Memory ETF bStocks"},
        "EWYBUSD": {"contract_val": 0.1, "tick_size": 0.01, "name": "iShares MSCI South Korea ETF"},
        "SPYXUSD": {"contract_val": 0.01, "tick_size": 0.01, "name": "SP500 xStock"},
        "QQQXUSD": {"contract_val": 0.01, "tick_size": 0.01, "name": "Nasdaq xStock"},
        "SLVONUSD": {"contract_val": 0.1, "tick_size": 0.01, "name": "iShares Silver Trust ONDO"}
    }

class QuantConfig(BaseModel):
    # Configurable Alpha threshold (empirically tested across 0.30, 0.40, 0.45, 0.50, 0.60)
    ALPHA_THRESHOLD: float = float(os.getenv("QUANT_ALPHA_THRESHOLD", "0.45"))
    MIN_REWARD_TO_RISK: float = 1.2
    
    # Dual-Pillar Factor Family Weights (Normalized to sum to 1.0)
    WEIGHT_MOMENTUM: float = 0.30        # Multi-window rate of change (ROC 5/10/20/30)
    WEIGHT_PV_CORR: float = 0.25         # Rolling price-volume correlation
    WEIGHT_MORPHOLOGY: float = 0.20      # KMID2, KUP, KLOW candle body/wick ratios
    WEIGHT_TREND_ALIGNMENT: float = 0.15 # EMA 20/50/200 Ribbon & MA divergence
    WEIGHT_ORDERBOOK: float = 0.10       # L2 depth imbalance

class MTFConfig(BaseModel):
    MACRO_RESOLUTION: str = "1h"
    INTERMEDIATE_RESOLUTION: str = "15m"
    MICRO_RESOLUTION: str = "5m"
    
    # Candle counts (ensures true mathematical convergence for EMA200)
    MACRO_CANDLE_COUNT: int = 250
    INTERMEDIATE_CANDLE_COUNT: int = 250
    MICRO_CANDLE_COUNT: int = 60
    
    # Macro trend ribbon
    MACRO_EMA_FAST: int = 50
    MACRO_EMA_SLOW: int = 200
    
    # Value Pullback Guardrail (distance from 15m EMA20)
    PULLBACK_MAX_DIST_ATR: float = 2.0
    
    # 5m Structural Pivot Lookback
    SWING_PIVOT_LOOKBACK: int = 10
    MIN_STOP_ATR_MULT: float = 0.8
    MAX_STOP_ATR_MULT: float = 2.5

    # Post-Impulse Displacement & Entry-State Classification Guardrails
    IMPULSE_DISPLACEMENT_THRESHOLD_ATR: float = 2.0  # Cumulative 6-bar 5m move in multiples of ATR
    IMPULSE_SHOCK_CANDLE_ATR: float = 1.8            # Single-bar high-low shock range in multiples of ATR
    IMPULSE_BASE_MIN_CANDLES: int = 3                # Minimum consecutive candles required to form base
    EXHAUSTION_CHASE_MAX_ATR: float = 1.8            # Extension distance from 15m EMA20 defining exhaustion tail

    # 1-Hour Supply & Demand Zone Clearance Guardrails
    ZONE_LOOKBACK_BARS: int = 48                     # 48-bar (2-day) 1h structural lookback
    ZONE_MIN_DISPLACEMENT_ATR: float = 1.0           # Minimum 1h impulse displacement to validate pivot zone
    ZONE_CLEARANCE_MIN_RATIO: float = 1.2            # Minimum R:R clearance required before opposing active zone
    ZONE_MAX_TESTS: int = 3                          # Re-test count at which zone liquidity is depleted (non-blocking)

delta_config = DeltaConfig()
compounding_config = CompoundingConfig()
system_config = SystemConfig()
quant_config = QuantConfig()
mtf_config = MTFConfig()
staleness_config = StalenessConfig()
research_lab_config = ResearchLabConfig()


