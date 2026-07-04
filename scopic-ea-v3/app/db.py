"""SQLite storage: signals, outcomes, settings. Thread-safe via a single lock."""
import json
import sqlite3
import threading
import time
from . import config

_lock = threading.Lock()
_conn = None


def init():
    global _conn
    _conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    with _lock:
        _conn.executescript("""
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at INTEGER NOT NULL,
            instrument TEXT, timeframe TEXT, strategy_id TEXT, direction TEXT,
            entry_low REAL, entry_high REAL, stop REAL, target REAL, rr REAL,
            regime TEXT, payload TEXT,
            suppressed TEXT DEFAULT '',      -- '' | NEWS | DAILY_LOSS_LIMIT | LOW_RR | FILTERED
            outcome TEXT DEFAULT '',         -- '' | WIN | LOSS | BE | SKIPPED\n            pnl REAL
        );
        CREATE INDEX IF NOT EXISTS ix_signals_created ON signals(created_at);
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS ai_analyses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at INTEGER NOT NULL,
            instrument TEXT, bias TEXT, regime TEXT, confidence INTEGER,
            summary TEXT, data TEXT, model TEXT, usage TEXT
        );
        CREATE INDEX IF NOT EXISTS ix_ai_created ON ai_analyses(created_at);
        CREATE TABLE IF NOT EXISTS market_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at INTEGER NOT NULL,
            instrument TEXT, data TEXT
        );
        CREATE INDEX IF NOT EXISTS ix_snap_inst ON market_snapshots(instrument, created_at);
        CREATE TABLE IF NOT EXISTS ai_instructions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at INTEGER NOT NULL,
            text TEXT NOT NULL,
            active INTEGER DEFAULT 1
        );
        """)
        _conn.commit()
    # seed defaults
    for k, v in config.DEFAULT_SETTINGS.items():
        if get_setting(k) is None:
            set_setting(k, v)


def get_setting(key):
    with _lock:
        row = _conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    if row is None:
        return None
    try:
        return json.loads(row["value"])
    except Exception:
        return row["value"]


def set_setting(key, value):
    with _lock:
        _conn.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)),
        )
        _conn.commit()


def all_settings():
    with _lock:
        rows = _conn.execute("SELECT key,value FROM settings").fetchall()
    out = {}
    for r in rows:
        try:
            out[r["key"]] = json.loads(r["value"])
        except Exception:
            out[r["key"]] = r["value"]
    return out


def insert_signal(payload: dict, rr: float, suppressed: str) -> int:
    with _lock:
        cur = _conn.execute(
            "INSERT INTO signals(created_at,instrument,timeframe,strategy_id,direction,"
            "entry_low,entry_high,stop,target,rr,regime,payload,suppressed) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                int(time.time() * 1000),
                payload.get("instrument"), payload.get("timeframe"),
                payload.get("strategy_id"), payload.get("direction"),
                payload.get("entry_low"), payload.get("entry_high"),
                payload.get("stop"), payload.get("target"), rr,
                payload.get("regime"), json.dumps(payload), suppressed,
            ),
        )
        _conn.commit()
        return cur.lastrowid


def list_signals(limit=100, instrument=None, strategy=None):
    q = "SELECT * FROM signals"
    cond, args = [], []
    if instrument:
        cond.append("instrument=?"); args.append(instrument)
    if strategy:
        cond.append("strategy_id=?"); args.append(strategy)
    if cond:
        q += " WHERE " + " AND ".join(cond)
    q += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    with _lock:
        rows = _conn.execute(q, args).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["payload"] = json.loads(d["payload"])
        out.append(d)
    return out


def set_outcome(signal_id: int, outcome: str, pnl=None) -> bool:
    with _lock:
        cur = _conn.execute("UPDATE signals SET outcome=?, pnl=? WHERE id=?",
                            (outcome, pnl, signal_id))
        _conn.commit()
        return cur.rowcount > 0


def pnl_today(day_start_ms: int) -> float:
    with _lock:
        row = _conn.execute(
            "SELECT COALESCE(SUM(pnl),0) s FROM signals WHERE pnl IS NOT NULL AND created_at>=?",
            (day_start_ms,)).fetchone()
    return row["s"] or 0.0


def losses_today(day_start_ms: int) -> int:
    with _lock:
        row = _conn.execute(
            "SELECT COUNT(*) c FROM signals WHERE outcome='LOSS' AND created_at>=?",
            (day_start_ms,),
        ).fetchone()
    return row["c"]


def strategy_stats():
    """Per strategy x instrument: counts and win rate from logged outcomes."""
    with _lock:
        rows = _conn.execute(
            "SELECT strategy_id, instrument, "
            "COUNT(*) total, "
            "SUM(CASE WHEN outcome='WIN' THEN 1 ELSE 0 END) wins, "
            "SUM(CASE WHEN outcome='LOSS' THEN 1 ELSE 0 END) losses, "
            "SUM(CASE WHEN outcome='BE' THEN 1 ELSE 0 END) be, "
            "AVG(rr) avg_rr "
            "FROM signals WHERE suppressed='' GROUP BY strategy_id, instrument"
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        judged = (d["wins"] or 0) + (d["losses"] or 0)
        d["win_rate"] = round(100.0 * (d["wins"] or 0) / judged, 1) if judged else None
        out.append(d)
    return out


# ---------------- AI analyses ----------------

def insert_analysis(instrument: str, parsed: dict, model: str, usage: dict) -> int:
    with _lock:
        cur = _conn.execute(
            "INSERT INTO ai_analyses(created_at,instrument,bias,regime,confidence,"
            "summary,data,model,usage) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                int(time.time() * 1000), instrument,
                parsed.get("bias"), parsed.get("regime"),
                int(parsed.get("confidence") or 0),
                parsed.get("summary", ""), json.dumps(parsed),
                model, json.dumps(usage or {}),
            ),
        )
        _conn.commit()
        return cur.lastrowid


def list_analyses(limit=30, instrument=None):
    q = "SELECT * FROM ai_analyses"
    args = []
    if instrument:
        q += " WHERE instrument=?"
        args.append(instrument)
    q += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    with _lock:
        rows = _conn.execute(q, args).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["data"] = json.loads(d["data"])
        d.pop("usage", None)
        out.append(d)
    return out


# ---------------- AI instructions ----------------

def add_instruction(text: str) -> int:
    with _lock:
        cur = _conn.execute(
            "INSERT INTO ai_instructions(created_at,text) VALUES(?,?)",
            (int(time.time() * 1000), text.strip()),
        )
        _conn.commit()
        return cur.lastrowid


def list_instructions(active_only=False):
    q = "SELECT * FROM ai_instructions"
    if active_only:
        q += " WHERE active=1"
    q += " ORDER BY id ASC"
    with _lock:
        rows = _conn.execute(q).fetchall()
    return [dict(r) for r in rows]


def delete_instruction(iid: int) -> bool:
    with _lock:
        cur = _conn.execute("DELETE FROM ai_instructions WHERE id=?", (iid,))
        _conn.commit()
        return cur.rowcount > 0


def toggle_instruction(iid: int, active: bool) -> bool:
    with _lock:
        cur = _conn.execute("UPDATE ai_instructions SET active=? WHERE id=?",
                            (1 if active else 0, iid))
        _conn.commit()
        return cur.rowcount > 0


# ---------------- market snapshots (from MW engine, feeds the AI) ----------------

def insert_snapshot(instrument: str, data: dict) -> int:
    with _lock:
        cur = _conn.execute(
            "INSERT INTO market_snapshots(created_at,instrument,data) VALUES(?,?,?)",
            (int(time.time() * 1000), instrument, json.dumps(data)),
        )
        # prune anything older than 48h to keep the DB lean
        _conn.execute("DELETE FROM market_snapshots WHERE created_at < ?",
                      (int(time.time() * 1000) - 48 * 3600_000,))
        _conn.commit()
        return cur.lastrowid


def latest_snapshots(instrument: str, limit=6):
    with _lock:
        rows = _conn.execute(
            "SELECT created_at, data FROM market_snapshots WHERE instrument=? "
            "ORDER BY id DESC LIMIT ?", (instrument, limit),
        ).fetchall()
    out = []
    for r in rows:
        d = json.loads(r["data"])
        d["age_min"] = round((time.time() * 1000 - r["created_at"]) / 60000)
        out.append(d)
    return out
