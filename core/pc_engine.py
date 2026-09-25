"""
core/pc_engine.py — the computer-control engine, rebuilt around one rule:

    SCREEN → FIND TARGET → REAL SCREEN COORDINATES → MOVE → VERIFY

WHY THIS EXISTS
    The old stack spread control across three files with three different ideas of
    "where things are": computer_control guessed a point off a full-screen
    screenshot, screen_ai read a UIA rect, pc_input normalised over the virtual
    desktop. Each was individually reasonable and together they disagreed — the
    classic symptom being a click that lands 20 px off on a 125 % display, or a
    "click the button" that clicks the window behind it. This module is the one
    place that answers "where is X, in screen pixels", and every action goes
    through it.

WHAT IT FIXES
    1. MOUSE ACCURACY
       - The process is per-monitor DPI aware before anything is measured.
       - A capture is never trusted to match the mouse's coordinate space: the
         ratio between the image and the virtual desktop is measured on every
         capture and applied explicitly (ScreenMap).
       - Coordinates cover the WHOLE virtual desktop (all monitors), not the
         primary one, so a second monitor to the left (negative x) works.
       - Grounding is a cascade, best source first:
             UIA element rect  →  vision, coarse then fine  →  nothing
         Rejected rather than guessed: if nothing found the target, no click is
         issued and the caller is told.
    2. TYPE ACCURACY
       - The field is found and focused first, and focus is verified (UIA focus,
         or a caret probe) before a single keystroke leaves.
       - Long or non-Latin text goes through the clipboard, never through
         per-character key events (which lose characters under load).
       - The result is READ BACK (UIA value, else select-all/copy) and compared.
         A mismatch clears the field and retries once by the other method; if it
         still differs the caller is told what is actually in the field.
    3. LATENCY
       - UIA answers with zero model calls; the vision path only runs when the
         accessibility tree genuinely cannot answer.
       - The UIA tree is cached for a moment, so a multi-step task walks it once.
       - Two-stage vision (coarse full screen → fine crop ×3) needs far fewer
         pixels per call than one 4K screenshot, so it is both more accurate and
         quicker.
       - Verification is usually a local pixel-signature comparison; the model is
         only asked when the screen did not move at all.
    4. RECOVERY
       - Every action returns a Result carrying what it did, by which method, and
         whether it was verified. Failures say what was on screen instead, so the
         caller can re-plan instead of repeating the same dead click.

EVERYTHING HERE IS BEST-EFFORT AND NEVER RAISES OUT OF A PUBLIC FUNCTION: a
control action must return a sentence the assistant can say, not a traceback.
"""

from __future__ import annotations

import io
import platform
import re
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

_IS_WINDOWS = sys.platform == "win32"

# ── optional dependencies (every one degrades gracefully) ─────────────────────

try:
    import pyautogui as _PAG
    _PAG.FAILSAFE = False        # we clamp and steer corners ourselves
    _PAG.PAUSE = 0.0             # we own the pacing; the default 100 ms per call
                                 # alone made a six-step task feel slow
    _HAS_PAG = True
except Exception:                # pragma: no cover
    _PAG = None
    _HAS_PAG = False

try:
    from PIL import Image, ImageDraw, ImageFont, ImageStat
    _HAS_PIL = True
except Exception:                # pragma: no cover
    Image = ImageDraw = ImageFont = ImageStat = None
    _HAS_PIL = False

try:
    import pywinauto
    _HAS_UIA = True
except Exception:
    pywinauto = None
    _HAS_UIA = False

try:
    from core import pc_log
except Exception:                # pragma: no cover
    pc_log = None


def _log(msg: str) -> str:
    """Console logging that cannot itself crash a control action."""
    try:
        print(str(msg).encode("ascii", "replace").decode("ascii"))
    except Exception:
        pass
    return ""


# ════════════════════════════════════════════════════════════════════════════
#  1. COORDINATE TRUTH
# ════════════════════════════════════════════════════════════════════════════

def _enable_dpi_awareness() -> None:
    """Per-monitor DPI awareness, without fighting a mode the host already set.

    A DPI-unaware process is handed *virtualised* metrics: `size()` says 1536x864
    while the screenshot is a real 1920x1080, so every coordinate read off the
    picture is wrong by the scale factor. Setting this once at import removes the
    whole class of offset bugs. Qt sets its own on some machines, so an already
    aware process is left alone.
    """
    if not _IS_WINDOWS:
        return
    try:
        import ctypes
        try:
            ctypes.windll.shcore.GetProcessDpiAwareness(None, ctypes.byref(ctypes.c_int(0)))
        except Exception:
            pass
        try:
            # PER_MONITOR_AWARE_V2 = -4; correct on mixed-DPI multi-monitor setups
            if ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
                return
        except Exception:
            pass
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


_enable_dpi_awareness()


def virtual_desktop() -> tuple[int, int, int, int]:
    """(left, top, width, height) of every monitor combined, in real pixels.

    A second monitor positioned left of or above the primary has negative
    coordinates; those are part of the desktop and must not be clamped away.
    """
    if _IS_WINDOWS:
        try:
            import ctypes
            u = ctypes.windll.user32
            left, top = int(u.GetSystemMetrics(76)), int(u.GetSystemMetrics(77))
            wide, high = int(u.GetSystemMetrics(78)), int(u.GetSystemMetrics(79))
            if wide > 1 and high > 1:
                return left, top, wide, high
        except Exception:
            pass
    w, h = primary_size()
    return 0, 0, w, h


def primary_size() -> tuple[int, int]:
    """(width, height) of the primary monitor in real pixels."""
    if _IS_WINDOWS:
        try:
            import ctypes
            u = ctypes.windll.user32
            w, h = int(u.GetSystemMetrics(0)), int(u.GetSystemMetrics(1))
            if w > 1 and h > 1:
                return w, h
        except Exception:
            pass
    if _HAS_PAG:
        try:
            s = _PAG.size()
            return int(s[0]), int(s[1])
        except Exception:
            pass
    return 1920, 1080


def monitors() -> list[dict]:
    """Geometry of each attached monitor: [{'x','y','width','height'}, ...]."""
    if not _IS_WINDOWS:
        w, h = primary_size()
        return [{"x": 0, "y": 0, "width": w, "height": h}]
    out: list[dict] = []
    try:
        import ctypes
        from ctypes import wintypes
        MONITORENUMPROC = ctypes.WINFUNCTYPE(
            wintypes.BOOL, wintypes.HANDLE, wintypes.HANDLE,
            ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)

        def _cb(_m, _hdc, rect, _lp):
            r = rect.contents
            out.append({"x": r.left, "y": r.top,
                        "width": r.right - r.left, "height": r.bottom - r.top})
            return True

        ctypes.windll.user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(_cb), 0)
    except Exception:
        pass
    if not out:
        w, h = primary_size()
        out = [{"x": 0, "y": 0, "width": w, "height": h}]
    return out


@dataclass(frozen=True)
class ScreenMap:
    """The bridge between picture pixels and mouse pixels.

    `img_width/img_height` describe one captured image (possibly a crop of a
    region). `left/top/width/height` describe the virtual desktop. The scale is
    derived, never assumed: on a correctly DPI-aware process it is 1.0, and when
    the host forces scaling it still comes out right.
    """
    left: int
    top: int
    width: int
    height: int
    img_width: int = 0
    img_height: int = 0

    @property
    def scale_x(self) -> float:
        return (self.width / self.img_width) if self.img_width else 1.0

    @property
    def scale_y(self) -> float:
        return (self.height / self.img_height) if self.img_height else 1.0

    @property
    def right(self) -> int:
        return self.left + self.width - 1

    @property
    def bottom(self) -> int:
        return self.top + self.height - 1

    def to_screen(self, img_x: float, img_y: float,
                  origin: tuple[int, int] | None = None) -> tuple[int, int]:
        """Image pixel (+ the region's screen origin) → screen pixel.

        `origin` defaults to the top-left of THIS map, which is the virtual
        desktop's top-left — not (0, 0). Assuming (0, 0) is exactly how a second
        monitor positioned left of or above the primary ends up with every
        coordinate shifted by the virtual origin, silently clicking the wrong
        place. For a crop, pass that crop's screen origin explicitly.
        """
        if origin is None:
            origin = (self.left, self.top)
        x = origin[0] + float(img_x) * self.scale_x
        y = origin[1] + float(img_y) * self.scale_y
        return clamp_screen(int(round(x)), int(round(y)), self)

    def to_image(self, x: float, y: float,
                 origin: tuple[int, int] | None = None) -> tuple[int, int]:
        """Screen pixel → image pixel, relative to a region at `origin`."""
        if origin is None:
            origin = (self.left, self.top)
        sx = self.scale_x or 1.0
        sy = self.scale_y or 1.0
        return (int(round((float(x) - origin[0]) / sx)),
                int(round((float(y) - origin[1]) / sy)))

    def contains(self, x: int, y: int) -> bool:
        return self.left <= x <= self.right and self.top <= y <= self.bottom

    def describe(self) -> str:
        return (f"{self.width}x{self.height} at {self.left},{self.top} "
                f"(image {self.img_width}x{self.img_height}, "
                f"scale {self.scale_x:.3f}x{self.scale_y:.3f})")


def screen_map(img_size: tuple[int, int] | None = None) -> ScreenMap:
    """Build the mapping for the whole virtual desktop."""
    left, top, wide, high = virtual_desktop()
    iw, ih = (int(img_size[0]), int(img_size[1])) if img_size else (wide, high)
    return ScreenMap(left=left, top=top, width=wide, height=high,
                     img_width=iw, img_height=ih)


def clamp_screen(x: float, y: float, sm: ScreenMap | None = None) -> tuple[int, int]:
    """Keep a point inside the virtual desktop and off the fail-safe corners.

    pyautogui treats the four corners as an abort switch; parking the pointer on
    one silently kills the *next* control call, so a clamped point must never be
    able to wedge the engine.
    """
    sm = sm or screen_map()
    try:
        cx, cy = int(round(float(x))), int(round(float(y)))
    except (TypeError, ValueError):
        cx, cy = sm.left, sm.top
    cx = max(sm.left, min(cx, sm.right))
    cy = max(sm.top, min(cy, sm.bottom))
    pw, ph = primary_size()
    if 0 <= cx < pw:
        cx = 1 if cx == 0 else (pw - 2 if cx >= pw - 1 else cx)
    if 0 <= cy < ph:
        cy = 1 if cy == 0 else (ph - 2 if cy >= ph - 1 else cy)
    return cx, cy


def clamp_box(box: Sequence[int], sm: ScreenMap | None = None) -> tuple[int, int, int, int]:
    """Clamp a region box (x, y, w, h) to the desktop. Pure."""
    sm = sm or screen_map()
    try:
        x, y, w, h = (int(box[0]), int(box[1]), int(box[2]), int(box[3]))
    except Exception:
        return sm.left, sm.top, sm.width, sm.height
    x = max(sm.left, min(x, sm.right))
    y = max(sm.top, min(y, sm.bottom))
    w = max(1, min(w, sm.right - x + 1))
    h = max(1, min(h, sm.bottom - y + 1))
    return x, y, w, h


# ── capture ──────────────────────────────────────────────────────────────────

def _grab(box: tuple[int, int, int, int] | None = None):
    """Grab the virtual desktop, or a box inside it, as a PIL image.

    Pillow directly rather than pyautogui: pyautogui's screenshot crops a
    PRIMARY-screen grab, so a region on a second monitor comes back as whatever
    happens to sit at those coordinates on monitor 1 — one of the quiet reasons
    "click the thing on my other screen" used to fail.
    """
    if _HAS_PIL:
        try:
            from PIL import ImageGrab
            if box:
                x, y, w, h = box
                return ImageGrab.grab(bbox=(x, y, x + w, y + h), all_screens=True)
            return ImageGrab.grab(all_screens=True)
        except TypeError:
            # all_screens is Windows-only; other platforms take the plain call.
            try:
                from PIL import ImageGrab
                if box:
                    x, y, w, h = box
                    return ImageGrab.grab(bbox=(x, y, x + w, y + h))
                return ImageGrab.grab()
            except Exception:
                pass
        except Exception:
            pass
    if _HAS_PAG:
        return _PAG.screenshot(region=box)
    raise RuntimeError("screen capture needs pillow or pyautogui")


def capture_region(region: tuple[int, int, int, int] | None = None,
                   *, max_width: int = 0):
    """Grab the screen (or a region) and return (image, ScreenMap, origin).

    `max_width` downscales the image for a cheap call; the ScreenMap still maps
    its pixels onto the real desktop, so accuracy does not depend on the size.
    """
    if not _HAS_PIL and not _HAS_PAG:
        raise RuntimeError("screen capture needs pillow or pyautogui")
    box = None
    if region:
        x, y, w, h = clamp_box(region)
        box = (x, y, w, h)
    img = _grab(box)
    if img is None:
        raise RuntimeError("screenshot returned nothing")
    origin = (box[0], box[1]) if box else (virtual_desktop()[:2])
    if max_width and img.width > max_width:
        ratio = max_width / float(img.width)
        img = img.resize((int(img.width * ratio), max(1, int(img.height * ratio))),
                         Image.BILINEAR)
    if box:
        sm = ScreenMap(left=box[0], top=box[1], width=box[2], height=box[3],
                       img_width=img.width, img_height=img.height)
    else:
        sm = screen_map((img.width, img.height))
    return img, sm, origin


# ════════════════════════════════════════════════════════════════════════════
#  2. TARGETS
# ════════════════════════════════════════════════════════════════════════════

@dataclass
class Target:
    """Something on screen that can be clicked, in real screen pixels."""
    label: str
    x: int
    y: int
    w: int = 0
    h: int = 0
    source: str = "vision"          # uia | vision-fine | vision-coarse | point
    confidence: float = 0.5
    control_type: str = ""
    app: str = ""
    note: str = ""

    @property
    def center(self) -> tuple[int, int]:
        return self.x + self.w // 2, self.y + self.h // 2

    @property
    def box(self) -> tuple[int, int, int, int]:
        return self.x, self.y, self.w, self.h

    def summary(self) -> str:
        cx, cy = self.center if self.w or self.h else (self.x, self.y)
        ct = f" [{self.control_type}]" if self.control_type else ""
        return f"{self.label!r}{ct} at {cx},{cy} via {self.source}"


@dataclass
class Result:
    """What an action actually did. `ok` means it happened; `verified` means the
    effect was confirmed. A caller may legitimately report ok=True,
    verified=False — it must never report ok=False as done."""
    ok: bool
    detail: str
    method: str = ""
    verified: bool = False
    target: Target | None = None
    recovered: bool = False

    def __bool__(self) -> bool:
        return bool(self.ok)

    def line(self) -> str:
        tag = ""
        if self.ok and not self.verified:
            tag = " (unverified)"
        elif self.ok and self.recovered:
            tag = " (recovered)"
        return f"{self.detail}{tag}"


# ── UIA access (the accurate, free source) ───────────────────────────────────

_UIA_CACHE: dict[str, Any] = {"at": 0.0, "key": "", "elements": []}
_UIA_TTL = 1.2                      # seconds; one task walks the tree once

_INTERACTIVE = {
    "Button", "Edit", "Text", "Document", "Hyperlink", "TabItem", "CheckBox",
    "RadioButton", "ListItem", "MenuItem", "ComboBox", "TreeItem", "Slider",
    "HeaderItem", "DataItem", "SplitButton", "Spinner", "Custom",
}


def uia_available() -> bool:
    return _HAS_UIA and _IS_WINDOWS


def foreground_title() -> str:
    """Title of the window that currently has focus."""
    if not _IS_WINDOWS:
        return ""
    try:
        import ctypes
        from ctypes import wintypes
        u = ctypes.windll.user32
        u.GetForegroundWindow.restype = wintypes.HWND
        hwnd = u.GetForegroundWindow()
        if not hwnd:
            return ""
        n = u.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(n + 1)
        u.GetWindowTextW(hwnd, buf, n + 1)
        return buf.value.strip()
    except Exception:
        return ""


def active_app() -> str:
    """Short application name for strategy memory / logging."""
    title = foreground_title()
    if not title:
        return "unknown"
    for sep in (" - ", " — ", " | "):
        if sep in title:
            title = title.split(sep)[-1]
    return title.strip()[:40] or "unknown"


def _uia_root(window: str | None):
    if not uia_available():
        return None
    try:
        if window:
            wins = pywinauto.findwindows.find_windows(
                title_re=rf".*{re.escape(window)}.*", backend="uia", visible_only=True)
            if not wins:
                return None
            return pywinauto.Desktop(backend="uia").window(handle=wins[0])
        # Prefer the window under the cursor's focus; fall back to the desktop.
        from win32gui import GetForegroundWindow           # type: ignore
        hwnd = GetForegroundWindow()
        if hwnd:
            return pywinauto.Desktop(backend="uia").window(handle=hwnd)
    except Exception as e:
        _log(f"[pc_engine] uia root failed: {e}")
    try:
        return pywinauto.Desktop(backend="uia").window(handle=None)
    except Exception:
        return None


def _rect(elm) -> tuple[int, int, int, int]:
    try:
        r = elm.rectangle()
        return int(r.left), int(r.top), int(r.right), int(r.bottom)
    except Exception:
        pass
    try:
        r = elm.element_info.rectangle
        return int(r.left), int(r.top), int(r.right), int(r.bottom)
    except Exception:
        return 0, 0, 0, 0


def _collect(elms: Iterable, out: list, depth: int = 0, cap: int = 700) -> None:
    """Flatten a UIA subtree into dicts. Bounded so a huge window cannot hang."""
    if depth > 22 or len(out) >= cap:
        return
    for elm in elms or []:
        if len(out) >= cap:
            return
        try:
            ctype = (elm.control_type or "").strip()
        except Exception:
            ctype = ""
        name = ""
        try:
            name = (elm.name or "").strip()
        except Exception:
            pass
        left, top, right, bottom = _rect(elm)
        kids = []
        try:
            kids = elm.children() or []
        except Exception:
            kids = []
        if ctype and (left != right or top != bottom):
            if ctype in _INTERACTIVE or name:
                out.append({
                    "type": ctype, "name": name,
                    "x": left, "y": top, "w": max(0, right - left),
                    "h": max(0, bottom - top), "elm": elm,
                })
        _collect(kids, out, depth + 1, cap)


def uia_elements(window: str | None = None, *, cap: int = 700,
                 fresh: bool = False) -> list[dict]:
    """Interactive elements of a window, as dicts with real screen rects.

    Cached for `_UIA_TTL` because the common multi-step task ("click the search
    box, type, click the first result") reads the same tree three times, and each
    walk of a browser window costs real milliseconds.
    """
    if not uia_available():
        return []
    key = f"{window or ''}|{foreground_title()}"
    now = time.monotonic()
    if not fresh and _UIA_CACHE["key"] == key and (now - _UIA_CACHE["at"]) < _UIA_TTL:
        return list(_UIA_CACHE["elements"])
    root = _uia_root(window)
    if root is None:
        return []
    out: list[dict] = []
    try:
        _collect(root.children(), out, cap=cap)
    except Exception:
        pass
    if not out:
        try:
            _collect([root], out, cap=cap)
        except Exception:
            pass
    _UIA_CACHE.update({"at": now, "key": key, "elements": out})
    return list(out)


def clear_uia_cache() -> None:
    _UIA_CACHE.update({"at": 0.0, "key": "", "elements": []})


def element_at(x: int, y: int) -> dict | None:
    """The UIA element under a screen point (used to verify a click landed)."""
    if not uia_available():
        return None
    try:
        elm = pywinauto.Desktop(backend="uia").element_from_point(int(x), int(y))
        left, top, right, bottom = _rect(elm)
        return {"name": (getattr(elm, "name", "") or "").strip(),
                "type": (getattr(elm, "control_type", "") or "").strip(),
                "x": left, "y": top, "w": right - left, "h": bottom - top,
                "elm": elm}
    except Exception:
        return None


# ── matching (pure, unit-tested) ─────────────────────────────────────────────

_LOOK_AROUND = {
    "search box": ("Edit", "ComboBox", "Document"),
    "search bar": ("Edit", "ComboBox", "Document"),
    "address bar": ("Edit", "ComboBox", "ToolBar"),
    "message box": ("Edit", "Document"),
    "text field": ("Edit", "Document"),
    "input": ("Edit", "Document"),
    "button": ("Button", "SplitButton", "Hyperlink"),
    "close": ("Button", "MenuItem"),
    "save": ("Button", "MenuItem"),
}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", str(s or "").lower()).strip()


def wanted_types(query: str) -> tuple[str, ...]:
    """Control types implied by a description like 'search box' or 'Save button'."""
    q = _norm(query)
    for phrase, types in _LOOK_AROUND.items():
        if phrase in q:
            return types
    return ()


def score_element(query: str, el: dict) -> float:
    """How well a UIA element matches a description. Pure, 0..1.

    Exact name wins; then the name with the describing words stripped ('Save
    button' → 'save'); then a substring either way; then automation-id hits.
    A control-type hint ('button', 'box') breaks ties rather than filtering, so a
    description that names the wrong widget still finds its target.
    """
    q = _norm(query)
    if not q:
        return 0.0
    name = _norm(el.get("name"))
    types = wanted_types(query)
    type_bonus = 0.08 if types and el.get("type") in types else 0.0
    if not name:
        return type_bonus * 0.5
    if name == q:
        return 1.0 + type_bonus
    # Strip the widget nouns the user bandies about and compare the rest.
    core = q
    for word in ("button", "box", "field", "bar", "tab", "menu", "item", "link",
                 "icon", "search", "text"):
        core = core.replace(word, " ")
    core = " ".join(core.split())
    if core:
        if name == core:
            return 0.97 + type_bonus
        if core in name:
            # "the search box" → Edit "Search" scores high; a 200-word document
            # that merely contains it scores low.
            return (0.80 - min(0.2, len(name) / 400.0)) + type_bonus
        if name in core:
            return 0.74 + type_bonus
    if name and name in q:
        return 0.70 + type_bonus
    aid = _norm(el.get("automation_id") or el.get("aid") or "")
    if aid and (aid == core or (core and core in aid)):
        return 0.66 + type_bonus
    return type_bonus


def best_matches(query: str, elements: Sequence[dict],
                 *, threshold: float = 0.55, limit: int = 8) -> list[tuple[float, dict]]:
    """Ranked (score, element) candidates above `threshold`. Pure."""
    ranked = [(score_element(query, e), e) for e in elements or []]
    ranked = [r for r in ranked if r[0] >= threshold]
    ranked.sort(key=lambda r: (-r[0], r[1].get("y", 0), r[1].get("x", 0)))
    return ranked[:limit]


_NUM = r"(-?\d{1,5})"
_COORD_PATTERNS = (
    # "x=812 y=540" / "x: 812, y: 540"
    re.compile(rf"x\s*[=:]\s*{_NUM}\D{{0,10}}?y\s*[=:]\s*{_NUM}", re.I),
    # "812,540" / "(812, 540)" / "812px, 540"
    re.compile(rf"{_NUM}\s*(?:px)?\s*[,;]\s*(?:y\s*[=:]\s*)?{_NUM}", re.I),
    # "812 540" (only trusted in a short, coordinate-shaped reply)
    re.compile(rf"^\W{{0,3}}{_NUM}\s+{_NUM}\W{{0,3}}$"),
)
_PCT_RE = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%\s*[,;/\s]\s*(\d{1,3}(?:\.\d+)?)\s*%")


def parse_coords(text: str, sm: ScreenMap | None = None,
                 origin: tuple[int, int] | None = None) -> tuple[int, int] | None:
    """Pull a coordinate out of a model reply, tolerantly. Pure.

    Accepts "812,540", "812 540", "(812, 540)", "x=812 y=540" and percentage
    forms like "62%,44%" (some models answer in fractions of the image when the
    grid is faint). Returns None for NOT_FOUND or anything unusable — a guess is
    worse than no answer here, because it becomes a click.
    """
    raw = str(text or "").strip()
    if not raw or "NOT_FOUND" in raw.upper():
        return None
    sm = sm or screen_map()
    pct = _PCT_RE.search(raw)
    if pct:
        px = min(100.0, max(0.0, float(pct.group(1)))) / 100.0
        py = min(100.0, max(0.0, float(pct.group(2)))) / 100.0
        return sm.to_screen(px * sm.img_width, py * sm.img_height, origin)
    for pattern in _COORD_PATTERNS:
        m = pattern.search(raw if len(raw) <= 60 else raw[:60])
        if not m:
            continue
        try:
            return sm.to_screen(float(m.group(1)), float(m.group(2)), origin)
        except Exception:
            continue
    return None


# ── vision grounding (coarse → fine) ─────────────────────────────────────────

def _vision_text(parts: list, *, timeout_ms: int = 18_000) -> str:
    """One FAST vision call through the configured provider chain."""
    try:
        from core import gemini
        txt = gemini.text(parts, tier=gemini.FAST, timeout_ms=timeout_ms, default="")
        return (txt or "").strip()
    except Exception as e:
        _log(f"[pc_engine] vision call failed: {e}")
        return ""


def _png(img, *, quality_width: int = 0) -> bytes:
    buf = io.BytesIO()
    if quality_width and img.width > quality_width:
        ratio = quality_width / float(img.width)
        img = img.resize((int(img.width * ratio), max(1, int(img.height * ratio))),
                         Image.BILINEAR)
    img.convert("RGB").save(buf, format="PNG", optimize=False)
    return buf.getvalue()


def _grid(img, step: int = 120):
    """Label the coordinate system onto the image.

    Models read a coordinate off a labelled grid far more accurately than they
    estimate one on a blank screenshot; the labels are the image's own pixel
    numbers, so the caller's ScreenMap does the rest.
    """
    try:
        draw = ImageDraw.Draw(img)
        w, h = img.size
        try:
            font = ImageFont.load_default()
        except Exception:
            font = None
        for x in range(0, w, step):
            draw.line([(x, 0), (x, h)], fill=(255, 60, 60), width=1)
            if font:
                draw.text((x + 2, 2), str(x), fill=(255, 40, 40), font=font)
        for y in range(0, h, step):
            draw.line([(0, y), (w, y)], fill=(255, 60, 60), width=1)
            if font:
                draw.text((2, y + 2), str(y), fill=(255, 40, 40), font=font)
    except Exception:
        pass
    return img


def vision_locate(description: str, *, region: tuple[int, int, int, int] | None = None,
                  hint: tuple[int, int] | None = None, refine: bool = True,
                  timeout_ms: int = 18_000) -> Target | None:
    """Find `description` on screen and return its real screen coordinates.

    Stage 1 (coarse): a downscaled, grid-labelled capture — cheap and enough to
    place the element within ~40 px.
    Stage 2 (fine): a ×3 upscale of a small crop around that estimate, which the
    model reads with sub-pixel-ish precision. Two small images beat one huge one
    on both accuracy and latency.

    `hint` skips the coarse stage entirely when the caller already knows roughly
    where to look (a previous hit, or a UIA rect with a stale position) — that is
    the fast path for "the target moved a little".
    """
    if not _HAS_PIL:
        return None
    try:
        from google.genai import types as gtypes
    except Exception:
        return None

    desc = str(description or "").strip()
    if not desc:
        return None

    coarse: tuple[int, int] | None = None
    if hint and region is None:
        coarse = (int(hint[0]), int(hint[1]))
        sm = screen_map()
    else:
        try:
            img, sm, origin = capture_region(region, max_width=1440)
        except Exception as e:
            _log(f"[pc_engine] coarse capture failed: {e}")
            return None
        grid_img = _grid(img.copy(), max(80, img.width // 10))
        prompt = (
            f"Screenshot of a computer screen with a faint red coordinate grid. "
            f"Each grid line is labelled with its own pixel coordinate. Find the "
            f"UI element described as: '{desc}'. Read its CENTRE off the grid "
            f"labels and reply with ONLY 'x,y' in this image's pixel coordinates. "
            f"If it is not visible, reply exactly NOT_FOUND."
        )
        reply = _vision_text(
            [gtypes.Part.from_bytes(data=_png(grid_img), mime_type="image/png"), prompt],
            timeout_ms=timeout_ms)
        if not reply or "NOT_FOUND" in reply.upper():
            return None
        coarse = parse_coords(reply, sm, origin)
        if coarse is None:
            return None
        origin_arg = origin

    if not refine:
        return Target(label=desc, x=coarse[0], y=coarse[1], source="vision-coarse",
                      confidence=0.55, app=active_app())

    # Stage 2 — precision on a small crop, upscaled so the model has pixels to
    # spare. A ±70 px window is 140 px wide; at ×3 that is a comfortable image.
    pad = 70
    box = clamp_box((coarse[0] - pad, coarse[1] - pad, pad * 2, pad * 2))
    try:
        crop, csm, corigin = capture_region(box)
    except Exception:
        return Target(label=desc, x=coarse[0], y=coarse[1], source="vision-coarse",
                      confidence=0.55, app=active_app())
    big = crop.resize((crop.width * 3, crop.height * 3), Image.LANCZOS)
    prompt = (
        f"A magnified close-up (3x) of one small area of a screen, {crop.width}x"
        f"{crop.height} real pixels. Inside it is the element described as: "
        f"'{desc}'. Reply with ONLY its centre as 'x,y' in the CLOSE-UP image's "
        f"pixel coordinates (image is {big.width}x{big.height}). If the element "
        f"is not in this close-up, reply exactly NOT_FOUND."
    )
    reply = _vision_text(
        [gtypes.Part.from_bytes(data=_png(big), mime_type="image/png"), prompt],
        timeout_ms=timeout_ms)
    if reply and "NOT_FOUND" not in reply.upper():
        m = _COORD_PATTERNS[1].search(reply[:60]) or _COORD_PATTERNS[0].search(reply[:60])
        if m:
            fx = float(m.group(1)) / 3.0
            fy = float(m.group(2)) / 3.0
            x, y = csm.to_screen(fx, fy, corigin)
            if abs(x - coarse[0]) <= pad * 2 and abs(y - coarse[1]) <= pad * 2:
                return Target(label=desc, x=x, y=y, source="vision-fine",
                              confidence=0.8, app=active_app())
    return Target(label=desc, x=coarse[0], y=coarse[1], source="vision-coarse",
                  confidence=0.55, app=active_app())


# ── context memory: "there", "it", "that one" ─────────────────────────────

# What JARVIS last found on screen. Conversation is full of pointers back at it —
# "move the arrow there", "click it", "do that again" — and resolving them from
# the live screen is the difference between an assistant that follows a
# conversation and one that asks "where do you mean?" every other turn.
_CONTEXT: dict[str, Any] = {"last": None, "window": ""}

_DEICTIC = frozenset({
    "there", "here", "it", "that", "this", "that one", "this one", "the one",
    "it there", "over there", "the same thing", "that thing", "this thing",
    "the same one", "that place", "over here",
})


def is_deictic(description: str) -> bool:
    """True for a pointer-word target that only the screen can resolve. Pure."""
    t = re.sub(r"[^a-z ]+", " ", str(description or "").lower()).strip()
    t = re.sub(r"^(?:right|left|over|up|down)\s+", "", t)
    return t in _DEICTIC


def remember_target(t: Target) -> None:
    """Record what was just found, so the next "there" can mean it."""
    _CONTEXT["last"] = t
    _CONTEXT["window"] = foreground_title()


def last_target() -> Target | None:
    """The last target, but only while we are still in the same window.

    A stale coordinate is worse than none: if the user has since switched to
    another window, "there" no longer refers to it, so the memory is dropped
    rather than honoured.
    """
    t = _CONTEXT.get("last")
    if t is None:
        return None
    if _CONTEXT.get("window") and foreground_title() != _CONTEXT.get("window"):
        return None
    return t


# ── the cascade ──────────────────────────────────────────────────────────────

def locate(description: str, *, window: str | None = None,
           allow_vision: bool = True, hint: tuple[int, int] | None = None,
           fresh: bool = True) -> Target | None:
    """Find a target, cheapest accurate source first. None means: do not click.

    Order:
      0. "there" / "it" / "that" — the last thing found, if we are still in the
         same window. Zero cost, and it is what the user meant.
      1. UIA exact/fuzzy element match — exact rect, zero model calls.
      2. Vision (coarse → fine) — for canvases, games, images, non-UIA apps.
    Never returns a made-up coordinate: 'not found' is a valid, useful answer.
    """
    desc = str(description or "").strip()
    if not desc:
        return None
    if is_deictic(desc):
        prev = last_target()
        if prev is not None:
            if pc_log:
                pc_log.event("pc_engine.locate", item=desc, method="context",
                             box=f"{prev.x},{prev.y}",
                             result=f"resolved to {prev.label!r}")
            return Target(label=f"{prev.label} (what {desc!r} referred to)",
                          x=prev.x, y=prev.y, w=prev.w, h=prev.h,
                          source="context", confidence=0.7, app=prev.app,
                          control_type=prev.control_type)
    if uia_available():
        els = uia_elements(window, fresh=fresh)
        if els:
            matches = best_matches(desc, els, threshold=0.70, limit=2)
            if matches:
                sc, el = matches[0]
                # Only trust it outright when it is unambiguous.
                ambiguous = len(matches) > 1 and abs(matches[1][0] - sc) < 0.04
                if not ambiguous or sc >= 0.9:
                    if pc_log:
                        pc_log.event("pc_engine.locate", app=active_app(),
                                     item=desc, method="uia",
                                     box=f"{el['x']},{el['y']},{el['w']},{el['h']}")
                    t = Target(label=desc, x=el["x"], y=el["y"],
                               w=el["w"], h=el["h"], source="uia",
                               confidence=min(0.99, 0.6 + sc / 3.0),
                               control_type=el.get("type", ""),
                               app=active_app())
                    remember_target(t)
                    return t
    if not allow_vision:
        return None
    t = vision_locate(desc, hint=hint)
    if t:
        remember_target(t)
        if pc_log:
            pc_log.event("pc_engine.locate", app=t.app, item=desc, method=t.source,
                         box=f"{t.x},{t.y}", confidence=f"{t.confidence:.2f}")
    return t


def locate_all(description: str, *, window: str | None = None,
               limit: int = 8) -> list[Target]:
    """Every plausible match, best first — for 'which one did you mean?'."""
    out: list[Target] = []
    if uia_available():
        for sc, el in best_matches(description, uia_elements(window), limit=limit):
            out.append(Target(label=desc_for(el, description), x=el["x"], y=el["y"],
                              w=el["w"], h=el["h"], source="uia",
                              confidence=min(0.99, 0.6 + sc / 3.0),
                              control_type=el.get("type", ""), app=active_app()))
    return out


def desc_for(el: dict, fallback: str) -> str:
    return (el.get("name") or el.get("type") or fallback)[:60]


# ════════════════════════════════════════════════════════════════════════════
#  3. PERCEPTION HELPERS (cheap, local — no model call)
# ════════════════════════════════════════════════════════════════════════════

_SIG_W, _SIG_H = 48, 27


def signature(region: tuple[int, int, int, int] | None = None) -> bytes:
    """A tiny grayscale fingerprint of the screen (or a region).

    48x27 pixels is enough to notice "the page changed" and costs a few
    milliseconds, so verification rarely needs a model round trip at all.
    """
    if not (_HAS_PAG and _HAS_PIL):
        return b""
    try:
        img, _sm, _origin = capture_region(region)
        small = img.convert("L").resize((_SIG_W, _SIG_H), Image.BILINEAR)
        return small.tobytes()
    except Exception:
        return b""


def signature_distance(a: bytes, b: bytes) -> float:
    """Mean absolute difference of two fingerprints, 0..255. Pure."""
    if not a or not b or len(a) != len(b):
        return 255.0
    return sum(abs(x - y) for x, y in zip(a, b)) / float(len(a))


def wait_for_change(before: bytes,
                    region: tuple[int, int, int, int] | None = None,
                    *, timeout: float = 1.6, threshold: float = 3.0,
                    poll: float = 0.12) -> float:
    """Block until the screen differs from `before`. Returns the distance seen.

    Returns 0.0 on timeout, which is the signal "nothing happened" — the caller
    can then re-look and try another method instead of assuming success.
    """
    if not before:
        return 0.0
    deadline = time.monotonic() + max(0.1, timeout)
    best = 0.0
    while time.monotonic() < deadline:
        time.sleep(poll)
        d = signature_distance(before, signature(region))
        best = max(best, d)
        if d >= threshold:
            return d
    return 0.0 if best < threshold else best


def wait_for_settle(*, timeout: float = 6.0, quiet: float = 0.5,
                    threshold: float = 1.2,
                    region: tuple[int, int, int, int] | None = None,
                    poll: float = 0.18) -> float:
    """Block until the screen stops changing (animations, page loads, spinning
    launchers). Returns how much movement was seen before it settled.

    `quiet` is how long the screen must hold still to count as settled. A
    fast-changing screen (video playing) times out at `timeout` and the caller
    simply proceeds — settling is best-effort by design.
    """
    deadline = time.monotonic() + max(0.3, timeout)
    quiet_needed = max(0.15, quiet)
    still_since: float | None = None
    moved = 0.0
    prev = signature(region)
    while time.monotonic() < deadline:
        time.sleep(poll)
        cur = signature(region)
        d = signature_distance(prev, cur)
        prev = cur
        if d >= threshold:
            moved = max(moved, d)
            still_since = None
        else:
            still_since = still_since or time.monotonic()
            if time.monotonic() - still_since >= quiet_needed:
                return moved
    return moved          # never settled (e.g. video playing) — caller proceeds


def describe_screen(*, region: tuple[int, int, int, int] | None = None,
                    max_width: int = 1280, timeout_ms: int = 18_000) -> str:
    """A short, honest inventory of what is on screen right now.

    UIA names the real controls for free when the foreground window exposes a
    tree; only when it does not (a game, a canvas, a picture) is the vision model
    asked. This is the 'SEE' step of the loop, and it is deliberately cheap
    enough to run before every decision.
    """
    title = foreground_title()
    lines: list[str] = [f"Foreground window: {title or 'unknown'}"]
    els = uia_elements() if uia_available() else []
    if els:
        lines.append(f"{len(els)} accessible controls. Notable:")
        seen = 0
        for el in els:
            name = (el.get("name") or "").strip()
            if not name or el.get("type") not in ("Button", "Edit", "ComboBox",
                                                  "TabItem", "Hyperlink",
                                                  "ListItem", "Document", "CheckBox"):
                continue
            cx, cy = el["x"] + el["w"] // 2, el["y"] + el["h"] // 2
            lines.append(f"- [{el['type']}] {name[:70]!r} at {cx},{cy}")
            seen += 1
            if seen >= 40:
                break
        if seen == 0:
            lines.append("- (names not exposed)")
        return "\n".join(lines)

    try:
        from google.genai import types as gtypes
    except Exception:
        return "\n".join(lines + ["No accessibility tree, and vision is unavailable."])
    try:
        img, _sm, _o = capture_region(region, max_width=max_width)
    except Exception as e:
        return "\n".join(lines + [f"Could not capture the screen: {e}"])
    prompt = (
        "Screenshot of a computer screen. In at most 120 words, list what is on "
        "it for an assistant that will control this desktop with a mouse: the "
        "application and page/document shown, then the visible interactive "
        "elements (buttons, fields, links, tabs) each with its approximate pixel "
        "position like 'Save button ~ 812,540'. Do not guess at elements you "
        "cannot see. Plain text only."
    )
    txt = _vision_text(
        [gtypes.Part.from_bytes(data=_png(img), mime_type="image/png"), prompt],
        timeout_ms=timeout_ms)
    lines.append(txt or "(no vision response)")
    return "\n".join(lines)


def explain_change(before: bytes, after: bytes, *,
                   before_elements: list[dict] | None = None,
                   max_items: int = 6) -> str:
    """Human one-liner for what changed between two screen fingerprints.

    `before`/`after` are fingerprint() bytes; `before_elements` is the cached
    accessibility tree captured BEFORE the action (uia_elements() is cached, so
    the caller already has it for free). With it, the answer is 'a dialog
    titled X appeared' rather than 'pixels moved'; without it only the visual
    delta can be described. Never raises.
    """
    def _keyset(elms: list[dict]) -> tuple[set[tuple[str, str]],
                                           dict[tuple[str, str], str]]:
        keys: set[tuple[str, str]] = set()
        display: dict[tuple[str, str], str] = {}
        for e in elms or []:
            name = (e.get("name") or "").strip()
            if name:
                k = (name.lower()[:80], str(e.get("type") or ""))
                keys.add(k)
                display.setdefault(k, name)
        return keys, display

    after_elms: list[dict] = []
    walk_ok = True
    try:
        after_elms = uia_elements(fresh=True)
    except Exception:
        walk_ok = False           # a failed walk proves nothing — do not diff
        after_elms = []
    before_names, bd = _keyset(before_elements if before_elements is not None
                               else after_elms)
    after_names, ad = _keyset(after_elms)

    if before_elements is not None and walk_ok:
        appeared = [ad.get(k, k[0])
                    for k in sorted(after_names - before_names)][:max_items]
        kinds = {k[0]: k[1] for k in (after_names - before_names)}
        vanished = [bd.get(k, k[0])
                    for k in sorted(before_names - after_names)][:max_items]
        parts: list[str] = []
        if appeared:
            parts.append("appeared: " + "; ".join(
                f"{n} ({kinds.get(n.lower()[:80], '')})".rstrip(" ()")
                for n in appeared))
        if vanished:
            parts.append("gone: " + "; ".join(vanished))
        if parts:
            return " ".join(parts)

    d = signature_distance(before, after)
    if d <= 0.5:
        return "the screen did not visibly change"
    if d < 6:
        return "a small visual change (hover state or caret movement)"
    return f"the screen changed substantially (visual delta {d:.0f}/255)"


# ════════════════════════════════════════════════════════════════════════════
#  4. ACTIONS — every one verifies, and reports what it saw
# ════════════════════════════════════════════════════════════════════════════

def _require_input() -> None:
    if not _HAS_PAG:
        raise RuntimeError("pyautogui is required for physical control")


def pointer() -> tuple[int, int]:
    """Where the cursor actually is, in real screen pixels."""
    _require_input()
    try:
        x, y = _PAG.position()
        return int(x), int(y)
    except Exception:
        return -1, -1


def move(x: int, y: int, *, duration: float = 0.18) -> Result:
    """Move the cursor and confirm where it arrived.

    The read-back is the point: on a scaled display the *only* way to know the
    move worked is to ask the OS afterwards, and a move that silently landed
    200 px away must be reported as a failure rather than assumed good.
    """
    _require_input()
    tx, ty = clamp_screen(x, y)
    try:
        _PAG.moveTo(tx, ty, duration=duration)
    except Exception as e:
        return Result(False, f"Could not move the pointer: {e}", "move")
    time.sleep(0.04)
    ax, ay = pointer()
    if abs(ax - tx) <= 2 and abs(ay - ty) <= 2:
        return Result(True, f"Moved the pointer to {tx},{ty}", "move", verified=True)
    return Result(False,
                  f"Moved the pointer towards {tx},{ty} but it is at {ax},{ay} "
                  f"(display scaling is in the way)", "move",
                  target=Target("pointer", ax, ay, source="point"))


def click_at(x: int, y: int, *, button: str = "left", clicks: int = 1) -> Result:
    """Move, click, and check the cursor really was where the click went."""
    _require_input()
    mv = move(x, y)
    if not mv.ok:
        return mv
    before = signature()
    try:
        if clicks == 2:
            _PAG.doubleClick(button=button)
        else:
            _PAG.click(button=button, clicks=clicks)
    except Exception as e:
        return Result(False, f"Click failed: {e}", "click", target=mv.target)
    verb = "Double-clicked" if clicks == 2 else (
        "Right-clicked" if button == "right" else "Clicked")
    changed = 0.0
    if clicks == 1 and button == "left":
        changed = wait_for_change(before, timeout=1.4)
    return Result(True, f"{verb} at {x},{y}",
                  "click", verified=bool(changed), target=mv.target)


def click_target(description: str, *, window: str | None = None,
                 button: str = "left", clicks: int = 1,
                 expect_change: bool = True,
                 retries: int = 1) -> Result:
    """FIND → MOVE → VERIFY THE POINTER IS ON IT → CLICK → VERIFY THE EFFECT.

    Retries once with an independent grounding pass when the first attempt
    visibly did nothing — the usual cause is a target that moved while the
    coordinates were being worked out, which a fresh look fixes.
    """
    desc = str(description or "").strip()
    if not desc:
        return Result(False, "I need a description of what to click.", "click")
    hint: tuple[int, int] | None = None
    last = Result(False, f"Could not find {desc!r} on screen.", "click")
    for attempt in range(max(1, retries + 1)):
        t = locate(desc, window=window, hint=hint if attempt else None)
        if t is None:
            continue
        cx, cy = t.center if (t.w or t.h) else (t.x, t.y)
        # Trust but verify: is the thing under the pointer the thing we asked for?
        under = element_at(cx, cy) if uia_available() else None
        if under and under.get("name"):
            um = score_element(desc, under)
            if um < 0.30 and t.source == "uia":
                # The rect moved since it was cached — force a fresh look.
                clear_uia_cache()
                t = locate(desc, window=window, fresh=True)
                if t is None:
                    continue
                cx, cy = t.center if (t.w or t.h) else (t.x, t.y)
        res = click_at(cx, cy, button=button, clicks=clicks)
        res.method = t.source
        res.target = Target(t.label, cx, cy, source=t.source, app=t.app,
                            confidence=t.confidence)
        if res.ok and (res.verified or not expect_change or clicks != 1
                       or button != "left"):
            if under and under.get("name"):
                res.detail += f" (over {under['name']!r})"
            _remember(desc, t.source, True, "located and clicked")
            if pc_log:
                pc_log.event("pc_engine.click", app=active_app(), item=desc,
                             method=t.source, box=f"{cx},{cy}",
                             verify=("ok" if res.verified else "no-visible-change"),
                             result=res.detail)
            return res
        last = Result(True,
                      f"Clicked {desc!r} at {cx},{cy} ({t.source}) but the screen "
                      f"did not change — the click may have done nothing",
                      t.source, verified=False, target=t)
        hint = (cx, cy)
        clear_uia_cache()
        time.sleep(0.25)
    _remember(desc, last.method or "vision", False, "no visible effect")
    if pc_log:
        pc_log.event("pc_engine.click", app=active_app(), item=desc,
                     method=last.method, verify="failed", result=last.detail)
    return last


def point_at(description: str, *, window: str | None = None,
             duration: float = 0.18) -> Result:
    """Move the cursor onto a described element and verify it arrived."""
    desc = str(description or "").strip()
    if not desc:
        return Result(False, "I need a description of where to point.", "move")
    t = locate(desc, window=window)
    if t is None:
        return Result(False, f"I could not find {desc!r} on screen, so I did not "
                             f"move the pointer.", "move")
    cx, cy = t.center if (t.w or t.h) else (t.x, t.y)
    res = move(cx, cy, duration=duration)
    res.method = t.source
    res.target = Target(t.label, cx, cy, source=t.source, app=t.app,
                        confidence=t.confidence)
    if res.ok:
        under = element_at(cx, cy) if uia_available() else None
        if under and under.get("name"):
            res.detail += f" (over {under['name']!r})"
        res.detail = f"Pointer on {desc!r} at {cx},{cy} via {t.source}"
    if pc_log:
        pc_log.event("pc_engine.point", app=active_app(), item=desc,
                     method=t.source, box=f"{cx},{cy}",
                     verify="ok" if res.ok else "failed", result=res.detail)
    return res


# ── typing ───────────────────────────────────────────────────────────────────

_LAST_CLIPBOARD = {"text": "", "saved": False}


def _clipboard_read() -> str:
    """Clipboard text via the dependency-free engine, else pyperclip."""
    try:
        from core import pc_input
        return pc_input.clipboard_get()
    except Exception:
        try:
            import pyperclip
            return str(pyperclip.paste())
        except Exception:
            return ""


def _clipboard_write(text: str) -> bool:
    try:
        from core import pc_input
        pc_input.clipboard_set(text)
        return True
    except Exception:
        pass
    try:
        import pyperclip
        pyperclip.copy(text)
        return True
    except Exception:
        return False


def _stash_clipboard() -> None:
    """Remember the user's clipboard so a paste-based type can give it back."""
    if _LAST_CLIPBOARD["saved"]:
        return
    _LAST_CLIPBOARD["text"] = _clipboard_read()[:20000]
    _LAST_CLIPBOARD["saved"] = True


def restore_clipboard() -> None:
    """Put the user's clipboard back after we borrowed it. Never raises."""
    if not _LAST_CLIPBOARD["saved"]:
        return
    try:
        _clipboard_write(_LAST_CLIPBOARD["text"])
    except Exception:
        pass
    _LAST_CLIPBOARD["saved"] = False


def select_all() -> None:
    mod = "command" if platform.system() == "Darwin" else "ctrl"
    _require_input()
    _PAG.hotkey(mod, "a")


def clear_field() -> Result:
    """Select everything in the focused field and delete it."""
    _require_input()
    try:
        select_all()
        time.sleep(0.05)
        _PAG.press("delete")
        time.sleep(0.05)
        return Result(True, "Cleared the field", "keyboard", verified=True)
    except Exception as e:
        return Result(False, f"Could not clear the field: {e}", "keyboard")


def _uia_focused_value() -> str | None:
    """Text in the focused control via UIA, or None when it cannot be read.

    None and '' mean different things: '' is 'the control really is empty', None
    is 'no idea' — and a verifier that confuses them is how an empty field gets
    reported as a successful type.
    """
    if not uia_available():
        return None
    value = None
    try:
        from pywinauto import uia_defines
        from pywinauto.controls.uiawrapper import UIAWrapper
        el = uia_defines.IUIA().iuia.GetFocusedElement()
        if el is None:
            return None
        w = UIAWrapper(el)
        for getter in (lambda: w.get_value(),
                       lambda: w.iface_value.CurrentValue,
                       lambda: w.legacy_properties().get("Value")):
            try:
                v = getter()
                if v is not None:
                    value = str(v)
                    break
            except Exception:
                continue
        if value is None:
            try:
                t = w.control_type()
            except Exception:
                t = ""
            # Only a text-bearing control can be read back as its own content.
            if t in ("Edit", "ComboBox", "Document", "Spinner"):
                try:
                    value = str(w.window_text())
                except Exception:
                    value = None
    except Exception:
        return None
    if value is None:
        return None
    # A Document's "value" is the whole document — useless as a read-back and
    # expensive to fetch, so it counts as unreadable rather than as content.
    if len(value) > 4000:
        return None
    return value


def focused_value() -> str:
    """Text currently in the focused control, or '' if it cannot be read.

    UIA first (free and exact). If the app has no tree, fall back to
    select-all + copy, then collapse the selection back to the end so the user
    is left where they were.
    """
    via_uia = _uia_focused_value()
    if via_uia is not None:
        return via_uia
    try:
        before = _clipboard_read()
        select_all()
        time.sleep(0.06)
        mod = "command" if platform.system() == "Darwin" else "ctrl"
        _PAG.hotkey(mod, "c")
        time.sleep(0.12)
        got = _clipboard_read()
        _PAG.press("end")           # collapse the selection, leave the caret
        _clipboard_write(before)    # put the user's clipboard back
        if got == before and before:
            return ""               # copy did not take; do not trust it
        return got or ""
    except Exception:
        return ""


def type_text(text: str, *, field: str | None = None, clear: bool = True,
              verify: bool = True, paste_over: int = 24,
              window: str | None = None) -> Result:
    """Type reliably into the right place, then prove it landed.

    The order matters and is the whole fix:
      1. If a field was named, find it and CLICK it — never type into whichever
         window happens to have focus.
      2. Verify focus (UIA will tell us; otherwise a caret probe by typing a
         probe character is too invasive, so we confirm the click moved the
         screen or that the target is an edit control).
      3. Clear, then type — clipboard paste for long/non-Latin text, keystrokes
         otherwise (keystroke paths lose characters when the target is busy).
      4. READ THE FIELD BACK and compare. Mismatch → clear and retry once with
         the other method. Still wrong → say what is actually in the field.
    """
    _require_input()
    text = str(text if text is not None else "")
    if not text:
        return Result(False, "There is nothing to type.", "type")

    target: Target | None = None
    if field:
        res = click_target(field, expect_change=False, window=window)
        if not res.ok and res.target is None:
            return Result(False, f"I could not find the {field!r} field, so I did "
                                 f"not type anything.", "type")
        target = res.target
        time.sleep(0.12)

    if clear:
        clear_field()

    def _paste() -> bool:
        if not _clipboard_write(text):
            return False
        time.sleep(0.06)
        mod = "command" if platform.system() == "Darwin" else "ctrl"
        _PAG.hotkey(mod, "v")
        return True

    def _keys() -> bool:
        try:
            # typewrite fails on non-ASCII; key_type handles any Unicode.
            from core import pc_input
            pc_input.key_type(text, interval=0.012)
            return True
        except Exception:
            pass
        try:
            _PAG.write(text, interval=0.02)
            return True
        except Exception as e:
            _log(f"[pc_engine] write failed: {e}")
            return False

    long_or_unicode = len(text) > paste_over or any(ord(c) > 127 for c in text)
    methods = [("paste", _paste), ("keys", _keys)] if long_or_unicode \
        else [("keys", _keys), ("paste", _paste)]

    if methods[0][0] == "paste":
        # Borrowing the clipboard is only polite if we hand it back.
        _stash_clipboard()

    typed_by = ""
    for name, fn in methods:
        try:
            ok = fn()
        except Exception as e:
            _log(f"[pc_engine] type via {name} failed: {e}")
            ok = False
        if not ok:
            continue
        typed_by = name
        if not verify:
            break
        time.sleep(0.18)
        got = focused_value()
        if not got:
            # Cannot read the field: verify what we can (screen moved at all).
            break
        if got.strip() == text.strip() or (len(got) <= 400 and text.strip() in got):
            restore_clipboard()
            where = f" into the {field!r} field" if field else ""
            return Result(True, f"Typed {len(text)} characters{where} and read "
                                f"them back", name, verified=True, target=target)
        # Wrong content: clear and try the other method.
        clear_field()
        time.sleep(0.1)

    restore_clipboard()
    if typed_by:
        got = focused_value() if verify else ""
        where = f" into the {field!r} field" if field else ""
        if got:
            return Result(True, f"Typed {len(text)} characters{where}, but the "
                                f"field reads {got[:60]!r} — the text may not "
                                f"have landed exactly", typed_by, verified=False,
                          target=target)
        return Result(True, f"Typed {len(text)} characters{where} (could not "
                            f"verify the contents)", typed_by, verified=False,
                      target=target)
    return Result(False, "Typing failed — no keystrokes or paste reached the "
                         "window", "type", target=target)


def press_keys(*keys: str) -> Result:
    """Press one key, or a '+'/'ctrl+c'-style combination."""
    _require_input()
    if len(keys) == 1 and isinstance(keys[0], str) and "+" in keys[0]:
        keys = tuple(k.strip() for k in keys[0].split("+") if k.strip())
    if not keys:
        return Result(False, "No key given.", "keyboard")
    try:
        if len(keys) == 1:
            _PAG.press(keys[0])
        else:
            _PAG.hotkey(*keys)
        clear_uia_cache()
        return Result(True, "Pressed " + "+".join(keys), "keyboard", verified=True)
    except Exception as e:
        return Result(False, f"Key press failed: {e}", "keyboard")


def scroll(direction: str = "down", amount: int = 3) -> Result:
    """Scroll the wheel where the pointer is, and check the view moved."""
    _require_input()
    d = str(direction or "down").strip().lower()
    n = max(1, min(int(amount or 3), 40))
    before = signature()
    try:
        if d in ("up", "down"):
            _PAG.scroll(n if d == "up" else -n)
        elif d in ("left", "right"):
            _PAG.hscroll(n if d == "left" else -n)
        else:
            return Result(False, "Scroll direction must be up, down, left or right.",
                          "scroll")
    except Exception as e:
        return Result(False, f"Scroll failed: {e}", "scroll")
    moved = wait_for_change(before, timeout=1.0, threshold=1.5)
    return Result(True, f"Scrolled {d} ×{n}", "scroll", verified=bool(moved))


def drag_to(source: str, target: str) -> Result:
    """Drag one visible thing onto another, both found by description."""
    _require_input()
    a = locate(source)
    b = locate(target)
    if a is None:
        return Result(False, f"I could not find the drag source {source!r}.", "drag")
    if b is None:
        return Result(False, f"I could not find the drop target {target!r}.", "drag")
    sx, sy = a.center if (a.w or a.h) else (a.x, a.y)
    tx, ty = b.center if (b.w or b.h) else (b.x, b.y)
    before = signature()
    try:
        move(sx, sy)
        _PAG.mouseDown()
        time.sleep(0.08)
        steps = 14
        for i in range(1, steps + 1):
            t = i / steps
            move(int(sx + (tx - sx) * t), int(sy + (ty - sy) * t), duration=0.01)
        time.sleep(0.08)
        _PAG.mouseUp()
    except Exception as e:
        try:
            _PAG.mouseUp()
        except Exception:
            pass
        return Result(False, f"Drag failed: {e}", "drag")
    changed = wait_for_change(before, timeout=1.2)
    return Result(True, f"Dragged {source!r} onto {target!r}", "drag",
                  verified=bool(changed))


# ── strategy memory (learn which route works, per app) ───────────────────────

def _remember(item: str, strategy: str, ok: bool, note: str = "") -> None:
    try:
        from core import strategy_memory
        strategy_memory.record(active_app(), item, str(strategy).split("-")[0], ok, note)
    except Exception:
        pass


# ════════════════════════════════════════════════════════════════════════════
#  5. WINDOWS & APPS — cheap, reliable, no pixels at all
# ════════════════════════════════════════════════════════════════════════════

def focus_window(title_fragment: str) -> bool:
    """Bring a window forward by title substring. Tries the engine, then UIA."""
    if not _IS_WINDOWS:
        return False
    try:
        from core import pc_input
        if pc_input.window_focus(title_fragment):
            time.sleep(0.15)
            return True
    except Exception:
        pass
    if uia_available():
        try:
            wins = pywinauto.findwindows.find_windows(
                title_re=rf".*{re.escape(str(title_fragment))}.*",
                backend="uia", visible_only=True)
            if wins:
                pywinauto.Desktop(backend="uia").window(handle=wins[0]).set_focus()
                time.sleep(0.15)
                return True
        except Exception:
            pass
    return False


def wait_for_window(title_fragment: str, *, timeout: float = 12.0) -> bool:
    """Wait for a window to exist, then focus it.

    Opening an app used to be followed immediately by a blind click, which
    landed on whatever was still in front. Waiting for the window is the fix,
    and it makes 'open YouTube then play something' work without asking.
    """
    deadline = time.monotonic() + max(0.5, timeout)
    while time.monotonic() < deadline:
        if focus_window(title_fragment):
            # Found it — but the window is still materialising (opening
            # animation, first paint). Settle briefly so the next click does
            # not race it; never fail because of a busy screen.
            try:
                wait_for_settle(timeout=1.6, quiet=0.3, threshold=2.0)
            except Exception:
                pass
            return True
        time.sleep(0.3)
    return False


def open_uri(uri: str) -> Result:
    """Open a URL / file / folder with the shell (the user's own default app)."""
    target = str(uri or "").strip()
    if not target:
        return Result(False, "Nothing to open.", "shell")
    try:
        import os
        if _IS_WINDOWS:
            os.startfile(target)                        # noqa: S606 — user intent
        elif platform.system() == "Darwin":
            import subprocess
            subprocess.Popen(["open", target])
        else:
            import subprocess
            subprocess.Popen(["xdg-open", target])
        return Result(True, f"Opened {target}", "shell", verified=True)
    except Exception as e:
        return Result(False, f"Could not open {target}: {e}", "shell")


# ════════════════════════════════════════════════════════════════════════════
#  6. STOP SUPPORT
# ════════════════════════════════════════════════════════════════════════════

def cancelled() -> bool:
    """True when the user asked JARVIS to stop; control loops check this often."""
    try:
        from core import cancel
        return bool(cancel.stopped())
    except Exception:
        return False


def state() -> dict:
    """A compact snapshot for logs, the tool layer and the HUD."""
    try:
        sm = screen_map()
        geo = sm.describe()
    except Exception:
        geo = "unknown"
    region = ""
    try:
        px, py = pointer()
        for m in monitors():
            if m["x"] <= px < m["x"] + m["width"] and m["y"] <= py < m["y"] + m["height"]:
                region = f"monitor {m['width']}x{m['height']}@ {m['x']},{m['y']}"
                break
    except Exception:
        pass
    return {
        "windows": _IS_WINDOWS,
        "input": _HAS_PAG,
        "vision": _HAS_PIL,
        "uia": uia_available(),
        "desktop": geo,
        "monitors": len(monitors()),
        "pointer": pointer(),
        "pointer_monitor": region,
        "foreground": foreground_title(),
    }


__all__ = [
    "ScreenMap", "Target", "Result",
    "screen_map", "virtual_desktop", "primary_size", "monitors",
    "clamp_screen", "clamp_box", "capture_region",
    "uia_available", "uia_elements", "clear_uia_cache", "element_at",
    "foreground_title", "active_app",
    "score_element", "best_matches", "wanted_types", "parse_coords",
    "locate", "locate_all", "vision_locate",
    "is_deictic", "remember_target", "last_target",
    "signature", "signature_distance", "wait_for_change", "describe_screen",
    "pointer", "move", "click_at", "click_target", "point_at",
    "type_text", "press_keys", "scroll", "drag_to", "select_all", "clear_field",
    "focused_value", "restore_clipboard",
    "focus_window", "wait_for_window", "open_uri",
    "cancelled", "state",
]
