"""
core/trading/orderflow.py — ANALYZE → PREPARE → SHOW → CONFIRM → SUBMIT →
VERIFY → JOURNAL (spec §12).

Hard rules enforced structurally, not by convention:

  * default mode is PAPER — a live order cannot exist unless the user has
    explicitly flipped mode to "live" in settings;
  * a LIVE submit reaches _execute_live() only through core/confirm.py's
    resolve() callback, i.e. only after a human presses CONFIRM in the HUD —
    the model cannot forge that (same reasoning as shutdown gating);
  * prepare() always stops at SHOW with the full order ticket + risk math, and
    an impulse-flagged request (§19) forces the confirm gate even for paper;
  * platform automation is config-driven (order_steps macro) — with no config
    the live path honestly refuses instead of blind-clicking a real-money
    order ticket it cannot see;
  * VERIFY is attempted after submit; an unverifiable fill is reported as
    unverified, never claimed as filled.

Pending orders live in memory (10-minute TTL) — a stale ticket is never
silently executed.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime

from core.trading import journal, language, paper, risk, settings

_PENDING_TTL = 600.0
_lock = threading.Lock()
_pending: dict[str, dict] = {}


def _ticket_id() -> str:
    return f"ORD{int(time.time() * 1000) % 1_000_000_000:09d}"


# ── §6 ticket formatting ────────────────────────────────────────────────────

def _fmt(v, default="market"):
    if v in (None, ""):
        return default
    try:
        return f"{float(v):,.4f}".rstrip("0").rstrip(".")
    except Exception:
        return str(v)


def _ticket(order: dict) -> str:
    entry = order.get("entry")
    stop = order.get("stop")
    target = order.get("target")
    lines = ["POSSIBLE SETUP — ORDER TICKET (scenario prepared for review)"]
    lines.append(f"  Symbol: {order['symbol']}   Side: {order['side'].upper()}"
                 + (f"   Quantity: {_fmt(order.get('quantity'), '—')}"
                    if order.get("quantity") else "   Quantity: — (not sized)"))
    lines.append(f"  Entry: {_fmt(entry)}")
    lines.append(f"  Invalidation (stop): {_fmt(stop, 'MISSING')}")
    lines.append(f"  Target: {_fmt(target, 'MISSING')}")
    rr = order.get("rr")
    lines.append(f"  Risk/Reward: {'1:' + f'{rr:.2f}' if rr else 'n/a (need stop + target)'}")
    if order.get("sizing_text"):
        lines.append("  POSITION SIZING (your rule, shown long-hand):")
        lines.append("    " + order["sizing_text"].replace("\n", "\n    "))
    if order.get("risk_checks"):
        lines.append("  RISK CHECKS:")
        for k, v in order["risk_checks"].items():
            lines.append(f"    {k}: {v}")
    for w in order.get("warnings", []):
        lines.append(f"  WARNING: {w}")
    if order.get("impulse_flags"):
        lines.append(f"  HIGH-RISK FLAG: {', '.join(order['impulse_flags'])} — "
                     f"confirmation required even for paper.")
    lines.append("  CONFIRMATION NEEDED before submit:"
                 + (" (LIVE — explicit confirmation is mandatory)"
                    if order["mode"] == "live" else
                    " (high-risk/paper ticket — confirm to proceed)")
                 if order.get("confirmation_required") else
                 "  Type of order: paper virtual fill (no real money).")
    lines.append(f"  Mode: {order['mode'].upper()}")
    return "\n".join(lines)


# ── prepare ─────────────────────────────────────────────────────────────────

def prepare(symbol: str, side: str, quantity=None, entry=None, stop=None,
            target=None, reason: str = "", note: str = "",
            context: str = "") -> dict:
    """Build + show a ticket. Never submits anything (§12 step 3 of 6)."""
    sym = str(symbol or "").strip().upper()
    if not sym:
        return {"ok": False, "error": "No symbol given."}
    side_l = str(side or "").lower()
    if side_l in ("buy", "long"):
        side_l = "long"
    elif side_l in ("sell", "short"):
        side_l = "short"
    else:
        return {"ok": False, "error": "side must be buy/sell (or long/short)."}

    mode = "live" if settings.is_live() else "paper"
    impulse = risk.impulse_flags(f"{reason} {note} {context}")

    # Size from configured risk when the caller did not pass a quantity (§5).
    sizing_text = None
    acct = float(settings.get("account_size") or 0)
    risk_pct = float(settings.get("risk_per_trade_pct") or 1)
    if (quantity in (None, "", 0)) and acct > 0 and stop not in (None, "", 0) and entry:
        size = risk_mod_size(acct, risk_pct, entry, stop)
        if size.get("ok"):
            quantity = size["shares"]
            sizing_text = risk.format_size(size)

    warnings: list[str] = []
    check = risk.check_trade(sym, side_l, entry, stop, quantity, target=target,
                             existing_positions=_existing(sym))
    if mode == "paper":
        # §18 gates LIVE trades; paper shows the same math as warnings and
        # proceeds (virtual money — but the numbers are still surfaced).
        warnings = list(check["violations"]) + list(check["warnings"])
        check = dict(check, ok=True, violations=[])

    rr = None
    try:
        if stop not in (None, "", 0) and target not in (None, "", 0) and entry:
            rr = abs(float(target) - float(entry)) / abs(float(entry) - float(stop))
    except Exception:
        rr = None

    confirmation_required = (mode == "live") or bool(impulse)

    order = {
        "id": _ticket_id(), "symbol": sym, "side": side_l,
        "quantity": float(quantity) if quantity not in (None, "") else None,
        "entry": float(entry) if entry not in (None, "") else None,
        "stop": float(stop) if stop not in (None, "") else None,
        "target": float(target) if target not in (None, "") else None,
        "rr": rr, "mode": mode, "reason": reason, "note": note,
        "impulse_flags": impulse, "warnings": warnings,
        "risk_checks": check.get("checks", {}),
        "sizing_text": sizing_text,
        "confirmation_required": confirmation_required,
        "created": time.time(),
        "status": "prepared",
    }
    if not check.get("ok"):
        # Live mode: guardrail violation stops the trade before it exists (§18).
        return {"ok": False, "blocked": True,
                "error": risk.format_check(check,
                                           heading="TRADE PREPARATION STOPPED — RISK GUARDRAILS")}

    with _lock:
        _prune()
        _pending[order["id"]] = order

    text = _ticket(order)
    if impulse:
        text += "\n\n" + risk.impulse_response(impulse, account=acct, risk_pct=risk_pct)
    text += ("\n\nNext: say \"submit\" (or call trade_order action=submit) to review "
             "and confirm — nothing is sent until you confirm on screen."
             if confirmation_required else
             "\n\nNext: say \"submit it\" when you want this paper ticket filled "
             "(`trade_order action=submit`).")
    text = language.ensure_safe(text)
    return {"ok": True, "order": order, "text": text}


def risk_mod_size(acct, risk_pct, entry, stop):
    from core.trading import risk as _r
    return _r.position_size(acct, risk_pct, entry, stop)


def _existing(sym: str) -> list[dict]:
    """Every open paper position — same-symbol adds and correlated clusters
    are exactly the exposure §18 wants surfaced."""
    try:
        return list(paper.positions())
    except Exception:
        return []


# ── submit ──────────────────────────────────────────────────────────────────

def get(order_id: str) -> dict | None:
    with _lock:
        _prune()
        return _pending.get(str(order_id or "").strip().upper()) \
            or _pending.get(str(order_id or "").strip())


def submit(order_id: str) -> dict:
    """Route a prepared ticket. LIVE always goes through core/confirm (the HUD
    button is the only thing that can invoke _execute_live); paper executes
    directly unless §19 impulse flags forced the gate."""
    order = get(order_id)
    if order is None:
        return {"ok": False,
                "error": f"No prepared order '{order_id}' (tickets expire after "
                         f"10 minutes). Re-run trade_order action=prepare."}

    if order["confirmation_required"]:
        try:
            from core import confirm as confirm_gate
        except Exception as e:
            return {"ok": False, "error": f"Confirmation system unavailable: {e}. "
                                          f"Nothing was submitted."}
        title = (f"Submit LIVE {order['symbol']} {order['side'].upper()} order?"
                 if order["mode"] == "live" else
                 f"Fill PAPER {order['symbol']} {order['side'].upper()} ticket?")
        detail = (_ticket(order)
                  + "\n\nLIVE = real money at your broker. The order will be "
                    "prepared on screen and verified after submission."
                  if order["mode"] == "live" else
                  _ticket(order) + "\n\nPaper = virtual fill, no real money.")
        msg = confirm_gate.request(
            key=f"trade-{order['id']}", title=title, detail=detail,
            run=lambda: _execute(order))
        if msg.startswith("[CONFIRMATION"):
            order["status"] = "awaiting_confirmation"
            return {"ok": True, "awaiting_confirmation": True, "order": order,
                    "text": (msg + "\n\n" + _ticket(order))}
        # UI not bound — confirm.request already said so; nothing executed.
        return {"ok": False, "error": msg}

    result = _execute(order)
    return {"ok": True, "order": order, "result": result,
            "text": language.ensure_safe(result.get("text", str(result)))}


def _execute(order: dict) -> dict:
    """Runs only from user action: direct (paper) or confirm.resolve (live)."""
    if order["mode"] == "live":
        return _execute_live(order)
    return _execute_paper(order)


def _execute_paper(order: dict) -> dict:
    res = paper.open_position(order["symbol"], order["side"],
                              order.get("entry") or 0, order.get("quantity") or 1,
                              stop=order.get("stop"), target=order.get("target"),
                              note=order.get("reason") or "")
    if not res.get("ok"):
        return {"ok": False, "text": f"Paper fill FAILED: {res.get('error')} — "
                                     f"nothing recorded."}
    pos = res["position"]
    # VERIFY: read back the position we just wrote (the only honest verify
    # available for a virtual fill).
    verified = any(p.get("id") == pos["id"] for p in paper.positions())
    journal.record(mode="paper", asset=order["symbol"], direction=order["side"],
                   entry=pos["entry"], stop=pos.get("stop"), target=pos.get("target"),
                   quantity=pos["quantity"],
                   result="open", reason=order.get("reason", ""),
                   notes=order.get("note", ""), verified=verified,
                   rr_planned=order.get("rr"))
    with _lock:
        _pending.pop(order["id"], None)
    text = (f"PAPER order filled — {order['side'].upper()} "
            f"{_fmt(order.get('quantity'))} {order['symbol']} @ {_fmt(order.get('entry'))}"
            f"{'  stop ' + _fmt(order.get('stop')) if order.get('stop') else ''}"
            f"{'  target ' + _fmt(order.get('target')) if order.get('target') else ''}\n"
            f"VERIFY: position {pos['id']} present in the paper account "
            f"({'confirmed' if verified else 'NOT confirmed'}). "
            f"Balance ${res['balance']:,.2f}. Mode: PAPER (virtual money).")
    return {"ok": True, "verified": verified, "text": language.ensure_safe(text)}


def _execute_live(order: dict) -> dict:
    """LIVE path — reached only via the user's HUD confirmation. Opens the
    platform, searches the symbol, then either runs the configured order_steps
    macro or honestly stops short of an un-sightable ticket (never blind-clicks
    a real-money order)."""
    steps_json = str(settings.get("order_steps") or "").strip()
    platform_app = str(settings.get("platform_app") or "").strip()
    missing = []
    if not platform_app:
        missing.append("platform_app (the trading platform to open)")
    if not steps_json:
        missing.append("order_steps (platform-specific macro for ticket fields, "
                       "including the final submit click)")
    if missing:
        with _lock:
            _pending.pop(order["id"], None)
        text = ("LIVE SUBMISSION NOT PERFORMED — platform automation is not "
                "configured:\n  - " + "\n  - ".join(missing) +
                "\nNothing was submitted (nothing was blind-clicked either). "
                "Configure via trading_settings, then prepare again. "
                "Your confirmation above has been recorded as intent, not fill.")
        return {"ok": False, "verified": False, "text": text}

    log: list[str] = []
    # 1. Open the platform + search the symbol — safe preparation steps.
    try:
        from actions.open_app import open_app
        r = open_app({"app_name": platform_app})
        log.append(f"open {platform_app}: {r if isinstance(r, str) else 'requested'}")
    except Exception as e:
        log.append(f"open {platform_app}: FAILED ({e})")
    try:
        from actions.computer_control import _hotkey, _type
        hk = str(settings.get("symbol_search_hotkey") or "ctrl+k")
        keys = [k.strip() for k in hk.split("+") if k.strip()]
        if len(keys) > 1:
            _hotkey(*keys)
            log.append(f"search hotkey {hk}: sent")
        else:
            log.append(f"search hotkey '{hk}' not sent — set a combo (e.g. ctrl+k)")
        _type(order["symbol"])
        log.append(f"typed symbol {order['symbol']}")
    except Exception as e:
        log.append(f"symbol search: FAILED ({e})")

    # 2. Run the configured macro. The macro's final click IS the submit —
    #    and we only got here because a human pressed CONFIRM above.
    import json as _json
    try:
        steps = _json.loads(steps_json)
        if not isinstance(steps, list):
            raise ValueError("order_steps must be a JSON list of step objects")
    except Exception as e:
        with _lock:
            _pending.pop(order["id"], None)
        return {"ok": False, "verified": False,
                "text": f"order_steps is invalid ({e}) — nothing submitted. "
                        f"Steps executed before this point (preparation only): "
                        + " | ".join(log)}

    exec_log = _run_macro(steps, order)
    verified = any("VERIFIED" in s for s in exec_log)
    submitted = any("submit" in s.lower() for s in exec_log) and \
        not any("FAILED" in s for s in exec_log)
    if submitted:
        journal.record(mode="live", asset=order["symbol"], direction=order["side"],
                       entry=order.get("entry"), stop=order.get("stop"),
                       target=order.get("target"), quantity=order.get("quantity"),
                       result="open", reason=order.get("reason", ""),
                       notes=order.get("note", ""), verified=verified,
                       rr_planned=order.get("rr"))
    with _lock:
        _pending.pop(order["id"], None)

    if not submitted:
        text = ("LIVE order NOT submitted — a macro step failed before the "
                "submit click:\n  " + "\n  ".join(log + exec_log) +
                "\nNothing was sent to your broker.")
        return {"ok": False, "verified": False, "text": text}
    if not verified:
        text = ("LIVE order submitted per your confirmation, BUT NOT VERIFIED — "
                "I could not read a fill confirmation on screen. Check your "
                "platform now. Log:\n  " + "\n  ".join(log + exec_log) +
                "\nThe journal recorded this as mode=live, verified=false.")
        return {"ok": True, "verified": False, "text": language.ensure_safe(text)}
    text = ("LIVE order submitted AND verified on screen. Log:\n  "
            + "\n  ".join(log + exec_log)
            + "\nRecorded in the journal as mode=live.")
    return {"ok": True, "verified": True, "text": language.ensure_safe(text)}


def _run_macro(steps: list, order: dict) -> list[str]:
    """Execute configured order_steps. Supported actions: hotkey, type,
    press, wait, expect (screen_find a description), read (vision read), and
    click_desc (vision click — a step with role='submit' is the actual
    buy/sell click, which is why this function only ever runs behind the
    user's HUD confirmation for live orders)."""
    import time as _t
    out: list[str] = []
    subs = {"{symbol}": order["symbol"],
            "{quantity}": _fmt(order.get("quantity"), ""),
            "{entry}": _fmt(order.get("entry"), ""),
            "{stop}": _fmt(order.get("stop"), ""),
            "{target}": _fmt(order.get("target"), ""),
            "{side}": order["side"]}
    for i, raw in enumerate(steps):
        if not isinstance(raw, dict):
            out.append(f"step {i + 1}: malformed (not an object) — SKIPPED")
            continue
        action = str(raw.get("action", "")).lower()
        val = str(raw.get("value", ""))
        for k, v in subs.items():
            val = val.replace(k, v)
        try:
            if action == "wait":
                _t.sleep(min(float(raw.get("seconds", 1)), 5.0))
                out.append(f"step {i + 1}: waited")
            elif action == "hotkey":
                from actions.computer_control import _hotkey
                keys = [k.strip() for k in val.split("+") if k.strip()]
                _hotkey(*keys)
                out.append(f"step {i + 1}: hotkey {val} sent")
            elif action == "type":
                from actions.computer_control import _type
                _type(val)
                out.append(f"step {i + 1}: typed '{val[:30]}'"
                           + ("..." if len(val) > 30 else ""))
            elif action == "press":
                from actions.computer_control import _press
                _press(val)
                out.append(f"step {i + 1}: pressed {val}")
            elif action in ("click_desc", "expect", "read"):
                from actions.computer_control import _do_screen_click, _screen_find
                if action == "expect":
                    found = _screen_find(val)
                    out.append(f"step {i + 1}: expect '{val}' → "
                               + ("VERIFIED visible" if found else "NOT visible"))
                elif action == "read":
                    from actions.screen_ai import _vision_read_text
                    out.append(f"step {i + 1}: read '{val}' → "
                               f"{_vision_read_text(val)[:80]}")
                else:
                    role = " (SUBMIT)" if raw.get("role") == "submit" else ""
                    r = _do_screen_click(val)
                    ok = "verified" in str(r).lower()
                    out.append(f"step {i + 1}: click '{val}'{role} → "
                               + ("VERIFIED" if ok else f"unverified: {str(r)[:70]}"))
            else:
                out.append(f"step {i + 1}: unknown action '{action}' — SKIPPED "
                           f"(macro not silently truncated)")
        except Exception as e:
            out.append(f"step {i + 1}: FAILED ({e})")
            if raw.get("role") == "submit":
                break
    return out


# ── status / cancel ─────────────────────────────────────────────────────────

def status(order_id: str = "") -> dict:
    with _lock:
        _prune()
        if not order_id:
            return {"ok": True, "prepared": list(_pending.values())}
        o = _pending.get(order_id) or _pending.get(order_id.upper())
        return {"ok": bool(o), "order": o,
                "error": None if o else f"No order '{order_id}'."}


def cancel(order_id: str) -> dict:
    with _lock:
        o = _pending.pop(str(order_id or "").strip(), None) \
            or _pending.pop(str(order_id or "").strip().upper(), None)
    return {"ok": bool(o),
            "text": f"Order {order_id} cancelled — nothing will be submitted."
                    if o else f"No order '{order_id}' to cancel."}


def format_prepared(orders: list[dict]) -> str:
    if not orders:
        return ("No prepared orders. Prepare one with trade_order "
                "action=prepare symbol=... side=buy/sell.")
    return "\n\n".join(_ticket(o) + f"\n  id: {o['id']}" for o in orders)


def _prune() -> None:
    now = time.time()
    for k in [k for k, v in _pending.items() if now - v["created"] > _PENDING_TTL]:
        _pending.pop(k, None)
