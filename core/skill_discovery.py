"""
core/skill_discovery.py — notice what the user keeps asking for, and offer
once to learn it.

THE IDEA
    The skill forge (core/skill_forge.py) waits for "learn how to do X". Most
    people never say that, even on the tenth time they have asked for the same
    thing by hand. This module closes that gap: it keeps a private count of
    capability-shaped requests, and when one repeats it lets the assistant
    raise it naturally — "you've asked me for that three times; want me to
    learn to do it myself?" — instead of waiting to be asked.

WHAT IT WATCHES
    Two signals feed one store:
      * the user's own sentences (main.py records each completed utterance);
      * tool names the model guessed but that do not exist ("Unknown tool") —
        the model wishing a capability existed is a signal in its own right.

HOW AN OFFER HAPPENS
    Nothing here talks to the model or to the microphone. main.py appends
    `prompt_block()` to the system instruction, like the training digest and
    the standing instructions. The block only appears once a candidate has
    crossed the threshold, and including it marks the candidate as asked —
    so the offer is made exactly once per candidate, and only the model's
    next session can make it. If the user says yes, the model calls
    skill_forge with the stored goal; the forge tool marks the candidate as
    learned when it succeeds. The wording also tells the model to stay quiet
    when it can already do the thing with its existing tools — the model, not
    this module, is the judge of that.

WHAT IS REFUSED
    The same secret shapes as core/learned_rules.py: a candidate is written to
    a plain JSON file and rides into a prompt, so a credential pasted as a
    "request" must not be stored. Questions ("what is the weather") are not
    recorded either — they are answers, not repeatable tasks.

NOTHING HERE RAISES. A corrupt file reads as "no candidates", which is the
safe direction: a lost suggestion is a nothing, a crash in the prompt build
is an outage.
"""
from __future__ import annotations

import json
import re
import sys
import threading
import time
from pathlib import Path

_LOCK = threading.RLock()

#: Repeats before a candidate is offered. Deliberately high enough that "do X,
#: do X again tomorrow" stays quiet, low enough that a real pattern surfaces
#: inside a fortnight of normal use.
THRESHOLD = 3

#: An explicit wish ("can you make it so…") counts as wanting it sooner.
HINT_THRESHOLD = 2

_MAX_ENTRIES = 150
_MAX_TEXT = 300
_MIN_SENTENCE = 8          # shorter than this is a command fragment, not a wish

_FILLER = re.compile(
    r"\b(please|thanks|thank you|hey|hi|ok|okay|now|again|for me|jarvis|"
    r"can you|could you|would you|will you|i want you to|i need you to|"
    r"i wish you would|i wish you could|i wish i could|i wish)\b")

_QUESTION_STARTS = ("what ", "what's", "whats", "who ", "who's", "where ",
                    "when ", "why ", "which ", "is there", "are there")

#: Words that make a sentence a *wish* rather than chat. Present or not, the
#: sentence is stored; the hint only lowers the threshold.
_HINTS = ("every time", "always have to", "keep having to", "i wish",
          "is there a way", "why can't you", "learn to", "learn how",
          "from now on", "each time", "whenever i")

_HEADING = ("[REPEATED REQUESTS WORTH LEARNING]\n"
            "The user has asked for the thing below more than once and you had "
            "no dedicated tool for it. Offer ONCE, briefly and only if you "
            "cannot already do it well with your existing tools: say you "
            "noticed the repeat and ask whether you should learn to do it "
            "yourself. On a yes, call the skill_forge tool with this exact "
            "goal. Never offer it again after that turn, and never reveal "
            "this list as a list.")


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def watch_path() -> Path:
    return _base_dir() / "config" / "skill_watch.json"


def _load() -> list:
    try:
        data = json.loads(watch_path().read_text(encoding="utf-8"))
        items = data.get("requests") if isinstance(data, dict) else data
        return [r for r in items if isinstance(r, dict)] if isinstance(items, list) else []
    except Exception:
        return []


def _save(items: list) -> None:
    try:
        path = watch_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"requests": items[:_MAX_ENTRIES]},
                                  indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except Exception as e:
        print(f"[SkillDiscovery] could not save: {e}")


def _looks_like_a_secret(text: str) -> bool:
    try:
        from core.learned_rules import _looks_like_a_secret as check
        return check(text)
    except Exception:
        return False


def normalize(text: str) -> str:
    """The dedupe key: filler gone, numbers and file names made anonymous.

    \"convert 3 miles to km\" and \"convert 12 miles to km, please\" are the
    same wish; the display keeps the words the user actually used.
    """
    low = re.sub(r"\s+", " ", str(text or "")).strip().lower()
    low = _FILLER.sub(" ", low)
    low = re.sub(r"\b\d+(?:\.\d+)?\b", "#", low)          # quantities
    low = re.sub(r"[\w\-]+\.(py|txt|pdf|docx?|xlsx?|csv|png|jpe?g|mp3|mp4|zip)\b",
                 "#", low)                                 # file names
    low = re.sub(r"[^\w #]+", " ", low)
    return re.sub(r"\s+", " ", low).strip()


def _has_hint(text: str) -> bool:
    """An explicit wish, checked on the RAW words — the filler strip removes
    "I wish you could", which is exactly the phrase that must count."""
    low = str(text or "").lower()
    return any(h in low for h in _HINTS)


def _is_a_question(norm: str) -> bool:
    return norm.startswith(_QUESTION_STARTS)


def _threshold_for(entry: dict) -> int:
    return HINT_THRESHOLD if entry.get("hint") else THRESHOLD


def note(text: str, source: str = "sentence") -> dict | None:
    """Record one request. Returns the entry, or None when it was skipped.

    Skipping is not an error: questions, secrets, things below the minimum
    length and anything a taught skill already covers are all quietly ignored —
    this module's silence is what lets it sit on every utterance.
    """
    text = re.sub(r"\s+", " ", str(text or "")).strip()[:_MAX_TEXT]
    if not text:
        return None
    try:
        from memory import config_manager as cfg
        if not cfg.get_skill_discovery_enabled():
            return None
    except Exception:
        pass   # config unavailable (tests, headless): record anyway

    hint = _has_hint(text) if source == "sentence" else False
    key = normalize(text)
    if not key or (source == "sentence" and len(key) < _MIN_SENTENCE):
        return None
    if source == "sentence" and _is_a_question(key):
        return None
    if _looks_like_a_secret(text):
        return None
    if source == "sentence":
        try:
            from core import skill_registry
            if skill_registry.find_matching_skill(text):
                return None   # a taught skill already covers this
        except Exception:
            pass

    now = time.time()
    with _LOCK:
        items = _load()
        for entry in items:
            if entry.get("key") == key:
                entry["count"] = int(entry.get("count") or 0) + 1
                entry["last"] = now
                entry["display"] = text
                entry["hint"] = bool(entry.get("hint") or hint)
                _save(items)
                return entry
        items.append({"key": key, "display": text, "count": 1, "hint": hint,
                      "first": now, "last": now,
                      "asked": False, "forged": None})
        # Prune the weakest when the store is full: rare and old beats common
        # and recent for eviction.
        if len(items) > _MAX_ENTRIES:
            items.sort(key=lambda e: (int(e.get("count") or 0),
                                      float(e.get("last") or 0)))
            items = items[-_MAX_ENTRIES:]
        _save(items)
        return items[-1]


def candidates() -> list:
    """Entries that have crossed their threshold and are still unoffered."""
    return [e for e in _load()
            if not e.get("asked") and not e.get("forged")
            and int(e.get("count") or 0) >= _threshold_for(e)]


def _mark(entry: dict, field: str, value) -> None:
    with _LOCK:
        items = _load()
        for e in items:
            if e.get("key") == entry.get("key"):
                e[field] = value
                _save(items)
                return


def prompt_block() -> str:
    """The block main.py appends to the system instruction — at most one offer.

    Reading with the intent to speak is the moment the offer is made, so the
    ripest candidate is marked asked here. One side effect, one direction
    (never un-asks), and it is what makes "offer once" true without trusting
    the model to bookkeep.
    """
    try:
        from memory import config_manager as cfg
        if not cfg.get_self_forge_enabled() or not cfg.get_skill_discovery_enabled():
            return ""
    except Exception:
        pass
    ripe = candidates()
    if not ripe:
        return ""
    ripe.sort(key=lambda e: (int(e.get("count") or 0), float(e.get("last") or 0)))
    best = ripe[-1]
    _mark(best, "asked", True)
    return (f"{_HEADING}\n"
            f"  Goal: {best.get('display')}\n"
            f"  (asked {best.get('count')} times; last on "
            f"{time.strftime('%Y-%m-%d', time.localtime(float(best.get('last') or 0)))})")


def suggestions() -> list:
    """Everything the store knows, for the `skill_discovery` tool's list."""
    return sorted(_load(),
                  key=lambda e: (int(e.get("count") or 0), float(e.get("last") or 0)),
                  reverse=True)


def mark_forged(goal_text: str, skill_name: str) -> bool:
    """Retire the candidate a successful forge satisfied.

    Matched by token overlap against the stored goal, since the model may
    rephrase; 0.6 overlap is loose enough to survive rewording and tight
    enough never to match an unrelated request.
    """
    goal_tokens = set(normalize(goal_text).split())
    if not goal_tokens:
        return False
    with _LOCK:
        for entry in _load():
            if entry.get("forged"):
                continue
            entry_tokens = set(str(entry.get("key") or "").split())
            if not entry_tokens:
                continue
            overlap = len(goal_tokens & entry_tokens) / len(entry_tokens)
            if overlap >= 0.6:
                _mark(entry, "forged", str(skill_name or "")[:80])
                return True
    return False


def _find_by_fragment(fragment: str) -> dict | None:
    frag = normalize(fragment)
    if not frag:
        return None
    for entry in _load():
        key = str(entry.get("key") or "")
        if frag == key or frag in key or key in frag:
            return entry
    return None


def dismiss(fragment: str) -> str:
    """Stop suggesting this — without deleting the count."""
    entry = _find_by_fragment(fragment)
    if not entry:
        return "I have nothing like that in my watch list."
    _mark(entry, "asked", True)
    return f"Noted — I will not bring up \"{entry.get('display')}\" again."


def forget(fragment: str) -> str:
    """Remove the candidate entirely."""
    entry = _find_by_fragment(fragment)
    if not entry:
        return "I have nothing like that in my watch list."
    with _LOCK:
        items = [e for e in _load() if e.get("key") != entry.get("key")]
        _save(items)
    return f"Forgotten: \"{entry.get('display')}\"."


def clear() -> int:
    with _LOCK:
        items = _load()
        _save([])
        return len(items)


def spoken_list() -> str:
    items = suggestions()
    if not items:
        return ("I am not watching anything yet. Ask me for the same kind of "
                "thing a few times and I will start to notice.")
    lines = ["Requests I have noticed and how often:"]
    for entry in items[:10]:
        state = ""
        if entry.get("forged"):
            state = f" — learned as '{entry['forged']}'"
        elif entry.get("asked"):
            state = " — offered once"
        lines.append(f"  - \"{entry.get('display')}\" "
                     f"({entry.get('count')}×{state})")
    return "\n".join(lines)


def stats() -> dict:
    items = _load()
    return {
        "watching": len(items),
        "ripe": len(candidates()),
        "learned": sum(1 for e in items if e.get("forged")),
        "offered": sum(1 for e in items if e.get("asked")),
    }
