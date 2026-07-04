"""Formats engine payloads into the detailed signal card (Telegram + dashboard).

Pure functions, no I/O — unit-testable offline.
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from . import config

LINE = "-" * 50


def _fmt_price(instrument: str, p) -> str:
    if p is None:
        return "n/a"
    dec = 2 if instrument == "GC" else 2  # NQ quotes to .25, two decimals is fine
    return f"${p:,.{dec}f}"


def _bias_word(b: str) -> str:
    return {"BULLISH": "Bullish", "BEARISH": "Bearish"}.get(b, "Neutral")


def _regime_word(r: str) -> str:
    return {
        "OUT_OF_BALANCE_UP": "Out of Balance (Up)",
        "OUT_OF_BALANCE_DOWN": "Out of Balance (Down)",
        "BALANCED": "Balanced",
    }.get(r or "", "Unknown")


def compute_rr(p: dict) -> float:
    try:
        if p["direction"] == "LONG":
            risk = p["entry_high"] - p["stop"]
            reward = p["target"] - p["entry_high"]
        else:
            risk = p["stop"] - p["entry_low"]
            reward = p["entry_low"] - p["target"]
        if risk <= 0:
            return 0.0
        return round(reward / risk, 2)
    except Exception:
        return 0.0


def sizing_block(p: dict, account_size: float, risk_pct: float) -> str:
    """Suggested contracts for full-size and micro, from stop distance."""
    inst = p.get("instrument", "GC")
    spec = config.CONTRACTS.get(inst)
    if not spec:
        return ""
    if p["direction"] == "LONG":
        stop_dist = abs(p["entry_high"] - p["stop"])
    else:
        stop_dist = abs(p["stop"] - p["entry_low"])
    ticks = max(1, round(stop_dist / spec["tick"]))
    risk_usd = account_size * risk_pct / 100.0

    full_risk_per_ct = ticks * spec["tick_value"]
    micro = spec["micro"]
    micro_risk_per_ct = ticks * micro["tick_value"]

    full_n = int(risk_usd // full_risk_per_ct) if full_risk_per_ct > 0 else 0
    micro_n = int(risk_usd // micro_risk_per_ct) if micro_risk_per_ct > 0 else 0

    lines = [f"SIZING (risk ${risk_usd:,.0f} = {risk_pct}% of ${account_size:,.0f} | stop {ticks} ticks):"]
    if full_n >= 1:
        lines.append(f"- {inst}: {full_n} contract{'s' if full_n != 1 else ''} "
                     f"(${full_risk_per_ct * full_n:,.0f} at risk, ${spec['tick_value']:.2f}/tick)")
    else:
        lines.append(f"- {inst}: stop too wide for risk budget "
                     f"(1 contract risks ${full_risk_per_ct:,.0f})")
    if micro_n >= 1:
        lines.append(f"- {micro['symbol']}: {micro_n} contract{'s' if micro_n != 1 else ''} "
                     f"(${micro_risk_per_ct * micro_n:,.0f} at risk, ${micro['tick_value']:.2f}/tick)")
    else:
        lines.append(f"- {micro['symbol']}: stop too wide even for micros "
                     f"(1 contract risks ${micro_risk_per_ct:,.0f})")
    return "\n".join(lines)


def format_card(p: dict, account_size: float, risk_pct: float,
                news_note: str = "", losses_today: int = 0, daily_limit: int = 3) -> str:
    inst = p.get("instrument", "?")
    micro = config.CONTRACTS.get(inst, {}).get("micro", {}).get("symbol", "")
    direction = p.get("direction", "?")
    side = "BUY / LONG" if direction == "LONG" else "SELL / SHORT"
    of = p.get("orderflow", {}) or {}
    htf = p.get("htf_bias", {}) or {}
    rr = compute_rr(p)

    ts = p.get("time")
    when = ""
    if ts:
        dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).astimezone(ZoneInfo(config.DISPLAY_TZ))
        when = dt.strftime("%d %b %H:%M %Z")

    delta = of.get("bar_delta")
    delta_str = f"{'+' if (delta or 0) >= 0 else ''}{delta:,}" if delta is not None else "n/a"
    cvd = of.get("cum_session_delta")
    cvd_str = f"{'+' if (cvd or 0) >= 0 else ''}{cvd:,}" if cvd is not None else "n/a"

    trigger = p.get("strategy_name", p.get("strategy_id", ""))
    imb = of.get("imbalance_levels") or 0
    if imb and of.get("imbalance_ratio"):
        trigger += f" ({imb}x stacked, max {of['imbalance_ratio']}:1)"
    if of.get("absorption"):
        trigger += f" | absorption {of.get('absorption_volume', 0):,} contracts"

    vol_ctx = (of.get("volume_context") or "").replace("_", " ").title()

    lines = [
        f"[{inst}{'/' + micro if micro else ''} SIGNAL] - {side} \U0001F6A8",
        LINE,
        f"ENTRY RANGE: {_fmt_price(inst, p.get('entry_low'))} - {_fmt_price(inst, p.get('entry_high'))}",
        f"STOP LOSS: {_fmt_price(inst, p.get('stop'))} ({p.get('stop_reason', '')})",
        f"TAKE PROFIT: {_fmt_price(inst, p.get('target'))} ({p.get('target_reason', '')})",
        f"R:R {rr}  |  TF: {p.get('timeframe', '?')}"
        + ("  |  INTRABAR (unconfirmed)" if p.get("evaluation") == "INTRABAR" else "")
        + (f"  |  {when}" if when else ""),
        LINE,
        f"\U0001F4D0 HTF CONTEXT: 1D {_bias_word(htf.get('1D'))} | 4H {_bias_word(htf.get('4H'))} | 1H {_bias_word(htf.get('1H'))}",
        f"\U0001F9ED MARKET REGIME: {_regime_word(p.get('regime'))}",
        LINE,
        "\U0001F4CA ORDER FLOW DETAILS:",
        f"- Trigger: {trigger}",
        f"- Bar Delta: {delta_str}  |  Session CVD: {cvd_str} ({(of.get('cvd_slope') or 'FLAT').title()})",
        f"- Volume Context: {vol_ctx}",
    ]

    lvl = []
    for key, label in (("poc", "POC"), ("vah", "VAH"), ("val", "VAL"), ("vwap", "VWAP")):
        v = of.get(key)
        if v is not None:
            lvl.append(f"{label} {v:,.2f}")
    if lvl:
        lines.append("- Levels: " + " / ".join(lvl))
    if of.get("nearest_lvn"):
        lines.append(f"- Nearest LVN: {of['nearest_lvn']:,.2f}")
    if p.get("dom_note"):
        lines.append(f"- Tape/DOM: {p['dom_note']}")

    lines.append(LINE)
    lines.append(sizing_block(p, account_size, risk_pct))

    footers = []
    if news_note:
        footers.append(f"\u26A0\uFE0F {news_note}")
    if losses_today:
        footers.append(f"\U0001F6D1 Losses today: {losses_today}/{daily_limit} (FV daily limit)")
    if footers:
        lines.append(LINE)
        lines.extend(footers)

    lines.append("")
    lines.append("Not financial advice - conditions per your rules; execution is yours.")
    return "\n".join(lines)


def format_standdown(losses: int, limit: int) -> str:
    return (
        "\U0001F6D1 DAILY LOSS LIMIT HIT\n" + LINE +
        f"\n{losses} losses logged today (limit {limit}).\n"
        "Per the FV risk framework the engine is standing down: new signals are "
        "recorded on the dashboard but not alerted until the next session."
    )
