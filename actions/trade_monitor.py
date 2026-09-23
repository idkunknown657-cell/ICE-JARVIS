"""
actions/trade_monitor.py — open-trade monitoring (spec §9).
watch registers a position (entry/stop/targets) and auto-registers its
stop/target price alerts; status computes entry, current price, unrealized
P/L ($ and %), time in trade and distances — all from provider quotes with
their provenance.
"""
from core.trading import alerts


def _targets(p) -> list:
    raw = p.get("targets")
    out = []
    if isinstance(raw, (list, tuple)):
        out = [t for t in raw if t not in (None, "")]
    elif raw not in (None, ""):
        out = [raw]
    if p.get("target") not in (None, ""):
        out.append(p.get("target"))
    return out


def trade_monitor(parameters: dict, player=None, speak=None) -> str:
    p = parameters or {}
    action = str(p.get("action") or "status").strip().lower()
    sym = str(p.get("symbol") or "").strip()
    try:
        if action == "watch":
            if not sym:
                return "trade_monitor action=watch needs a symbol."
            r = alerts.add_watch(symbol=sym, side=str(p.get("side") or "long"),
                                 entry=p.get("entry"), stop=p.get("stop"),
                                 targets=_targets(p), quantity=p.get("quantity"))
            if not r.get("ok"):
                return f"Watch not registered: {r['error']}"
            text = (f"Now watching {r['watching']} — auto alerts: "
                    f"{', '.join(r['auto_alerts'])}.")
            st = alerts.watch_status(sym)
            if st.get("ok"):
                return text + "\n\n" + alerts.format_watch(st)
            return text + f"\n(current status unavailable: {st.get('error')})"

        if action == "list":
            rows = alerts.list_watches()
            if not rows:
                return "No trades being watched. Start one with trade_monitor action=watch."
            return "WATCHING:\n" + "\n".join(
                f"  {w['symbol']} {w['side']} @ {w['entry']} "
                f"(stop {w.get('stop') or '-'}, since {w.get('opened_at', '')[:16]})"
                for w in rows)

        if action == "remove":
            r = alerts.remove_watch(sym)
            return r.get("text") or (f"Stopped watching {r['removed']}."
                                     if r.get("ok") else r.get("error", "Not watching."))

        # status (default)
        if not sym:
            return "trade_monitor action=status needs a symbol (or use action=list)."
        st = alerts.watch_status(sym)
        return alerts.format_watch(st)
    except Exception as e:
        return f"trade_monitor failed: {e}"


TOOL = {
    "name": "trade_monitor",
    "description": (
        "Monitor an open trade (spec §9): action=watch registers symbol, "
        "side, entry, stop, targets and quantity — it also auto-registers "
        "stop_level/target_level price alerts so 'tell me if my stop is hit' "
        "fires without extra setup. action=status (default): current price, "
        "unrealized P/L in dollars and percent, time in trade, distance to "
        "stop and targets, with data provider/timestamp (delayed data labeled "
        "as such). action=list / action=remove manage watches. Use for "
        "'monitor this trade', 'watch this position', 'what's my unrealized "
        "P/L on BTC', 'tell me if this setup breaks support'. Reports facts "
        "and configured conditions only — no predictions, no spam."),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "watch, status (default), list, remove."},
            "symbol": {"type": "STRING", "description": "Ticker symbol."},
            "side": {"type": "STRING", "description": "buy/sell for action=watch."},
            "entry": {"type": "NUMBER", "description": "Entry price of the watched trade."},
            "stop": {"type": "NUMBER", "description": "Stop price (auto-alerts on hit)."},
            "target": {"type": "NUMBER", "description": "Single target price (alias of targets[0])."},
            "targets": {"type": "ARRAY", "items": {"type": "NUMBER"},
                        "description": "One or more target prices."},
            "quantity": {"type": "NUMBER", "description": "Position size for P/L math."},
        },
        "required": [],
    },
    "handler": trade_monitor,
}
