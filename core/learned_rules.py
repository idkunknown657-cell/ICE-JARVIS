"""
core/learned_rules.py — the standing instructions the user gives on purpose.

WHY THIS IS SEPARATE FROM core/learning.py
    learning.py *infers* things: it reads a conversation and works out that this
    person likes short answers, or that a project exists. That is useful and it is
    guesswork. This module is the other half — the instructions a user states
    outright ("always open links in Chrome", "never touch my Downloads folder",
    "reply in Hindi"). Those are not inferences to be scored and forgotten; they
    are rules, and they belong somewhere the person can see, switch off and delete.

    The distinction matters in both directions:
      * an inferred style note that turns out wrong should fade, and learning.py's
        dedupe and caps handle that;
      * an explicit rule that turns out wrong must be *visible*, with an id, so
        "forget rule 3" is a thing that can be said.
    So there are two stores, and `improve_list`/`forget` here are about the second.

HOW A RULE REACHES THE MODEL
    Nothing here talks to the model. `prompt_block()` returns a short block of
    text that main.py appends to the system instruction at connect time, exactly
    like the training digest — one place, one format, and a rule added mid-
    conversation is picked up at the next session without restarting.

WHAT IS NOT ALLOWED IN
    Passwords, API keys and tokens. The block is injected into every prompt and
    written into a plain JSON file, so a credential pasted here would be both
    echoed to the model on every turn and left sitting in config/. A rule that
    looks like a secret is refused with that reason — the one case where this
    module argues back.

NOTHING HERE RAISES. A corrupt file reads as "no rules", which is the safe
direction: a missing rule is an inconvenience, a corrupted one that crashes the
prompt build is an outage.
"""
from __future__ import annotations

import json
import re
import sys
import threading
import time
import uuid
from pathlib import Path

_LOCK = threading.RLock()

MAX_RULES = 200
MAX_TEXT = 400

#: Shorter than this is not an instruction. It exists to refuse the accidents —
#: a stray number, a single word that lost its sentence — without pretending to
#: judge the content of a rule the user typed on purpose.
MIN_TEXT = 4

#: Shown in the prompt as the heading, so the model can tell a standing
#: instruction apart from the rest of its instructions.
_HEADING = ("[STANDING INSTRUCTIONS FROM THE USER]\n"
            "These are rules the user set deliberately, not preferences you "
            "inferred. Follow them unless they ask you to override one now. If a "
            "rule conflicts with the current request, do what they asked and "
            "mention the rule in one short sentence.")

#: Nothing in this list ever belongs in a prompt or in config/. Checked on the
#: *rule text* only, so a rule such as "never type my password into a form" is
#: still allowed — it is describing an action, not carrying a secret.
_SECRET_SHAPES = (
    re.compile(r"\b(sk|pk|rk|ghp|gho|ghs|xoxb|xoxp)-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}"),
    re.compile(r"\bAKIA[0-9A-Z]{12,}"),
    re.compile(r"\bey[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\."),
    re.compile(r"\b(api[_ ]?key|secret|token|password)\s*[:=]\s*\S{8,}", re.I),
)
_SECRET_WORDS = ("my password is", "the password is", "my api key is",
                 "my token is", "here is my key")


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def rules_path() -> Path:
    return _base_dir() / "config" / "learned_rules.json"


def _load() -> list:
    try:
        data = json.loads(rules_path().read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data = data.get("rules")
        return [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []
    except Exception:
        return []


def _save(rules: list) -> None:
    path = rules_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(rules[-MAX_RULES:], indent=2, ensure_ascii=False),
                       encoding="utf-8")
        tmp.replace(path)
    except Exception as e:
        print(f"[Rules] could not save: {e}")


def _looks_like_a_secret(text: str) -> bool:
    low = text.lower()
    if any(word in low for word in _SECRET_WORDS):
        return True
    return any(pattern.search(text) for pattern in _SECRET_SHAPES)


def _clean(text) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()[:MAX_TEXT]


def add(text: str, category: str = "general", origin: str = "user") -> dict:
    """Store one rule. Refuses secrets, dedupes, and returns the stored entry."""
    rule = _clean(text)
    if not rule:
        return {"ok": False, "message": "There was nothing to remember in that."}
    if len(rule) < MIN_TEXT:
        return {"ok": False,
                "message": (f"\"{rule}\" is too short to be an instruction — tell "
                            f"me the whole rule and I will keep it.")}
    if _looks_like_a_secret(rule):
        # Deliberately emphatic: this is the one refusal a user might not expect,
        # and it needs to explain itself in terms they can act on.
        return {"ok": False, "message":
                ("I have not saved that: it looks like a password, key or token. "
                 "Rules are added to every conversation and written to a plain "
                 "file, so secrets do not belong there. Keep it in the API key "
                 "settings instead.")}

    with _LOCK:
        rules = _load()
        for entry in rules:
            if _clean(entry.get("rule")).lower() == rule.lower():
                entry["active"] = True
                entry["updated_at"] = time.time()
                _save(rules)
                return {"ok": True, "entry": entry, "duplicate": True,
                        "message": f"That rule was already saved — it is active again: "
                                   f"\"{rule}\""}

        entry = {
            "id": uuid.uuid4().hex[:8],
            "rule": rule,
            "category": _clean(category) or "general",
            "origin": _clean(origin) or "user",
            "active": True,
            "created_at": time.time(),
            "updated_at": time.time(),
        }
        rules.append(entry)
        _save(rules)
    return {"ok": True, "entry": entry,
            "message": f"Remembered: \"{rule}\". I will follow it from now on."}


def all_rules() -> list:
    """Every stored rule, oldest first."""
    return _load()


def active_rules() -> list:
    return [r for r in _load() if r.get("active", True)]


def find(rule_id: str) -> dict:
    """A rule by id, or by a distinctive fragment of its text."""
    wanted = str(rule_id or "").strip().lower()
    if not wanted:
        return {}
    for entry in _load():
        if str(entry.get("id")) == wanted:
            return entry
    matches = [e for e in _load() if wanted in _clean(e.get("rule")).lower()]
    return matches[0] if len(matches) == 1 else {}


def toggle(rule_id: str, active: bool | None = None) -> dict:
    with _LOCK:
        rules = _load()
        target = None
        wanted = str(rule_id or "").strip().lower()
        for entry in rules:
            if str(entry.get("id")) == wanted or wanted in _clean(entry.get("rule")).lower():
                target = entry
                break
        if target is None:
            return {"ok": False, "message": f"I have no rule matching '{rule_id}'."}
        target["active"] = (not target.get("active", True)) if active is None \
            else bool(active)
        target["updated_at"] = time.time()
        _save(rules)
    state = "active" if target["active"] else "switched off"
    return {"ok": True, "entry": target, "message": f"That rule is now {state}."}


def forget(rule_id: str) -> dict:
    with _LOCK:
        rules = _load()
        wanted = str(rule_id or "").strip().lower()
        kept, removed = [], None
        for entry in rules:
            if removed is None and (str(entry.get("id")) == wanted
                                    or wanted in _clean(entry.get("rule")).lower()):
                removed = entry
                continue
            kept.append(entry)
        if removed is None:
            return {"ok": False, "message": f"I have no rule matching '{rule_id}'."}
        _save(kept)
    return {"ok": True, "message": f"Forgotten: \"{_clean(removed.get('rule'))}\"."}


def clear() -> int:
    """Delete every rule. Returns how many there were — the count is the only way
    the caller can say something honest about what just happened."""
    with _LOCK:
        count = len(_load())
        _save([])
    return count


def prompt_block() -> str:
    """The block main.py appends to the system instruction. '' when there are no
    active rules, so nothing is added to a prompt that does not need it."""
    entries = active_rules()
    if not entries:
        return ""
    lines = [_HEADING, ""]
    for i, entry in enumerate(entries[:40], 1):
        text = _clean(entry.get("rule"))
        if text:
            lines.append(f"{i}. {text}")
    lines.append("")
    return "\n".join(lines)


def spoken_list() -> str:
    """Every rule as a sentence, for the tool to read back."""
    entries = all_rules()
    if not entries:
        return ("I have no standing instructions saved. Say \"remember: always...\" "
                "and I will keep one.")
    lines = ["Standing instructions:"]
    for entry in entries:
        state = "" if entry.get("active", True) else " (switched off)"
        lines.append(f"  [{entry.get('id')}] {_clean(entry.get('rule'))}{state}")
    lines.append(f"{len(entries)} in total.")
    return "\n".join(lines)


def count() -> int:
    return len(active_rules())


__all__ = ["add", "all_rules", "active_rules", "find", "toggle", "forget",
           "clear", "prompt_block", "spoken_list", "count", "rules_path"]
