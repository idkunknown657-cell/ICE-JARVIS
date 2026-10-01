"""
actions/skill_forge.py — the tool behind "learn how to do X".

Two things happen here that core/skill_forge.py deliberately does not do: the
result is phrased for someone listening rather than reading, and a skill that was
just forged is immediately *used* on the request that prompted it. "Learn how to
check my internet speed" should end with a speed reading, not with an
announcement that a tool now exists — the announcement is only the fallback when
the new skill cannot answer the original question.

The other actions are the maintenance surface: list what it has taught itself,
switch one off, delete one, and inspect anything that failed verification and is
therefore parked in config/forge_staging/ rather than live.
"""
from __future__ import annotations

from core import skill_forge as forge
from core import skill_crucible as crucible
from core import skill_registry as registry

_TAG = "[SkillForge]"


def _log(player, text: str) -> None:
    if player:
        try:
            player.write_log(f"JARVIS: {text}")
        except Exception:
            pass


def _visible(player, condition, text: str) -> None:
    """Transient HUD note for the long parts. Uses the interface's status line
    when it offers one, and is silent when it does not — a forge on a headless
    run still works, the user just sees less of it happening."""
    if not player:
        return
    try:
        player.set_state("THINKING", detail=text) if condition else None
    except Exception:
        pass


def _errorish(text: str) -> bool:
    low = str(text or "").strip().lower()
    return not low or low.startswith(("error", "failed", "could not", "i cannot",
                                      "no ", "unknown", "there is no"))


def _first_string_param(parameters: dict) -> str:
    props = parameters.get("properties") if isinstance(parameters, dict) else None
    if not isinstance(props, dict):
        return ""
    for name, spec in props.items():
        if isinstance(spec, dict) and str(spec.get("type", "STRING")).upper() == "STRING":
            return str(name)
    return ""


def _run_new_skill(name: str, goal: str, player, speak) -> str:
    """Use the tool that was just written, on the request that prompted it.

    Called with an empty argument set first, because the crucible proved that
    works. Only if the skill itself reports it could not answer does the goal get
    handed to its first string parameter — so a skill that legitimately needs a
    search term still gets one, and one that needs nothing is never handed a
    nonsense argument it would then use as data.
    """
    try:
        from core import plugin_loader
        reg = plugin_loader.active_registry()
        if reg is None or not reg.has(name):
            return ""
    except Exception:
        return ""

    result = ""
    try:
        result = reg.run(name, {}, player=player, session_memory=None) or ""
    except Exception as e:
        print(f"{_TAG} first run of '{name}' raised: {e}")

    if not _errorish(result):
        return str(result)

    entry = registry.get(name) or {}
    param = _first_string_param(entry.get("parameters") or {})
    if not param:
        return str(result)

    print(f"{_TAG} retrying '{name}' with {param}=<goal>")
    try:
        second = reg.run(name, {param: goal, "query": goal, "goal": goal,
                                "text": goal}, player=player, session_memory=None)
        if second and not _errorish(second):
            return str(second)
    except Exception as e:
        print(f"{_TAG} second run of '{name}' raised: {e}")
    return str(result)


# ── actions ──────────────────────────────────────────────────────────────────

def _forge(parameters: dict, player, speak) -> str:
    goal = str(parameters.get("goal") or parameters.get("request")
               or parameters.get("query") or "").strip()
    if not goal:
        return ("Tell me what you would like me to learn — for example, "
                "\"learn how to check my internet speed\".")

    if speak:
        try:
            speak("Writing a new tool for that now. Give me a moment.")
        except Exception:
            pass
    _visible(player, True, f"Writing a new skill: {goal[:48]}")

    result = forge.forge(goal=goal, name=str(parameters.get("skill_name") or ""),
                         context=str(parameters.get("context") or ""))
    _visible(player, False, "")

    if not result.get("ok"):
        return str(result.get("message") or "I could not learn that.")

    name = str(result.get("name") or "")
    if not result.get("activated"):
        # Verified but held back: the user asked to approve new code by hand.
        entry = result.get("staged_path", "")
        return (f"{result.get('message')} The file is at {entry} — say \"turn on "
                f"self-forge auto\" and I will activate it myself next time.")

    answer = _run_new_skill(name, goal, player, speak)
    summary = f"I learned '{name}'. {result.get('description', '')}".strip()
    if answer:
        summary = f"{summary}\n\nAnswering your original request:\n{answer}"
    _log(player, summary)
    if speak:
        try:
            spoken = answer if answer and len(answer) < 260 else summary
            speak(str(spoken)[:400])
        except Exception:
            pass
    return summary


def _list(player, speak) -> str:
    text = forge.inventory()
    _log(player, text)
    if speak:
        try:
            forged = registry.list_skills()
            if forged:
                speak(f"I have taught myself {len(forged)} skill"
                      f"{'s' if len(forged) != 1 else ''}: "
                      + ", ".join(str(e.get("name")) for e in forged[:5]) + ".")
            else:
                speak("I have not taught myself anything yet.")
        except Exception:
            pass
    return text


def _run(parameters: dict, player, speak) -> str:
    """Run a self-taught skill, chosen either by name or from the sentence.

    The sentence path is the one that costs nothing: find_matching_skill is
    deterministic, so "use the speed test skill" reaches the right tool without a
    model call deciding which tool to use.
    """
    name = str(parameters.get("skill_name") or "").strip()
    if not name:
        query = str(parameters.get("query") or "").strip()
        if not query:
            return "Which skill should I run?"
        match = registry.find_matching_skill(query)
        if not match:
            return (f"Nothing I have taught myself matches \"{query}\". "
                    f"Say \"list my skills\" to hear what I have.")
        name = match[0]

    entry = registry.get(name)
    if entry and not entry.get("active", True):
        return f"'{name}' is switched off. Say \"enable {name}\" first."

    args = parameters.get("arguments")
    args = args if isinstance(args, dict) else {}

    try:
        from core import plugin_loader
        reg = plugin_loader.active_registry()
        if reg is not None and reg.has(name):
            registry.record_invocation(name)
            out = reg.run(name, args, player=player, session_memory=None)
            text = out if isinstance(out, str) else str(out)
            _log(player, text)
            return text
    except Exception as e:
        registry.record_error(name, str(e))
        return f"The '{name}' skill failed: {e}"

    text = registry.run_vault_skill(name, args)
    _log(player, text)
    return text


def _toggle(parameters: dict, player, name: str, active: bool) -> str:
    if not registry.is_known(name):
        return f"I have no record of a skill called '{name}'."
    entry = registry.toggle(name, active)
    state = "on" if entry.get("active") else "off"
    text = f"'{name}' is now switched {state}."
    _log(player, text)
    return text


def _delete(parameters: dict, player, name: str) -> str:
    entry = registry.get(name)
    if not entry:
        return f"I have no record of a skill called '{name}'."
    if str(entry.get("source")) != "forged":
        return (f"'{name}' was not written by me, so I will not delete its file. "
                f"You can remove it from the plugins folder yourself.")
    registry.forget(name, remove_file=True)
    try:
        from core import plugin_loader
        reg = plugin_loader.active_registry()
        if reg is not None:
            reg.reload()
    except Exception:
        pass
    text = f"Deleted the '{name}' skill."
    _log(player, text)
    return text


def _staged(player) -> str:
    items = forge.staged()
    if not items:
        return "Nothing is waiting in staging — every skill I wrote either passed or is gone."
    lines = ["Skills that did not pass verification (kept for inspection):"]
    for item in items[:10]:
        lines.append(f"  - {item['name']} ({item['size']} bytes, {item['written']})")
    lines.append("Say \"clear staged skills\" to delete them.")
    text = "\n".join(lines)
    _log(player, text)
    return text


def _status(player, speak) -> str:
    stats = registry.stats()
    try:
        from memory import config_manager as cfg
        writing = cfg.get_self_forge_enabled()
        auto = cfg.get_self_forge_auto()
    except Exception:
        writing, auto = True, True

    lines = [
        "SELF-FORGE STATUS",
        f"  Writing new skills: {'on' if writing else 'off'}",
        f"  Activate automatically once verified: {'yes' if auto else 'no'}",
        f"  Skills I wrote: {stats['forged']} ({stats['active']} active, "
        f"{stats['invocations']} uses)",
        f"  Skill packages installed: {stats['vault_packages']}",
    ]
    if stats["vault_broken"]:
        lines.append(f"  Unreadable packages: {stats['vault_broken']}")
    if stats["last"]:
        lines.append(f"  Most recent: {stats['last'].get('name')}")
    waiting = forge.staged()
    if waiting:
        lines.append(f"  Waiting in staging: {len(waiting)}")
    text = "\n".join(lines)
    _log(player, text)
    if speak:
        try:
            speak(f"I have written {stats['forged']} skill"
                  f"{'s' if stats['forged'] != 1 else ''} for myself, "
                  f"{stats['invocations']} uses so far.")
        except Exception:
            pass
    return text


def _route(parameters: dict, player) -> str:
    """Which skill would handle this sentence? Exposed so the answer is visible
    rather than an invisible guess — and so the router can be checked without
    letting it act."""
    query = str(parameters.get("query") or parameters.get("goal") or "").strip()
    if not query:
        return "Give me a sentence and I will say which of my skills fits it."
    match = registry.find_matching_skill(query)
    if not match:
        return f"None of my self-taught skills match \"{query}\"."
    return f"\"{query}\" routes to '{match[0]}' with {match[1] or 'no arguments'}."


def skill_forge(parameters: dict, player=None, speak=None,
                response=None, session_memory=None) -> str:
    """Entry point for the auto-discovered TOOL below."""
    parameters = parameters if isinstance(parameters, dict) else {}
    action = str(parameters.get("action") or "forge").strip().lower()
    name = str(parameters.get("skill_name") or "").strip()

    try:
        if action in ("forge", "learn", "create"):
            return _forge(parameters, player, speak)
        if action in ("list", "skills", "inventory"):
            return _list(player, speak)
        if action in ("run", "use", "execute"):
            return _run(parameters, player, speak)
        if action in ("enable", "disable"):
            if not name:
                return "Which skill?"
            return _toggle(parameters, player, name, action == "enable")
        if action in ("delete", "forget", "remove"):
            if not name:
                return "Which skill should I delete?"
            return _delete(parameters, player, name)
        if action in ("staged", "failed", "review"):
            return _staged(player)
        if action in ("clear_staged", "clear"):
            removed = forge.forget_staged(name)
            return (f"Deleted {removed} staged file{'s' if removed != 1 else ''}."
                    if removed else "Nothing was staged.")
        if action in ("route", "match"):
            return _route(parameters, player)
        if action in ("status", "about"):
            return _status(player, speak)
        return (f"I do not know the '{action}' action. I can forge, list, run, "
                f"enable, disable, delete, review staged, or report status.")
    except Exception as e:
        print(f"{_TAG} {action} failed: {e}")
        return f"Something went wrong handling that: {e}"


TOOL = {
    "name": "skill_forge",
    "description": (
        "Teach JARVIS a capability it does not already have, by writing a new "
        "tool for itself. Use this when the user asks it to LEARN something "
        "('learn how to check my internet speed', 'teach yourself to look up "
        "crypto prices', 'make yourself able to calculate a Vedic square'), or "
        "when a task genuinely cannot be done with the tools you already have and "
        "the capability is a small, self-contained lookup or calculation. It "
        "writes the code, verifies it in a sandbox, and can use it immediately. "
        "Also use it to list, run, enable, disable or delete skills it wrote "
        "earlier. Do NOT use it for something an existing tool already does, and "
        "do NOT use it for actions that change the user's machine — it is for "
        "reading, computing and reporting."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": (
                    "One of: forge (write a new skill — the default), list (what "
                    "it has taught itself), run (use one), enable, disable, delete, "
                    "staged (skills that failed verification), clear_staged, "
                    "route (which skill fits a sentence), status."
                ),
            },
            "goal": {
                "type": "STRING",
                "description": (
                    "For 'forge': the capability, phrased as the user's request. "
                    "Be specific about what the answer should contain."
                ),
            },
            "skill_name": {
                "type": "STRING",
                "description": "For forge/enable/disable/delete/run: the skill's name.",
            },
            "query": {
                "type": "STRING",
                "description": (
                    "For 'run' without a skill_name, or for 'route': the user's "
                    "own sentence, so the matching skill can be found."
                ),
            },
            "context": {
                "type": "STRING",
                "description": "For 'forge': extra detail the new tool needs.",
            },
            "arguments": {
                "type": "OBJECT",
                "description": "For 'run': the arguments to call the skill with.",
            },
        },
        "required": ["action"],
    },
    "handler": skill_forge,
}
