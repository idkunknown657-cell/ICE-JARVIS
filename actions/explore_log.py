"""
actions/explore_log.py — the notebook JARVIS keeps while it has the machine.

WHY THIS EXISTS
    core/initiative.py decides *what* to do and actions/pc_agent.py *does* it.
    Neither can answer the question the user actually asks afterwards: "so what
    did you find?" An autonomous session that reads three things and remembers
    none of them spent the user's electricity to produce a warm CPU.

    This is the other half of `idle_agenda()`'s promise to "say one short
    sentence about it": the sentence is spoken now, this tool keeps it for
    later. The list it writes to is the same one the HUD shows under the mood
    chip, so "occasionally tell me what it discovered" has something real to
    tell, and the interest pool that makes tomorrow's choices better is the
    same data.

WHAT IT CAN DO (exhaustive)
    * append one finding to a capped list (40 entries, oldest dropped)
    * append one unfinished task to a capped list (6 entries)
    * record what it is doing right now, for the activity line on the HUD
    Nothing else. No files, no screen, no network, no permission needed — it is
    a notebook, and the autonomy policy treats it as one (`FREE`, always).

WHY IT IS A TOOL AND NOT INFERRED FROM THE CONVERSATION
    Guessing "that sounds like something it learned" from a reply is how a
    memory fills with pleasantries. The model says what it found, in its own
    words, or nothing is recorded — and a session where nothing was found
    writes nothing, which is the honest result.
"""
from __future__ import annotations

from core import initiative


TOOL = {
    "name": "explore_log",
    "description": (
        "Keep something you found, or something you left unfinished, while you "
        "are using the PC on your own. Use it for the one thing worth "
        "remembering — a fact, a page, a tool, a project — or for a job you "
        "started and could not finish. It is a notebook: it writes nothing to "
        "disk except your own short line, changes nothing on screen, and needs "
        "no permission. Call it at most once per thing you actually found; "
        "logging noise is worse than logging nothing."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "finding": {
                "type": "STRING",
                "description": (
                    "The one thing worth keeping, in one plain sentence — what "
                    "you learned or found, not what you did to find it. Leave "
                    "it out when you found nothing worth keeping."
                ),
            },
            "task": {
                "type": "STRING",
                "description": (
                    "Something you started and did not finish, phrased as the "
                    "next concrete step, so you can pick it up later."
                ),
            },
            "doing": {
                "type": "STRING",
                "description": (
                    "What you are working on at this moment, in two or three "
                    "words, for the activity line on screen — for example "
                    "'reading about carb heat' or 'sorting Downloads'."
                ),
            },
        },
        "required": [],
    },
    "handler": None,          # set below, after the function exists
}


def explore_log(parameters: dict, response=None, player=None,
                session_memory=None) -> str:
    """Record a finding, a loose end, or the current activity. Never raises."""
    params = parameters or {}
    finding = " ".join(str(params.get("finding") or "").split())
    task = " ".join(str(params.get("task") or "").split())
    doing = " ".join(str(params.get("doing") or "").split())

    if not (finding or task or doing):
        return ("Nothing to note — pass a finding, a task to come back to, or "
                "what you are doing now.")

    kept: list[str] = []
    try:
        if finding:
            row = initiative.note_discovery(finding, source="own browsing")
            if row:
                kept.append("kept the finding")
        if task:
            initiative.note_task(task)
            kept.append("noted it as unfinished")
        if doing:
            d = initiative.director()
            if d.current:
                d.current["label"] = doing[:80]
                d._dirty = True
                d.save()
            kept.append("set the activity line")
    except Exception:
        # Bookkeeping must never be able to fail a turn: the assistant is
        # mid-task on the user's machine, and a lost note is a lost note.
        return "I could not write that down just now — carrying on."

    return "Noted: " + ", ".join(kept) + "."


TOOL["handler"] = explore_log
