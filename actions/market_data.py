"""
actions/market_data.py — market data tool (spec §1/§7/§8).
Thin front-end over core.trading.data: quotes, candles, news, earnings,
calendar — each carrying provider + timestamp, with honest "unavailable"
answers instead of invented numbers.
"""
from core.trading import data as tdata


def _news_text(query: str, count: int) -> str:
    items = tdata.fetch_news(query, max_results=max(1, min(int(count), 10)))
    if not items:
        return (f"No recent headlines found for '{query}' — reporting that "
                f"instead of inventing news (§8).")
    lines = [f"NEWS — '{query}' ({len(items)} headlines, facts only):"]
    for it in items:
        src = it.get("source") or "source unknown"
        when = it.get("date") or "time not given"
        lines.append(f"  - {it['title']}")
        lines.append(f"    Source: {src}   Published: {when}")
        if it.get("url"):
            lines.append(f"    {it['url']}")
        if it.get("snippet"):
            lines.append(f"    {it['snippet'][:200]}")
    lines.append("Headlines above are FACT as reported by their sources — "
                 "interpretation belongs in market_analysis, not here.")
    return "\n".join(lines)


def market_data(parameters: dict, player=None, speak=None) -> str:
    p = parameters or {}
    sym = str(p.get("symbol") or "").strip()
    kind = str(p.get("data") or "quote").strip().lower()
    tf = str(p.get("timeframe") or "1D").strip()
    try:
        count = int(p.get("count") or 12)
    except (TypeError, ValueError):
        count = 12
    query = str(p.get("query") or "").strip() or (f"{sym} stock market news" if sym else "")

    try:
        if kind == "quote":
            if not sym:
                return "market_data: a symbol is required for data=quote (e.g. AAPL, BTC-USD)."
            return tdata.format_quote(tdata.get_quote(sym))
        if kind == "candles":
            if not sym:
                return "market_data: a symbol is required for data=candles."
            return tdata.format_candles(tdata.get_candles(sym, tf), last=count)
        if kind == "news":
            if not query:
                return "market_data: give a symbol or query for data=news."
            return _news_text(query, count)
        if kind == "earnings":
            if not sym:
                return "market_data: a symbol is required for data=earnings."
            r = tdata.get_earnings(sym)
            if not r.get("available"):
                return r["reason"]
            lines = [f"EARNINGS — {sym.upper()} ({r['provider']}):"]
            for row in r["rows"]:
                lines.append(f"  {row['period']}: estimate {row['estimate']} "
                             f"actual {row['actual']}")
            return "\n".join(lines)
        if kind == "calendar":
            r = tdata.get_calendar()
            return r["reason"]
        return (f"market_data: unknown data='{kind}'. Supported: quote, "
                f"candles, news, earnings, calendar.")
    except tdata.DataError as e:
        return f"Market data unavailable: {e}"
    except Exception as e:
        return f"market_data failed: {e}"


TOOL = {
    "name": "market_data",
    "description": (
        "Fetch market data with full provenance (provider + data timestamp). "
        "data=quote: current price, change %, OHLC, volume, timeframe, timestamp; "
        "delayed data carries the warning 'Market data may be delayed' and is never "
        "called real-time. data=candles: OHLCV bars for timeframes 1m/5m/15m/30m/1h/"
        "4h/1D/1W/1M. data=news: real headlines with source and publish time (FACT "
        "only — never invent news). data=earnings and data=calendar report honestly "
        "when the current provider does not support them. Use for 'what's the price "
        "of X', 'show the chart data', 'latest news on X', 'when is the earnings "
        "report'. Present scenarios, not guarantees, when discussing results."),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "symbol": {"type": "STRING",
                       "description": "Ticker symbol, e.g. AAPL, BTC-USD, EURUSD=X, ^GSPC."},
            "data": {"type": "STRING",
                     "description": "One of: quote (default), candles, news, earnings, calendar."},
            "timeframe": {"type": "STRING",
                          "description": "For candles: 1m, 5m, 15m, 30m, 1h, 4h, 1D (default), 1W, 1M."},
            "count": {"type": "INTEGER",
                      "description": "How many candles/headlines to show (default 12)."},
            "query": {"type": "STRING",
                      "description": "Extra search text for data=news (e.g. 'FOMC decision')."},
        },
        "required": ["symbol"],
    },
    "handler": market_data,
}
