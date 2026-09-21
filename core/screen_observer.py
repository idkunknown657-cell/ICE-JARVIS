"""
screen_observer.py — lightweight foreground-window & activity observer.

What and why
------------
Gives JARVIS a continuous but *cheap* picture of what the user is doing, without
screenshots: it samples the active window title + process name every few seconds
and classifies the activity (BROWSING / CODING / GAMING / WATCHING_VIDEO / ...).
No pixels move, no API calls happen. The snapshots feed the proactive engine so
Gemini can decide whether a check-in is actually welcome, instead of firing on a
pure timer.

Design rules
------------
- No PyQt, no heavy imports at module level (only stdlib; psutil is optional and
  already a project dependency). The rest of core imports this directly.
- Privacy is respected at the source: if the caller disables awareness, sampling
  returns an empty snapshot and a worker thread sits idle.
- Windows uses user32 (foreground window + title, no new dependency). macOS uses
  AppKit when importable; Linux falls back to process name only. On any failure
  the module degrades to '' rather than raising.
"""
from __future__ import annotations

import platform
import time
import threading
from datetime import datetime
from typing import Callable

try:
    import psutil
    _PSUTIL = True
except Exception:          # pragma: no cover — observer must never die on missing psutil
    psutil = None
    _PSUTIL = False

_OS = platform.system()          # "Windows" | "Darwin" | "Linux"


def _windows_active_window() -> dict:
    """Foreground window title + owning process via ctypes (user32 only)."""
    import ctypes
    from ctypes import wintypes

    user32   = ctypes.windll.user32
    user32.GetForegroundWindow.restype = wintypes.HWND
    hwnd     = user32.GetForegroundWindow()
    pid      = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    length   = user32.GetWindowTextLengthW(hwnd)
    buf      = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    title    = buf.value or ""
    proc     = ""
    if _PSUTIL:
        try:
            p = psutil.Process(pid.value)
            proc = (p.name() or "").split(".exe")[0] if p.name() else ""
        except Exception:
            proc = ""
    return {"title": (title or "").strip(), "app": (proc or "").lower(), "pid": pid.value}


def _macos_active_window() -> dict:
    """Frontmost app name (+ window title when AppKit is importable)."""
    app, title = "", ""
    try:
        from AppKit import NSWorkspace
        ws = NSWorkspace.sharedWorkspace()
        app = (ws.frontmostApplication().localizedName() or "").lower()
    except Exception:
        pass
    try:
        title = get_active_window_title_fallback()
    except Exception:
        title = ""
    return {"title": (title or "").strip(), "app": (app or "").strip().lower(), "pid": 0}


def get_active_window_title_fallback() -> str:      # pragma: no cover
    """Best-effort title on non-Windows where no cheap API exists."""
    return ""


def _linux_active_window() -> dict:
    """Process name of the most recently active app (title unavailable cheaply)."""
    app = ""
    if _PSUTIL:
        try:
            procs = psutil.process_iter(["name", "create_time"])
            procs = sorted(procs, key=lambda p: p.info.get("create_time") or 0, reverse=True)
            app = (procs[0].info.get("name") or "").split(".exe")[0].lower()
        except Exception:
            app = ""
    return {"title": "", "app": app, "pid": 0}


def _active_window() -> dict:
    if _OS == "Windows":
        return _windows_active_window()
    if _OS == "Darwin":
        return _macos_active_window()
    return _linux_active_window()


# ── Activity classification ───────────────────────────────────────────────────
# keyword → category. Process name and window title are matched together, so
# "chrome-google docs" and "notepad++ - server.py" both land correctly.

_CATEGORY_RULES: list[tuple[tuple[str, ...], str]] = [
    (("chrome", "msedge", "edge", "firefox", "brave", "opera", "vivaldi",
      "chromium", "yandex"),                                    "BROWSING"),
    (("code", "cursor", "vscode", "sublime", "pycharm", "intellij", "idea64",
      "webstorm", "goland", "clion", "rider", "androidstudio", "android studio",
      "xed", "vim", "nvim", "neovim", "emacs", "nano", "notepad++", "zsh",
      "bash", "terminal", "windows terminal", "wt", "cmd", "powershell",
      "konsole", "gnome-terminal", "iTerm"),                     "CODING"),
    (("steam", "epicgameslauncher", "valorant", "league", "csgo", "cs2",
      "dota", "minecraft", "rocketleague", "fortnite", "gta", "aoe",
      "roblox", "pubg", "xbox", "playnite"),                     "GAMING"),
    (("youtube", "netflix", "prime video", "disney", "hotstar", "hulu",
      "vlc", "mpv", "plex", "kodi"),                             "WATCHING_VIDEO"),
    (("whatsapp", "telegram", "discord", "slack", "messenger", "signal",
      "skype", "outlook", "thunderbird", "mail", "gmail"),       "COMMUNICATION"),
    (("explorer", "finder", "nautilus", "dolphin", "thunar", "files",
      "total commander", "winrar", "7z", "onedrive", "dropbox"), "FILE_MANAGEMENT"),
    (("word", "winword", "powerpoint", "excel", "pages", "keynote",
      "numbers", "notion", "obsidian", "typora", "evernote",
      "canva", "slides", "docs"),                                "WRITING"),
    (("spotify", "itunes", "apple music", "clementine",
      "foobar2000", "winamp"),                                   "MUSIC"),
    (("figma", "photoshop", "illustrator", "gimp", "blender",
      "premiere", "after effects", "krita", "paint", "mspaint",
      "procreate", "clip studio"),                               "DESIGN"),
]


def classify_activity(snapshot: dict) -> tuple[str, str]:
    """Return (category, sub_activity) for a snapshot dict with 'app'/'title'.

    sub_activity is the matched app name (or UNKNOWN). Matching is case-insensitive
    and checks app name then window title.
    """
    app   = str(snapshot.get("app", "") or "").lower()
    title = str(snapshot.get("title", "") or "").lower()
    full  = f"{app} {title}"

    for keywords, category in _CATEGORY_RULES:
        for kw in keywords:
            if kw in full:
                return category, app or kw
    return "UNKNOWN", app or "unknown"


# ── Observer ─────────────────────────────────────────────────────────────────

_ACTIVITY_IDLE_SECS = 0.0     # placeholder; computed from samples


class ScreenObserver:
    """Background sampler of the active window + short event history.

    A single worker thread polls every `interval` seconds. Snapshots are kept in
    `.current`; significant changes (different app or a different window title)
    are appended to `events` (FIFO, max `max_events`) and passed to `on_change`
    if provided. Everything is lock-protected so the async loop can read freely.
    """

    def __init__(
        self,
        interval: float = 5.0,
        max_events: int = 6,
        on_change: Callable[[dict], None] | None = None,
        enabled: bool = True,
    ):
        self.interval    = max(0.5, float(interval))
        self.max_events  = max(1, int(max_events))
        self.on_change   = on_change
        self._enabled    = bool(enabled)
        self._lock       = threading.Lock()
        self._stop       = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_key   = ("", "")
        self._last_change_ts = 0.0
        self.current: dict = self._empty()
        self.events: list[dict] = []
        # Rolling activity timeline (in-memory only, never persisted): a span per
        # foreground-app session so JARVIS can say what you have been doing.
        self._history: list[dict] = []
        self._max_history = 120
        self._cur_span: dict | None = None

    @staticmethod
    def _empty() -> dict:
        return {"title": "", "app": "", "pid": 0,
                "category": "UNKNOWN", "sub": "unknown",
                "ts": time.time(), "active": False}

    # ── lifecycle ────────────────────────────────────────────────────────────

    def set_enabled(self, enabled: bool) -> None:
        with self._lock:
            self._enabled = bool(enabled)
            if not enabled:
                self.current = self._empty()
                self._history = []
                self._cur_span = None
                self._last_key = ("", "")

    @property
    def enabled(self) -> bool:
        with self._lock:
            return self._enabled

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="screen-observer",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None

    # ── sampling ─────────────────────────────────────────────────────────────

    def sample_once(self, now: float | None = None) -> dict:
        """Take one snapshot, classify it, and (on change) record an event.

        Returns the fully-populated current snapshot. Never raises. When the
        observer is disabled this returns an empty snapshot and touches nothing.
        """
        now = now if now is not None else time.time()
        if not self.enabled:
            with self._lock:
                self.current = self._empty()
            return self.current
        raw  = {}
        try:
            raw = _active_window()
        except Exception:
            raw = {}
        snap = {
            "title":     str(raw.get("title", "") or "").strip()[:140],
            "app":       str(raw.get("app", "") or "").lower().strip(),
            "pid":       int(raw.get("pid", 0) or 0),
            "category":  "UNKNOWN",
            "sub":       "unknown",
            "ts":        now,
            "active":    bool(raw.get("app") or raw.get("title")),
        }
        snap["category"], snap["sub"] = classify_activity(snap)

        key = (snap["app"], snap["title"][:80])
        debounce = 8.0
        if key != self._last_key and (now - self._last_change_ts) >= debounce:
            self._last_change_ts = now
            ev = dict(snap)
            ev["before"] = {"app": self._last_key[0], "title": self._last_key[1]}
            with self._lock:
                self._finalize_span(now)
                if snap["app"]:
                    self._cur_span = {
                        "app": snap["app"], "category": snap["category"],
                        "title": snap["title"], "start": now,
                    }
                self.events.append(ev)
                self.events = self.events[-self.max_events:]
                self.current = snap
            self._last_key = key
            if self.on_change:
                try:
                    self.on_change(snap)
                except Exception:
                    pass
        else:
            with self._lock:
                self.current = snap
        return snap

    def _finalize_span(self, now: float) -> None:
        """Close the current activity span into the rolling history."""
        if self._cur_span is None:
            return
        self._cur_span["end"] = now
        self._history.append(self._cur_span)
        self._history = self._history[-self._max_history:]
        self._cur_span = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            started = time.time()
            try:
                if self._safe_enabled():
                    self.sample_once()
            except Exception:
                pass                      # pragma: no cover — observer is best-effort
            elapsed = time.time() - started
            self._stop.wait(max(0.5, self.interval - elapsed))

    def _safe_enabled(self) -> bool:
        with self._lock:
            return self._enabled

    # ── reading for callers ──────────────────────────────────────────────────

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self.current)

    def recent_events(self, limit: int | None = None) -> list[dict]:
        with self._lock:
            evs = list(self.events)
        return evs[-limit:] if limit else evs

    def humanize(self) -> str:
        """A one-line, prompt-friendly description of the current state."""
        c = self.snapshot()
        if not c.get("active") or c.get("category") == "UNKNOWN":
            return ""
        line = f"{c['category']}"
        if c.get("app"):
            line += f" in {c['app']}"
        if c.get("title"):
            line += f" — {c['title'][:90]}"
        return line

    def activity_timeline(self, hours: float = 1.5, limit: int = 12,
                          now: float | None = None) -> list[str]:
        """Prompt-friendly lines of what the user has been doing recently.

        Entirely in-memory: the last finished activity spans overlapping the
        last `hours`, newest first, e.g.
        "3:04 PM — Chrome — youtube.com (6 min)". Durations are best-effort and
        approximate.
        """
        now = time.time() if now is None else now
        window = now - hours * 3600
        with self._lock:
            spans = [s for s in self._history if s.get("end", 0) >= window]
        spans.sort(key=lambda s: s.get("start", 0), reverse=True)
        lines = []
        for s in spans[:limit]:
            app = s.get("app") or "unknown"
            title = s.get("title") or ""
            start = datetime.fromtimestamp(s["start"]).strftime("%I:%M %p")
            mins = (s.get("end", now) - s["start"]) / 60.0
            dur = f" ({int(round(mins))} min)" if mins >= 1 else ""
            label = f"{app}" + (f" — {title[:60]}" if title else "")
            lines.append(f"{start} — {label}{dur}")
        return lines