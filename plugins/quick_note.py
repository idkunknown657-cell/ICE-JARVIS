"""JARVIS companion plugin: jot and recall a short note.

A tiny local scratchpad so JARVIS can remember a one-line note between
conversations ("remind me this weekend", "the wifi password is…"). Stored in
config/plugin_notes.json — plain text, capped, never secret credentials.
"""

import json
from pathlib import Path

_BASE = Path(__file__).resolve().parent.parent / "config"
_NOTES_PATH = _BASE / "plugin_notes.json"
_MAX = 40

PLUGIN = {
    "name": "quick_note",
    "description": (
        "Saves or recalls a short one-line note JARVIS keeps for you locally. "
        "Trigger phrases: 'note this down', 'remember this', 'remind me later', "
        "'what's in my notes?', 'show my notes'. For proper reminders use "
        "'reminder' — NOT this plugin."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action":  {"type": "STRING", "description": "'save' | 'read' | 'clear'. Default: read."},
            "note":    {"type": "STRING", "description": "The note text (save only)."},
        },
        "required": [],
    },
}


def run(parameters: dict, player=None, session_memory=None) -> str:
    try:
        action = str(parameters.get("action", "read")).strip().lower()
        notes = _load()
        if action == "save":
            text = str(parameters.get("note", "")).strip()
            if not text:
                return "Sir, there's nothing to save — type the note first."
            notes.append(text)
            if len(notes) > _MAX:
                del notes[:-_MAX]
            _save(notes)
            spoken = f"Noted: “{text[:90]}”. I've got it."
        elif action == "clear":
            _save([])
            spoken = "Notes wiped clean, sir."
        else:  # read
            if not notes:
                return "No notes saved yet, sir — say 'note this down' and I'll keep it."
            spoken = "Your notes: " + " | ".join(f"“{n[:60]}”" for n in notes[-5:])
    except Exception as e:
        return f"Sir, my notes went sideways: {e}"
    if player:
        try:
            player.write_log(f"JARVIS: {spoken}")
        except Exception:
            pass
    return spoken


def _load() -> list[str]:
    try:
        data = json.loads(_NOTES_PATH.read_text(encoding="utf-8"))
        return [str(x)[:200] for x in data if str(x).strip()][-_:].copy()
    except Exception:
        return []


def _save(notes: list[str]) -> None:
    _BASE.mkdir(parents=True, exist_ok=True)
    _NOTES_PATH.write_text(json.dumps(notes, ensure_ascii=False, indent=2),
                           encoding="utf-8")
