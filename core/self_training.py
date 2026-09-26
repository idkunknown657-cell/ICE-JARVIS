"""core/self_training.py — JARVIS trains itself while nobody is talking.

WHY THIS EXISTS
    Everything else in ICE learns *from the user*: memory extraction, style
    preferences, lessons after a conversation. That makes improvement reactive —
    nothing happens until the person speaks again, and the mistakes that matter
    most (a click that missed, a field that was never verified) are the ones
    nobody wants to sit and tutor. This is the piece that runs when the room is
    quiet: it watches what the assistant actually *did*, works out where it is
    weakest, and drills that weakness until the mistake stops repeating.

THE LOOP — OBSERVE → DIAGNOSE → DRILL → REMEMBER → VERIFY
    1. OBSERVE   the competency ledger: per-capability attempts/wins, fed by the
                 control log (core/pc_log.py) and by record_outcome() calls from
                 anything that knows whether a step worked.
    2. DIAGNOSE  the weakest capability with real evidence behind it, plus that
                 capability's most recent verbatim failures. "Weakest" is not
                 just the lowest win rate: a capability that reads 80% but has
                 just fallen from 95% is more urgent than one that has sat at
                 70% for a week, because the first is a regression it can still
                 stop and the second is a limitation. A capability actively
                 slipping therefore outranks a stable one with a similar score.
    3. DRILL     one model call writes at most three "next time this happens, do
                 exactly this" rules for those specific failures — not advice,
                 an instruction. Rules that merely restate something already
                 believed are dropped mechanically (learning._similar). A
                 capability whose practice has stopped moving escalates to the
                 smarter model and is told, in the prompt, that repeating the
                 same habits is worthless.
    4. REMEMBER  surviving rules are stored as the user's own memory would be —
                 category "training", one entry per drill — and logged in the
                 reversible improvements log, so `forget` removes them and the
                 whole round is auditable.
    5. VERIFY    every unmeasured rule is scored against the outcomes that
                 arrived AFTER it was written: the capability's win rate now
                 minus its win rate when the rule was written. That verdict
                 (helped / flat / hurt) rides on the rule itself, so a playbook
                 entry that never paid off is visible and can be forgotten,
                 instead of accumulating forever on the strength of having once
                 sounded plausible. A rule with no new evidence behind it is
                 reported as "measuring" — never guessed at.

WHAT IT CANNOT DO (hard boundaries, same brief as core/learning.py)
    * it never writes code, settings, permissions or credentials; the only two
      stores it touches are long-term memory ("training" drills) and its own
      ledger;
    * it never acts on the machine — no mouse, no keyboard, no tool call. The
      only thing a cycle changes is what JARVIS believes;
    * it is bounded — cycles-per-hour, a minimum idle delay, a prompt budget, a
      hard stop when memory is switched off, and a stop the moment the user
      speaks (main.py only calls it while idle);
    * it is honest — the drills are written and scored by the model itself, which
      the UI says out loud ("self-written"), and a round that finds nothing new
      reports "nothing new" instead of manufacturing progress.

NOTHING HERE RAISES. Every public entry point is safe from a background thread
and from a test: a broken ledger, a dead API or a half-written JSON file means
one round did nothing, never a traceback in the middle of someone's session.
"""
from __future__ import annotations

import json
import re
import threading
import time
import uuid
from pathlib import Path

from core import gemini, reasoner

_LOCK = threading.RLock()

# ── the capabilities a competency is tracked for ─────────────────────────────
# Deliberately behavioural, not per-tool: "did the thing it tried actually
# happen?" is comparable across a click, a keystroke and a typed message, while
# one score per tool would be both unreadable and mostly empty.
CAPABILITIES: tuple[str, ...] = (
    "pc_control",       # mouse, clicks, dragging, windows
    "screen",           # finding things on screen, reading it back
    "keyboard",         # typing, hotkeys, focus
    "tool_use",         # calling the right tool with the right arguments
    "planning",         # multi-step goals, order of steps
    "error_recovery",   # what it does after something fails
    "memory",           # recall and storing the right thing
    "conversation",     # being understood and answering usefully
)

CAP_LABEL = {
    "pc_control": "PC control",
    "screen": "Screen reading",
    "keyboard": "Typing & keys",
    "tool_use": "Tool use",
    "planning": "Planning",
    "error_recovery": "Error recovery",
    "memory": "Memory",
    "conversation": "Conversation",
}

# Control-log event prefixes → capability. Longest prefix wins, so the specific
# "screen_ai.*" entries do not fall into the generic bucket.
_EVENT_CAPS: tuple[tuple[str, str], ...] = (
    ("discord.send", "keyboard"),
    ("screen_ai.find", "screen"),
    ("screen_ai.click", "screen"),
    ("screen_ai.hover", "screen"),
    ("screen_ai.type", "keyboard"),
    ("computer.screen_find", "screen"),
    ("computer.screen_click", "pc_control"),
    ("computer.screen_move", "pc_control"),
    ("computer.screen_drag", "pc_control"),
    ("pc_engine.locate", "screen"),
    ("pc_engine.click", "pc_control"),
    ("pc_engine.point", "pc_control"),
    ("pc_engine.type", "keyboard"),
    ("pc_agent.step", "planning"),
    ("goal_agent.step", "planning"),
)

# Verdicts that mean "we know it did not work" vs "we do not know". Anything
# unverified/unknown is *not* counted at all — a ledger that guesses turns into
# noise, and noise is worse than no data.
_FAILED_VERDICTS = ("failed", "fail", "no-visible-change", "error", "missed",
                    "mismatch", "not-found", "false")
_OK_VERDICTS = ("ok", "verified", "done", "true", "sent", "typed", "released")

# intensity → (max cycles per hour, minimum idle seconds before a round)
INTENSITIES: dict[str, tuple[int, int]] = {
    "gentle":   (1, 180),
    "balanced": (2, 90),
    "focused":  (5, 45),
}
DEFAULT_INTENSITY = "balanced"

# How much the win rate has to move before a rule is credited. Noise-sized
# moves (±2%) are called flat, because a verdict is a claim about the world.
_PAYOFF_BAND = 0.02

# Escalation: a capability whose practice has stalled runs its next drill on the
# smarter tier. Cheap rounds stay cheap; stalled ones buy reasoning power.
_BASE_TIER = gemini.FAST
_ESCALATED_TIER = gemini.SMART

_MAX_RULES_PER_CYCLE = 3
_MAX_RULE_CHARS = 220
_MAX_SITUATION_CHARS = 160
_MAX_RECENT = 30          # outcomes kept per capability (rolling window)
_MAX_FAILURES = 6         # verbatim failure notes kept per capability
_MAX_DRILLS_PER_CAP = 6   # drills stored for one capability
_MAX_HISTORY = 30         # cycle history entries
_SAVE_DEBOUNCE_S = 2.0
_CONFIG_TTL_S = 2.0

_STATE: dict | None = None
_LAST_SAVE = 0.0
_CONFIG_CACHE: tuple[float, bool, str, bool] = (0.0, True, DEFAULT_INTENSITY, True)


def _base_dir() -> Path:
    import sys
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


STATE_PATH = _base_dir() / "config" / "self_training.json"


# ════════════════════════════════════════════════════════════════════════════
#  Config (cached — a control event must not read a file)
# ════════════════════════════════════════════════════════════════════════════

def _config() -> tuple[bool, str, bool]:
    """(training_enabled, intensity, memory_enabled) with a short TTL cache."""
    global _CONFIG_CACHE
    now = time.monotonic()
    if now - _CONFIG_CACHE[0] < _CONFIG_TTL_S:
        return _CONFIG_CACHE[1], _CONFIG_CACHE[2], _CONFIG_CACHE[3]
    enabled, intensity, memory = True, DEFAULT_INTENSITY, True
    try:
        from memory import config_manager as cm
        enabled = bool(cm.get_self_training())
        intensity = str(cm.get_training_intensity() or DEFAULT_INTENSITY)
        memory = bool(cm.get_memory_enabled())
    except Exception:
        pass
    if intensity not in INTENSITIES:
        intensity = DEFAULT_INTENSITY
    _CONFIG_CACHE = (now, enabled, intensity, memory)
    return enabled, intensity, memory


def enabled() -> bool:
    """True when the user has self-training on *and* memory is on. Memory off
    means the assistant may not learn, full stop — including on its own."""
    on, _, memory = _config()
    return bool(on and memory)


def intensity() -> str:
    return _config()[1]


def limits() -> tuple[int, int]:
    """(max cycles per hour, minimum idle seconds) for the current intensity."""
    return INTENSITIES.get(intensity(), INTENSITIES[DEFAULT_INTENSITY])


# ════════════════════════════════════════════════════════════════════════════
#  State
# ════════════════════════════════════════════════════════════════════════════

def _blank() -> dict:
    return {
        "version": 1,
        "created": time.time(),
        "cycles": 0,
        "last_ts": 0.0,
        "last_reason": "",
        "focus": "",
        "competency": {},
        "drills": [],
        "history": [],
        # capability → consecutive rounds whose practice did not move the score.
        # Only used to decide when to stop repeating itself (see _recheck).
        "stuck": {},
    }


def _load() -> dict:
    global _STATE
    if _STATE is not None:
        return _STATE
    data: dict = {}
    try:
        raw = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            data = raw
    except Exception:
        data = {}
    state = _blank()
    state.update({k: v for k, v in data.items() if k in state})
    if not isinstance(state.get("competency"), dict):
        state["competency"] = {}
    for key in ("drills", "history"):
        if not isinstance(state.get(key), list):
            state[key] = []
    if not isinstance(state.get("stuck"), dict):
        state["stuck"] = {}
    else:
        state["stuck"] = {str(k): int(v) for k, v in state["stuck"].items()
                          if isinstance(v, (int, float)) and not isinstance(v, bool)}
    _STATE = state
    return state


def _save(force: bool = False) -> None:
    """Write the ledger. Debounced unless forced — the ledger is written on every
    control event, and that must cost nothing measurable."""
    global _LAST_SAVE
    now = time.monotonic()
    if not force and (now - _LAST_SAVE) < _SAVE_DEBOUNCE_S:
        return
    _LAST_SAVE = now
    try:
        state = _load()
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE_PATH.with_name(STATE_PATH.name + ".tmp")
        tmp.write_text(json.dumps(state, indent=1, ensure_ascii=False),
                       encoding="utf-8")
        tmp.replace(STATE_PATH)
    except Exception:
        pass


def flush() -> None:
    """Persist now (called on cycle end and at shutdown)."""
    with _LOCK:
        _save(force=True)


# ════════════════════════════════════════════════════════════════════════════
#  OBSERVE — the competency ledger
# ════════════════════════════════════════════════════════════════════════════

def _entry(state: dict, cap: str) -> dict:
    comp = state.setdefault("competency", {})
    e = comp.get(cap)
    if not isinstance(e, dict):
        e = {"attempts": 0, "wins": 0, "recent": [], "failures": [],
             "last_ts": 0.0, "last_note": ""}
        comp[cap] = e
    e.setdefault("recent", [])
    e.setdefault("failures", [])
    return e


def _score(row: list) -> float:
    vals = [1 if v else 0 for v in row if v in (0, 1, True, False)]
    if not vals:
        return 0.0
    return round(sum(vals) / len(vals), 3)


def _trend(e: dict) -> float:
    """Recent-half win rate minus the half before it. 0 when there is not enough
    history to say anything — a made-up trend is worse than no trend."""
    row = list(e.get("recent") or [])[-20:]
    if len(row) < 6:
        return 0.0
    half = len(row) // 2
    return round(_score(row[half:]) - _score(row[:half]), 3)


def record_outcome(capability: str, ok: bool, note: str = "") -> None:
    """[thread-safe, never raises] One real outcome: did the step happen?"""
    try:
        cap = capability if capability in CAPABILITIES else "tool_use"
        with _LOCK:
            state = _load()
            e = _entry(state, cap)
            e["attempts"] = int(e.get("attempts") or 0) + 1
            if ok:
                e["wins"] = int(e.get("wins") or 0) + 1
            e["recent"] = (list(e.get("recent") or []) + [1 if ok else 0])[-_MAX_RECENT:]
            e["last_ts"] = time.time()
            if not ok and note:
                e["last_note"] = str(note)[:180]
                fails = [str(n) for n in (e.get("failures") or [])
                         if str(n) != str(note)]
                e["failures"] = ([str(note)[:180]] + fails)[:_MAX_FAILURES]
            _save()
    except Exception:
        pass


def note_pc_event(name: str, fields: dict | None = None) -> None:
    """[thread-safe, never raises] Feed one control-log line into the ledger.

    Called from core/pc_log.event, so every click, move, drag, find and type the
    assistant performs — in any module — becomes training evidence for free.
    Verdicts we cannot read are ignored rather than guessed at.
    """
    try:
        if not enabled():
            return
        f = fields or {}
        verdict = str(f.get("verify") or "").strip().lower()
        if not verdict:
            return
        ok: bool | None = None
        if verdict in _OK_VERDICTS:
            ok = True
        elif verdict in _FAILED_VERDICTS:
            ok = False
        elif verdict.startswith("no"):
            ok = False
        if ok is None:
            return
        cap = "tool_use"
        for prefix, mapped in _EVENT_CAPS:
            if name.startswith(prefix):
                cap = mapped
                break
        detail = (f.get("result") or f.get("item") or f.get("description")
                  or f.get("action") or "")
        where = f.get("app") or f.get("target") or ""
        note = (" ".join(str(x) for x in (name, where, detail) if x)).strip()
        record_outcome(cap, ok, note)
    except Exception:
        pass


# ════════════════════════════════════════════════════════════════════════════
#  DIAGNOSE — where is it weakest, with evidence
# ════════════════════════════════════════════════════════════════════════════

def _effective(e: dict) -> float:
    """Win rate, punished for a capability that is actively slipping.

    A drop matters more than a level: something at 80% that just fell from 95%
    is a regression the next round can still stop, while something parked at 70%
    is a standing limitation. The penalty is half the drop and capped at it, so
    this re-orders near-ties instead of chasing a noisy window — a capability
    with a genuinely lower score is still practised first.
    """
    score = _score(e.get("recent") or [])
    drop = max(0.0, -_trend(e))
    return round(score - 0.5 * drop, 3)


def _pick_reason(e: dict) -> str:
    """Why this capability won the round — shown to the model and to the user,
    so the choice is never a mystery."""
    if _trend(e) <= -0.1:
        return "slipping"
    if _score(e.get("recent") or []) <= 0.6:
        return "weak"
    return "lowest"


def _weakest(state: dict) -> tuple[str, dict]:
    """The capability most worth practising: lowest win rate among those with
    real evidence, tie-broken by how often it was attempted, with a capability
    that is currently slipping pulled ahead of a stable one at a similar score.

    With no evidence at all the answer is `conversation` — the thing every
    session exercises — rather than a capability it has never once tried.
    """
    comp = state.get("competency") or {}
    rows = [(cap, e) for cap, e in comp.items()
            if isinstance(e, dict) and int(e.get("attempts") or 0) >= 2]
    if not rows:
        return "conversation", _entry(state, "conversation")
    rows.sort(key=lambda kv: (_effective(kv[1]),
                              -int(kv[1].get("attempts") or 0)))
    return rows[0][0], rows[0][1]


def _existing_beliefs(cap: str, limit: int = 8) -> list[str]:
    """Everything it already believes about this capability: its own drills plus
    the conversation lessons. Used to reject a rule that teaches nothing new."""
    out: list[str] = []
    try:
        from memory import memory_manager as mm
        mem = mm.load_memory()
        for key, entry in (mem.get("training", {}) or {}).items():
            if not str(key).startswith("drill_" + cap):
                continue
            out.append(str(entry.get("value", "") if isinstance(entry, dict)
                           else entry))
        for entry in (mem.get("lessons", {}) or {}).values():
            out.append(str(entry.get("value", "") if isinstance(entry, dict)
                           else entry))
    except Exception:
        pass
    return [t.strip() for t in out if t and t.strip()][:limit]


def _recent_failures(cap: str) -> list[str]:
    state = _load()
    try:
        return list((state.get("competency", {}).get(cap, {}) or {})
                    .get("failures") or [])[:_MAX_FAILURES]
    except Exception:
        return []


# ════════════════════════════════════════════════════════════════════════════
#  VERIFY — did the rules it wrote actually work?
# ════════════════════════════════════════════════════════════════════════════

def _stuck_of(state: dict, cap: str) -> int:
    try:
        return max(0, int((state.get("stuck") or {}).get(cap) or 0))
    except Exception:
        return 0


def _bump_stuck(state: dict, cap: str, moved: bool) -> int:
    """Count (or clear) consecutive rounds on this capability that went nowhere."""
    stuck = state.setdefault("stuck", {})
    n = 0 if moved else _stuck_of(state, cap) + 1
    stuck[cap] = n
    return n


def _recheck(state: dict) -> list[dict]:
    """Score every unmeasured rule against what happened after it was written.

    A verdict costs evidence: the capability must have accumulated new outcomes
    since the rule was stored, otherwise the honest answer is "measuring" and
    nothing is recorded. Each check also feeds the stuck counter, which is what
    eventually makes the loop stop repeating itself and think harder instead.
    """
    checks: list[dict] = []
    drills = [d for d in (state.get("drills") or []) if isinstance(d, dict)]
    if not drills:
        return checks
    comp = state.get("competency") or {}
    by_cap: dict[str, list[dict]] = {}
    for d in drills:
        if d.get("checks"):
            continue
        by_cap.setdefault(str(d.get("capability") or ""), []).append(d)
    for cap, pending in by_cap.items():
        e = comp.get(cap)
        if not isinstance(e, dict) or int(e.get("attempts") or 0) < 2:
            continue
        base_attempts = None
        for d in pending:
            try:
                base_attempts = int(d.get("attempts_at_write"))
            except Exception:
                base_attempts = None
            if base_attempts is not None:
                break
        now_attempts = int(e.get("attempts") or 0)
        new_attempts = now_attempts - (base_attempts if base_attempts is not None
                                       else now_attempts)
        if new_attempts <= 0:
            continue                     # no new evidence: a delta would be noise
        score_now = _score(e.get("recent") or [])
        try:
            baseline = float(pending[0].get("baseline") or 0.0)
        except Exception:
            baseline = 0.0
        delta = round(score_now - baseline, 3)
        verdict = ("helped" if delta >= _PAYOFF_BAND else
                   "hurt" if delta <= -_PAYOFF_BAND else "flat")
        check = {"ts": time.time(), "score": score_now, "baseline": baseline,
                 "delta": delta, "attempts": new_attempts, "verdict": verdict}
        for d in pending:
            d["checks"] = [check]
            d["verdict"] = verdict
        _bump_stuck(state, cap, moved=verdict == "helped")
        checks.append({"capability": cap, **check})
    return checks


# ════════════════════════════════════════════════════════════════════════════
#  DRILL — one model call writes the rules
# ════════════════════════════════════════════════════════════════════════════

_DRILL_PROMPT = (
    "You are the self-training loop of an assistant that controls a computer. "
    "Nobody is talking to it right now — this is its own practice round.\n\n"
    "CAPABILITY UNDER PRACTICE: {cap_label} ({cap})\n"
    "IT WAS CHOSEN BECAUSE: {why}\n"
    "ITS REAL RECORD: {wins} of {attempts} attempts worked "
    "(recent win rate {score:.0%}, trend {trend:+.2f}).\n"
    "WHAT WENT WRONG LATELY, transcribed from its own control log:\n{failures}\n\n"
    "WHAT IT ALREADY BELIEVES (repeating any of this is worthless):\n{beliefs}\n\n"
    "{escalation}"
    "Write at most {n} NEW rules that would have prevented those failures. Each "
    "rule is one concrete situation and the exact move to make in it — an "
    "instruction it can follow in a single step, not advice or a principle. "
    "\"Be careful with dialogs\" is worthless; \"when a dialog has two buttons "
    "and neither name matches the intent, press Escape and re-read the window "
    "instead of guessing\" is a rule.\n"
    "Rules must be about its OWN behaviour (finding, clicking, typing, verifying, "
    "recovering), never about changing its settings or permissions.\n"
    "Answer with ONLY JSON:\n"
    '{{"rules": [{{"situation": "<=160 chars", "rule": "<=200 chars"}}]}}\n'
    "If what it already believes covers these failures, answer "
    '{{"rules": []}} — an empty round is a correct answer, not a failure.'
)


def _clean(text, limit: int) -> str:
    s = re.sub(r"\s+", " ", str(text or "")).strip()
    return s[:limit]


def _sanitize_rules(data, cap: str) -> list[dict]:
    rules: list[dict] = []
    if not isinstance(data, dict):
        return rules
    for item in data.get("rules") or []:
        if not isinstance(item, dict):
            continue
        situation = _clean(item.get("situation"), _MAX_SITUATION_CHARS)
        rule = _clean(item.get("rule"), _MAX_RULE_CHARS)
        if not rule or len(rule) < 12:
            continue
        rules.append({"capability": cap, "situation": situation, "rule": rule})
    return rules[:_MAX_RULES_PER_CYCLE]


def _novel(rules: list[dict], beliefs: list[str]) -> list[dict]:
    """Drop rules that restate something already believed, or each other."""
    try:
        from core.learning import _similar
    except Exception:
        def _similar(a, b, ratio=0.75):        # pragma: no cover - fallback
            return a == b
    kept: list[dict] = []
    for r in rules:
        text = r["rule"] + " " + r["situation"]
        if any(_similar(text, b) for b in beliefs):
            continue
        if any(_similar(text, k["rule"] + " " + k["situation"]) for k in kept):
            continue
        kept.append(r)
    return kept


def _store_rules(cap: str, rules: list[dict],
                 entry: dict | None = None) -> list[dict]:
    """Persist surviving rules as ordinary memory entries the user can forget.

    Each rule carries the win rate it was written at (`baseline`) and the number
    of outcomes recorded so far, which is the only way a later round can tell
    whether the rule changed anything (see _recheck).
    """
    try:
        baseline = _score((entry or {}).get("recent") or [])
        attempts_at_write = int((entry or {}).get("attempts") or 0)
    except Exception:
        baseline, attempts_at_write = 0.0, 0
    stored: list[dict] = []
    for r in rules:
        drill_id = uuid.uuid4().hex[:10]
        value = (f"{r['situation']} → {r['rule']}" if r.get("situation")
                 else r["rule"])
        key = f"drill_{cap}_{drill_id}"
        try:
            from memory import memory_manager as mm
            mm.update_memory({"training": {key: value}})
        except Exception:
            continue
        stored.append({"id": drill_id, "key": key, "capability": cap,
                       "situation": r.get("situation") or "",
                       "rule": r["rule"], "value": value[:400],
                       "baseline": baseline, "attempts_at_write": attempts_at_write,
                       "at": time.time()})
    return stored


def _log_rules(cap: str, rules: list[dict]) -> None:
    try:
        from core import learning
        for r in rules:
            learning.log_improvement(
                "self_training",
                f"[{CAP_LABEL.get(cap, cap)}] {r['rule'][:180]}",
                old="", new=(r.get("situation") or "")[:180],
                applied=True, module="self_training")
    except Exception:
        pass


# ════════════════════════════════════════════════════════════════════════════
#  The cycle
# ════════════════════════════════════════════════════════════════════════════

def cycle_due(idle_seconds: float, now: float | None = None) -> bool:
    """Should an idle round run right now?

    False unless: training on, memory on, the user has been quiet for the
    intensity's minimum, and fewer than the hourly budget of rounds have run.
    This is the whole throttle — main.py asks this and nothing else.
    """
    try:
        if not enabled():
            return False
        if not (isinstance(idle_seconds, (int, float)) and idle_seconds >= 0):
            return False
        state = _load()
        per_hour, min_idle = limits()
        if idle_seconds < min_idle:
            return False
        recent = [float(h.get("ts") or 0) for h in (state.get("history") or [])
                  if isinstance(h, dict)]
        now = time.time() if now is None else now
        if len([t for t in recent if now - t < 3600]) >= per_hour:
            return False
        last = float(state.get("last_ts") or 0)
        return not (last and (now - last) < min_idle)
    except Exception:
        return False


def run_cycle(reason: str = "manual", force: bool = False) -> dict:
    """[worker-thread] One full training round. Never raises.

    Returns a small report; `learned` is the number of genuinely new rules, and
    a round that produced none reports that honestly rather than pretending.
    """
    started = time.time()
    try:
        on, level, memory_on = _config()
        if not memory_on:
            return {"ok": False, "reason": "memory-off", "learned": 0}
        if not on and not force:
            return {"ok": False, "reason": "disabled", "learned": 0}

        with _LOCK:
            state = _load()
            # VERIFY first: score whatever rules are still unmeasured against the
            # outcomes that arrived since they were written. Doing this before
            # choosing the capability is what lets a rule that never paid off
            # count as a stalled round rather than being written off silently.
            checks = _recheck(state)
            cap, entry = _weakest(state)
            stuck = _stuck_of(state, cap)
            failures = list(entry.get("failures") or [])[:_MAX_FAILURES]
            _save(force=bool(checks))
        beliefs = _existing_beliefs(cap)

        escalation = ""
        if stuck:
            escalation = (
                f"⚠ YOU HAVE PRACTISED THIS {stuck + 1} TIME(S) AND THE RECORD "
                "HAS NOT MOVED. The habits above (or the ones you already hold) "
                "are not working. Do not restate them, and do not reword them: "
                "name what is actually broken in your approach and write a "
                "genuinely different instruction. If the capability cannot be "
                "fixed by a rule at all, say so in one rule that changes when "
                "you stop and ask the user instead.\n\n")

        prompt = _DRILL_PROMPT.format(
            cap_label=CAP_LABEL.get(cap, cap), cap=cap,
            why=_pick_reason(entry) + " — the weakest effective record it has",
            wins=int(entry.get("wins") or 0), attempts=int(entry.get("attempts") or 0),
            score=_score(entry.get("recent") or []), trend=_trend(entry),
            failures=("\n".join("- " + f for f in failures) if failures
                      else "- (none logged — practice the weakest areas of this "
                           "capability in general)"),
            beliefs=("\n".join("- " + b for b in beliefs[:6]) if beliefs
                     else "- (nothing yet)"),
            escalation=escalation,
            n=_MAX_RULES_PER_CYCLE,
        )[:7000]

        proposed: list[dict] = []
        tier = reasoner.pick_tier("self training drill for " + cap) or _BASE_TIER
        escalated = bool(stuck)
        if escalated:
            # practice has stalled: buy reasoning power for this one round
            tier = _ESCALATED_TIER
        try:
            data = gemini.as_json(prompt, tier=tier, timeout_ms=45_000, default=None)
            proposed = _sanitize_rules(data, cap)
        except Exception as e:
            print(f"[Training] drill call failed: {e}")

        fresh = _novel(proposed, beliefs)
        stored = _store_rules(cap, fresh, entry) if fresh else []
        if stored:
            _log_rules(cap, stored)

        with _LOCK:
            state = _load()
            state["cycles"] = int(state.get("cycles") or 0) + 1
            state["last_ts"] = time.time()
            state["last_reason"] = str(reason)[:40]
            state["focus"] = cap
            # A round that wrote nothing new is practice that went nowhere; count
            # it, so the next round on this capability escalates instead of
            # asking the same question a third time.
            if not stored:
                _bump_stuck(state, cap, moved=False)
            drills = [d for d in (state.get("drills") or [])
                      if isinstance(d, dict)]
            same = [d for d in drills if d.get("capability") == cap]
            if len(same) > _MAX_DRILLS_PER_CAP:
                drop = {d.get("id") for d in same[:len(same) - _MAX_DRILLS_PER_CAP]}
                drills = [d for d in drills
                          if not (d.get("capability") == cap and d.get("id") in drop)]
            state["drills"] = (drills + stored)[-40:]
            state["history"] = ([h for h in (state.get("history") or [])
                                 if isinstance(h, dict)]
                                + [{"ts": state["last_ts"],
                                    "reason": str(reason)[:40],
                                    "capability": cap,
                                    "proposed": len(proposed),
                                    "learned": len(stored),
                                    "checked": len(checks),
                                    "stuck": _stuck_of(state, cap),
                                    "escalated": escalated,
                                    "ms": int((time.time() - started) * 1000)}]
                                )[-_MAX_HISTORY:]
            _save(force=True)

        report = {
            "ok": True,
            "reason": str(reason)[:40],
            "capability": cap,
            "capability_label": CAP_LABEL.get(cap, cap),
            "why": _pick_reason(entry),
            "proposed": len(proposed),
            "learned": len(stored),
            "rules": [{"id": d["id"], "capability": cap, "rule": d["rule"],
                       "situation": d["situation"], "at": d["at"],
                       "verdict": "measuring"} for d in stored],
            "checked": checks,
            "stuck": stuck,
            "escalated": escalated,
            "score": _score(entry.get("recent") or []),
            "attempts": int(entry.get("attempts") or 0),
            "ms": int((time.time() - started) * 1000),
            "self_scored": True,
        }
        for c in checks:
            print(f"[Training] {c['capability']}: last rules {c['verdict']} "
                  f"({c['delta']:+.0%} over {c['attempts']} new outcome(s)).")
        if stored:
            print(f"[Training] {cap}: learned {len(stored)} new rule(s) "
                  f"({len(proposed)} proposed){' [escalated]' if escalated else ''}.")
        else:
            print(f"[Training] {cap}: nothing new this round.")
        return report
    except Exception as e:
        print(f"[Training] ⚠️ cycle failed: {e}")
        return {"ok": False, "reason": "error", "err": str(e)[:160], "learned": 0}


# ════════════════════════════════════════════════════════════════════════════
#  READ — what the UI shows and the prompt uses
# ════════════════════════════════════════════════════════════════════════════

def competency() -> list[dict]:
    """Per-capability rows for the UI, weakest first, evidence included."""
    state = _load()
    rows: list[dict] = []
    for cap in CAPABILITIES:
        e = (state.get("competency") or {}).get(cap)
        if not isinstance(e, dict):
            continue
        attempts = int(e.get("attempts") or 0)
        if not attempts:
            continue
        rows.append({
            "capability": cap,
            "label": CAP_LABEL.get(cap, cap),
            "attempts": attempts,
            "wins": int(e.get("wins") or 0),
            "score": _score(e.get("recent") or []),
            "trend": _trend(e),
        })
    rows.sort(key=lambda r: (r["score"], -r["attempts"]))
    return rows


def _verdict_of(d: dict) -> tuple[str, float | None]:
    """(verdict, delta) for one rule. "measuring" until real outcomes arrive."""
    checks = d.get("checks")
    if not isinstance(checks, list) or not checks:
        return "measuring", None
    last = checks[-1] if isinstance(checks[-1], dict) else {}
    verdict = str(d.get("verdict") or last.get("verdict") or "measuring")
    if verdict not in ("helped", "flat", "hurt"):
        verdict = "measuring"
    try:
        delta = (float(last["delta"]) if last.get("delta") is not None else None)
    except Exception:
        delta = None
    return verdict, delta


def drills(limit: int = 12) -> list[dict]:
    """Newest self-written rules, newest first, each with what it actually did."""
    state = _load()
    out = [d for d in (state.get("drills") or []) if isinstance(d, dict)]
    out.sort(key=lambda d: float(d.get("at") or 0), reverse=True)
    rows: list[dict] = []
    for d in out[:limit]:
        verdict, delta = _verdict_of(d)
        rows.append({"id": d.get("id"), "capability": d.get("capability"),
                     "label": CAP_LABEL.get(str(d.get("capability")),
                                            str(d.get("capability"))),
                     "situation": d.get("situation") or "",
                     "rule": d.get("rule") or "",
                     "verdict": verdict, "delta": delta,
                     "at": float(d.get("at") or 0)})
    return rows


def payoff() -> dict:
    """How the playbook is actually performing: counts per verdict, plus the
    capabilities whose record is falling right now."""
    state = _load()
    counts = {"helped": 0, "flat": 0, "hurt": 0, "measuring": 0}
    for d in (state.get("drills") or []):
        if not isinstance(d, dict):
            continue
        verdict, _ = _verdict_of(d)
        counts[verdict] = counts.get(verdict, 0) + 1
    slipping = [c for c in competency()
                if float(c.get("trend") or 0) <= -0.1 and float(c.get("score") or 0) < 0.95]
    return {"counts": counts, "total": sum(counts.values()),
            "slipping": slipping[:3],
            "focus_stuck": _stuck_of(state, str(state.get("focus") or ""))}


def stats() -> dict:
    """Counters the Home card and the settings page both read."""
    state = _load()
    history = [h for h in (state.get("history") or []) if isinstance(h, dict)]
    learned = sum(int(h.get("learned") or 0) for h in history)
    now = time.time()
    per_hour, _ = limits()
    last_hour = len([h for h in history if now - float(h.get("ts") or 0) < 3600])
    return {
        "cycles": int(state.get("cycles") or 0),
        "learned": learned,
        "drill_count": len(state.get("drills") or []),
        "focus": state.get("focus") or "",
        "focus_label": CAP_LABEL.get(str(state.get("focus")), ""),
        "last_ts": float(state.get("last_ts") or 0),
        "last_reason": state.get("last_reason") or "",
        "rounds_left": max(0, per_hour - last_hour),
        "rounds_per_hour": per_hour,
        **(lambda p: {"helped": p["counts"]["helped"],
                      "flat": p["counts"]["flat"],
                      "hurt": p["counts"]["hurt"],
                      "measuring": p["counts"]["measuring"],
                      "slipping": p["slipping"],
                      "focus_stuck": p["focus_stuck"]})(payoff()),
    }


def snapshot() -> dict:
    """Everything the UI needs in one call: levers, scores, drills, history."""
    on, level, memory_on = _config()
    st = stats()
    return {
        "enabled": bool(on),
        "intensity": level,
        "memory_enabled": bool(memory_on),
        "active": enabled(),
        "self_scored": True,
        "competency": competency(),
        # NOTE the two names: `drills` is the list the cards render, while the
        # count lives in stats() as `drill_count`. They were one key once, and
        # the count silently overwrote the list on the way to the UI.
        "drills": drills(12),
        "history": [h for h in (_load().get("history") or [])
                    if isinstance(h, dict)][-8:][::-1],
        **st,
    }


def curriculum_digest(limit: int = 3) -> str:
    """A short prompt block: what it is currently training on, and the rules it
    wrote for itself. Empty when training is off or there is nothing to say, so
    the caller can simply skip it.

    Proven rules are preferred over unproven ones and over rules that failed to
    move the record, because the point of the digest is to change behaviour in
    the next conversation — leaning on an instruction that measurably did not
    help would be worse than having no digest at all.
    """
    try:
        if not enabled():
            return ""
        rows = competency()
        weakest = rows[0] if rows else None
        rules = [d for d in drills(12) if isinstance(d, dict)]
        if weakest:
            rules = [r for r in rules if r.get("capability") == weakest["capability"]] or rules
        rank = {"helped": 0, "measuring": 1, "flat": 2, "hurt": 3}
        rules.sort(key=lambda r: rank.get(str(r.get("verdict")), 1))
        good = [r for r in rules if r.get("verdict") in ("helped", "measuring")][:limit]
        failed = [r for r in rules if r.get("verdict") in ("flat", "hurt")][:2]
        lines: list[str] = []
        if weakest:
            slip = (" and it is falling right now"
                    if float(weakest.get("trend") or 0) <= -0.1 else "")
            lines.append(
                f"Where you are currently weakest, from your own record: "
                f"{weakest['label']} — {weakest['wins']}/{weakest['attempts']} "
                f"worked{slip}. Be extra deliberate there.")
        if good:
            lines.append("Rules you wrote for yourself while practising:")
            lines.extend(f"- {r['rule']}" for r in good if r.get("rule"))
        if failed:
            lines.append("Rules of yours that have NOT improved the record "
                         "(do not rely on these):")
            lines.extend(f"- {r['rule']}" for r in failed if r.get("rule"))
        return "\n".join(lines)[:900]
    except Exception:
        return ""


# ════════════════════════════════════════════════════════════════════════════
#  Reversibility — the user is always allowed to take it back
# ════════════════════════════════════════════════════════════════════════════

def forget(drill_id: str) -> bool:
    """Remove one self-written rule, from memory and from the ledger."""
    try:
        drill_id = str(drill_id or "").strip()
        if not drill_id:
            return False
        removed = False
        with _LOCK:
            state = _load()
            keep = []
            for d in (state.get("drills") or []):
                if isinstance(d, dict) and d.get("id") == drill_id:
                    key = d.get("key")
                    removed = True
                    try:
                        from memory import memory_manager as mm
                        mem = mm.load_memory()
                        if key and key in (mem.get("training", {}) or {}):
                            del mem["training"][key]
                            mm.save_memory(mem)
                    except Exception:
                        pass
                    continue
                keep.append(d)
            state["drills"] = keep
            _save(force=True)
        return removed
    except Exception:
        return False


def forget_all() -> int:
    """Forget every self-written rule. Returns how many went."""
    try:
        with _LOCK:
            state = _load()
            n = len([d for d in (state.get("drills") or []) if isinstance(d, dict)])
            try:
                from memory import memory_manager as mm
                mem = mm.load_memory()
                training = mem.get("training") or {}
                keys = [k for k in training if str(k).startswith("drill_")]
                for k in keys:
                    del training[k]
                if keys:
                    mm.save_memory(mem)
            except Exception:
                pass
            state["drills"] = []
            _save(force=True)
        return n
    except Exception:
        return 0


def reset() -> None:
    """Wipe the ledger and drills. The improvements log is left alone — it is an
    audit trail of what happened, not a setting."""
    global _STATE
    try:
        with _LOCK:
            forget_all()
            _STATE = _blank()
            _save(force=True)
    except Exception:
        pass
