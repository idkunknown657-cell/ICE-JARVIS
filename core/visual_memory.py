"""visual_memory.py — short-term visual memory, change significance and
adaptive observation cadence for the always-on screen awareness.

The glance loop in main.py already keeps the newest frame + caption and skips
work on a static screen (frame fingerprints). What it treats as "a change" is
still binary: any flicker equals any new window. This module turns that into
the pipeline the design calls for:

    Screen Change → Region Diff → Significance Score → (maybe) Vision → Memory

Three pieces, all local and dependency-free (PIL only, already a dependency):

- `change_regions(a, b)`  — 4x4 grid hash diff of two encoded frames: how much
  of the screen changed and where. A cursor blink moves one cell; a dialog
  moves several. This is the cheap "relevant region detection".

- `VisualMemory`          — a bounded, in-RAM ring of recent screen states
  (fingerprint, caption, app, category, changed-cells). `summarize()` renders
  it as a prompt-friendly block so conversation and proactive check-ins see a
  CONTINUOUS scene ("README.md inside the repo we were just reading"), not a
  pile of unrelated snapshots. Never persisted to disk.

- `significance(...)`     — a 0..1 relevance score for a screen event, from
  the region diff, what the captions say (errors, downloads, dialogs), and the
  activity category. The glance loop and the proactive engine use it to decide
  whether anything is worth doing at all.

- `adaptive_poll(...)`    — the cadence knob: video/games stretch the poll,
  rapid interaction tightens it (with a floor), idle relaxes it.

Everything here is fast enough to run every poll cycle on a low-end machine:
hashes of a 16x12 thumbnail, dict ops, string matching.
"""

from __future__ import annotations

import time

# ── Region diff ───────────────────────────────────────────────────────────────

_GRID = 4          # 4x4 = 16 cells
_GRID_FILE = "core/screen_share.py: frame_fingerprint"   # hash source (16x12)

# Cells (col, row) of the 4x4 grid → human location, row-major from top-left.
_CELL_NAMES = [
    ["top-left", "top-centre-left", "top-centre-right", "top-right"],
    ["upper-left", "upper-centre-left", "upper-centre-right", "upper-right"],
    ["lower-left", "lower-centre-left", "lower-centre-right", "lower-right"],
    ["bottom-left", "bottom-centre-left", "bottom-centre-right", "bottom-right"],
]


def change_regions(data_a: bytes | None, data_b: bytes | None) -> dict:
    """Compare two encoded frames on a 4x4 grid of perceptual hashes.

    Returns {"changed": bool, "ratio": 0..1, "cells": [(col, row), ...],
    "where": "top-left, centre"}. `changed` is False when either frame is
    undecodable or identical. Never raises.
    """
    out = {"changed": False, "ratio": 0.0, "cells": [], "where": ""}
    if not data_a or not data_b:
        return out
    try:
        from core.screen_share import _grid_hashes
    except Exception:
        _grid_hashes = None
    try:
        if _grid_hashes is None:
            from core import screen_share as _ss
            _grid_hashes = _ss._grid_hashes
        ha = _grid_hashes(data_a)
        hb = _grid_hashes(data_b)
    except Exception:
        return out
    if not ha or not hb or ha == hb:
        return out
    cells = [(i % _GRID, i // _GRID)
             for i in range(_GRID * _GRID) if ha[i] != hb[i]]
    if not cells:
        return out
    out["changed"] = True
    out["cells"] = cells
    out["ratio"] = len(cells) / float(_GRID * _GRID)
    names = [(_CELL_NAMES[r][c]) for c, r in cells]
    out["where"] = ", ".join(names[:3]) + ("…" if len(names) > 3 else "")
    return out


# ── Significance scoring ──────────────────────────────────────────────────────

# Caption fragments that mark an event worth noticing. Ordered by weight.
_HIGH_SIGNAL = (
    "error", "failed", "failure", "crash", "warning", "critical",
    "download complete", "download finished", "update available",
    "install", "setup wizard", "permission", "confirm", "dialog",
    "password", "payment", "checkout", "verification",
)
_MEDIUM_SIGNAL = (
    "new tab", "new window", "search results", "login", "sign in",
    "file explorer", "terminal", "notification", "message",
)
# Categories where nothing on screen is usually worth interrupting for.
_LOW_CATEGORIES = ("WATCHING_VIDEO", "GAMING", "MUSIC")


def significance(
    ratio: float = 0.0,
    caption_new: str = "",
    caption_old: str = "",
    category: str = "",
    idle_secs: float = 0.0,
    static_secs: float = 0.0,
) -> tuple[float, str]:
    """Score how interesting a screen change is: 0..1 plus a reason string.

    - region ratio: small diffs (cursor, blink) score low; a third of the
      screen changing (a new window/dialog) scores high.
    - caption keywords: errors / downloads / dialogs boost sharply; the same
      word appearing in the OLD caption already is not news.
    - category: video/game/music frames are demoted hard.
    - a screen that has been static for a long time decays to zero.
    """
    score = 0.0
    reasons: list[str] = []

    # Region weight: 1 cell (6%) ≈ nothing, ≥4 cells (25%) starts to matter.
    if ratio > 0:
        score += min(0.45, ratio * 1.6)
        if ratio >= 0.25:
            reasons.append("large area changed")

    new_low = (caption_new or "").lower()
    old_low = (caption_old or "").lower()
    for kw in _HIGH_SIGNAL:
        if kw in new_low and kw not in old_low:
            score += 0.35
            reasons.append(f"'{kw}' appeared")
            break
    for kw in _MEDIUM_SIGNAL:
        if kw in new_low and kw not in old_low:
            score += 0.15
            reasons.append(f"'{kw}' appeared")
            break

    if category in _LOW_CATEGORIES:
        score *= 0.25
        reasons.append(f"media category ({category.lower()}) demoted")

    if static_secs > 300:
        score *= 0.4
        reasons.append("screen long static")

    if idle_secs > 0 and idle_secs < 3:
        # User actively interacting: slightly more interesting.
        score = min(1.0, score + 0.05)

    if not reasons:
        reasons.append("minor change")
    return min(1.0, score), "; ".join(reasons)


# ── Activity inference ────────────────────────────────────────────────────────

# app/category + caption keywords → a plain-words activity guess.
def infer_activity(category: str, app: str, caption: str) -> str:
    """One short line: what the user is probably doing. '' when unknown."""
    cap = (caption or "").lower()
    if category == "CODING":
        return f"working on code in {app}" if app else "working on code"
    if category == "BROWSING":
        if "github" in cap:
            return "browsing a GitHub project"
        if any(s in cap for s in ("search", "results")):
            return "searching the web"
        if any(s in cap for s in ("docs", "documentation", "wiki")):
            return "reading documentation"
        return "browsing the web"
    if category == "GAMING":
        return "gaming"
    if category == "WATCHING_VIDEO":
        return "watching a video"
    if category == "COMMUNICATION":
        return "chatting with someone"
    if category == "FILE_MANAGEMENT":
        if "download" in cap:
            return "managing downloaded files"
        return "managing files"
    if category == "WRITING":
        return "writing"
    if category == "MUSIC":
        return "listening to music"
    if category == "DESIGN":
        return "designing"
    return ""


# ── Adaptive cadence ──────────────────────────────────────────────────────────

def adaptive_poll(base_secs: float, category: str = "",
                  recent_change_ratio: float = 0.0,
                  static_secs: float = 0.0) -> float:
    """Next poll interval for the glance loop.

    - video/game/music: 1.8x base (nothing to caption while consuming)
    - rapid UI churn (high change ratio): 0.6x base, floored at 2.5 s
    - long-static screen: up to 2x base (nothing is happening)
    """
    base = max(1.0, float(base_secs))
    if category in _LOW_CATEGORIES:
        return base * 1.8
    if recent_change_ratio >= 0.5:
        return max(2.5, base * 0.6)
    if static_secs > 600:
        return base * 2.0
    return base


# ── The memory ────────────────────────────────────────────────────────────────

class VisualMemory:
    """Bounded short-term memory of what the screen has been showing.

    Fed by the glance loop on every real change. Entries are dicts:
    {ts, fingerprint, caption, app, title, category, ratio, where, sig}.
    The ring holds `_max` entries (default 24 ≈ a few minutes of active use).
    Everything lives in RAM only; `set_enabled(False)` wipes it.
    """

    def __init__(self, max_entries: int = 24):
        self._max = max(4, int(max_entries))
        self._states: list[dict] = []
        self._enabled = True
        self._last: dict | None = None

    # ── lifecycle ────────────────────────────────────────────────────────────
    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)
        if not enabled:
            self._states.clear()
            self._last = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    # ── writing ──────────────────────────────────────────────────────────────
    def observe(
        self,
        fingerprint: str = "",
        caption: str = "",
        app: str = "",
        title: str = "",
        category: str = "",
        ratio: float = 0.0,
        where: str = "",
        sig: float = 0.0,
        ts: float | None = None,
    ) -> dict:
        """Record one screen state (call only on real changes). Returns the
        entry stored. No-ops (returning {}) when disabled or deduplicated."""
        if not self._enabled:
            return {}
        fp = fingerprint or ""
        if self._last and fp and self._last.get("fingerprint") == fp:
            return {}
        entry = {
            "ts": time.time() if ts is None else ts,
            "fingerprint": fp,
            "caption": (caption or "")[:300],
            "app": (app or "")[:60],
            "title": (title or "")[:120],
            "category": (category or "")[:30],
            "ratio": float(ratio),
            "where": (where or "")[:80],
            "sig": float(sig),
        }
        self._states.append(entry)
        if len(self._states) > self._max:
            self._states = self._states[-self._max:]
        self._last = entry
        return entry

    # ── reading ──────────────────────────────────────────────────────────────
    @property
    def last(self) -> dict | None:
        return self._last

    def current_activity(self) -> str:
        """One line: what the user is doing right now (best effort)."""
        if not self._last:
            return ""
        e = self._last
        act = infer_activity(e.get("category", ""), e.get("app", ""),
                             e.get("caption", ""))
        return act or (e.get("category", "").lower().replace("_", " ") or "")

    def static_secs(self, now: float | None = None) -> float:
        """Seconds since the last real change (0 when nothing recorded)."""
        if not self._last:
            return 0.0
        return max(0.0, (time.time() if now is None else now)
                   - self._last.get("ts", 0.0))

    def recent_changes(self, limit: int = 4) -> list[str]:
        """Prompt-friendly lines of the last real screen changes, newest first."""
        out: list[str] = []
        for e in reversed(self._states[-limit:]):
            when = time.strftime("%H:%M", time.localtime(e["ts"]))
            what = e.get("caption") or (e.get("app") + " " + e.get("title", "")).strip()
            what = what[:110] if what else "screen changed"
            loc = f" ({e['where']})" if e.get("where") else ""
            out.append(f"{when} — {what}{loc}")
        return out

    def summarize(self, limit: int = 4) -> str:
        """A compact prompt block: current scene + the trail that led here.
        '' when awareness has nothing (disabled or no changes yet)."""
        if not self._enabled or not self._states:
            return ""
        lines: list[str] = []
        act = self.current_activity()
        if act:
            lines.append(f"Currently: {act}")
        e = self._last or {}
        if e.get("caption"):
            lines.append(f"Latest view: {e['caption'][:160]}")
        changes = self.recent_changes(limit)
        if changes:
            lines.append("Recent screen changes:")
            lines.extend(f"- {c}" for c in changes)
        return "\n".join(lines)
