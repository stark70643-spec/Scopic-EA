"""AI Analyst: provider-agnostic (OpenAI-compatible) market analysis + chat.

Default provider: DeepSeek. Switch by changing AI_BASE_URL / AI_MODEL / AI_API_KEY.

Two modes:
  1. auto_analyze(instrument): builds a structured market packet (candles, engine
     signals, order-flow context, world monitor, upcoming events), asks the model
     for a JSON assessment, stores it, and promotes actionable ones to signals
     (strategy_id = AI_ANALYST) so they render on the chart like engine signals.
  2. chat(messages): free-form conversation. The system prompt always includes the
     strategy framework + the user's saved instructions, so anything taught via
     the instruction list immediately shapes future analyses.
"""
import json
import re
import time
from datetime import datetime, timezone

import httpx

from . import config, db, services, options_flow

SYSTEM_BASE = """You are the resident order-flow analyst on a private futures trading desk.
Markets covered: Gold futures (GC/MGC) and E-mini Nasdaq (NQ/MNQ), CME Globex.

The desk trades an order-flow playbook grounded in auction market theory and the
Fabio Valentini framework:
1. STACKED_IMBALANCE - 3+ consecutive diagonal footprint imbalances (>=3:1) near a
   key volume zone -> continuation entry; stop beyond the imbalance zone; target
   front-runs the next low-volume node (LVN).
2. ABSORPTION - heavy passive volume at a key level with price unable to progress
   (<=2 ticks) and delta agreement -> fade toward nearest LVN.
3. DELTA_DIVERGENCE - new session extreme while cumulative delta diverges ->
   reversal, tight stop beyond the swing.
4. FV_TREND - market out of balance; pullback into an impulse-leg LVN with renewed
   aggression in trend direction; stop beyond the aggressive print +2 ticks.
5. FV_MEAN_REVERSION - failed breakout beyond value that reclaims; target the
   balance POC; never widen stops.
Risk framework: 0.25-0.5% per trade, maximum 3 losses per day, structure+location+
aggression must all align or stay flat.

Ground rules:
- Reason ONLY from the data provided in the packet. Never invent prices or events.
- engine_orderflow_snapshots (footprint imbalances, CVD, volume profile, regime,
  streamed live from the trading platform every 15M bar) are AUTHORITATIVE.
  Dashboard candle data is delayed/approximate - use it for shape, not levels,
  whenever snapshots are present.
- Being flat is a valid, often correct call. Only propose a trade when structure,
  location and aggression align.
- Prices must respect tick size (GC 0.10, NQ 0.25).
- options_derived_context (GEX, vanna, IV, OI from delayed PROXY chains: QQQ for
  NQ, GLD for GC) is POSITIONING CONTEXT ONLY - never an entry trigger. OI is
  prior-day settle. Weight the zero_dte_slice LOW in the afternoon session.
- You are an analyst, not an executor. The trader makes all decisions.
"""

ANALYSIS_TASK = """TASK: Assess the current state of {instrument} from the packet below.

Respond with ONLY a JSON object, no markdown fences, no commentary:
{{
  "bias": "BULLISH" | "BEARISH" | "NEUTRAL",
  "regime": "BALANCED" | "OUT_OF_BALANCE_UP" | "OUT_OF_BALANCE_DOWN",
  "confidence": 0-100,
  "summary": "2-4 sentences: what the market is doing and why, referencing the data",
  "key_levels": [{{"price": 0.0, "label": "..."}}],
  "signal": null | {{
    "direction": "LONG" | "SHORT",
    "strategy": "which playbook setup this most resembles",
    "entry_low": 0.0, "entry_high": 0.0,
    "stop": 0.0, "target": 0.0,
    "stop_reason": "...", "target_reason": "...",
    "reasoning": "2-3 sentences"
  }}
}}
Set "signal" to null unless the setup genuinely qualifies. Do not force trades."""


# ---------------- provider client ----------------

async def _chat_completion(messages, temperature=0.3, max_tokens=1200):
    if not config.AI_API_KEY:
        raise RuntimeError("AI_API_KEY not set")
    url = f"{config.AI_BASE_URL}/chat/completions"
    # DeepSeek's base URL works with or without /v1; normalize common case
    if "deepseek.com" in url and "/v1/" not in url:
        url = url.replace("/chat/completions", "/v1/chat/completions")
    async with httpx.AsyncClient(timeout=90) as c:
        r = await c.post(
            url,
            headers={"Authorization": f"Bearer {config.AI_API_KEY}"},
            json={
                "model": config.AI_MODEL,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
        )
        r.raise_for_status()
        j = r.json()
    return j["choices"][0]["message"]["content"], j.get("usage", {})


def _system_prompt():
    parts = [SYSTEM_BASE]
    instructions = db.list_instructions(active_only=True)
    if instructions:
        parts.append("TRADER'S STANDING INSTRUCTIONS (always follow; added by the trader):")
        for i, ins in enumerate(instructions, 1):
            parts.append(f"{i}. {ins['text']}")
    return "\n".join(parts)


# ---------------- market packet ----------------

def _slim_candles(candles, n):
    out = []
    for c in candles[-n:]:
        out.append([c["t"], round(c["o"], 2), round(c["h"], 2),
                    round(c["l"], 2), round(c["c"], 2), c["v"]])
    return out


async def build_packet(instrument: str) -> dict:
    candles15 = await services.get_candles(instrument, "15m", "5d")
    candles1h = await services.get_candles(instrument, "60m", "1mo")
    world = await services.get_world()
    cal = await services.get_calendar()

    now_ms = int(time.time() * 1000)
    upcoming = [
        {"title": e["title"], "impact": e["impact"], "country": e["country"],
         "minutes_away": round((e["ts"] - now_ms) / 60000)}
        for e in cal
        if e["impact"] in ("High", "Medium") and 0 <= e["ts"] - now_ms <= 12 * 3600_000
    ][:8]

    recent = db.list_signals(limit=8, instrument=instrument)
    engine_signals = []
    latest_of = None
    for r in recent:
        p = r["payload"]
        engine_signals.append({
            "age_min": round((now_ms - r["created_at"]) / 60000),
            "strategy": p.get("strategy_id"), "direction": p.get("direction"),
            "timeframe": p.get("timeframe"), "entry": p.get("entry_low"),
            "stop": p.get("stop"), "target": p.get("target"),
            "outcome": r.get("outcome") or "open/unlogged",
        })
        if latest_of is None and p.get("strategy_id") != "AI_ANALYST":
            of = p.get("orderflow") or {}
            latest_of = {
                "age_min": round((now_ms - r["created_at"]) / 60000),
                "cum_session_delta": of.get("cum_session_delta"),
                "cvd_slope": of.get("cvd_slope"),
                "poc": of.get("poc"), "vah": of.get("vah"), "val": of.get("val"),
                "vwap": of.get("vwap"), "session_high": of.get("session_high"),
                "session_low": of.get("session_low"), "regime": p.get("regime"),
            }

    snapshots = db.latest_snapshots(instrument, limit=6)

    fut_price = None
    if snapshots:
        fut_price = (snapshots[0].get("bar") or {}).get("c")
    elif candles15:
        fut_price = candles15[-1]["c"]
    options_ctx = options_flow.compact_for_ai(await options_flow.compute(instrument, fut_price))

    return {
        "instrument": instrument,
        "utc_time": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        "candles_15m_last30": _slim_candles(candles15, 30),
        "candles_1h_last24": _slim_candles(candles1h, 24),
        "engine_orderflow_snapshots_newest_first": snapshots or
            "none received - MotiveWave engine offline; rely on candles only and lower confidence",
        "engine_orderflow_context_from_last_signal": latest_of or "no engine signal recently",
        "options_derived_context": options_ctx,
        "recent_signals": engine_signals,
        "world_monitor": [
            {"code": w["code"], "price": w["price"], "chg_pct": w["change_pct"]}
            for w in world if w["price"] is not None
        ],
        "upcoming_events_12h": upcoming,
        "candle_format": "[epoch_ms, open, high, low, close, volume]",
    }


# ---------------- auto analysis ----------------

def _extract_json(text: str):
    text = text.strip()
    text = re.sub(r"^```(json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON in model reply")
    return json.loads(text[start:end + 1])


async def auto_analyze(instrument: str) -> dict:
    packet = await build_packet(instrument)
    messages = [
        {"role": "system", "content": _system_prompt()},
        {"role": "user", "content": ANALYSIS_TASK.format(instrument=instrument)
                                    + "\n\nPACKET:\n" + json.dumps(packet)},
    ]
    raw, usage = await _chat_completion(messages, temperature=0.2, max_tokens=900)
    parsed = _extract_json(raw)

    analysis_id = db.insert_analysis(instrument, parsed, config.AI_MODEL, usage)

    sig = parsed.get("signal")
    signal_id = None
    if isinstance(sig, dict) and sig.get("direction") in ("LONG", "SHORT"):
        payload = {
            "engine_version": "ai-analyst",
            "instrument": instrument,
            "symbol_raw": instrument,
            "strategy_id": "AI_ANALYST",
            "strategy_name": f"AI Analyst ({sig.get('strategy', 'discretionary')})",
            "direction": sig["direction"],
            "timeframe": "15M",
            "time": int(time.time() * 1000),
            "price": sig.get("entry_high") if sig["direction"] == "LONG" else sig.get("entry_low"),
            "entry_low": sig.get("entry_low"), "entry_high": sig.get("entry_high"),
            "stop": sig.get("stop"), "target": sig.get("target"),
            "stop_reason": sig.get("stop_reason", ""),
            "target_reason": sig.get("target_reason", ""),
            "regime": parsed.get("regime", ""),
            "htf_bias": {},
            "orderflow": {},
            "dom_note": sig.get("reasoning", ""),
        }
        from . import formatter
        rr = formatter.compute_rr(payload)
        s = db.all_settings()
        suppressed = ""
        if rr < float(s.get("min_rr", 1.0)):
            suppressed = "LOW_RR"
        signal_id = db.insert_signal(payload, rr, suppressed)

    return {"analysis_id": analysis_id, "signal_id": signal_id, "analysis": parsed}


# ---------------- chat ----------------

async def chat(user_messages: list) -> str:
    """user_messages: [{role: user|assistant, content: str}, ...] most-recent last."""
    trimmed = user_messages[-16:]
    messages = [{"role": "system", "content": _system_prompt()
                 + "\nYou are chatting with the trader. Be direct and specific. If they ask "
                   "you to remember a rule or strategy change, restate it in one crisp "
                   "sentence and tell them to press 'Save as instruction' so it persists."}]
    messages.extend(trimmed)
    reply, _ = await _chat_completion(messages, temperature=0.5, max_tokens=1000)
    return reply


# ---------------- market hours (CME Globex) ----------------

def market_open() -> bool:
    """Globex: Sun 18:00 NY through Fri 17:00 NY, daily halt 17:00-18:00 NY."""
    from zoneinfo import ZoneInfo
    ny = datetime.now(ZoneInfo("America/New_York"))
    wd, h = ny.weekday(), ny.hour  # Mon=0..Sun=6
    if wd == 5:  # Saturday
        return False
    if wd == 6:  # Sunday
        return h >= 18
    if wd == 4 and h >= 17:  # Friday after close
        return False
    if h == 17:  # daily halt
        return False
    return True


# ---------------- event-triggered analysis ----------------

_event_last = {}

async def event_analyze(instrument: str, min_gap_sec: int = 90):
    """Immediate analysis on notable snapshots, debounced per instrument."""
    import time as _t
    now = _t.time()
    if now - _event_last.get(instrument, 0) < min_gap_sec:
        return None
    _event_last[instrument] = now
    return await auto_analyze(instrument)
