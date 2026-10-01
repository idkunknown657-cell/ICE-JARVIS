"""
core/window_context.py — what is on screen right now, and just that.

WHY THIS EXISTS
    "Summarise this", "what does this error mean", "reply to this" — a large share
    of real requests are about the window the person is looking at, and until now
    the only way to answer one was to screenshot the whole desktop and let the
    model work out which part mattered. Which window is focused, what it is
    called, and where its rectangle is on a multi-monitor desktop is cheap to
    read and turns a vague answer into a specific one.

WHY IT IS A SEPARATE MODULE
    core/screen_observer.py watches the screen on a schedule and core/screen_ai.py
    drives it. Both need to know the same two things before they can do anything
    targeted, and neither should own the Win32 plumbing. So: one file that answers
    "which window, and where", with no policy about when to ask, no thread, and no
    side effects.

THE TWO ENTRY POINTS
    foreground()           title, class, process id, and the window's rectangle.
    capture(window_only=True)  a JPEG of just the focused window, or of the whole
                           desktop, as bytes ready to hand to the model.

PLATFORMS
    The window handle work is Win32-only and returns an empty-but-valid result
    everywhere else — the same shape, so a caller never has to branch, and nothing
    raises on a machine without user32. The screenshot half uses PIL, which is a
    declared dependency of this project.

NOTHING HERE RAISES. A window that has closed between the two calls, a
minimised window with no rectangle, or a headless machine all produce a result
with empty fields, which the caller renders as "I could not tell what is on
screen" rather than as a traceback.
"""
from __future__ import annotations

import io
import sys

_IS_WINDOWS = sys.platform == "win32"

#: A window smaller than this is a tooltip, a shadow or a hidden helper window —
#: capturing it produces an image of nothing and a confusing answer.
_MIN_USEFUL_SIZE = 40


def _user32():
    """The Win32 library, or None. Imported lazily so this module can be imported
    on any platform and in tests without loading ctypes.windll."""
    if not _IS_WINDOWS:
        return None
    try:
        import ctypes
        return ctypes.windll.user32
    except Exception:
        return None


def _empty(**over) -> dict:
    data = {"title": "", "class_name": "", "pid": 0, "hwnd": 0,
            "rect": (0, 0, 0, 0), "bbox": None, "available": False}
    data.update(over)
    return data


def foreground() -> dict:
    """Metadata about the focused window. Never raises.

    `available` says whether the reading is real. It exists so a caller can tell
    "the desktop is empty" apart from "this platform cannot tell me", which are
    very different things to say to a user.
    """
    user32 = _user32()
    if user32 is None:
        return _empty(reason="window details are only available on Windows")

    try:
        import ctypes
        from ctypes import wintypes

        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return _empty(reason="no window has focus")

        title = ""
        try:
            length = user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buffer = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buffer, length + 1)
                title = (buffer.value or "").strip()
        except Exception:
            pass

        class_name = ""
        try:
            class_buffer = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, class_buffer, 256)
            class_name = (class_buffer.value or "").strip()
        except Exception:
            pass

        pid = 0
        try:
            holder = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(holder))
            pid = int(holder.value)
        except Exception:
            pass

        rect = (0, 0, 0, 0)
        bbox = None
        try:
            class _RECT(ctypes.Structure):
                _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                            ("right", wintypes.LONG), ("bottom", wintypes.LONG)]
            box = _RECT()
            if user32.GetWindowRect(hwnd, ctypes.byref(box)):
                width = max(0, box.right - box.left)
                height = max(0, box.bottom - box.top)
                rect = (box.left, box.top, width, height)
                if width >= _MIN_USEFUL_SIZE and height >= _MIN_USEFUL_SIZE:
                    bbox = (box.left, box.top, box.right, box.bottom)
        except Exception:
            pass

        return {"title": title, "class_name": class_name, "pid": pid,
                "hwnd": int(hwnd), "rect": rect, "bbox": bbox, "available": True,
                "process": _process_name(pid)}

    except Exception as exc:                                # pragma: no cover
        return _empty(reason=f"could not read the window: {exc}")


def _process_name(pid: int) -> str:
    """The executable behind a window, which is far more useful to say out loud
    than a window class name. Best-effort: '' when anything about it fails."""
    if not pid:
        return ""
    try:
        import psutil
        return str(psutil.Process(pid).name() or "")
    except Exception:
        return ""


def describe() -> str:
    """One spoken sentence about the focused window, or '' when there is nothing
    worth saying. Used by tools that want context without a wall of fields."""
    info = foreground()
    if not info.get("available"):
        return ""
    title = info.get("title") or ""
    app = info.get("process") or ""
    if not title and not app:
        return ""
    if app and title:
        return f"You are in {app}, window titled \"{title}\"."
    if title:
        return f"The window in front is titled \"{title}\"."
    return f"The application in front is {app}."


def capture(window_only: bool = True, quality: int = 80) -> bytes | None:
    """A JPEG of the focused window, or of the whole desktop. None on failure.

    `window_only` falls back to the full desktop when the window has no usable
    rectangle — a maximised window on a rotated monitor, or a window that closed
    between the two calls. Returning a picture of the desktop is more useful than
    returning nothing, and the caller can say which it got.
    """
    try:
        from PIL import ImageGrab
    except Exception:
        return None

    bbox = foreground().get("bbox") if window_only else None

    try:
        # ImageGrab raises rather than clamping when a window has been dragged
        # partly off the desktop, so the fallback to a full grab is the recovery
        # path rather than an afterthought.
        image = ImageGrab.grab(bbox=bbox) if bbox else ImageGrab.grab()
    except Exception:
        # A window dragged partly off the desktop makes ImageGrab raise rather
        # than clamp, so falling back to a full grab is the recovery path.
        if not bbox:
            print("[WindowContext] the screen could not be read")
            return None
        try:
            image = ImageGrab.grab()
        except Exception as e:
            print(f"[WindowContext] capture failed: {e}")
            return None

    try:
        buffer = io.BytesIO()
        image.convert("RGB").save(buffer, format="JPEG", quality=int(quality))
        return buffer.getvalue()
    except Exception as e:
        print(f"[WindowContext] could not encode the capture: {e}")
        return None


def context_for_prompt() -> str:
    """A compact block for the model: which app, which window, and how big. Kept
    to two lines because it may ride in every turn's context."""
    info = foreground()
    if not info.get("available"):
        return ""
    parts = []
    if info.get("process"):
        parts.append(f"app={info['process']}")
    if info.get("title"):
        parts.append(f"window=\"{info['title'][:120]}\"")
    rect = info.get("rect") or (0, 0, 0, 0)
    if rect[2] and rect[3]:
        parts.append(f"size={rect[2]}x{rect[3]} at ({rect[0]},{rect[1]})")
    if not parts:
        return ""
    return "[ACTIVE WINDOW] " + ", ".join(parts)


__all__ = ["foreground", "describe", "capture", "context_for_prompt"]
