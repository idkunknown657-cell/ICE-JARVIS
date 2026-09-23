"""
actions/market_analysis.py — technical/multi-timeframe analysis tool
(spec §2/§3/§4/§6).

mode=quick: indicator readout for the given timeframe(s) — 'check the 1-hour
and 4-hour chart'. mode=full: the structured CURRENT MARKET STATE / BULLISH /
BEARISH / INVALIDATION / RISK / TRADE PLAN report with entry, stop, targets,
R:R and confirmation checklist — 'should I buy', 'analyze BTC', 'is this a
good entry'.
"""
from core.trading import analysis, language, settings
from core.trading.data import ALLOWED_TIMEFRAMES, DataError, format_quote, \
    get_candles, get_quote


def _parse_timeframes(raw) -> list[str]:
    if isinstance(raw, (list, tuple)):
        items = [str(x).strip() for x in raw]
    else:
        items = [x.strip() for x in str(raw or "").replace(";", ",").split(",")]
    out = []
    for it in items:
        if not it:
            continue
        if it not in ALLOWED_TIMEFRAMES:
            raise DataError(f"Unknown timeframe '{it}'. Supported: "
                            f"{', '.join(ALLOWED_TIMEFRAMES)}.")
        out.append(it)
    return out


def market_analysis(parameters: dict, player=None, speak=None) -> str:
    p = parameters or {}
    sym = str(p.get("symbol") or "").strip().upper()
    if not sym:
        return "market_analysis: a symbol is required (e.g. symbol=BTC-USD or NVDA)."
    mode = str(p.get("mode") or "quick").strip().lower()
    if mode not in ("quick", "full"):
        mode = "quick"
    try:
        tfs = _parse_timeframes(p.get("timeframes"))
    except DataError as e:
        return str(e)

    try:
        if mode == "full":
            r = analysis.analyze(sym, mode="full", timeframes=tfs)
            return r["text"] if r.get("ok") else f"Analysis unavailable: {r['error']}"

        # quick: one readout per requested timeframe (default 1h)
        if not tfs:
            tfs = ["1h"]
        blocks = []
        try:
            blocks.append(format_quote(get_quote(sym)))
        except Exception:
            blocks.append(f"(quote unavailable for {sym} — timeframe readouts follow)")
        for tf in tfs[:4]:
            try:
                snap = analysis.snapshot(get_candles(sym, tf))
                blocks.append(analysis.snapshot_text(sym, snap))
            except Exception as e:
                blocks.append(f"{sym} {tf}: could not load — {e}")
        text = (f"QUICK ANALYSIS — {sym} ({settings.mode_label()})\n\n"
                + "\n\n".join(blocks)
                + "\n\n" + language.DISCLAIMER_ANALYSIS)
        return language.ensure_safe(text)
    except DataError as e:
        return f"Analysis unavailable: {e}"
    except Exception as e:
        return f"market_analysis failed: {e}"


TOOL = {
    "name": "market_analysis",
    "description": (
        "Analyze an asset with evidence-based, scenario language (never "
        "certainty — say what current data supports, what would invalidate it, "
        "what the downside is). mode=quick: per-timeframe indicator readout "
        "(trend structure, RSI, MACD, Bollinger, ATR, ADX, stochastic, S/R, "
        "previous highs/lows, breakouts, MA crosses, volume, volatility) for "
        "timeframes in 'timeframes' (1m/5m/15m/30m/1h/4h/1D/1W/1M) — use for "
        "'check the 1-hour and 4-hour chart' or 'what does RSI show'. "
        "mode=full: multi-timeframe structured report (higher TF trend → "
        "middle TF structure → lower TF entry) with sections CURRENT MARKET "
        "STATE, BULLISH SCENARIO, BEARISH SCENARIO, INVALIDATION, RISK, and "
        "TRADE PLAN (entry zone, stop/invalidation, targets, risk/reward, "
        "confirmation checklist, position sizing when account_size is set) — "
        "use for 'analyze BTC', 'should I buy/sell', 'is this a good entry', "
        "'find potential setups', 'tell me the bullish and bearish scenarios', "
        "'where would the setup be invalidated'. Never claim an outcome is "
        "guaranteed; distinguish observations from interpretations."),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "symbol": {"type": "STRING", "description": "Ticker to analyze, e.g. NVDA, BTC-USD."},
            "mode": {"type": "STRING",
                     "description": "quick (default) = indicator readout; full = structured "
                                    "multi-timeframe setup report with trade plan."},
            "timeframes": {"type": "STRING",
                           "description": "Comma-separated timeframes, e.g. '1D,4h,15m'. "
                                          "full defaults to 1D,4h,15m; quick defaults to 1h."},
        },
        "required": ["symbol"],
    },
    "handler": market_analysis,
}
