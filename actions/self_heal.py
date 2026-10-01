"""
actions/self_heal.py — "fix yourself" as a thing you can say out loud.

The interesting action is `heal`, which runs the full pipeline from
core/self_heal.py against the most recent crash. Two things make it worth having
as a tool rather than only as a background behaviour: the user usually knows when
something broke and will say so, and the file-by-file reason it gives when it
refuses is the difference between "it can't fix that" and "it won't touch its own
updater, on purpose".
"""
from __future__ import annotations

from pathlib import Path

from core import self_heal as heal_engine
from core import boot_sentry

_TAG = "[SelfHeal]"


def _log(player, text: str) -> None:
    if player:
        try:
            player.write_log(f"JARVIS: {text}")
        except Exception:
            pass


def _too_short(traceback: str) -> bool:
    return len(str(traceback or "").strip()) < 20


def _heal(parameters: dict, player, speak) -> str:
    traceback_text = str(parameters.get("traceback") or parameters.get("error")
                         or parameters.get("error_traceback") or "")
    context = str(parameters.get("notes") or parameters.get("context") or "")

    if not traceback_text.strip():
        traceback_text = heal_engine.last_error()

    if not traceback_text.strip():
        return ("I have no recent error to work from. If something just failed, "
                "paste the message and I will take a look — or say \"show my "
                "recent errors\" if you want to see what I have logged.")

    _log(player, "Reading the traceback and preparing a fix…")
    result = heal_engine.heal(traceback_text, context=context)

    if result.get("ok") and result.get("patch_id"):
        text = str(result.get("message") or "Fixed it.")
        _log(player, text)
        if speak:
            try:
                speak("I found the fault and patched it. It will hold for the rest "
                      "of this session.")
            except Exception:
                pass
        return text

    if result.get("stage") == "awaiting_confirmation":
        # The banner is up. Say so without claiming anything was done — the same
        # contract core/confirm.py uses everywhere else.
        _log(player, "A fix is ready — waiting for confirmation on screen.")
        return ("I worked out the fix and it is on screen waiting for you: press "
                "CONFIRM and I will apply it. I have not changed anything yet.")

    if result.get("stage") == "locate":
        return str(result.get("message") or "I could not find the failing file.")

    if result.get("stage") == "synthesize":
        return f"I looked at it but could not write a safe fix: " \
               f"{result.get('message', '')}"

    return str(result.get("message") or "I could not repair that.")


def _history(player, speak) -> str:
    patches = heal_engine.applied_patches(6)
    if not patches:
        return ("I have not patched myself. Nothing has needed it, or nothing has "
                "been confirmed yet.")
    lines = ["Fixes I have applied to my own code:"]
    for entry in patches:
        lines.append(f"  [{entry['id']}] {entry['name']} line {entry['line']} "
                     f"({entry['at']}): {entry['explanation']}")
    lines.append(f"Backups are in {heal_engine.backup_folder()}.")
    text = "\n".join(lines)
    _log(player, text)
    if speak:
        try:
            speak(f"I have applied {len(patches)} fixes to myself. The most recent "
                  f"was {patches[0]['name']}.")
        except Exception:
            pass
    return text


def _rollback(parameters: dict, player, speak) -> str:
    patch_id = str(parameters.get("patch_id") or "latest")
    result = heal_engine.rollback(patch_id)
    text = str(result.get("message") or "Done.")
    _log(player, text)
    if speak and result.get("ok"):
        try:
            speak("I have put the previous version back.")
        except Exception:
            pass
    return text


def _last_errors(player) -> str:
    text = heal_engine.last_error()
    if not text:
        return "I have not seen any errors this session."
    parsed = heal_engine.parse_traceback(text)
    if not parsed.get("ok"):
        return (f"Something went wrong that I cannot patch: {parsed.get('reason')}.\n\n"
                f"Last lines:\n{text[-800:]}")
    return (f"The most recent failure was {Path(parsed['path']).name} line "
            f"{parsed['line']}: {parsed.get('error_type', '')} — "
            f"{parsed.get('message', '')}\n\n"
            f"Say \"fix that\" and I will prepare a patch.")


def _why(parameters: dict, player) -> str:
    target = str(parameters.get("file") or parameters.get("path") or "")
    if not target:
        names = ", ".join(sorted(heal_engine.PROTECTED))
        return ("I never patch these files, whatever the crash says, because they "
                "are how I start, update or undo my own changes:\n  " + names)
    return heal_engine.why_not_patchable(target)


def _clear(parameters: dict, player) -> str:
    removed = heal_engine.forget_history()
    return (f"Cleared {removed} entr{'y' if removed == 1 else 'ies'} from the patch "
            f"log. The backups themselves are still there."
            if removed else "The patch log was already empty.")


def self_heal(parameters: dict, player=None, speak=None, response=None,
              session_memory=None) -> str:
    """Entry point for the auto-discovered TOOL below."""
    parameters = parameters if isinstance(parameters, dict) else {}
    action = str(parameters.get("action") or "status").strip().lower()

    try:
        if action in ("heal", "fix", "patch", "repair"):
            return _heal(parameters, player, speak)
        if action in ("history", "list", "patches"):
            return _history(player, speak)
        if action in ("rollback", "undo", "revert"):
            return _rollback(parameters, player, speak)
        if action in ("errors", "last_error", "what_went_wrong"):
            return _last_errors(player)
        if action in ("why", "protected", "boundaries"):
            return _why(parameters, player)
        if action in ("clear", "forget"):
            return _clear(parameters, player)
        if action in ("verify", "healthy", "confirm_start"):
            # Called once the session is genuinely up: tells the boot sentry the
            # most recent patch survived, so it will not be undone at next start.
            cleared = boot_sentry.mark_healthy()
            return ("Patch confirmed good — I have stopped watching it."
                    if cleared else "There was no pending patch to confirm.")
        if action in ("status", "about"):
            text = heal_engine.status()
            _log(player, text)
            if speak:
                try:
                    speak("My self-repair system is ready.")
                except Exception:
                    pass
            return text
        return (f"I do not know the '{action}' action. I can heal, show history, "
                f"roll back a patch, show the last error, or report status.")
    except Exception as e:
        print(f"{_TAG} {action} failed: {e}")
        return f"Something went wrong handling that: {e}"


TOOL = {
    "name": "self_heal",
    "description": (
        "Diagnose and repair a crash in JARVIS's own code. Use this when the user "
        "says something is broken, asks it to fix itself, reports an error or a "
        "traceback, or when a tool has just failed and the user wants it sorted. "
        "It reads the traceback, finds the failing line in its own files, writes a "
        "minimal fix and — by default — puts it on screen for the user to confirm "
        "before applying it. Also use it to review or undo its own patches, to "
        "explain which files it will never modify, and to report self-repair "
        "status. Do NOT use it to change behaviour the user asked for; that is a "
        "feature request, not a bug."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": ("One of: heal (prepare or apply a fix — the default), "
                                "history (patches applied), rollback (undo one), "
                                "errors (what failed most recently), why (which files "
                                "it will not touch), clear, status."),
            },
            "traceback": {
                "type": "STRING",
                "description": ("For 'heal': the error text or traceback to repair. "
                                "Omit to use the most recent failure it has seen."),
            },
            "notes": {
                "type": "STRING",
                "description": "For 'heal': anything else that might explain the failure.",
            },
            "patch_id": {
                "type": "STRING",
                "description": "For 'rollback': the patch to undo. Defaults to the latest.",
            },
            "file": {
                "type": "STRING",
                "description": "For 'why': the file to ask about.",
            },
        },
        "required": ["action"],
    },
    "handler": self_heal,
}
