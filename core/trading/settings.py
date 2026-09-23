"""
core/trading/settings.py — trading configuration store.

One JSON (config/trading_settings.json) mirroring memory/config_manager's
read-modify-write style, kept separate because these are the user's personal
risk parameters: mode, account size, loss limits, platform automation.

Defaults are deliberately conservative and load-bearing (spec §13/§17/§18):
  * mode = "paper" on a fresh install — real-money trading is opt-in only,
    via an explicit set_mode("live") call;
  * 1% risk per trade, no leverage;
  * risk limits are ONLY written by the user-facing settings action — no
    analysis, alert, or order path is allowed to raise them (§17: never
    silently increase risk). Nothing in this module calls set() on its own.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

DEFAULTS: dict = {
    "mode": "paper",              # "paper" | "live" — live requires explicit choice
    "account_size": 0.0,          # 0 = not configured → analyses skip dollar math
    "risk_per_trade_pct": 1.0,
    "max_daily_loss_pct": 3.0,
    "max_trades_per_day": 5,
    "rr_preference": 1.5,
    "max_leverage": 1.0,
    "provider": "yahoo",
    "favorite_symbols": [],       # remembered preferences (§17) — never risk limits
    "favorite_timeframes": [],
    "platform_app": "",           # trading-platform automation (§12), off until set
    "symbol_search_hotkey": "ctrl+k",
    "buy_button_desc": "",
    "sell_button_desc": "",
    "order_steps": "",            # optional JSON macro list — see orderflow.py
}

_NUMERIC = ("account_size", "risk_per_trade_pct", "max_daily_loss_pct",
            "max_trades_per_day", "rr_preference", "max_leverage")
_LISTS = ("favorite_symbols", "favorite_timeframes")
_lock = threading.Lock()


def _path() -> Path:
    from memory.config_manager import CONFIG_DIR
    return CONFIG_DIR / "trading_settings.json"


def get(key: str, default=None):
    """Read one setting; unknown keys fall back to DEFAULTS, then `default`."""
    try:
        data = {}
        p = _path()
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
        if key in data:
            return data[key]
        if key in DEFAULTS:
            return DEFAULTS[key]
        return default
    except Exception:
        return DEFAULTS.get(key, default)


def get_all() -> dict:
    """Full effective settings (defaults + stored overrides). Never raises."""
    out = dict(DEFAULTS)
    try:
        p = _path()
        if p.exists():
            stored = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(stored, dict):
                out.update(stored)
    except Exception:
        pass
    return out


def set_value(key: str, value):
    """User-explicit write with validation. Returns (old, new) or raises
    ValueError with a precise reason — callers surface it verbatim (§17: the
    ONLY place risk limits ever change is an explicit user call)."""
    if key not in DEFAULTS:
        raise ValueError(
            f"Unknown setting '{key}'. Valid keys: {', '.join(sorted(DEFAULTS))}.")
    if key == "mode":
        v = str(value).strip().lower()
        if v not in ("paper", "live"):
            raise ValueError("mode must be 'paper' or 'live'.")
        value = v
    elif key in _NUMERIC:
        try:
            value = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"{key} must be a number.")
        if value < 0:
            raise ValueError(f"{key} cannot be negative.")
        if key in ("risk_per_trade_pct", "max_daily_loss_pct", "rr_preference"):
            if value == 0:
                raise ValueError(f"{key} must be greater than 0.")
    elif key in _LISTS:
        if isinstance(value, str):
            value = [s.strip() for s in value.split(",") if s.strip()]
        elif isinstance(value, (list, tuple)):
            value = [str(s).strip() for s in value if str(s).strip()]
        else:
            raise ValueError(f"{key} must be a list of strings.")
    elif key == "max_trades_per_day":
        value = int(value)
    else:
        value = str(value).strip()

    with _lock:
        old = get(key)
        data = {}
        p = _path()
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                data = {}
        data[key] = value
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, indent=4), encoding="utf-8")
    return old, value


def is_live() -> bool:
    return str(get("mode", "paper")).lower() == "live"


def mode_label() -> str:
    """The banner every trading report carries (§13: never blur paper/live)."""
    return "LIVE TRADING MODE" if is_live() else "PAPER TRADING / ANALYSIS MODE"
