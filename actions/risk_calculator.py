"""
actions/risk_calculator.py — position sizing + trade guardrail checks
(spec §5/§18/§19).

size: the Maximum Dollar Risk / Distance to Stop calculation, always shown
long-hand. check: runs the user's configured limits and reports violations as
stops with arithmetic. Impulse phrasing in `context` triggers the calm
numbers-first response and forces the confirmation path downstream.
"""
from core.trading import language, risk, settings


def risk_calculator(parameters: dict, player=None, speak=None) -> str:
    p = parameters or {}
    action = str(p.get("action") or "size").strip().lower()
    context = str(p.get("context") or "")
    flags = risk.impulse_flags(context)

    if action == "check":
        result = risk.check_trade(
            symbol=str(p.get("symbol") or ""),
            side=str(p.get("side") or ""),
            entry=p.get("entry"), stop=p.get("stop"),
            quantity=p.get("quantity"), target=p.get("target"))
        text = risk.format_check(result, "TRADE RISK CHECK (configured limits)")
        if flags:
            text += "\n\n" + risk.impulse_response(flags)
        return language.ensure_safe(text)

    # action == "size"
    acct = p.get("account")
    rp = p.get("risk_pct")
    try:
        acct = float(acct) if acct not in (None, "") else float(settings.get("account_size") or 0)
    except (TypeError, ValueError):
        acct = 0.0
    try:
        rp = float(rp) if rp not in (None, "") else float(settings.get("risk_per_trade_pct") or 1)
    except (TypeError, ValueError):
        rp = 1.0

    entry = p.get("entry")
    stop = p.get("stop")
    if entry in (None, "") or stop in (None, ""):
        msg = ("risk_calculator action=size needs entry and stop prices — "
               "position size = maximum dollar risk / distance to stop.")
        if flags:
            return language.ensure_safe(msg + "\n\n" + risk.impulse_response(flags, acct or None, rp))
        return msg
    if acct <= 0:
        msg = ("Account size is not configured, so dollar sizing cannot run. "
               "Pass account=10000 explicitly, or set account_size once via "
               "trading_settings so every analysis can size positions.")
        if flags:
            return language.ensure_safe(msg + "\n\n" + risk.impulse_response(flags, None, rp))
        return msg

    size = risk.position_size(acct, rp, entry, stop)
    text = "POSITION SIZE CALCULATION (Maximum Dollar Risk / Distance to Stop)\n" \
        + risk.format_size(size)
    if flags:
        text += "\n\n" + risk.impulse_response(flags, acct, rp)
    return language.ensure_safe(text)


TOOL = {
    "name": "risk_calculator",
    "description": (
        "Position sizing and trade risk checks. action=size (default): "
        "Position Size = Maximum Dollar Risk / Distance to Stop, shown "
        "long-hand — e.g. account $10,000 at 1% risk → max risk $100; entry "
        "$100, stop $98 → $2 risk per share → 50 shares. Uses configured "
        "account_size/risk_per_trade_pct when account/risk_pct are omitted. "
        "action=check: pre-flight guardrails against the user's configured "
        "limits (position size, stop presence, account risk, leverage cap, "
        "daily loss limit, daily trade cap, existing/correlated exposure) — "
        "violations STOP the trade and explain the conflict with numbers; "
        "never bypass or raise the user's limits. If the user's wording "
        "sounds impulsive ('all in', 'double my position', 'recover my "
        "losses', 'maximum leverage'), pass their exact words in context=: "
        "the reply then leads with the arithmetic and enforces explicit "
        "confirmation. Keep replies calm and factual — never shame."),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "size (default) or check."},
            "account": {"type": "NUMBER", "description": "Account size in currency units (defaults to configured account_size)."},
            "risk_pct": {"type": "NUMBER", "description": "Risk per trade percent (defaults to configured risk_per_trade_pct)."},
            "entry": {"type": "NUMBER", "description": "Entry price."},
            "stop": {"type": "NUMBER", "description": "Stop-loss / invalidation price."},
            "target": {"type": "NUMBER", "description": "Target price (for the risk/reward readout)."},
            "symbol": {"type": "STRING", "description": "Symbol, for exposure checks in action=check."},
            "side": {"type": "STRING", "description": "buy/sell (or long/short) for action=check."},
            "quantity": {"type": "NUMBER", "description": "Proposed size for action=check."},
            "context": {"type": "STRING",
                        "description": "The user's raw wording, when checking an impulsive-sounding request."},
        },
        "required": [],
    },
    "handler": risk_calculator,
}
