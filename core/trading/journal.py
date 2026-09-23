"""
core/trading/journal.py — the trade journal (§14).

Records paper AND live trades with an explicit `mode` field so the two never
blur, and summarizes history with the past≠future disclaimer bolted on: these
are facts about what happened, never a forecast (§16).

Storage: config/trading_journal.json — personal financial data, gitignored.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path

_lock = threading.Lock()


def _path() -> Path:
    from memory.config_manager import CONFIG_DIR
    return CONFIG_DIR / "trading_journal.json"


def _load() -> dict:
    try:
        p = _path()
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("trades"), list):
                return data
    except Exception:
        pass
    return {"trades": []}


def _save(data: dict) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)


def record(**fields) -> dict:
    """Append one trade record. Accepts any subset of the §14 fields; missing
    ones stay None/empty rather than being guessed. Never raises."""
    try:
        now = datetime.now()
        entry = {
            "id": fields.get("id") or f"J{int(now.timestamp() * 1000)}",
            "ts": now.isoformat(timespec="seconds"),
            "date": now.strftime("%Y-%m-%d %H:%M"),
            "mode": fields.get("mode", "paper"),        # "paper" | "live"
            "asset": fields.get("asset") or fields.get("symbol") or "",
            "direction": fields.get("direction") or fields.get("side") or "",
            "entry": _num(fields.get("entry")),
            "stop": _num(fields.get("stop")),
            "target": _num(fields.get("target")),
            "quantity": _num(fields.get("quantity") or fields.get("size")),
            "risk": _num(fields.get("risk")),           # planned $ risk
            "exit": _num(fields.get("exit")),
            "pnl": _num(fields.get("pnl")),
            "pnl_pct": _num(fields.get("pnl_pct")),
            "result": fields.get("result") or ("open" if fields.get("exit") in (None, "")
                                               else "unknown"),
            "reason": str(fields.get("reason") or ""),
            "conditions": str(fields.get("conditions") or ""),
            "strategy": str(fields.get("strategy") or ""),
            "notes": str(fields.get("notes") or ""),
            "rr_planned": _num(fields.get("rr_planned")),
            "verified": fields.get("verified"),          # fill verified? None if n/a
        }
        with _lock:
            data = _load()
            data["trades"].append(entry)
            _save(data)
        return entry
    except Exception as e:
        return {"error": str(e)}


def trades(month: str | None = None, since: str | None = None,
           until: str | None = None, asset: str | None = None,
           mode: str | None = None, include_open: bool = False) -> list[dict]:
    """Filter records. `month` is 'YYYY-MM'. since/until are 'YYYY-MM-DD'."""
    out = []
    try:
        for t in _load()["trades"]:
            if not include_open and t.get("result") == "open":
                continue
            ts = str(t.get("ts") or "")
            if month and not ts.startswith(str(month)):
                continue
            if since and ts[:10] < str(since):
                continue
            if until and ts[:10] > str(until):
                continue
            if asset and str(t.get("asset", "")).upper() != str(asset).upper():
                continue
            if mode and str(t.get("mode", "")) != str(mode):
                continue
            out.append(t)
    except Exception:
        return []
    return out


def stats_today() -> dict:
    """{trades, pnl} for today — feeds the daily-loss/trade-count guardrails."""
    today = datetime.now().strftime("%Y-%m-%d")
    rows = trades(since=today, until=today)
    return {"trades": len(rows),
            "pnl": sum(float(r.get("pnl") or 0.0) for r in rows)}


def summary(month: str | None = None, since: str | None = None,
            until: str | None = None, mode: str | None = None) -> dict:
    """§14 statistics over closed trades. Max drawdown is peak-to-trough on the
    cumulative P/L curve; avg_rr uses planned R:R where recorded."""
    rows = trades(month=month, since=since, until=until, mode=mode)
    pnls = [float(r.get("pnl") or 0.0) for r in rows]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    rrs = [float(r.get("rr_planned")) for r in rows if r.get("rr_planned")]

    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnls:
        cum += p
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)

    open_count = sum(1 for t in _load()["trades"] if t.get("result") == "open")

    return {
        "total_trades": len(rows),
        "open_trades": open_count,
        "winning_trades": len(wins),
        "losing_trades": len(losses),
        "win_rate": (len(wins) / len(rows) * 100.0) if rows else None,
        "avg_win": (sum(wins) / len(wins)) if wins else None,
        "avg_loss": (sum(losses) / len(losses)) if losses else None,
        "net_pnl": sum(pnls),
        "max_drawdown": max_dd,
        "avg_rr": (sum(rrs) / len(rrs)) if rrs else None,
        "best": max(pnls) if pnls else None,
        "worst": min(pnls) if pnls else None,
        "filter": {"month": month, "since": since, "until": until, "mode": mode},
    }


def format_summary(d: dict) -> str:
    """Plain-text §14 report with the mandatory past≠future disclaimer."""
    from core.trading.language import DISCLAIMER_JOURNAL
    f = d.get("filter") or {}
    scope = []
    if f.get("month"):
        scope.append(f"month {f['month']}")
    if f.get("since") or f.get("until"):
        scope.append(f"{f.get('since') or 'start'} → {f.get('until') or 'today'}")
    if f.get("mode"):
        scope.append(f"mode: {f['mode']}")
    head = "TRADING JOURNAL SUMMARY" + (f" ({', '.join(scope)})" if scope else "")

    def money(v):
        if v is None:
            return "n/a"
        return f"-${abs(v):,.2f}" if v < 0 else f"${v:,.2f}"

    lines = [head]
    if d["total_trades"] == 0:
        lines.append("  No closed trades match this filter yet.")
    else:
        lines.append(f"  Total trades: {d['total_trades']} (open: {d['open_trades']})")
        lines.append(f"  Winning trades: {d['winning_trades']}")
        lines.append(f"  Losing trades: {d['losing_trades']}")
        lines.append(f"  Win rate: {d['win_rate']:.1f}%" if d["win_rate"] is not None else "  Win rate: n/a")
        lines.append(f"  Average win: {money(d['avg_win'])}")
        lines.append(f"  Average loss: {money(d['avg_loss'])}")
        lines.append(f"  Maximum drawdown: {money(d['max_drawdown'])}")
        lines.append(f"  Average risk/reward (planned): "
                     + (f"1:{d['avg_rr']:.2f}" if d["avg_rr"] is not None else "n/a"))
        lines.append(f"  Total P/L: {money(d['net_pnl'])}")
    lines.append(f"  {DISCLAIMER_JOURNAL}")
    return "\n".join(lines)


def _num(v):
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None
