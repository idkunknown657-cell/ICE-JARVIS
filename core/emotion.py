"""The emotional delivery engine: user message → emotion state → delivery.

The conversation model is expressive on its own; what it lacks is a compact,
zero-cost way to know HOW to sound before it answers. This module closes the
chain the brief asks for:

    Context → Emotion Detection → Emotion State → Response Style → Voice Delivery

Three public pieces:

- `detect_emotion(text)` — merges the deterministic keyword read (core.tone)
  with conversational-context signals (greetings, task-success, failure,
  gratitude, questions) into one emotion label from a fixed palette.

- `delivery_tag(emotion)` — a short "[DELIVER: …]" instruction the model reads
  before answering: how warm, how playful, how fast, how soft. It deliberately
  says nothing about the reply's CONTENT — that is the model's job.

- `voice_params(emotion)` — prosody parameters (rate/pitch/volume nudges) for
  TTS engines that support them, so the voice actually sounds the emotion.

Anti-overact is a hard rule: the palette is bounded, intensities are capped,
neutral passes through untouched, and the tags describe restraint. A companion,
not a character.

Zero network calls, no model calls — everything here is cheap enough to run on
every message.
"""

from __future__ import annotations

import re

from core import tone as _tone

# ── The palette ───────────────────────────────────────────────────────────────
# Fixed and small on purpose: every state has hand-written delivery text and
# TTS params, so an unbounded palette would silently lose both.
EMOTIONS = (
    "neutral", "happy", "excited", "amused", "playful", "curious",
    "supportive", "concerned", "surprised", "calm", "focused",
)

# Delivery tags — the model's instruction for HOW to sound. Each one names the
# register and the restraint, never the words.
_DELIVERY = {
    "neutral":    "warm and easy, like a relaxed friend in the room",
    "happy":      ("let a real smile into your voice — pleased, a touch "
                   "brighter, one honest reaction before the substance"),
    "excited":    ("match their energy — quick, bright, delighted; one beat of "
                   "shared excitement, then be useful. Do not stay bouncy "
                   "longer than the moment lasts"),
    "amused":     ("let the laugh show — a warm 'hehe' or an honest chuckle "
                   "landed naturally; play the observation back, then answer"),
    "playful":    ("tease back lightly and with obvious affection — witty, "
                   "quick, never sharp; land the joke and move on"),
    "curious":    ("lean in — interested, a little delighted, ask the one real "
                   "question you actually have"),
    "supportive": ("slow down and soften — warm, steady, on their side; no "
                   "cheerfulness, no pep talk, just 'we've got this'"),
    "concerned":  ("gentle and careful — quieter, slower, unhurried; take the "
                   "weight off before offering anything"),
    "surprised":  ("react honestly — a genuine 'wait, really?' moment, then "
                   "settle and be useful"),
    "calm":       ("low and even — the relaxed register of an evening with "
                   "nothing to fix; no forced energy"),
    "focused":    ("clean and direct, minimal warmth overhead — competent and "
                   "quick, they are working"),
}

# ── Conversational context signals ───────────────────────────────────────────
# (emotion, patterns) — checked on the lowercased message. The first tables are
# cheap regexes because these moments repeat constantly in real use.
_GREET = re.compile(
    r"^(hey+|hi+|hello+|yo+|sup|good (morning|evening|afternoon)|"
    r"merhaba|selam|günaydın|iyi geceler|namaste|salut|ciao)\b", re.I)
_HOWARE = re.compile(r"how (are|r) (you|u|things)|nasılsın|what's up|whats up|wassup", re.I)
_SUCCESS = re.compile(
    r"\b(finally (fixed|works|worked|done|finished)|it('s| is| was) (working|"
    r"fixed|done)|i (did|fixed|solved|cracked) it|we (did|fixed) it|"
    r"tamam(landı|dır)?\s*!?$|çözüldü|olmuş)\b", re.I)
_FAIL = re.compile(
    r"\b(this isn't working|not working|keeps (failing|crashing)|it broke|"
    r"it's broken|it doesnt? work|it does not work|failed again|"
    r"olmuyor|çalışmıyor|bozuldu)\b", re.I)
_THANKS = re.compile(r"\b(thanks|thank you|ty|teşekkür|sağ ?ol|sağol|merci)\b", re.I)
_QUEST = re.compile(r"\?\s*$")
_WORKING = re.compile(
    r"\b(working on|debugging|coding|refactor|writing|building|let me|"
    r"give me a sec|bir bak|üygulama)\b", re.I)
_CUTE = re.compile(r"\b(aw+w|so cute|adorable|kotek|baby (animal|cat|dog)|kitten|puppy)\b", re.I)

# Emoji → emotion shorthand. People's fastest emotional signals are emoji.
_EMOJI_MOODS = (
    ("😂🤣😆", "amused"),
    ("😄😃😀😁😊🙂🥰😍", "happy"),
    ("🔥🎉💪🚀👏", "excited"),
    ("😜😝😏🙃", "playful"),
    ("🤔🧐", "curious"),
    ("😢😭😞☹️", "concerned"),
    ("😮😯😲🤯", "surprised"),
    ("😌😪😴", "calm"),
)


def _emoji_emotion(text: str) -> str:
    best, count = "", 0
    for emojis, emo in _EMOJI_MOODS:
        c = sum(text.count(e) for e in emojis)
        if c > count:
            best, count = emo, c
    return best


# tone.mood (from keywords) → palette. The mood labels and palette overlap but
# are not identical; this is the mapping.
_TONE_MAP = {
    "frustrated": "supportive",
    "angry":      "supportive",
    "excited":    "excited",
    "sad":        "concerned",
    "anxious":    "supportive",
    "tired":      "calm",
    "surprised":  "surprised",
    "joking":     "amused",
    "playful":    "playful",
    "serious":    "focused",
}


def detect_emotion(text: str | None) -> dict:
    """Classify the emotional state of a user message.

    Returns {"emotion": <palette label>, "intensity": 0..1, "source": str}.
    Never raises; empty/None → neutral at 0.
    """
    t = (text or "").strip()
    if not t:
        return {"emotion": "neutral", "intensity": 0.0, "source": "empty"}

    low = t.lower()

    # 1. Conversational moments — the everyday context beats generic keywords:
    #    a success/failure moment says more than the word "finally" does.
    if _SUCCESS.search(low):
        return {"emotion": "excited", "intensity": 0.75, "source": "task-success"}
    if _FAIL.search(low):
        return {"emotion": "supportive", "intensity": 0.65, "source": "task-failure"}
    if _CUTE.search(low):
        return {"emotion": "amused", "intensity": 0.6, "source": "cute"}

    # 2. Deterministic keyword read (the strongest signal when it fires).
    mood_r = _tone.detect_mood(t)
    if mood_r["strength"] >= 0.6 and mood_r["mood"] != "neutral":
        emo = _TONE_MAP.get(mood_r["mood"], "neutral")
        return {"emotion": emo, "intensity": min(1.0, mood_r["strength"]),
                "source": f"tone:{mood_r['mood']}"}
    if _GREET.match(t):
        emo = "happy" if _HOWARE.search(low) else "happy"
        return {"emotion": emo, "intensity": 0.55, "source": "greeting"}
    if _THANKS.search(low):
        return {"emotion": "happy", "intensity": 0.45, "source": "gratitude"}

    # 3. Emoji shorthand.
    emo = _emoji_emotion(t)
    if emo:
        return {"emotion": emo, "intensity": 0.6, "source": "emoji"}

    # 4. Mode signals: working vs asking.
    if _WORKING.search(low):
        return {"emotion": "focused", "intensity": 0.45, "source": "working"}
    if _QUEST.search(t) and len(t) < 140:
        return {"emotion": "curious", "intensity": 0.4, "source": "question"}

    return {"emotion": "neutral", "intensity": 0.0, "source": "default"}


def delivery_tag(text: str | None, threshold: float = 0.5) -> str:
    """The [DELIVER: …] line for a user message, "" when neutral/weak.

    Prepended next to the existing [MOOD: …] tag so the model reads both before
    composing. Kept to one short line — the model already has a personality
    contract; this only sets the emotional register of THIS reply.
    """
    r = detect_emotion(text)
    if r["emotion"] == "neutral" or r["intensity"] < threshold:
        return ""
    d = _DELIVERY.get(r["emotion"], "")
    if not d:
        return ""
    return f"[DELIVER: {d}]"


def emotion_of(text: str | None) -> str:
    """Just the label — used by TTS paths and logging."""
    return detect_emotion(text)["emotion"]


# ── Voice delivery ────────────────────────────────────────────────────────────
# Prosody nudges per emotion. Rate/pitch are multiplicative (1.0 = engine
# default); volume is a gain. The nudges are deliberately small — the brief's
# "do not overact" applies to the voice more than anywhere else: ±8% rate,
# ±6% pitch is the difference between "sounds happy" and "sounds unhinged".
_VOICE_PARAMS: dict[str, dict] = {
    "neutral":    {},
    "happy":      {"rate": 1.03, "pitch": 1.05},
    "excited":    {"rate": 1.08, "pitch": 1.06},
    "amused":     {"rate": 1.03, "pitch": 1.04},
    "playful":    {"rate": 1.05, "pitch": 1.05},
    "curious":    {"rate": 0.99, "pitch": 1.03},
    "supportive": {"rate": 0.94, "pitch": 0.98},
    "concerned":  {"rate": 0.92, "pitch": 0.96},
    "surprised":  {"rate": 1.05, "pitch": 1.06},
    "calm":       {"rate": 0.96, "pitch": 0.99},
    "focused":    {"rate": 1.02, "pitch": 1.0},
}


def voice_params(emotion: str) -> dict:
    """TTS prosody parameters for an emotion. Empty dict = engine defaults."""
    return dict(_VOICE_PARAMS.get(emotion, {}))
