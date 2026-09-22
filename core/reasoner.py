"""Adaptive task routing — pick the right tier of model for the task at hand.

Not every request deserves the heavy reasoning tier. Small talk, thanks, one-
liners and greeting exchanges are satisfied comfortably by the lightweight FAST
models (and by the free OpenAI-compatible providers that back them when Gemini
is drained); planning, debugging, comparison and real reasoning earn the SMART
ladder. This module is the cheap, deterministic classifier that tells callers
which bucket a prompt falls into — no model call, no network.
"""

import re

from core import gemini

# Greetings and pure social acknowledgements — cheapest of the cheap. The heavy
# tier adds nothing here and the FREE providers handle them fine.
_SOCIAL_FIRST = {
    "hi", "hey", "hello", "hiya", "yo", "wassup", "sup", "namaste", "salaam",
    "good", "goodnight", "thanks", "thank", "thx", "bye", "tata", "welcome",
    "ok", "okay", "sure", "k", "lol", "lmao", "rofl", "haha", "hehe", "chalo",
    "shukriya", "dhanyawad", "kaise", "kya", "haan", "hmm", "mm", "nice",
    "alright", "perfect", "great",
}

_SOCIAL_TAIL_WORDS = {
    "a", "lot", "so", "much", "morning", "afternoon", "evening", "night", "day",
    "and", "you", "u", "ya", "too", "bro", "bhai", "yaar", "dost", "friend",
    "buddy", "there", "guys", "everyone", "ho", "theek", "fine", "sure", "one",
    "going", "it", "hey", "hi", "thanks", "ok", "okay", "haan", "hmm", "mm",
    "right", "absolutely", "totally", "awesome", "amazing", "great", "lol",
    "lmao", "haha", "hehe",
}

_SOCIAL_TAIL_ALT = re.compile(
    r"^(how('s| is| are)?\s+(you|u|things|it\s+going)|"
    r"whats\s+up|what('s| is)\s+up)$", re.I)

_COLLAPSE_REP = re.compile(r"(.)\1+$")


def _is_social(t: str) -> bool:
    """True when a message is (almost) pure social exchange — greeting,
    thanks, acknowledgement, filler. A short greeting plus a small-talk follow-
    up ("Heyy, how are you?") also counts, but never a real request."""
    words = re.findall(r"[a-z]+", t.lower())
    if not words:
        return True
    first = _COLLAPSE_REP.sub(r"\1", words[0])   # heyy -> hey, looool -> lol
    if first not in _SOCIAL_FIRST:
        return False
    if len(words) > 6:
        return False
    if _TASK_HINTS.search(t) or _HARD_VERBS.search(t) or _REASONING_HINTS.search(t):
        return False
    tail = words[1:]
    if not tail:
        return True
    if all(w in _SOCIAL_TAIL_WORDS for w in tail):
        return True
    return bool(_SOCIAL_TAIL_ALT.match(" ".join(tail)))

_HARD_VERBS = re.compile(
    r"\b(create|build|write|make|develop|full|complete|implement|refactor|"
    r"optimize|troubleshoot|debug|design|architect|explain (in|how|why)|"
    r"why did|why is|why does|compare|analyze|evaluate|plan|strateg(y|ize)|"
    r"breakdown|step by step)", re.I)

_REASONING_HINTS = re.compile(
    r"\b(because|therefore|however|assuming|if .* then|trade-?offs|"
    r"pros and cons|alternatives|scenarios|root cause|regression|complexity|"
    r"privacy|security|should i|is it better)", re.I)

_TASK_HINTS = re.compile(
    r"\b(open|close|shut|launch|start|stop|run|search|find|show|look up|"
    r"download|install|set |change|turn |play|pause|stop (the )?music|volume|"
    r"wallpaper|screenshot|remind|schedule|call |message|what time|weather|"
    r"news|translate|summar|draft|rewrite|type |paste|copy|save|update|"
    r"configure|restart|clear|empty|mute|unmute)", re.I)

_VISION_HINTS = re.compile(
    r"\b(what do you see|describe (the )?(screen|image|photo|picture)|look at|"
    r"read (the )?(screen|text)|what('s| is) on (the )?(screen|my screen)|"
    r"see my screen|analyze (the )?image)", re.I)


def classify(prompt: str | None) -> dict:
    """Categorise a prompt into a {nature, smalltalk, hard, vision, tier}.

    The tag is deliberately coarse — three buckets is all the routing needs:
      nature    — one of social / vision / task / reasoning / complex / plain
      smalltalk — social exchanges, greeting, thanks, filler
      hard      — genuinely needs the reasoning ladder
      vision    — talks about looking at a screen/image
      tier      — a gemini tier ("fast" | "smart"), the adaptive decision
    """
    t = re.sub(r"\s+", " ", (prompt or "")).strip()
    if not t:
        return {"nature": "plain", "smalltalk": False, "hard": False,
                "vision": False, "tier": gemini.SMART}

    if _VISION_HINTS.search(t):
        nature, hard = "vision", False
    elif _is_social(t):
        nature, hard = "social", False
    elif _HARD_VERBS.search(t) or _REASONING_HINTS.search(t):
        nature, hard = "complex", True
    elif _TASK_HINTS.search(t):
        nature, hard = "task", True
    else:
        nature, hard = "plain", False

    # Social and vision never need the heavy tier; short plain asks use FAST;
    # task/complex reasoning earns SMART. Adaptive, not expensive.
    if nature == "social":
        tier = gemini.FAST
    elif nature == "vision":
        tier = gemini.SMART          # images want the better vision model
    elif nature == "complex":
        tier = gemini.SMART
    else:
        tier = gemini.FAST if len(t) <= 120 else gemini.SMART

    return {"nature": nature, "smalltalk": nature == "social",
            "hard": hard, "vision": nature == "vision", "tier": tier}


def nature_tag(prompt: str | None) -> str:
    """Short stable label for memory/statistics ("" when plain)."""
    r = classify(prompt)
    return r["nature"] if r["nature"] not in ("plain",) else ""


def pick_tier(prompt: str | None) -> str:
    """The tier a given prompt should run on — the adaptive routing decision."""
    return classify(prompt)["tier"]