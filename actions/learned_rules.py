"""
actions/learned_rules.py — "remember: always do X" as a tool.

The value of exposing this by voice is that a user corrects the assistant in the
moment they are annoyed by it ("stop opening files in Notepad — remember: always
use VS Code"), and that is exactly when a tool call is cheaper than a trip to a
settings screen. The store, the secret refusal and the prompt injection all live
in core/learned_rules.py; this file is the phrasing.
"""
from __future__ import annotations

from core import learned_rules as rules


def _log(player, text: str) -> None:
    if player:
        try:
            player.write_log(f"JARVIS: {text}")
        except Exception:
            pass


def _add(parameters: dict, player, speak) -> str:
    text = (parameters.get("rule") or parameters.get("text")
            or parameters.get("instruction") or parameters.get("directive") or "")
    result = rules.add(text, category=str(parameters.get("category") or "general"))
    message = str(result.get("message") or "Done.")
    _log(player, message)
    if speak and result.get("ok"):
        try:
            speak("Understood — I will follow that from now on.")
        except Exception:
            pass
    return message


def _list(player, speak) -> str:
    text = rules.spoken_list()
    _log(player, text)
    if speak:
        try:
            total = len(rules.all_rules())
            speak(f"I am following {total} standing instruction"
                  f"{'s' if total != 1 else ''}." if total
                  else "I have no standing instructions yet.")
        except Exception:
            pass
    return text


def _forget(parameters: dict, player) -> str:
    target = (parameters.get("rule_id") or parameters.get("id")
              or parameters.get("rule") or "")
    if not (target or "").strip():
        return "Which instruction should I forget? Say \"list my instructions\" first."
    out = rules.forget(target)
    return str(out.get("message") or "Done.")


def _toggle(parameters: dict, player, active: bool | None = None) -> str:
    target = (parameters.get("rule_id") or parameters.get("id")
              or parameters.get("rule") or "")
    if not (target or "").strip():
        return "Which instruction do you mean?"
    out = rules.toggle(target, active)
    return str(out.get("message") or "Done.")


def _clear(player) -> str:
    count = rules.clear()
    return (f"Deleted all {count} standing instruction"
            f"{'s' if count != 1 else ''}." if count
            else "There were no standing instructions to clear.")


def learned_rules(parameters: dict, player=None, speak=None, response=None,
                  session_memory=None) -> str:
    """Entry point for the auto-discovered TOOL below."""
    parameters = parameters if isinstance(parameters, dict) else {}
    action = str(parameters.get("action") or "add").strip().lower()
    try:
        if action in ("add", "remember", "set"):
            return _add(parameters, player, speak)
        if action in ("list", "show", "all"):
            return _list(player, speak)
        if action in ("forget", "delete", "remove"):
            return _forget(parameters, player)
        if action in ("enable", "disable"):
            return _toggle(parameters, player, action == "enable")
        if action == "clear":
            return _clear(player)
        return (f"I do not know the '{action}' action. I can add, list, forget, "
                f"enable, disable or clear standing instructions.")
    except Exception as e:
        return f"Something went wrong with that instruction: {e}"


TOOL = {
    "name": "learned_rules",
    "description": (
        "Save a permanent instruction from the user that changes how JARVIS "
        "behaves from then on — 'remember: always open links in Chrome', 'from now "
        "on reply in Hindi', 'never move files out of my Downloads folder'. Use it "
        "whenever the user corrects behaviour in a way that should stick beyond "
        "this conversation, or explicitly asks it to remember a rule. Also use it "
        "to list, switch off or forget those instructions. "
        "Do NOT use it for one-off requests ('open Chrome now') or for facts about "
        "the user — personal facts belong in memory, not here."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": ("One of: add (the default), list, forget, enable, "
                                "disable, clear."),
            },
            "rule": {
                "type": "STRING",
                "description": ("For 'add': the instruction, phrased as a lasting "
                                "rule rather than a request ('always …', 'never …', "
                                "'reply in …'). For forget/enable/disable: the rule "
                                "id or a distinctive fragment of its wording."),
            },
            "category": {
                "type": "STRING",
                "description": ("Optional grouping for 'add': general, style, "
                                "security, workflow, formatting."),
            },
            "rule_id": {
                "type": "STRING",
                "description": "For forget/enable/disable: the id shown by 'list'.",
            },
        },
        "required": ["action"],
    },
    "handler": learned_rules,
}
