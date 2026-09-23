"""
actions/trade_order.py — ANALYZE → PREPARE → SHOW → CONFIRM → SUBMIT →
VERIFY → JOURNAL (spec §12).

prepare builds and SHOWS a ticket with risk math (never submits). submit
routes live orders through core/confirm.py — only the HUD CONFIRM button can
execute them; paper fills directly unless impulse flags forced the gate.
status/cancel inspect prepared tickets (10-minute TTL).
"""
from core.trading import language, orderflow


def trade_order(parameters: dict, player=None, speak=None) -> str:
    p = parameters or {}
    action = str(p.get("action") or "prepare").strip().lower()

    try:
        if action == "prepare":
            r = orderflow.prepare(
                symbol=p.get("symbol"), side=p.get("side"),
                quantity=p.get("quantity"), entry=p.get("entry"),
                stop=p.get("stop"), target=p.get("target"),
                reason=str(p.get("reason") or ""),
                note=str(p.get("note") or ""),
                context=str(p.get("context") or ""))
            if not r.get("ok"):
                return r.get("error", "Order preparation failed.")
            return r["text"]

        if action == "submit":
            oid = str(p.get("order_id") or "").strip()
            if not oid:
                return ("trade_order action=submit needs order_id — prepare the "
                        "order first, show me the ticket, then submit that id "
                        "(the prepare/confirm/show sequence is required).")
            r = orderflow.submit(oid)
            if not r.get("ok"):
                return r.get("error", "Order submission refused.")
            result = r.get("result") or {}
            if r.get("awaiting_confirmation"):
                return r["text"]
            return result.get("text", "Done.")

        if action == "cancel":
            r = orderflow.cancel(str(p.get("order_id") or ""))
            return r.get("text", "No order to cancel.")

        # status
        oid = str(p.get("order_id") or "").strip()
        r = orderflow.status(oid)
        if not r.get("ok"):
            return r.get("error", "No such order.")
        if oid:
            return orderflow.format_prepared([r["order"]])
        return orderflow.format_prepared(r.get("prepared") or [])
    except Exception as e:
        return f"trade_order failed: {e}"


TOOL = {
    "name": "trade_order",
    "description": (
        "Order workflow with an unforgivable-by-the-model confirmation gate. "
        "action=prepare: build and SHOW a ticket (symbol, side, quantity, "
        "entry, stop/invalidation, target, risk/reward, sizing math, guardrail "
        "results) — it never submits anything; defaults to paper mode; live "
        "guardrail violations block preparation. action=submit: requires "
        "order_id of a prepared ticket; LIVE orders are only executed after "
        "the user presses CONFIRM on the on-screen prompt (the model cannot "
        "forge that), and execution refuses honestly when platform automation "
        "is unconfigured — never blind-clicks a real-money ticket. Impulsive-"
        "sounding requests (context= their words) force confirmation even for "
        "paper. action=status / action=cancel: inspect or drop a prepared "
        "ticket (tickets expire in 10 minutes). Use after 'prepare the "
        "order', 'do not submit it yet', 'review the order', 'submit it'. "
        "Always show the user the full ticket before submitting."),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "prepare (default), submit, status, cancel."},
            "symbol": {"type": "STRING", "description": "Ticker symbol."},
            "side": {"type": "STRING", "description": "buy / sell (or long / short)."},
            "quantity": {"type": "NUMBER",
                         "description": "Size in units; omit to auto-size from configured risk."},
            "entry": {"type": "NUMBER", "description": "Entry price (omit for market)."},
            "stop": {"type": "NUMBER", "description": "Stop-loss / invalidation price (required for live)."},
            "target": {"type": "NUMBER", "description": "Target price."},
            "reason": {"type": "STRING", "description": "Reason for entry."},
            "note": {"type": "STRING", "description": "Free-form note (strategy, conditions)."},
            "context": {"type": "STRING",
                        "description": "The user's raw wording when their request sounds impulsive."},
            "order_id": {"type": "STRING", "description": "Prepared ticket id (submit/status/cancel)."},
        },
        "required": [],
    },
    "handler": trade_order,
}
