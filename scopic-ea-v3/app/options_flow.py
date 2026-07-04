"""Options-derived analytics from FREE proxy chains (QQQ for NQ, GLD for GC).

Computes in-house from Yahoo option chains:
  - Net GEX profile by strike, zero-gamma flip, call/put gamma walls
  - Net vanna exposure by strike
  - IV: ATM, term structure, put-call skew
  - 0DTE/nearest-expiry slice
  - OI concentration
All levels are also translated to futures-equivalent prices via the live
proxy/futures ratio. Everything is stamped with data age.

IMPORTANT (also stated to the AI): this is positioning CONTEXT derived from
delayed proxy data and once-daily OI settles. Never an entry trigger.
"""
import math
import time
from datetime import datetime, timezone

import httpx

from . import config

PROXY = {"GC": "GLD", "NQ": "QQQ"}
_UA = {"User-Agent": "Mozilla/5.0 (ScopicEA/1.0)"}
_cache = {}   # instrument -> (ts, summary)

CACHE_SEC = 600
RISK_FREE = 0.04


# ---------------- Black-Scholes greeks ----------------

def _norm_pdf(x):
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def _d1(S, K, T, r, iv):
    return (math.log(S / K) + (r + 0.5 * iv * iv) * T) / (iv * math.sqrt(T))


def gamma(S, K, T, r, iv):
    if T <= 0 or iv <= 0 or S <= 0 or K <= 0:
        return 0.0
    try:
        return _norm_pdf(_d1(S, K, T, r, iv)) / (S * iv * math.sqrt(T))
    except (ValueError, ZeroDivisionError, OverflowError):
        return 0.0


def vanna(S, K, T, r, iv):
    """dDelta/dVol per 1.0 vol; sign matters more than magnitude here."""
    if T <= 0 or iv <= 0 or S <= 0 or K <= 0:
        return 0.0
    try:
        d1 = _d1(S, K, T, r, iv)
        d2 = d1 - iv * math.sqrt(T)
        return -_norm_pdf(d1) * d2 / iv
    except (ValueError, ZeroDivisionError, OverflowError):
        return 0.0


# ---------------- chain fetch ----------------

async def _fetch_chain(client, symbol, date=None):
    url = f"https://query1.finance.yahoo.com/v7/finance/options/{symbol}"
    params = {"date": date} if date else {}
    r = await client.get(url, params=params)
    r.raise_for_status()
    return r.json()["optionChain"]["result"][0]


async def compute(instrument: str, futures_price: float | None = None) -> dict:
    """Full options-derived summary for GC or NQ via its proxy."""
    proxy = PROXY.get(instrument)
    if not proxy:
        return {"error": "unsupported instrument"}
    cached = _cache.get(instrument)
    if cached and time.time() - cached[0] < CACHE_SEC:
        out = dict(cached[1])
        out["cache_age_sec"] = int(time.time() - cached[0])
        if futures_price:
            _translate(out, futures_price)
        return out

    try:
        async with httpx.AsyncClient(timeout=20, headers=_UA) as c:
            root = await _fetch_chain(c, proxy)
            spot = root["quote"].get("regularMarketPrice")
            expirations = root.get("expirationDates", [])[:4]  # nearest 4
            now = time.time()

            per_strike = {}     # strike -> dict(gex, vanna, call_oi, put_oi, call_gex, put_gex)
            term = []           # (dte_days, atm_iv)
            slice0 = {"net_gex": 0.0, "call_oi": 0, "put_oi": 0, "dte_days": None}
            skew = None

            for i, exp in enumerate(expirations):
                data = root if i == 0 else await _fetch_chain(c, proxy, exp)
                opts = data["options"][0]
                T = max((exp - now) / 86400.0, 0.02) / 365.0
                dte = round((exp - now) / 86400.0, 1)

                atm_iv, atm_dist = None, 1e9
                iv_put_lo, iv_call_hi = None, None  # ~0.95 / ~1.05 moneyness for skew
                for kind, rows in (("call", opts.get("calls", [])), ("put", opts.get("puts", []))):
                    for o in rows:
                        K = o.get("strike"); oi = o.get("openInterest") or 0
                        iv = o.get("impliedVolatility") or 0
                        if not K or iv <= 0.01 or iv > 5:
                            continue
                        g = gamma(spot, K, T, RISK_FREE, iv)
                        vn = vanna(spot, K, T, RISK_FREE, iv)
                        # dealer convention: long calls (+), short puts (-)
                        # GEX per 1% move, $: gamma * OI * 100 * spot^2 * 0.01
                        gex = g * oi * 100 * spot * spot * 0.01
                        vex = vn * oi * 100 * spot * 0.01
                        sgn = 1 if kind == "call" else -1
                        s = per_strike.setdefault(K, {"gex": 0.0, "vanna": 0.0,
                                                      "call_oi": 0, "put_oi": 0,
                                                      "call_gex": 0.0, "put_gex": 0.0})
                        s["gex"] += sgn * gex
                        s["vanna"] += sgn * vex
                        s[kind + "_oi"] += oi
                        s[kind + "_gex"] += gex
                        if i == 0:
                            slice0["net_gex"] += sgn * gex
                            slice0[kind + "_oi"] += oi
                            slice0["dte_days"] = dte
                        d = abs(K - spot)
                        if d < atm_dist:
                            atm_dist, atm_iv = d, iv
                        m = K / spot
                        if kind == "put" and 0.93 <= m <= 0.97:
                            iv_put_lo = iv if iv_put_lo is None else (iv_put_lo + iv) / 2
                        if kind == "call" and 1.03 <= m <= 1.07:
                            iv_call_hi = iv if iv_call_hi is None else (iv_call_hi + iv) / 2
                if atm_iv:
                    term.append({"dte_days": dte, "atm_iv_pct": round(atm_iv * 100, 1)})
                if i == 0 and iv_put_lo and iv_call_hi:
                    skew = round((iv_put_lo - iv_call_hi) * 100, 1)  # >0 = put-skewed

            strikes = sorted(per_strike)
            net_gex = sum(v["gex"] for v in per_strike.values())
            call_wall = max(strikes, key=lambda k: per_strike[k]["call_gex"], default=None)
            put_wall = max(strikes, key=lambda k: per_strike[k]["put_gex"], default=None)
            oi_top = sorted(strikes, key=lambda k: per_strike[k]["call_oi"] + per_strike[k]["put_oi"],
                            reverse=True)[:5]

            # zero-gamma flip: where cumulative net gex crosses 0 scanning up
            flip = None
            cum, prev_k, prev_cum = 0.0, None, 0.0
            for k in strikes:
                cum += per_strike[k]["gex"]
                if prev_k is not None and prev_cum < 0 <= cum:
                    flip = round((prev_k + k) / 2, 2)
                prev_k, prev_cum = k, cum

            vanna_net = sum(v["vanna"] for v in per_strike.values())
            profile = [{"strike": k, "net_gex_musd": round(per_strike[k]["gex"] / 1e6, 1)}
                       for k in strikes if abs(per_strike[k]["gex"]) > 1e5][:60]

            summary = {
                "instrument": instrument, "proxy": proxy, "proxy_spot": spot,
                "as_of_utc": datetime.now(timezone.utc).isoformat(timespec="minutes"),
                "quote_delay_note": "proxy quotes ~15min delayed; OI is prior-day settle",
                "net_gex_musd": round(net_gex / 1e6, 1),
                "dealer_positioning": "SHORT_GAMMA (moves amplify)" if net_gex < 0
                                       else "LONG_GAMMA (moves dampen/pin)",
                "zero_gamma_flip_proxy": flip,
                "call_wall_proxy": call_wall, "put_wall_proxy": put_wall,
                "top_oi_strikes_proxy": oi_top,
                "net_vanna": round(vanna_net / 1e6, 2),
                "vanna_read": "vol-down supports price" if vanna_net > 0 else "vol-down pressures price",
                "iv_term_structure": term,
                "put_call_skew_pts": skew,
                "zero_dte_slice": {**slice0, "net_gex_musd": round(slice0["net_gex"] / 1e6, 1),
                                   "afternoon_note": "intraday 0DTE flow invisible in free data; weight low late-session"},
                "gex_profile": profile,
            }
            summary["zero_dte_slice"].pop("net_gex", None)
            _cache[instrument] = (time.time(), summary)
            out = dict(summary)
            out["cache_age_sec"] = 0
            if futures_price:
                _translate(out, futures_price)
            return out
    except Exception as e:
        if cached:
            out = dict(cached[1])
            out["cache_age_sec"] = int(time.time() - cached[0])
            out["stale_error"] = str(e)
            return out
        return {"instrument": instrument, "error": f"options fetch failed: {e}"}


def _translate(out: dict, fut_price: float):
    """Add futures-equivalent levels via proxy ratio."""
    spot = out.get("proxy_spot")
    if not spot or not fut_price:
        return
    ratio = fut_price / spot
    out["futures_price_used"] = fut_price
    for k_src, k_dst in (("zero_gamma_flip_proxy", "zero_gamma_flip_futures"),
                         ("call_wall_proxy", "call_wall_futures"),
                         ("put_wall_proxy", "put_wall_futures")):
        v = out.get(k_src)
        out[k_dst] = round(v * ratio, 1) if v else None


def compact_for_ai(summary: dict) -> dict:
    """Trimmed version for the AI packet."""
    if not summary or summary.get("error"):
        return {"unavailable": summary.get("error", "no data")}
    keys = ["proxy", "as_of_utc", "cache_age_sec", "quote_delay_note", "net_gex_musd",
            "dealer_positioning", "zero_gamma_flip_proxy", "zero_gamma_flip_futures",
            "call_wall_proxy", "call_wall_futures", "put_wall_proxy", "put_wall_futures",
            "top_oi_strikes_proxy", "net_vanna", "vanna_read", "iv_term_structure",
            "put_call_skew_pts", "zero_dte_slice"]
    return {k: summary.get(k) for k in keys if summary.get(k) is not None}
