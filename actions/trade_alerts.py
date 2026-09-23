"""
actions/trade_alerts.py — trading alerts tool (spec §10).
add/list/remove against the shared alert store + background worker. Fires
once by default (§9: no nagging); economic_event is honestly refused until a
provider supports calendars.
"""
from core.trading import alerts


def trade_alerts(parameters: dict, player=None, speak=None) -> str:
    p = parameters or {}
    action = str(p.get("action") or "list").strip().lower()
    try:
        if action == "add":
            kind = str(p.get("type") or p.get("alert_type") or "").strip()
            once = p.get("once")
            if isinstance(once, str):
                once = once.strip().lower() not in ("0", "false", "no", "off")
            elif once is None:
                once = True
            else:
                once = bool(once)
            r = alerts.add(
                kind=kind, symbol=str(p.get("symbol") or ""),
                level=p.get("level"), topic=str(p.get("topic") or ""),
                direction=str(p.get("direction") or "above"),
                multiplier=p.get("multiplier") or 2.0,
                ref_price=p.get("ref_price"), once=once)
            if not r.get("ok"):
                return f"Alert not registered: {r['error']}"
            a = r["alert"]
            desc = f"{a['type']} {a['symbol']} level {a['level']}" if a.get("level") \
                else f"{a['type']} topic '{a['topic']}'"
            return (f"Alert registered: {a['id']} — {desc} "
                    f"({'fires once' if a['once'] else 'recurring'}); evaluated in "
                    f"the background and reported when it triggers.")

        if action == "remove":
            ref = str(p.get("id") or p.get("symbol") or p.get("type") or "").strip()
            r = alerts.remove(ref)
            return r.get("text") or (
                f"Removed alert {r['removed']}." if r.get("ok") else r.get("error", "No alert removed."))

        return alerts.format_alerts(alerts.list_alerts())
    except Exception as e:
        return f"trade_alerts failed: {e}"


TOOL = {
    "name": "trade_alerts",
    "description": (
        "Market alerts evaluated in the background (spec §10): price_above, "
        "price_below, pct_move (level = % move from ref price), breakout, "
        "breakdown, rsi_level (15m RSI crossing a level, direction=above|"
        "below), ma_cross (15m SMA 20/50), volume_spike (multiplier vs 20-bar "
        "avg), support, resistance, stop_level, target_level, news (topic "
        "headlines), economic_event (refused honestly until a calendar "
        "provider exists). action=add needs type + symbol + level (except "
        "news: topic). Alerts fire once by default — one notification, no "
        "spam. action=list shows registered alerts; action=remove takes an "
        "id/symbol/type. Use for 'alert me if BTC reaches X', 'tell me if my "
        "stop level is reached', 'alert me if NVDA breaks above X', 'watch "
        "for news on the FOMC decision'. Fired alerts reach the user as "
        "factual one-shot notifications — no predictions attached."),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "add, list (default), remove."},
            "type": {"type": "STRING", "description": "Alert type (see description)."},
            "symbol": {"type": "STRING", "description": "Ticker the alert watches."},
            "level": {"type": "NUMBER", "description": "Trigger level: price, % move, or RSI value."},
            "topic": {"type": "STRING", "description": "News alert topic, e.g. 'FOMC decision'."},
            "direction": {"type": "STRING", "description": "For rsi_level: above (default) or below."},
            "multiplier": {"type": "NUMBER", "description": "For volume_spike: x average (default 2.0)."},
            "ref_price": {"type": "NUMBER", "description": "Optional explicit reference price for pct_move."},
            "once": {"type": "BOOLEAN", "description": "Fire once (default true)."},
            "id": {"type": "STRING", "description": "Alert id for action=remove."},
        },
        "required": [],
    },
    "handler": trade_alerts,
}
