"""
core/trading/data.py — market data behind a pluggable provider registry.

Providers register in PROVIDERS and are chosen by the `provider` setting
(default `yahoo`: free, no API key, covers stocks/ETFs/indices/forex/
commodities/crypto/futures quotes + candles). Provider-specific extras
(options chains, economic calendar) honestly report "not supported by the
current provider" instead of inventing anything (spec §1/§7/§8).

What every path upholds:
  * each payload carries provider, exchange timestamp and fetch time;
  * stale data is never dressed as real-time — the formatter prints the
    data timestamp and appends the delay warning when it is >15 min old;
  * a provider failure raises DataError carrying the provider's own words —
    there is no code path that emits an invented price.

All network I/O funnels through _http_get so tests replace it wholesale.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime
from urllib.parse import quote as _urlquote

import requests


class DataError(RuntimeError):
    """The provider said no (or said something we cannot trust)."""


# timeframe -> (provider interval, lookback). 4h has no native Yahoo interval:
# it is aggregated from 1h bars in UTC 4-hour buckets (documented behaviour).
TIMEFRAMES: dict[str, tuple[str, str]] = {
    "1m":  ("1m",  "5d"),
    "5m":  ("5m",  "30d"),
    "15m": ("15m", "30d"),
    "30m": ("30m", "30d"),
    "1h":  ("60m", "6mo"),
    "4h":  ("60m", "6mo"),
    "1D":  ("1d",  "2y"),
    "1W":  ("1wk", "10y"),
    "1M":  ("1mo", "20y"),
}
ALLOWED_TIMEFRAMES = tuple(TIMEFRAMES)

_TTL = {                    # seconds per cache-key kind (§21: cache first)
    "quote": 20.0,
    "intraday": 30.0,
    "daily": 300.0,
    "news": 600.0,
}
_DELAY_AFTER = 900.0         # older than 15 min → "Market data may be delayed."

_cache: dict[tuple, tuple[float, object]] = {}
_cache_lock = threading.Lock()


# ── HTTP + cache ─────────────────────────────────────────────────────────────

def _http_get(url: str, params: dict | None = None, timeout: float = 10.0) -> dict:
    """Single network gateway (tests patch this). Raises DataError, never
    returns something unparseable."""
    try:
        resp = requests.get(
            url, params=params, timeout=timeout,
            headers={"User-Agent": "Mozilla/5.0 (compatible; ICE-JARVIS/1.0)"},
        )
    except requests.RequestException as e:
        raise DataError(f"Network error contacting data provider: {e}") from e
    if resp.status_code != 200:
        raise DataError(f"Provider returned HTTP {resp.status_code} for {url.split('?')[0]}.")
    try:
        return resp.json()
    except ValueError as e:
        raise DataError(f"Provider returned a non-JSON body: {e}") from e


def _cached(key: tuple, ttl: float, fetch):
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and hit[0] > now:
            return hit[1]
    value = fetch()                      # network happens outside the lock
    with _cache_lock:
        _cache[key] = (time.time() + ttl, value)
    return value


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def _ttl_for(timeframe: str) -> float:
    return _TTL["intraday"] if timeframe in ("1m", "5m", "15m", "30m", "1h", "4h") \
        else _TTL["daily"]


# ── Providers ────────────────────────────────────────────────────────────────

class YahooProvider:
    """Yahoo Finance v8 chart endpoint — no key, decent coverage. Data is
    exchange-delayed for many venues; we never claim otherwise."""
    name = "Yahoo Finance"

    def fetch(self, symbol: str, timeframe: str, period: str | None = None) -> dict:
        interval, lookback = TIMEFRAMES[timeframe]
        # `period` (backtests, §15) overrides the default lookback — the
        # provider validates it, so a bogus range fails loudly as DataError.
        url = (f"https://query1.finance.yahoo.com/v8/finance/chart/"
               f"{_urlquote(symbol, safe='')}")
        data = _http_get(url, params={"interval": interval,
                                      "range": str(period or lookback)})
        chart = data.get("chart") or {}
        err = chart.get("error")
        if err:
            raise DataError(
                f"{self.name}: {err.get('description') or err.get('code') or 'unknown error'}")
        results = chart.get("result")
        if not results:
            raise DataError(f"{self.name}: no data for symbol '{symbol}' "
                            f"(it may not exist or may be delisted).")
        return results[0]

    # -- parsing -----------------------------------------------------------

    @staticmethod
    def _aggregate_4h(bars: list[dict]) -> list[dict]:
        """Merge 1h bars into UTC 4-hour buckets: open=first, high=max,
        low=min, close=last, volume=sum. Pure function — unit-tested."""
        out: list[dict] = []
        for bar in bars:
            bucket = (int(bar["t"]) // (4 * 3600)) * (4 * 3600)
            if out and out[-1]["t"] == bucket:
                b = out[-1]
                b["h"] = max(b["h"], bar["h"])
                b["l"] = min(b["l"], bar["l"])
                b["c"] = bar["c"]
                b["v"] = b.get("v", 0) + bar.get("v", 0)
            else:
                out.append({"t": bucket, "o": bar["o"], "h": bar["h"],
                            "l": bar["l"], "c": bar["c"], "v": bar.get("v", 0)})
        return out

    def to_bars(self, result: dict, timeframe: str) -> tuple[list[dict], dict]:
        ts = result.get("timestamp") or []
        quote_arrays = ((result.get("indicators") or {}).get("quote") or [{}])[0]
        opens, highs, lows = quote_arrays.get("open") or [], quote_arrays.get("high") or [], \
            quote_arrays.get("low") or []
        closes, vols = quote_arrays.get("close") or [], quote_arrays.get("volume") or []

        bars: list[dict] = []
        for i, t in enumerate(ts):
            if i >= len(closes) or closes[i] is None:
                continue                       # holes are dropped, never zero-filled
            try:
                bars.append({
                    "t": int(t),
                    "o": float(opens[i]) if i < len(opens) and opens[i] is not None else float(closes[i]),
                    "h": float(highs[i]) if i < len(highs) and highs[i] is not None else float(closes[i]),
                    "l": float(lows[i]) if i < len(lows) and lows[i] is not None else float(closes[i]),
                    "c": float(closes[i]),
                    "v": int(vols[i]) if i < len(vols) and vols[i] is not None else 0,
                })
            except (TypeError, ValueError):
                continue

        if timeframe == "4h":
            bars = self._aggregate_4h(bars)

        m = result.get("meta") or {}
        meta = {
            "price": _num(m.get("regularMarketPrice")),
            "prev_close": _num(m.get("chartPreviousClose") or m.get("previousClose")),
            "currency": m.get("currency") or "",
            "exchange": m.get("fullExchangeName") or m.get("exchangeName") or "",
            "instrument": m.get("instrumentType") or "",
            "name": m.get("longName") or m.get("shortName") or "",
            "data_ts": _num(m.get("regularMarketTime")),
        }
        return bars, meta


def _num(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


PROVIDERS = {"yahoo": YahooProvider()}


def get_provider(name: str | None = None):
    """Resolve the configured provider; unknown names fail loudly (§1: config
    must actually point at something real)."""
    chosen = str(name or settings_provider() or "yahoo").strip().lower()
    if chosen not in PROVIDERS:
        raise DataError(
            f"Unknown market-data provider '{chosen}'. "
            f"Configured providers: {', '.join(sorted(PROVIDERS))}.")
    return PROVIDERS[chosen]


def settings_provider() -> str:
    from core.trading import settings
    return str(settings.get("provider", "yahoo"))


# ── Public fetch API ─────────────────────────────────────────────────────────

def _bars(symbol: str, timeframe: str, ttl: float | None = None,
          period: str | None = None) -> tuple[list[dict], dict]:
    if timeframe not in TIMEFRAMES:
        raise DataError(f"Unsupported timeframe '{timeframe}'. "
                        f"Supported: {', '.join(ALLOWED_TIMEFRAMES)}.")
    sym = str(symbol or "").strip()
    if not sym:
        raise DataError("No symbol given.")
    provider = get_provider()
    key = ("bars", provider.name, sym, timeframe, period)
    return _cached(key, ttl or _ttl_for(timeframe),
                   lambda: provider.to_bars(
                       provider.fetch(sym, timeframe, period=period), timeframe))


def get_candles(symbol: str, timeframe: str = "1D", period: str | None = None) -> dict:
    """Bars + provenance. Shape: {symbol, timeframe, bars[], meta, provider,
    fetched_at, data_asof, delayed}. `period` overrides the default lookback
    (e.g. '6mo' for a backtest)."""
    bars, meta = _bars(symbol, timeframe, period=period)
    if not bars:
        raise DataError(f"{get_provider().name}: '{symbol}' returned zero bars on {timeframe}.")
    asof = meta.get("data_ts") or bars[-1]["t"]
    return {
        "symbol": symbol.upper(),
        "timeframe": timeframe,
        "bars": bars,
        "meta": meta,
        "provider": get_provider().name,
        "fetched_at": time.time(),
        "data_asof": asof,
        "delayed": (time.time() - asof) > _DELAY_AFTER,
    }


def get_quote(symbol: str) -> dict:
    """Current price + day OHLC/volume with full provenance (§1). Price comes
    from the provider's regular-market field; OHLC/volume from the latest
    daily bar. delayed=True whenever the data timestamp is >15 min old."""
    bars, meta = _bars(symbol, "1D", ttl=_TTL["quote"])
    if not bars:
        raise DataError(f"{get_provider().name}: '{symbol}' returned no daily bars.")
    last = bars[-1]
    price = meta.get("price") or last["c"]
    prev = meta.get("prev_close")
    if not prev and len(bars) >= 2:
        prev = bars[-2]["c"]
    change = (price - prev) if (price is not None and prev) else None
    asof = meta.get("data_ts") or last["t"]
    return {
        "symbol": symbol.upper(),
        "price": price,
        "prev_close": prev,
        "change": change,
        "change_pct": (change / prev * 100.0) if (change is not None and prev) else None,
        "open": last["o"], "high": last["h"], "low": last["l"], "close": last["c"],
        "volume": last["v"],
        "timeframe": "1D",
        "currency": meta.get("currency", ""),
        "exchange": meta.get("exchange", ""),
        "name": meta.get("name", ""),
        "instrument": meta.get("instrument", ""),
        "provider": get_provider().name,
        "data_asof": asof,
        "fetched_at": time.time(),
        "delayed": (time.time() - asof) > _DELAY_AFTER,
    }


def fetch_news(query: str, max_results: int = 5) -> list[dict]:
    """Headlines via the existing DDG news path — reused, not rebuilt. Every
    item keeps its source and date so the report can show them (§7/§8)."""
    try:
        from actions.web_search import _ddg_news
        raw = _ddg_news(str(query or "").strip(), max_results=max_results) or []
    except Exception as e:
        raise DataError(f"News search unavailable: {e}") from e
    out = []
    for item in raw[:max_results]:
        if not isinstance(item, dict):
            continue
        title = (item.get("title") or "").strip()
        if not title:
            continue
        out.append({
            "title": title,
            "source": (item.get("source") or "").strip(),
            "url": (item.get("url") or "").strip(),
            "date": str(item.get("date") or item.get("published") or "").strip(),
            "snippet": (item.get("snippet") or item.get("body") or "").strip(),
        })
    return out


def get_earnings(symbol: str) -> dict:
    """Earnings dates via quoteSummary. Yahoo now gates that endpoint behind
    authorization, so failure is the likely case — which we report honestly
    rather than guessing a date (§7: do not invent)."""
    url = (f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/"
           f"{_urlquote(str(symbol).strip(), safe='')}?modules=earnings")
    try:
        data = _http_get(url, timeout=8.0)
        summary = ((data.get("quoteSummary") or {}).get("result") or [None])[0]
        rows = (((summary or {}).get("earnings") or {}).get("earningsChart") or {}).get("quarterly") or []
        if rows:
            return {"available": True, "provider": get_provider().name,
                    "rows": [{"period": r.get("date"), "estimate": r.get("estimate"),
                              "actual": r.get("actual")} for r in rows]}
        raise DataError("provider returned no earnings rows")
    except DataError as e:
        return {"available": False, "provider": get_provider().name,
                "reason": f"Earnings data unavailable: {e}. "
                          f"No earnings information was invented."}


def get_calendar() -> dict:
    """Economic calendar — only honest when a provider supports it (§1
    'when supported'). The default provider does not, and we say so."""
    provider = get_provider()
    return {
        "available": False,
        "provider": provider.name,
        "reason": (f"Provider '{provider.name}' has no economic-calendar "
                   f"endpoint. Use data=news with the event name (e.g. 'FOMC "
                   f"decision') for coverage, or configure a provider with "
                   f"calendar support via trading_settings."),
    }


# ── Formatting (the text the user actually sees) ────────────────────────────

def _fmt_ts(epoch) -> str:
    try:
        return datetime.fromtimestamp(float(epoch)).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return "unknown"


def delay_notice(item: dict) -> str:
    """The delay warning when data is >15 min old — never says 'real-time'."""
    try:
        if item.get("delayed"):
            from core.trading.language import DISCLAIMER_DELAY
            return DISCLAIMER_DELAY
    except Exception:
        pass
    return ""


def format_quote(q: dict) -> str:
    lines = []
    label = q["symbol"]
    if q.get("name"):
        label += f" — {q['name']}"
    if q.get("exchange"):
        label += f" ({q['exchange']})"
    lines.append(label)
    price = q.get("price")
    lines.append(f"Price: {_money(price)} {q.get('currency','')}".rstrip())
    if q.get("change") is not None:
        sign = "+" if q["change"] >= 0 else ""
        pct = f" ({sign}{q['change_pct']:.2f}%)" if q.get("change_pct") is not None else ""
        lines.append(f"Change: {sign}{_money(q['change'])}{pct} vs previous close {_money(q.get('prev_close'))}")
    lines.append(f"OHLC (latest daily bar): O {_money(q.get('open'))}  "
                 f"H {_money(q.get('high'))}  L {_money(q.get('low'))}  C {_money(q.get('close'))}")
    vol = q.get("volume")
    lines.append(f"Volume: {vol:,}" if isinstance(vol, int) else "Volume: n/a")
    lines.append(f"Timeframe: {q.get('timeframe','1D')}")
    lines.append(f"Data provider: {q.get('provider','')}   "
                 f"Data timestamp: {_fmt_ts(q.get('data_asof'))} (exchange time)")
    notice = delay_notice(q)
    if notice:
        lines.append(notice)
    return "\n".join(lines)


def format_candles(c: dict, last: int = 12) -> str:
    bars = c["bars"][-max(1, int(last)):]
    lines = [
        f"{c['symbol']}  {c['timeframe']}  ({len(c['bars'])} bars loaded, "
        f"showing last {len(bars)})",
        "Time                Open       High        Low        Close      Volume",
    ]
    for b in bars:
        lines.append(
            f"{_fmt_ts(b['t'])}  {b['o']:>10.4f} {b['h']:>10.4f} "
            f"{b['l']:>10.4f} {b['c']:>10.4f} {b['v']:>11,}")
    lines.append(f"Data provider: {c['provider']}   "
                 f"Data timestamp: {_fmt_ts(c['data_asof'])} (exchange time)")
    notice = delay_notice(c)
    if notice:
        lines.append(notice)
    return "\n".join(lines)


def _money(v) -> str:
    if v is None:
        return "n/a"
    try:
        v = float(v)
        if abs(v) >= 1000:
            return f"{v:,.2f}"
        if abs(v) >= 1:
            return f"{v:.4f}".rstrip("0").rstrip(".")
        return f"{v:.6f}".rstrip("0").rstrip(".")
    except Exception:
        return str(v)
