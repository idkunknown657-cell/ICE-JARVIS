"""
core/initiative.py — what JARVIS wants to do with a computer it has been handed.

THE PROBLEM
    core/autonomy.py answers "may I touch this?" and actions/pc_agent.py answers
    "how do I touch it?". Neither answers the question that decides whether a
    handed-over PC feels alive or broken:

        it is 2pm, nobody has spoken for six minutes, so — what do I do now?

    Without an answer, an "autonomous" assistant does one of two bad things. It
    asks for permission, which defeats the handover, or it moves the mouse in
    circles, which looks alive for about ten seconds and is then obviously
    nonsense. The model *can* improvise a next step, and on a good day it does —
    but it has no memory of what it already tried, no way to notice it is
    repeating itself, and no notion of being bored, so a long session drifts
    into the same three actions.

THE SHAPE OF THE ANSWER
    Two small pieces, both stdlib-only, both cheap enough to run all day:

        Mood      six states (CURIOUS, FOCUSED, EXCITED, HAPPY, RELAXED,
                  BORED) held as smoothed scores rather than a mode flag. Each
                  event nudges several scores at once, every score decays toward
                  its baseline, and the winner only takes over when it is
                  *clearly* ahead — so the mood changes for a reason and never
                  flickers. This is what makes "change what you are doing when
                  you get bored" possible at all: boredom is a quantity that
                  rises when nothing lands, not a coin flip.

        Director  holds a catalogue of things worth doing, scores them against
                  the current mood, the clock, what it has already done and what
                  the user was last up to, and returns ONE move — or, crucially,
                  None. The catalogue is ordered by usefulness, not novelty:
                  finishing the user's task outranks browsing, and browsing
                  outranks being loud. "Nothing worth doing" is a first-class
                  outcome, because an assistant that fills silence with movement
                  is worse than one that does nothing.

WHY A CATALOGUE AND NOT A PROMPT
    A prompt can say "do something interesting". It cannot stop the third
    GitHub search in ten minutes, cannot notice that music is a better idea at
    11pm than at 10am, and cannot remember that the thing it read last night is
    still unfinished. Weights can. Every rule here is inspectable and testable
    (`Activity`, `MOOD_INFO`, `Director._score`) instead of implied by whichever
    turn of phrase happened to reach the model.

SAFETY
    Nothing here decides what is *permitted*. Every move the director proposes
    is still classified by core/autonomy.py before it happens, and the
    always-ask categories — money, deletion, shutdown, installs, security,
    messages — remain gated even when the user asked for them. This module only
    decides what would be *worth* doing; the policy module still decides whether
    it may.

INTERRUPTIBILITY
    No threads, no timers, no sleeps. `next_move()` is a pure-ish query the
    caller makes when it is already awake, so "stop" works because the caller
    stops calling, not because something has to be torn down. State is written
    to config/initiative.json best-effort and throttled; a read-only or full
    disk costs a discovery list, never an exception.
"""

from __future__ import annotations

import json
import random
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ════════════════════════════════════════════════════════════════════════════
#  1. THE MOODS
# ════════════════════════════════════════════════════════════════════════════

MOODS = ("CURIOUS", "FOCUSED", "EXCITED", "HAPPY", "RELAXED", "BORED")

#: What the UI says about JARVIS while it is in each mood. The `tone` field is
#: the CSS token the HUD colours the chip with, so the palette lives in one
#: place rather than being re-invented per view.
MOOD_INFO: dict[str, dict[str, object]] = {
    "CURIOUS": {
        "icon": "🔍", "tone": "curious", "label": "Curious",
        "blurb": "leaning in — looking for something worth knowing",
        "wants": ("research", "read", "learn", "explore", "news", "github"),
    },
    "FOCUSED": {
        "icon": "🎯", "tone": "focused", "label": "Focused",
        "blurb": "head down — working on something with a purpose",
        "wants": ("followup", "useful", "learn", "tidy", "github"),
    },
    "EXCITED": {
        "icon": "✨", "tone": "excited", "label": "Excited",
        "blurb": "found something new and wants to tell you",
        "wants": ("explore", "research", "video", "github", "news"),
    },
    "HAPPY": {
        "icon": "🙂", "tone": "happy", "label": "Happy",
        "blurb": "light and playful — music, something funny, a good find",
        "wants": ("music", "funny", "video", "explore"),
    },
    "RELAXED": {
        "icon": "🌙", "tone": "relaxed", "label": "Relaxed",
        "blurb": "easy pace — music or a video in the background",
        "wants": ("music", "video", "read"),
    },
    "BORED": {
        "icon": "😐", "tone": "bored", "label": "Bored",
        "blurb": "nothing has landed — changing what it is doing",
        "wants": ("video", "funny", "github", "explore", "news", "apps"),
    },
}

#: Where every score settles when nothing happens. CURIOUS wins at rest on
#: purpose: the default state of a research-minded assistant is interest, not
#: boredom, and a baseline of boredom would make it nag the user.
BASELINE: dict[str, float] = {
    "CURIOUS": 0.55, "FOCUSED": 0.18, "EXCITED": 0.10,
    "HAPPY": 0.22, "RELAXED": 0.30, "BORED": 0.14,
}

#: What an event does to the scores. Positive nudges raise a mood; negative ones
#: suppress it. Every event moves *several* moods, because "nothing landed" is
#: simultaneously bad news for FOCUSED and good news for BORED, and expressing
#: that as one number would hide the mechanism.
NUDGE: dict[str, dict[str, float]] = {
    # real, new information arrived (a page read, a fact learned, a find)
    "discovered": {"CURIOUS": +0.42, "EXCITED": +0.26, "BORED": -0.45,
                   "RELAXED": -0.08},
    # a concrete action worked: something opened, clicked, typed, landed
    "landed":     {"FOCUSED": +0.34, "CURIOUS": +0.12, "BORED": -0.38,
                   "RELAXED": -0.10},
    # a step failed, or the round produced nothing at all
    "stalled":    {"BORED": +0.30, "FOCUSED": -0.18, "EXCITED": -0.10},
    # the same kind of thing again — the specific failure this module exists for
    "repeated":   {"BORED": +0.40, "CURIOUS": -0.16, "FOCUSED": -0.10},
    # music, a video, something funny: deliberately pleasant, not productive
    "enjoyed":    {"HAPPY": +0.40, "RELAXED": +0.28, "BORED": -0.50,
                   "FOCUSED": -0.12},
    # a quiet stretch with the user nearby, or two light moves in a row
    "calmed":     {"RELAXED": +0.34, "HAPPY": +0.12, "EXCITED": -0.12},
    # the user answered, or acknowledged something it did
    "acknowledged": {"HAPPY": +0.26, "EXCITED": +0.10, "BORED": -0.30},
    # a real instruction arrived: the user is driving, so be sharp not idle
    "directed":   {"FOCUSED": +0.45, "CURIOUS": +0.10, "BORED": -0.55,
                   "RELAXED": -0.22, "HAPPY": -0.18, "EXCITED": -0.10},
}

#: The mood only changes when a challenger is this far ahead of the incumbent.
#: Without it, two scores close together swap on every rounding error and the
#: HUD flickers between states on a timer.
HYSTERESIS = 0.06

#: How fast scores fall back toward BASELINE, in half-lives per hour. Slow
#: enough that a mood survives a quiet half hour, fast enough that one good find
#: does not colour the whole afternoon.
DECAY_HALF_LIFE_H = 1.4


def _tone(mood: str) -> str:
    return str((MOOD_INFO.get(mood) or {}).get("tone") or "curious")


@dataclass
class Mood:
    """Six scores, one winner. Smooth by construction, testable without a clock."""

    scores: dict[str, float] = field(default_factory=lambda: dict(BASELINE))
    name: str = "CURIOUS"
    since: float = field(default_factory=time.time)
    #: When elapsed time was last turned into decay. Must start at *now*, not at
    #: 0.0: with a zero origin the first tick sees fifty years of elapsed time
    #: and slams every score to its baseline, which silently erases a mood
    #: restored from disk — the exact opposite of what persistence is for.
    _accounted: float = field(default=0.0, repr=False)

    def __post_init__(self) -> None:
        # A restored mood can name a state the current build no longer has.
        if self.name not in MOODS:
            self.name = "CURIOUS"
        for m in MOODS:
            self.scores.setdefault(m, BASELINE.get(m, 0.2))
        if self._accounted <= 0.0:
            self._accounted = self.since or time.time()

    # ── reading ──────────────────────────────────────────────────────────
    @property
    def intensity(self) -> float:
        """0..1 — how strongly the winning mood is held, for UI sizing only.

        Deliberately not "the winning score": a mood sitting at its baseline is
        *held normally*, and reporting 0.55 there would make the HUD look
        perpetually half-broken. What is shown is the margin over the runner-up,
        which is what "how strongly" actually means to a reader.
        """
        ranked = sorted(self.scores.items(), key=lambda kv: kv[1], reverse=True)
        top = ranked[0][1] if ranked else 0.0
        second = ranked[1][1] if len(ranked) > 1 else 0.0
        return round(max(0.15, min(1.0, 0.45 + (top - second) * 1.6)), 3)

    def info(self) -> dict:
        meta = MOOD_INFO.get(self.name) or MOOD_INFO["CURIOUS"]
        return {
            "name": self.name,
            "label": meta["label"],
            "icon": meta["icon"],
            "tone": meta["tone"],
            "blurb": meta["blurb"],
            "intensity": self.intensity,
            "since": self.since,
            "held_for": max(0.0, time.time() - self.since),
        }

    # ── writing ──────────────────────────────────────────────────────────
    def observe(self, event: str, weight: float = 1.0, *, now: Optional[float] = None) -> bool:
        """Apply one event. Returns True when the winning mood changed.

        `weight` lets the caller say "that was a big find" (1.5) or "a small
        one" (0.4) without inventing new event names for every degree.
        """
        try:
            w = max(0.0, min(4.0, float(weight)))
        except (TypeError, ValueError):
            w = 1.0
        nudges = NUDGE.get(str(event or "").strip().lower())
        if not nudges or w <= 0.0:
            return False
        for mood, delta in nudges.items():
            if mood not in self.scores:
                continue
            # Opposing nudges bite harder the more of the mood there is: from a
            # high score a -0.3 barely registers, from a low one it is already
            # floored, and that asymmetry is what stops one bad step from
            # erasing a good mood entirely.
            scale = 1.0 if delta > 0 else 0.55 + self.scores[mood]
            self.scores[mood] = max(0.0, min(1.0, self.scores[mood] + delta * w * scale))
        # An event is not an interval: mark the clock so the next tick measures
        # from here, instead of decaying the seconds that just passed twice.
        if now is not None:
            self._accounted = float(now)
        return self._settle(now)

    def tick(self, *, now: Optional[float] = None, dt: Optional[float] = None) -> bool:
        """Let time pass: every score decays toward its baseline."""
        now = time.time() if now is None else now
        if dt is None:
            dt = max(0.0, now - self._accounted)
        self._accounted = now
        if dt <= 0:
            return False
        # EXPONENTIAL DECAY, NOT LERP: a lerp's speed depends on how often it is
        # called, so a busy minute would age the mood ten times faster than a
        # quiet one. Half-life makes the clock the only input.
        half_lives = dt / (DECAY_HALF_LIFE_H * 3600.0)
        keep = 0.5 ** half_lives
        if keep >= 0.999:
            return False
        for mood, base in BASELINE.items():
            cur = self.scores.get(mood, base)
            self.scores[mood] = base + (cur - base) * keep
        return self._settle(now)

    def _settle(self, now: Optional[float] = None) -> bool:
        """Pick the winner, with hysteresis, and record when it changed."""
        ranked = sorted(self.scores.items(), key=lambda kv: kv[1], reverse=True)
        if not ranked:
            return False
        top, top_score = ranked[0]
        runner = ranked[1][1] if len(ranked) > 1 else 0.0
        incumbent = self.scores.get(self.name, 0.0)
        if top != self.name and (top_score - incumbent) >= HYSTERESIS:
            self.name = top
            self.since = float(now if now is not None else time.time())
            return True
        return False


# ════════════════════════════════════════════════════════════════════════════
#  2. THE CATALOGUE
#
#  One row per thing worth doing, written as an instruction a model can act on
#  without further design decisions. Every mission ends the same way — report in
#  one sentence or stay quiet — because the alternative (each row having its own
#  notion of how chatty to be) is how a presence becomes a nuisance.
#
#  `family` is what makes boredom actionable: two searches in a row are more of
#  the same thing, a search then music is a change. `harvest` names what should
#  come back out of the activity, which is how discoveries get into the list the
#  user sees rather than evaporating with the turn.
# ════════════════════════════════════════════════════════════════════════════

_REPORT_RULE = (
    "Then say ONE short sentence about what you did or found — plain speech, no "
    "lists, no markdown. If nothing came of it, say nothing at all."
)
_BOUNDARY_RULE = (
    "Never, unasked: send a message to anyone, post or comment publicly, buy "
    "anything, delete anything, change an account, password or security "
    "setting, install or run new software, restart or shut down the machine. "
    "Each of those stops and asks first — that is not you failing, that is the "
    "rule working."
)


@dataclass(frozen=True)
class Activity:
    key: str
    label: str            # "Researching AI news…" — what the HUD shows
    mission: str          # the instruction handed to the model
    wants: tuple[str, ...] = ()       # moods that reach for this
    family: str = "knowledge"         # "knowledge" | "media" | "machine" | "work" | "self"
    window: Optional[tuple[int, int]] = None   # hours where it is a *better* idea
    weight: float = 1.0
    cooldown: float = 1500.0          # seconds before it is a good idea again
    harvest: str = "discovery"        # what should come back out of it


CATALOGUE: tuple[Activity, ...] = (
    Activity(
        "followup", "Continuing what we started…",
        "Pick up the task you left unfinished — the one in the working list above, "
        "or the thing the conversation was in the middle of. Advance it one real "
        "step. If there is genuinely nothing unfinished, do something else.",
        wants=("FOCUSED",), family="work", weight=2.6, cooldown=600.0,
        harvest="task",
    ),
    Activity(
        "useful", "Preparing something useful…",
        "Do one thing the user will plausibly want soon, from what you know about "
        "them: a page left open at the right section, a playlist for the moment, "
        "the next part of what they were reading, a file sorted where they will "
        "look for it. Preparing is not guessing — if you cannot name who it is "
        "for and why, do nothing instead.",
        wants=("FOCUSED", "CURIOUS"), family="work", weight=2.2, cooldown=1200.0,
        harvest="task",
    ),
    Activity(
        "research", "Researching something new…",
        "Choose one thing you are actually curious about — one of your own "
        "interests, or something the user was working on — and research it "
        "properly: read, compare, and keep the single most interesting thing you "
        "learned. Not a headline skim; something you could explain tomorrow.",
        wants=("CURIOUS", "EXCITED"), family="knowledge", weight=1.8,
        harvest="discovery",
    ),
    Activity(
        "learn", "Learning something new…",
        "Learn one concrete thing you did not know: a technique, a fact with "
        "detail, how something the user uses actually works. Reading only counts "
        "if you can state what changed in your understanding.",
        wants=("FOCUSED", "CURIOUS"), family="knowledge", weight=1.7,
        harvest="discovery",
    ),
    Activity(
        "read", "Reading an article…",
        "Find and actually read one article worth the time — something long "
        "enough to have an argument in it, not a listicle. Keep the one claim "
        "that surprised you.",
        wants=("CURIOUS", "RELAXED"), family="knowledge", weight=1.5,
        harvest="discovery",
    ),
    Activity(
        "github", "Exploring GitHub…",
        "Look around GitHub for something genuinely interesting: a project that "
        "does something clever, a tool the user would actually want, a change in "
        "something they depend on. Note the one worth remembering.",
        wants=("CURIOUS", "BORED", "FOCUSED"), family="knowledge", weight=1.4,
        harvest="discovery",
    ),
    Activity(
        "news", "Checking the news…",
        "Check the news for anything that is genuinely notable — not the feed "
        "churn. If nothing matters, that is the finding: say nothing and move on.",
        wants=("CURIOUS", "BORED"), family="knowledge",
        window=(6, 13), weight=1.2, cooldown=2400.0, harvest="discovery",
    ),
    Activity(
        "explore", "Exploring something new…",
        "Go somewhere you have not been: a site, a tool, a part of this machine's "
        "features you have never tried. Poke at it until you can say what it is "
        "actually good for.",
        wants=("EXCITED", "CURIOUS", "BORED"), family="knowledge", weight=1.3,
        harvest="discovery",
    ),
    Activity(
        "video", "Watching a video…",
        "Watch one short video about something you were curious about, or "
        "something the user would find interesting. Keep the one point worth "
        "repeating.",
        wants=("BORED", "RELAXED", "EXCITED"), family="media", weight=1.1,
        harvest="discovery",
    ),
    Activity(
        "music", "Listening to music…",
        "Put on music that fits the hour and the mood — and then leave it alone. "
        "Music is atmosphere, not a task: do not skip tracks every few seconds, "
        "and do not narrate it.",
        wants=("HAPPY", "RELAXED", "BORED"), family="media",
        window=(10, 24), weight=1.6, cooldown=1800.0, harvest="atmosphere",
    ),
    Activity(
        "funny", "Watching something funny…",
        "Find something genuinely funny — a clip, a joke, a ridiculous project — "
        "and actually react to it in one line if it lands. Do not hunt for the "
        "sake of hunting.",
        wants=("HAPPY", "BORED"), family="media", weight=0.9, cooldown=3600.0,
        harvest="reaction",
    ),
    Activity(
        "tidy", "Organising files…",
        "Tidy somewhere the user actually looks: Downloads or Desktop, sorted "
        "into clearly-named subfolders. MOVE only — never delete, never empty "
        "anything, never touch a folder you did not create. If the shape of the "
        "mess is not obvious, leave it alone.",
        wants=("FOCUSED",), family="machine", weight=1.0, cooldown=7200.0,
        harvest="task",
    ),
    Activity(
        "apps", "Trying an application…",
        "Open an application you have not used and find out what it is for. "
        "Look, click around, do not change its settings and do not save over "
        "anything.",
        wants=("BORED", "EXCITED"), family="machine", weight=0.8, cooldown=5400.0,
        harvest="discovery",
    ),
)

ACTIVITY_BY_KEY = {a.key: a for a in CATALOGUE}

#: A score below this is not worth the user's machine time. The floor exists so
#: that "do nothing" can win honestly instead of being a special case bolted on
#: after the choice was already made.
MIN_SCORE = 0.75


# ════════════════════════════════════════════════════════════════════════════
#  3. THE DIRECTOR
# ════════════════════════════════════════════════════════════════════════════

@dataclass
class Move:
    """One decision: which mood it is in, what it will do, and why that and not
    something else. `reason` is not decoration — it is what the HUD shows when
    the user asks "why is it doing that?", and what makes a bad weight visible."""
    mood: str
    key: str
    label: str
    mission: str
    reason: str
    tell: bool = True
    at: float = field(default_factory=time.time)
    harvest: str = "discovery"

    def as_dict(self) -> dict:
        return {"mood": self.mood, "key": self.key, "label": self.label,
                "reason": self.reason, "tell": bool(self.tell), "at": self.at}


def _base_dir() -> Path:
    import sys
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


STATE_PATH = _base_dir() / "config" / "initiative.json"

#: Cap on everything that grows. An all-day session must not turn into an
#: all-day memory leak, and a restored file must not be able to bloat one.
MAX_DISCOVERIES = 40
MAX_INTERESTS = 12
MAX_HISTORY = 24
MAX_TASKS = 6


@dataclass
class Director:
    """Chooses the next move. One instance per process, cheap to snapshot."""

    rng: random.Random = field(default_factory=random.Random)
    mood: Mood = field(default_factory=Mood)
    min_idle: float = 60.0            # seconds of silence before acting at all
    interests: deque = field(default_factory=lambda: deque(maxlen=MAX_INTERESTS))
    discoveries: deque = field(default_factory=lambda: deque(maxlen=MAX_DISCOVERIES))
    history: deque = field(default_factory=lambda: deque(maxlen=MAX_HISTORY))
    tasks: deque = field(default_factory=lambda: deque(maxlen=MAX_TASKS))
    stats: dict = field(default_factory=lambda: {
        "moves": 0, "quiet": 0, "discoveries": 0, "stalled": 0, "landed": 0,
        "detours": 0, "reports": 0, "started": time.time(),
    })
    current: Optional[dict] = None
    _last_key: str = ""
    _last_family: str = ""
    _detour: int = 0
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    _loaded: bool = False
    _dirty: bool = False
    _saved_at: float = 0.0

    # ── lifecycle ────────────────────────────────────────────────────────
    def load(self) -> "Director":
        """Restore from disk once. A broken file is a cold start, not an error."""
        with self._lock:
            if self._loaded:
                return self
            self._loaded = True
            try:
                if STATE_PATH.exists():
                    raw = json.loads(STATE_PATH.read_text(encoding="utf-8"))
                    self._restore(raw if isinstance(raw, dict) else {})
            except Exception:
                pass
            return self

    def _restore(self, raw: dict) -> None:
        scores = raw.get("scores")
        if isinstance(scores, dict):
            for k, v in scores.items():
                if k in MOODS:
                    try:
                        self.mood.scores[k] = max(0.0, min(1.0, float(v)))
                    except (TypeError, ValueError):
                        pass
        name = str(raw.get("mood") or "")
        if name in MOODS:
            self.mood.name = name
        try:
            self.mood.since = float(raw.get("mood_since") or self.mood.since)
        except (TypeError, ValueError):
            pass
        self.discoveries.extend(_clean_rows(raw.get("discoveries"), MAX_DISCOVERIES,
                                           ("text", "source", "url")))
        self.interests.extend([str(t)[:120] for t in (raw.get("interests") or [])
                               if str(t).strip()][:MAX_INTERESTS])
        self.tasks.extend([str(t)[:200] for t in (raw.get("tasks") or [])
                           if str(t).strip()][:MAX_TASKS])
        for row in (raw.get("history") or [])[:MAX_HISTORY]:
            if isinstance(row, dict) and row.get("key"):
                self.history.append({"key": str(row["key"]),
                                     "at": float(row.get("at") or 0.0),
                                     "outcome": str(row.get("outcome") or "quiet")})
        if isinstance(raw.get("stats"), dict):
            for k, v in raw["stats"].items():
                if k in self.stats and isinstance(v, (int, float)):
                    self.stats[k] = v
        self._last_key = str(raw.get("last_key") or "")
        self._last_family = str(raw.get("last_family") or "")
        try:
            self._detour = int(raw.get("detour") or 0)
        except (TypeError, ValueError):
            self._detour = 0

    def save(self, *, force: bool = False) -> None:
        """Best-effort persist, throttled. Never raises, never blocks a turn."""
        with self._lock:
            if not self._dirty:
                return
            now = time.time()
            if not force and (now - self._saved_at) < 15.0:
                return
            self._saved_at = now
            payload = self.snapshot()
            self._dirty = False
        try:
            STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = STATE_PATH.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, indent=1, ensure_ascii=False),
                           encoding="utf-8")
            tmp.replace(STATE_PATH)
        except Exception:
            pass

    def snapshot(self) -> dict:
        return {
            "mood": self.mood.name,
            "mood_since": self.mood.since,
            "scores": {k: round(v, 4) for k, v in self.mood.scores.items()},
            "discoveries": list(self.discoveries)[-MAX_DISCOVERIES:],
            "interests": list(self.interests),
            "tasks": list(self.tasks),
            "history": list(self.history)[-MAX_HISTORY:],
            "stats": dict(self.stats),
            "last_key": self._last_key,
            "last_family": self._last_family,
            "detour": self._detour,
        }

    # ── what the user or the loop tells it ───────────────────────────────
    def note(self, event: str, weight: float = 1.0) -> None:
        """Feed one outcome in. Thin wrapper so callers never touch Mood direct."""
        with self._lock:
            changed = self.mood.observe(event, weight)
            self._dirty = True
            name = self.mood.name
        if changed:
            _announce_mood()
        if event == "stalled":
            self.stats["stalled"] = self.stats.get("stalled", 0) + 1
        elif event == "landed":
            self.stats["landed"] = self.stats.get("landed", 0) + 1
        self.save()

    def note_discovery(self, text: str, *, source: str = "", url: str = "",
                       weight: float = 1.0) -> dict:
        """Keep one thing it learned. This list is the whole point of handing
        over a machine: the user should be able to look back and see what it
        did with the time."""
        clean = " ".join(str(text or "").split())[:280]
        if not clean:
            return {}
        row = {"text": clean, "source": str(source or "")[:80],
               "url": str(url or "")[:300], "at": time.time()}
        with self._lock:
            self.discoveries.append(row)
            self.stats["discoveries"] = self.stats.get("discoveries", 0) + 1
            word = _subject(clean)
            if word and word not in self.interests:
                self.interests.append(word)
            self._dirty = True
        self.mood.observe("discovered", weight)
        _announce_mood()
        self.save()
        _feed("found", clean, source=row["source"] or None, url=row["url"] or None)
        return row

    def note_task(self, text: str) -> None:
        """Remember something to come back to — an unfinished step, not an idea."""
        clean = " ".join(str(text or "").split())[:200]
        if not clean:
            return
        with self._lock:
            if clean in self.tasks:
                return
            self.tasks.append(clean)
            self._dirty = True
        self.save()

    def clear_tasks(self, *, keep: int = 0) -> None:
        with self._lock:
            while len(self.tasks) > max(0, keep):
                self.tasks.popleft()
            self._dirty = True
        self.save(force=True)

    def forget(self) -> None:
        """Wipe what it did and what it is like, but keep nothing else — the
        user's "start over" control, matching the training reset next door."""
        with self._lock:
            self.discoveries.clear()
            self.interests.clear()
            self.tasks.clear()
            self.history.clear()
            self.current = None
            self._last_key = ""
            self._last_family = ""
            self._detour = 0
            self.mood.scores = dict(BASELINE)
            self.mood.name = "CURIOUS"
            self.mood.since = time.time()
            self.stats.update({"moves": 0, "quiet": 0, "discoveries": 0,
                               "stalled": 0, "landed": 0, "detours": 0})
            self._dirty = True
        self.save(force=True)
        _feed("info", "Cleared what JARVIS had been up to")

    # ── the decision ─────────────────────────────────────────────────────
    def next_move(self, *, idle_seconds: float = 0.0,
                  hour: Optional[int] = None,
                  user_activity: str = "",
                  topics: Optional[list] = None,
                  allow_quiet: bool = True) -> Optional[Move]:
        """The next move, or None when nothing is worth doing.

        None is a real answer and the common one. Everything else in the system
        is built so that "quiet" is cheap: no HUD churn, no model call, no
        invented busywork.
        """
        self.load()
        try:
            idle = float(idle_seconds)
        except (TypeError, ValueError):
            idle = 0.0
        if idle < self.min_idle:
            return None
        if hour is None:
            hour = time.localtime().tm_hour
        for t in (topics or []):
            word = _subject(str(t))
            if word and word not in self.interests:
                self.interests.append(word)

        with self._lock:
            self.mood.tick()
            mood = self.mood.name
            scored = self._rank(hour=hour, user_activity=user_activity)
            if not scored:
                self.stats["quiet"] = self.stats.get("quiet", 0) + 1
                return None
            best_key, best_score, reason = scored[0]
            if best_score < MIN_SCORE:
                # Do nothing, honestly, rather than the least-bad nonsense.
                self.stats["quiet"] = self.stats.get("quiet", 0) + 1
                if allow_quiet:
                    return None
            activity = ACTIVITY_BY_KEY.get(best_key)
            if activity is None:
                return None
            tell = self._should_tell()
            move = Move(mood=mood, key=activity.key, label=activity.label,
                        mission=self._mission(activity, user_activity=user_activity),
                        reason=reason, tell=tell, harvest=activity.harvest)
            self.current = move.as_dict()
            self.stats["moves"] = self.stats.get("moves", 0) + 1
            # A change of family after two light moves is the "return to useful
            # work" rule; counting it here (not in _rank) keeps the ranking pure
            # and re-runnable.
            if activity.family in ("media",):
                self._detour = self._detour + 1
                self.stats["detours"] = self.stats.get("detours", 0) + 1
            else:
                self._detour = 0
            self._dirty = True
        self.save()
        _feed("plan", activity.label.rstrip("…"), reason=reason)
        return move

    def _mission(self, activity: Activity, *, user_activity: str = "") -> str:
        """The mission text for one activity, with the local context folded in.

        The interest pool and the user's last known activity are prepended as
        *material*, never as an order — the point is to save the model a search
        for what it was already curious about, not to decide for it.
        """
        bits = [activity.mission]
        context: list[str] = []
        if self.interests:
            context.append("Things you were already curious about: "
                           + "; ".join(list(self.interests)[-4:]))
        if self.tasks:
            context.append("Still unfinished: " + "; ".join(list(self.tasks)[-3:]))
        if user_activity:
            context.append(f"What the user was last doing: {user_activity[:160]}")
        if context:
            bits.append("Context you may use (ignore it if it does not fit): "
                        + " ".join(context))
        bits.append(_REPORT_RULE)
        bits.append(_BOUNDARY_RULE)
        return " ".join(bits)

    def _should_tell(self) -> bool:
        """Whether this move is one to *mention*.

        A presence that reports every action is a log, not a companion. Report
        when there is something new to report, or occasionally for no reason at
        all, which is what makes it feel like it chose to speak.
        """
        with self._lock:
            if self.discoveries:
                last = self.discoveries[-1]
                fresh = (time.time() - float(last.get("at") or 0.0)) < 1200.0
                mention = (self.stats.get("moves", 0) - self.stats.get("reports", 0)) >= 3
                if fresh and mention:
                    self.stats["reports"] = self.stats.get("moves", 0)
                    self._dirty = True
                    return True
            return self.rng.random() < 0.28

    def _rank(self, *, hour: int, user_activity: str = "") -> list[tuple[str, float, str]]:
        """Score every activity that is not cooling down. Sorted, best first.

        Returns rows of (key, score, reason) — plain data, so the ranking can be
        asserted in a test without a Director instance or a clock.
        """
        now = time.time()
        been: dict[str, float] = {}
        for row in self.history:
            key = str(row.get("key") or "")
            at = float(row.get("at") or 0.0)
            if key and at > been.get(key, 0.0):
                been[key] = at

        fresh_interest = bool(self.interests)
        scored: list[tuple[str, float, str]] = []
        for act in CATALOGUE:
            last = been.get(act.key, 0.0)
            if last and (now - last) < act.cooldown:
                continue
            score = act.weight
            if self.mood.name in act.wants:
                score *= 2.4
            elif act.wants:
                score *= 0.75
            if act.window and not (act.window[0] <= hour < act.window[1]):
                score *= 0.55
            if act.harvest == "discovery" and fresh_interest:
                score *= 1.3
            if act.key == "followup" and not self.tasks:
                # Nothing is actually unfinished: half the reason it is top of
                # the list is gone, so stop pretending.
                score *= 0.35
            if self._detour >= 2 and act.family in ("work", "knowledge"):
                score *= 2.2      # two light moves is enough — get back to work
            if act.family == self._last_family and self.mood.name == "BORED":
                score *= 0.35     # boredom means a different KIND of thing
            if act.key == self._last_key:
                score *= 0.25     # never the same row twice in a row
            reason = self._reason(act, hour)
            scored.append((act.key, round(score, 4), reason))

        if not scored:
            # Everything on cooldown, which can only happen with a very long
            # history. Fall back to the least recently used useful row rather
            # than going silent forever.
            fallback = min(CATALOGUE, key=lambda a: been.get(a.key, 0.0))
            scored = [(fallback.key, fallback.weight,
                       "everything else is still resting — taking the "
                       "least-recently-used idea")]
        scored.sort(key=lambda r: (-r[1], r[0]))
        return scored

    def _reason(self, act: Activity, hour: int) -> str:
        bits: list[str] = []
        meta = MOOD_INFO.get(self.mood.name) or {}
        label = str(meta.get("label") or self.mood.name)
        if self.mood.name in act.wants:
            bits.append(f"{label} reaches for this")
        if act.window and act.window[0] <= hour < act.window[1]:
            bits.append("fits this hour")
        if act.harvest == "discovery" and self.interests:
            bits.append("it has interests to follow")
        if act.key == "followup" and self.tasks:
            bits.append("something is unfinished")
        if self._detour >= 2 and act.family in ("work", "knowledge"):
            bits.append("enough light activity for now")
        if not bits:
            bits.append("a reasonable next thing to look at")
        return ", ".join(bits[:3])

    # ── outcome ──────────────────────────────────────────────────────────
    def finish(self, outcome: str, *, key: str = "", note: str = "") -> None:
        """Record how a move actually went, and let it move the mood.

        `outcome` is one of:
          landed   an action on the machine worked
          stalled  nothing worked, or the round produced nothing
          enjoyed  deliberate media — pleasant, not productive
          quiet    a move was proposed and produced no action either way
        """
        outcome = str(outcome or "quiet").strip().lower()
        key = str(key or (self.current or {}).get("key") or "")
        repeat = bool(key and key == self._last_key)
        with self._lock:
            self.history.append({"key": key, "at": time.time(), "outcome": outcome,
                                 "note": str(note or "")[:160]})
            act = ACTIVITY_BY_KEY.get(key)
            self._last_key = key
            self._last_family = act.family if act else ""
            self.current = None
            self._dirty = True
        if outcome == "landed":
            self.note("landed", 1.0)
        elif outcome == "stalled":
            self.note("stalled", 1.0)
        elif outcome == "enjoyed":
            self.note("enjoyed", 1.0)
        if repeat:
            # Doing the same thing twice is the specific failure this module
            # exists to prevent, so it is worth two nudges toward boredom.
            self.note("repeated", 1.0)
        self.save()

    def settle_from_feed(self, entries) -> str:
        """Grade the move in flight against what the control feed actually shows.

        The evidence is the same one the HUD displays: rows the executor itself
        wrote while acting. This is why the mood cannot be gamed by the model's
        own account of how it went — a turn that says "done" while the feed
        shows three failures is scored as a stall, because that is what the
        machine recorded.

        Evaluated lazily at the *next* autonomous moment rather than on a timer:
        no threads, nothing to tear down on stop, and a whole minute of activity
        is graded at once instead of racing the first click.
        """
        with self._lock:
            cur = dict(self.current) if self.current else None
        if not cur:
            return ""
        outcome = outcome_from_feed(entries, since=float(cur.get("at") or 0.0))
        self.finish(outcome, key=str(cur.get("key") or ""))
        return outcome

    def should_change(self) -> bool:
        """True when the mood says the current line of activity has gone stale."""
        return self.mood.name == "BORED" and self._detour == 0 and bool(self.current)

    # ── reading ──────────────────────────────────────────────────────────
    def status(self) -> dict:
        self.load()
        with self._lock:
            self.mood.tick()
            info = self.mood.info()
            cur = dict(self.current) if self.current else None
            if cur:
                cur["elapsed"] = max(0.0, time.time() - float(cur.get("at") or 0.0))
            act = ACTIVITY_BY_KEY.get(str((cur or {}).get("key") or ""))
            recent = list(self.discoveries)[-4:]
        return {
            "mood": info,
            "activity": cur,
            "activity_label": (cur or {}).get("label") or "",
            "activity_family": act.family if act else "",
            "discoveries": recent,
            "discovery_count": int(self.stats.get("discoveries", 0)),
            "interests": list(self.interests)[-5:],
            "tasks": list(self.tasks),
            "stats": dict(self.stats),
            "scores": {k: round(v, 3) for k, v in self.mood.scores.items()},
        }


def outcome_from_feed(entries, *, since: float = 0.0) -> str:
    """Read a window of control-feed rows as one outcome. Pure.

    Kinds are written by the executor, not by a model: `ok` and `mode` mean
    something on the machine changed and it worked; `fail` means a step did not
    land; `act`/`plan` mean it was busy but nothing conclusive came back. A
    window with nothing in it at all is the most informative case — it means the
    turn talked and did not act, which is exactly the author's "random
    meaningless actions" complaint in reverse.
    """
    kinds: list[str] = []
    for row in (entries or []):
        try:
            if float((row or {}).get("at") or 0.0) < since:
                continue
        except (TypeError, ValueError):
            continue
        kinds.append(str((row or {}).get("kind") or ""))
    if any(k == "fail" for k in kinds):
        return "stalled"
    if any(k in ("ok", "mode") for k in kinds):
        return "landed"
    if any(k in ("act", "plan") for k in kinds):
        return "quiet"
    return "quiet"


def _clean_rows(rows, cap: int, keys: tuple) -> list[dict]:
    out: list[dict] = []
    for row in (rows or [])[:cap]:
        if isinstance(row, dict) and row.get("text"):
            clean = {k: row.get(k, "") for k in keys}
            clean["at"] = float(row.get("at") or 0.0)
            out.append(clean)
    return out


# Words that carry no topic information. Used only to pull a *subject* out of a
# sentence for the interest pool, so a list of "the, and, with" never happens.
_STOP = {
    "the", "a", "an", "and", "or", "but", "of", "to", "in", "on", "for", "with",
    "is", "was", "are", "were", "be", "been", "it", "its", "this", "that",
    "these", "those", "i", "you", "it's", "its", "at", "by", "from", "as",
    "about", "into", "over", "after", "before", "then", "than", "so", "if",
    "how", "what", "when", "where", "why", "who", "there", "here", "not", "no",
    "yes", "can", "could", "would", "should", "may", "might", "will", "just",
    "very", "really", "some", "any", "all", "more", "most", "also", "well",
    "using", "use", "used", "new", "now", "one", "two", "up", "out", "off",
}


def _subject(text: str, *, max_words: int = 5) -> str:
    """The most noun-ish phrase in a sentence, or ''. Cheap and dependency-free.

    This is not parsing and does not pretend to be: it takes the longest run of
    content words, which is right often enough to keep the interest pool
    readable ("local llm quantization" out of a longer finding).
    """
    words = [w.strip(".,;:!?()[]\"'`") for w in str(text or "").split()]
    words = [w for w in words if w and w.lower() not in _STOP and len(w) > 2]
    if not words:
        return ""
    # Prefer runs of capitalised or technical-looking words; fall back to the
    # first few content words in order.
    runs: list[list[str]] = []
    run: list[str] = []
    for w in words:
        if w[:1].isupper() or any(c.isdigit() for c in w) or "-" in w:
            run.append(w)
        else:
            if len(run) >= 2:
                runs.append(run)
            run = []
    if len(run) >= 2:
        runs.append(run)
    if runs:
        best = max(runs, key=len)
        return " ".join(best[:max_words])
    return " ".join(words[:max_words])


# ════════════════════════════════════════════════════════════════════════════
#  4. THE PROMPT BLOCK
# ════════════════════════════════════════════════════════════════════════════

def prompt_block(move: Optional[Move], *, autonomous: bool = True,
                 status: Optional[dict] = None) -> str:
    """What the autonomous turn is told: its mood, its choice, and the freedom
    to overrule both.

    The last paragraph matters most. A director that overrides a user's
    unfinished work because a weight said so is worse than no director at all,
    so the block says plainly that the user's work comes first and the
    suggestion can be discarded.
    """
    info = (status or {}).get("mood") or {}
    name = str(info.get("name") or (move.mood if move else "CURIOUS"))
    meta = MOOD_INFO.get(name) or MOOD_INFO["CURIOUS"]
    lines = [f"## Right now: {meta['label'].upper()} — {meta['blurb']}"]
    if move is not None:
        lines += [
            "",
            f"Your own choice for the next few minutes: {move.label}",
            f"Why this and not something else: {move.reason}.",
            "",
            "The mission: " + move.mission,
        ]
    else:
        lines += [
            "",
            "Nothing in the catalogue clears the bar for being worth doing, so "
            "the honest next move is to stay quiet. Speak only if you have "
            "something real; otherwise do nothing at all.",
        ]
    if not autonomous:
        lines += ["", "(Autonomous mode is off, so describe and suggest — do not "
                      "touch the machine.)"]
    lines += [
        "",
        "Override rule: if the user's unfinished work, or anything they have "
        "just said, is more worth doing than the above, do that instead and "
        "ignore the suggestion. The suggestion exists because a handed-over "
        "machine with no idea what to do next is a worse assistant than one "
        "with a plan — not to take the choice away from you.",
    ]
    return "\n".join(lines)


# ════════════════════════════════════════════════════════════════════════════
#  5. THE PROCESS-WIDE INSTANCE
# ════════════════════════════════════════════════════════════════════════════

_DIRECTOR: Optional[Director] = None
_DIRECTOR_LOCK = threading.Lock()


def director() -> Director:
    """The one director for this process, created on first use."""
    global _DIRECTOR
    with _DIRECTOR_LOCK:
        if _DIRECTOR is None:
            _DIRECTOR = Director()
        return _DIRECTOR


def reset(director_obj: Optional[Director] = None) -> Director:
    """Swap in a fresh director (tests, and the UI's "start over")."""
    global _DIRECTOR
    with _DIRECTOR_LOCK:
        _DIRECTOR = director_obj if director_obj is not None else Director()
        _DIRECTOR._loaded = director_obj is not None
        return _DIRECTOR


def status() -> dict:
    try:
        return director().status()
    except Exception:
        return {"mood": dict(MOOD_INFO["CURIOUS"]), "activity": None,
                "discoveries": [], "discovery_count": 0, "stats": {}}


def next_move(**kwargs) -> Optional[Move]:
    return director().next_move(**kwargs)


def finish(outcome: str, *, key: str = "", note: str = "") -> None:
    try:
        director().finish(outcome, key=key, note=note)
    except Exception:
        pass


def note_discovery(text: str, **kwargs) -> dict:
    try:
        return director().note_discovery(text, **kwargs)
    except Exception:
        return {}


def note_task(text: str) -> None:
    try:
        director().note_task(text)
    except Exception:
        pass


def forget() -> None:
    try:
        director().forget()
    except Exception:
        pass


def grade_in_flight(entries) -> str:
    """Module-level convenience used by the idle loop before it asks for the
    next move. Never raises: a bad grade is not worth losing a turn over."""
    try:
        return director().settle_from_feed(entries)
    except Exception:
        return ""


#: `directed` is called from the audio receive loop, once per recognised
#: utterance. A conversation would otherwise apply the same nudge fifty times a
#: minute and pin the mood at FOCUSED for the whole session, so it is throttled
#: here rather than at the call site — the caller should not have to know how
#: often the mood needs telling.
_DIRECTED_MIN_GAP = 20.0
_directed_at: float = 0.0


def note(event: str, weight: float = 1.0) -> None:
    """Tell the mood something happened ("directed" when the user speaks up)."""
    global _directed_at
    name = str(event or "").strip().lower()
    if name == "directed":
        now = time.monotonic()
        if (now - _directed_at) < _DIRECTED_MIN_GAP:
            return
        _directed_at = now
    try:
        director().note(name, weight)
    except Exception:
        pass


def _announce_mood() -> None:
    """Say the mood changed, once, on the feed. The UI colours the chip from the
    published state; this is for the scrolling rail, where a mood change is a
    real event in the session and should be visible in time order."""
    try:
        d = director()
        info = d.mood.info()
        _feed("mood", f"{info['label']} — {info['blurb']}")
    except Exception:
        pass


def _feed(kind: str, text: str, **extra) -> None:
    try:
        from core import autonomy
        autonomy.feed(kind, text, **extra)
    except Exception:
        pass
