"""Lightweight script/language detection for naturally mixed conversations.

JARVIS speaks the language of the person in front of it. The Gemini prompt
already says "mirror the user's most recent message", but a Roman-script
Hinglish line reads as plain English to a scanner and the model can drift to
pure English. This module gives callers a cheap, deterministic signal:

  detect_language("kal wala kaam bata")  -> "hinglish"
  detect_language("आप कैसे हैं")          -> "hindi"
  detect_language("please open chrome")   -> "english"

No ML, no network — a script check plus a small marker lexicon, so it runs in
microseconds on every typed turn and is fully unit-testable.
"""

import re

_DEVANAGARI = re.compile(r"[\u0900-\u097F]")
_WORD = re.compile(r"[a-z]+")

# Roman-script Hindi/Hinglish words. All-lowercase. TWO distinct matches switch
# a Latin-script line to "hinglish"; one is too weak ("kal" is a name, "ho" is
# Christmas-season English, "kar" appears in "karate"). The strong set alone is
# distinctive enough on its own.
MARKERS = frozenset({
    "matlab", "theek", "thik", "nahi", "yaar", "achha", "accha", "kya",
    "kaise", "kahan", "karna", "karo", "karta", "karte", "chahiye", "chaho",
    "chahte", "wala", "wali", "jaldi", "thoda", "thodi", "bata", "batao",
    "dekho", "dekha", "bolo", "bola", "samajh", "sawal", "haan", "kal",
    "abhi", "zyada", "bahut", "kuch", "aaj", "mera", "meri", "mere", "tum",
    "aap", "kaam", "raha", "rahi", "rahe", "hoga", "hogayi", "hoke", "karna",
})

# Words that appear when text is verbatim Latin ("minimise false positives"):
_STRONG_MARKERS = {
    "matlab", "theek", "nahi", "yaar", "achha", "accha", "kya", "kaise",
    "kahan", "karna", "chahiye", "wala", "jaldi", "thoda", "batao", "samajh",
    "sawal", "haan", "zyada", "bahut", "chaho",
}


def _words(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))


def _marker_hits(words: set[str]) -> tuple[int, int]:
    hits = [w for w in words if w in MARKERS]
    strong = sum(1 for w in hits if w in _STRONG_MARKERS)
    return len(hits), strong


def detect_language(text: str | None) -> str:
    """Classify a short user line as "hindi", "hinglish", "english" or "other".

    Devanagari presence (and it not being overshadowed by Latin words) → hindi.
    A Latin-script line that clearly uses Roman Hindi markers → hinglish.
    Anything else that is mostly Latin → english.
    """
    t = (text or "").strip()
    if not t:
        return "other"

    n_deva = len(_DEVANAGARI.findall(t))
    n_latin = len(_WORD.findall(t.lower()))

    # Devanagari dominates → Hindi. A Hindi line that also carries Latin words
    # ("ok theek hai, wifi chalo") is still Hindi-flavoured.
    if n_deva and n_deva >= max(1, n_latin // 2):
        return "hindi"

    hits, strong = _marker_hits(_words(t))
    if n_latin and (hits >= 2 or strong >= 1):
        return "hinglish"
    if n_latin:
        return "english"
    if n_deva:
        return "hindi"
    return "other"


def language_directive(text: str | None) -> str:
    """A short bracketed prompt hint for a detected line, "" for English/other
    (English is the default the model produces, so no tag is needed there).

    Lives in the user-turn itself (like [MOOD: ...]) so the model mirrors a
    mixed-language line exactly instead of flattening it to English.
    """
    lang = detect_language(text)
    if lang == "hindi":
        return ("[LANG: the user wrote Hindi — reply in Hindi (keep the "
                "same script they used).]")
    if lang == "hinglish":
        return ("[LANG: the user wrote Hinglish (Roman Hindi mixed with "
                "English) — mirror that exact natural mix, never flatten to "
                "pure English.]")
    return ""


def last_directive(lines: list[str]) -> str:
    """Directive for the given conversation snippet, based on the most recent
    user line (the one it should mirror). lines like "User: ...". Split on
    the first colon to get the user content."""
    for line in reversed(lines or []):
        mark = line.find(":")
        if mark <= 0:
            continue
        speaker, content = line[:mark].strip().lower(), line[mark + 1:].strip()
        if speaker in ("user", "you"):
            return language_directive(content)
    return ""


def strip_transient_prefix(s: str) -> str:
    """Remove the scaffolding JARVIS itself prepends to a typed turn — the
    [NOW] realtime-context block and the [REASON] directive — before a line is
    echoed back into the conversation log, so learning never mistakes its own
    decorations for user speech. The [MOOD]/[LANG] tags are kept (they were
    part of the pre-existing behaviour and are model-facing, not noise)."""
    try:
        s = re.sub(r"^\[NOW —.*?\n\n", "", s, flags=re.S)
        s = re.sub(r"^\[REASON\][^\n]*\n", "", s)
    except Exception:
        pass
    return s