"""External services: Telegram, ForexFactory calendar, RSS news, world monitor,
price history for dashboard charts. All async (httpx), all cached in memory."""
import asyncio
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
import httpx
from . import config

_UA = {"User-Agent": "Mozilla/5.0 (OrderFlowDashboard/1.0)"}

# ---------------- ForexFactory economic calendar ----------------

_calendar_cache = {"ts": 0, "data": []}

async def get_calendar(force=False):
    """Weekly calendar from ForexFactory's free JSON mirror. Cached 30 min."""
    now = time.time()
    if not force and now - _calendar_cache["ts"] < 1800 and _calendar_cache["data"]:
        return _calendar_cache["data"]
    try:
        async with httpx.AsyncClient(timeout=15, headers=_UA, follow_redirects=True) as c:
            r = await c.get(config.FF_CALENDAR_URL)
            r.raise_for_status()
            raw = r.json()
        events = []
        for e in raw:
            try:
                dt = datetime.fromisoformat(e.get("date", "").replace("Z", "+00:00"))
                events.append({
                    "title": e.get("title", ""),
                    "country": e.get("country", ""),
                    "impact": e.get("impact", ""),          # High / Medium / Low
                    "time_utc": dt.astimezone(timezone.utc).isoformat(),
                    "ts": int(dt.timestamp() * 1000),
                    "forecast": e.get("forecast", ""),
                    "previous": e.get("previous", ""),
                })
            except Exception:
                continue
        events.sort(key=lambda x: x["ts"])
        _calendar_cache.update(ts=now, data=events)
        return events
    except Exception:
        return _calendar_cache["data"]


async def news_blackout(block_minutes: int):
    """Return a note if now is within +/- block_minutes of a High-impact USD event."""
    if block_minutes <= 0:
        return None
    events = await get_calendar()
    now_ms = int(time.time() * 1000)
    win = block_minutes * 60_000
    for e in events:
        if e["impact"] == "High" and e["country"] in ("USD", "US"):
            dt = e["ts"] - now_ms
            if -win <= dt <= win:
                mins = round(dt / 60000)
                when = f"in {mins}m" if mins > 0 else (f"{-mins}m ago" if mins < 0 else "now")
                return f"High-impact USD event: {e['title']} ({when})"
    return None

# ---------------- RSS news ----------------

_news_cache = {"ts": 0, "data": []}

def _parse_rss(source: str, xml_text: str):
    items = []
    try:
        root = ET.fromstring(xml_text)
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            pub = (item.findtext("pubDate") or "").strip()
            if title:
                items.append({"source": source, "title": title, "link": link, "published": pub})
    except Exception:
        pass
    return items


async def get_news(force=False):
    now = time.time()
    if not force and now - _news_cache["ts"] < 300 and _news_cache["data"]:
        return _news_cache["data"]
    items = []
    async with httpx.AsyncClient(timeout=12, headers=_UA, follow_redirects=True) as c:
        for source, url in config.RSS_FEEDS:
            try:
                r = await c.get(url)
                if r.status_code == 200:
                    items.extend(_parse_rss(source, r.text)[:12])
            except Exception:
                continue
    if items:
        _news_cache.update(ts=now, data=items)
    return _news_cache["data"]

# ---------------- World monitor (Yahoo Finance quotes) ----------------

_world_cache = {"ts": 0, "data": []}

async def get_world(force=False):
    now = time.time()
    if not force and now - _world_cache["ts"] < 60 and _world_cache["data"]:
        return _world_cache["data"]
    out = []
    async with httpx.AsyncClient(timeout=12, headers=_UA) as c:
        for code, ysym, label in config.WORLD_SYMBOLS:
            try:
                r = await c.get(
                    f"https://query1.finance.yahoo.com/v8/finance/chart/{ysym}",
                    params={"interval": "1d", "range": "5d"},
                )
                j = r.json()["chart"]["result"][0]
                meta = j["meta"]
                price = meta.get("regularMarketPrice")
                prev = meta.get("chartPreviousClose") or meta.get("previousClose")
                chg = None
                if price is not None and prev:
                    chg = round((price - prev) / prev * 100, 2)
                out.append({"code": code, "label": label, "price": price,
                            "change_pct": chg, "symbol": ysym})
            except Exception:
                out.append({"code": code, "label": label, "price": None,
                            "change_pct": None, "symbol": ysym})
    if any(x["price"] is not None for x in out):
        _world_cache.update(ts=now, data=out)
    return _world_cache["data"] or out

# ---------------- Price history for dashboard charts ----------------

_price_cache = {}

async def get_candles(instrument: str, interval: str = "15m", range_: str = "5d"):
    """OHLC candles from Yahoo for the dashboard chart (context, not execution)."""
    spec = config.CONTRACTS.get(instrument)
    if not spec:
        return []
    key = (instrument, interval, range_)
    cached = _price_cache.get(key)
    if cached and time.time() - cached[0] < 60:
        return cached[1]
    try:
        async with httpx.AsyncClient(timeout=12, headers=_UA) as c:
            r = await c.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{spec['yahoo']}",
                params={"interval": interval, "range": range_},
            )
            j = r.json()["chart"]["result"][0]
            ts = j["timestamp"]
            q = j["indicators"]["quote"][0]
            candles = []
            for i, t in enumerate(ts):
                o, h, l, cl = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
                if None in (o, h, l, cl):
                    continue
                candles.append({"t": t * 1000, "o": o, "h": h, "l": l, "c": cl,
                                "v": (q["volume"][i] or 0)})
            _price_cache[key] = (time.time(), candles)
            return candles
    except Exception:
        return cached[1] if cached else []
