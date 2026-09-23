"""
actions/stop_task.py — stop/cancel the automation JARVIS is running.

"Stop." / "Cancel." / "Wait." / "Don't do that." must halt queued work
immediately.  Voice interrupt already stops speech; this stops the *task*:
it raises core.cancel's flag, which every cancellable loop checks before its
next step.  It never closes apps or undoes work that already finished — it
only prevents further steps.
"""
from __future__ import annotations

from core import cancel


def stop_task(
    parameters: dict = None,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    was_stopped = cancel.stopped()
    cancel.request()
    if player:
        try:
            player.write_log("[stop] stop requested")
        except Exception:
            pass
    if was_stopped:
        return "Stop already requested — the running task is halting."
    return ("Stopping — any running task will halt at its next step, and no "
            "further steps will be started. Finished work is left untouched.")


# ── Tool declaration (auto-discovered by core/action_loader.py) ──────────────
TOOL = {
    "name": "stop_task",
    "description": (
        "Stop/cancel automation JARVIS is running right now. Use the moment "
        "the user says 'stop', 'cancel', 'wait', \"don't do that\" or similar "
        "while a multi-step task or sequence of actions is in progress. It "
        "does not close apps or undo completed work — it only prevents "
        "further steps from running."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {},
    },
    "handler": stop_task,
}
