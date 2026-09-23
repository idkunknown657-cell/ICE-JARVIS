"""
core/trading/analysis.py — technical analysis + the structured buy/sell report
(spec §2/§3/§4/§6).

Observation → interpretation discipline lives here: indicator functions return
*measurements*, and this module phrases them as OBSERVATIONS (what the data
shows) and INTERPRETATIONS (what it could mean), with bullish/bearish evidence
read both ways, a computed INVALIDATION level, and a scenario TRADE PLAN whose
entry/stop/targets/RR are arithmetic on ATR and swing levels — never a promise.

Multi-timeframe by default for `full` (higher → trend, middle → structure,
lower → entry), quick mode for single-chart checks. Every report ends with the
§16/§8 disclaimers and the data provenance block. Nothing here calls a model —
pure number crunching, per §21.
"""
from __future__ import annotations

import numpy as np

from core.trading import indicators as ind
from core.trading import language, settings
from core.trading.data import (ALLOWED_TIMEFRAMES, DataError, delay_notice,
                               fetch_news, format_quote, get_candles, get_quote)


# ── Per-timeframe indicator snapshot ────────────────────────────────────────

def snapshot(candles: dict) -> dict:
    """All §2 indicators for one timeframe. Pure computation on the payload."""
    bars = candles["bars"]
    c = np.array([b["c"] for b in bars], dtype=float)
    h = np.array([b["h"] for b in bars], dtype=float)
    l = np.array([b["l"] for b in bars], dtype=float)
    v = np.array([b["v"] for b in bars], dtype=float)
    price = float(c[-1]) if c.size else None

    macd_line, macd_sig, macd_hist = ind.macd(c)
    _, _, _, pct_b, bandwidth = ind.bollinger(c)
    adx, pdi, mdi = ind.adx(h, l, c)
    k, s = ind.stochastic(h, l, c)
    avg_vol, rel_vol = ind.volume_stats(v)
    atr_pct = ind.atr_pct(h, l, c)
    sr = ind.support_resistance({"h": h, "l": l, "c": c}, price) if price else None
    trend = ind.trend_structure(h, l, c)
    prev = ind.previous_levels(candles)
    vol = ind.volatility(c)

    def f(arr):
        return ind.last_finite(arr)

    return {
        "timeframe": candles["timeframe"],
        "bars": len(bars),
        "price": price,
        "sma20": f(ind.sma(c, 20)), "sma50": f(ind.sma(c, 50)),
        "sma200": f(ind.sma(c, 200)),
        "ema20": f(ind.ema(c, 20)), "ema50": f(ind.ema(c, 50)),
        "ema200": f(ind.ema(c, 200)),
        "vwap": f(ind.vwap(h, l, c, v)),
        "rsi": f(ind.rsi(c)), "macd": f(macd_line), "macd_signal": f(macd_sig),
        "macd_hist": f(macd_hist),
        "bb_pctb": f(pct_b), "bb_bandwidth": f(bandwidth),
        "atr": f(ind.atr(h, l, c)), "atr_pct": atr_pct,
        "adx": f(adx), "plus_di": f(pdi), "minus_di": f(mdi),
        "stoch_k": f(k), "stoch_d": f(s),
        "avg_volume": avg_vol, "relative_volume": rel_vol,
        "sr": sr, "trend": trend, "previous": prev,
        "ma_cross": ind.ma_cross_state(c),
        "breakout": ind.breakout_state(c, sr) if sr else None,
        "returns_pct": vol.get("returns_pct"),
        "data_asof": candles.get("data_asof"),
        "provider": candles.get("provider"),
        "delayed": candles.get("delayed"),
        "_closes": c,
    }


def _n(v, digits=4):
    if v is None:
        return "n/a"
    try:
        return f"{float(v):,.{digits}f}".rstrip("0").rstrip(".")
    except Exception:
        return str(v)


def snapshot_text(sym: str, snap: dict) -> str:
    """§2 readout for one timeframe: what each indicator *currently shows*."""
    lines = [f"{sym} — {snap['timeframe']}  ({snap['bars']} bars, "
             f"data as of {snap['data_asof']})"]
    tr = snap["trend"]
    lines.append(f"  Trend structure: {tr['label']} — {'; '.join(tr['evidence'])}")
    lines.append(f"  RSI(14): {_n(snap['rsi'], 2)}   Stoch %K/%D: "
                 f"{_n(snap['stoch_k'], 1)}/{_n(snap['stoch_d'], 1)}   "
                 f"ADX: {_n(snap['adx'], 1)} (+DI {_n(snap['plus_di'], 1)} / "
                 f"-DI {_n(snap['minus_di'], 1)})")
    hist = snap["macd_hist"]
    side = "rising/positive" if (hist or 0) > 0 else "falling/negative"
    lines.append(f"  MACD hist: {_n(hist)} ({side})   "
                 f"Bollinger %B: {_n(snap['bb_pctb'], 2)}  bandwidth: {_n(snap['bb_bandwidth'], 4)}")
    lines.append(f"  SMA 20/50/200: {_n(snap['sma20'])} / {_n(snap['sma50'])} / "
                 f"{_n(snap['sma200'])}   EMA 20/50: {_n(snap['ema20'])} / {_n(snap['ema50'])}")
    lines.append(f"  VWAP (series-anchored): {_n(snap['vwap'])}   "
                 f"ATR(14): {_n(snap['atr'])} ({_n(snap['atr_pct'], 2)}% of price)")
    rv = snap["relative_volume"]
    lines.append(f"  Volume: last vs 20-bar avg = {f'{rv:.2f}x' if rv else 'n/a'} "
                 f"(avg {_n(snap['avg_volume'], 0)})")
    sr = snap["sr"] or {}
    sup = ", ".join(_n(x["price"]) for x in sr.get("supports", [])) or "n/a"
    res = ", ".join(_n(x["price"]) for x in sr.get("resistances", [])) or "n/a"
    fb = " (series extremes — not enough swings for pivot levels)" if sr.get("fallback") else ""
    lines.append(f"  Support: {sup}   Resistance: {res}{fb}")
    prev = snap.get("previous")
    if prev:
        lines.append(f"  Previous bar high/low/close: {_n(prev['high'])} / "
                     f"{_n(prev['low'])} / {_n(prev['close'])}")
    mc = snap["ma_cross"]
    cross = (f" — cross {mc['cross_bars_ago']} bar(s) ago"
             if mc["cross_bars_ago"] is not None else "")
    lines.append(f"  MA cross (SMA{mc['fast']}/{mc['slow']}): {mc['state']}{cross}")
    bo = snap.get("breakout")
    if bo and bo.get("price") is not None:
        lines.append(f"  Breakout watch: nearest resistance {_n(bo.get('near_resistance'))}, "
                     f"nearest support {_n(bo.get('near_support'))} — recent bars touched "
                     f"{'above' if bo.get('closed_above') else ''}"
                     f"{'/' if bo.get('closed_above') and bo.get('closed_below') else ''}"
                     f"{'below' if bo.get('closed_below') else ''} those levels"
                     if (bo.get("closed_above") or bo.get("closed_below"))
                     else f"  Breakout watch: price {_n(bo.get('price'))} inside "
                          f"support {_n(bo.get('near_support'))} / resistance {_n(bo.get('near_resistance'))}")
    lines.append("  Indicators are measurements of past/current data — "
                 "none of this is a guaranteed prediction.")
    return "\n".join(lines)


# ── Structured setup (§4/§6) ────────────────────────────────────────────────

def _levels(snap: dict, side: str) -> dict:
    """Entry zone / invalidation / targets / RR — arithmetic on ATR + S/R.
    Scenario numbers, labeled POSSIBLE by the caller."""
    price = snap["price"]
    atr = snap["atr"] or (price * 0.02 if price else None)
    sr = snap["sr"] or {"supports": [], "resistances": []}
    long_side = side != "short"

    if long_side:
        swing_lows = [x["price"] for x in sr.get("supports", [])]
        invalidation = (max([s for s in swing_lows if s < price], default=None)
                        if swing_lows else None)
        if invalidation is None:
            invalidation = price - atr
        else:
            invalidation = min(invalidation, price - 0.25 * atr) - 0.5 * atr
        entry_lo = price - 0.25 * atr
        entry_hi = price
        above = sorted(x["price"] for x in sr.get("resistances", []) if x["price"] > price)
        t1 = above[0] if above else price + 2 * atr
        t2 = above[1] if len(above) > 1 else price + 4 * atr
        t3 = above[2] if len(above) > 2 else price + 6 * atr
    else:
        swing_highs = [x["price"] for x in sr.get("resistances", [])]
        invalidation = (min([s for s in swing_highs if s > price], default=None)
                        if swing_highs else None)
        if invalidation is None:
            invalidation = price + atr
        else:
            invalidation = max(invalidation, price + 0.25 * atr) + 0.5 * atr
        entry_lo = price
        entry_hi = price + 0.25 * atr
        below = sorted((x["price"] for x in sr.get("supports", []) if x["price"] < price),
                       reverse=True)
        t1 = below[0] if below else price - 2 * atr
        t2 = below[1] if len(below) > 1 else price - 4 * atr
        t3 = below[2] if len(below) > 2 else price - 6 * atr

    risk = abs(price - invalidation)
    rr = {}
    for name, tgt in (("t1", t1), ("t2", t2), ("t3", t3)):
        reward = abs(tgt - price)
        rr[name] = (reward / risk) if risk > 0 else None
    return {"entry_lo": entry_lo, "entry_hi": entry_hi,
            "invalidation": invalidation, "t1": t1, "t2": t2, "t3": t3,
            "risk_per_unit": risk, "rr": rr, "atr": atr}


def _scenario_bullets(snap: dict) -> tuple[list[str], list[str]]:
    """Evidence FOR and AGAINST — every bullet starts with its label so
    observations and interpretations never blur (§4)."""
    bull: list[str] = []
    bear: list[str] = []
    tr = snap["trend"]["label"]
    if "uptrend" in tr:
        bull.append(f"OBSERVATION: higher highs/lows on {snap['timeframe']} — "
                    f"structure is up. INTERPRETATION: trend-following longs have "
                    f"the path of least resistance.")
        bear.append("INTERPRETATION (counter): an extended uptrend is where "
                    "crowded longs get squeezed; chasing here has poorer R:R "
                    "than waiting for a pullback.")
    elif "downtrend" in tr:
        bear.append(f"OBSERVATION: lower highs/lows on {snap['timeframe']} — "
                    f"structure is down. INTERPRETATION: dips are the path of "
                    f"least resistance until that changes.")
        bull.append("INTERPRETATION (counter): sustained selloffs in this pattern "
                    "also unwind into sharp relief rallies — shorts need a stop above "
                    "the last lower high.")
    else:
        bull.append("OBSERVATION: swing structure is mixed/range. INTERPRETATION: "
                    "neither side has control — range strategies favor the edges.")
        bear.append("INTERPRETATION: in ranges, breakouts both ways get faded; "
                    "a close beyond the range edge is what would change this.")

    rsi = snap["rsi"]
    if rsi is not None:
        if rsi >= 70:
            bear.append(f"OBSERVATION: RSI {_n(rsi, 1)} is in overbought territory. "
                        f"INTERPRETATION: momentum is strong but historically "
                        f"stretched — late entries carry pullback risk.")
            bull.append("INTERPRETATION (counter): overbought RSI in a strong trend "
                        "stays overbought — it is not by itself a sell signal.")
        elif rsi <= 30:
            bull.append(f"OBSERVATION: RSI {_n(rsi, 1)} is in oversold territory. "
                        f"INTERPRETATION: selling is stretched — bounce setups exist.")
            bear.append("INTERPRETATION (counter): oversold in a downtrend keeps "
                        "getting cheaper — no reversal until structure turns.")
        else:
            side = "bullish" if rsi >= 50 else "bearish"
            bull.append(f"OBSERVATION: RSI {_n(rsi, 1)} ({'above' if rsi >= 50 else 'below'} 50). "
                        f"INTERPRETATION: momentum leans {side}.") \
                if side == "bullish" else \
                bear.append(f"OBSERVATION: RSI {_n(rsi, 1)} (below 50). "
                            f"INTERPRETATION: momentum leans bearish.")

    hist = snap["macd_hist"]
    if hist is not None:
        (bull if hist > 0 else bear).append(
            f"OBSERVATION: MACD histogram {_n(hist)} "
            f"({'above' if hist > 0 else 'below'} zero). INTERPRETATION: "
            f"{'buy momentum expanding' if hist > 0 else 'sell momentum expanding'}.")

    adx = snap["adx"]
    if adx is not None:
        if adx >= 25:
            lead = snap["plus_di"] if (snap["plus_di"] or 0) > (snap["minus_di"] or 0) else snap["minus_di"]
            who = "bulls" if lead == snap["plus_di"] else "bears"
            (bull if who == "bulls" else bear).append(
                f"OBSERVATION: ADX {_n(adx, 1)} ≥ 25 — a defined trend. "
                f"INTERPRETATION: {who} are in control; fading the trend is the "
                f"higher-risk side.")
        else:
            bull.append(f"OBSERVATION: ADX {_n(adx, 1)} < 25 — weak trend. "
                        f"INTERPRETATION: choppy conditions; breakout quality is low.")
            bear.append("INTERPRETATION: the same chop cuts both ways — "
                        "signals fail more often without trend support.")

    ema20, ema50 = snap["ema20"], snap["ema50"]
    if ema20 and ema50:
        if ema20 > ema50:
            bull.append(f"OBSERVATION: EMA20 ({_n(ema20)}) above EMA50 ({_n(ema50)}) — "
                        f"short-term momentum up.")
        else:
            bear.append(f"OBSERVATION: EMA20 ({_n(ema20)}) below EMA50 ({_n(ema50)}) — "
                        f"short-term momentum down.")

    rv = snap["relative_volume"]
    if rv and rv >= 1.5:
        bull.append(f"OBSERVATION: volume {rv:.1f}× the 20-bar average. "
                    f"INTERPRETATION: participation confirms the move's conviction.")
        bear.append("INTERPRETATION (counter): volume spikes often mark "
                    "climaxes — the very bar that confirms can also be the last.")

    return bull, bear


def _news_block(symbol: str) -> str:
    """§7/§8: real headlines with source + time, FACT separated from
    INTERPRETATION. Lookup failure says so — never invents news."""
    try:
        items = fetch_news(f"{symbol} stock market news", max_results=3)
    except DataError as e:
        return ("NEWS CHECK\n  FACT: news lookup failed — " + str(e) +
                "\n  INTERPRETATION: none offered; no news is reported rather "
                "than guessed.")
    if not items:
        return ("NEWS CHECK\n  FACT: the news search returned no recent headlines "
                "for this symbol.\n  INTERPRETATION: none — no headlines means no "
                "news-driven context, not that nothing happened.")
    lines = ["NEWS CHECK"]
    for it in items:
        src = it.get("source") or "source unknown"
        when = it.get("date") or "time not given"
        lines.append(f"  FACT: {it['title']} (Source: {src}, {when})")
    lines.append("  INTERPRETATION: headlines are reported as-is above; assess how "
                 "each one bears on your setup — direction is not inferred from a "
                 "headline alone.")
    return "\n".join(lines)


def analyze(symbol: str, mode: str = "quick",
            timeframes: list[str] | None = None) -> dict:
    """Build the analysis. mode='quick' → one timeframe (§2 readout).
    mode='full' → §4 multi-timeframe structured report (§3 default stack
    1D → 4h → 15m, or the timeframes given)."""
    sym = str(symbol or "").strip().upper()
    if not sym:
        return {"ok": False, "error": "No symbol given. e.g. symbol=BTC-USD or AAPL."}
    mode = str(mode or "quick").lower()
    tfs = [t for t in (timeframes or []) if t in ALLOWED_TIMEFRAMES]

    try:
        quote = get_quote(sym)
    except Exception as e:
        return {"ok": False, "error": f"Could not price {sym}: {e}"}

    if mode == "quick":
        tf = tfs[0] if tfs else "1h"
        try:
            candles = get_candles(sym, tf)
        except Exception as e:
            return {"ok": False, "error": f"Could not load {tf} data for {sym}: {e}"}
        snap = snapshot(candles)
        text = (f"QUICK ANALYSIS — {sym} ({settings.mode_label()})\n"
                + format_quote(quote) + "\n\n"
                + snapshot_text(sym, snap) + "\n\n"
                + language.DISCLAIMER_ANALYSIS)
        return {"ok": True, "symbol": sym, "mode": "quick", "text":
                language.ensure_safe(text), "snapshot": snap}

    # ── full, multi-timeframe (§3) ──────────────────────────────────────────
    stack = tfs if len(tfs) >= 2 else ["1D", "4h", "15m"]
    snaps: dict[str, dict] = {}
    errors: list[str] = []
    for tf in stack:
        try:
            snaps[tf] = snapshot(get_candles(sym, tf))
        except Exception as e:
            errors.append(f"{tf}: {e}")
    if not snaps:
        return {"ok": False, "error": f"No timeframe could be loaded for {sym}. "
                                     f"Details: " + " | ".join(errors)}
    htf = stack[0] if stack[0] in snaps else next(iter(snaps))
    mtf = stack[1] if len(stack) > 1 and stack[1] in snaps else htf
    ltf = stack[2] if len(stack) > 2 and stack[2] in snaps else (mtf if mtf in snaps else htf)

    levels = _levels(snaps[ltf], "long")
    levels_short = _levels(snaps[ltf], "short")
    bull, bear = _scenario_bullets(snaps[mtf])
    bull_h, bear_h = _scenario_bullets(snaps[htf])
    # Higher-timeframe structure dominates both scenarios.
    bull = bull_h[:1] + bull
    bear = bear_h[:1] + bear

    acct = float(settings.get("account_size") or 0)
    risk_pct = float(settings.get("risk_per_trade_pct") or 1)

    lines = [
        f"ANALYSIS — {sym} ({settings.mode_label()})",
        "",
        "CURRENT MARKET STATE",
        format_quote(quote),
        f"Higher timeframe {htf}: {snaps[htf]['trend']['label']}",
        f"Middle timeframe {mtf}: {snaps[mtf]['trend']['label']}",
        f"Lower timeframe {ltf}: entry/confirmation lens (RSI "
        f"{_n(snaps[ltf]['rsi'], 1)}, ATR {_n(snaps[ltf]['atr'])})",
        f"Volatility: ATR% { _n(snaps[ltf]['atr_pct'], 2) } on {ltf}; "
        f"returns stdev { _n(snaps[ltf]['returns_pct'], 3) }%",
        "",
        _news_block(sym),
        "",
        "BULLISH SCENARIO (conditions that could support an upward move)",
    ]
    lines += [f"  {b}" for b in dict.fromkeys(bull)]
    lines += ["", "BEARISH SCENARIO (conditions that could support a downward move)"]
    lines += [f"  {b}" for b in dict.fromkeys(bear)]

    lines += [
        "",
        "INVALIDATION (what makes the long setup no longer valid)",
        f"  OBSERVATION: a close below {_n(levels['invalidation'])} on {ltf} "
        f"({settings.get('provider')} data) breaks the level this setup relies on.",
        f"  For the short scenario the mirror invalidation is a close above "
        f"{_n(levels_short['invalidation'])}.",
        "",
        "RISK",
        f"  Potential downside if stopped at the invalidation level: "
        f"{_n(levels['risk_per_unit'])} per unit ({_n(levels['risk_per_unit'] / snaps[ltf]['price'] * 100, 2)}% of price).",
        f"  Volatility: ATR {_n(snaps[ltf]['atr'])} = {_n(snaps[ltf]['atr_pct'], 2)}% — "
        f"position size must shrink as stops widen.",
    ]
    if acct > 0:
        from core.trading import risk as risk_mod
        size = risk_mod.position_size(acct, risk_pct, snaps[ltf]["price"],
                                      levels["invalidation"])
        lines.append("  Position sizing at your configured risk (§5 math):")
        lines.append("    " + risk_mod.format_size(size).replace("\n", "\n    "))
    else:
        lines.append("  Account size not configured — dollar risk not computed. "
                     "Set it via trading_settings to see sizing in every analysis.")
    lines += [
        "  Important events: see the NEWS CHECK above; a calendar provider is "
        "not configured, so economic-release dates are not claimed.",
        "",
        "TRADE PLAN — POSSIBLE SETUP (scenario, not a prediction)",
        f"  Entry: {_n(levels['entry_lo'])}–{_n(levels['entry_hi'])} (±0.25 ATR zone "
        f"around {_n(snaps[ltf]['price'])})",
        f"  Invalidation: below {_n(levels['invalidation'])}",
        f"  Target 1: {_n(levels['t1'])}  (R:R 1:{_n(levels['rr']['t1'], 2)})",
        f"  Target 2: {_n(levels['t2'])}  (R:R 1:{_n(levels['rr']['t2'], 2)})",
        f"  Target 3: {_n(levels['t3'])}  (R:R 1:{_n(levels['rr']['t3'], 2)})",
        f"  Risk/Reward to T1 uses risk {_n(levels['risk_per_unit'])} per unit.",
        "  CONFIRMATION NEEDED:",
        f"    - {ltf} closes back inside the entry zone (not just a wick).",
        f"    - Momentum reading cooperates: RSI {_n(snaps[ltf]['rsi'], 1)} holding "
        f"{'above 50' if (snaps[ltf]['rsi'] or 50) >= 50 else 'turning up from oversold'}.",
        f"    - Volume on the trigger bar at or above the 20-bar average "
        f"(currently {_n(snaps[ltf]['relative_volume'], 2)}×).",
    ]
    if errors:
        lines.append(f"  NOTE: some timeframes failed to load ({'; '.join(errors)}) — "
                     f"the report covers what the provider returned.")
    lines += [
        "",
        f"Data: {quote.get('provider')} as of the timestamps above. "
        f"Two scenarios are currently plausible; confirmation would strengthen "
        f"either. The downside risk is the invalidation distance shown above.",
        language.DISCLAIMER_ANALYSIS,
    ]
    notice = delay_notice(quote)
    if notice:
        lines.append(notice)
    return {"ok": True, "symbol": sym, "mode": "full", "stack": stack,
            "levels": {"long": levels, "short": levels_short},
            "text": language.ensure_safe("\n".join(lines))}
