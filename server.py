import asyncio
import os
from typing import Dict, Any, List
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel

from config import delta_config, compounding_config, system_config
from delta_client import delta_client
from agents.orchestrator import orchestrator
from agents.shadow_account import shadow_account
from agents.execution_manager import execution_manager
from agents.forensic_learner import forensic_learner
from agents.session_momentum_engine import session_momentum_engine

app = FastAPI(title="Delta Exchange US Stocks RWA Multi-Agent Trading System")

# Ensure web directory exists
web_dir = os.path.join(os.path.dirname(__file__), "web")
os.makedirs(web_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=web_dir), name="static")

class ActiveConnections:
    def __init__(self):
        self.connections: List[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.connections.append(ws)

    def disconnect(self, ws: WebSocket):
        if ws in self.connections:
            self.connections.remove(ws)

    async def broadcast(self, message: Dict[str, Any]):
        for conn in list(self.connections):
            try:
                await conn.send_json(message)
            except Exception:
                self.disconnect(conn)

ws_manager = ActiveConnections()

class ModeRequest(BaseModel):
    mode: str

class ScanRequest(BaseModel):
    symbol: str = ""

@app.get("/")
async def get_index():
    index_path = os.path.join(web_dir, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return HTMLResponse("<h1>Dashboard loading... please check web/index.html</h1>")

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        # Send initial state immediately
        summary = shadow_account.get_performance_summary()
        await websocket.send_json({
            "type": "INITIAL_STATE",
            "mode": execution_manager.mode,
            "portfolio": summary,
            "logs": orchestrator.deliberation_logs[-25:],
            "symbols": system_config.US_STOCKS_RWA,
            "forensic_memory": forensic_learner.get_memory_summary()
        })
        while True:
            # Keep socket alive and receive client commands
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
    except Exception:
        ws_manager.disconnect(websocket)

@app.get("/api/status")
async def get_status():
    summary = shadow_account.get_performance_summary()
    session_info = session_momentum_engine.get_current_session()
    return {
        "status": "ONLINE",
        "mode": execution_manager.mode,
        "base_url": delta_config.BASE_URL,
        "balance": summary["current_balance"],
        "target": compounding_config.TARGET_CAPITAL,
        "progress_pct": round(((summary["current_balance"] - 60.0) / (5000.0 - 60.0)) * 100, 2),
        "open_positions": len([p for p in delta_client.get_positions().get("result", []) if abs(float(p.get("size", 0))) > 0]) if execution_manager.mode == "LIVE" else summary["open_positions_count"],
        "total_trades": summary["total_trades"],
        "win_rate": summary["win_rate"],
        "is_scanning": orchestrator.is_scanning,
        "session": session_info
    }

@app.get("/api/session")
async def get_session():
    """Return active market session, momentum tier, and dynamic parameters."""
    return session_momentum_engine.get_current_session()

@app.post("/api/telegram/scan-digest")
async def send_telegram_scan_digest():
    """Trigger an instant real-time scan and dispatch the Radar Digest to Telegram."""
    scan_res = orchestrator.run_full_scan(force_digest=True)
    return {"success": True, "message": "Telegram scan digest dispatched"}

@app.get("/api/portfolio")
async def get_portfolio():
    return shadow_account.get_performance_summary()

@app.get("/api/tickers")
async def get_tickers():
    tickers = []
    for sym in system_config.US_STOCKS_RWA:
        t = delta_client.get_ticker(sym)
        ob = delta_client.get_l2_orderbook(sym)
        spec = system_config.CONTRACT_SPECS.get(sym, {})
        tickers.append({
            "symbol": sym,
            "name": spec.get("name", sym),
            "contract_val": spec.get("contract_val", 0.01),
            "mark_price": t.get("mark_price", 0.0),
            "price_change_24h": t.get("price_change_24h", 0.0),
            "imbalance": ob.get("imbalance", 0.0),
            "spread_pct": ob.get("spread_pct", 0.0),
            "best_bid": ob.get("best_bid", 0.0),
            "best_ask": ob.get("best_ask", 0.0)
        })
    return tickers

@app.get("/api/logs")
async def get_logs():
    return orchestrator.deliberation_logs

@app.get("/api/forensic-memory")
async def get_forensic_memory():
    """Expose self-improving memory, autopsies, quarantined symbols, and learned anti-patterns."""
    return forensic_learner.get_memory_summary()

@app.post("/api/scan")
async def trigger_scan(req: ScanRequest = ScanRequest()):
    """Trigger an on-demand scan across US stock RWAs."""
    scan_res = orchestrator.run_full_scan()
    # Broadcast scan update over WebSocket
    await ws_manager.broadcast({
        "type": "SCAN_COMPLETED",
        "scan": scan_res,
        "portfolio": shadow_account.get_performance_summary(),
        "logs": orchestrator.deliberation_logs[-20:],
        "forensic_memory": forensic_learner.get_memory_summary()
    })
    return scan_res

@app.post("/api/mode")
async def set_mode(req: ModeRequest):
    execution_manager.set_mode(req.mode)
    await ws_manager.broadcast({"type": "MODE_CHANGED", "mode": execution_manager.mode})
    return {"mode": execution_manager.mode}

@app.post("/api/reset")
async def reset_balance():
    """Reset shadow balance back to $60.00 for a fresh run."""
    shadow_account.__init__(initial_balance=60.0)
    orchestrator.log_agent_thought("System", "ALL", "Reset shadow bankroll to $60.00 USD. Stage 1 initiated.", level="INFO")
    summary = shadow_account.get_performance_summary()
    await ws_manager.broadcast({
        "type": "BALANCE_RESET",
        "portfolio": summary,
        "logs": orchestrator.deliberation_logs[-20:]
    })
    return summary

# Asynchronous Background Worker Loop
async def background_trading_loop():
    await asyncio.sleep(2)
    orchestrator.log_agent_thought("Supervisor", "INIT", "Autonomous US Stock RWA multi-agent engine online. Starting periodic scanning.", level="SUCCESS")
    while True:
        try:
            # 1. Update tickers for open positions
            tickers_map = {}
            for sym in system_config.US_STOCKS_RWA[:8]:
                tk = delta_client.get_ticker(sym)
                if tk.get("mark_price", 0) > 0:
                    tickers_map[sym] = tk["mark_price"]
            
            # Sync trailing stops and PnL
            portfolio = execution_manager.sync_portfolio(tickers_map)

            # 2. Run scan cycle every interval
            scan_data = orchestrator.run_full_scan()

            # 3. Broadcast to UI
            await ws_manager.broadcast({
                "type": "CYCLE_UPDATE",
                "portfolio": portfolio,
                "logs": orchestrator.deliberation_logs[-15:],
                "active_results": scan_data.get("results", []),
                "forensic_memory": forensic_learner.get_memory_summary()
            })
        except Exception as e:
            print(f"[BackgroundLoop] Error: {e}")
        
        sleep_interval = session_momentum_engine.get_scan_interval()
        await asyncio.sleep(sleep_interval)

@app.on_event("startup")
async def on_startup():
    asyncio.create_task(background_trading_loop())
