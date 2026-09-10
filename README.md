# Delta Exchange US Stocks RWA Multi-Agent Trading Engine

Autonomous, institutional-grade algorithmic trading system designed for **Delta Exchange India** to trade crypto blue-chips (`BTCUSD`, `ETHUSD`) and 32 US Equity Real-World Assets (RWAs) / bStocks (`NVDAXUSD`, `TSLAXUSD`, `PLTRBUSD`, `MSTRBUSD`, `COINXUSD`, etc.).

---

## 🏛️ Multi-Agent Architecture

```
                    MARKET DATA / WEBSOCKET / L2 BOOK
                                   │
                                   ▼
                   ┌───────────────────────────────┐
                   │  Agent 1: Social & News Pulse │
                   └───────────────┬───────────────┘
                                   │ (Gate 1: News Sentiment & Bot Veto)
                                   ▼
                   ┌───────────────────────────────┐
                   │   Agent 2: Quant Analyst      │
                   └───────────────┬───────────────┘
                                   │ (Gate 2: Qlib Alphas & MTF Ribbon)
                                   │ (Gate 2b: Post-Impulse Displacement Veto)
                                   │ (Gate 2c: 1H Zone Clearance Engine)
                                   ▼
                   ┌───────────────────────────────┐
                   │   Dual-Key Consensus Engine   │
                   └───────────────┬───────────────┘
                                   │ (Consensus Approval)
                                   ▼
                   ┌───────────────────────────────┐
                   │   Agent 3: Risk Governor      │
                   └───────────────┬───────────────┘
                                   │ (25% Dynamic Cushion, Max 37.5% Margin/Slot)
                                   ▼
                   ┌───────────────────────────────┐
                   │   Agent 4: Execution Manager  │
                   └───────────────┬───────────────┘
                                   │ (Delta Orderbook Native Execution)
                                   ▼
                             DELTA EXCHANGE
```

---

## ⚡ Core Institutional Engines

1. **1-Hour Supply & Demand Zone Clearance Engine**:
   - Analyzes 48 hours of 1-Hour candles for structural swing pivots with $\ge 1.0\times\text{ATR}$ displacement.
   - Monitors mitigation retests. Zones tested $\ge 3$ times are marked `DEPLETED` (liquidity drained, non-blocking).
   - Enforces a minimum clearance ratio: $\text{clearance\_ratio} = \frac{\text{available\_reward}}{\text{risk\_distance}} \ge 1.2\text{R}$.
   - Prohibits buying into overhead supply ceilings or shorting into underlying demand floors.

2. **Post-Impulse Displacement & Entry-State Classification Engine**:
   - Classifies market structure into State A (Continuation), State B (Genuine Reversal with $\ge 3$-bar base and higher low), and State C (Trap).
   - Hard-vetoes dead-cat bounces and flush exhaustion chases.

3. **Risk Governor & Dynamic Cushion**:
   - Fixed 25% cash buffer (`CUSHION_PCT = 0.25`) reserved against liquidations.
   - 75% deployable capital split across 2 concurrent position slots (max 37.5% margin per trade).
   - High-watermark deposit tracking: added funds instantly scale margin ceilings and reset the daily circuit breaker.

4. **Microstructure & Stoikov Engine**:
   - Computes Stoikov micro-price and 5-level depth imbalance to optimize execution limit pricing.

---

## 🚀 Quick Start

### 1. Installation
```bash
git clone https://github.com/Tirtha-byte/delta-algo-bot.git
cd delta-algo-bot
pip install -r requirements.txt  # or install dependencies: delta-client, fastapi, uvicorn, pydantic, pandas, numpy
```

### 2. Configuration
Copy the sample environment file and add your Delta Exchange API credentials:
```bash
cp .env.example .env
```
Edit `.env`:
```env
DELTA_API_KEY=your_api_key_here
DELTA_API_SECRET=your_api_secret_here
TRADING_MODE=LIVE  # or SHADOW
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
```

### 3. Run Locally or with Docker
```bash
# Direct run
python run.py

# Docker Compose
docker-compose up -d
```

### 4. Run Test Suite
```bash
python -m unittest discover tests
```

---

## 📊 Dashboard API
- Web UI & Status: `http://localhost:8000/`
- System Status: `GET /api/status`
- Active Positions: `GET /api/positions`
- Manual Scan: `POST /api/scan`

---

## 📜 License
MIT License.
