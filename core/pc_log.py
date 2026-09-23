"""
core/pc_log.py — the PC-control debug log.

One structured line per control decision, appended to logs/pc_control.log:

2026-09-23T12:00:00  screen_ai.click  app=Chrome  item=Download  method=uia  \
        box=1020,340,120,36  action=click-center  verify=ok  result=Clicked

Rules (design brief §30):
  * never raises — logging must not be able to kill a control action;
  * never logs secrets — typed text is recorded as a *length*, never its
    contents; no clipboard data, no API keys, no passwords;
  * cheap — one small append, file rotated at 512 KB.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

_lock = threading.Lock()
_MAX_FIELD = 160


def _log_path() -> Path:
    import sys
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).parent
    else:
        base = Path(__file__).resolve().parent.parent
    return base / "logs" / "pc_control.log"


def _rotate(path: Path) -> None:
    try:
        if path.exists() and path.stat().st_size > 512 * 1024:
            path.replace(path.with_name("pc_control.log.1"))
    except Exception:
        pass


def event(name: str, **fields) -> None:
    """Append one structured line. None/empty fields are skipped.

    `name` (not `action`) so callers can pass action=... as a normal field."""
    try:
        parts = [time.strftime("%Y-%m-%dT%H:%M:%S"), str(name)[:48]]
        for key, value in fields.items():
            if value is None or value == "":
                continue
            text = str(value).replace("\n", " ").strip()
            if len(text) > _MAX_FIELD:
                text = text[:_MAX_FIELD] + "..."
            if " " in text or "=" in text or not text:
                text = '"' + text.replace('"', "'") + '"'
            parts.append(f"{key}={text}")
        line = "  ".join(parts)
        path = _log_path()
        with _lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            _rotate(path)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
    except Exception:
        pass


def note_text(text: str) -> str:
    """Safe stand-in for user-typed text: length only, never the contents."""
    try:
        return f"text<{len(str(text))} chars>"
    except Exception:
        return "text<?>"
