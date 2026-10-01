"""
actions/skill_discovery.py — "what have you noticed?" as a tool.

The store, the thresholds and the ask-once rule live in
core/skill_discovery.py; this file is the phrasing. It exists so the user can
act on a suggestion in the moment it is offered: "yes, learn it", "no, forget
that one", "what have you noticed?". The forge itself is the same pipeline
`learn how to X` already uses — nothing here is a second way to write code.
"""
from __future__ import annotations

from core import skill_discovery as discovery


def _log(player, text: str) -> None:
    if player:
        try:
            player.write_log(f"JARVIS: {text}")
        except Exception:
            pass


def _list(player, speak) -> str:
    text = discovery.spoken_list()
    _log(player, text)
    if speak:
        try:
            stats = discovery.stats()
            speak(f"I am watching {stats['watching']} request"
                  f"{'s' if stats['watching'] != 1 else ''}."
                  if stats["watching"] else
                  "I have not noticed any repeats yet.")
        except Exception:
            pass
    return text


def _forge(parameters: dict, player, speak) -> str:
    """Learn the thing the user just said yes to.

    The goal comes either from the model quoting the offer, or from the stored
    candidate itself when it does not — the offer text is not something the
    user should have to repeat.
    """
    goal = str(parameters.get("goal") or parameters.get("request") or "").strip()
    if not goal:
        ripe = discovery.candidates() or discovery.suggestions()
        if not ripe:
            return ("There is nothing I have been meaning to learn — ask me for "
                    "something a few times and I will notice.")
        goal = str(ripe[0].get("display") or "")
    from core import skill_forge as forge
    result = forge.forge(goal)
    message = str(result.get("message") or "Done.")
    if result.get("ok"):
        discovery.mark_forged(goal, str(result.get("name") or ""))
    _log(player, message)
    if speak:
        try:
            speak(message)
        except Exception:
            pass
    return message


def skill_discovery(parameters: dict, player=None, speak=None, response=None,
                    session_memory=None) -> str:
    """Entry point for the auto-discovered TOOL below."""
    parameters = parameters if isinstance(parameters, dict) else {}
    action = str(parameters.get("action") or "list").strip().lower()
    try:
        if action in ("list", "show", "all", "status"):
            if action == "status":
                stats = discovery.stats()
                text = (f"Watching {stats['watching']} requests — {stats['ripe']} "
                        f"ripe, {stats['offered']} offered, {stats['learned']} learned.")
                _log(player, text)
                return text
            return _list(player, speak)
        if action in ("forge", "learn", "yes"):
            return _forge(parameters, player, speak)
        if action in ("dismiss", "skip", "no"):
            target = (parameters.get("goal") or parameters.get("request")
                      or parameters.get("name") or "")
            text = discovery.dismiss(str(target))
            _log(player, text)
            return text
        if action in ("forget", "delete", "remove"):
            target = (parameters.get("goal") or parameters.get("request")
                      or parameters.get("name") or "")
            text = discovery.forget(str(target))
            _log(player, text)
            return text
        if action == "clear":
            count = discovery.clear()
            return (f"Cleared {count} watched request"
                    f"{'s' if count != 1 else ''}." if count
                    else "There was nothing being watched.")
        return (f"I do not know the '{action}' action. I can list, forge, "
                f"dismiss, forget or clear.")
    except Exception as e:
        return f"Something went wrong with the watch list: {e}"


TOOL = {
    "name": "skill_discovery",
    "description": (
        "The list of requests JARVIS has noticed the user repeating, and the "
        "way to act on an offer to learn one. Use 'list' when the user asks "
        "what has been noticed or what could be learned; 'forge' when they say "
        "yes to such an offer (this runs the same skill_forge pipeline as "
        "'learn how to X'); 'dismiss' to stop offering one without deleting it; "
        "'forget' to delete it; 'clear' to empty the list. "
        "Do NOT use it for one-off requests, and never fabricate an entry — the "
        "list is the only truth about what was noticed."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "One of: list (default), forge, dismiss, forget, clear, status.",
            },
            "goal": {
                "type": "STRING",
                "description": ("For forge/dismiss/forget: the request as it was "
                                "shown in the offer. Optional — the most recent "
                                "candidate is used when omitted."),
            },
        },
        "required": ["action"],
    },
    "handler": skill_discovery,
}
