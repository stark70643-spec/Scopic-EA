# SCOPIC EA — Order Flow Dashboard + AI Analyst (v3)

One Railway deployment: FastAPI backend + glassy black/green/violet dashboard.
Pairs with the Scopic EA MotiveWave engine v1.2 (5M or 500-tick base chart).

## What's new in v3
- **SCOPIC EA** theme: black glass, bright-green bull / bright-violet bear candles,
  direction-colored signal markers (green ▲ long / red ▼ short; ◆ = AI).
- **Per-timeframe view**: 500T / 5M / 15M / 1H / 4H / 1D selector — chart markers
  and the signal section below both follow the selected timeframe.
- **Intrabar signals** from the engine appear tagged INTRABAR (unconfirmed),
  dimmed on the chart; toggle acceptance in Settings.
- **Options Flow tab** (free proxy chains, computed in-house): net GEX profile +
  zero-gamma flip + call/put walls (with futures-equivalent levels), vanna, IV
  term structure & skew, 0DTE slice, top OI strikes. Feeds every AI packet as
  positioning context (never an entry trigger).
- **AI event triggers**: notable snapshots (regime flip, full imbalance stack,
  2× delta) fire an immediate analysis, debounced 90s, on top of the N-minute loop.
- **$ P&L logging** on outcomes → daily P&L tracking; **flat-time guard**
  (default 16:45 ET, editable — verify in your prop dashboard).

## Deploy (Railway)
Volume at `/data`; variables: `ENGINE_SECRET`, `DB_PATH=/data/signals.db`,
`DISPLAY_TZ`, `AI_BASE_URL=https://api.deepseek.com`, `AI_MODEL=deepseek-chat`,
`AI_API_KEY=sk-…`. Engine webhook: `https://<app>.up.railway.app/webhook/signal`.

## API additions in v3
`GET /api/options?instrument=GC|NQ` · snapshot webhook now honors `notable` for
event-triggered AI · `POST /api/signals/{id}/outcome` accepts `pnl`.

Delayed dashboard candles are context only — MotiveWave is the execution view.
Signals flag conditions per your rules; not financial advice.
