"""
core/trading/alerts.py — trading alerts (§10) and position watching (§9).

One store, one worker: a lazily-started 20-second daemon that reads quotes
through the data cache (§21: rate-limit friendly) and evaluates registered
conditions. Alerts fire ONCE by default — §9 explicitly forbids nagging.

Delivery mirrors the existing news monitor: fired alerts go into a queue that
main.py's async loop drains onto the Live session; this module never speaks
by itself. Position watches (§9) reuse the same store and auto-register their
own stop/target price alerts — "tell me if my stop is hit" needs no extra
machinery.

economic_event alerts are honestly refused until a provider ships a calendar
(§1 'when supported' — we never fake one).
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from datetime import datetime
from pathlib import Path

ALERT_TYPES = (
    "price_above", "price_below", "pct_move", "breakout", "breakdown",
    "rsi_level", "ma_cross", "volume_spike", "support", "resistance",
    "stop_level", "target_level", "news", "economic_event",
)
_LEVEL_TYPES = {"price_above", "price_below", "breakout", "breakdown",
                "support", "resistance", "stop_level", "target_level"}

_AUTO_START = True          # tests flip this — no real threads/feeds in unit tests
_POLL_SECONDS = 20.0

_lock = threading.Lock()
_queue: list[str] = []
_notifier = None
_worker = None
_worker_stop = threading.Event()


def _path() -> Path:
    from memory.config_manager import CONFIG_DIR
    return CONFIG_DIR / "trading_alerts.json"


def _load() -> dict:
    try:
        p = _path()
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("alerts", {})
                data.setdefault("watches", {})
                data.setdefault("seq", 0)
                return data
    except Exception:
        pass
    return {"alerts": {}, "watches": {}, "seq": 0}


def _save(data: dict) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)


def configure(auto_start: bool) -> None:
    """Test hook: disable the background worker entirely."""
    global _AUTO_START
    _AUTO_START = bool(auto_start)


def set_notifier(cb) -> None:
    global _notifier
    _notifier = cb


def drain() -> list[str]:
    """Pop every fired alert for delivery (main's async loop owns speaking)."""
    with _lock:
        out, _queue[:] = _queue[:], []
    return out


# ── Adding / listing / removing ──────────────────────────────────────────────

def add(kind: str, symbol: str = "", level=None, topic: str = "",
        direction: str = "above", multiplier: float = 2.0,
        ref_price=None, once: bool = True) -> dict:
    """Register an alert. Validates against §10's type list and refuses
    un-supportable types with a real alternative (no fake 'monitoring')."""
    kind = str(kind or "").strip().lower()
    if kind not in ALERT_TYPES:
        return {"ok": False,
                "error": f"Unknown alert type '{kind}'. Supported: {', '.join(ALERT_TYPES)}."}
    if kind == "economic_event":
        from core.trading import data as tdata
        return {"ok": False,
                "error": tdata.get_calendar()["reason"]}
    if kind == "news":
        if not str(topic or "").strip():
            return {"ok": False, "error": "News alerts need a topic (e.g. 'FOMC decision')."}
    elif not str(symbol or "").strip():
        return {"ok": False, "error": f"Alert '{kind}' needs a symbol."}

    if kind in _LEVEL_TYPES:
        if level is None:
            return {"ok": False, "error": f"Alert '{kind}' needs a price level."}
        try:
            level = float(level)
        except (TypeError, ValueError):
            return {"ok": False, "error": "level must be a number."}
    elif kind == "pct_move":
        try:
            level = float(level)
        except (TypeError, ValueError):
            return {"ok": False, "error": "pct_move needs level = the % move to alert on."}
        if ref_price is None and str(symbol or "").strip():
            try:
                from core.trading import data as tdata
                ref_price = tdata.get_quote(symbol)["price"]
            except Exception as e:
                return {"ok": False,
                        "error": f"Could not capture a reference price ({e}). "
                                 f"Retry when data is reachable, or pass ref_price explicitly."}
        if ref_price is None:
            return {"ok": False, "error": "pct_move needs a reference price."}
    elif kind == "rsi_level":
        try:
            level = float(level)
        except (TypeError, ValueError):
            return {"ok": False, "error": "rsi_level needs level = the RSI value (0-100)."}
        direction = str(direction or "above").lower()
        if direction not in ("above", "below"):
            return {"ok": False, "error": "rsi_level direction must be 'above' or 'below'."}
    elif kind == "volume_spike":
        try:
            multiplier = float(multiplier)
        except (TypeError, ValueError):
            multiplier = 2.0

    with _lock:
        data = _load()
        data["seq"] += 1
        aid = f"A{data['seq']:04d}"
        data["alerts"][aid] = {
            "id": aid, "type": kind, "symbol": str(symbol or "").upper().strip(),
            "level": level, "topic": str(topic or ""), "direction": direction,
            "multiplier": multiplier, "ref_price": ref_price,
            "once": bool(once), "fired": 0, "added": _now(),
            "last_check": "", "state": {},
        }
        _save(data)
        rec = data["alerts"][aid]
    _ensure_worker()
    return {"ok": True, "alert": rec}


def list_alerts() -> list[dict]:
    data = _load()
    return list(data["alerts"].values())


def remove(ref: str) -> dict:
    """Remove by id (A0001) or any substring of type/symbol/topic."""
    ref = str(ref or "").strip()
    if not ref:
        return {"ok": False, "error": "Give an alert id, symbol or type to remove."}
    with _lock:
        data = _load()
        for aid, rec in list(data["alerts"].items()):
            hay = " ".join([aid, rec.get("type", ""), rec.get("symbol", ""),
                            rec.get("topic", "")]).lower()
            if ref.lower() in hay.lower():
                del data["alerts"][aid]
                _save(data)
                return {"ok": True, "removed": aid, "alert": rec}
    return {"ok": False, "error": f"No alert matches '{ref}'."}


def format_alerts(alerts: list[dict]) -> str:
    if not alerts:
        return ("No trading alerts registered. Add one with trade_alerts "
                "action=add (e.g. type=price_above symbol=BTC-USD level=100000).")
    lines = [f"TRADING ALERTS ({len(alerts)}):"]
    for a in alerts:
        desc = f"  {a['id']}  {a['type']}"
        if a.get("symbol"):
            desc += f"  {a['symbol']}"
        if a.get("level") is not None:
            desc += f"  level {a['level']}"
        if a.get("topic"):
            desc += f"  topic '{a['topic']}'"
        desc += "  (fires once)" if a.get("once") else "  (recurring)"
        if a.get("fired"):
            desc += f"  [fired {a['fired']}×]"
        lines.append(desc)
    return "\n".join(lines)


# ── Position watching (§9) ───────────────────────────────────────────────────

def add_watch(symbol: str, side: str, entry, stop="", targets=None,
              quantity="") -> dict:
    """Watch an open trade: status is computed on demand, and stop/target
    price alerts are auto-registered (once) so level hits notify for free."""
    sym = str(symbol or "").strip().upper()
    if not sym:
        return {"ok": False, "error": "A symbol is required to watch a trade."}
    try:
        entry_f = float(entry)
    except (TypeError, ValueError):
        return {"ok": False, "error": "entry price is required to watch a trade."}
    if entry_f <= 0:
        return {"ok": False, "error": "entry price must be positive."}
    if stop not in (None, "") and _f(stop) is None:
        return {"ok": False, "error": "stop must be a price (number)."}
    for t in (targets or []):
        if t not in (None, "") and _f(t) is None:
            return {"ok": False, "error": f"target '{t}' must be a price (number)."}
    side_l = str(side or "long").lower()
    if side_l in ("buy", "long"):
        side_l = "long"
    elif side_l in ("sell", "short"):
        side_l = "short"

    with _lock:
        data = _load()
        data["watches"][sym] = {
            "symbol": sym, "side": side_l, "entry": entry_f,
            "stop": _f(stop), "targets": [_f(t) for t in (targets or []) if _f(t)],
            "quantity": _f(quantity), "opened_at": _now(),
        }
        _save(data)

    registered = []
    if _f(stop):
        r = add("stop_level", symbol=sym, level=float(stop),
                direction="below" if side_l == "long" else "above")
        if r.get("ok"):
            registered.append(f"stop {stop}")
    for t in (targets or []):
        if _f(t):
            r = add("target_level", symbol=sym, level=float(t),
                    direction="above" if side_l == "long" else "below")
            if r.get("ok"):
                registered.append(f"target {t}")
    return {"ok": True, "watching": sym,
            "auto_alerts": registered or ["none (no stop/target levels given)"]}


def list_watches() -> list[dict]:
    return list(_load()["watches"].values())


def remove_watch(symbol: str) -> dict:
    sym = str(symbol or "").strip().upper()
    with _lock:
        data = _load()
        if sym not in data["watches"]:
            return {"ok": False, "error": f"Not watching '{symbol}'."}
        del data["watches"][sym]
        # Drop the stop/target alerts THIS watch auto-registered — they belong
        # to the position, not to the user's own separate alert choices.
        dropped = [aid for aid, rec in data["alerts"].items()
                   if rec.get("symbol") == sym
                   and rec.get("type") in ("stop_level", "target_level")]
        for aid in dropped:
            del data["alerts"][aid]
        _save(data)
        return {"ok": True, "removed": sym, "alerts_dropped": len(dropped)}


def watch_status(symbol: str) -> dict:
    """§9 field set: entry, current, stop, targets, unrealized P/L, %, time in
    trade. Data provenance comes along from the quote."""
    sym = str(symbol or "").strip().upper()
    watch = _load()["watches"].get(sym)
    if not watch:
        return {"ok": False, "error": f"Not watching {sym}. Start with trade_monitor "
                                      f"action=watch."}
    try:
        from core.trading import data as tdata
        q = tdata.get_quote(sym)
    except Exception as e:
        return {"ok": False, "error": f"Could not price {sym}: {e}"}

    from core.trading import paper
    u = paper.unrealized(watch, q["price"])
    out = {
        "ok": True, "symbol": sym, "side": watch["side"],
        "entry": watch["entry"], "current": q["price"],
        "stop": watch.get("stop"), "targets": watch.get("targets") or [],
        "unrealized_pnl": u["pnl"], "unrealized_pnl_pct": u["pnl_pct"],
        "quantity": watch.get("quantity"),
        "time_in_trade": _elapsed(watch.get("opened_at")),
        "provider": q.get("provider"), "data_asof": q.get("data_asof"),
        "delayed": q.get("delayed"),
    }
    if out["stop"] and u["pnl"] is not None:
        out["distance_to_stop_pct"] = abs(q["price"] - out["stop"]) / q["price"] * 100.0
    out["distance_to_targets_pct"] = [
        abs(t - q["price"]) / q["price"] * 100.0 for t in out["targets"]]
    return out


def format_watch(st: dict) -> str:
    if not st.get("ok"):
        return st["error"]
    def m(v):
        return "n/a" if v is None else f"{v:,.4f}".rstrip("0").rstrip(".")
    lines = [
        f"WATCHING {st['symbol']} ({st['side'].upper()}"
        + (f" ×{m(st['quantity'])}" if st.get('quantity') else "") + ")",
        f"  Entry: {m(st['entry'])}    Current: {m(st['current'])}",
        f"  Stop: {m(st['stop'])}    Targets: {', '.join(m(t) for t in st['targets']) or 'n/a'}",
        f"  Unrealized P/L: ${st['unrealized_pnl']:,.2f} ({st['unrealized_pnl_pct']:+.2f}%)"
        if st.get("unrealized_pnl") is not None else "  Unrealized P/L: n/a",
        f"  Time in trade: {st['time_in_trade']}",
    ]
    if st.get("distance_to_stop_pct") is not None:
        lines.append(f"  Distance to stop: {st['distance_to_stop_pct']:.2f}%")
    if st.get("distance_to_targets_pct"):
        lines.append("  Distance to targets: "
                     + ", ".join(f"{d:.2f}%" for d in st["distance_to_targets_pct"]))
    from core.trading.data import delay_notice
    lines.append(f"  Data: {st.get('provider','')} as of "
                 f"{datetime.fromtimestamp(st['data_asof']).strftime('%Y-%m-%d %H:%M:%S')}"
                 if st.get("data_asof") else "  Data: unavailable")
    notice = delay_notice(st)
    if notice:
        lines.append(f"  {notice}")
    return "\n".join(lines)


# ── Evaluation (pure-ish core, unit-testable without threads) ────────────────

def evaluate_once() -> list[str]:
    """Evaluate every registered alert once. Returns [TRADE_ALERT] strings;
    never raises (a broken feed skips a cycle instead of killing the worker)."""
    data = _load()
    alerts = dict(data["alerts"])
    if not alerts:
        return []
    fired: list[str] = []
    to_delete: list[str] = []
    quote_cache: dict[str, dict] = {}
    candles_cache: dict[tuple, dict] = {}

    def quote(sym):
        if sym not in quote_cache:
            from core.trading import data as tdata
            quote_cache[sym] = tdata.get_quote(sym)
        return quote_cache[sym]

    def candles(sym, tf="15m"):
        key = (sym, tf)
        if key not in candles_cache:
            from core.trading import data as tdata
            candles_cache[key] = tdata.get_candles(sym, tf)
        return candles_cache[key]

    for aid, a in alerts.items():
        try:
            msg = _evaluate(a, quote, candles)
            if msg:
                fired.append(msg)
                with _lock:
                    d2 = _load()
                    rec = d2["alerts"].get(aid)
                    if rec is not None:
                        rec["fired"] = int(rec.get("fired", 0)) + 1
                        if rec.get("once", True):
                            del d2["alerts"][aid]
                        _save(d2)
        except Exception:
            continue      # provider hiccup: this cycle is skipped, honestly
    return fired


def _evaluate(a: dict, quote, candles) -> str | None:
    kind = a["type"]
    sym = a.get("symbol", "")

    if kind == "news":
        return _eval_news(a)
    if kind in ("price_above", "breakout", "resistance", "target_level",
                "price_below", "breakdown", "support", "stop_level"):
        q = quote(sym)
        price = q.get("price")
        prov = f" — data from {q.get('provider') or 'provider unknown'}"
        above = kind in ("price_above", "breakout", "resistance", "target_level")
        if price is None:
            return None
        if above and price >= a["level"]:
            return _alert_line(a, f"price {price:g} crossed ABOVE "
                                  f"{a['level']:g}{prov}")
        if not above and price <= a["level"]:
            return _alert_line(a, f"price {price:g} crossed BELOW "
                                  f"{a['level']:g}{prov}")
        return None
    if kind == "pct_move":
        q = quote(sym)
        price = q.get("price")
        ref = a.get("ref_price")
        if price is None or not ref:
            return None
        change = (price - ref) / ref * 100.0
        if abs(change) >= a["level"]:
            return _alert_line(a, f"moved {change:+.2f}% from reference {ref:g} "
                                  f"(now {price:g}) — data from "
                                  f"{q.get('provider') or 'provider unknown'}")
        return None
    if kind == "rsi_level":
        from core.trading import indicators as i2
        bars = candles(sym, "15m")
        r = i2.last_finite(i2.rsi([b["c"] for b in bars["bars"]], 14))
        if r is None:
            return None
        prev_list = i2.rsi([b["c"] for b in bars["bars"][:-1]], 14)
        prev = i2.last_finite(prev_list)
        level = a["level"]
        if a.get("direction", "above") == "above":
            hit = prev is not None and prev < level <= r
        else:
            hit = prev is not None and prev > level >= r
        if hit:
            return _alert_line(a, f"RSI(14) on 15m crossed {a.get('direction','above')} "
                                  f"{level:g} (now {r:.1f})")
        return None
    if kind == "ma_cross":
        from core.trading import indicators as i2
        bars = candles(sym, "15m")
        state = i2.ma_cross_state([b["c"] for b in bars["bars"]], 20, 50)
        if state["cross_bars_ago"] == 0:
            return _alert_line(a, f"SMA20/50 cross on 15m — {state['state']}")
        return None
    if kind == "volume_spike":
        from core.trading import indicators as i2
        bars = candles(sym, "15m")
        avg, rel = i2.volume_stats([b["v"] for b in bars["bars"]], 20)
        if rel is not None and rel >= float(a.get("multiplier") or 2.0):
            return _alert_line(a, f"volume spike: {rel:.1f}× the 20-bar average")
        return None
    return None


def _eval_news(a: dict) -> str | None:
    """Headline-hash check (same once-per-new-title logic as the news monitor)."""
    try:
        from core.trading import data as tdata
        items = tdata.fetch_news(a.get("topic", ""), max_results=3)
    except Exception:
        return None
    if not items:
        return None
    top = items[0]
    h = hashlib.md5(top["title"].encode("utf-8", "ignore")).hexdigest()[:12]
    state = a.get("state") or {}
    if state.get("hash") == h:
        return None
    with _lock:
        data = _load()
        rec = data["alerts"].get(a["id"])
        if rec is not None:
            rec["state"] = {"hash": h, "title": top["title"]}
            _save(data)
    src = top.get("source") or "source unknown"
    date = top.get("date") or "time not given"
    return (f"[TRADE_ALERT] news for '{a.get('topic','')}': {top['title']} "
            f"(Source: {src}, {date})")


def _alert_line(a: dict, detail: str) -> str:
    return (f"[TRADE_ALERT] {a.get('symbol') or a.get('topic')} "
            f"{a['type']}: {detail}. Alert {a['id']} fired at "
            f"{datetime.now().strftime('%H:%M:%S')}.")


# ── Worker ───────────────────────────────────────────────────────────────────

def _ensure_worker() -> None:
    global _worker
    if not _AUTO_START:
        return
    with _lock:
        if _worker and _worker.is_alive():
            return
        _worker_stop.clear()
        _worker = threading.Thread(target=_worker_loop, daemon=True,
                                   name="trade-alerts")
        _worker.start()


def _worker_loop() -> None:
    while not _worker_stop.is_set():
        try:
            for msg in evaluate_once():
                with _lock:
                    _queue.append(msg)
                if _notifier:
                    try:
                        _notifier(msg)
                    except Exception:
                        pass
        except Exception:
            pass
        _worker_stop.wait(_POLL_SECONDS)


def stop_worker() -> None:
    _worker_stop.set()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _elapsed(since: str) -> str:
    try:
        start = datetime.fromisoformat(since)
        delta = datetime.now() - start
        mins = int(delta.total_seconds() // 60)
        if mins < 60:
            return f"{mins} min"
        hours, m = divmod(mins, 60)
        if hours < 48:
            return f"{hours} h {m} min"
        return f"{hours // 24} d {hours % 24} h"
    except Exception:
        return "unknown"


def _f(v):
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None
