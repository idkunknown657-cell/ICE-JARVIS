"""actions/pc_drills.py — self-training drills the assistant runs on itself.

Companion to core/self_training.py. The idle cycle there is *reflection*: it
reads the ledger and writes rules. The tool here is *practice*: one passive,
read-only drill on the live screen whose only results are a pointer move and
more evidence in the competency ledger.

WHAT A DRILL MAY DO (exhaustive list)
    * find a described element via the accessibility tree / vision
    * move the pointer over it — no click, ever
    * read back what is on screen
Nothing else. No clicks, no keys, no focus changes, nothing the user could
lose. Power (did the drill even get to run?) and aim (did the aim land?) are
recorded separately: only the aim side feeds the drill's own competency, so a
drill that could not run because PC Control is off is never scored as "the aim
missed" — the ledger would otherwise learn a lie.

WHEN IT RUNS
    Through the same core/self_training.py throttle as every idle round: quiet
    time, hourly budget, memory on, training on. JARVIS may request a drill
    itself; a human request goes through the same autonomy policy gate — the
    drills are passive, so the policy allows them, and a refusal is an honest
    sentence rather than a fake success.
"""
from __future__ import annotations

import time

from core import autonomy
from core import self_training
from core import pc_engine


TOOL = {
    "name": "training_run",
    "description": (
        "Run one self-training drill and record how it went. Passive and "
        "read-only: FIND the described element on screen, POINT the mouse at "
        "it (no click), and READ what is on screen. Nothing is typed, clicked "
        "or changed — the point is practice with real feedback. Self-initiated "
        "drills are also bounded by the quiet-time and per-hour limits; the "
        "tool refuses honestly when a round is not due."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "target": {
                "type": "STRING",
                "description": (
                    "What to look for and aim at, in plain language — for "
                    "example 'the address bar', 'the Send button', "
                    "'the first search result'."
                ),
            },
            "asked_by_user": {
                "type": "BOOLEAN",
                "description": (
                    "True only when the user asked for this drill in this "
                    "turn. Self-initiated drills must leave it false."
                ),
            },
            "reason": {
                "type": "STRING",
                "description": "One short line: why this drill, now.",
            },
        },
        "required": ["target"],
    },
    "handler": None,          # set below, after the function exists
}


def _policy_gate(asked_by_user: bool) -> str:
    """'' when the drill may run, otherwise the honest refusal sentence.

    Answered by the same policy table every other action goes through, so the
    assistant's own drills can never drift outside the boundaries the HUD
    shows.
    """
    return autonomy.gate_reason(
        "run a passive self-training drill on the screen without clicking",
        asked_by_user=asked_by_user)


def _idle_seconds(player) -> float:
    """How long the user has been quiet, as far as the drill is concerned.

    main.py knows the real value; the tool layer receives whatever the dispatch
    passes. `player` may carry `_last_user_speech` — used when present, 0.0
    otherwise so the throttle, not this file, decides honestly.
    """
    try:
        last = getattr(player, "_last_user_speech", None)
        if last is not None:
            return max(0.0, time.monotonic() - float(last))
    except Exception:
        pass
    return 0.0


def training_run(parameters: dict, response=None, player=None,
                 session_memory=None) -> str:
    params = parameters or {}
    target = str(params.get("target") or "").strip()
    asked = bool(params.get("asked_by_user", False))

    if not target:
        return ("A drill needs something to look for — name an element you "
                "want to practise finding.")

    # The drill is the assistant touching the machine, read-only as it is, so
    # it answers to the PC-control master switch like everything else.
    if not autonomy.get_mode("pc_control"):
        self_training.record_outcome("pc_control", False,
                                     "training_run: pc control is off")
        return ("PC control is off, so I cannot run a drill — I cannot even "
                "move the pointer passively. Turn it on and ask again.")

    gate = _policy_gate(asked)
    if gate:
        self_training.record_outcome("tool_use", False,
                                     "training_run blocked: " + gate[:120])
        return ("I am not allowed to run that drill right now. " + gate)

    # The throttle: a self-initiated drill must be due, exactly like the idle
    # cycle. A user-asked drill walks the same check with however long the user
    # has actually been quiet — practice mid-conversation is worthless anyway.
    if not self_training.cycle_due(_idle_seconds(player)):
        return ("Not now — a drill is not due: I practise while you are "
                "quiet, on a small hourly budget. Ask again after a pause, "
                "or check ⚙ Self-training.")

    started = time.time()

    # 1. READ — the reading half of the drill.
    try:
        description = pc_engine.describe_screen()
    except Exception as e:                    # a broken describer is aim data too
        description = ""
        self_training.record_outcome("screen", False,
                                     "describe_screen failed: " + str(e)[:120])
    read_ok = bool((description or "").strip())
    self_training.record_outcome("screen", read_ok,
                                 "describe_screen returned nothing during drill"
                                 if not read_ok else "")

    # 2+3. FIND and POINT — locate the target and move the pointer onto it.
    # No click, ever: point_at locates, moves and verifies arrival, and stops
    # dead when the target is not found.
    res = pc_engine.point_at(target)
    aim_ok = bool(res.ok)

    # The drill's own competency: the aim landing. Power states above are kept
    # out of this so an off-switch is never scored as a miss.
    self_training.record_outcome("pc_control", aim_ok,
                                 "drill: " + (res.detail or target)[:160])

    try:
        autonomy.feed("mode", "self-training drill on '{}': {}".format(
            target[:60], (res.detail or ("screen read " +
                                         ("ok" if read_ok else "failed")))[:120]))
    except Exception:
        pass

    if not aim_ok:
        return ("Drill done — honest miss. {} Nothing was clicked, and the "
                "failure is recorded so I practise it.".format(res.detail))

    ms = int((time.time() - started) * 1000)
    return ("Drill done in {} ms — {}. Screen read {}. No click, nothing "
            "changed; recorded as a pass.".format(
                ms, res.detail, "ok" if read_ok else "failed"))


TOOL["handler"] = training_run
