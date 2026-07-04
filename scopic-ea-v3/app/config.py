"""Configuration: environment variables + futures contract specifications."""
import os

# ---- Environment ----
ENGINE_SECRET = os.getenv("ENGINE_SECRET", "")            # must match MW study "Shared Secret"
DB_PATH = os.getenv("DB_PATH", "signals.db")
DISPLAY_TZ = os.getenv("DISPLAY_TZ", "Asia/Kolkata")      # signal card timestamps
PORT = int(os.getenv("PORT", "8000"))

# ---- AI Analyst (any OpenAI-compatible provider) ----
# DeepSeek (default):  AI_BASE_URL=https://api.deepseek.com  AI_MODEL=deepseek-chat
# Gemini:   AI_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai  AI_MODEL=gemini-2.5-flash
# Groq:     AI_BASE_URL=https://api.groq.com/openai/v1      AI_MODEL=llama-3.3-70b-versatile
# OpenAI:   AI_BASE_URL=https://api.openai.com/v1           AI_MODEL=gpt-4o-mini
AI_BASE_URL = os.getenv("AI_BASE_URL", "https://api.deepseek.com").rstrip("/")
AI_API_KEY = os.getenv("AI_API_KEY", "")
AI_MODEL = os.getenv("AI_MODEL", "deepseek-chat")

# Defaults for user-editable settings (stored in DB, editable from dashboard)
DEFAULT_SETTINGS = {
    "account_size": 50000.0,          # USD
    "risk_pct": 0.5,                  # % of account risked per trade (FV: 0.25-0.5)
    "daily_loss_limit": 3,            # FV rule: stop after 3 losses
    "news_block_minutes": 10,         # suppress signals +/- N min around high-impact USD events
    "min_rr": 1.0,                    # drop signals below this reward:risk
    "enabled_strategies": "STACKED_IMBALANCE,ABSORPTION,DELTA_DIVERGENCE,FV_TREND,FV_MEAN_REVERSION,AI_ANALYST",
    "enabled_timeframes": "500T,5M,15M,1H,4H,1D",
    "enabled_instruments": "GC,NQ",
    "ai_enabled": True,
    "ai_interval_min": 5,             # auto-analysis cadence
    "ai_market_hours_only": True,     # skip weekends + daily 17:00-18:00 NY halt
    "ai_event_trigger": True,         # analyze instantly on notable snapshots
    "show_unconfirmed": True,         # show intrabar (unconfirmed) signals
    "flat_guard_enabled": True,       # suppress signals near forced flat time
    "flat_time_et": "16:45",          # verify in your Lucid dashboard
    "flat_block_minutes": 10,
}

# ---- Contract specs ----
# tick: minimum price increment; tick_value: USD per tick per contract
CONTRACTS = {
    "GC": {
        "name": "Gold Futures",
        "tick": 0.10, "tick_value": 10.00,
        "micro": {"symbol": "MGC", "name": "Micro Gold", "tick": 0.10, "tick_value": 1.00},
        "yahoo": "GC=F",
    },
    "NQ": {
        "name": "E-mini Nasdaq-100",
        "tick": 0.25, "tick_value": 5.00,
        "micro": {"symbol": "MNQ", "name": "Micro E-mini Nasdaq", "tick": 0.25, "tick_value": 0.50},
        "yahoo": "NQ=F",
    },
}

# ---- World monitor symbols (Yahoo Finance) ----
WORLD_SYMBOLS = [
    ("DXY",     "DX-Y.NYB", "US Dollar Index"),
    ("US10Y",   "^TNX",     "US 10Y Yield"),
    ("ES",      "ES=F",     "S&P 500 Fut"),
    ("NQ",      "NQ=F",     "Nasdaq Fut"),
    ("GC",      "GC=F",     "Gold Fut"),
    ("SI",      "SI=F",     "Silver Fut"),
    ("VIX",     "^VIX",     "Volatility Index"),
    ("NIKKEI",  "^N225",    "Nikkei 225"),
    ("DAX",     "^GDAXI",   "DAX"),
    ("FTSE",    "^FTSE",    "FTSE 100"),
    ("EURUSD",  "EURUSD=X", "EUR/USD"),
    ("BTC",     "BTC-USD",  "Bitcoin"),
]

# ---- Free news sources ----
FF_CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"  # ForexFactory weekly calendar
RSS_FEEDS = [
    ("Investing.com Commodities", "https://www.investing.com/rss/news_11.rss"),
    ("Investing.com Indices",     "https://www.investing.com/rss/news_25.rss"),
    ("FXStreet News",             "https://www.fxstreet.com/rss/news"),
    ("MarketWatch Top",           "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
]
