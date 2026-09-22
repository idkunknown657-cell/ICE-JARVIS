"""Deterministic emotional-tone detection for the user's typed messages.

Produces a short "[MOOD: ...]" tag that is prepended to text user-turns so the
model gets a clear, concise signal about how the person sounds — mirroring the
tone without noise on neutral messages. No LLM calls: the signal is keyword /
pattern heuristics, so strength is a simple confidence score.

`detect_mood(text)` -> {"mood", "strength", "delivery"}
`mood_tag(text, threshold=0.6)` -> "" or "[MOOD: you sound X — Y]"
"""

import re

# (mood, delivery hint, keywords) — keywords appear lowercased in the message.
# Match explicitly-named feelings first; wordiness signals come second.
_MOODS = [
    ("frustrated",
     "they sound frustrated — stay calm, soft and sure; take over and fix it, "
     "do not lecture",
     ("frustrating", "frustrated", "aggravated", "annoying", "annoyed",
      "why won't", "why is it not", "why does it not", "what the hell",
      "what is wrong", "not working", "stopped working", "broken", "keeps "
      "crashing", "won't work", "doesn't work", "doesn't wanna",
      "can't believe", "cannot believe", "this stupid", "so stupid", "dumb",
      "ridiculous",
      "give me a break", "oh come on", "seriously now", "are you kidding")),
    ("angry",
     "they sound angry — be calm competence, no cleverness, no jokes, patient and "
     "brief",
     ("i hate", "hate this", "hated", "furious", "pissed", "fed up", "sick of",
      "sick and tired", "trash", "garbage", "junk", "unbelievable",
      "i'm so mad", "angry", "rage")),
    ("excited",
     "they sound excited and happy — meet the energy, share the delight, keep it "
     "bubbly but real",
     ("awesome", "amazing", "excited", "great news", "so happy", "i love it",
      "love this", "this is the best", "finally", "yay", "wohoo", "woo hoo",
      "let's go", "let's do this", "can't wait", "works perfectly",
      "perfectly", "it's working", "done it", "i did it", "we did it")),
    ("sad",
     "they sound sad or down — be soft, steady and caring; quick to help, warm "
     "without forcing cheer",
     ("i'm sad", "so sad", "feeling sad", "depressed", "down today", "rough "
      "day", "terrible day", "awful day", "i'm crying", "lonely", "hurt",
      "heartbroken", "disappointed", "this sucks")),
    ("anxious",
     "they sound anxious or worried — be calm, concrete and reassuring; give a "
     "clear plan, do not pile on more",
     ("nervous", "worried", "worrying", "anxious", "stressed", "dreading",
      "scared", "afraid", "panic", "panicking", "overthinking",
      "i can't sleep", "worried about")),
    ("tired",
     "they sound tired — be gentle, brief and practical; do not demand more energy "
     "than they have",
     ("i'm tired", "so tired", "exhausted", "worn out", "sleepy", "drained",
      "no energy", "burned out", "long day", "can't think")),
    ("surprised",
     "they are surprised — react with them, share the moment, ask what happened",
     ("wow", "omg", "no way", "whoa", "wait what", "you're kidding", "what?!",
      "really?", "i can't believe it", "that's crazy", "that's wild")),
    ("joking",
     "they are joking or teasing — play back with light humour and wit, match the "
     "playfulness",
     ("ha ha", "haha", "lol", "lmao", "😂", "🤣", "😜", "😝", "just kidding",
      "jk", "just joking", "pulling your leg", "obviously not", "as if")),
    ("playful",
     "they are being playful — keep the banter light, cheeky, and kind",
     ("😊", "😄", "😁", "silly", "mischief", "cheeky", "🤗", "teehee", "hehe")),
]

# Patterns that mark a message as emphatic, regardless of content: heavy
# punctuation, shouting (all-caps), and emphatic repeats ("no no no").
_EMPATHIC = re.compile(r"([!?])\1{1,}|[!?.]{2,}|\b([a-z]+)( \1){2,}\b", re.I)
_SHOUT = re.compile(r"\b[A-Z]{2,}\b")
_SHOUT_PAT = re.compile(r"(^| )([A-Z]{2,}[a-z]*)( |$|[!?])")

# A very strong single word ("PERFECT!", "FINALLY!") still counts as emphasis
# even without hurling a full sentence of caps.
_SERIOUS_HINTS = (
    "serious", "seriously", "not joking", "i'm not kidding", "no jokes",
)


def detect_mood(text: str | None) -> dict:
    """Classify the emotional tone of a short user message.

    Returns {"mood", "strength", "delivery"}. mood is one of the _MOODS labels
    or "neutral"; strength is a rough 0..1 confidence; delivery is the one-line
    mirroring instruction paired with the mood ("" for neutral).
    """
    t = (text or "").strip()
    if not t:
        return {"mood": "neutral", "strength": 0.0, "delivery": ""}

    low = t.lower()
    hits = [(mood, delivery, kws) for mood, delivery, kws in _MOODS
            if any(kw in low for kw in kws)]

    # Punctuation / shouting / stutter push the score up. A single "!" already
    # makes a message lively; "!!/?!" or all-caps is emphatic.
    score = 0.0
    if re.search(r"[!?]{2,}", t) or _EMPATHIC.search(t):
        score += 0.25
    if _SHOUT_PAT.search(t):
        score += 0.20
    _cap_words = sum(1 for _ in re.finditer(r"[A-Z]{2,}", t))
    if t.isupper() and len(t) >= 4:
        score += 0.15

    explicit = len(hits) > 1
    base = 0.6 if hits else 0.0
    confidence = max(0.0, min(1.0, base + score + 0.15 * explicit))

    if not hits:
        # Emphatic but no keyword: strong punctuation alone can still carry a
        # clear mood, and an explicit serious request overrides everything.
        if any(h in low for h in _SERIOUS_HINTS):
            return {"mood": "serious", "strength": 0.7,
                    "delivery": ("they are being serious — be direct, brief and "
                                 "no-nonsense, leave the jokes out")}
        if confidence >= 0.35 and "!" in t and "?" not in t:
            return {"mood": "excited", "strength": 0.6,
                    "delivery": _delivery_for("excited")}
        return {"mood": "neutral", "strength": 0.0, "delivery": ""}

    # Pick the strongest hit: specificity beats punctuation, and the mood with
    # the most matching keywords wins ties.
    best = max(hits, key=lambda h: (len([k for k in h[2] if k in low]),
                                    confidence))
    mood, delivery, _kws = best
    return {"mood": mood, "strength": confidence, "delivery": delivery}


def _delivery_for(mood: str) -> str:
    for _m, d, _k in _MOODS:
        if _m == mood:
            return d
    return ""


def mood_tag(text: str | None, threshold: float = 0.6) -> str:
    """A short bracketed tag for strong moods, "" otherwise.

    Threshold is the confidence floor — only clearly-felt messages get a tag, so
    neutral or mildly worded messages stay untouched.
    """
    r = detect_mood(text)
    if r["strength"] < threshold:
        return ""
    return f"[MOOD: you sound {r['mood']} — {r['delivery']}]"