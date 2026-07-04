/* SCOPIC EA — dashboard app (vanilla JS, no build step) */
"use strict";
const $ = (id) => document.getElementById(id);
const api = {
  async get(p) { const r = await fetch(p); if (!r.ok) throw new Error(await r.text()); return r.json(); },
  async send(p, m, b) {
    const r = await fetch(p, { method: m, headers: { "Content-Type": "application/json" }, body: JSON.stringify(b || {}) });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
    return r.json();
  },
};
const state = { instrument: "GC", tf: "5M", source: "", candles: [], signals: [],
                settings: {}, chat: [], optInst: "GC", options: null };
const GREEN = "#00ff9d", VIOLET = "#b44dff", RED = "#ff3b4d";

/* ---------- tabs & segs ---------- */
document.querySelectorAll(".tab").forEach((b) => b.addEventListener("click", () => {
  document.querySelectorAll(".tab").forEach((x) => x.classList.toggle("active", x === b));
  document.querySelectorAll("main .panel").forEach((p) => (p.hidden = p.id !== "tab-" + b.dataset.tab));
  const t = b.dataset.tab;
  if (t === "news") loadNews();
  if (t === "world") loadWorld();
  if (t === "strategies") loadStats();
  if (t === "options") loadOptions();
  if (t === "ai") { loadAiStatus(); loadAnalyses(); loadInstructions(); }
  if (t === "chart") drawChart();
}));
function seg(el, cb) {
  el.querySelectorAll("b").forEach((x) => x.addEventListener("click", () => {
    el.querySelectorAll("b").forEach((y) => y.classList.toggle("on", y === x));
    cb(x.dataset.v);
  }));
}

/* ---------- header ---------- */
function renderSessions() {
  const defs = [["ASIA", 0, 6], ["LON", 8, 16.5], ["NY", 14.5, 21]];
  const h = new Date().getUTCHours() + new Date().getUTCMinutes() / 60;
  $("sessionClocks").innerHTML = defs.map(([n, o, c]) =>
    `<span class="sess ${h >= o && h < c ? "open" : ""}"><span class="dot"></span>${n}</span>`).join("");
}
async function pollHealth() {
  try { await api.get("/api/health"); $("connPill").className = "pill conn ok"; $("connText").textContent = "live"; }
  catch { $("connPill").className = "pill conn bad"; $("connText").textContent = "offline"; }
}
async function pollStats() {
  try {
    const s = await api.get("/api/stats");
    const limit = s.daily_limit || 3, losses = Math.min(s.losses_today || 0, limit);
    $("fvSlots").innerHTML = Array.from({ length: limit }, (_, i) => `<span class="fv-slot ${i < losses ? "hit" : ""}"></span>`).join("");
    $("standdownBanner").hidden = losses < limit;
    renderStatsTable(s.strategies || []);
  } catch {}
}
async function pollSnapshot() {
  try {
    const snaps = await api.get(`/api/snapshots?instrument=${state.instrument}&limit=1`);
    if (snaps.length) {
      const sn = snaps[0];
      $("regimeBadge").textContent = "REGIME: " + (sn.regime || "—").replaceAll("_", " ");
      const cvd = sn.cum_session_delta;
      $("cvdBadge").textContent = `CVD ${cvd > 0 ? "+" : ""}${(cvd ?? 0).toLocaleString()} ${sn.cvd_slope === "RISING" ? "↑" : sn.cvd_slope === "FALLING" ? "↓" : "→"} · ${sn.age_min}m ago`;
    } else {
      $("regimeBadge").textContent = "REGIME: no engine feed";
      $("cvdBadge").textContent = "CVD — (MotiveWave offline?)";
    }
  } catch {}
}

/* ---------- chart ---------- */
const cv = $("cv"), tip = $("chartTip");
const PAD = { l: 12, r: 66, t: 16, b: 26 };
const TF_MAP = { "500T": ["5m", "1d"], "5M": ["5m", "1d"], "15M": ["15m", "5d"],
                 "1H": ["60m", "1mo"], "4H": ["60m", "3mo"], "1D": ["1d", "1y"] };
function agg4h(c60) {
  const out = [];
  for (const c of c60) {
    const b = Math.floor(c.t / 14400000) * 14400000;
    const last = out[out.length - 1];
    if (last && last.t === b) { last.h = Math.max(last.h, c.h); last.l = Math.min(last.l, c.l); last.c = c.c; last.v += c.v; }
    else out.push({ t: b, o: c.o, h: c.h, l: c.l, c: c.c, v: c.v });
  }
  return out;
}
async function loadCandles() {
  const [iv, rg] = TF_MAP[state.tf];
  try {
    let c = await api.get(`/api/candles?instrument=${state.instrument}&interval=${iv}&range=${rg}`);
    if (state.tf === "4H") c = agg4h(c);
    state.candles = c.slice(-180);
  } catch { state.candles = []; }
  $("chartNote").textContent = state.tf === "500T"
    ? "500T signals plotted on a 5M context chart (tick candles aren't available from the delayed feed — MotiveWave shows the true 500T chart)."
    : "Chart feed is delayed context — execution view stays in MotiveWave.";
  drawChart();
}
function drawChart() {
  const dpr = devicePixelRatio || 1, w = cv.clientWidth, h = cv.clientHeight;
  if (!w) return;
  cv.width = w * dpr; cv.height = h * dpr;
  const ctx = cv.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  ctx.font = "10px IBM Plex Mono, monospace";
  const cs = state.candles;
  if (!cs.length) { ctx.fillStyle = "#8b93a1"; ctx.textAlign = "center"; ctx.fillText("No chart data", w / 2, h / 2); return; }
  let lo = Infinity, hi = -Infinity;
  cs.forEach((c) => { lo = Math.min(lo, c.l); hi = Math.max(hi, c.h); });
  const pad = (hi - lo) * 0.06 || 1; lo -= pad; hi += pad;
  const iw = w - PAD.l - PAD.r, ih = h - PAD.t - PAD.b, xw = iw / cs.length;
  const X = (i) => PAD.l + i * xw + xw / 2, Y = (p) => PAD.t + (1 - (p - lo) / (hi - lo)) * ih;
  ctx.strokeStyle = "rgba(255,255,255,.05)"; ctx.fillStyle = "#8b93a1"; ctx.textAlign = "left";
  for (let i = 0; i <= 6; i++) {
    const p = lo + ((hi - lo) * i) / 6, y = Y(p);
    ctx.beginPath(); ctx.moveTo(PAD.l, y); ctx.lineTo(w - PAD.r, y); ctx.stroke();
    ctx.fillText(p.toFixed(state.instrument === "GC" ? 1 : 0), w - PAD.r + 6, y + 3);
  }
  ctx.textAlign = "center";
  const every = Math.ceil(cs.length / 7);
  for (let i = 0; i < cs.length; i += every) {
    const d = new Date(cs[i].t);
    ctx.fillText(state.tf === "1D" ? d.toLocaleDateString(undefined, { month: "short", day: "numeric" })
      : d.toLocaleString(undefined, { day: "numeric", hour: "2-digit", minute: "2-digit" }), X(i), h - 8);
  }
  cs.forEach((c, i) => {
    const up = c.c >= c.o, col = up ? GREEN : VIOLET;
    ctx.strokeStyle = ctx.fillStyle = col;
    ctx.shadowColor = col; ctx.shadowBlur = 5;
    const x = X(i);
    ctx.beginPath(); ctx.moveTo(x, Y(c.h)); ctx.lineTo(x, Y(c.l)); ctx.stroke();
    const bw = Math.max(2, xw * 0.62), yo = Y(c.o), yc = Y(c.c);
    ctx.fillRect(x - bw / 2, Math.min(yo, yc), bw, Math.max(1.4, Math.abs(yc - yo)));
    ctx.shadowBlur = 0;
  });
  // markers: color = direction, shape = source
  const t0 = cs[0].t, span = cs[1] ? cs[1].t - cs[0].t : 60000, t1 = cs[cs.length - 1].t + span;
  state.signals.filter((s) => s.timeframe === state.tf && s.created_at >= t0 && s.created_at <= t1)
    .forEach((s) => {
      let idx = cs.findIndex((c) => s.created_at < c.t) - 1;
      if (idx < 0) idx = s.created_at >= cs[cs.length - 1].t ? cs.length - 1 : 0;
      const long = s.direction === "LONG";
      const price = long ? (s.entry_low ?? s.stop) : (s.entry_high ?? s.stop);
      if (price == null) return;
      const x = X(idx), y = Y(price), col = long ? GREEN : RED;
      ctx.fillStyle = col; ctx.shadowColor = col; ctx.shadowBlur = 9;
      ctx.globalAlpha = s.payload && s.payload.evaluation === "INTRABAR" && !s.payload.confirmed ? 0.55 : 1;
      ctx.beginPath();
      if (s.strategy_id === "AI_ANALYST") { ctx.moveTo(x, y - 6); ctx.lineTo(x + 5, y); ctx.lineTo(x, y + 6); ctx.lineTo(x - 5, y); }
      else if (long) { ctx.moveTo(x, y + 4); ctx.lineTo(x + 6, y + 13); ctx.lineTo(x - 6, y + 13); }
      else { ctx.moveTo(x, y - 4); ctx.lineTo(x + 6, y - 13); ctx.lineTo(x - 6, y - 13); }
      ctx.closePath(); ctx.fill();
      ctx.shadowBlur = 0; ctx.globalAlpha = 1;
    });
}
cv.addEventListener("mousemove", (e) => {
  const cs = state.candles; if (!cs.length) return;
  const rect = cv.getBoundingClientRect(), x = e.clientX - rect.left;
  const iw = cv.clientWidth - PAD.l - PAD.r;
  const i = Math.max(0, Math.min(cs.length - 1, Math.floor(((x - PAD.l) / iw) * cs.length)));
  const c = cs[i]; if (!c) { tip.hidden = true; return; }
  tip.hidden = false;
  tip.style.left = Math.min(x + 14, cv.clientWidth - 175) + "px";
  tip.style.top = (e.clientY - rect.top + 12) + "px";
  tip.textContent = `${new Date(c.t).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}\nO ${c.o.toFixed(2)}  H ${c.h.toFixed(2)}\nL ${c.l.toFixed(2)}  C ${c.c.toFixed(2)}\nVol ${c.v.toLocaleString()}`;
});
cv.addEventListener("mouseleave", () => (tip.hidden = true));
addEventListener("resize", () => { drawChart(); if (state.options) drawGex(); });

/* ---------- signals ---------- */
async function loadSignals() {
  try {
    const q = new URLSearchParams({ limit: 120, instrument: state.instrument });
    state.signals = await api.get("/api/signals?" + q);
    renderSignals(); drawChart();
  } catch {}
}
function esc(t) { return String(t).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); }
function renderSignals() {
  $("sigTitle").textContent = "Signals — " + state.tf;
  let rows = state.signals.filter((s) => s.timeframe === state.tf);
  if (state.source === "ENGINE") rows = rows.filter((s) => s.strategy_id !== "AI_ANALYST");
  if (state.source === "AI_ANALYST") rows = rows.filter((s) => s.strategy_id === "AI_ANALYST");
  if (!$("fUnconfirmed").checked) rows = rows.filter((s) => !(s.payload && s.payload.evaluation === "INTRABAR"));
  if (!$("fSuppressed").checked) rows = rows.filter((s) => !s.suppressed);
  $("signalsMeta").textContent = rows.length + " shown";
  const list = $("signalsList");
  if (!rows.length) { list.innerHTML = `<div class="empty glass">No ${state.tf} signals yet for ${state.instrument}.</div>`; return; }
  list.innerHTML = rows.map((s) => {
    const isAi = s.strategy_id === "AI_ANALYST", long = s.direction === "LONG";
    const when = new Date(s.created_at).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
    const badges = [];
    if (s.payload && s.payload.evaluation === "INTRABAR") badges.push(`<span class="badge warn">INTRABAR</span>`);
    if (s.suppressed) badges.push(`<span class="badge warn">${s.suppressed}</span>`);
    if (s.outcome) badges.push(`<span class="badge ${s.outcome === "WIN" ? "g" : s.outcome === "LOSS" ? "r" : ""}">${s.outcome}${s.pnl != null ? " $" + s.pnl : ""}</span>`);
    const acts = s.outcome ? "" : `<div class="acts">
      <button class="btn win" data-oc="WIN" data-id="${s.id}">Win</button>
      <button class="btn loss" data-oc="LOSS" data-id="${s.id}">Loss</button>
      <button class="btn" data-oc="BE" data-id="${s.id}">BE</button>
      <button class="btn" data-oc="SKIPPED" data-id="${s.id}">Skipped</button></div>`;
    return `<div class="sig glass ${long ? "long" : "short"} ${s.suppressed ? "suppressed" : ""}">
      <div class="rail"></div><div class="sig-body">
      <div class="sig-head"><span class="dir">${s.instrument} ${s.direction}</span>
        <span class="badge ${isAi ? "v" : "g"}">${isAi ? "◆ AI ANALYST" : s.strategy_id}</span>
        <span class="badge">${s.timeframe || ""}</span><span class="badge">R:R ${s.rr ?? "?"}</span>
        ${badges.join("")}<span class="spacer"></span><span class="muted small">${when}</span></div>
      <pre>${esc(s.card || "")}</pre>
      <button class="link-btn" data-expand>expand / collapse</button>${acts}</div></div>`;
  }).join("");
  list.querySelectorAll("[data-oc]").forEach((b) => b.addEventListener("click", async () => {
    let pnl = null;
    if (b.dataset.oc === "WIN" || b.dataset.oc === "LOSS" || b.dataset.oc === "BE") {
      const v = prompt("P&L in $ for this trade (optional — powers daily tracking):", "");
      if (v !== null && v.trim() !== "") pnl = v.trim();
    }
    try { await api.send(`/api/signals/${b.dataset.id}/outcome`, "POST", { outcome: b.dataset.oc, pnl }); loadSignals(); pollStats(); }
    catch (e) { alert(e.message); }
  }));
  list.querySelectorAll("[data-expand]").forEach((b) => b.addEventListener("click", () => b.closest(".sig").classList.toggle("open")));
}

/* ---------- options ---------- */
async function loadOptions(force) {
  $("optAge").textContent = "loading…";
  try {
    state.options = await api.get(`/api/options?instrument=${state.optInst}`);
    renderOptions();
  } catch (e) { $("optAge").textContent = "error: " + e.message; }
}
function renderOptions() {
  const o = state.options; if (!o) return;
  if (o.error) { $("optWidgets").innerHTML = `<div class="empty glass">${esc(o.error)}</div>`; return; }
  $("optAge").textContent = `as of ${o.as_of_utc} UTC · cache ${o.cache_age_sec}s · ${o.quote_delay_note}`;
  const neg = (o.net_gex_musd || 0) < 0;
  const term = (o.iv_term_structure || []).map((t) => `${t.dte_days}d ${t.atm_iv_pct}%`).join(" · ");
  const z = o.zero_dte_slice || {};
  $("optWidgets").innerHTML = `
    <div class="wid glass"><h4>Net GEX (${o.proxy})</h4>
      <div class="big ${neg ? "r-txt" : "g-txt"}">${neg ? "" : "+"}$${o.net_gex_musd}M</div>
      <div class="note">${esc(o.dealer_positioning || "")}</div></div>
    <div class="wid glass"><h4>Zero-gamma flip</h4>
      <div class="big">${o.zero_gamma_flip_proxy ?? "—"}</div>
      <div class="note">futures-equiv ≈ <b>${o.zero_gamma_flip_futures ?? "—"}</b></div></div>
    <div class="wid glass"><h4>Gamma walls</h4>
      <div>Call <b class="g-txt">${o.call_wall_proxy ?? "—"}</b> (fut ≈ ${o.call_wall_futures ?? "—"})</div>
      <div>Put <b class="r-txt">${o.put_wall_proxy ?? "—"}</b> (fut ≈ ${o.put_wall_futures ?? "—"})</div>
      <div class="note">Top OI: ${(o.top_oi_strikes_proxy || []).join(", ")}</div></div>
    <div class="wid glass"><h4>Vanna</h4>
      <div class="big ${o.net_vanna >= 0 ? "g-txt" : "v-txt"}">${o.net_vanna}</div>
      <div class="note">${esc(o.vanna_read || "")}</div></div>
    <div class="wid glass"><h4>IV term / skew</h4>
      <div>${term || "—"}</div>
      <div class="note">put-call skew ${o.put_call_skew_pts ?? "—"} pts</div></div>
    <div class="wid glass"><h4>0DTE slice (${z.dte_days ?? "—"}d)</h4>
      <div>GEX ${z.net_gex_musd ?? "—"}M · C-OI ${(z.call_oi || 0).toLocaleString()} · P-OI ${(z.put_oi || 0).toLocaleString()}</div>
      <div class="note">${esc(z.afternoon_note || "")}</div></div>`;
  drawGex();
}
function drawGex() {
  const o = state.options; const c = $("gexCv");
  if (!o || !o.gex_profile || !c.clientWidth) return;
  const dpr = devicePixelRatio || 1, w = c.clientWidth, h = c.clientHeight;
  c.width = w * dpr; c.height = h * dpr;
  const ctx = c.getContext("2d"); ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h); ctx.font = "10px IBM Plex Mono";
  const rows = o.gex_profile;
  const maxAbs = Math.max(...rows.map((r) => Math.abs(r.net_gex_musd)), 1);
  const P = { l: 60, r: 20, t: 10, b: 10 };
  const bh = Math.min(14, (h - P.t - P.b) / rows.length);
  const mid = P.l + (w - P.l - P.r) / 2;
  ctx.strokeStyle = "rgba(255,255,255,.15)";
  ctx.beginPath(); ctx.moveTo(mid, P.t); ctx.lineTo(mid, h - P.b); ctx.stroke();
  rows.forEach((r, i) => {
    const y = P.t + i * bh;
    const len = (Math.abs(r.net_gex_musd) / maxAbs) * ((w - P.l - P.r) / 2 - 6);
    const pos = r.net_gex_musd >= 0;
    ctx.fillStyle = pos ? GREEN : RED;
    ctx.shadowColor = ctx.fillStyle; ctx.shadowBlur = 4;
    ctx.fillRect(pos ? mid : mid - len, y, len, Math.max(2, bh - 3));
    ctx.shadowBlur = 0;
    if (i % Math.ceil(rows.length / 14) === 0) { ctx.fillStyle = "#8b93a1"; ctx.textAlign = "right"; ctx.fillText(String(r.strike), P.l - 6, y + bh - 3); }
  });
}
$("optRefresh").addEventListener("click", () => loadOptions(true));

/* ---------- AI tab ---------- */
async function loadAiStatus() {
  try {
    const st = await api.get("/api/ai/status");
    const on = !!state.settings.ai_enabled && st.configured;
    $("aiPill").className = "pill " + (on ? "on" : "");
    $("aiPillText").textContent = st.configured ? (on ? "AI ON" : "AI OFF") : "AI unconfigured";
    $("aiModel").textContent = st.model || "—";
    $("sAiModel").textContent = `${st.model} @ ${st.base_url}`;
    $("footModel").textContent = st.model || "—";
    $("aiMarket").textContent = st.market_open ? "OPEN" : "CLOSED";
    $("aiRuns").textContent = st.runs || 0;
    $("aiLastRun").textContent = st.last_run ? new Date(st.last_run).toLocaleTimeString() : "—";
    $("aiError").hidden = !st.last_error; $("aiError").textContent = st.last_error || "";
    const btn = $("aiPower");
    if (!st.configured) { btn.textContent = "SET AI_API_KEY ON RAILWAY"; btn.disabled = true; btn.className = "btn power"; }
    else if (on) { btn.textContent = "■ STOP AI"; btn.className = "btn power running"; btn.disabled = false; }
    else { btn.textContent = "► START AI"; btn.className = "btn power stopped"; btn.disabled = false; }
  } catch {}
}
async function toggleAi() { state.settings = await api.send("/api/settings", "PUT", { ai_enabled: !state.settings.ai_enabled }); loadAiStatus(); }
$("aiPower").addEventListener("click", toggleAi);
$("aiPill").addEventListener("click", toggleAi);
$("aiInterval").addEventListener("change", async (e) => { state.settings = await api.send("/api/settings", "PUT", { ai_interval_min: +e.target.value }); });
$("aiHoursOnly").addEventListener("change", async (e) => { state.settings = await api.send("/api/settings", "PUT", { ai_market_hours_only: e.target.checked }); });
$("aiEvent").addEventListener("change", async (e) => { state.settings = await api.send("/api/settings", "PUT", { ai_event_trigger: e.target.checked }); });
async function analyzeNow(inst, btn) {
  btn.disabled = true; const old = btn.textContent; btn.textContent = "Analyzing…";
  try { await api.send("/api/ai/analyze", "POST", { instrument: inst }); await loadAnalyses(); await loadSignals(); }
  catch (e) { alert(e.message); }
  btn.disabled = false; btn.textContent = old;
}
$("aiAnalyzeGC").addEventListener("click", (e) => analyzeNow("GC", e.target));
$("aiAnalyzeNQ").addEventListener("click", (e) => analyzeNow("NQ", e.target));
async function loadAnalyses() {
  try {
    const rows = await api.get("/api/ai/analyses?limit=25");
    const el = $("aiAnalyses");
    if (!rows.length) { el.innerHTML = `<div class="empty glass">No analyses yet.</div>`; return; }
    el.innerHTML = rows.map((a) => {
      const d = a.data || {}, sig = d.signal;
      const when = new Date(a.created_at).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
      const levels = (d.key_levels || []).map((l) => `${l.label} ${l.price}`).join(" · ");
      return `<div class="an-card glass"><div class="an-head">
        <span class="an-inst">${a.instrument}</span><span class="bias ${a.bias}">${a.bias || "?"}</span>
        <span class="badge">${(a.regime || "").replaceAll("_", " ")}</span><span class="badge">conf ${a.confidence ?? "?"}%</span>
        <span class="spacer"></span><span class="muted small">${when}</span></div>
        <div class="an-sum">${esc(a.summary || "")}</div>
        ${levels ? `<div class="an-levels">Levels: ${esc(levels)}</div>` : ""}
        ${sig ? `<div class="an-signal">◆ ${sig.direction} · entry ${sig.entry_low}–${sig.entry_high} · stop ${sig.stop} · target ${sig.target}<br>${esc(sig.reasoning || "")}</div>` : ""}
      </div>`;
    }).join("");
  } catch {}
}
function pushChat(role, content) {
  state.chat.push({ role, content });
  const div = document.createElement("div");
  div.className = "chat-msg " + role; div.textContent = content;
  if (role === "assistant") {
    const save = document.createElement("button");
    save.className = "link-btn"; save.textContent = "Save as instruction";
    save.addEventListener("click", async () => {
      await api.send("/api/ai/instructions", "POST", { text: content.slice(0, 1200) });
      save.textContent = "Saved ✓"; save.disabled = true; loadInstructions();
    });
    div.appendChild(save);
  }
  $("chatLog").appendChild(div); $("chatLog").scrollTop = 1e9;
}
$("chatSend").addEventListener("click", sendChat);
$("chatText").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendChat(); } });
async function sendChat() {
  const t = $("chatText").value.trim(); if (!t) return;
  $("chatText").value = ""; pushChat("user", t); $("chatSend").disabled = true;
  try { const r = await api.send("/api/ai/chat", "POST", { messages: state.chat }); pushChat("assistant", r.reply); }
  catch (e) { pushChat("assistant", "⚠ " + e.message); }
  $("chatSend").disabled = false;
}
async function loadInstructions() {
  try {
    const rows = await api.get("/api/ai/instructions");
    $("instrList").innerHTML = rows.map((r) => `
      <li class="${r.active ? "" : "off"}"><input type="checkbox" data-tgl="${r.id}" ${r.active ? "checked" : ""}>
      <span class="txt">${esc(r.text)}</span><button class="link-btn" data-del="${r.id}">delete</button></li>`).join("")
      || `<li class="off"><span class="txt">No instructions yet — AI runs on the base playbook.</span></li>`;
    $("instrList").querySelectorAll("[data-del]").forEach((b) => b.addEventListener("click", async () => { await fetch(`/api/ai/instructions/${b.dataset.del}`, { method: "DELETE" }); loadInstructions(); }));
    $("instrList").querySelectorAll("[data-tgl]").forEach((b) => b.addEventListener("change", async () => { await api.send(`/api/ai/instructions/${b.dataset.tgl}`, "PUT", { active: b.checked }); loadInstructions(); }));
  } catch {}
}
$("instrAdd").addEventListener("click", async () => {
  const t = $("instrText").value.trim(); if (!t) return;
  await api.send("/api/ai/instructions", "POST", { text: t }); $("instrText").value = ""; loadInstructions();
});

/* ---------- strategies ---------- */
const STRATS = [
  ["STACKED_IMBALANCE", "Stacked Aggressive Imbalances", "3+ consecutive diagonal footprint imbalances (≥3:1) near a key volume zone → continuation. Stop beyond zone, target front-runs next LVN."],
  ["ABSORPTION", "Absorption at Key Levels", "Heavy passive volume at extreme / VAH / VAL / HVN with ≤2 ticks progress + delta agreement → fade to nearest LVN."],
  ["DELTA_DIVERGENCE", "Delta Divergence", "New session extreme with diverging CVD → trapped traders, reversal with tight stop."],
  ["FV_TREND", "FV Trend Model", "Out-of-balance leg → pullback into leg LVN with renewed aggression → stop beyond print +2 ticks, target leg extreme."],
  ["FV_MEAN_REVERSION", "FV Mean Reversion", "Failed breakout beyond value reclaims → target balance POC. Never widen the stop."],
  ["AI_ANALYST", "AI Analyst", "Reads live engine snapshots, candles, options positioning, world monitor and your standing instructions. Proposes setups only on full alignment.", true],
];
function renderStrategyCards() {
  const enabled = String(state.settings.enabled_strategies || "").split(",");
  $("strategyCards").innerHTML = STRATS.map(([id, name, desc, ai]) => `
    <div class="strat-card glass ${ai ? "ai-card" : ""}"><h3>${name}</h3><p>${desc}</p>
    <label class="chk"><input type="checkbox" data-strat="${id}" ${enabled.includes(id) ? "checked" : ""}> Active</label></div>`).join("");
  document.querySelectorAll("[data-strat]").forEach((b) => b.addEventListener("change", async () => {
    const on = new Set(String(state.settings.enabled_strategies || "").split(",").filter(Boolean));
    b.checked ? on.add(b.dataset.strat) : on.delete(b.dataset.strat);
    state.settings = await api.send("/api/settings", "PUT", { enabled_strategies: [...on].join(",") });
  }));
}
function renderStatsTable(rows) {
  const tb = document.querySelector("#statsTable tbody"); if (!tb) return;
  tb.innerHTML = rows.map((r) => `<tr><td>${r.strategy_id}</td><td>${r.instrument}</td><td>${r.total}</td>
    <td class="pos">${r.wins || 0}</td><td class="neg">${r.losses || 0}</td><td>${r.be || 0}</td>
    <td>${r.win_rate == null ? "—" : r.win_rate + "%"}</td><td>${(r.avg_rr || 0).toFixed(2)}</td></tr>`).join("")
    || `<tr><td colspan="8" class="muted">No outcomes logged yet.</td></tr>`;
}
async function loadStats() { try { const s = await api.get("/api/stats"); renderStatsTable(s.strategies); } catch {} }

/* ---------- news & world ---------- */
async function loadNews() {
  try {
    const cal = await api.get("/api/calendar"); const now = Date.now();
    document.querySelector("#calTable tbody").innerHTML = cal.slice(0, 60).map((e) => `
      <tr class="${e.ts < now - 1800000 ? "past" : ""}">
      <td>${new Date(e.ts).toLocaleString(undefined, { weekday: "short", hour: "2-digit", minute: "2-digit" })}</td>
      <td>${e.country}</td><td><span class="impact ${e.impact}">${e.impact}</span></td>
      <td>${esc(e.title)}</td><td>${e.forecast || ""}</td><td>${e.previous || ""}</td></tr>`).join("");
    const news = await api.get("/api/news");
    $("headlines").innerHTML = news.slice(0, 40).map((n) => `<li><a href="${n.link}" target="_blank" rel="noopener">${esc(n.title)}</a><span class="src">${n.source} · ${n.published || ""}</span></li>`).join("");
    $("blockMins").textContent = state.settings.news_block_minutes ?? 10;
  } catch {}
}
async function loadWorld() {
  try {
    const w = await api.get("/api/world");
    $("worldGrid").innerHTML = w.map((t) => `<div class="w-tile glass">
      <div class="w-code">${t.code}</div><div class="w-label">${t.label}</div>
      <div class="w-price">${t.price == null ? "—" : Number(t.price).toLocaleString(undefined, { maximumFractionDigits: 2 })}</div>
      <div class="w-chg ${t.change_pct > 0 ? "pos" : t.change_pct < 0 ? "neg" : ""}">${t.change_pct == null ? "" : (t.change_pct > 0 ? "+" : "") + t.change_pct + "%"}</div></div>`).join("");
  } catch {}
}

/* ---------- settings ---------- */
async function loadSettings() {
  state.settings = await api.get("/api/settings");
  const s = state.settings;
  $("sAccount").value = s.account_size; $("sRisk").value = s.risk_pct;
  $("sDaily").value = s.daily_loss_limit; $("sMinRR").value = s.min_rr;
  $("sNews").value = s.news_block_minutes;
  $("sUnconfirmed").checked = !!s.show_unconfirmed;
  const inst = String(s.enabled_instruments || "").split(",");
  $("sInstGC").checked = inst.includes("GC"); $("sInstNQ").checked = inst.includes("NQ");
  const tfs = String(s.enabled_timeframes || "").split(",");
  [["sTf500T","500T"],["sTf5M","5M"],["sTf15","15M"],["sTf1H","1H"],["sTf4H","4H"],["sTf1D","1D"]]
    .forEach(([id, v]) => $(id).checked = tfs.includes(v));
  $("sAiEnabled").checked = !!s.ai_enabled; $("sAiInterval").value = s.ai_interval_min;
  $("sAiHours").checked = !!s.ai_market_hours_only; $("sAiEvent").checked = !!s.ai_event_trigger;
  $("aiInterval").value = s.ai_interval_min; $("aiHoursOnly").checked = !!s.ai_market_hours_only;
  $("aiEvent").checked = !!s.ai_event_trigger;
  $("sFlatOn").checked = !!s.flat_guard_enabled; $("sFlatTime").value = s.flat_time_et || "16:45";
  $("sFlatMin").value = s.flat_block_minutes ?? 10;
  renderStrategyCards();
}
$("saveSettings").addEventListener("click", async () => {
  const inst = [$("sInstGC").checked && "GC", $("sInstNQ").checked && "NQ"].filter(Boolean).join(",");
  const tfs = [["sTf500T","500T"],["sTf5M","5M"],["sTf15","15M"],["sTf1H","1H"],["sTf4H","4H"],["sTf1D","1D"]]
    .filter(([id]) => $(id).checked).map(([, v]) => v).join(",");
  try {
    state.settings = await api.send("/api/settings", "PUT", {
      account_size: +$("sAccount").value, risk_pct: +$("sRisk").value,
      daily_loss_limit: +$("sDaily").value, min_rr: +$("sMinRR").value,
      news_block_minutes: +$("sNews").value, show_unconfirmed: $("sUnconfirmed").checked,
      enabled_instruments: inst, enabled_timeframes: tfs,
      ai_enabled: $("sAiEnabled").checked, ai_interval_min: +$("sAiInterval").value,
      ai_market_hours_only: $("sAiHours").checked, ai_event_trigger: $("sAiEvent").checked,
      flat_guard_enabled: $("sFlatOn").checked, flat_time_et: $("sFlatTime").value,
      flat_block_minutes: +$("sFlatMin").value,
    });
    $("saveNote").textContent = "Saved ✓"; setTimeout(() => ($("saveNote").textContent = ""), 2500);
    loadAiStatus(); renderStrategyCards();
  } catch (e) { $("saveNote").textContent = "⚠ " + e.message; }
});

/* ---------- wiring ---------- */
seg($("segInstrument"), (v) => { state.instrument = v; loadCandles(); loadSignals(); pollSnapshot(); });
seg($("segInterval"), (v) => { state.tf = v; loadCandles(); renderSignals(); });
seg($("segSource"), (v) => { state.source = v; renderSignals(); });
seg($("segOptInst"), (v) => { state.optInst = v; loadOptions(); });
$("fUnconfirmed").addEventListener("change", renderSignals);
$("fSuppressed").addEventListener("change", renderSignals);

async function boot() {
  renderSessions();
  await loadSettings().catch(() => {});
  await Promise.all([loadCandles(), loadSignals(), pollHealth(), pollStats(), loadAiStatus(), pollSnapshot()]);
  setInterval(pollHealth, 15000);
  setInterval(() => { loadSignals(); pollStats(); pollSnapshot(); }, 20000);
  setInterval(() => { loadAiStatus(); if (!$("tab-ai").hidden) loadAnalyses(); }, 30000);
  setInterval(loadCandles, 60000);
  setInterval(renderSessions, 60000);
  setInterval(() => { if (!$("tab-world").hidden) loadWorld(); }, 60000);
  setInterval(() => { if (!$("tab-news").hidden) loadNews(); }, 300000);
  setInterval(() => { if (!$("tab-options").hidden) loadOptions(); }, 600000);
}
boot();
