"""Order Flow Signal Backend v2 — dashboard-only (no Telegram) + AI Analyst.

Pipeline:  MotiveWave engine --> POST /webhook/signal --> filters --> SQLite
           AI Analyst (DeepSeek by default) --> auto-analysis every N min +
           chat + persistent trader instructions.
Dashboard: chart-first UI served from ./static, signals render as chart markers
           with detail cards below.

Run locally:   uvicorn app.main:app --reload
Railway:       uvicorn app.main:app --host 0.0.0.0 --port $PORT
"""
import asyncio
import os
import time
from datetime import datetime, timedelta

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from . import ai, config, db, formatter, options_flow, services

app = FastAPI(title="Scopic EA Backend", version="3.0.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

_ai_task = None
_ai_status = {"last_run": None, "last_error": None, "runs": 0}


@app.on_event("startup")
async def startup():
    global _ai_task
    db.init()
    asyncio.create_task(services.get_calendar())
    asyncio.create_task(services.get_news())
    asyncio.create_task(services.get_world())
    _ai_task = asyncio.create_task(_ai_loop())


async def _ai_loop():
    """Background auto-analysis: every ai_interval_min per enabled instrument."""
    await asyncio.sleep(10)  # let caches warm
    while True:
        try:
            s = db.all_settings()
            interval = max(2, int(s.get("ai_interval_min", 5)))
            if not bool(s.get("ai_enabled", True)) or not config.AI_API_KEY:
                await asyncio.sleep(30)
                continue
            if bool(s.get("ai_market_hours_only", True)) and not ai.market_open():
                await asyncio.sleep(60)
                continue
            instruments = [i for i in str(s.get("enabled_instruments", "GC,NQ")).split(",") if i]
            for inst in instruments:
                try:
                    await ai.auto_analyze(inst)
                    _ai_status["runs"] += 1
                    _ai_status["last_error"] = None
                except Exception as e:
                    _ai_status["last_error"] = f"{inst}: {type(e).__name__}: {e}"
            _ai_status["last_run"] = int(time.time() * 1000)
            await asyncio.sleep(interval * 60)
        except asyncio.CancelledError:
            return
        except Exception as e:
            _ai_status["last_error"] = f"loop: {e}"
            await asyncio.sleep(60)


def _near_flat_time(s) -> bool:
    if not bool(s.get("flat_guard_enabled", True)):
        return False
    try:
        from zoneinfo import ZoneInfo
        hh, mm = str(s.get("flat_time_et", "16:45")).split(":")
        ny = datetime.now(ZoneInfo("America/New_York"))
        flat = ny.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
        mins = (flat - ny).total_seconds() / 60
        return 0 <= mins <= float(s.get("flat_block_minutes", 10))
    except Exception:
        return False


def _session_start_ms() -> int:
    """Futures trading day starts 18:00 New York the prior calendar day."""
    from zoneinfo import ZoneInfo
    ny = datetime.now(ZoneInfo("America/New_York"))
    start = ny.replace(hour=18, minute=0, second=0, microsecond=0)
    if ny.hour < 18:
        start -= timedelta(days=1)
    return int(start.timestamp() * 1000)


# ---------------- Webhook from MotiveWave engine ----------------

@app.post("/webhook/signal")
async def webhook_signal(request: Request, x_engine_secret: str = Header(default="")):
    if config.ENGINE_SECRET and x_engine_secret != config.ENGINE_SECRET:
        raise HTTPException(401, "bad secret")
    payload = await request.json()

    s = db.all_settings()
    suppressed = ""

    if payload.get("strategy_id") not in str(s.get("enabled_strategies", "")).split(","):
        suppressed = "FILTERED"
    elif payload.get("timeframe") not in str(s.get("enabled_timeframes", "")).split(","):
        suppressed = "FILTERED"
    elif payload.get("instrument") not in str(s.get("enabled_instruments", "")).split(","):
        suppressed = "FILTERED"

    rr = formatter.compute_rr(payload)
    if not suppressed and rr < float(s.get("min_rr", 1.0)):
        suppressed = "LOW_RR"

    if not suppressed:
        note = await services.news_blackout(int(s.get("news_block_minutes", 10)))
        if note:
            suppressed = "NEWS"

    losses = db.losses_today(_session_start_ms())
    limit = int(s.get("daily_loss_limit", 3))
    if not suppressed and losses >= limit:
        suppressed = "DAILY_LOSS_LIMIT"

    if not suppressed and _near_flat_time(s):
        suppressed = "FLAT_TIME"

    if not suppressed and payload.get("evaluation") == "INTRABAR" and not bool(s.get("show_unconfirmed", True)):
        suppressed = "FILTERED"

    sig_id = db.insert_signal(payload, rr, suppressed)
    return {"ok": True, "id": sig_id, "rr": rr, "suppressed": suppressed}


@app.post("/webhook/snapshot")
async def webhook_snapshot(request: Request, x_engine_secret: str = Header(default="")):
    if config.ENGINE_SECRET and x_engine_secret != config.ENGINE_SECRET:
        raise HTTPException(401, "bad secret")
    data = await request.json()
    inst = str(data.get("instrument", "")).upper()
    if inst not in config.CONTRACTS:
        raise HTTPException(400, "unknown instrument")
    sid = db.insert_snapshot(inst, data)
    s = db.all_settings()
    if (data.get("notable") and bool(s.get("ai_event_trigger", True))
            and bool(s.get("ai_enabled", True)) and config.AI_API_KEY):
        asyncio.create_task(_safe_event_analyze(inst))
    return {"ok": True, "id": sid}


async def _safe_event_analyze(inst: str):
    try:
        await ai.event_analyze(inst)
        _ai_status["runs"] += 1
    except Exception as e:
        _ai_status["last_error"] = f"event {inst}: {e}"


@app.get("/api/snapshots")
async def api_snapshots(instrument: str = "GC", limit: int = 6):
    return db.latest_snapshots(instrument, limit=limit)


# ---------------- Dashboard API ----------------

@app.get("/api/signals")
async def api_signals(limit: int = 100, instrument: str = None, strategy: str = None,
                      since: int = None):
    rows = db.list_signals(limit=limit, instrument=instrument, strategy=strategy)
    if since:
        rows = [r for r in rows if r["created_at"] >= since]
    s = db.all_settings()
    for r in rows:
        r["card"] = formatter.format_card(
            r["payload"],
            account_size=float(s.get("account_size", 50000)),
            risk_pct=float(s.get("risk_pct", 0.5)),
        )
    return rows


@app.post("/api/signals/{signal_id}/outcome")
async def api_outcome(signal_id: int, body: dict):
    outcome = str(body.get("outcome", "")).upper()
    if outcome not in ("WIN", "LOSS", "BE", "SKIPPED", ""):
        raise HTTPException(400, "outcome must be WIN/LOSS/BE/SKIPPED")
    pnl = body.get("pnl")
    try:
        pnl = float(pnl) if pnl not in (None, "") else None
    except (TypeError, ValueError):
        pnl = None
    if not db.set_outcome(signal_id, outcome, pnl):
        raise HTTPException(404, "signal not found")
    losses = db.losses_today(_session_start_ms())
    return {"ok": True, "losses_today": losses, "pnl_today": db.pnl_today(_session_start_ms()),
            "limit": int(db.all_settings().get("daily_loss_limit", 3))}


@app.get("/api/settings")
async def api_get_settings():
    return db.all_settings()


@app.put("/api/settings")
async def api_put_settings(body: dict):
    allowed = set(config.DEFAULT_SETTINGS.keys())
    for k, v in body.items():
        if k in allowed:
            db.set_setting(k, v)
    return db.all_settings()


@app.get("/api/stats")
async def api_stats():
    return {
        "strategies": db.strategy_stats(),
        "losses_today": db.losses_today(_session_start_ms()),
        "pnl_today": db.pnl_today(_session_start_ms()),
        "daily_limit": int(db.all_settings().get("daily_loss_limit", 3)),
    }


@app.get("/api/calendar")
async def api_calendar():
    return await services.get_calendar()


@app.get("/api/news")
async def api_news():
    return await services.get_news()


@app.get("/api/world")
async def api_world():
    return await services.get_world()


@app.get("/api/candles")
async def api_candles(instrument: str = "GC", interval: str = "15m", range: str = "5d"):
    return await services.get_candles(instrument, interval, range)


# ---------------- AI Analyst API ----------------

@app.get("/api/ai/status")
async def ai_status():
    return {
        "configured": bool(config.AI_API_KEY),
        "model": config.AI_MODEL,
        "base_url": config.AI_BASE_URL,
        "market_open": ai.market_open(),
        **_ai_status,
    }


@app.get("/api/ai/analyses")
async def ai_analyses(limit: int = 30, instrument: str = None):
    return db.list_analyses(limit=limit, instrument=instrument)


@app.post("/api/ai/analyze")
async def ai_analyze_now(body: dict):
    inst = str(body.get("instrument", "GC")).upper()
    if inst not in config.CONTRACTS:
        raise HTTPException(400, "instrument must be GC or NQ")
    if not config.AI_API_KEY:
        raise HTTPException(400, "AI_API_KEY not configured")
    try:
        return await ai.auto_analyze(inst)
    except Exception as e:
        raise HTTPException(502, f"AI provider error: {e}")


@app.post("/api/ai/chat")
async def ai_chat(body: dict):
    msgs = body.get("messages", [])
    if not msgs:
        raise HTTPException(400, "messages required")
    if not config.AI_API_KEY:
        raise HTTPException(400, "AI_API_KEY not configured")
    clean = [{"role": m.get("role"), "content": str(m.get("content", ""))[:8000]}
             for m in msgs if m.get("role") in ("user", "assistant")]
    try:
        reply = await ai.chat(clean)
        return {"reply": reply}
    except Exception as e:
        raise HTTPException(502, f"AI provider error: {e}")


@app.get("/api/ai/instructions")
async def ai_get_instructions():
    return db.list_instructions()


@app.post("/api/ai/instructions")
async def ai_add_instruction(body: dict):
    text = str(body.get("text", "")).strip()
    if not text:
        raise HTTPException(400, "text required")
    iid = db.add_instruction(text)
    return {"ok": True, "id": iid}


@app.delete("/api/ai/instructions/{iid}")
async def ai_del_instruction(iid: int):
    if not db.delete_instruction(iid):
        raise HTTPException(404, "not found")
    return {"ok": True}


@app.put("/api/ai/instructions/{iid}")
async def ai_toggle_instruction(iid: int, body: dict):
    if not db.toggle_instruction(iid, bool(body.get("active", True))):
        raise HTTPException(404, "not found")
    return {"ok": True}


@app.get("/api/options")
async def api_options(instrument: str = "GC"):
    inst = instrument.upper()
    if inst not in config.CONTRACTS:
        raise HTTPException(400, "instrument must be GC or NQ")
    snaps = db.latest_snapshots(inst, limit=1)
    fut = (snaps[0].get("bar") or {}).get("c") if snaps else None
    if not fut:
        candles = await services.get_candles(inst, "15m", "1d")
        fut = candles[-1]["c"] if candles else None
    return await options_flow.compute(inst, fut)


@app.get("/api/health")
async def health():
    return {"ok": True, "time": int(time.time() * 1000)}


# ---------------- Static dashboard ----------------

STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
if os.path.isdir(STATIC_DIR):
    app.mount("/assets", StaticFiles(directory=STATIC_DIR), name="assets")

    @app.get("/")
    async def index():
        idx = os.path.join(STATIC_DIR, "index.html")
        if os.path.isfile(idx):
            return FileResponse(idx)
        return {"service": "order-flow-backend", "dashboard": "missing static/index.html"}
