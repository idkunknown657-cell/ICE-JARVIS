"""
core/pc_input.py — dependency-free Windows input & PC control engine.

WHY THIS EXISTS
    The distributed ICE.exe ships WITHOUT pyautogui / pyperclip / pycaw /
    comtypes / pywin32 (deliberately excluded — see ICE.spec). That left the
    two computer-control tools (computer_control, computer_settings) with no
    working backend inside the exe: every physical action died on
    "_require_pyautogui()". This module is that backend, built on ctypes +
    SendInput + the Win32 API, so the exe can move the mouse, type Hindi, click
    buttons, manage windows and processes with ZERO extra packages.

WHAT IT PROVIDES
    1. A plain engine API:  mouse_position / mouse_move / mouse_click /
       mouse_drag / mouse_scroll, key_press / key_hotkey / key_type,
       clipboard_get / clipboard_set, window_list / window_focus / window_*,
       process_list / process_start / process_kill, volume_* / brightness_*,
       media_*, lock_screen, sleep_display, screen_size, system_info.
    2. A pyautogui-compatible surface (press / hotkey / scroll / hscroll /
       write / typewrite / click / moveTo / dragTo / size / position /
       screenshot) so existing action files can simply do:

           if not _PYAUTOGUI:
               from core import pc_input as pyautogui

       and every existing call site keeps working — nothing to rewrite.

    Windows is the target; on macOS/Linux the physical functions raise a clear
    RuntimeError instead of pretending, and read-only helpers degrade to
    psutil / pyperclip where they still make sense.

SAFETY
    Nothing is hidden. window_close sends WM_CLOSE (the same as pressing the X),
    process_kill is gated behind core/confirm.py at the action layer — lethal
    actions must be seen coming.
"""

from __future__ import annotations

import ctypes
import os
import platform
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

_IS_WINDOWS = sys.platform == "win32"

_HAS_PYPERCLIP = False
try:
    import pyperclip            # noqa: F401 — preferred when present (exe: absent)
    _HAS_PYPERCLIP = True
except ImportError:
    pass

_HAS_PSUTIL = False
try:
    import psutil               # noqa: F401 — declared in requirements
    _HAS_PSUTIL = True
except ImportError:
    pass

_HAS_PIL = False
try:
    from PIL import ImageGrab   # noqa: F401 — pillow is collected into the exe
    _HAS_PIL = True
except ImportError:
    pass

_HAS_MSS = False
try:
    import mss                  # noqa: F401 — collected into the exe
    _HAS_MSS = True
except ImportError:
    pass

_HAS_PYCAW = False
try:
    from ctypes import cast, POINTER
    from comtypes import CLSCTX_ALL
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
    _HAS_PYCAW = True
except ImportError:
    pass


def _windows() -> "ctypes.WinDLL | None":
    if _IS_WINDOWS:
        try:
            return ctypes.windll.user32
        except Exception:
            return None
    return None


class _WinNotSupported(RuntimeError):
    """Raised for physical input on non-Windows hosts."""


def _need_windows(what: str) -> None:
    if not _IS_WINDOWS:
        raise _WinNotSupported(
            f"pc_input.{what} is Windows-only (uses SendInput / user32). "
            "On Mac/Linux the app falls back to pyautogui when it is installed."
        )


# ── small structs for SendInput ───────────────────────────────────────────────

_LONG   = wintypes.LONG
_DWORD  = wintypes.DWORD
_WORD   = wintypes.WORD
_PTR    = ctypes.POINTER(ctypes.c_ulong)


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", _LONG), ("dy", _LONG),
        ("mouseData", _DWORD), ("dwFlags", _DWORD),
        ("time", _DWORD), ("dwExtraInfo", _PTR),
    ]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", _WORD), ("wScan", _WORD),
        ("dwFlags", _DWORD), ("time", _DWORD), ("dwExtraInfo", _PTR),
    ]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", _DWORD), ("wParamL", _WORD), ("wParamH", _WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT), ("hi", _HARDWAREINPUT)]


class _INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", _DWORD), ("u", _INPUTUNION)]


_INPUT_MOUSE = 0
_INPUT_KEYBD = 1

# MOUSEINPUT flags
_MF_MOVE          = 0x0001
_MF_LEFTDOWN      = 0x0002
_MF_LEFTUP        = 0x0004
_MF_RIGHTDOWN     = 0x0008
_MF_RIGHTUP       = 0x0010
_MF_MIDDLEDOWN    = 0x0020
_MF_MIDDLEUP      = 0x0040
_MF_XDOWN         = 0x0080
_MF_XUP           = 0x0100
_MF_WHEEL         = 0x0800
_MF_HWHEEL        = 0x1000
_MF_ABSOLUTE      = 0x8000
_MF_VIRTUALDESK   = 0x4000   # normalise over the whole virtual desktop (all monitors)

# KEYBDINPUT flags
_KF_EXTENDEDKEY   = 0x0001
_KF_KEYUP         = 0x0002
_KF_UNICODE       = 0x0004

_WHEEL_DELTA = 120

# ── key-name → virtual-key table (pyautogui-compatible names included) ────────

_KEY_MAP: dict[str, int] = {
    "esc": 0x1B, "escape": 0x1B,
    "tab": 0x09, "space": 0x20, "spacebar": 0x20,
    "enter": 0x0D, "return": 0x0D,
    "backspace": 0x08, "delete": 0x2E, "del": 0x2E,
    "insert": 0x2D, "ins": 0x2D,
    "home": 0x24, "end": 0x23,
    "pageup": 0x21, "pgup": 0x21, "pagedown": 0x22, "pgdn": 0x22,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "shift": 0x10, "ctrl": 0x11, "control": 0x11,
    "alt": 0x12, "menu": 0x12, "option": 0x12,
    "win": 0x5B, "lwin": 0x5B, "rwin": 0x5C,
    "cmd": 0x5B, "command": 0x5B, "super": 0x5B, "windows": 0x5B,
    "capslock": 0x14, "numlock": 0x90, "scrolllock": 0x91, "scroll": 0x91,
    "print_screen": 0x2C, "prtsc": 0x2C, "snapshot": 0x2C,
    "pause": 0x13, "break": 0x13,
    "volumeup": 0xAF, "volume_up": 0xAF,
    "volumedown": 0xAE, "volume_down": 0xAE,
    "volumemute": 0xAD, "volume_mute": 0xAD,
    "nexttrack": 0xB0, "prevtrack": 0xB1, "stop": 0xB2, "playpause": 0xB3,
    "bracketleft": 0xDB, "leftbracket": 0xDB, "[": 0xDB,
    "bracketright": 0xDD, "rightbracket": 0xDD, "]": 0xDD,
    "backslash": 0xDC, "\\": 0xDC,
    "semicolon": 0xBA, ";": 0xBA,
    "apostrophe": 0xDE, "quote": 0xDE, "'": 0xDE,
    "comma": 0xBC, ",": 0xBC,
    "period": 0xBE, "fullstop": 0xBE, ".": 0xBE,
    "slash": 0xBF, "forwardslash": 0xBF, "/": 0xBF,
    "minus": 0xBD, "-": 0xBD,
    "equal": 0xBB, "equals": 0xBB, "=": 0xBB,
    "grave": 0xC0, "backtick": 0xC0, "`": 0xC0,
    "num0": 0x60, "num1": 0x61, "num2": 0x62, "num3": 0x63, "num4": 0x64,
    "num5": 0x65, "num6": 0x66, "num7": 0x67, "num8": 0x68, "num9": 0x69,
    "add": 0x6B, "subtract": 0x6D, "multiply": 0x6A, "divide": 0x6F, "decimal": 0x6E,
}

for _i in range(10):
    _KEY_MAP[str(_i)] = 0x30 + _i                      # "0".."9"
for _c in range(26):
    _ch = chr(ord("a") + _c)
    _KEY_MAP[_ch] = 0x41 + _c                          # "a".."z"
for _f in range(1, 25):
    _KEY_MAP[f"f{_f}"] = 0x6F + _f                     # f1..f24

# keys that need the KEYEVENTF_EXTENDEDKEY flag
_EXTENDED = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E,
             0x5B, 0x5C, 0x91}


def vk_for_name(name: str) -> int | None:
    """Resolve a key name (pyautogui-style) to a Windows virtual-key code."""
    return _KEY_MAP.get(str(name or "").strip().lower())


# ── low-level SendInput helpers ───────────────────────────────────────────────

def _send(*inputs: _INPUT) -> bool:
    """Send one or more input structures; return True if all were accepted."""
    if not inputs:
        return False
    arr = (_INPUT * len(inputs))(*inputs)
    sent = ctypes.windll.user32.SendInput(len(inputs), arr, ctypes.sizeof(_INPUT))
    return sent == len(inputs)


def _block_inputs(*, consistent: bool = False) -> None:
    pass  # no-op hook kept for callers that want to batch; not used yet


def _mouse_event(dx: int, dy: int, flags: int, data: int = 0) -> _INPUT:
    inp = _INPUT()
    inp.type = _INPUT_MOUSE
    inp.mi.dx, inp.mi.dy = dx, dy
    inp.mi.mouseData = data
    inp.mi.dwFlags = flags
    return inp


def _key_event(vk: int | None, char: str | None, flags: int, scan: int = 0) -> _INPUT:
    inp = _INPUT()
    inp.type = _INPUT_KEYBD
    if char is not None:
        inp.ki.wVk = 0
        inp.ki.wScan = scan
        inp.ki.dwFlags = flags | _KF_UNICODE
    else:
        inp.ki.wVk = vk or 0
        inp.ki.wScan = scan
        inp.ki.dwFlags = flags
    return inp


# ── mouse ─────────────────────────────────────────────────────────────────────

def _screen_size_inner() -> tuple[int, int]:
    u = ctypes.windll.user32
    return (u.GetSystemMetrics(0), u.GetSystemMetrics(1))


def screen_size() -> tuple[int, int]:
    """(width, height) of the primary monitor in pixels."""
    if not _IS_WINDOWS:
        raise _WinNotSupported("screen_size")
    return _screen_size_inner()


def mouse_position() -> tuple[int, int]:
    """Current cursor position as (x, y) — primary monitor coordinates."""
    _need_windows("mouse_position")
    pt = wintypes.POINT()
    if not ctypes.windll.user32.GetCursorPos(ctypes.byref(pt)):
        raise RuntimeError("GetCursorPos failed")
    return int(pt.x), int(pt.y)


def mouse_move(x: int, y: int, *, relative: bool = False) -> None:
    """Move the cursor to (x, y), or by (dx, dy) when relative=True.

    Absolute moves are normalised over the VIRTUAL desktop (every monitor,
    MOUSEEVENTF_VIRTUALDESK) so coordinates on a second monitor — including
    negatives to the left of the primary — land where they belong instead of
    being clamped onto monitor 1 (#23).
    """
    _need_windows("mouse_move")
    if relative:
        _send(_mouse_event(int(x), int(y), _MF_MOVE))
    else:
        _send(_mouse_event(*_abs_norm(x, y), _MF_MOVE | _MF_ABSOLUTE | _MF_VIRTUALDESK))


def _virtual_rect() -> tuple[int, int, int, int]:
    """(left, top, width, height) of the whole virtual screen."""
    u = ctypes.windll.user32
    left, top = int(u.GetSystemMetrics(76)), int(u.GetSystemMetrics(77))
    wide, high = int(u.GetSystemMetrics(78)), int(u.GetSystemMetrics(79))
    if wide > 1 and high > 1:
        return left, top, wide, high
    w, h = _screen_size_inner()
    return 0, 0, w, h


def _abs_norm(x: int, y: int) -> tuple[int, int]:
    """Clamp (x, y) to the virtual screen and normalise to 0..65535 for
    MOUSEEVENTF_ABSOLUTE|MOUSEEVENTF_VIRTUALDESK. Pure math — unit-testable."""
    vl, vt, vw, vh = _virtual_rect()
    cx = max(vl, min(int(x), vl + vw - 1))
    cy = max(vt, min(int(y), vt + vh - 1))
    norm_x = int((cx - vl) * 65535 / max(1, vw - 1))
    norm_y = int((cy - vt) * 65535 / max(1, vh - 1))
    return max(0, min(65535, norm_x)), max(0, min(65535, norm_y))


def mouse_click(x: int | None = None, y: int | None = None,
                button: str = "left", clicks: int = 1,
                interval: float = 0.08) -> None:
    """Click at (x, y) (or current position). button: left|right|middle."""
    _need_windows("mouse_click")
    if x is not None and y is not None:
        mouse_move(int(x), int(y))
        time.sleep(0.05)
    down, up = {
        "left":   (_MF_LEFTDOWN, _MF_LEFTUP),
        "right":  (_MF_RIGHTDOWN, _MF_RIGHTUP),
        "middle": (_MF_MIDDLEDOWN, _MF_MIDDLEUP),
        "x":      (_MF_XDOWN, _MF_XUP),
    }.get(str(button).lower(), (_MF_LEFTDOWN, _MF_LEFTUP))
    for _ in range(max(1, int(clicks))):
        _send(_mouse_event(0, 0, down), _mouse_event(0, 0, up))
        time.sleep(interval)


def mouse_drag(x1: int, y1: int, x2: int, y2: int, *,
               button: str = "left", steps: int = 12) -> None:
    """Press the button at (x1,y1), drag to (x2,y2), release."""
    _need_windows("mouse_drag")
    down, up = {
        "left":   (_MF_LEFTDOWN, _MF_LEFTUP),
        "right":  (_MF_RIGHTDOWN, _MF_RIGHTUP),
        "middle": (_MF_MIDDLEDOWN, _MF_MIDDLEUP),
    }.get(str(button).lower(), (_MF_LEFTDOWN, _MF_LEFTUP))
    mouse_move(x1, y1)
    time.sleep(0.05)
    _send(_mouse_event(0, 0, down))
    time.sleep(0.05)
    for i in range(1, steps + 1):
        t = i / steps
        _send(_mouse_event(
            *_abs_norm(x1 + (x2 - x1) * t, y1 + (y2 - y1) * t),
            _MF_MOVE | _MF_ABSOLUTE | _MF_VIRTUALDESK,
        ))
        time.sleep(0.015)
    _send(_mouse_event(0, 0, up))


def mouse_scroll(amount: int, *, horizontal: bool = False) -> None:
    """Scroll the wheel. Positive = up (or left when horizontal)."""
    _need_windows("mouse_scroll")
    delta = int(amount) * _WHEEL_DELTA
    _send(_mouse_event(0, 0, _MF_HWHEEL if horizontal else _MF_WHEEL, delta))


def mouse_wheel_up(notches: int = 1) -> None:
    mouse_scroll(notches)


def mouse_wheel_down(notches: int = 1) -> None:
    mouse_scroll(-notches)


def mouse_wheel_raw(delta: int, *, horizontal: bool = False) -> None:
    """Send an exact wheel delta (pyautogui semantics: 120 ≈ one notch)."""
    _need_windows("mouse_wheel_raw")
    _send(_mouse_event(0, 0, _MF_HWHEEL if horizontal else _MF_WHEEL,
                       int(delta)))


# ── keyboard ──────────────────────────────────────────────────────────────────

def _key_down_up(vk: int, down: bool) -> None:
    flags = _KF_EXTENDEDKEY if vk in _EXTENDED else 0
    if not down:
        flags |= _KF_KEYUP
    _send(_key_event(vk, None, flags))


def key_press(name: str) -> None:
    """Press and release one named key (e.g. 'enter', 'win', 'ctrl')."""
    _need_windows("key_press")
    vk = vk_for_name(name)
    if vk is None:
        raise ValueError(f"Unknown key name: '{name}'")
    _key_down_up(vk, True)
    time.sleep(0.02)
    _key_down_up(vk, False)


def key_hold(name: str, seconds: float) -> None:
    """Hold a key down for `seconds`, then release it."""
    _need_windows("key_hold")
    vk = vk_for_name(name)
    if vk is None:
        raise ValueError(f"Unknown key name: '{name}'")
    _key_down_up(vk, True)
    time.sleep(max(0.0, float(seconds)))
    _key_down_up(vk, False)


def key_hotkey(*names: str, interval: float = 0.05) -> None:
    """Press modifier+key combination (e.g. key_hotkey('ctrl','c'))."""
    _need_windows("key_hotkey")
    vks: list[int] = []
    for name in names:
        vk = vk_for_name(name)
        if vk is None:
            raise ValueError(f"Unknown key name: '{name}'")
        vks.append(vk)
    for vk in vks:
        _key_down_up(vk, True)
        time.sleep(interval)
    for vk in reversed(vks):
        _key_down_up(vk, False)
        time.sleep(interval)


def key_type(text: str, interval: float = 0.02) -> None:
    """Type arbitrary text (any Unicode — Hindi, emoji, symbols) as key events.

    Characters outside the BMP (emoji and friends, e.g. \U0001F600) do not fit
    the 16-bit scan-code field: assigning them used to truncate silently and a
    wrong character landed in the field. They are sent as their UTF-16 surrogate
    pair instead, which is exactly what Windows expects for KEYEVENTF_UNICODE.
    """
    _need_windows("key_type")
    for ch in str(text):
        code = ord(ch)
        units = (code,) if code <= 0xFFFF else (
            0xD800 + ((code - 0x10000) >> 10),          # high surrogate
            0xDC00 + ((code - 0x10000) & 0x3FF),        # low surrogate
        )
        for u in units:
            _send(_key_event(None, chr(u), 0, u))
            time.sleep(0.002)
            _send(_key_event(None, chr(u), _KF_KEYUP, u))
        if interval:
            time.sleep(interval)


# ── clipboard (no pyperclip needed) ───────────────────────────────────────────

_CF_UNICODETEXT = 13
_GMEM_MOVEABLE = 0x0002


def clipboard_get() -> str:
    """Read the clipboard as text. Prefers pyperclip, else Win32 clipboard."""
    if _HAS_PYPERCLIP:
        try:
            return str(pyperclip.paste())
        except Exception:
            pass
    if not _IS_WINDOWS:
        return ""
    try:
        for _ in range(5):
            if ctypes.windll.user32.OpenClipboard(None):
                break
            time.sleep(0.05)
        else:
            return ""
        try:
            h = ctypes.windll.user32.GetClipboardData(_CF_UNICODETEXT)
            if not h:
                return ""
            ptr = ctypes.windll.kernel32.GlobalLock(h)
            if not ptr:
                return ""
            try:
                return ctypes.wstring_at(ptr)
            finally:
                ctypes.windll.kernel32.GlobalUnlock(h)
        finally:
            ctypes.windll.user32.CloseClipboard()
    except Exception:
        return ""


def clipboard_set(text: str) -> None:
    """Put `text` onto the clipboard (Win32 path, no pyperclip required)."""
    text = str(text or "")
    if _HAS_PYPERCLIP:
        try:
            pyperclip.copy(text)
            return
        except Exception:
            pass
    _need_windows("clipboard_set")
    try:
        for _ in range(5):
            if ctypes.windll.user32.OpenClipboard(None):
                break
            time.sleep(0.05)
        else:
            raise RuntimeError("clipboard is locked by another process")
        try:
            ctypes.windll.user32.EmptyClipboard()
            buf = ctypes.create_unicode_buffer(text)
            h = ctypes.windll.kernel32.GlobalAlloc(_GMEM_MOVEABLE,
                                                   ctypes.sizeof(buf))
            if not h:
                raise RuntimeError("GlobalAlloc failed")
            mem = ctypes.windll.kernel32.GlobalLock(h)
            ctypes.memmove(mem, buf, ctypes.sizeof(buf))
            ctypes.windll.kernel32.GlobalUnlock(h)
            # On success the clipboard OWNS the handle — never GlobalFree it.
            if not ctypes.windll.user32.SetClipboardData(_CF_UNICODETEXT, h):
                ctypes.windll.kernel32.GlobalFree(h)  # only on failure
                raise RuntimeError("SetClipboardData failed")
        finally:
            ctypes.windll.user32.CloseClipboard()
    except Exception as e:
        raise RuntimeError(f"clipboard_set failed: {e}")


def clipboard_clear() -> None:
    """Empty the clipboard."""
    clipboard_set("")


# ── windows ───────────────────────────────────────────────────────────────────

_WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND,
                                  wintypes.LPARAM)


def window_list(limit: int = 50) -> list[tuple[int, str, int]]:
    """Visible top-level windows as [(hwnd, title, pid), ...]."""
    if not _IS_WINDOWS:
        raise _WinNotSupported("window_list")
    found: list[tuple[int, str, int]] = []

    def _cb(hwnd, _lparam):
        if not ctypes.windll.user32.IsWindowVisible(hwnd):
            return True
        length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value.strip()
        if not title:
            return True
        pid = wintypes.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        found.append((int(hwnd), title, int(pid.value)))
        return len(found) < limit

    ctypes.windll.user32.EnumWindows(_WNDENUMPROC(_cb), 0)
    return found


def _find_window(title_fragment: str, exact: bool = False) -> int | None:
    fragment = str(title_fragment or "").strip().lower()
    if not fragment:
        return None
    for hwnd, title, _pid in window_list(limit=200):
        if exact and title.lower() == fragment:
            return hwnd
        if not exact and fragment in title.lower():
            return hwnd
    return None


def window_focus(title_fragment: str) -> bool:
    """Bring a window (title substring) to the foreground."""
    _need_windows("window_focus")
    hwnd = _find_window(title_fragment)
    if not hwnd:
        return False
    user32 = ctypes.windll.user32
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)          # SW_RESTORE
    user32.ShowWindow(hwnd, 5)              # SW_SHOW
    user32.BringWindowToTop(hwnd)
    # The classic foreground-lock bypass: a sentinel Alt tap, then focus.
    user32.keybd_event(0x12, 0, 0, 0)
    user32.keybd_event(0x12, 0, _KF_KEYUP, 0)
    user32.SetForegroundWindow(hwnd)
    return True


def window_minimize(title_fragment: str) -> bool:
    return _show_window(title_fragment, 6)              # SW_MINIMIZE


def window_maximize(title_fragment: str) -> bool:
    return _show_window(title_fragment, 3)              # SW_MAXIMIZE


def window_restore(title_fragment: str) -> bool:
    return _show_window(title_fragment, 9)              # SW_RESTORE


def _show_window(title_fragment: str, show_cmd: int) -> bool:
    _need_windows("window_show")
    hwnd = _find_window(title_fragment)
    if not hwnd:
        return False
    return bool(ctypes.windll.user32.ShowWindow(hwnd, show_cmd))


def window_move(title_fragment: str, x: int, y: int,
                width: int | None = None, height: int | None = None) -> bool:
    """Move (and optionally resize) a window by title substring."""
    _need_windows("window_move")
    hwnd = _find_window(title_fragment)
    if not hwnd:
        return False
    rect = wintypes.RECT()
    if width is None or height is None:
        ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
        width = width if width is not None else rect.right - rect.left
        height = height if height is not None else rect.bottom - rect.top
    return bool(ctypes.windll.user32.MoveWindow(hwnd, int(x), int(y),
                                                int(width), int(height), True))


def window_close(title_fragment: str) -> bool:
    """Ask a window to close (WM_CLOSE — the same as clicking its X)."""
    _need_windows("window_close")
    hwnd = _find_window(title_fragment)
    if not hwnd:
        return False
    ctypes.windll.user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
    return True


def foreground_window_title() -> str:
    """Title of the window currently in the foreground."""
    if not _IS_WINDOWS:
        return ""
    hwnd = ctypes.windll.user32.GetForegroundWindow()
    if not hwnd:
        return ""
    length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value.strip()


# ── processes ─────────────────────────────────────────────────────────────────

def process_list(filter_str: str = "") -> list[tuple[int, str, float]]:
    """Running processes as [(pid, name, memory_MB), ...]. psutil or tasklist."""
    rows: list[tuple[int, str, float]] = []
    flt = str(filter_str or "").strip().lower()
    try:
        if _HAS_PSUTIL:
            for p in psutil.process_iter(["pid", "name", "memory_info"]):
                try:
                    info = p.info
                    name = str(info.get("name") or "")
                    if flt and flt not in name.lower():
                        continue
                    mem = 0.0
                    if info.get("memory_info") is not None:
                        mem = round(info["memory_info"].rss / (1024 * 1024), 1)
                    rows.append((int(info["pid"]), name, mem))
                except Exception:
                    continue
            rows.sort(key=lambda r: -r[2])
            return rows
    except Exception:
        pass
    if _IS_WINDOWS:
        try:
            out = subprocess.run(
                ["tasklist", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=20,
            ).stdout
            for line in out.splitlines():
                parts = line.strip('"').split('","')
                if len(parts) < 5:
                    continue
                name, pid = parts[0], parts[1]
                try:
                    mem = float(parts[4].replace(",", "").replace(" K", ""))
                except Exception:
                    mem = 0.0
                if flt and flt not in name.lower():
                    continue
                rows.append((int(pid), name, round(mem / 1024, 1)))
        except Exception:
            pass
    return rows


def process_start(target: str) -> str:
    """Launch an app / file / URL / command. Returns a short confirmation."""
    target = str(target or "").strip()
    if not target:
        raise ValueError("process_start needs a path, app name or command")
    if _IS_WINDOWS:
        subprocess.Popen(["cmd", "/c", "start", "", target],
                         close_fds=True)
        return f"Started: {target}"
    subprocess.Popen(["sh", "-c", f"{target} &"], close_fds=True)
    return f"Started: {target}"


def process_kill(target: str | int) -> str:
    """Terminate a process by name or PID. (Actions gate this behind confirm.)"""
    if isinstance(target, int):
        pid = target
        if _HAS_PSUTIL:
            try:
                p = psutil.Process(pid)
                p.terminate()
                p.wait(timeout=5)
                return f"Terminated PID {pid} ({p.name()})"
            except psutil.NoSuchProcess:
                return f"PID {pid} not found"
            except Exception as e:
                return f"Could not terminate PID {pid}: {e}"
        if _IS_WINDOWS:
            subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                           capture_output=True, timeout=15)
            return f"taskkill PID {pid} attempted"
        return f"Could not terminate PID {pid} (no psutil)"
    name = str(target).strip()
    if not name:
        return "process_kill needs a process name or PID"
    if _HAS_PSUTIL:
        killed = []
        for p in psutil.process_iter(["pid", "name"]):
            try:
                if str(p.info.get("name") or "").lower() == name.lower():
                    p.terminate()
                    killed.append(int(p.info["pid"]))
            except psutil.NoSuchProcess:
                continue
            except Exception:
                continue
        if killed:
            return f"Terminated {len(killed)} process(es) named '{name}': {killed}"
        return f"No running process named '{name}'"
    if _IS_WINDOWS:
        subprocess.run(["taskkill", "/F", "/IM", name],
                       capture_output=True, timeout=15)
        return f"taskkill /IM {name} attempted"
    return f"Cannot kill '{name}' (no psutil, non-Windows)"


# ── volume & media ────────────────────────────────────────────────────────────

def volume_get() -> int | None:
    """Master volume 0-100 via pycaw (in requirements) or None."""
    if _HAS_PYCAW:
        try:
            devices = AudioUtilities.GetSpeakers()
            interface = devices.Activate(IAudioEndpointVolume._iid_,
                                         CLSCTX_ALL, None)
            vol = cast(interface, POINTER(IAudioEndpointVolume))
            db = vol.GetMasterVolumeLevel()
            if db <= -65.0:
                return 0
            return max(0, min(100, round(10 ** (db / 20) * 100)))
        except Exception:
            return None
    return None


def volume_set(value: int) -> None:
    """Set master volume 0-100 (pycaw). Raises if unavailable."""
    value = max(0, min(100, int(value)))
    if _HAS_PYCAW:
        import math
        devices = AudioUtilities.GetSpeakers()
        interface = devices.Activate(IAudioEndpointVolume._iid_,
                                     CLSCTX_ALL, None)
        vol = cast(interface, POINTER(IAudioEndpointVolume))
        vol_db = -65.25 if value == 0 else max(-65.25, 20 * math.log10(value / 100))
        vol.SetMasterVolumeLevel(vol_db, None)
        return
    raise RuntimeError("volume_set needs pycaw (does not ship in the exe); "
                       "use volume_up / volume_down instead")


def volume_up(steps: int = 1) -> None:
    for _ in range(max(1, int(steps))):
        key_press("volumeup")


def volume_down(steps: int = 1) -> None:
    for _ in range(max(1, int(steps))):
        key_press("volumedown")


def volume_mute() -> None:
    key_press("volumemute")


def media_next() -> None:
    key_press("nexttrack")


def media_prev() -> None:
    key_press("prevtrack")


def media_play_pause() -> None:
    key_press("playpause")


def media_stop() -> None:
    key_press("stop")


# ── brightness (PowerShell WMI — no extra package) ────────────────────────────

def brightness_get() -> int | None:
    if not _IS_WINDOWS:
        return None
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "(Get-WmiObject -Namespace root/wmi -Class "
             "WmiMonitorBrightness).CurrentBrightness"],
            capture_output=True, text=True, timeout=8,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return max(0, min(100, int(r.stdout.strip()))) if r.stdout.strip() else None
    except Exception:
        return None


def brightness_set(value: int) -> None:
    value = max(0, min(100, int(value)))
    if not _IS_WINDOWS:
        raise _WinNotSupported("brightness_set")
    subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command",
         "(Get-WmiObject -Namespace root/wmi -Class "
         "WmiMonitorBrightnessMethods).WmiSetBrightness(1, "
         f"{value})"],
        capture_output=True, timeout=8,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


# ── OS actions ────────────────────────────────────────────────────────────────

def lock_screen() -> None:
    """Lock the workstation (Win+L)."""
    _need_windows("lock_screen")
    ctypes.windll.user32.LockWorkStation()


def sleep_display() -> None:
    """Turn the display off (Win+L-level power save on the monitor only)."""
    _need_windows("sleep_display")
    ctypes.windll.user32.SendMessageW(0xFFFF, 0x0112, 0xF170, 2)


def show_desktop() -> None:
    key_hotkey("win", "d")


def run_dialog() -> None:
    key_hotkey("win", "r")


def system_info() -> dict:
    """CPU / RAM / disk / uptime / battery summary (psutil when present)."""
    info: dict = {
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "cpu_count": os.cpu_count() or 0,
    }
    try:
        import platform as _p
        info["processor"] = _p.processor() or ""
    except Exception:
        info["processor"] = ""
    if _HAS_PSUTIL:
        try:
            vm = psutil.virtual_memory()
            info["ram_total_gb"] = round(vm.total / (1024 ** 3), 1)
            info["ram_used_gb"] = round(vm.used / (1024 ** 3), 1)
            info["ram_percent"] = vm.percent
            try:
                du = psutil.disk_usage(str(Path.home().anchor if Path.home().anchor
                                           else Path.home().drive))
                info["disk_total_gb"] = round(du.total / (1024 ** 3), 1)
                info["disk_free_gb"] = round(du.free / (1024 ** 3), 1)
            except Exception:
                pass
            try:
                boot = psutil.boot_time()
                info["uptime_hours"] = round((time.time() - boot) / 3600, 1)
            except Exception:
                pass
            try:
                batt = psutil.sensors_battery()
                if batt is not None:
                    info["battery_percent"] = round(batt.percent, 0)
                    info["battery_plugged"] = bool(batt.power_plugged)
            except Exception:
                pass
        except Exception:
            pass
    return info


def screen_monitors() -> list[dict]:
    """Geometry of every attached monitor: [{'x','y','width','height'}, ...]."""
    if not _IS_WINDOWS:
        raise _WinNotSupported("screen_monitors")
    monitors: list[dict] = []

    def _cb(mon, _hdc, rect, _lparam):
        r = rect.contents
        monitors.append({"x": r.left, "y": r.top,
                         "width": r.right - r.left, "height": r.bottom - r.top})
        return True

    MONITORENUMPROC = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HANDLE, wintypes.HANDLE,
        ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)
    ctypes.windll.user32.EnumDisplayMonitors(None, None,
                                             MONITORENUMPROC(_cb), 0)
    return monitors


# ── screenshot ────────────────────────────────────────────────────────────────

def screenshot(save_path: str | None = None):
    """Capture the screen. Returns the saved path, or a PIL Image when
    save_path is None (mirrors pyautogui.screenshot's two behaviours)."""
    if _HAS_PIL:
        from PIL import ImageGrab as _ig
        img = _ig.grab()
        if save_path:
            img.save(str(save_path))
            return str(save_path)
        return img
    if _HAS_MSS:
        import mss as _mss
        with _mss.mss() as sct:
            shot = sct.grab(sct.monitors[1])
            if save_path:
                from PIL import Image as _PILImage   # mss needs PIL to save
                _PILImage.frombytes("RGB", shot.size, shot.bgra,
                                    "raw", "BGRX").save(str(save_path))
                return str(save_path)
            return shot
    raise RuntimeError("screenshot needs pillow or mss (both ship in the exe)")


# ── pyautogui-compatible surface ──────────────────────────────────────────────
# These exist so `from core import pc_input as pyautogui` is a drop-in inside
# the exe where real pyautogui is excluded.

def press(key: str, presses: int = 1, interval: float = 0.05) -> None:
    """pyautogui.press — press+release a key N times."""
    for _ in range(max(1, int(presses))):
        key_press(key)
        if interval:
            time.sleep(interval)


def hotkey(*keys: str) -> None:
    """pyautogui.hotkey — e.g. hotkey('ctrl','shift','esc')."""
    key_hotkey(*keys)


def write(text: str, interval: float = 0.02) -> None:
    """pyautogui.write — type each character in order."""
    key_type(str(text), interval=interval)


def typewrite(text: str, interval: float = 0.02) -> None:
    """Old pyautogui name for write()."""
    key_type(str(text), interval=interval)


def scroll(clicks: int = 1, _x: int = None, _y: int = None) -> None:
    """pyautogui.scroll — the raw delta is passed straight to the wheel."""
    mouse_wheel_raw(clicks)


def hscroll(clicks: int = 1, _x: int = None, _y: int = None) -> None:
    """pyautogui.hscroll — positive scrolls left (matches pyautogui)."""
    mouse_wheel_raw(clicks, horizontal=True)


def click(x: int | None = None, y: int | None = None, button: str = "left",
          clicks: int = 1, **_kw) -> None:
    mouse_click(x, y, button=button, clicks=clicks)


def doubleClick(x: int | None = None, y: int | None = None,
                button: str = "left") -> None:          # noqa: N802 — pyautogui API
    mouse_click(x, y, button=button, clicks=2)


def moveTo(x: int, y: int, duration: float = 0.0, **_kw) -> None:  # noqa: N802
    if duration and duration > 0:
        x0, y0 = mouse_position()
        steps = max(2, int(duration * 60))
        for i in range(1, steps + 1):
            t = i / steps
            mouse_move(int(x0 + (x - x0) * t), int(y0 + (y - y0) * t))
            time.sleep(duration / steps)
    else:
        mouse_move(int(x), int(y))


def dragTo(x: int, y: int, duration: float = 0.0, button: str = "left",  # noqa: N802
           **_kw) -> None:
    x0, y0 = mouse_position()
    mouse_drag(x0, y0, int(x), int(y), button=button)


def position() -> tuple[int, int]:
    return mouse_position()


def size() -> tuple[int, int]:
    return screen_size()


def screenshot_compat(save_path: str | None = None, **kw):
    return screenshot(save_path)


# Explicit aliases so `pyautogui = pc_input` finds everything at once.
SIZE = size
POSITION = position