"""
actions/trading_journal.py — trade journal tool (spec §14).
Record and summarize trades (paper + live, always labeled), with the
past-results-don't-guarantee-future-performance disclaimer attached to every
summary.
"""
from core.trading import journal


def _row(t: dict) -> str:
    def n(v, d="-"):
        return d if v in (None, "") else f"{v:g}" if isinstance(v, (int, float)) else str(v)
    pnl = t.get("pnl")
    pnl_s = f"{pnl:+,.2f}" if isinstance(pnl, (int, float)) else "-"
    return (f"  {t.get('date', '')}  [{t.get('mode', '?')}] {t.get('asset', '')} "
            f"{t.get('direction', '')}  in {n(t.get('entry'))} out {n(t.get('exit'))} "
            f"size {n(t.get('quantity'))}  P/L {pnl_s}  ({t.get('result', '?')})"
            + (f"  reason: {t['reason']}" if t.get("reason") else ""))


def trading_journal(parameters: dict, player=None, speak=None) -> str:
    p = parameters or {}
    action = str(p.get("action") or "summary").strip().lower()
    try:
        if action == "list":
            rows = journal.trades(month=p.get("month"), since=p.get("since"),
                                  until=p.get("until"), asset=p.get("asset"),
                                  mode=p.get("mode"),
                                  include_open=str(p.get("include_open") or "").lower()
                                  in ("1", "true", "yes"))
            if not rows:
                return "No journal entries match that filter."
            return "JOURNAL ENTRIES:\n" + "\n".join(_row(t) for t in rows[-50:])

        if action == "record":
            if not p.get("symbol") and not p.get("asset"):
                return "trading_journal action=record needs a symbol/asset."
            entry = journal.record(
                mode=str(p.get("mode") or "paper"), asset=p.get("asset") or p.get("symbol"),
                direction=p.get("direction") or p.get("side"),
                entry=p.get("entry"), stop=p.get("stop"), target=p.get("target"),
                quantity=p.get("quantity"), exit=p.get("exit"), pnl=p.get("pnl"),
                pnl_pct=p.get("pnl_pct"), reason=str(p.get("reason") or ""),
                conditions=str(p.get("conditions") or ""),
                strategy=str(p.get("strategy") or ""), notes=str(p.get("notes") or ""))
            if entry.get("error"):
                return f"Journal record failed: {entry['error']}"
            return (f"Journaled {entry['asset']} {entry['direction']} "
                    f"({entry['mode']}) on {entry['date']} — id {entry['id']}.")

        # summary — "show me my trading performance this month"
        d = journal.summary(month=p.get("month"), since=p.get("since"),
                            until=p.get("until"), mode=p.get("mode"))
        return journal.format_summary(d)
    except Exception as e:
        return f"trading_journal failed: {e}"


TOOL = {
    "name": "trading_journal",
    "description": (
        "Trade journal: record trades and summarize historical performance. "
        "action=summary (default): total trades, winning/losing trades, win "
        "rate, average win, average loss, maximum drawdown, average "
        "risk/reward, total P/L — filter with month='YYYY-MM' (e.g. 'show me "
        "my trading performance this month'), since/until dates, or "
        "mode=paper|live. Summaries describe history only: never turn these "
        "statistics into claims about future performance. action=list: the "
        "individual entries. action=record: manually add a trade with its "
        "reason, market conditions, strategy and notes (paper_trading and "
        "live fills journal themselves — this is for manual entries)."),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "summary (default), list, record."},
            "month": {"type": "STRING", "description": "Filter by month, YYYY-MM."},
            "since": {"type": "STRING", "description": "Filter start date, YYYY-MM-DD."},
            "until": {"type": "STRING", "description": "Filter end date, YYYY-MM-DD."},
            "mode": {"type": "STRING", "description": "paper or live — never blended."},
            "asset": {"type": "STRING", "description": "Filter/record symbol."},
            "symbol": {"type": "STRING", "description": "Alias of asset for action=record."},
            "direction": {"type": "STRING", "description": "buy/sell for action=record."},
            "entry": {"type": "NUMBER", "description": "Entry price."},
            "stop": {"type": "NUMBER", "description": "Stop price."},
            "target": {"type": "NUMBER", "description": "Target price."},
            "quantity": {"type": "NUMBER", "description": "Position size."},
            "exit": {"type": "NUMBER", "description": "Exit price."},
            "pnl": {"type": "NUMBER", "description": "Realized P/L in currency."},
            "reason": {"type": "STRING", "description": "Reason for entry."},
            "conditions": {"type": "STRING", "description": "Market conditions at entry."},
            "strategy": {"type": "STRING", "description": "Strategy used."},
            "notes": {"type": "STRING", "description": "User notes."},
            "include_open": {"type": "BOOLEAN", "description": "Include open trades in action=list."},
        },
        "required": [],
    },
    "handler": trading_journal,
}
