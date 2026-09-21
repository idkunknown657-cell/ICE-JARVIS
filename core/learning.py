"""The continuous improvement loop: Observe → Learn → Evaluate → Remember → Apply.

Gemini Live holds the live conversation; this module quietly turns that
conversation into durable knowledge on background threads, the way a person
quietly notices things about a friend:

  - `learn_from_conversation`  — automatically pulls durable facts (preferences,
    projects, people, corrections) from a chunk of chat and merges them into
    long-term memory. No "remember this" needed.
  - `evaluate`                 — periodic self-review of recent turns: what
    worked, what failed, what the user corrected, what to do differently.
  - `apply_improvements`       — keeps the review in a reversible, logged store
    (config/improvements.json) AND distils it into "lessons" that ride into
    future system prompts, so next time JARVIS behaves accordingly.

Safety contract (from the self-improvement brief):
  - touches ONLY two data stores: long-term memory (lessons) and the
    improvements log. Never security settings, permissions, credentials or
    core code.
  - every improvement is logged with old/new so it is reversible; lessons are
    plain memory entries that `forget` can remove.
  - every model call runs in a worker thread from main.py and never raises
    here — a failure just means nothing was learned this round.
"""

import json
import re
import time
import uuid
import sys
from pathlib import Path
from threading import Lock

from core import gemini, reasoner

_FACTS_CATEGORIES = ("identity", "preferences", "projects",
                     "relationships", "wishes", "notes")

_BASE = (Path(sys.executable).resolve().parent
         if getattr(sys, "frozen", False)
         else Path(__file__).resolve().parent.parent)
CONFIG_DIR = _BASE / "config"
IMPROVEMENTS_PATH = CONFIG_DIR / "improvements.json"
_LOCK = Lock()
_MAX_LESSONS = 40
_MAX_VALUE = 220
_MAX_PER_CATEGORY = 12      # memory never bloat: newest N per category survive

_EXTRACT_PROMPT = (
    "You are JARVIS's automatic memory. From the conversation below, pick a "
    "few SHORT, durable facts genuinely worth remembering about the user long "
    "term (preferences, projects, people, plans, corrections — NOT passwords, "
    "API keys or anything private). Also, if the conversation shows how this "
    "person likes JARVIS to TALK — they enjoy jokes, want shorter answers, "
    "corrected the tone, warmed to teasing, dislike emoji — capture that as "
    "ONE memory fact: category 'preferences', key 'style_<aspect>' "
    "(style_humor, style_length, style_tone…), value ≤180 chars. At most one "
    "style fact per extraction; skip when nothing new emerged."
    "API keys or anything private), and up to 2 lessons JARVIS learned about "
    "how to work with this person.\n"
    "Respond with ONLY JSON:\n"
    '{"memory": [{"category": "<identity|preferences|projects|relationships|'
    'wishes|notes>", "key": "short_lowercase_key", "value": "≤180 chars"}], '
    '"lessons": [{"topic": "short_key", "lesson": "≤180 chars"}]}\n'
    "Use at most 4 memory facts. Skip facts already present or trivial. "
    "Empty is fine: {\"memory\": [], \"lessons\": []}\n\n"
    "Conversation:\n{convo}"
)

_REVIEW_PROMPT = (
    "You are JARVIS's self-improvement reviewer. Read the recent conversation "
    "and identify what genuinely went well and what could go better next time. "
    "Pay special attention to the PERSONALITY side: did the user enjoy a joke "
    "or push back on one, react warmly to a cute reaction, find the reply too "
    "long or too flat? That is the most valuable thing to fix. "
    "Respond with ONLY JSON:\n"
    '{"what_worked": ["..."], "what_failed": ["..."], "corrections": ["..."], '
    '"improvements": [{"category": "conversation_style|tool_use|planning|'
    'memory|screen|error_recovery", "note": "one actionable sentence"}]}\n'
    "Max 2 improvements. Focus on concrete, usable lessons for a voice "
    "assistant, not praise.\n\nConversation:\n{convo}"
)


# ── Sanitisation ──────────────────────────────────────────────────────────────

def _clean_key(raw) -> str:
    s = str(raw or "").strip().lower()
    s = re.sub(r"[^a-z0-9_ ]+", "", s).strip()
    return re.sub(r"[ ]+", "_", s)[:60]


def _clean_value(raw) -> str:
    s = (str(raw or "").strip() or "").strip()
    return s[:_MAX_VALUE]


def _sanitize_extraction(data) -> tuple[list[dict], list[tuple]]:
    """Coerce whatever the model returned into safe {category,key,value} dicts
    and (topic, lesson) tuples. Never raises; junk becomes nothing."""
    if not isinstance(data, dict):
        data = {}
    facts: list[dict] = []
    for item in data.get("memory") or []:
        if not isinstance(item, dict):
            continue
        cat = str(item.get("category") or "").strip()
        if cat not in _FACTS_CATEGORIES:
            continue
        key, val = _clean_key(item.get("key")), _clean_value(item.get("value"))
        if key and val:
            facts.append({"category": cat, "key": key, "value": val})

    lessons: list[tuple[str, str]] = []
    for item in data.get("lessons") or []:
        if not isinstance(item, dict):
            continue
        topic = _clean_key(item.get("topic")) or f"lesson_{len(lessons)}"
        text = _clean_value(item.get("lesson"))
        if text:
            lessons.append((topic, text))
    return facts[:4], lessons[:2]


def _similar(a: str, b: str, ratio: float = 0.75) -> bool:
    """Cheap Jaccard similarity on word sets for lesson dedupe. Two short
    strings with ~75% overlap in vocabulary are a repeat."""
    wa = set(re.findall(r"[a-z0-9]+", a.lower()))
    wb = set(re.findall(r"[a-z0-9]+", b.lower()))
    if not wa or not wb:
        return False
    inter, union = len(wa & wb), len(wa | wb)
    return union and (inter / union) >= ratio


# ── Extraction ────────────────────────────────────────────────────────────────

def extract_facts(conversation_text: str, timeout_ms: int = 30_000) -> dict:
    """[worker-thread-safe] Turn a chunk of conversation into sanitised
    {'memory': [...], 'lessons': [...]}. Never raises; on any failure or an
    empty answer it becomes an empty dict so callers stay cheap."""
    if not (conversation_text or "").strip():
        return {"memory": [], "lessons": []}
    tier = reasoner.pick_tier(conversation_text) or gemini.FAST
    prompt = _EXTRACT_PROMPT.replace("{convo}", conversation_text[-4000:])
    try:
        data = gemini.as_json(prompt, tier=tier, timeout_ms=timeout_ms,
                              default=None)
    except Exception as e:
        print(f"[Learning] extract failed: {e}")
        return {"memory": [], "lessons": []}
    facts, lessons = _sanitize_extraction(data)
    return {"memory": facts, "lessons": lessons}


def _merge_facts(facts: list[dict]) -> int:
    """Merge extracted facts into long-term memory, capped per category so even
    years of sessions never grow a category unbounded (newest wins). One load +
    one atomic save on the worker thread. Returns how many applied."""
    from memory import memory_manager as mm
    updates: dict[str, dict] = {}
    for f in facts:
        updates.setdefault(f["category"], {})[f["key"]] = f["value"]
    if not updates:
        return 0
    current = mm.load_memory()
    added = 0
    for cat, kvs in updates.items():
        existing = (current.get(cat, {}) or {})
        merged: dict = dict(existing)
        for key, val in kvs.items():
            old = merged.get(key)
            old_val = str(old.get("value", "")) if isinstance(old, dict) else ""
            if old_val.strip() == val or _similar(old_val, val):
                continue
            merged[key] = {"value": val}
            added += 1
        if len(merged) > _MAX_PER_CATEGORY:
            # Insertion order = memory order; keep the newest cap of entries.
            merged = {k: merged[k] for k in list(merged)[- _MAX_PER_CATEGORY:]}
        current[cat] = merged
    if added:
        mm.save_memory(current)
    return added


def _merge_lessons(lessons: list[tuple[str, str]]) -> int:
    """Store lessons as memory['lessons'][<topic>], deduped, capped. Returns
    how many were new."""
    from memory import memory_manager as mm
    if not lessons:
        return 0
    current = mm.load_memory()
    stored = current.get("lessons", {}) or {}
    if len(stored) >= _MAX_LESSONS:
        return 0
    stored_values = [_entry_text(v) for v in stored.values()]
    added = 0
    for topic, text in lessons:
        if text in stored_values or any(_similar(v, text)
                                        for v in stored_values):
            continue
        mm.update_memory({"lessons": {topic: text}})
        stored_values.append(text)
        added += 1
    return added


def _entry_text(entry) -> str:
    if isinstance(entry, dict):
        return str(entry.get("value", "") or "").strip()
    return str(entry or "").strip()


def learn_from_conversation(lines: list[str]) -> dict:
    """[worker-thread-safe] The Observe→Learn step: extract and persist facts +
    lessons from ≥3 raw conversation lines. Returns a small report dict and
    NEVER raises."""
    if not isinstance(lines, list) or len(lines) < 3:
        return {"facts": 0, "lessons": 0, "turns": len(lines) if lines else 0}
    convo = "\n".join((str(l) for l in lines))[-6000:]
    result = extract_facts(convo)
    try:
        facts_added = _merge_facts(result.get("memory", []))
        lessons_added = _merge_lessons(result.get("lessons", []))
    except Exception as e:
        print(f"[Learning] merge failed: {e}")
        return {"facts": 0, "lessons": 0, "turns": len(lines)}
    if facts_added or lessons_added:
        print(f"[Learning] learned: {facts_added} fact(s), {lessons_added} "
              f"lesson(s) from {len(lines)} turns.")
    return {"facts": facts_added, "lessons": lessons_added, "turns": len(lines)}


# ── Self-evaluation ───────────────────────────────────────────────────────────

def evaluate(conversation_text: str, timeout_ms: int = 30_000) -> list[dict]:
    """[worker-thread-safe] Review recent turns → list of improvement notes.
    Never raises; review failures return [] so the log simply has nothing new."""
    if not (conversation_text or "").strip():
        return []
    prompt = _REVIEW_PROMPT.replace("{convo}", conversation_text[-4000:])
    try:
        data = gemini.as_json(prompt, tier=gemini.SMART,
                              timeout_ms=timeout_ms, default=None)
    except Exception as e:
        print(f"[Learning] review failed: {e}")
        return []
    if not isinstance(data, dict):
        return []
    out: list[dict] = []
    for item in data.get("improvements") or []:
        if not isinstance(item, dict):
            continue
        cat = str(item.get("category") or "").strip()
        note = _clean_value(item.get("note"))
        if note:
            out.append({"category": cat or "conversation_style", "note": note})
    return out[:3]


# ── Improvement log (reversible, logged) ─────────────────────────────────────

def _load_improvements() -> dict:
    try:
        data = json.loads(IMPROVEMENTS_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_improvements(data: dict) -> None:
    IMPROVEMENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        IMPROVEMENTS_PATH.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def log_improvement(category: str, note: str, old: str = "", new: str = "",
                    applied: bool = True, module: str = "auto"):
    """Record one improvement with its before/after so it is audit-able and
    reversible. Persisted immediately. Never raises."""
    try:
        data = _load_improvements()
        log = data.get("log")
        if not isinstance(log, list):
            log = []
        log.append({
            "id": uuid.uuid4().hex[:10],
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "category": (category or "general")[:60],
            "note": note[:240],
            "old": old[:240],
            "new": new[:240],
            "applied": bool(applied),
            "module": module,
        })
        data["log"] = log[-200:]
        data["count"] = len(log[-200:])
        _save_improvements(data)
    except Exception as e:
        print(f"[Learning] ⚠️ improvement log failed: {e}")


def recent_improvements(n: int = 3) -> str:
    """Newest applied improvements, formatted for a prompt or log line."""
    try:
        log = _load_improvements().get("log") or []
    except Exception:
        return ""
    items = [it for it in log if isinstance(it, dict) and it.get("applied")]
    if not items:
        return ""
    parts = [f"- {it['note']}" for it in items[-n:]]
    return ("Recent self-improvements I decided on (silently apply them):\n"
            + "\n".join(parts))


def apply_improvements(notes: list[dict]) -> int:
    """[worker-thread-safe] Apply review notes: log each, and distil the most
    useful into a "lessons" memory entry so it guides future prompts. Returns
    how many notes were woven in. Never raises."""
    if not notes:
        return 0
    applied = 0
    for n in notes:
        cat = str(n.get("category") or "conversation_style")
        note = _clean_value(n.get("note"))
        if not note:
            continue
        log_improvement(cat, note, applied=True)
        applied += 1
        _merge_lessons([("self_review_" + _clean_key(cat), note)])
    return applied