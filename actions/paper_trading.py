"""
actions/paper_trading.py — virtual trading account tool (spec §13).
The default mode on fresh installs; virtual balance, positions, entries,
exits, P/L and history. Closes are journaled with mode='paper' so paper and
live statistics never blend.
"""
from core.trading import language, paper


def _hist_lines(rows) -> str:
    if not rows:
        return "No closed paper trades yet."
    lines = []
    for h in reversed(rows):
        pnl = h.get("pnl")
        lines.append(
            f"  {h.get('closed_at', '')[:16]}  {h.get('side', '').upper()} "
            f"{h.get('symbol')}  in {h.get('entry')} → out {h.get('exit')}  "
            f"P/L ${pnl:,.2f}" if pnl is not None else
            f"  {h.get('closed_at', '')[:16]}  {h.get('symbol')}  (no P/L recorded)")
    return "\n".join(lines)


def paper_trading(parameters: dict, player=None, speak=None) -> str:
    p = parameters or {}
    action = str(p.get("action") or "status").strip().lower()
    try:
        if action == "open":
            r = paper.open_position(
                symbol=p.get("symbol"), side=p.get("side"),
                entry=p.get("entry"), quantity=p.get("quantity"),
                stop=p.get("stop"), target=p.get("target"),
                note=str(p.get("note") or ""))
            if not r.get("ok"):
                return f"Paper open refused: {r.get('error')}"
            pos = r["position"]
            return language.ensure_safe(
                f"PAPER position opened — {pos['side'].upper()} "
                f"{pos['quantity']:g} {pos['symbol']} @ {pos['entry']:g}"
                + (f"  stop {pos['stop']:g}" if pos.get("stop") else "")
                + (f"  target {pos['target']:g}" if pos.get("target") else "")
                + f"\n  id {pos['id']}   virtual balance ${r['balance']:,.2f}"
                  f"\n  This is PAPER (virtual money) — no real order exists.")

        if action == "close":
            ref = str(p.get("symbol") or p.get("order_id") or "").strip()
            if not ref:
                return "paper_trading action=close needs the symbol (or order_id)."
            r = paper.close_position(ref, p.get("exit") or p.get("exit_price"))
            if not r.get("ok"):
                return f"Paper close refused: {r.get('error')}"
            c = r["closed"]
            return language.ensure_safe(
                f"PAPER closed — {c['side'].upper()} {c['quantity']:g} {c['symbol']} "
                f"{c['entry']:g} → {c['exit']:g}  P/L ${c['pnl']:,.2f} "
                f"({c['pnl_pct']:+.2f}%)\n  virtual balance ${r['balance']:,.2f}\n"
                f"  journaled as mode=paper, result={c['result']}")

        if action == "history":
            try:
                limit = int(p.get("limit") or 20)
            except (TypeError, ValueError):
                limit = 20
            return ("PAPER TRADING history (virtual money):\n"
                    + _hist_lines(paper.history(limit=limit)))

        return paper.format_status(paper.status())
    except Exception as e:
        return f"paper_trading failed: {e}"


TOOL = {
    "name": "paper_trading",
    "description": (
        "Virtual (paper) trading account — the DEFAULT mode: no real money, "
        "no real orders. action=status (default): virtual balance, open "
        "positions, closed-trade stats. action=open: open a virtual position "
        "(symbol, side, entry, quantity, optional stop/target/note). "
        "action=close: close at exit price — P/L hits the virtual balance and "
        "the trade is journaled as mode=paper. action=history: recent closed "
        "trades. Use for 'prepare a paper trade', 'open a paper position', "
        "'close my paper BTC trade', 'what's my paper balance'. Always make "
        "clear the result is paper, never live. Sizing still respects the "
        "user's configured risk — show the math."),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "status (default), open, close, history."},
            "symbol": {"type": "STRING", "description": "Ticker symbol (and position to close)."},
            "side": {"type": "STRING", "description": "buy / sell for action=open."},
            "entry": {"type": "NUMBER", "description": "Entry price for action=open."},
            "quantity": {"type": "NUMBER", "description": "Size for action=open."},
            "exit": {"type": "NUMBER", "description": "Exit price for action=close."},
            "stop": {"type": "NUMBER", "description": "Stop price for action=open."},
            "target": {"type": "NUMBER", "description": "Target price for action=open."},
            "note": {"type": "STRING", "description": "Note/reason for the trade."},
            "limit": {"type": "INTEGER", "description": "How many history rows (default 20)."},
        },
        "required": [],
    },
    "handler": paper_trading,
}
