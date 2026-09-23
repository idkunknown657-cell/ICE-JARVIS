"""
actions/backtest_strategy.py — rule-based backtesting tool (spec §15).
Feeds user/model rule strings into core.trading.backtest and returns the full
stat block with the historical-results disclaimer.
"""
from core.trading import backtest


def _rules(raw) -> list[str]:
    if isinstance(raw, (list, tuple)):
        return [str(r).strip() for r in raw if str(r).strip()]
    return [r.strip() for r in str(raw or "").replace("\n", ",").split(",") if r.strip()]


def _num(p, key):
    v = p.get(key)
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def backtest_strategy(parameters: dict, player=None, speak=None) -> str:
    p = parameters or {}
    sym = str(p.get("symbol") or "").strip().upper()
    if not sym:
        return "backtest_strategy: a symbol is required (e.g. symbol=SPY)."
    entry = _rules(p.get("entry_rules"))
    if not entry:
        return ("backtest_strategy: entry_rules required — rule-based strategies "
                "only, e.g. entry_rules=['close > ema(200)', 'rsi(14) < 40'].")
    exit_rules = _rules(p.get("exit_rules"))
    try:
        result = backtest.run_backtest(
            symbol=sym,
            timeframe=str(p.get("timeframe") or "1D"),
            period=str(p.get("period") or "1y"),
            side=str(p.get("side") or "long"),
            entry_rules=entry, exit_rules=exit_rules,
            stop_loss_pct=_num(p, "stop_loss_pct") or 0.0,
            take_profit_pct=_num(p, "take_profit_pct") or 0.0,
            fees_pct=_num(p, "fees_pct") or 0.0,
            slippage_pct=_num(p, "slippage_pct") or 0.0,
            fixed_quantity=_num(p, "fixed_quantity"),
            risk_pct=_num(p, "risk_pct"),
        )
        return backtest.format_result(result)
    except Exception as e:
        return f"backtest_strategy failed: {e}"


TOOL = {
    "name": "backtest_strategy",
    "description": (
        "Backtest a rule-based strategy against historical data (spec §15). "
        "Rules are ANDed strings, e.g. 'close > ema(200)', 'rsi(14) < 30', "
        "'macd_hist > 0', 'close cross_above sma(20)', 'volume > "
        "avg_volume(20)*2'; operands: close/open/high/low/volume/sma(n)/ema(n)/"
        "rsi(n)/atr(n)/avg_volume(n)/macd_hist/bb_upper/bb_lower or a number; "
        "operators: > < >= <= cross_above cross_below. Entries fill on the "
        "next bar's open (no future leak). Configure period/timeframe, "
        "stop_loss_pct, take_profit_pct, fees_pct, slippage_pct, and sizing "
        "via fixed_quantity or risk_pct. Reports total trades, net return, "
        "max drawdown, win rate, average trade, profit factor, per-trade "
        "Sharpe, and winning/losing streaks — always with the caveat that "
        "historical performance does not guarantee future results. Never "
        "present a backtest as evidence an outcome will repeat."),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "symbol": {"type": "STRING", "description": "Ticker to test, e.g. SPY, BTC-USD."},
            "timeframe": {"type": "STRING", "description": "Bar size: 1m/5m/15m/30m/1h/4h/1D (default)/1W/1M."},
            "period": {"type": "STRING", "description": "Lookback, e.g. 6mo, 1y (default), 2y, 5y."},
            "side": {"type": "STRING", "description": "long (default) or short."},
            "entry_rules": {"type": "ARRAY", "items": {"type": "STRING"},
                            "description": "ANDed entry conditions."},
            "exit_rules": {"type": "ARRAY", "items": {"type": "STRING"},
                           "description": "ANDed exit conditions (in addition to stop/take-profit)."},
            "stop_loss_pct": {"type": "NUMBER", "description": "Stop distance as % of entry."},
            "take_profit_pct": {"type": "NUMBER", "description": "Target distance as % of entry."},
            "fees_pct": {"type": "NUMBER", "description": "Fees per side as % of notional."},
            "slippage_pct": {"type": "NUMBER", "description": "Slippage per fill as % adverse."},
            "fixed_quantity": {"type": "NUMBER", "description": "Fixed units per trade."},
            "risk_pct": {"type": "NUMBER",
                         "description": "Risk % of configured account per trade (alternative sizing)."},
        },
        "required": ["symbol", "entry_rules"],
    },
    "handler": backtest_strategy,
}
