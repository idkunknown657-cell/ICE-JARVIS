"""
core/cancel.py — stop token for multi-step automation.

The user must always be able to interrupt JARVIS ("Stop.", "Cancel.", the
interrupt button).  Speech already stops instantly (main.JARVIS.interrupt);
this flag stops the *work*: every cancellable loop (goal_agent) checks it
before each step and aborts instead of continuing a task nobody wants any more.

Rules that keep it honest:
  * begin() clears any stale flag, so a stop pressed while nothing was running
    can never kill the next task.
  * request() while nothing is running still records the request, but the next
    begin() wipes it — a stop is momentary, never a land mine.
  * never raises — logging/cancel must not be able to kill a control action.
"""
from __future__ import annotations

import threading

_lock = threading.Lock()
_flag = False


def begin() -> None:
    """Arm a cancellable run (also clears any stale stop request)."""
    global _flag
    with _lock:
        _flag = False


def request() -> None:
    """Ask current/next cancellable automation to stop. Never raises."""
    global _flag
    try:
        with _lock:
            _flag = True
    except Exception:
        pass


def stopped() -> bool:
    """True when a stop has been requested since the last begin()."""
    try:
        with _lock:
            return _flag
    except Exception:
        return False


def clear() -> None:
    """Drop a stop request without starting a run."""
    begin()
