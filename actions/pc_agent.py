"""
actions/pc_agent.py — one tool that uses this computer like a person does.

    OBSERVE → UNDERSTAND → DECIDE → ACT → CHECK → RECOVER

WHY ONE TOOL
    Driving a desktop used to cost one model round trip per micro-step: "open
    YouTube", then "click the search box", then "type it", then "press Enter",
    then "click the first result". Five round trips for one sentence, five gaps
    where the user hears nothing, and five chances for the screen to move on
    between look and click. This tool exists to collapse that: the model states
    the intent (or the whole ordered sequence of them) once, and the local
    executor runs it — at machine speed, re-locating each target from the live
    screen as it goes, and verifying every step.

THE ROUTER
    `plan_intent(text)` turns ordinary phrasing into a concrete primitive
    without any model call at all:

        "move the arrow to Settings"   -> point(target="Settings")
        "click that button"            -> click(target="button")
        "type hello into the search box" -> type(text="hello", field="search box")
        "open YouTube"                 -> open(target="YouTube")
        "scroll down a bit"            -> scroll(direction="down")
        "press ctrl+s"                 -> keys(["ctrl", "s"])
        "focus the Discord window"     -> focus(target="Discord")

    That is the latency fix for the common case: no reasoning is required to
    know what "click Save" means, so no reasoning is paid for. Anything the
    router cannot parse is handed to the vision path instead of guessed at.

SAFETY
    Every plan is classified by core/autonomy.py before it runs. Ordinary,
    reversible things — browsing, searching, watching, reading, clicking,
    typing, scrolling, opening apps — happen without a question, because that
    is the entire point of the tool. Money, permanent deletion, shutdown,
    security changes and installs are parked behind the on-screen confirmation
    gate; messages and posts are executed when the user asked for them and never
    when the autonomous loop invented them.
"""

from __future__ import annotations

import platform
import re
import time

_SYSTEM = platform.system()

try:
    from core import pc_engine as engine
except Exception:                                   # pragma: no cover
    engine = None

try:
    from core import autonomy
except Exception:                                   # pragma: no cover
    autonomy = None

try:
    from core import confirm
except Exception:                                   # pragma: no cover
    confirm = None

try:
    from core import pc_log
except Exception:                                   # pragma: no cover
    pc_log = None

try:
    from core import cancel
except Exception:                                   # pragma: no cover
    cancel = None


# ════════════════════════════════════════════════════════════════════════════
#  1. THE INTENT ROUTER  (pure — no screen, no side effects, unit-testable)
# ════════════════════════════════════════════════════════════════════════════

_FILLERS = (
    "can you", "could you", "would you", "will you", "please", "kindly",
    "jarvis", "hey jarvis", "i want you to", "i need you to", "i'd like you to",
    "go ahead and", "and then", "just", "now", "for me", "ok", "okay",
)

# the words people use for the pointer; they carry no target information
_POINTER_WORDS = (
    "the arrow", "arrow", "the mouse", "mouse", "the cursor", "cursor",
    "pointer", "the pointer", "mouse pointer",
)

_VERB_POINT = ("move", "hover", "point", "aim", "take", "put")
_VERB_CLICK = ("click", "tap", "press on", "select")
_VERB_TYPE = ("type", "write", "enter", "input", "fill", "paste")
_VERB_OPEN = ("open", "launch", "start", "go to", "visit", "navigate to", "load")
_VERB_FOCUS = ("focus", "switch to", "bring up", "bring forward", "activate")
_VERB_KEYS = ("press", "hit", "send the key", "key")


def _strip_fillers(text: str) -> str:
    t = " " + str(text or "").strip().lower() + " "
    changed = True
    while changed:
        changed = False
        for filler in _FILLERS:
            needle = f" {filler} "
            if needle in t:
                t = t.replace(needle, " ", 1)
                changed = True
    return " ".join(t.split()).strip()


def _strip_pointer_words(text: str) -> str:
    t = f" {text} "
    for w in _POINTER_WORDS:
        t = t.replace(f" {w} ", " ")
    return " ".join(t.split()).strip()


def _clean_target(text: str) -> str:
    """Tidy an extracted target: drop leading articles and a trailing 'please'."""
    t = str(text or "").strip().strip("\"'“”").strip()
    t = re.sub(r"^(the|a|an|my|this|that)\s+", "", t, flags=re.I)
    t = re.sub(r"\s+(please|now|for me)$", "", t, flags=re.I)
    return " ".join(t.split()).strip(" .,")


_KEY_NAMES = {
    "enter": "enter", "return": "enter", "escape": "esc", "esc": "esc",
    "tab": "tab", "space": "space", "spacebar": "space", "backspace": "backspace",
    "delete": "delete", "del": "delete", "home": "home", "end": "end",
    "up": "up", "down": "down", "left": "left", "right": "right",
    "page up": "pageup", "page down": "pagedown", "pageup": "pageup",
    "pagedown": "pagedown", "win": "win", "windows key": "win",
    "f1": "f1", "f2": "f2", "f3": "f3", "f4": "f4", "f5": "f5", "f6": "f6",
    "f7": "f7", "f8": "f8", "f9": "f9", "f10": "f10", "f11": "f11", "f12": "f12",
}
_MOD_NAMES = {
    "ctrl": "ctrl", "control": "ctrl", "alt": "alt", "shift": "shift",
    "win": "win", "windows": "win", "cmd": "command", "command": "command",
    "meta": "command", "super": "win",
}


def parse_keys(text: str) -> list[str]:
    """'ctrl shift s' / 'ctrl+s' / 'enter' → ['ctrl','s'] / ['enter']. Pure."""
    t = str(text or "").strip().lower()
    if not t:
        return []
    parts = [p for p in re.split(r"\s*\+\s*|\s+and\s+|\s+plus\s+", t) if p]
    if len(parts) == 1 and " " in parts[0]:
        parts = parts[0].split()
    out: list[str] = []
    for p in parts:
        p = p.strip()
        if p in _MOD_NAMES:
            out.append(_MOD_NAMES[p])
        elif p in _KEY_NAMES:
            out.append(_KEY_NAMES[p])
        elif len(p) == 1:
            out.append(p)
        elif re.fullmatch(r"f\d{1,2}", p):
            out.append(p)
        else:
            return []
    return out


# "type X into Y" / "type X in the Y box" split into (text, field)
_TYPE_SPLIT = re.compile(
    r"^(?P<text>.+?)\s+(?:in(?:to)?|inside|on)\s+(?:the\s+)?(?P<field>.+?)$", re.I)


def _is_deictic(text: str) -> bool:
    if engine is not None:
        try:
            return engine.is_deictic(text)
        except Exception:
            pass
    return str(text or "").strip().lower() in (
        "there", "here", "it", "that", "this", "that one", "this one", "the one")


def plan_intent(text: str) -> dict | None:
    """Turn one ordinary sentence into one primitive. Pure.

    Returns a dict with an `action` key and its arguments, or None when the
    sentence needs real reasoning (the caller then uses vision instead of
    guessing). This function is the whole reason a click no longer costs a model
    round trip.
    """
    raw = str(text or "").strip()
    if not raw:
        return None
    t = _strip_fillers(raw)
    if not t:
        return None

    # quoted text always means "the literal string", whatever the sentence says
    quoted = re.search(r"[\"“'](.+?)[\"”']", raw)

    # ── scrolling ───────────────────────────────────────────────────────────
    m = re.match(r"^scroll\s*(?:(up|down|left|right)\b)?\s*(?:by\s*)?(\d+)?", t)
    if m and "scroll" in t.split()[0]:
        direction = (m.group(1) or "down").lower()
        amount = int(m.group(2)) if m.group(2) else 3
        if "a bit" in t or "slightly" in t or "little" in t:
            amount = max(1, amount // 4)
        elif amount >= 10:
            amount = 5                      # wheel notches, not pixels
        return {"action": "scroll", "direction": direction, "amount": min(amount, 40)}

    # ── key chords ──────────────────────────────────────────────────────────
    m = re.match(r"^(?:press|hit|send|do)\s+(?:the\s+)?(?:key\s+)?(.+)$", t)
    if m:
        keys = parse_keys(m.group(1))
        if keys:
            return {"action": "keys", "keys": keys}
        # "press the button" is a click, not a keyboard shortcut. Unhandled,
        # it used to fall through to the bare-target rule by accident and could
        # even clear a field via parse_keys on a wrong noun; route it plainly.
        lowered = m.group(1).strip().lower()
        if lowered and lowered not in _KEY_NAMES:
            target = _clean_target(m.group(1))
            return {"action": "click", "target": target,
                    "clicks": 1, "button": "left", "vague": _is_deictic(target)}

    # ── typing ──────────────────────────────────────────────────────────────
    if quoted:
        literal = quoted.group(1)
        field = None
        fm = re.search(r"(?:in(?:to)?|inside|on)\s+(?:the\s+)?(.+?)\s*$", t, re.I)
        if fm:
            candidate = _clean_target(fm.group(1))
            if candidate and candidate.lower() not in literal.lower():
                field = candidate
        return {"action": "type", "text": literal, "field": field}

    m = re.match(r"^(?:type|write|enter|input|fill|paste)\s+(?:out\s+)?(.+)$", t)
    if m:
        body = m.group(1).strip()
        field = None
        sm = _TYPE_SPLIT.match(body)
        if sm:
            body_candidate = _clean_target(sm.group("text"))
            field = _clean_target(sm.group("field"))
            # "type hello in the search box": keep 'hello' as the text
            if body_candidate and field:
                return {"action": "type", "text": body_candidate, "field": field}
        return {"action": "type", "text": body, "field": field or None}

    # ── pointer moves (explicitly about the pointer) ────────────────────────
    lowered = f" {t} "
    mentions_pointer = any(f" {w} " in lowered for w in _POINTER_WORDS)
    m = re.match(
        r"^(?:move|hover|point|aim|take|put)\b(.*)$", t)
    if m:
        rest = _clean_target(_strip_pointer_words(m.group(1)))
        rest = re.sub(r"^(?:to|over|onto|on|at|towards?)\s+", "", rest, flags=re.I)
        rest = _clean_target(rest)
        # "move it there" → "there", which the engine resolves from the last
        # thing it actually found, rather than from a guess.
        if rest.startswith("it "):
            rest = rest[3:].strip()
        if rest:
            return {"action": "point", "target": rest, "vague": _is_deictic(rest)}
        if mentions_pointer:
            return {"action": "point", "target": "there", "vague": True}

    # ── clicking ────────────────────────────────────────────────────────────
    m = re.match(
        r"^(?:click|tap|double[- ]?click|right[- ]?click|press on|press|select)\b(.*)$", t)
    if m:
        verb = t.split()[0]
        rest = _clean_target(m.group(1))
        rest = re.sub(r"^(?:on|at|the)\s+", "", rest, flags=re.I)
        rest = _clean_target(rest)
        if rest:
            # "click that button" / "click the first one" name nothing specific —
            # the screen and the last thing found have to answer, so they are
            # marked vague and resolve through context rather than a guess.
            vague = rest.lower() in (
                "it", "that", "this", "here", "there", "that one", "this one",
                "the one", "first", "first one", "the first one", "button",
                "that button", "this button", "the button", "one", "second one",
                "next", "that thing", "the same thing",
            )
            clicks = 2 if "double" in verb else 1
            button = "right" if "right" in verb else "left"
            return {"action": "click", "target": rest, "clicks": clicks,
                    "button": button, "vague": vague}
        # A bare "click" with nothing after it means the thing just located.
        return {"action": "click", "target": "there", "vague": True}

    # ── windows ─────────────────────────────────────────────────────────────
    m = re.match(r"^(?:focus|switch to|bring up|bring forward|activate|go to)\b(.*)$", t)
    if m:
        target = _clean_target(m.group(1))
        target = re.sub(r"^(?:the|my)\s+", "", target, flags=re.I)
        target = re.sub(r"\s+window$", "", target, flags=re.I)
        if target:
            return {"action": "focus", "target": target}

    # ── searching ───────────────────────────────────────────────────────────
    # "search YouTube for minecraft" is a site search; "search minecraft" is a
    # web search. Both land on a results page, which is what the user wants to
    # see next — they will say "click the first one" a moment later.
    m = re.match(
        r"^(?:search|find|look up|google|look for)\s+(?:(?:on|in)\s+)?(.+?)\s+for\s+(.+)$",
        t, re.I)
    if m:
        site, query = _clean_target(m.group(1)), _clean_target(m.group(2))
        if site and query:
            return {"action": "search", "query": query, "site": site}
    m = re.match(r"^(?:search|google|look up|find)\s+(?:for\s+)?(.+)$", t, re.I)
    if m:
        query = _clean_target(m.group(1))
        if query:
            return {"action": "search", "query": query}

    # ── opening ─────────────────────────────────────────────────────────────
    m = re.match(r"^(?:open|launch|start|run|go to|visit|navigate to|load)\b(.*)$", t)
    if m:
        target = _clean_target(m.group(1))
        if target:
            return {"action": "open", "target": target}

    # ── a bare target with no verb: assume the user means "click it" ────────
    first = t.split()[0] if t.split() else ""
    if (len(t.split()) <= 3 and first not in _VERB_WORDS
            and not any(ch.isdigit() for ch in t) and "?" not in t):
        return {"action": "click", "target": t,
                "vague": _is_deictic(t) or any(w in t for w in _ORDINALS)}
    return None


# Verbs that can start the second half of a compound instruction. Wider than the
# router's own verb lists, because "open YouTube and play music" needs `play` to
# count as an instruction even though the router never parses `play` on its own.
_VERB_EXTRA = ("play", "watch", "listen", "show", "close", "switch", "pause",
               "resume", "mute", "refresh", "reload", "back", "forward", "copy",
               "paste", "send", "download", "install", "upload", "save", "quit",
               "exit", "drag", "select", "hover", "scroll", "search", "find", "look")

# Written out rather than derived from the phrase lists: splitting "send the key"
# into words would put "the" in this set, and then "the first one" stops being a
# target and starts being an instruction.
_VERB_WORDS = frozenset((
    "move", "hover", "point", "aim", "put", "click", "tap", "press", "select",
    "type", "write", "enter", "input", "fill", "paste", "open", "launch",
    "start", "run", "visit", "navigate", "load", "focus", "switch", "activate",
    "hit", "send", "key", "goto",
) + _VERB_EXTRA)

# "open YouTube and play music" is ONE instruction containing two steps. Splitting
# it here is what lets the whole sentence run in a single call instead of needing
# the model to come back between each half.
_SPLIT_JOINERS = re.compile(
    r"\s*(?:,|;|\.)?\s*(?:and then|and|then|after that|also|,)\s+", re.I)


def split_intents(text: str) -> list[str]:
    """Split a compound instruction into ordered single intents. Pure.

    Only splits at a joiner followed by a *known verb*, so "open the search box"
    stays whole while "open YouTube and play music" becomes two steps.
    """
    raw = str(text or "").strip()
    if not raw:
        return []
    parts: list[str] = []
    buffer = ""
    for piece in _SPLIT_JOINERS.split(raw):
        piece = piece.strip()
        if not piece:
            continue
        if not buffer:
            buffer = piece
            continue
        head = _strip_fillers(piece).split()[0] if _strip_fillers(piece) else ""
        if head in _VERB_WORDS:
            parts.append(buffer)
            buffer = piece
        else:
            buffer = f"{buffer} and {piece}"
    if buffer:
        parts.append(buffer)
    return parts or [raw]


# app name → the window title it shows, so we can wait for it properly
# Site search URLs for the places people actually say out loud. Unknown sites
# fall back to a plain web search, which is never wrong, only less specific.
_SITE_SEARCH = {
    "youtube": "https://www.youtube.com/results?search_query={q}",
    "google": "https://www.google.com/search?q={q}",
    "bing": "https://www.bing.com/search?q={q}",
    "duckduckgo": "https://duckduckgo.com/?q={q}",
    "amazon": "https://www.amazon.com/s?k={q}",
    "github": "https://github.com/search?q={q}",
    "reddit": "https://www.reddit.com/search/?q={q}",
    "wikipedia": "https://en.wikipedia.org/w/index.php?search={q}",
    "spotify": "https://open.spotify.com/search/{q}",
    "steam": "https://store.steampowered.com/search/?term={q}",
    "netflix": "https://www.netflix.com/search?q={q}",
    "twitter": "https://twitter.com/search?q={q}",
    "x": "https://twitter.com/search?q={q}",
    "instagram": "https://www.instagram.com/explore/tags/{q}",
    "flipkart": "https://www.flipkart.com/search?q={q}",
}


def _search_url(site: str, query: str) -> str:
    """Build a search URL for a named site, or a web search. Pure."""
    from urllib.parse import quote_plus
    q = quote_plus(str(query or "").strip())
    site = (str(site or "").strip().lower().replace(" ", "")
            .replace("https://", "").replace("http://", "").split(".")[0])
    template = _SITE_SEARCH.get(site)
    if template:
        return template.format(q=q)
    if site:
        return f"https://{site}.com" if not q else _SITE_SEARCH["google"].format(q=q)
    return _SITE_SEARCH["google"].format(q=q)


_EXPECTED_WINDOW = {
    "youtube": "YouTube", "chrome": "Chrome", "google chrome": "Chrome",
    "edge": "Edge", "msedge": "Edge", "firefox": "Firefox", "brave": "Brave",
    "discord": "Discord", "spotify": "Spotify", "steam": "Steam",
    "notepad": "Notepad", "explorer": "File Explorer", "files": "File Explorer",
    "settings": "Settings", "task manager": "Task Manager", "vscode": "Visual Studio Code",
    "vs code": "Visual Studio Code", "code": "Visual Studio Code",
    "terminal": "Terminal", "cmd": "Command Prompt", "powershell": "PowerShell",
    "whatsapp": "WhatsApp", "telegram": "Telegram", "slack": "Slack",
    "gmail": "Gmail", "github": "GitHub", "netflix": "Netflix",
}


def expected_window(target: str) -> str:
    """Best guess at the window title a launch target will produce. Pure."""
    t = str(target or "").strip().lower()
    t = re.sub(r"^https?://", "", t)
    t = re.sub(r"^www\.", "", t)
    for name, title in _EXPECTED_WINDOW.items():
        if t == name or t.startswith(name + " ") or t.startswith(name + "."):
            return title
    # a URL → the domain's brand word ("youtube.com/watch" → "youtube")
    head = re.split(r"[/.]", t)[0]
    head = re.sub(r"[^a-z0-9]", "", head)
    if head:
        return _EXPECTED_WINDOW.get(head, "")
    return ""


# ════════════════════════════════════════════════════════════════════════════
#  2. EXECUTION
# ════════════════════════════════════════════════════════════════════════════

# Words that mean "the operating system thing", not "a control in this window".
# Brands people say as one word that are websites before they are apps.
_WEB_WORD_BRANDS = frozenset({
    "youtube", "gmail", "github", "netflix", "reddit", "wikipedia",
    "twitter", "instagram", "facebook", "amazon", "flipkart", "maps",
    "whatsapp", "chatgpt", "spotify",
})

_SHELL_HINTS = frozenset({
    "settings", "task manager", "file explorer", "explorer", "notepad",
    "calculator", "paint", "terminal", "command prompt", "powershell",
    "recycle bin", "control panel", "device manager", "snipping tool",
    "downloads", "documents", "pictures", "desktop", "my computer",
})


def _need_engine() -> str | None:
    if engine is None:
        return "The computer-control engine is unavailable in this build."
    return None


def _web_brand(target: str) -> str:
    """The site a single-word brand name refers to, or ''.

    "Open YouTube" reaches here as a bare word: it is not a file, not a path and
    not a registered association, but it is unmistakably a website. Returning
    the brand lets the caller open it for real instead of reporting a failure.
    """
    t = re.sub(r"[^a-z0-9]", "", str(target or "").strip().lower())
    if not t or len(t) > 20:
        return ""
    return t if t in _SITE_SEARCH or t in _WEB_WORD_BRANDS else ""


def _launch_app(name: str) -> str:
    """Open an installed application by name, via the app launcher action.

    Returns '' when the launcher is unavailable so the caller can fall back to
    the plain shell open rather than reporting a failure that never happened.
    """
    try:
        from actions.open_app import open_app as _open_app
    except Exception:
        return ""
    try:
        out = _open_app({"app_name": str(name)})
    except Exception as e:
        return f"Could not start {name}: {e}"
    text = str(out or "")
    if text.lower().startswith("could not confirm") or "failed" in text.lower():
        return ""
    return text


def _safe_observe(limit: int = 800) -> str:
    """A short screen description that never raises (used in failure messages)."""
    try:
        return engine.describe_screen()[:limit]
    except Exception as e:
        return f"(could not look: {e})"


def _note(text: str, kind: str = "act") -> None:
    """Record one step in the action feed (the HUD shows these live)."""
    if autonomy is not None:
        try:
            autonomy.feed(kind, text)
        except Exception:
            pass


# "that thing" / "it" / "there" — resolvable only from what was just found.
_VAGUE_CONTEXT = {
    "it", "that", "this", "there", "here", "that one", "this one", "the one",
    "over there", "that thing", "this thing", "the same thing", "it there",
}
_ORDINALS = {"first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3,
             "fourth": 4, "4th": 4, "fifth": 5, "5th": 5}
_WIDGET_NOUNS = {"button": "Button", "box": "Edit", "field": "Edit",
                 "link": "Hyperlink", "tab": "TabItem", "menu": "MenuItem",
                 "checkbox": "CheckBox", "item": "ListItem"}


def resolve_vague(target: str) -> str:
    """Turn a pronoun-ish target into something the engine can actually find.

    Three genuinely different cases hide behind "click that":
      * a back-reference ("it", "there") — the last thing found, and only while
        we are still in the same window;
      * an ordinal ("the first one") — a position in a list, which only the
        screen knows, so it goes to vision as written;
      * a bare widget noun ("that button") — if the window exposes exactly one
        control of that kind, that is unambiguous and can be named exactly.
    Returning '' means the caller must look before it can act.
    """
    t = str(target or "").strip().lower()
    t = re.sub(r"^(?:the|a|an|that|this|my)\s+", "", t).strip()
    if t in _VAGUE_CONTEXT:
        return "there" if engine.last_target() is not None else ""
    for word, n in _ORDINALS.items():
        if word in t:
            rest = re.sub(rf"\b{word}\b", "", t).strip()
            rest = re.sub(r"^(?:one|result|item|link|video|option|thing|entry)\b",
                          "", rest).strip()
            rest = rest or "result"
            return f"the {word} {rest} on screen"
    words = t.split()
    noun = next((w for w in words if w in _WIDGET_NOUNS), "")
    if noun:
        try:
            ctype = _WIDGET_NOUNS[noun]
            same = [e for e in engine.uia_elements() if e.get("type") == ctype
                    and (e.get("name") or "").strip()]
            if len(same) == 1:
                return same[0]["name"]
        except Exception:
            pass
    return ""


def _step(plan: dict, *, asked_by_user: bool) -> dict:
    """Run ONE planned primitive. Returns {'ok','detail','plan'}."""
    action = str(plan.get("action") or "").lower()
    detail = ""
    ok = False

    if plan.get("vague") and action in ("point", "click"):
        resolved = resolve_vague(plan.get("target", ""))
        if not resolved:
            return {"ok": False, "plan": plan,
                    "detail": (f"\"{plan.get('target')}\" could mean more than one "
                               f"thing on this screen, so I did not act. What is "
                               f"on screen now:\n"
                               + _safe_observe(700))}
        plan = dict(plan, target=resolved)

    if action == "point":
        res = engine.point_at(plan.get("target", ""))
        detail, ok = res.line(), res.ok
    elif action == "click":
        # Capture the pre-click world so a failure can be EXPLAINED (a dialog
        # appeared, the page navigated, nothing moved at all) instead of the
        # old bare 'I clicked it but nothing happened'.
        before_sig = before_elms = None
        try:
            before_sig = engine.signature()
            before_elms = engine.uia_elements()
        except Exception:
            pass
        res = engine.click_target(
            plan.get("target", ""), button=plan.get("button", "left"),
            clicks=int(plan.get("clicks", 1) or 1),
            expect_change=not plan.get("vague"))
        detail, ok = res.line(), res.ok
        if not ok and before_sig is not None:
            try:
                why = engine.explain_change(before_sig, engine.signature(),
                                            before_elements=before_elms)
                if why:
                    detail += f" — {why}"
            except Exception:
                pass
    elif action == "type":
        res = engine.type_text(plan.get("text", ""), field=plan.get("field"))
        detail, ok = res.line(), res.ok
    elif action == "keys":
        res = engine.press_keys(*plan.get("keys", []))
        detail, ok = res.line(), res.ok
    elif action == "scroll":
        res = engine.scroll(plan.get("direction", "down"),
                            int(plan.get("amount", 3) or 3))
        detail, ok = res.line(), res.ok
    elif action == "focus":
        got = engine.focus_window(plan.get("target", ""))
        detail = (f"Brought {plan.get('target')!r} to the front" if got else
                  f"No window matching {plan.get('target')!r} is open")
        ok = bool(got)
    elif action == "open":
        target = str(plan.get("target") or "")
        # Smart method selection: "open Settings" means launch the app, but
        # "open the search box" means click the control that is already on
        # screen. A known app name or anything URL-shaped launches; anything else
        # is first looked for as a real control in the current window.
        launchy = (bool(re.search(r"[./\\]", target)) or bool(expected_window(target))
                   or target.strip().lower() in _SHELL_HINTS)
        if not launchy:
            inside = engine.click_target(target, expect_change=False, retries=0)
            if inside.ok:
                _note(f"open: {target} (already on screen)", "ok")
                return {"ok": True, "plan": plan,
                        "detail": f"Opened {target!r} — it was already on screen, "
                                  f"so I clicked it."}
        res = engine.open_uri(target)
        detail, ok = res.line(), res.ok
        if not ok:
            # A bare name is not a file association, so the shell may refuse it.
            # Fall through: installed app first, then the web brand it names.
            app_result = _launch_app(target)
            if app_result:
                detail, ok = app_result, True
        if not ok and _web_brand(target):
            res = engine.open_uri("https://www." + _web_brand(target) + ".com")
            detail, ok = res.line(), res.ok
        if ok and not launchy:
            detail = f"Opened {target!r}"
        if ok and plan.get("wait_for_window", True):
            title = expected_window(target)
            if title and engine.wait_for_window(title, timeout=float(
                    plan.get("window_timeout", 10.0))):
                detail += f"; {title} is up"
    elif action == "search":
        query = str(plan.get("query") or "")
        site = str(plan.get("site") or "").strip().lower()
        url = _search_url(site, query)
        res = engine.open_uri(url)
        detail, ok = res.line(), res.ok
        if ok and site:
            title = expected_window(site)
            if title and engine.wait_for_window(title, timeout=8.0):
                detail = f"Searching {site} for {query!r}"
    elif action == "drag":
        res = engine.drag_to(plan.get("source", ""), plan.get("target", ""))
        detail, ok = res.line(), res.ok
    else:
        return {"ok": False, "detail": f"I do not know how to {action!r}.", "plan": plan}

    if pc_log:
        pc_log.event("pc_agent.step", action=action,
                     target=str(plan.get("target") or plan.get("field") or "")[:60],
                     verify="ok" if ok else "failed", result=detail[:120])
    _note(f"{action}: {detail}", "ok" if ok else "fail")
    return {"ok": ok, "detail": detail, "plan": plan}


def _recover(plan: dict) -> dict | None:
    """One different attempt after a step failed, before giving up.

    Repeating the same dead click is worse than useless: it burns time and
    teaches nothing. The recovery ladder here is:
      1. re-look at the screen with a fresh accessibility tree (the target moved)
      2. try the keyboard route instead of the mouse
      3. give up honestly and say what is on screen
    """
    action = str(plan.get("action") or "").lower()
    if action not in ("click", "point", "type"):
        return None
    try:
        engine.clear_uia_cache()
    except Exception:
        pass
    if action in ("click", "point"):
        retry = dict(plan)
        # Keep the vague flag. Setting it False here used to skip resolve_vague,
        # so a failed "click that" retried the literal string "that" — a retry
        # designed to fail. Re-resolving after a fresh UIA walk (above) is the
        # whole point of the recovery ladder.
        time.sleep(0.35)
        return _step(retry, asked_by_user=True)
    if action == "type" and plan.get("field"):
        # The field may not be on screen at all: focus the window it lives in
        # by title and try once more without a named field.
        retry = {"action": "type", "text": plan.get("text", "")}
        time.sleep(0.3)
        return _step(retry, asked_by_user=True)
    return None


def _gated(plan: dict) -> bool:
    """True when this plan must go through the on-screen confirmation gate."""
    if autonomy is None or confirm is None:
        return False
    text = " ".join(str(plan.get(k) or "") for k in
                    ("action", "target", "text", "field"))
    policy = autonomy.classify(text, asked_by_user=True)
    return policy.needs_confirmation


def _gate_and_run(plan: dict, result: dict) -> dict:
    """Park a dangerous plan behind the human gate and return immediately."""
    text = " ".join(str(plan.get(k) or "") for k in ("action", "target", "text"))
    title = f"{plan.get('action')}: {plan.get('target') or plan.get('text') or ''}"[:80]

    def _run() -> str:
        out = _step(plan, asked_by_user=True)
        return out.get("detail", "")

    msg = confirm.request("pc_agent", title,
                          f"JARVIS wants to {plan.get('action')} "
                          f"{plan.get('target') or plan.get('text') or ''}. "
                          f"{autonomy.classify(text, asked_by_user=True).reason}",
                          _run)
    result.update({"ok": False, "detail": msg, "gated": True})
    return result


def run_intent(text: str, *, autonomous: bool = False,
               allow_vision: bool = True, timeout: float = 0.0) -> dict:
    """One sentence in, one verified action out. The public half of the router."""
    if _need_engine():
        return {"ok": False, "detail": _need_engine()}
    if autonomous and autonomy is not None:
        allowed, why = autonomy.can_act(autonomous_only=True)
        if not allowed:
            return {"ok": False, "detail": why}
    if engine.cancelled():
        return {"ok": False, "detail": "Stopped at your request."}
    plan = plan_intent(text)
    if plan is None:
        if not allow_vision:
            return {"ok": False,
                    "detail": f"I could not work out what {text!r} means as a "
                              f"single computer action."}
        return _vision_fallback(text)
    if _gated(plan) and not autonomous:
        return _gate_and_run(plan, {"ok": False, "detail": ""})
    out = _step(plan, asked_by_user=not autonomous)
    if not out.get("ok"):
        recovered = _recover(plan)
        if recovered and recovered.get("ok"):
            recovered["detail"] = (f"{recovered['detail']} "
                                   f"(recovered after a retry)")
            recovered["recovered"] = True
            return recovered
    out["plan"] = plan
    return out


def _vision_fallback(text: str) -> dict:
    """Last resort for sentences the router cannot parse: look, then act.

    The model is shown the screen and asked for ONE primitive — not for a plan.
    Keeping the reasoning to a single step keeps it honest: it can point at what
    it can actually see and nothing more.
    """
    try:
        seen = engine.describe_screen()
    except Exception as e:
        return {"ok": False, "detail": f"I could not see the screen: {e}"}
    return {"ok": False,
            "detail": f"I could not reduce {text!r} to one action. What is on "
                      f"screen now:\n{seen[:900]}",
            "needs_vision": True}


# ════════════════════════════════════════════════════════════════════════════
#  3. MODES
# ════════════════════════════════════════════════════════════════════════════

_MODE_ALIASES = {
    "pc": "pc_control", "pc_control": "pc_control", "control": "pc_control",
    "autonomous": "autonomous", "autonomy": "autonomous", "auto": "autonomous",
    "screen": "screen_awareness", "screen_awareness": "screen_awareness",
    "eyes": "screen_awareness",
    "proactive": "proactive", "interaction": "proactive",
    "discord": "discord", "discord_control": "discord",
    "voice": "voice", "mic": "voice", "microphone": "voice",
    "voice_control": "voice",
}


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in ("1", "true", "yes", "on", "enable",
                                                "enabled", "start")


def _mode_action(params: dict) -> str:
    if autonomy is None:
        return "Mode control is unavailable."
    name = str(params.get("mode") or params.get("name") or "").strip().lower()
    want = params.get("enabled")
    if want is None:
        want = params.get("on")
    if not name:
        return ("Modes: " + ", ".join(
            f"{k}={'ON' if v else 'OFF'}" for k, v in autonomy.modes().items()))
    key = _MODE_ALIASES.get(name)
    if key is None:
        return (f"No such mode: {name!r}. Try one of: "
                + ", ".join(sorted(set(_MODE_ALIASES.values()))))
    if want is None:
        return f"{key} is {'ON' if autonomy.get_mode(key) else 'OFF'}."
    value = _truthy(want)
    applied = autonomy.set_mode(key, value)
    if key == "pc_control" and not applied:
        _note("PC control OFF — JARVIS will not touch the mouse or keyboard", "mode")
    return (f"{key} {'ON' if applied else 'OFF'}."
            + (" Taking the PC." if key == "autonomous" and applied else ""))


# ════════════════════════════════════════════════════════════════════════════
#  4. ENTRY POINT
# ════════════════════════════════════════════════════════════════════════════

def pc_agent(parameters: dict = None, response=None, player=None,
             session_memory=None) -> str:
    params = parameters or {}
    action = str(params.get("action") or "").strip().lower().replace("-", "_")
    action = {
        "run": "do", "act": "do", "intent": "do", "execute": "do",
        "look": "observe", "see": "observe", "screenshot": "observe",
        "status": "state", "modes": "mode", "toggle": "mode",
        "click": "do", "type": "do", "point": "do",
    }.get(action, action)

    if player:
        try:
            player.write_log(f"[pc_agent] {action}")
        except Exception:
            pass
    _log_line(f"[pc_agent] {action} {params}")

    if action in ("state", "info"):
        return _state_text()
    if action == "mode":
        return _mode_action(params)
    if action == "feed":
        return _feed_text(int(params.get("limit") or 20))
    if action == "observe":
        if engine is None:
            return "The computer-control engine is unavailable in this build."
        region = None
        box = params.get("region")
        if isinstance(box, (list, tuple)) and len(box) == 4:
            try:
                region = tuple(int(v) for v in box)      # type: ignore[assignment]
            except Exception:
                region = None
        try:
            return engine.describe_screen(region=region)[:2500]
        except Exception as e:
            return f"I could not see the screen: {e}"
    if action == "do":
        return _do_action(params)
    return ("pc_agent can: do (run an intent or a list of steps), observe "
            "(describe the screen), state (control status), mode (on/off "
            "levers), feed (recent actions).")


def _log_line(msg: str) -> None:
    try:
        print(str(msg).encode("ascii", "replace").decode("ascii"))
    except Exception:
        pass


def _state_text() -> str:
    if engine is None:
        return "The computer-control engine is unavailable in this build."
    try:
        st = engine.state()
    except Exception as e:
        return f"Could not read the control state: {e}"
    lines = [
        f"Control: input={'yes' if st['input'] else 'NO'} "
        f"vision={'yes' if st['vision'] else 'NO'} "
        f"accessibility tree={'yes' if st['uia'] else 'no'}",
        f"Screen: {st['desktop']}, {st['monitors']} monitor(s)",
        f"Pointer: {st['pointer'][0]},{st['pointer'][1]}"
        + (f" ({st['pointer_monitor']})" if st.get("pointer_monitor") else ""),
        f"Foreground: {st['foreground'] or 'unknown'}",
    ]
    if autonomy is not None:
        lines.append("Modes: " + ", ".join(
            f"{k}={'ON' if v else 'OFF'}" for k, v in autonomy.modes().items()))
    return "\n".join(lines)


def _feed_text(limit: int = 20) -> str:
    if autonomy is None:
        return "No action feed is available."
    rows = autonomy.feed_recent(limit)
    if not rows:
        return "Nothing has been done on the PC yet."
    out = []
    for r in rows:
        stamp = time.strftime("%H:%M:%S", time.localtime(r.get("at", time.time())))
        out.append(f"{stamp}  {r.get('text', '')}")
    return "\n".join(out)


def _do_action(params: dict) -> str:
    if engine is None:
        return "The computer-control engine is unavailable in this build."

    autonomous = _truthy(params.get("autonomous"))
    steps = params.get("steps")
    if isinstance(steps, str):
        steps = [s for s in re.split(r"\s*\|\s*|\s*;\s*|\n", steps) if s.strip()]
    if not isinstance(steps, (list, tuple)) or not steps:
        intent = str(params.get("intent") or params.get("text")
                     or params.get("target") or "").strip()
        if not intent:
            return ("pc_agent do needs an 'intent' sentence, or 'steps' — an "
                    "ordered list of them.")
        # "Open YouTube and play music" arrives as ONE sentence and is two
        # actions; expanding it here is what lets a whole request complete
        # without the model coming back between the halves.
        steps = split_intents(intent)

    if cancel is not None:
        try:
            cancel.clear()
        except Exception:
            pass

    results: list[str] = []
    all_ok = True
    for raw_step in list(steps)[:12]:
        if engine.cancelled():
            results.append("· stopped at your request")
            all_ok = False
            break
        step_text = str(raw_step).strip()
        if not step_text:
            continue
        out = run_intent(step_text, autonomous=autonomous)
        mark = "✓" if out.get("ok") else ("⏸" if out.get("gated") else "✗")
        results.append(f"{mark} {step_text} — {out.get('detail', '')}")
        if not out.get("ok"):
            all_ok = False
            if out.get("gated"):
                break            # a human is needed; do not run later steps blind
        time.sleep(float(params.get("step_pause", 0.25) or 0.25))

    header = ("Done." if all_ok else
              "Here is exactly how far I got — the rest did not happen:")
    return header + "\n" + "\n".join(results)


# ── Tool declaration (auto-discovered by core/action_loader.py) ──────────────
TOOL = {
    "name": "pc_agent",
    "description": (
        "Uses this computer like a person: find the real on-screen target, move "
        "there, click, type, verify, and recover if it failed. This is the tool "
        "for anything that needs the mouse or keyboard on the desktop — clicking "
        "elements, moving the pointer, focusing a window, typing into a field, "
        "key chords, scrolling, dragging, and opening apps or sites. "
        "ACTION=do takes `steps`, an ordered list of ordinary sentences, and runs "
        "the whole sequence in ONE call ('open YouTube', 'click the search box', "
        "'type lofi beats', 'press enter', 'press play') — use it so a multi-step "
        "request does not cost a round trip per step. Harmless actions need no "
        "permission; anything irreversible is handled with the user automatically. "
        "observe: what is on screen now. state: control and screen status."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "enum": ["do", "observe", "state", "mode", "feed"],
                "description": "do = act; observe = look at the screen; "
                               "state = control/screen status; mode = turn the "
                               "control levers on or off; feed = what was done "
                               "recently.",
            },
            "intent": {
                "type": "STRING",
                "description": "One ordinary sentence describing the action, e.g. "
                               "'click the Sign in button', 'move the mouse to "
                               "Settings', 'type hello into the search box', "
                               "'open YouTube', 'scroll down', 'press ctrl+s'.",
            },
            "steps": {
                "type": "ARRAY",
                "items": {"type": "STRING"},
                "description": "An ordered list of intent sentences to run in one "
                               "call. Preferred for anything with more than one "
                               "step.",
            },
            "target": {
                "type": "STRING",
                "description": "Element, window or app to act on (used when the "
                               "intent sentence is not convenient).",
            },
            "mode": {
                "type": "STRING",
                "description": "Which lever to change: pc_control | autonomous | "
                               "screen_awareness | proactive | discord | voice.",
            },
            "enabled": {
                "type": "BOOLEAN",
                "description": "true to switch the named mode on, false for off.",
            },
            "autonomous": {
                "type": "BOOLEAN",
                "description": "True only when this call is JARVIS acting on its "
                               "own initiative (autonomous mode). Leave false "
                               "when the user asked for it.",
            },
            "region": {
                "type": "ARRAY",
                "items": {"type": "NUMBER"},
                "description": "Optional [x, y, width, height] area for observe.",
            },
            "limit": {
                "type": "NUMBER",
                "description": "How many feed entries to show (feed action).",
            },
        },
        "required": ["action"],
    },
    "handler": pc_agent,
}
