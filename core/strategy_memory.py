"""strategy_memory.py — learned UI-interaction strategies, per application.

The last piece of the visual brief's Observe → Understand → Act → Verify →
Learn → Improve loop. screen_ai already verifies every click (UIA name under
the pointer) and computer_control verifies screen_clicks with a vision check;
those verdicts arrive here as tiny records keyed by the focused app:

    {"app": "chrome", "strategy": "uia", "item": "download button",
     "ok": true}

- A strategy that repeatedly verifies lands in the store; one that keeps
  failing does not.
- `hints_for(app, item)` returns the remembered approach as a short prompt
  block the model reads BEFORE it acts, so the second attempt at the same
  dialog is already an expert.

Storage: config/ui_strategies.json — small, capped, human-readable, wiped by
`clear()`. All functions are worker-thread-safe via a module lock and never
raise; a failed read/write simply loses one note.
"""

from __future__ import annotations

import json
import time
import sys
from pathlib import Path
from threading import Lock

_BASE = (Path(sys.executable).resolve().parent
         if getattr(sys, "frozen", False)
         else Path(__file__).resolve().parent.parent)
STRATEGIES_PATH = _BASE / "config" / "ui_strategies.json"

_LOCK = Lock()
_MAX_ENTRIES = 60
_MAX_VALUE = 200

# A strategy needs this many verifications before it is trusted as a hint,
# and this success ratio among them.
_MIN_SEEN = 2
_MIN_RATIO = 0.6


def _load() -> dict:
    try:
        data = json.loads(STRATEGIES_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(data: dict) -> None:
    try:
        STRATEGIES_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = STRATEGIES_PATH.with_name(STRATEGIES_PATH.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False),
                       encoding="utf-8")
        tmp.replace(STRATEGIES_PATH)
    except Exception:
        pass


def _norm_app(app: str) -> str:
    return (str(app or "").strip().lower().split(".")[0])[:40]


def _norm_item(item: str) -> str:
    return (str(item or "").strip().lower())[:80]


def record(app: str, item: str, strategy: str, ok: bool,
           note: str = "") -> None:
    """Record one verified interaction attempt. Never raises."""
    try:
        with _LOCK:
            data = _load()
            log = data.get("log")
            if not isinstance(log, list):
                log = []
            log.append({
                "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                "app": _norm_app(app),
                "item": _norm_item(item),
                "strategy": str(strategy or "uia").strip()[:20],
                "ok": bool(ok),
                "note": str(note or "").strip()[:_MAX_VALUE],
            })
            data["log"] = log[-200:]
            _save(data)
    except Exception:
        pass


def _aggregate() -> dict:
    """log rows → {(app, item, strategy): {"ok": n, "fail": n, "note": str}}"""
    data = _load()
    out: dict = {}
    for row in data.get("log") or []:
        if not isinstance(row, dict):
            continue
        key = (row.get("app", ""), row.get("item", ""), row.get("strategy", ""))
        agg = out.setdefault(key, {"ok": 0, "fail": 0, "note": ""})
        if row.get("ok"):
            agg["ok"] += 1
        else:
            agg["fail"] += 1
        if not agg["note"] and row.get("note"):
            agg["note"] = str(row["note"])[:_MAX_VALUE]
    return out


def hints_for(app: str, item: str = "") -> str:
    """Prompt block of trusted strategies for one app ('' when none yet).

    With `item`, only rows for that element (fuzzy: the item appears inside
    the remembered item name) are returned; otherwise the app's top rows.
    """
    try:
        with _LOCK:
            agg = _aggregate()
        app_n = _norm_app(app)
        item_n = _norm_item(item)
        lines: list[str] = []
        for (a, i, s), v in agg.items():
            if a != app_n or (item_n and item_n not in i and i not in item_n):
                continue
            seen = v["ok"] + v["fail"]
            if seen < _MIN_SEEN or v["ok"] / seen < _MIN_RATIO:
                continue
            line = f"- In {a}, '{i}' works via {s}"
            if v["note"]:
                line += f" — {v['note']}"
            lines.append(line)
        if not lines:
            return ""
        return ("Learned UI strategies for this app (apply silently):\n"
                + "\n".join(lines[:5]))
    except Exception:
        return ""


def summary() -> str:
    """One-line count for the diagnostics tool / status chips."""
    try:
        with _LOCK:
            agg = _aggregate()
        trusted = sum(1 for v in agg.values()
                      if v["ok"] + v["fail"] >= _MIN_SEEN
                      and v["ok"] / (v["ok"] + v["fail"]) >= _MIN_RATIO)
        return f"{trusted} learned UI strategies"
    except Exception:
        return "no learned UI strategies"


def clear() -> None:
    """Wipe the store (user-requested forget)."""
    try:
        with _LOCK:
            _save({})
    except Exception:
        pass
