"""
core/trading/risk.py — sizing math, trade guardrails, impulse protection (§5/§18/§19).

Three duties:

  * position_size() — the Position Size = Maximum Dollar Risk / Distance to
    Stop calculation, returned long-hand so every report can show its work;
  * check_trade() — the gate a live order cannot skip. Configured limits are
    the USER's rules: violations stop the trade with the arithmetic, and no
    code path here relaxes or rewrites a limit (§17/§18);
  * impulse protection (§19) — detects "all in / double it / make it back"
    phrasing and answers with calm numbers + a confirmation requirement.
    Never shames; never refuses to show the math.
"""
from __future__ import annotations

import math

from core.trading import settings


# ── §5: position sizing ─────────────────────────────────────────────────────

def position_size(account, risk_pct, entry, stop) -> dict:
    """Maximum Dollar Risk / Distance to Stop. Returns the numbers AND the
    error when the inputs cannot produce a sane size."""
    try:
        account = float(account)
        risk_pct = float(risk_pct)
        entry = float(entry)
        stop = float(stop)
    except (TypeError, ValueError):
        return {"ok": False, "error": "account, risk_pct, entry and stop must all be numbers."}
    if account <= 0:
        return {"ok": False, "error": "Account size must be positive to size a position."}
    if risk_pct <= 0:
        return {"ok": False, "error": "Risk per trade must be greater than 0%."}
    if entry <= 0 or stop <= 0:
        return {"ok": False, "error": "Entry and stop must be positive prices."}
    dist = abs(entry - stop)
    if dist == 0:
        return {"ok": False, "error": "Stop equals entry — distance to stop is 0, "
                                      "so position size is undefined. Move the stop."}
    budget = account * risk_pct / 100.0
    raw = budget / dist
    shares = math.floor(raw * 1e8) / 1e8 if raw < 1 else float(math.floor(raw))
    if shares <= 0:
        return {"ok": False, "shares": 0.0,
                "error": f"Risk budget ${budget:.2f} is smaller than the ${dist:.4f} "
                         f"distance to stop — no whole unit fits. Reduce the stop "
                         f"distance or accept a smaller size is impossible; do not widen risk."}
    return {"ok": True, "account": account, "risk_pct": risk_pct,
            "budget": budget, "entry": entry, "stop": stop,
            "dist": dist, "shares": shares, "risk_at_size": shares * dist}


def format_size(size: dict, shares_label: str = "shares") -> str:
    """The §5 worked-example layout, verbatim structure."""
    if not size.get("ok"):
        return f"Position size unavailable: {size.get('error', 'unknown error')}"
    lines = [
        f"Account: ${size['account']:,.0f}",
        f"Risk: {size['risk_pct']:g}%",
        f"Maximum risk: ${size['budget']:,.2f}",
        f"Entry: {_px(size['entry'])}",
        f"Stop: {_px(size['stop'])}",
        f"Risk per unit: ${size['dist']:.4f}".replace(".0000", ""),
        f"Position size: {_qty(size['shares'])} {shares_label}",
    ]
    if abs(size["risk_at_size"] - size["budget"]) > 1e-9:
        lines.append(f"(risk at this size: ${size['risk_at_size']:,.2f} — "
                     f"rounding down keeps you inside the ${size['budget']:,.2f} budget)")
    return "\n".join(lines)


def _px(v) -> str:
    try:
        return f"{float(v):,.4f}".rstrip("0").rstrip(".")
    except Exception:
        return str(v)


def _qty(v) -> str:
    try:
        f = float(v)
        return str(int(f)) if f == int(f) else f"{f:.8f}".rstrip("0").rstrip(".")
    except Exception:
        return str(v)


# ── §18: guardrails ─────────────────────────────────────────────────────────

def check_trade(symbol: str, side: str, entry, stop, quantity,
                target=None, existing_positions=None) -> dict:
    """Pre-flight every live trade. Returns
    {ok, violations[], warnings[], checks{}} — violations STOP the trade.
    Reads configured limits; NEVER writes them."""
    s = settings.get_all()
    account = _f(s.get("account_size"))
    risk_pct = _f(s.get("risk_per_trade_pct"))
    daily_pct = _f(s.get("max_daily_loss_pct"))
    max_trades = int(_f(s.get("max_trades_per_day")) or 0)
    max_lev = _f(s.get("max_leverage")) or 1.0
    rr_pref = _f(s.get("rr_preference"))

    violations: list[str] = []
    warnings: list[str] = []
    checks: dict[str, str] = {}

    entry = _f(entry)
    stop = _f(stop)
    quantity = _f(quantity)
    side = str(side or "").lower()
    if side in ("buy", "long"):
        side = "long"
    elif side in ("sell", "short"):
        side = "short"

    if entry is None or entry <= 0:
        violations.append("Entry price is missing or invalid.")
    if stop is None or stop == 0:
        violations.append("No stop-loss provided. Your rules require a stop on every "
                          "live trade — give a stop price (the invalidation level) "
                          "before this can be prepared.")
        stop_dist = None
    elif entry:
        stop_dist = abs(entry - stop)
        if stop_dist == 0:
            violations.append("Stop equals entry — distance to stop is 0; there is no "
                              "defined risk, so the trade cannot be sized.")
            stop_dist = None
        elif side == "long" and stop > entry:
            warnings.append(f"Long side with stop ABOVE entry ({_px(stop)} > {_px(entry)}) "
                            f"— that reads as a take-profit, not a stop. Check the level.")
        elif side == "short" and stop < entry:
            warnings.append(f"Short side with stop BELOW entry ({_px(stop)} < {_px(entry)}) "
                            f"— that reads as a take-profit, not a stop. Check the level.")
    else:
        stop_dist = None

    if account > 0:
        budget = account * risk_pct / 100.0
        checks["risk_budget"] = (f"${budget:,.2f} maximum per trade "
                                 f"({risk_pct:g}% of ${account:,.0f})")
        if stop_dist and quantity:
            loss = quantity * stop_dist
            checks["planned_loss"] = f"${loss:,.2f} if stopped out"
            if loss > budget + 1e-9:
                allowed = math.floor(budget / stop_dist)
                violations.append(
                    f"Position risks ${loss:,.2f} — above your {risk_pct:g}% budget "
                    f"of ${budget:,.2f}. Maximum size at this stop distance: "
                    f"{_qty(allowed)}.")
        notional = (quantity or 0) * (entry or 0)
        checks["notional"] = f"${notional:,.2f} notional"
        if notional > account * max_lev + 1e-9:
            violations.append(
                f"Notional ${notional:,.2f} exceeds your {max_lev:g}× leverage cap "
                f"(${account * max_lev:,.2f} of buying power). Reduce size or "
                f"leverage — I will not raise your limit for you.")
        if daily_pct > 0:
            checks["daily_loss_limit"] = f"-${account * daily_pct / 100.0:,.2f} ({daily_pct:g}% of account)"
    else:
        # §18 requires account-risk checks before preparing a live trade:
        # with no account size they cannot run, so a live trade stops here
        # (paper preparation demotes violations to warnings upstream).
        violations.append(
            "Account size is not configured — account risk, leverage and "
            "daily-loss limits cannot be checked. Set account_size via "
            "trading_settings (e.g. account_size=10000) before any live trade.")

    target = _f(target)
    if target and stop_dist:
        rr = abs(target - (entry or 0)) / stop_dist
        checks["risk_reward"] = f"1:{rr:.2f} planned (your preference: 1:{rr_pref:g})"
        if rr < rr_pref:
            warnings.append(f"Risk/reward 1:{rr:.2f} is below your 1:{rr_pref:g} preference "
                            f"— targets at {_px((entry or 0) + stop_dist * rr_pref)} or "
                            f"better would satisfy it.")

    # Daily loss / trade-count — real numbers from the journal, best effort.
    try:
        from core.trading import journal
        today = journal.stats_today()
        if account > 0 and daily_pct > 0 and today["pnl"] <= -(account * daily_pct / 100.0) - 1e-9:
            violations.append(
                f"Daily loss limit reached: today is {today['pnl']:+,.2f} against a "
                f"limit of -${account * daily_pct / 100.0:,.2f}. That limit is yours; "
                f"I will not bypass it. Resets with a new session.")
        if max_trades and today["trades"] >= max_trades:
            violations.append(
                f"Daily trade cap reached: {today['trades']}/{max_trades} trades closed "
                f"today. Limit is yours; I will not bypass it.")
    except Exception:
        pass  # journal unreadable — sizing checks above still stand

    # Existing/correlated exposure (§18) — warnings: additive risk, not a rule break.
    sym = str(symbol or "").upper()
    for pos in existing_positions or []:
        try:
            ps = str(pos.get("symbol", "")).upper()
            if ps == sym:
                warnings.append(f"You already hold {_qty(pos.get('quantity'))} {ps} "
                                f"— adding doubles exposure to one asset.")
            elif ps and _cluster(ps) and _cluster(ps) == _cluster(sym):
                warnings.append(f"Existing {ps} position is correlated with {sym} "
                                f"({_cluster(sym)} group) — combined risk moves together.")
        except Exception:
            continue

    return {"ok": not violations, "violations": violations,
            "warnings": warnings, "checks": checks, "side": side}


def format_check(result: dict, heading: str = "RISK GUARDRAILS") -> str:
    lines = [heading]
    for k, v in result.get("checks", {}).items():
        lines.append(f"  {k.replace('_', ' ')}: {v}")
    for w in result.get("warnings", []):
        lines.append(f"  WARNING: {w}")
    for v in result.get("violations", []):
        lines.append(f"  STOP: {v}")
    lines.append("  Verdict: " + ("within your configured limits."
                                  if result.get("ok") else
                                  "BLOCKED — conflicts with your configured limits. "
                                  "Nothing will be prepared or submitted until this is resolved."))
    return "\n".join(lines)


_CLUSTERS = {
    "crypto": ("BTC", "ETH", "SOL", "XRP", "ADA", "DOGE", "BNB", "LTC"),
    "us-indices": ("SPY", "QQQ", "DIA", "IWM", "^GSPC", "^IXIC", "^DJI"),
}


def _cluster(symbol: str) -> str | None:
    up = str(symbol or "").upper()
    for name, members in _CLUSTERS.items():
        if any(up.startswith(m) for m in members):
            return name
    if up.endswith("-USD") or up.endswith("USD"):
        return "crypto"
    return None


def _f(v):
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


# ── §19: emotional / impulsive commands ─────────────────────────────────────

IMPULSE_PATTERNS = (
    ("all in", "committing the entire balance"),
    ("all-in", "committing the entire balance"),
    ("bet the house", "committing the entire balance"),
    ("use everything", "committing the entire balance"),
    ("double my position", "doubling exposure"),
    ("double the position", "doubling exposure"),
    ("maximum leverage", "maximum leverage"),
    ("max leverage", "maximum leverage"),
    ("full leverage", "maximum leverage"),
    ("recover my loss", "revenge trading"),
    ("recover my losses", "revenge trading"),
    ("make it back", "revenge trading"),
    ("win it back", "revenge trading"),
    ("chase my loss", "revenge trading"),
    ("yolo", "undiversified all-in bet"),
)


def impulse_flags(text: str) -> list[str]:
    """Matched risk-impulse phrasings (labels, calm and factual). Never raises."""
    try:
        low = str(text or "").lower()
        return sorted({label for phrase, label in IMPULSE_PATTERNS if phrase in low})
    except Exception:
        return []


def impulse_response(flags: list[str], account=None, risk_pct=None) -> str:
    """Show the numbers behind a high-risk request. Calm, factual, no shaming
    (tests assert no condescending vocabulary), and explicit that a configured
    limit is never raised silently."""
    s = settings.get_all()
    account = _f(account) if account is not None else _f(s.get("account_size"))
    risk_pct = _f(risk_pct) if risk_pct is not None else _f(s.get("risk_per_trade_pct"))
    risk_pct = risk_pct if risk_pct and risk_pct > 0 else 1.0

    lines = ["HIGH-RISK REQUEST — the arithmetic before anything moves:"]
    lines.append(f"  You flagged: {', '.join(flags)}.")
    if account and account > 0:
        budget = account * risk_pct / 100.0
        lines.append(f"  Your rule: risk {risk_pct:g}% per trade = ${budget:,.2f} of ${account:,.0f}.")
        lines.append(f"  'All in' would place 100% — ${account:,.0f} — at risk: "
                     f"{account / budget:.0f}× your per-trade limit, against a "
                     f"{_f(s.get('max_daily_loss_pct')) or 0:g}% daily-loss cap.")
    else:
        lines.append(f"  Your rule: {risk_pct:g}% risk per trade. An all-in bet is 100% "
                     f"of the balance — {100.0 / risk_pct:.0f}× your own limit.")
    lines.append("  These limits are yours; I will not raise them for you. "
                 "Any high-risk action still needs your explicit confirmation on "
                 "the order screen. Nothing has been executed.")
    return "\n".join(lines)
