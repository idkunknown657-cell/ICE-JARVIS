"""
core/autonomy.py — what JARVIS is allowed to do on its own, and what it must ask about.

THE PROBLEM
    "May I click that?" for every harmless click makes the assistant useless; no
    gate at all makes it dangerous. The old code had both failures at once: a
    confirmation banner for ending a process it had been told to end, and nothing
    whatsoever in front of "delete this folder" from an autonomous loop. The
    decision was spread across a dozen call sites and every one of them decided
    differently.

THE MODEL
    One classification function answers the only question that matters:

        classify(intent, asked_by_user=…) -> Policy(free, risk, reason)

    and it answers it in the vocabulary the brief uses:
      * FREE           — browsing, searching, watching, listening, reading,
                         scrolling, moving the pointer, switching tabs, opening
                         apps, harmless file organisation. No question, ever.
      * ASK ONLY IF    — things that leave this machine and touch other people
                         (messages, posts, comments), installs, downloads that
                         execute. Free when the user asked for them in this turn;
                         never initiated by autonomy alone.
      * ALWAYS ASK     — money, permanent deletion, shutdown/restart, security and
                         account changes, anything irreversibly destructive. Asked
                         even when the user explicitly requested it, because the
                         cost of a mistake here is not a click.

    `INTENT_RULES` is a table, so the policy is inspectable, testable and
    reviewable in one place instead of implied by whichever gate a given action
    file happened to remember to call.

THE MODES
    Six levers, each read live so a change on the HUD takes effect on the next
    action rather than the next restart:
        pc_control      JARVIS may touch the mouse and keyboard at all
        autonomous      it may act without being asked, while idle
        screen_awareness it may look at the screen
        proactive       it may speak first
        discord_control it may use Discord
        voice_control   it may use the microphone (the app's mute switch)

THE IDLE SIDE
    Autonomous mode is not "move the mouse forever". `idle_agenda()` builds the
    rules for a *reasoned* contribution and `IdleGovernor` decides when a check
    is even worth making: event-driven (a real screen change, a finished task) or
    on a slow timer, never a tight poll. Everything here is stdlib-only and
    cannot burn a core, because the whole point is that it runs all day.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

# ════════════════════════════════════════════════════════════════════════════
#  1. THE POLICY TABLE
# ════════════════════════════════════════════════════════════════════════════

FREE = "free"                       # do it, say nothing
ASK_IF_UNASKED = "ask-if-unasked"   # fine when they asked; never self-initiated
ALWAYS_ASK = "always-ask"           # needs a human on the on-screen gate


@dataclass(frozen=True)
class Rule:
    """One row of the policy table. `words` are matched as whole words against
    the normalised intent text; `category` is what the user is told the risk is."""
    tier: str
    category: str
    words: tuple[str, ...]
    reason: str


# Order matters: the first rule that matches wins, so the sharpest (always-ask)
# rows come first and broad free ones last.
INTENT_RULES: tuple[Rule, ...] = (
    # ── never without a human, even if asked ────────────────────────────────
    # Deliberately narrow wording: "check out this video" and "in order to" are
    # ordinary English, and a policy table that gates them teaches the user to
    # ignore the gate entirely.
    Rule(ALWAYS_ASK, "money",
         ("buy", "buy it", "purchasing", "purchase", "make a purchase",
          "checkout", "checkout page", "pay", "make a payment", "payment",
          "subscribe", "subscription", "upgrade to pro", "top up", "top-up",
          "transfer the money", "send money", "deposit", "withdraw", "place a bet",
          "place an order", "place my order", "place the order", "order it now",
          "place a trade", "add to cart", "buy now"),
         "money is involved, so the user must see the exact amount first"),
    Rule(ALWAYS_ASK, "destructive",
         ("delete", "remove permanently", "empty recycle bin", "empty the recycle bin",
          "empty trash", "empty the trash", "recycle bin", "format", "wipe",
          "erase", "uninstall", "shred", "overwrite", "reset factory",
          "clear all", "delete all", "purge"),
         "this removes something that cannot be brought back"),
    Rule(ALWAYS_ASK, "power",
         ("shutdown", "shut down", "power off", "restart", "reboot", "sign out",
          "log out", "sleep the pc", "hibernate", "close all apps"),
         "the machine goes down and unsaved work goes with it"),
    Rule(ALWAYS_ASK, "security",
         ("password", "passwd", "2fa", "two-factor", "authenticator", "recovery key",
          "api key", "access token", "firewall", "antivirus", "defender",
          "permissions", "admin rights", "uac", "policy", "certificate",
          "credit card", "card number", "cvv", "bank", "netbanking", "upi",
          "crypto wallet", "seed phrase", "private key"),
         "this touches credentials, identity or money — the user must drive it"),
    Rule(ALWAYS_ASK, "install",
         ("install", "installer", "setup exe", "run this file", "run the downloaded",
          "execute the download", "enable macros", "allow the add-in", "registry edit",
          "regedit", "disable the antivirus", "add to firewall"),
         "running new software can change this machine permanently"),

    # ── fine when the user asked for it in this turn; never self-started ────
    Rule(ASK_IF_UNASKED, "outward message",
         ("send", "send this", "message", "dm", "text them", "email", "reply",
          "post", "comment", "tweet", "publish", "share", "invite", "accept the call",
          "call", "ring", "forward"),
         "it leaves this machine and another person receives it"),
    Rule(ASK_IF_UNASKED, "account change",
         ("profile picture", "change the name on", "nickname", "account settings",
          "unfollow", "block", "report the user", "leave the server",
          "leave the group", "join the server", "add friend"),
         "it changes an account other people can see"),

    # ── free: the whole point of an autonomous assistant ────────────────────
    Rule(FREE, "browsing",
         ("open", "browse", "search", "look up", "google", "visit", "go to",
          "navigate", "read", "scroll", "watch", "play", "listen", "stream",
          "new tab", "switch tab", "close tab", "back", "forward", "reload",
          "bookmark", "screenshot", "zoom", "move the mouse", "move the pointer",
          "hover", "click", "type", "press", "hover over", "select", "drag",
          "copy", "paste", "maximise", "maximize", "minimise", "minimize",
          "organise", "organize", "sort files", "rename", "move the window",
          "volume", "brightness", "next track", "pause", "mute the music",
          "focus the window", "wayback", "wikipedia", "news", "weather",
          "translate", "calculate", "note this down", "take a note"),
         "an ordinary, reversible thing anyone does with a computer"),
)

_PROFILE_ONLY = ("webcam", "camera", "microphone", "record my screen", "screen record")
_RULE_BY_CATEGORY = {r.category: r for r in INTENT_RULES}


@dataclass(frozen=True)
class Policy:
    tier: str = FREE
    category: str = "general"
    reason: str = ""
    matched: str = ""

    @property
    def free(self) -> bool:
        return self.tier == FREE

    @property
    def needs_confirmation(self) -> bool:
        return self.tier == ALWAYS_ASK

    def describe(self) -> str:
        if self.free:
            return f"allowed ({self.category})"
        return f"{self.tier}: {self.reason}"


def _norm(text: str) -> str:
    out = []
    for ch in str(text or "").lower():
        out.append(ch if (ch.isalnum() or ch in " -") else " ")
    return " ".join("".join(out).split())


def _has_word(text: str, word: str) -> bool:
    """Whole-word (or whole-phrase) containment. Pure.

    Substring matching would gate "check out this video" as a purchase and
    "order" inside "in order to" as a transaction; word edges avoid both.
    """
    if " " in word or "-" in word:
        return word in text
    return f" {word} " in f" {text} "


def classify(intent: str, *, asked_by_user: bool = False,
             profile: bool = False) -> Policy:
    """Decide how a described action must be treated. Pure and total.

    `asked_by_user` is the crucial input: the *same sentence* is free when the
    user just said it and forbidden when the autonomous loop invented it, which
    is exactly the distinction the old confirmation gate could not express.
    """
    text = _norm(intent)
    if not text:
        return Policy(FREE, "general", "nothing to judge", "")
    for rule in INTENT_RULES:
        for word in rule.words:
            if _has_word(text, word):
                if rule.tier == FREE:
                    return Policy(FREE, rule.category, rule.reason, word)
                if rule.tier == ASK_IF_UNASKED and asked_by_user:
                    # The user asked for it, and it is not in the always-ask set:
                    # do it, without a second question.
                    return Policy(FREE, rule.category,
                                  "the user asked for this directly", word)
                return Policy(rule.tier, rule.category, rule.reason, word)
    if profile and any(_has_word(text, w) for w in _PROFILE_ONLY):
        return Policy(ASK_IF_UNASKED, "privacy", "the camera or microphone is "
                      "involved", "privacy")
    return Policy(FREE, "general", "an ordinary, reversible thing", "")


def gate_reason(intent: str, *, asked_by_user: bool = False) -> str:
    """'' when the action may proceed; otherwise the sentence explaining why not."""
    p = classify(intent, asked_by_user=asked_by_user)
    if p.free:
        return ""
    return (f"{p.category.replace('_', ' ')} — {p.reason}. The user must confirm "
            f"this on screen before it happens.")


# ════════════════════════════════════════════════════════════════════════════
#  2. MODES
# ════════════════════════════════════════════════════════════════════════════

# name -> (config key, default, human label)
MODES: dict[str, tuple[str, bool, str]] = {
    "pc_control":       ("pc_control", True, "PC control"),
    "autonomous":       ("autonomous", False, "Autonomous mode"),
    "screen_awareness": ("screen_awareness", False, "Screen awareness"),
    "proactive":        ("proactive_enabled", True, "Proactive interaction"),
    "discord":          ("discord_control", True, "Discord control"),
    "voice":            ("voice_control", True, "Voice control"),
}

# Modes the user's own words can switch on. "Take over my PC" is the brief's
# phrasing and has to work as spoken, without a settings trip.
_SPOKEN_ON = {
    "autonomous": ("take over my pc", "take over the pc", "you take over",
                   "you can control my pc", "control my pc", "do whatever you want",
                   "use my pc for a while", "use my computer for a while",
                   "autonomous mode", "take over", "take the wheel",
                   "keep using my pc", "you drive"),
}
_SPOKEN_OFF = {
    "autonomous": ("stop taking over", "stop controlling", "autonomous off",
                   "stop autonomy", "give me back control", "hands off",
                   "stop using my pc"),
    "pc_control": ("stop controlling my pc", "no mouse control", "pc control off"),
}


def _cfg():
    try:
        from memory import config_manager
        return config_manager
    except Exception:
        return None


def get_mode(name: str) -> bool:
    """Read one lever live. Unknown names read False rather than raising."""
    key_default = MODES.get(str(name or "").strip().lower())
    if key_default is None:
        return False
    key, default, _label = key_default
    cfg = _cfg()
    if cfg is None:
        return default
    try:
        data = cfg.load_api_keys()
        return bool(data.get(key, default))
    except Exception:
        return default


def set_mode(name: str, value: bool) -> bool:
    """Flip one lever. Returns the value actually stored."""
    entry = MODES.get(str(name or "").strip().lower())
    if entry is None:
        return False
    key = entry[0]
    cfg = _cfg()
    if cfg is None:
        return False
    try:
        data = cfg.load_api_keys()
        data[key] = bool(value)
        cfg.ensure_config_dir()
        import json
        cfg.CONFIG_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
        feed("mode", f"{entry[2]} → {'ON' if value else 'OFF'}")
        if not value and name in ("pc_control", "autonomous"):
            # Switching control off must stop it NOW, not at the end of the
            # current step. Every control loop polls core/cancel, so raising the
            # stop flag here is what makes the switch feel like a brake rather
            # than a setting.
            try:
                from core import cancel
                cancel.request()
            except Exception:
                pass
            feed("info", "stopping any action in progress")
        return bool(value)
    except Exception:
        return False


def modes() -> dict[str, bool]:
    return {name: get_mode(name) for name in MODES}


def spoken_mode_change(text: str) -> Optional[tuple[str, bool]]:
    """Recognise a spoken mode request in the user's own words.

    Returns (mode_name, value) or None. Pure — the caller decides whether to act
    on it, so this can be unit-tested without touching config.
    """
    t = _norm(text)
    if not t:
        return None
    for mode, phrases in _SPOKEN_OFF.items():
        if any(p in t for p in phrases):
            return mode, False
    for mode, phrases in _SPOKEN_ON.items():
        if any(p in t for p in phrases):
            return mode, True
    return None


def can_act(*, autonomous_only: bool = False) -> tuple[bool, str]:
    """(may JARVIS touch this machine right now, why not)."""
    if not get_mode("pc_control"):
        return False, "PC control is switched off — the user turned it off in settings."
    if autonomous_only and not get_mode("autonomous"):
        return False, "Autonomous mode is off — JARVIS does not act unasked."
    if get_mode("autonomous") and get_mode("pc_control"):
        return True, ""
    return True, ""


# ════════════════════════════════════════════════════════════════════════════
#  3. THE ACTION FEED  (what the HUD shows scrolling past)
# ════════════════════════════════════════════════════════════════════════════

_FEED: list[dict] = []
_FEED_LOCK = threading.Lock()
_FEED_CAP = 120
_FEED_CB: list[Callable[[dict], None]] = []


def bind_feed(cb: Callable[[dict], None]) -> None:
    """Register a sink for feed entries (the HUD pushes them to the browser)."""
    if callable(cb) and cb not in _FEED_CB:
        _FEED_CB.append(cb)


def feed(kind: str, text: str, **extra) -> dict:
    """Record one line of what JARVIS is doing. Never raises, never blocks."""
    entry = {"kind": str(kind or "info"), "text": str(text or "")[:200],
             "at": time.time()}
    if extra:
        entry.update({k: v for k, v in extra.items() if v is not None})
    with _FEED_LOCK:
        _FEED.append(entry)
        if len(_FEED) > _FEED_CAP:
            del _FEED[:-_FEED_CAP]
    for cb in list(_FEED_CB):
        try:
            cb(entry)
        except Exception:
            pass
    return entry


def feed_recent(n: int = 25) -> list[dict]:
    with _FEED_LOCK:
        return [dict(e) for e in _FEED[-max(1, int(n)):]]


def feed_clear() -> None:
    with _FEED_LOCK:
        _FEED.clear()


# ════════════════════════════════════════════════════════════════════════════
#  4. THE IDLE GOVERNOR — autonomy that stays out of the way
# ════════════════════════════════════════════════════════════════════════════

@dataclass
class IdleGovernor:
    """Decides when autonomous initiative is welcome, and builds its rules.

    Deliberately boring: no threads, no timers of its own. main.py already runs
    a slow loop and already knows when the user last spoke and what changed on
    screen, so this just holds the thresholds and the reasoning.

    Settings (all seconds):
      grace          user must be quiet this long before initiative is welcome
      cooldown       minimum gap between two autonomous contributions
      busy_suppress  keep quiet for this long after any user activity
    """

    grace: float = 120.0
    cooldown: float = 240.0
    busy_suppress: float = 45.0
    _last_offer: float = field(default=0.0, repr=False)
    _last_reason: str = field(default="", repr=False)

    def due(self, *, idle: float, now: Optional[float] = None) -> bool:
        """True when a contribution is worth considering at all."""
        now = time.monotonic() if now is None else now
        if not (get_mode("pc_control") and get_mode("autonomous")):
            return False
        if idle < self.busy_suppress or idle < self.grace:
            return False
        return (now - self._last_offer) >= self.cooldown

    def mark(self, reason: str = "", now: Optional[float] = None) -> None:
        self._last_offer = time.monotonic() if now is None else now
        self._last_reason = str(reason or "")

    @property
    def last_reason(self) -> str:
        return self._last_reason


def idle_agenda(*, activity: str = "", idle_seconds: float = 0.0,
                recent: str = "", autonomous: bool = True) -> str:
    """The rules handed to the model for one autonomous moment.

    Written as a priority ladder, not a task list: the user's unfinished work
    first, then what the conversation left hanging, then something genuinely
    useful, and being quiet at the bottom rather than the top. An assistant that
    fills silence with movement is worse than one that does nothing.
    """
    idle_min = max(0, int(idle_seconds // 60))
    lines = [
        "[AUTONOMOUS] The user has handed over the PC and is not talking to you "
        f"right now (quiet for about {idle_min} minutes).",
    ]
    if activity:
        lines.append(f"What they were last doing: {activity}")
    if recent:
        lines.append(f"What the conversation was about: {recent[:400]}")
    lines += [
        "",
        "You may act on this machine yourself, without asking, for anything "
        "harmless: opening things, searching, reading, playing music or a video, "
        "organising, looking around, preparing something useful. Do NOT ask "
        "permission for those — asking defeats the point of being handed over.",
        "",
        "Priority, top first:",
        "1. Finish or advance the task the user was already on.",
        "2. Act on something the conversation left open.",
        "3. Prepare something concretely useful for them (a search worth reading, "
        "a playlist for the mood, the next page of what they were reading).",
        "4. Something genuinely interesting or funny, if the mood fits.",
        "If none of these applies, do nothing at all. Movement is not liveliness; "
        "an idle screen is a perfectly good outcome.",
        "",
        "Never, without being asked: send a message or email to anyone, post or "
        "comment publicly, buy anything, delete anything, change an account, "
        "password or security setting, install or run new software, restart or "
        "shut down the machine. Those wait for a human, always.",
        "",
        "Do not narrate your actions as a list. Do one useful thing at most, then "
        "say one short sentence about it — or, if you are only observing, stay "
        "silent.",
    ]
    if not autonomous:
        lines.append("")
        lines.append("(Autonomous mode is off: you may suggest things and describe "
                     "what you see, but you must not act on the machine.)")
    return "\n".join(lines)


def boundary_summary() -> str:
    """One prompt-ready line naming what needs a human. Used in the system prompt."""
    cats: list[str] = []
    for rule in INTENT_RULES:
        if rule.tier == ALWAYS_ASK:
            cats.append(rule.category)
    return ("Needs the user's explicit go-ahead, every time: "
            + ", ".join(cats) + ".")
