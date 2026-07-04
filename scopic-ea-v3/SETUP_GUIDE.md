# SCOPIC EA — Complete Setup Guide

End-to-end setup for the full system:

```
MotiveWave (Rithmic data, your PC)                 Railway (cloud, ~$5/mo)
┌──────────────────────────────┐    HTTPS    ┌───────────────────────────────┐
│  Scopic EA Engine study      │──signals───▶│  FastAPI backend + SQLite     │
│  · GC 5M chart (or 500T)     │──snapshots─▶│  · filters, sizing, history   │
│  · NQ 5M chart (or 500T)     │  (each bar) │  · AI Analyst (DeepSeek)      │
└──────────────────────────────┘             │  · Web dashboard              │
                                             └───────────────────────────────┘
                                                       ▲
                                              you, from any browser
```

Files you need: `scopic-ea-engine-v1.2.zip` (MotiveWave engine) and `scopic-ea-v3.zip` (backend + dashboard).

---

## Part 0 — Prerequisites (15 min, one-time)

1. **MotiveWave** with the Order Flow / Volume Imprint capability, connected to
   Rithmic, tick data working. Quick check: add the built-in *Volume Imprint*
   study to a GC chart — if footprint cells show numbers, you're good.
2. **JDK 17+** on the machine running MotiveWave (needed once, to compile the study):
   - Windows: download Temurin JDK from `adoptium.net`, install with defaults,
     tick "Set JAVA_HOME" / "Add to PATH" if offered.
   - Verify: open a new Command Prompt → `javac -version` → should print a version.
3. **MotiveWave SDK**: motivewave.com → Support → SDK → download and unzip.
   Note the path to `mwave_sdk.jar` inside it.
4. **Accounts**: a Railway account (railway.app) and a DeepSeek account
   (platform.deepseek.com) — top up ~$5 of credit and create an API key
   (starts with `sk-`). Keep it somewhere safe; you'll paste it once.

---

## Part A — Build & install the MotiveWave engine (10 min)

1. Unzip `scopic-ea-engine-v1.2.zip` somewhere permanent, e.g. `C:\trading\scopic-ea\`.
2. Open Command Prompt in that folder and build:
   ```
   build.bat "C:\path\to\mwave_sdk.jar"
   ```
   (macOS/Linux: `./build.sh /path/to/mwave_sdk.jar`)
   Success = `Built: OrderFlowSignalEngine.jar`.
   > If the compiler errors on `forEachTick` or `isAskTick`, your SDK version has
   > slightly different names — send me the exact error and I'll patch it.
3. Copy `OrderFlowSignalEngine.jar` into your extensions folder:
   - Windows: `C:\Users\<you>\MotiveWave Extensions\`
   - macOS: `~/MotiveWave Extensions/`
4. Restart MotiveWave.

Don't configure the study yet — the backend URL doesn't exist until Part B.

---

## Part B — Deploy the backend + dashboard to Railway (15 min)

1. Unzip `scopic-ea-v3.zip`. Push the folder to a private GitHub repo
   (or use Railway's CLI/upload — GitHub is easiest for future updates).
2. Railway → **New Project → Deploy from GitHub repo** → pick the repo.
3. In the service → **Settings → Volumes → Add Volume**, mount path `/data`
   (this keeps your signal history across redeploys).
4. **Variables** — add these:
   | Variable | Value |
   |---|---|
   | `ENGINE_SECRET` | any long random string (30+ chars). You'll paste the same string into MotiveWave. |
   | `DB_PATH` | `/data/signals.db` |
   | `DISPLAY_TZ` | `Asia/Kolkata` |
   | `AI_BASE_URL` | `https://api.deepseek.com` |
   | `AI_MODEL` | `deepseek-chat` |
   | `AI_API_KEY` | your `sk-…` key from DeepSeek |
5. **Settings → Networking → Generate Domain**. Note it, e.g.
   `https://of-terminal-production.up.railway.app`.
6. Open that URL in a browser — the dashboard should load. Header shows
   **live** on the connection dot and **AI ON** (or AI OFF) on the pill.

---

## Part C — Connect MotiveWave to the backend (5 min)

Do this twice: once on a **GC** chart, once on an **NQ** chart.

1. Open a **5-minute** chart (recommended; a 500-tick chart also works) of the
   **front-month contract** (e.g. GCQ6 / NQU6). The engine internally builds
   15M/1H/4H/1D by clock time from the base bars.
2. Study → **Custom Studies → Scopic EA - Order Flow Signal Engine**.
3. In the study dialog:
   - **General → Webhook URL**: `https://<your-domain>.up.railway.app/webhook/signal`
   - **General → Shared Secret**: the exact `ENGINE_SECRET` value
   - **Stream Order Flow Snapshots**: leave ON, min interval 300s — footprint/
     CVD/profile data reaches the AI every 5 minutes, plus instantly on notable
     events (regime flip, full imbalance stack, 2× delta), which trigger an
     immediate AI analysis.
   - **Intrabar Evaluation**: ON by default — the forming bar is checked every
     5 seconds; those signals arrive tagged INTRABAR (unconfirmed).
   - **Strategies / Thresholds tabs**: defaults match the spec (3:1 ratio,
     20-lot cells, 3 stacked levels…). Tune later, after you've watched it live.
4. Keep both charts open whenever you want signals — the engine lives inside
   MotiveWave. Minimized is fine; closed is not.

---

## Part D — Verify the pipeline (5 min)

Work through this checklist in order; each step isolates one link.

1. `https://<domain>/api/health` → `{"ok": true, …}` — backend alive.
2. Wait ~5 minutes with a MW chart open, then
   `https://<domain>/api/snapshots?instrument=GC` — should show a JSON snapshot
   with `regime`, `profile`, `cvd_slope`. **This proves MW → backend works.**
3. Dashboard → **AI Analyst** tab → **Analyze GC now** → an analysis card should
   appear within ~30 s. **This proves backend → DeepSeek works.**
4. Press **► START AI** — the header pill lights green (**AI ON**); the loop runs
   every 5 min during market hours plus event-triggered runs on notable snapshots.
5. Open the **Options Flow** tab — GEX/vanna/IV widgets should populate within
   ~30 s from the free proxy chains (GLD/QQQ). This layer needs no setup.
6. Engine signals appear whenever conditions actually trigger — that can take
   hours or days depending on the market. Snapshots arriving (step 2) is the
   proof the engine is watching.

---

## Part E — Daily operation

- **Start of day**: MotiveWave open with both charts; glance at the dashboard
  header — connection **live**, AI pill state as desired, FV meter empty.
- **On each signal**: chart marker (gold ▲▼ = engine, blue ◆ = AI) + detailed
  card below with entry/stop/target and per-contract sizing for full & micro.
- **Log outcomes** (Win / Loss / BE / Skipped) on every card you acted on — this
  drives the FV daily-loss standdown and the Strategies win-rate table. The
  system is only as honest as your logging.
- **Teach the AI**: AI Analyst tab → chat, or add standing instructions directly.
  Instructions persist and shape every future analysis.
- **Control spend**: the START/STOP toggle and "market hours only" switch directly
  control DeepSeek usage. At 5-min cadence, market hours only, expect ~$10–25/mo.

---

## Troubleshooting

| Symptom | Likely cause → fix |
|---|---|
| Dashboard shows **offline** | Railway service crashed/asleep → check Railway logs; confirm the domain. |
| `/api/snapshots` empty after ~5 min | Wrong Webhook URL or Secret in the study; MW blocked from network by firewall; snapshots toggle off. Check MW's Console (Help → Console) for study errors. |
| Signals never arrive but snapshots do | Normal — thresholds are strict by design. Loosen in the study's Thresholds tab (e.g. stacked levels 3→2) to test the pipe, then restore. |
| AI tab: "SET AI_API_KEY ON RAILWAY" | `AI_API_KEY` variable missing/typo → set it, redeploy. |
| AI error `402` / insufficient balance | DeepSeek credit exhausted → top up, or STOP the AI. |
| AI error `401` | Wrong API key or wrong `AI_BASE_URL` for the key's provider. |
| Study missing from Custom Studies menu | Jar not in the Extensions folder, or MW not restarted; check MW Console for load errors. |
| Build fails on SDK method names | SDK version drift — send me the compiler output. |
| History wiped after redeploy | Volume not mounted at `/data`, or `DB_PATH` not pointing there. |

## Switching AI provider later (e.g. at a $50/mo budget)

Change three Railway variables, redeploy, done:
- **Gemini**: `AI_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai`, `AI_MODEL=gemini-2.5-flash` (or pro), key from AI Studio.
- **OpenAI**: `AI_BASE_URL=https://api.openai.com/v1`, `AI_MODEL=gpt-4o-mini` (or better).
- Any OpenAI-compatible gateway works the same way.

---

*The engine and AI flag order-flow conditions per your rules. Position sizing
suggestions are arithmetic, not advice — every execution decision is yours.*
