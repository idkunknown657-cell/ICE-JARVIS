"""
core/trading/backtest.py — rule-based strategy backtesting (§15).

A deliberately small rule language (AND-ed conditions, evaluated bar by bar,
entries taken on the NEXT bar's open so nothing leaks the future), with fees,
slippage, stop-loss-first intrabar assumption (pessimistic on ties), and the
full §15 stat block. The disclaimer travels with every result: historical
performance does not guarantee future results.

Rule grammar (strings the model/user supplies):
    "close > ema(200)", "rsi(14) < 30", "macd_hist > 0",
    "close cross_above ema(50)", "volume > 2000000",
    "close cross_below sma(20)", "atr(14) > 3"
Operands: close, open, high, low, volume, sma(n), ema(n), rsi(n), atr(n),
macd_hist, bb_upper, bb_lower, avg_volume(n), or a number.
Operators: >, <, >=, <=, cross_above, cross_below.
"""
from __future__ import annotations

import math
import re

import numpy as np

from core.trading import indicators as ind

_RULE_RE = re.compile(r"^\s*(.+?)\s*(>=|<=|cross_above|cross_below|>|<)\s*(.+?)\s*$")
_CALL_RE = re.compile(r"^(sma|ema|rsi|atr|avg_volume)\((\d+)\)$")
_SIMPLE = ("close", "open", "high", "low", "volume", "macd_hist",
           "bb_upper", "bb_lower")


def _build_arrays(candles: dict) -> dict:
    b = candles["bars"]
    arr = {k: np.array([x[k] for x in b], dtype=float)
           for k in ("o", "h", "l", "c", "v")}
    arr["open"], arr["high"], arr["low"] = arr["o"], arr["h"], arr["l"]
    arr["close"], arr["volume"] = arr["c"], arr["v"]
    arr["sma"], arr["ema"], arr["rsi"], arr["atr"], arr["avg_volume"] = {}, {}, {}, {}, {}
    return arr


def _operand(token: str, arr: dict):
    """Resolve one side of a rule to (values_array | constant). Raises ValueError
    with the supported list — honest failure beats a silently-wrong backtest."""
    tok = str(token).strip()
    try:
        return float(tok)
    except ValueError:
        pass
    low = tok.lower()
    if low in ("close", "open", "high", "low", "volume"):
        return arr[low]
    if low == "macd_hist":
        return ind.macd(arr["close"])[2]
    if low == "bb_upper":
        return ind.bollinger(arr["close"])[1]
    if low == "bb_lower":
        return ind.bollinger(arr["close"])[2]
    m = _CALL_RE.match(low)
    if m:
        fn, n = m.group(1), int(m.group(2))
        cache = arr[fn].setdefault if False else None
        store = arr[fn]
        if n not in store:
            if fn == "sma":
                store[n] = ind.sma(arr["close"], n)
            elif fn == "ema":
                store[n] = ind.ema(arr["close"], n)
            elif fn == "rsi":
                store[n] = ind.rsi(arr["close"], n)
            elif fn == "atr":
                store[n] = ind.atr(arr["high"], arr["low"], arr["close"], n)
            elif fn == "avg_volume":
                store[n] = ind.sma(arr["volume"], n)
        return store[n]
    raise ValueError(
        f"Unknown rule operand '{tok}'. Supported: close, open, high, low, "
        f"volume, sma(n), ema(n), rsi(n), atr(n), avg_volume(n), macd_hist, "
        f"bb_upper, bb_lower, or a number.")


def _parse(rules: list[str], arr: dict) -> list[tuple]:
    parsed = []
    for rule in rules or []:
        m = _RULE_RE.match(str(rule))
        if not m:
            raise ValueError(
                f"Cannot parse rule '{rule}'. Use e.g. 'close > ema(200)' or "
                f"'rsi(14) cross_above 30' (operands: close/open/high/low/volume/"
                f"sma(n)/ema(n)/rsi(n)/atr(n)/avg_volume(n)/macd_hist/bb_upper/"
                f"bb_lower or a number; operators: > < >= <= cross_above cross_below).")
        a, op, b = m.group(1), m.group(2), m.group(3)
        parsed.append((_operand(a, arr), op, _operand(b, arr), str(rule)))
    return parsed


def _val(v, i):
    if isinstance(v, (int, float, np.floating)):
        return float(v)
    return float(v[i]) if i < len(v) and np.isfinite(v[i]) else None


def _cross(a, b, i) -> bool:
    if i < 1:
        return False
    p, c = (_val(a, i - 1), _val(b, i - 1)), (_val(a, i), _val(b, i))
    if None in p or None in c:
        return False
    return p[0] <= p[1] and c[0] > c[1]


def _eval(cond, i) -> bool:
    a, op, b, _rule = cond
    if op == "cross_above":
        return _cross(a, b, i)
    if op == "cross_below":
        return _cross(b, a, i)
    x, y = _val(a, i), _val(b, i)
    if x is None or y is None:
        return False
    return {"<": x < y, ">": x > y, "<=": x <= y, ">=": x >= y}[op]


def run_backtest(symbol: str, timeframe: str = "1D", period: str = "1y",
                 side: str = "long", entry_rules: list[str] | None = None,
                 exit_rules: list[str] | None = None,
                 stop_loss_pct: float = 0.0, take_profit_pct: float = 0.0,
                 fees_pct: float = 0.0, slippage_pct: float = 0.0,
                 fixed_quantity: float | None = None,
                 risk_pct: float | None = None) -> dict:
    """Run one backtest. Returns stats + the mandatory §15 disclaimer, or an
    honest {error} — never fabricated numbers."""
    if not entry_rules:
        return {"error": "No entry rules given. A strategy needs at least one "
                         "entry rule (e.g. 'close > ema(200)')."}
    try:
        from core.trading import data as tdata
        candles = tdata.get_candles(symbol, timeframe, period=period)
    except Exception as e:
        return {"error": f"Could not load historical data: {e}"}

    bars = candles["bars"]
    if len(bars) < 30:
        return {"error": f"Only {len(bars)} bars available on {timeframe} — too few "
                         f"to backtest honestly. Use a longer period or lower timeframe."}

    arr = _build_arrays({"bars": bars})
    try:
        entry_c = _parse(entry_rules, arr)
        exit_c = _parse(exit_rules or [], arr)
    except ValueError as e:
        return {"error": str(e)}

    side_l = str(side or "long").lower()
    if side_l in ("buy", "long"):
        side_l = "long"
    elif side_l in ("sell", "short"):
        side_l = "short"
    else:
        return {"error": "side must be 'long' or 'short'."}

    sl = abs(float(stop_loss_pct or 0)) / 100.0
    tp = abs(float(take_profit_pct or 0)) / 100.0
    fees = abs(float(fees_pct or 0)) / 100.0
    slip = abs(float(slippage_pct or 0)) / 100.0

    qty_default = float(fixed_quantity) if fixed_quantity else None
    if qty_default is None:
        rp = float(risk_pct) if risk_pct else None
        if rp:
            from core.trading import settings
            account = float(settings.get("account_size") or 0)
            if account > 0 and sl > 0:
                qty_default = (account * rp / 100.0) / (bars[0]["c"] * sl)
        if qty_default is None:
            qty_default = 1.0

    trades: list[dict] = []
    equity: list[float] = []
    pos = None   # {"entry","qty","dir_i"}
    realized = 0.0

    def _close(idx: int, price: float, why: str):
        nonlocal pos, realized
        entry_p, qty, dirn = pos["entry"], pos["qty"], pos["side"]
        sign = 1.0 if dirn == "long" else -1.0
        gross = (price - entry_p) * qty * sign
        costs = (entry_p * qty + price * qty) * fees
        pnl = gross - costs
        realized += pnl
        trades.append({"bar": idx, "entry": entry_p, "exit": price,
                       "qty": qty, "pnl": pnl, "why": why,
                       "pnl_pct": (pnl / (entry_p * qty) * 100.0) if entry_p * qty else 0.0})
        pos = None

    for i in range(1, len(bars)):
        b = bars[i]
        if pos is not None:
            hit_stop = hit_target = False
            if sl > 0:
                stop_px = pos["entry"] * (1 - sl) if pos["side"] == "long" else pos["entry"] * (1 + sl)
                hit_stop = (b["l"] <= stop_px) if pos["side"] == "long" else (b["h"] >= stop_px)
            if tp > 0:
                tp_px = pos["entry"] * (1 + tp) if pos["side"] == "long" else pos["entry"] * (1 - tp)
                hit_target = (b["h"] >= tp_px) if pos["side"] == "long" else (b["l"] <= tp_px)
            if hit_stop:
                stop_px = pos["entry"] * (1 - sl) if pos["side"] == "long" else pos["entry"] * (1 + sl)
                _close(i, stop_px, "stop-loss")     # pessimistic: stop first on ties
                continue
            if hit_target:
                tp_px = pos["entry"] * (1 + tp) if pos["side"] == "long" else pos["entry"] * (1 - tp)
                _close(i, tp_px, "take-profit")
                continue
            if exit_c and all(_eval(c, i) for c in exit_c):
                _close(i, b["c"] * (1 - slip if pos["side"] == "long" else 1 + slip), "exit rule")
                continue
        else:
            if all(_eval(c, i) for c in entry_c) and i + 1 < len(bars):
                nxt = bars[i + 1]
                fill = nxt["o"] * (1 + slip if side_l == "long" else 1 - slip)
                if fill > 0 and qty_default > 0:
                    pos = {"entry": fill, "qty": qty_default, "side": side_l}
        equity.append(realized + (unreal := (0.0 if pos is None else
                     ((b["c"] - pos["entry"]) * pos["qty"] * (1 if pos["side"] == "long" else -1)))))

    if pos is not None:                       # force-close at the last close
        _close(len(bars) - 1, bars[-1]["c"], "end of data")

    pnls = [t["pnl"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))

    peak, max_dd = 0.0, 0.0
    cum = 0.0
    for e in equity:
        cum = e
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)

    max_loss_streak = max_win_streak = cur_loss = cur_win = 0
    for p in pnls:
        cur_loss = cur_loss + 1 if p < 0 else 0
        cur_win = cur_win + 1 if p > 0 else 0
        max_loss_streak = max(max_loss_streak, cur_loss)
        max_win_streak = max(max_win_streak, cur_win)

    returns = [t["pnl_pct"] for t in trades]
    sharpe = None
    if len(returns) >= 2:
        sd = float(np.std(returns, ddof=0))
        if sd > 0:
            sharpe = float(np.mean(returns)) / sd * math.sqrt(len(returns))

    from core.trading.language import DISCLAIMER_BACKTEST
    if not trades:
        return {"symbol": symbol, "timeframe": timeframe, "period": period,
                "side": side_l, "bars": len(bars), "total_trades": 0,
                "message": "No trades matched the rules over this period — "
                           "that is a result worth knowing, not a number to dress up.",
                "disclaimer": DISCLAIMER_BACKTEST}

    starting = qty_default * bars[0]["c"]
    return {
        "symbol": symbol, "timeframe": timeframe, "period": period,
        "side": side_l, "bars": len(bars),
        "total_trades": len(trades),
        "net_pnl": sum(pnls),
        "net_return_pct": (sum(pnls) / starting * 100.0) if starting else None,
        "max_drawdown": max_dd,
        "win_rate": len(wins) / len(pnls) * 100.0,
        "avg_trade": sum(pnls) / len(pnls),
        "profit_factor": (gross_win / gross_loss) if gross_loss else None,
        "sharpe": sharpe,
        "max_losing_streak": max_loss_streak,
        "max_winning_streak": max_win_streak,
        "exit_reasons": _count(t["why"] for t in trades),
        "sample_trades": trades[:10],
        "costs_note": f"fees {fees_pct:g}%/side, slippage {slippage_pct:g}% — applied",
        "disclaimer": DISCLAIMER_BACKTEST,
    }


def _count(items) -> dict:
    out: dict = {}
    for it in items:
        out[it] = out.get(it, 0) + 1
    return out


def format_result(d: dict) -> str:
    if d.get("error"):
        return f"Backtest not run: {d['error']}"
    if d.get("total_trades") == 0:
        return (f"{d['symbol']} {d['timeframe']} ({d['period']}, {d['side']}): "
                f"{d['message']}\n{d['disclaimer']}")
    def money(v):
        return "n/a" if v is None else f"${v:,.2f}"
    lines = [
        f"BACKTEST — {d['symbol']} {d['timeframe']} over {d['period']} ({d['side']})",
        f"  Bars: {d['bars']}   Trades: {d['total_trades']}",
        f"  Net P/L: {money(d['net_pnl'])}"
        + (f" ({d['net_return_pct']:+.2f}%)" if d.get('net_return_pct') is not None else ""),
        f"  Max drawdown: {money(d['max_drawdown'])}",
        f"  Win rate: {d['win_rate']:.1f}%   Avg trade: {money(d['avg_trade'])}",
        f"  Profit factor: " + (f"{d['profit_factor']:.2f}" if d['profit_factor'] is not None else "n/a (no losing trades)"),
        f"  Sharpe (per-trade, not annualised): " + (f"{d['sharpe']:.2f}" if d.get('sharpe') is not None else "n/a"),
        f"  Losing streak (max): {d['max_losing_streak']}   Winning streak (max): {d['max_winning_streak']}",
        f"  Exits: " + ", ".join(f"{k} ×{v}" for k, v in d['exit_reasons'].items()),
        f"  Costs: {d['costs_note']}",
        f"  {d['disclaimer']}",
    ]
    return "\n".join(lines)
