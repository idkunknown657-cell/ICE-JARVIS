"""
core/trading/paper.py — paper trading (§13): the DEFAULT mode on a fresh
install, and the only mode unless the user explicitly switches to live.

Tracks virtual balance, positions, entries/exits, P/L and history in
config/paper_account.json (personal data, gitignored). Closes flow into the
journal with mode="paper" so paper and live statistics never blend.

Position P/L is plain arithmetic — unrealized() is a pure function, tested
against hand-computed numbers.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path

DEFAULT_BALANCE = 10_000.0
_lock = threading.Lock()


def _path() -> Path:
    from memory.config_manager import CONFIG_DIR
    return CONFIG_DIR / "paper_account.json"


def _load() -> dict:
    try:
        p = _path()
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("balance", DEFAULT_BALANCE)
                data.setdefault("starting_balance", DEFAULT_BALANCE)
                data.setdefault("positions", [])
                data.setdefault("history", [])
                return data
    except Exception:
        pass
    return {"balance": DEFAULT_BALANCE, "starting_balance": DEFAULT_BALANCE,
            "positions": [], "history": [], "created": _now()}


def _save(data: dict) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def status() -> dict:
    d = _load()
    return {"balance": d["balance"], "starting_balance": d.get("starting_balance", DEFAULT_BALANCE),
            "open_positions": len(d["positions"]), "closed_trades": len(d["history"]),
            "created": d.get("created", ""), "mode": "paper"}


def unrealized(pos: dict, price: float) -> dict:
    """{pnl, pnl_pct} for an open position at `price` — pure math (§9)."""
    try:
        entry = float(pos["entry"])
        qty = float(pos["quantity"])
        price = float(price)
    except (KeyError, TypeError, ValueError):
        return {"pnl": None, "pnl_pct": None}
    sign = 1.0 if str(pos.get("side", "long")).lower() in ("long", "buy") else -1.0
    pnl = (price - entry) * qty * sign
    notional = entry * qty
    return {"pnl": pnl,
            "pnl_pct": (pnl / notional * 100.0) if notional else None}


def open_position(symbol: str, side: str, entry, quantity,
                  stop="", target="", note="") -> dict:
    """Open a virtual position (validated, instantly recorded)."""
    sym = str(symbol or "").strip().upper()
    if not sym:
        return {"ok": False, "error": "No symbol given."}
    try:
        entry = float(entry)
        quantity = float(quantity)
    except (TypeError, ValueError):
        return {"ok": False, "error": "entry and quantity must be numbers."}
    if entry <= 0 or quantity <= 0:
        return {"ok": False, "error": "entry and quantity must be positive."}
    side_l = str(side or "long").lower()
    if side_l in ("buy", "long"):
        side_l = "long"
    elif side_l in ("sell", "short"):
        side_l = "short"
    else:
        return {"ok": False, "error": "side must be buy/sell (or long/short)."}

    with _lock:
        d = _load()
        pos = {
            "id": f"P{len(d['history']) + len(d['positions']) + 1:04d}",
            "symbol": sym, "side": side_l, "entry": entry, "quantity": quantity,
            "stop": _f(stop), "target": _f(target), "note": str(note or ""),
            "opened_at": _now(), "mode": "paper",
        }
        d["positions"].append(pos)
        _save(d)
    return {"ok": True, "position": pos, "balance": _load()["balance"]}


def close_position(ref: str, exit_price) -> dict:
    """Close by position id or symbol (latest open). P/L hits the balance and
    the trade lands in the journal as mode='paper'."""
    try:
        exit_price = float(exit_price)
    except (TypeError, ValueError):
        return {"ok": False, "error": "exit_price must be a number."}
    if exit_price <= 0:
        return {"ok": False, "error": "exit_price must be positive."}

    with _lock:
        d = _load()
        pos = None
        for p in d["positions"]:
            if str(p.get("id", "")).lower() == str(ref or "").lower() \
                    or str(p.get("symbol", "")).upper() == str(ref or "").upper():
                pos = p
        if pos is None:
            return {"ok": False, "error": f"No open paper position matching '{ref}'."}
        u = unrealized(pos, exit_price)
        pnl = u["pnl"] or 0.0
        d["balance"] += pnl
        d["positions"].remove(pos)
        closed = dict(pos)
        closed.update({"exit": exit_price, "pnl": pnl, "pnl_pct": u["pnl_pct"],
                       "closed_at": _now(),
                       "result": "win" if pnl > 0 else ("loss" if pnl < 0 else "breakeven")})
        d["history"].append(closed)
        _save(d)

    # Journal entry (mode='paper' — never blended with live).
    try:
        from core.trading import journal
        journal.record(
            mode="paper", asset=pos["symbol"], direction=pos["side"],
            entry=pos["entry"], stop=pos.get("stop"), target=pos.get("target"),
            quantity=pos["quantity"], exit=exit_price, pnl=pnl,
            pnl_pct=u["pnl_pct"], result=closed["result"],
            notes=pos.get("note", ""),
        )
    except Exception:
        pass
    return {"ok": True, "closed": closed, "balance": d["balance"]}


def positions() -> list[dict]:
    return _load()["positions"]


def history(limit: int = 20) -> list[dict]:
    return _load()["history"][-max(1, int(limit)):]


def format_status(st: dict) -> str:
    lines = [
        "PAPER TRADING — virtual money, no real orders.",
        f"  Virtual balance: ${st['balance']:,.2f} (started ${st['starting_balance']:,.2f})",
        f"  Open positions: {st['open_positions']}",
        f"  Closed trades: {st['closed_trades']}",
    ]
    for p in _load()["positions"]:
        lines.append(f"    {p['id']}  {p['side'].upper()} {_qty(p['quantity'])} {p['symbol']} "
                     f"@ {p['entry']}  (opened {p['opened_at']})")
        if p.get("stop"):
            lines.append(f"        stop {p['stop']}" + (f"  target {p['target']}" if p.get("target") else ""))
    if st["closed_trades"]:
        pnls = [h.get("pnl") or 0.0 for h in _load()["history"]]
        wins = sum(1 for x in pnls if x > 0)
        lines.append(f"  Closed P/L total: ${sum(pnls):,.2f}   "
                     f"win rate {wins / len(pnls) * 100:.0f}% over {len(pnls)} trades")
    lines.append("  Switch to live only deliberately: trading_settings action=set key=mode value=live")
    return "\n".join(lines)


def _qty(v):
    try:
        f = float(v)
        return str(int(f)) if f == int(f) else f"{f:.8f}".rstrip("0").rstrip(".")
    except Exception:
        return str(v)


def _f(v):
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None
