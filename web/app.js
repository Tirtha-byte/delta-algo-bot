let ws = null;
let currentMode = "SHADOW";

function initWebSocket() {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const wsUrl = `${protocol}//${window.location.host}/ws`;

  ws = new WebSocket(wsUrl);

  ws.onopen = () => {
    console.log("[WS] Connected to Delta RWA Multi-Agent Engine");
    fetchTickers();
  };

  ws.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      handleWebSocketMessage(data);
    } catch (err) {
      console.error("[WS] Parse error:", err);
    }
  };

  ws.onclose = () => {
    console.warn("[WS] Disconnected, retrying in 3s...");
    setTimeout(initWebSocket, 3000);
  };
}

function handleWebSocketMessage(data) {
  if (data.type === "INITIAL_STATE") {
    currentMode = data.mode;
    updateModeBadge();
    updatePortfolio(data.portfolio);
    if (data.logs) renderLogs(data.logs);
    if (data.forensic_memory) renderForensicMemory(data.forensic_memory);
  } else if (data.type === "CYCLE_UPDATE" || data.type === "SCAN_COMPLETED") {
    if (data.portfolio) updatePortfolio(data.portfolio);
    if (data.logs) renderLogs(data.logs);
    if (data.forensic_memory) renderForensicMemory(data.forensic_memory);
    if (data.active_results && data.active_results.length > 0) {
      renderLastInspection(data.active_results[0]);
    }
    fetchTickers();
  } else if (data.type === "BALANCE_RESET") {
    updatePortfolio(data.portfolio);
    if (data.logs) renderLogs(data.logs);
  } else if (data.type === "MODE_CHANGED") {
    currentMode = data.mode;
    updateModeBadge();
  }
}

function updateModeBadge() {
  const badge = document.getElementById("modeBadge");
  if (!badge) return;
  if (currentMode === "SHADOW") {
    badge.textContent = "SHADOW SIMULATION";
    badge.style.color = "var(--color-purple)";
    badge.style.borderColor = "rgba(139, 92, 246, 0.4)";
    badge.style.background = "rgba(139, 92, 246, 0.15)";
  } else {
    badge.textContent = "LIVE DELTA API";
    badge.style.color = "var(--color-rose)";
    badge.style.borderColor = "rgba(244, 63, 94, 0.4)";
    badge.style.background = "rgba(244, 63, 94, 0.15)";
  }
}

function updatePortfolio(p) {
  if (!p) return;
  const balance = p.current_balance || 60.0;
  document.getElementById("accountBalance").textContent = balance.toFixed(2);
  
  const netPnl = p.net_pnl || 0.0;
  const growthPct = p.growth_pct || 0.0;
  const pnlTag = document.getElementById("netPnlTag");
  pnlTag.textContent = `${netPnl >= 0 ? "+" : ""}$${netPnl.toFixed(2)} (${growthPct >= 0 ? "+" : ""}${growthPct.toFixed(1)}%)`;
  pnlTag.className = `pnl-tag ${netPnl >= 0 ? "text-emerald" : "text-rose"}`;

  // Milestone Progress Bar ($60 to $5,000)
  const target = 5000.0;
  const start = 60.0;
  const progressPct = Math.min(100, Math.max(0.5, ((balance - start) / (target - start)) * 100));
  document.getElementById("progressBarFill").style.width = `${progressPct}%`;
  document.getElementById("progressPctText").textContent = `${progressPct.toFixed(2)}% to $5,000 Goal`;

  // Stage Badge
  const stageBadge = document.getElementById("currentStageBadge");
  if (balance < 200) {
    stageBadge.textContent = "STAGE 1: BASE BUILDING ($60 ➜ $200)";
  } else if (balance < 1000) {
    stageBadge.textContent = "STAGE 2: ACCELERATION ($200 ➜ $1,000)";
  } else {
    stageBadge.textContent = "STAGE 3: INSTITUTIONAL COMPOUNDING ($1,000 ➜ $5,000)";
  }

  // Stats
  document.getElementById("winRateVal").textContent = `${(p.win_rate || 0).toFixed(1)}%`;
  document.getElementById("profitFactorVal").textContent = (p.profit_factor || 1.0).toFixed(2);
  document.getElementById("totalTradesVal").textContent = p.total_trades || 0;
  document.getElementById("openTradesVal").textContent = p.open_positions_count || 0;
  document.getElementById("openCount").textContent = p.open_positions_count || 0;

  // Render open positions & journal
  renderPositions(p.open_positions || []);
  renderJournal(p.recent_trades || []);
}

function renderLogs(logs) {
  const container = document.getElementById("terminalLogs");
  if (!container || !logs) return;

  container.innerHTML = "";
  logs.forEach((log) => {
    const timeStr = log.timestamp ? log.timestamp.substring(11, 19) : "00:00:00";
    let levelClass = "log-info";
    if (log.level === "SUCCESS") levelClass = "log-success";
    if (log.level === "WARN") levelClass = "log-warn";
    if (log.level === "VETO") levelClass = "log-veto";

    const entry = document.createElement("div");
    entry.className = `log-entry ${levelClass}`;
    entry.innerHTML = `
      <span class="log-time">[${timeStr}]</span>
      <span class="log-agent">[${log.agent}]</span>
      <span class="log-msg">${log.message}</span>
    `;
    container.appendChild(entry);
  });
  container.scrollTop = container.scrollHeight;
}

function renderLastInspection(result) {
  if (!result) return;
  const sym = result.symbol;
  document.getElementById("inspSymbol").textContent = sym;
  
  const statusEl = document.getElementById("inspStatus");
  statusEl.textContent = result.dual_key_passed ? "DUAL-KEY PASSED" : "GATE VETOED";
  statusEl.style.color = result.dual_key_passed ? "var(--color-emerald)" : "var(--color-rose)";
  statusEl.style.background = result.dual_key_passed ? "rgba(16, 185, 129, 0.2)" : "rgba(244, 63, 94, 0.2)";

  // Gate 1 News
  const n = result.news_intelligence;
  if (n) {
    document.getElementById("gate1Score").textContent = `Usable: +${n.usable_sentiment} | Conf: ${(n.confidence*100).toFixed(0)}%`;
    const sc = n.source_counts || {};
    document.getElementById("t1Count").textContent = sc.tier1_primary || 0;
    document.getElementById("t2Count").textContent = sc.tier2_news || 0;
    document.getElementById("t3Count").textContent = sc.tier3_social || 0;
    document.getElementById("authScore").textContent = `${(n.authenticity_score * 100).toFixed(0)}%`;
    document.getElementById("epistemicTag").textContent = (n.facts && n.facts.length > 0) ? "VERIFIED FACT" : "MARKET REPORT";
  }

  // Gate 2 Quant
  const q = result.quant_analysis;
  if (q) {
    const gate2El = document.getElementById("gate2Score");
    if (gate2El) {
      const alphaSign = (q.composite_alpha || 0) >= 0 ? "+" : "";
      const alphaVal = q.composite_alpha !== undefined ? `${alphaSign}${q.composite_alpha.toFixed(2)}` : "N/A";
      const confVal = q.quant_confidence !== undefined ? `${(q.quant_confidence * 100).toFixed(0)}%` : "N/A";
      gate2El.textContent = `Signal: ${q.signal} | Alpha: ${alphaVal} | Conf: ${confVal}`;
    }

    const ind = q.indicators || {};
    const qf = ind.qlib_factors || {};
    const mtf = ind.mtf || {};

    const macroEl = document.getElementById("macroTrendVal");
    if (macroEl) {
      macroEl.textContent = mtf.macro_trend_1h || "NEUTRAL";
      if (mtf.macro_trend_1h === "BULLISH") macroEl.className = "text-emerald";
      else if (mtf.macro_trend_1h === "BEARISH") macroEl.className = "text-rose";
      else macroEl.className = "";
    }

    const pbEl = document.getElementById("pullbackVal");
    if (pbEl) {
      const pbDist = mtf.pullback_dist_atr !== undefined ? `${mtf.pullback_dist_atr} ATR` : "HEALTHY";
      pbEl.textContent = pbDist;
      if (mtf.pullback_dist_atr > 2.0) {
        pbEl.className = "text-amber";
      } else {
        pbEl.className = "text-emerald";
      }
    }

    const stopTypeEl = document.getElementById("stopTypeVal");
    if (stopTypeEl) {
      stopTypeEl.textContent = mtf.stop_type || "STRUCTURAL PIVOT";
    }

    const trendEl = document.getElementById("trendVal");
    if (trendEl) {
      trendEl.textContent = ind.trend || (ind.ema20 > ind.ema50 ? "BULLISH" : "BEARISH");
    }

    const rsiEl = document.getElementById("rsiVal");
    if (rsiEl) rsiEl.textContent = (ind.rsi || 50).toFixed(1);

    const atrEl = document.getElementById("atrVal");
    if (atrEl) atrEl.textContent = `$${(ind.atr || 0).toFixed(2)}`;

    const pvCorrEl = document.getElementById("pvCorrVal");
    if (pvCorrEl) {
      const pvCorr = qf.composite_corr_pv !== undefined ? qf.composite_corr_pv : (qf.corr_pv_10 || 0);
      pvCorrEl.textContent = `${pvCorr >= 0 ? "+" : ""}${pvCorr.toFixed(2)}`;
    }

    const kmid2El = document.getElementById("kmid2Val");
    if (kmid2El) {
      const kmid2 = qf.kmid2 !== undefined ? qf.kmid2 : 0;
      kmid2El.textContent = `${kmid2 >= 0 ? "+" : ""}${kmid2.toFixed(2)}`;
    }

    const rocEl = document.getElementById("rocCompVal");
    if (rocEl) {
      const roc = qf.composite_roc !== undefined ? (qf.composite_roc * 100) : 0;
      rocEl.textContent = `${roc >= 0 ? "+" : ""}${roc.toFixed(1)}%`;
    }

    const micro = ind.microstructure || {};
    const microDriftEl = document.getElementById("microDriftVal");
    if (microDriftEl) {
      const md = micro.micro_drift !== undefined ? micro.micro_drift : 0.0;
      microDriftEl.textContent = `${md >= 0 ? "+" : ""}${md.toFixed(2)}`;
      if (md >= 0.15) microDriftEl.className = "text-emerald";
      else if (md <= -0.15) microDriftEl.className = "text-rose";
      else microDriftEl.className = "";
    }

    const depthImbEl = document.getElementById("depthImbVal");
    if (depthImbEl) {
      const di = micro.depth_imbalance_5 !== undefined ? (micro.depth_imbalance_5 * 100) : 0.0;
      depthImbEl.textContent = `${di >= 0 ? "+" : ""}${di.toFixed(1)}%`;
      if (di >= 15) depthImbEl.className = "text-emerald";
      else if (di <= -15) depthImbEl.className = "text-rose";
      else depthImbEl.className = "";
    }

    const obEl = document.getElementById("obImbalance");
    if (obEl) obEl.textContent = `${((ind.orderbook_imbalance || 0) * 100).toFixed(1)}%`;

    const slEl = document.getElementById("slVal");
    if (slEl) slEl.textContent = `$${(q.stop_loss || 0).toFixed(2)}`;

    const tpEl = document.getElementById("tpVal");
    if (tpEl) tpEl.textContent = `$${(q.take_profit_1 || 0).toFixed(2)}`;
  }

  // Gate 2.5 Forensic Pre-Flight
  const fc = result.forensic_check;
  const gateForensicScore = document.getElementById("gateForensicScore");
  const inspQuarantineStatus = document.getElementById("inspQuarantineStatus");
  const inspHurdle = document.getElementById("inspHurdle");
  const inspAdaptiveAtr = document.getElementById("inspAdaptiveAtr");
  const inspAntiPatternStatus = document.getElementById("inspAntiPatternStatus");

  if (fc) {
    if (fc.approved) {
      if (gateForensicScore) {
        gateForensicScore.textContent = `Approved | 0 Rule Collisions`;
        gateForensicScore.className = "gate-score text-emerald";
      }
      if (inspQuarantineStatus) {
        inspQuarantineStatus.textContent = "CLEAR";
        inspQuarantineStatus.className = "text-emerald";
      }
      if (inspAntiPatternStatus) {
        inspAntiPatternStatus.textContent = "PASSED (NO MISTAKE MATCH)";
        inspAntiPatternStatus.className = "text-emerald";
      }
    } else {
      if (gateForensicScore) {
        gateForensicScore.textContent = `VETOED: ${fc.veto_code || "REJECTED"}`;
        gateForensicScore.className = "gate-score text-rose";
      }
      if (fc.veto_code === "ASSET_IN_QUARANTINE" && inspQuarantineStatus) {
        inspQuarantineStatus.textContent = "QUARANTINED";
        inspQuarantineStatus.className = "text-rose";
      }
      if (inspAntiPatternStatus) {
        inspAntiPatternStatus.textContent = `VETO: ${fc.reason}`;
        inspAntiPatternStatus.className = "text-rose";
      }
    }
    if (inspHurdle) inspHurdle.textContent = `${((fc.confidence_hurdle || 0.65) * 100).toFixed(0)}%`;
    if (inspAdaptiveAtr) inspAdaptiveAtr.textContent = `${(fc.adaptive_atr_multiplier || 1.5).toFixed(2)}x`;
  }
}

async function fetchTickers() {
  try {
    const res = await fetch("/api/tickers");
    if (!res.ok) return;
    const tickers = await res.json();
    renderRadar(tickers);
  } catch (e) {
    console.error("Failed to fetch tickers:", e);
  }
}

function renderRadar(tickers) {
  const list = document.getElementById("radarList");
  if (!list || !tickers) return;

  list.innerHTML = "";
  tickers.forEach((t) => {
    const chg = t.price_change_24h || 0.0;
    const isUp = chg >= 0;
    const item = document.createElement("div");
    item.className = "radar-item";
    item.innerHTML = `
      <div>
        <div class="radar-sym">${t.symbol}</div>
        <div class="radar-name">${t.name} (Val: ${t.contract_val})</div>
      </div>
      <div>
        <div class="radar-price">$${(t.mark_price || 0).toFixed(2)}</div>
        <div class="radar-chg ${isUp ? 'text-emerald' : 'text-rose'}">${isUp ? '+' : ''}${chg.toFixed(2)}%</div>
      </div>
    `;
    list.appendChild(item);
  });
}

function renderPositions(positions) {
  const tbody = document.getElementById("positionsTableBody");
  if (!tbody) return;

  if (positions.length === 0) {
    tbody.innerHTML = `<tr><td colspan="9" class="text-muted text-center">No active positions. Awaiting high-conviction dual-key setup.</td></tr>`;
    return;
  }

  tbody.innerHTML = "";
  positions.forEach((pos) => {
    const isUp = (pos.unrealized_pnl || 0) >= 0;
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><strong>${pos.symbol}</strong></td>
      <td><span class="${pos.side === 'BUY' ? 'text-emerald' : 'text-rose'}">${pos.side}</span></td>
      <td>${pos.contracts} (${pos.leverage}x)</td>
      <td>$${pos.entry_price.toFixed(2)}</td>
      <td>$${pos.current_price.toFixed(2)}</td>
      <td class="${pos.sl_at_breakeven ? 'text-emerald' : 'text-amber'}">$${pos.stop_loss.toFixed(2)} ${pos.sl_at_breakeven ? '(BE)' : ''}</td>
      <td class="text-cyan">$${pos.take_profit_1.toFixed(2)}</td>
      <td class="${isUp ? 'text-emerald' : 'text-rose'}">${isUp ? '+' : ''}$${pos.unrealized_pnl.toFixed(2)} (${isUp ? '+' : ''}${pos.unrealized_pnl_pct.toFixed(1)}%)</td>
      <td><span class="badge-mini">${pos.tp1_hit ? 'RUNNER (TP1 HIT)' : 'ACTIVE'}</span></td>
    `;
    tbody.appendChild(tr);
  });
}

function renderJournal(trades) {
  const tbody = document.getElementById("journalTableBody");
  if (!tbody) return;

  if (trades.length === 0) {
    tbody.innerHTML = `<tr><td colspan="9" class="text-muted text-center">No closed trades yet. System runs with strict capital preservation.</td></tr>`;
    return;
  }

  tbody.innerHTML = "";
  trades.slice().reverse().forEach((t) => {
    const isWin = (t.realized_pnl || 0) > 0;
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><strong>${t.symbol}</strong></td>
      <td><span class="${t.side === 'BUY' ? 'text-emerald' : 'text-rose'}">${t.side}</span></td>
      <td>${t.contracts}</td>
      <td>$${t.entry_price.toFixed(2)}</td>
      <td>$${t.exit_price.toFixed(2)}</td>
      <td class="${isWin ? 'text-emerald' : 'text-rose'}">${isWin ? '+' : ''}$${t.realized_pnl.toFixed(2)}</td>
      <td class="${isWin ? 'text-emerald' : 'text-rose'}">${isWin ? '+' : ''}${t.return_pct.toFixed(1)}%</td>
      <td><span class="badge-mini">${t.close_reason}</span></td>
      <td class="text-muted">${t.closed_at ? t.closed_at.substring(11, 19) : ''}</td>
    `;
    tbody.appendChild(tr);
  });
}

async function triggerManualScan() {
  const btn = document.getElementById("btnScan");
  btn.disabled = true;
  btn.innerHTML = `<span class="btn-icon">⏳</span> Scanning...`;
  try {
    const res = await fetch("/api/scan", { method: "POST" });
    const data = await res.json();
    console.log("Scan completed:", data);
  } catch (e) {
    console.error("Scan error:", e);
  } finally {
    btn.disabled = false;
    btn.innerHTML = `<span class="btn-icon">⚡</span> Run Multi-Agent Scan`;
  }
}

async function toggleMode() {
  const newMode = currentMode === "SHADOW" ? "LIVE" : "SHADOW";
  try {
    const res = await fetch("/api/mode", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode: newMode })
    });
    const data = await res.json();
    currentMode = data.mode;
    updateModeBadge();
  } catch (e) {
    console.error("Toggle mode error:", e);
  }
}

async function resetBankroll() {
  if (!confirm("Are you sure you want to reset your bankroll back to $60.00?")) return;
  try {
    const res = await fetch("/api/reset", { method: "POST" });
    const data = await res.json();
    updatePortfolio(data);
  } catch (e) {
    console.error("Reset error:", e);
  }
}

async function fetchForensicMemory() {
  try {
    const res = await fetch("/api/forensic-memory");
    if (!res.ok) return;
    const data = await res.json();
    renderForensicMemory(data);
  } catch (e) {
    console.error("Failed to fetch forensic memory:", e);
  }
}

function renderForensicMemory(mem) {
  if (!mem) return;

  // 1. Top Badges & Counters
  const preventedBadge = document.getElementById("preventedMistakesBadge");
  if (preventedBadge) {
    preventedBadge.textContent = `${mem.prevented_mistakes_count || 0} Repeated Mistakes Avoided`;
  }
  const autopsiesVal = document.getElementById("totalAutopsiesVal");
  if (autopsiesVal) autopsiesVal.textContent = mem.total_autopsies || 0;

  const lossesVal = document.getElementById("totalLossesVal");
  if (lossesVal) lossesVal.textContent = mem.total_losses_analyzed || 0;

  const winsVal = document.getElementById("totalWinsVal");
  if (winsVal) winsVal.textContent = mem.total_wins_analyzed || 0;

  const qCount = Object.keys(mem.quarantined_symbols || {}).length;
  const quarantinedVal = document.getElementById("quarantinedCountVal");
  if (quarantinedVal) quarantinedVal.textContent = qCount;

  // 2. Quarantined Assets List
  const qList = document.getElementById("quarantineBadgesList");
  if (qList) {
    const qEntries = Object.entries(mem.quarantined_symbols || {});
    if (qEntries.length === 0) {
      qList.innerHTML = `<span class="text-muted">No assets currently quarantined. All tracked pairs eligible for dual-key evaluation.</span>`;
    } else {
      qList.innerHTML = "";
      qEntries.forEach(([sym, info]) => {
        const span = document.createElement("span");
        span.className = "quarantine-tag";
        span.innerHTML = `⚠️ <strong>${sym}</strong>: ${info.reason || "Cooling off"}`;
        qList.appendChild(span);
      });
    }
  }

  // 3. Adaptive ATR Stop Multipliers List
  const atrList = document.getElementById("adaptiveAtrBadgesList");
  if (atrList) {
    const profiles = Object.entries(mem.asset_profiles || {});
    const adapted = profiles.filter(([_, p]) => (p.adaptive_atr_multiplier || 1.5) > 1.5);
    if (adapted.length === 0) {
      atrList.innerHTML = `<span class="text-muted">Default baseline: 1.50x ATR buffer across all pairs (No wick squeeze detected).</span>`;
    } else {
      atrList.innerHTML = "";
      adapted.forEach(([sym, p]) => {
        const span = document.createElement("span");
        span.className = "adaptive-tag";
        span.innerHTML = `⚙️ <strong>${sym}</strong>: ${p.adaptive_atr_multiplier.toFixed(2)}x ATR buffer (Hurdle: ${((p.confidence_hurdle || 0.65)*100).toFixed(0)}%)`;
        atrList.appendChild(span);
      });
    }
  }

  // 4. Recent Autopsies Case Files Table
  const tbody = document.getElementById("autopsyTableBody");
  if (tbody) {
    const autopsies = mem.recent_autopsies || [];
    if (autopsies.length === 0) {
      tbody.innerHTML = `<tr><td colspan="6" class="text-muted text-center">No loss autopsies recorded. Zero repeated mistakes active.</td></tr>`;
    } else {
      tbody.innerHTML = "";
      autopsies.slice().reverse().forEach((a) => {
        const tr = document.createElement("tr");
        let archetypeClass = "archetype-counter-trend";
        if (a.archetype === "VOLATILITY_WICK_SQUEEZE") archetypeClass = "archetype-wick-squeeze";
        else if (a.archetype && a.archetype.includes("OVEREXTENDED")) archetypeClass = "archetype-overextended";
        else if (a.archetype === "UNCONFIRMED_SOCIAL_HYPE") archetypeClass = "archetype-unconfirmed";

        const timeStr = a.timestamp ? a.timestamp.substring(11, 19) : "";
        tr.innerHTML = `
          <td>${timeStr}</td>
          <td><strong>${a.symbol}</strong></td>
          <td class="text-rose">-$${Math.abs(a.realized_pnl || 0).toFixed(2)}</td>
          <td><span class="archetype-pill ${archetypeClass}">${a.archetype || "STOP_OUT"}</span></td>
          <td style="max-width: 320px; font-size: 0.75rem; line-height: 1.3;">${a.diagnosis || ""}</td>
          <td style="max-width: 320px; font-size: 0.75rem; color: var(--color-cyan); line-height: 1.3;">${a.remedy || ""}</td>
        `;
        tbody.appendChild(tr);
      });
    }
  }
}

// Initial boot
async function fetchResearchStatus() {
  try {
    const res = await fetch("/api/research/status");
    const data = await res.json();
    renderResearchStatus(data);
  } catch (err) {
    console.error("[Research] Error fetching research status:", err);
  }
}

async function fetchReconciliationStatus() {
  try {
    const res = await fetch("/api/reconciliation");
    const data = await res.json();
    const badge = document.getElementById("reconciliationBadge");
    if (badge) {
      if (data.is_halted) {
        badge.textContent = "HALTED: " + (data.halt_reason || "Mismatch");
        badge.className = "stat-value text-rose";
      } else {
        badge.textContent = "HEALTHY (Synchronized)";
        badge.className = "stat-value text-emerald";
      }
    }
  } catch (err) {
    console.error("[Reconciliation] Error fetching reconciliation status:", err);
  }
}

function renderResearchStatus(data) {
  if (!data) return;
  const countEl = document.getElementById("labCycleCount");
  if (countEl) countEl.textContent = data.cycle_count || 0;

  const msgEl = document.getElementById("labStatusMsg");
  if (msgEl && data.status_message) msgEl.textContent = data.status_message;

  const topCandEl = document.getElementById("topCandidateDetails");
  if (topCandEl) {
    const cand = data.top_candidate;
    if (cand) {
      topCandEl.innerHTML = `
        <div><b>ID:</b> <span style="color:#38bdf8;">${cand.candidate_id || "CAND_1"}</span> | <b>Score:</b> <span style="color:#10b981;">${cand.composite_score || 0}/100</span></div>
        <div><b>OOS Profit Factor:</b> ${cand.oos_profit_factor || 0} | <b>WFE:</b> ${cand.wfe_ratio || 0} | <b>Win Rate:</b> ${cand.win_rate || 0}%</div>
        <div style="color:#94a3b8; font-size:11px;">Params: ${JSON.stringify(cand.parameter_set || {})}</div>
      `;
    }
  }
}

window.addEventListener("DOMContentLoaded", () => {
  initWebSocket();
  fetchTickers();
  fetchForensicMemory();
  fetchResearchStatus();
  fetchReconciliationStatus();
  setInterval(fetchTickers, 15000);
  setInterval(fetchForensicMemory, 15000);
  setInterval(fetchResearchStatus, 15000);
  setInterval(fetchReconciliationStatus, 15000);
});
