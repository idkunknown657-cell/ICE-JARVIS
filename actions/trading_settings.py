"""
actions/trading_settings.py — view/edit trading configuration (spec §13/§17/
§18). The ONLY code path that ever writes risk limits — every change here is
an explicit user instruction, echoed old → new, with heightened wording when
the user raises risk or switches to live mode (recorded as their choice, never
changed again silently).
"""
from core.trading import settings

_INCREASE_NOTED = {
    "risk_per_trade_pct": "per-trade risk",
    "max_daily_loss_pct": "daily loss limit",
    "max_trades_per_day": "daily trade cap",
    "max_leverage": "leverage cap",
    "rr_preference": "minimum risk/reward preference",
}


def trading_settings(parameters: dict, player=None, speak=None) -> str:
    p = parameters or {}
    action = str(p.get("action") or "get").strip().lower()
    try:
        if action == "set":
            key = str(p.get("key") or "").strip()
            if not key:
                return (f"trading_settings action=set needs key=. Valid keys: "
                        f"{', '.join(sorted(settings.DEFAULTS))}.")
            old, new = settings.set_value(key, p.get("value"))
            lines = [f"Setting updated: {key}: {old!r} → {new!r}"]
            if key == "mode":
                if new == "live":
                    lines.append(
                        "LIVE TRADING MODE enabled — your explicit choice. Real-money "
                        "orders are now possible and STILL require your on-screen "
                        "confirmation for every submission; platform automation needs "
                        "platform_app + order_steps to be configured, and an "
                        "unconfigured setup refuses honestly instead of clicking blind.")
                else:
                    lines.append("PAPER TRADING / ANALYSIS MODE — default, no real money.")
            elif key in _INCREASE_NOTED:
                try:
                    if float(new) > float(old or 0):
                        lines.append(
                            f"Recorded as your explicit choice: {_INCREASE_NOTED[key]} "
                            f"raised from {old} to {new}. I will not change it again "
                            f"without you asking.")
                except (TypeError, ValueError):
                    pass
            return "\n".join(lines)

        # get (default) — full effective config
        cfg = settings.get_all()
        lines = [settings.mode_label(), "  (paper = default virtual mode; live = "
                 "real-money orders gated behind your on-screen confirmation)"]
        for k in sorted(cfg):
            v = cfg[k]
            if k == "order_steps" and isinstance(v, str) and len(v) > 120:
                v = v[:120] + "…"
            lines.append(f"  {k}: {v}")
        return "\n".join(lines)
    except ValueError as e:
        return str(e)          # precise validation message from settings.set_value
    except Exception as e:
        return f"trading_settings failed: {e}"


TOOL = {
    "name": "trading_settings",
    "description": (
        "View or change trading configuration — the ONLY way risk limits ever "
        "change (never altered silently by analysis, alerts or orders). "
        "action=get (default) prints everything: mode (paper|live), "
        "account_size, risk_per_trade_pct, max_daily_loss_pct, "
        "max_trades_per_day, rr_preference, max_leverage, provider, "
        "favorite_symbols/timeframes, platform_app, symbol_search_hotkey, "
        "buy_button_desc, sell_button_desc, order_steps (JSON macro for live "
        "platform automation). action=set takes key+value, returns old→new, "
        "and calls out any risk increase as the user's explicit choice — "
        "use it for 'switch to paper trading', 'set my account size to "
        "10000', 'risk 1% per trade', 'remember I trade BTC and the 4h "
        "chart', 'configure my trading platform'. Refuses unknown keys and "
        "invalid values with the exact reason."),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "get (default) or set."},
            "key": {"type": "STRING", "description": "Setting name for action=set."},
            "value": {"type": "STRING",
                      "description": "New value (number/string as text; lists comma-separated; "
                                     "order_steps as a JSON list string)."},
        },
        "required": [],
    },
    "handler": trading_settings,
}
