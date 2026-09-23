#computer_control.py
import ctypes
import io
import json
import platform
import re
import string
import subprocess
import sys

if platform.system() == "Windows":
    _WIN_HIDE: dict = {"creationflags": subprocess.CREATE_NO_WINDOW}
else:
    _WIN_HIDE: dict = {}
import time
import random
from pathlib import Path

try:
    import pyautogui
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE    = 0.05
    _PYAUTOGUI = True
except ImportError:
    _PYAUTOGUI = False

try:
    import pyperclip
    _PYPERCLIP = True
except ImportError:
    _PYPERCLIP = False

try:
    from core import pc_log
except Exception:                                   # pragma: no cover
    pc_log = None

def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


_BASE         = _base_dir()
_CONFIG_PATH  = _BASE / "config" / "api_keys.json"
_MEMORY_PATH  = _BASE / "memory" / "long_term.json"

def _load_config() -> dict:
    try:
        return json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}

def _platform_os() -> str:
    return {"Windows": "windows", "Darwin": "mac", "Linux": "linux"}.get(
        platform.system(), "linux"
    )

def _get_os() -> str:
    return _load_config().get("os_system", _platform_os()).lower()


def _log(msg: str) -> None:
    """Console logging that can never crash a control action.

    Several consoles default to a legacy codepage (cp1252, cp932...); a stray
    emoji or a non-Latin character in the text being typed would raise
    UnicodeEncodeError from a bare `print` and abort the action. Logging is
    diagnostics, so it degrades to ASCII instead of taking the action down.
    """
    try:
        print(str(msg).encode("ascii", "replace").decode("ascii"))
    except Exception:
        pass


def _get_api_key() -> str:
    return _load_config().get("gemini_api_key", "")


def _focused_app_name() -> str:
    """Best-effort focused application name, used to key strategy memory.
    Falls back to the process' own window title when the foreground query
    fails; never raises."""
    try:
        if platform.system() == "Windows":
            from win32gui import GetForegroundWindow, GetWindowText
            title = GetWindowText(GetForegroundWindow()) or ""
        else:
            title = ""
        if not title:
            try:
                import pyautogui
                aw = pyautogui.getActiveWindow()
                title = getattr(aw, "title", "") or ""
            except Exception:
                title = ""
        return (title.split(" - ")[-1].split(" — ")[-1].strip()
                or "unknown")[:40]
    except Exception:
        return "unknown"

_SAFE_SCREENSHOT_ROOTS = (
    Path.home(),
)

def _safe_screenshot_path(requested: str | None) -> Path:
    fallback = Path.home() / "Desktop" / "jarvis_screenshot.png"
    if not requested:
        return fallback
    try:
        p = Path(requested).expanduser().resolve()
        for root in _SAFE_SCREENSHOT_ROOTS:
            if p.is_relative_to(root.resolve()):
                p.parent.mkdir(parents=True, exist_ok=True)
                return p
    except Exception:
        pass
    return fallback

def _require_pyautogui():
    if not _PYAUTOGUI:
        raise RuntimeError("PyAutoGUI not installed. Run: pip install pyautogui")


# ── DPI awareness + coordinate mapping ───────────────────────────────────────
# The mouse-miss bug: on a scaled display (125% / 150% Windows scaling) an
# unaware process gets a *virtualised* `pyautogui.size()` while the screenshot
# is captured at *physical* pixels. A coordinate the vision model reads off the
# screenshot is then in a different space from the one the mouse moves in, so
# every click lands offset by the scale factor. Two defences:
#   1. Declare the process per-monitor DPI aware, so the two spaces agree.
#   2. Never trust them to agree: measure the ratio between the screenshot and
#      `pyautogui.size()` on every capture and convert explicitly.

def _rescue_pag(fn, *args, **kwargs):
    """Run a pyautogui mouse op, recovering from the fail-safe corner abort.

    pyautogui aborts EVERY move/click with FailSafeException while the pointer
    sits at (0,0), and it checks before moving — so even the clearing move is
    blocked. A remote or virtualised session can idle the cursor there, which
    makes mouse control look completely dead. On that specific failure we
    temporarily disable the fail-safe, pull the pointer to the middle of the
    screen, restore it, and retry once; anything else propagates."""
    try:
        return fn(*args, **kwargs)
    except Exception as e:
        if _PYAUTOGUI and isinstance(e, pyautogui.FailSafeException):
            w, h = _screen_size()
            pyautogui.FAILSAFE = False
            try:
                pyautogui.moveTo(max(w // 2, 8), max(h // 2, 8), duration=0.1)
                time.sleep(0.05)
            finally:
                pyautogui.FAILSAFE = True
            return fn(*args, **kwargs)
        raise


def _enable_dpi_awareness() -> None:
    """Make this process per-monitor DPI aware on Windows, but never fight a
    mode the host already set (Qt sets its own early). Best-effort, silent."""
    if platform.system() != "Windows":
        return
    try:
        aware = ctypes.c_int(0)
        ctypes.windll.shcore.GetProcessDpiAwareness(None, ctypes.byref(aware))
        if aware.value != 0:
            return                      # already aware - leave it alone
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)   # per-monitor aware
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()    # pre-8.1 fallback
        except Exception:
            pass


_enable_dpi_awareness()


def _screen_size() -> tuple[int, int]:
    _require_pyautogui()
    size = pyautogui.size()
    return int(size[0]), int(size[1])


def _virtual_bounds() -> tuple[int, int, int, int]:
    """(left, top, width, height) of the WHOLE virtual screen — every monitor.

    Clamping to the primary monitor only would make "click the button on my
    second monitor" silently land on monitor 1 (#23). Falls back to the
    primary size when the query fails (non-Windows, mocked tests).
    """
    try:
        import ctypes
        u = ctypes.windll.user32
        left, top = int(u.GetSystemMetrics(76)), int(u.GetSystemMetrics(77))
        wide, high = int(u.GetSystemMetrics(78)), int(u.GetSystemMetrics(79))
        if wide > 1 and high > 1:
            return left, top, wide, high
    except Exception:
        pass
    w, h = _screen_size()
    return 0, 0, w, h


def _clamp_to_screen(x, y) -> tuple[int, int]:
    """Keep a coordinate inside the VIRTUAL screen (all monitors). A hallucinated
    or off-by-scale coordinate is a wrong click; a clamped one is merely an edge
    click. Points on monitor 2+ stay put instead of being yanked to monitor 1.

    Also steers away from the screen's four corners: pyautogui treats every
    corner as a failsafe-abort point, so parking the pointer on one makes the
    *next* control call raise FailSafeException. A clamped coordinate must not
    be able to wedge the controller like that.
    """
    vl, vt, vw, vh = _virtual_bounds()
    try:
        cx = int(round(float(x)))
        cy = int(round(float(y)))
    except (TypeError, ValueError):
        cx, cy = vl, vt
    cx = max(vl, min(cx, vl + vw - 1))
    cy = max(vt, min(cy, vt + vh - 1))
    # Failsafe corners belong to the primary screen (0,0)-(w-1,h-1).
    pw, ph = _screen_size()
    if pw >= 3 and ph >= 3:
        if 0 <= cx < pw:
            if cx == 0:
                cx = 1
            elif cx == pw - 1:
                cx = pw - 2
        if 0 <= cy < ph:
            if cy == 0:
                cy = 1
            elif cy == ph - 1:
                cy = ph - 2
        if (cx, cy) in ((0, 0), (pw - 1, 0), (0, ph - 1), (pw - 1, ph - 1)):
            cx = max(1, min(cx, pw - 2))
            cy = max(1, min(cy, ph - 2))
    return cx, cy


def _capture_with_mapping():
    """Screenshot plus the factor that converts screenshot pixels to mouse
    coordinates. Returns (pil_image, scale_x, scale_y). scale is 1.0 on a
    correctly DPI-aware process; it silently corrects the offset when it is not.
    """
    _require_pyautogui()
    img = pyautogui.screenshot()
    sw, sh = _screen_size()
    iw, ih = img.size
    sx = (sw / iw) if iw else 1.0
    sy = (sh / ih) if ih else 1.0
    return img, sx, sy

_FIRST_NAMES = [
    "Alex", "Jordan", "Taylor", "Morgan", "Casey", "Riley", "Drew", "Quinn",
    "Avery", "Blake", "Cameron", "Dakota", "Emerson", "Finley", "Harper",
]
_LAST_NAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller",
    "Davis", "Wilson", "Moore", "Taylor", "Anderson", "Thomas", "Jackson",
]
_DOMAINS = ["gmail.com", "yahoo.com", "outlook.com", "proton.me", "mail.com"]


def _random_data(data_type: str) -> str:
    dt = data_type.lower().strip()

    if dt == "first_name":
        return random.choice(_FIRST_NAMES)

    if dt == "last_name":
        return random.choice(_LAST_NAMES)

    if dt == "name":
        return f"{random.choice(_FIRST_NAMES)} {random.choice(_LAST_NAMES)}"

    if dt == "email":
        first = random.choice(_FIRST_NAMES).lower()
        last  = random.choice(_LAST_NAMES).lower()
        num   = random.randint(10, 999)
        return f"{first}.{last}{num}@{random.choice(_DOMAINS)}"

    if dt == "username":
        return f"{random.choice(_FIRST_NAMES).lower()}{random.randint(100, 9999)}"

    if dt == "password":
        chars = string.ascii_letters + string.digits + "!@#$%"
        raw   = (
            random.choice(string.ascii_uppercase)
            + random.choice(string.digits)
            + random.choice("!@#$%")
            + "".join(random.choices(chars, k=9))
        )
        return "".join(random.sample(raw, len(raw)))

    if dt == "phone":
        return f"+1{random.randint(200,999)}{random.randint(1_000_000, 9_999_999)}"

    if dt == "birthday":
        y = random.randint(1980, 2000)
        m = random.randint(1, 12)
        d = random.randint(1, 28)
        return f"{m:02d}/{d:02d}/{y}"

    if dt == "address":
        num    = random.randint(100, 9999)
        street = random.choice(["Main St", "Oak Ave", "Park Blvd", "Elm St", "Cedar Ln"])
        return f"{num} {street}"

    if dt == "zip_code":
        return str(random.randint(10000, 99999))

    if dt == "city":
        return random.choice(["New York", "Los Angeles", "Chicago", "Houston", "Phoenix"])

    return f"random_{data_type}_{random.randint(1000, 9999)}"

def _user_profile() -> dict:
    """Read identity fields from long-term memory."""
    try:
        if _MEMORY_PATH.exists():
            data     = json.loads(_MEMORY_PATH.read_text(encoding="utf-8"))
            identity = data.get("identity", {})
            return {k: v.get("value", "") for k, v in identity.items()}
    except Exception:
        pass
    return {}

def _type(text: str, interval: float = 0.03) -> str:
    _require_pyautogui()
    time.sleep(0.3)
    pyautogui.typewrite(text, interval=interval)
    return f"Typed: {text[:60]}{'...' if len(text) > 60 else ''}"


def _smart_type(text: str, clear_first: bool = True) -> str:
    _require_pyautogui()
    if clear_first:
        _clear_field()
        time.sleep(0.1)

    if len(text) > 20 and _PYPERCLIP:
        pyperclip.copy(text)
        time.sleep(0.1)
        paste_key = "command" if _get_os() == "mac" else "ctrl"
        pyautogui.hotkey(paste_key, "v")
        return f"Smart-typed (clipboard): {text[:60]}{'...' if len(text) > 60 else ''}"

    pyautogui.typewrite(text, interval=0.04)
    return f"Smart-typed: {text[:60]}{'...' if len(text) > 60 else ''}"


def _click(x=None, y=None, button: str = "left", clicks: int = 1) -> str:
    _require_pyautogui()
    if x is not None and y is not None:
        cx, cy = _clamp_to_screen(x, y)
        _rescue_pag(pyautogui.click, cx, cy, button=button, clicks=clicks)
        note = "" if (cx, cy) == (int(round(float(x))), int(round(float(y)))) else \
               f" (clamped from {int(float(x))},{int(float(y))})"
        return f"{'Double-c' if clicks == 2 else 'C'}licked ({cx}, {cy}) [{button}]{note}"
    _rescue_pag(pyautogui.click, button=button, clicks=clicks)
    return f"Clicked at current position [{button}]"


def _hotkey(*keys) -> str:
    _require_pyautogui()
    pyautogui.hotkey(*keys)
    return f"Hotkey: {'+'.join(keys)}"


def _press(key: str) -> str:
    _require_pyautogui()
    pyautogui.press(key)
    return f"Pressed: {key}"


def _scroll(direction: str = "down", amount: int = 3) -> str:
    _require_pyautogui()
    vertical   = direction in ("up", "down")
    clicks     = amount if direction in ("up", "right") else -amount
    pyautogui.scroll(clicks) if vertical else pyautogui.hscroll(clicks)
    return f"Scrolled {direction} x{amount}"


def _mouse_position() -> str:
    _require_pyautogui()
    x, y = pyautogui.position()
    w, h = _screen_size()
    return f"{int(x)},{int(y)} (screen {w}x{h})"


def _move(x: int, y: int, duration: float = 0.3) -> str:
    _require_pyautogui()
    cx, cy = _clamp_to_screen(x, y)
    _rescue_pag(pyautogui.moveTo, cx, cy, duration=duration)
    time.sleep(0.05)
    ax, ay = pyautogui.position()
    ax, ay = int(round(ax)), int(round(ay))
    if abs(ax - cx) <= 2 and abs(ay - cy) <= 2:
        return f"Mouse -> ({cx}, {cy})"
    # The pointer did not land where it was told: on Windows this is almost
    # always display scaling with a DPI-unaware process. Say so instead of
    # reporting a clean move.
    return (f"Mouse -> requested ({cx}, {cy}) but the pointer is at ({ax}, {ay}) "
            f"- display scaling/DPI mismatch is moving it; recalibrate with "
            f"'mouse_position' and use relative moves.")


def _move_rel(dx: int, dy: int, duration: float = 0.2) -> str:
    _require_pyautogui()
    # Get current position and calculate target with clamping
    cx, cy = pyautogui.position()
    w, h = _screen_size()
    tx = max(1, min(w - 2, cx + int(dx)))
    ty = max(1, min(h - 2, cy + int(dy)))
    _rescue_pag(pyautogui.moveTo, tx, ty, duration=duration)
    time.sleep(0.05)
    ax, ay = pyautogui.position()
    ax, ay = int(round(ax)), int(round(ay))
    if abs(ax - tx) <= 2 and abs(ay - ty) <= 2:
        return f"Mouse -> ({tx}, {ty}) by ({int(dx)}, {int(dy)})"
    return (f"Mouse -> requested ({tx}, {ty}) but pointer at ({ax}, {ay}) "
            f"- display scaling/DPI mismatch. Use 'mouse_position' to recalibrate.")


def _move_percent(x_pct: float, y_pct: float, duration: float = 0.25) -> str:
    """Move the pointer to a fraction of the screen: 50,50 = exact centre,
    0,0 = top-left, 100,100 = bottom-right. Clamps to a safe interior."""
    _require_pyautogui()
    w, h = _screen_size()
    tx = max(1, min(w - 2, int(round(w * (float(x_pct) / 100.0)))))
    ty = max(1, min(h - 2, int(round(h * (float(y_pct) / 100.0)))))
    _rescue_pag(pyautogui.moveTo, tx, ty, duration=duration)
    time.sleep(0.05)
    return f"Mouse -> {tx},{ty} ({x_pct}%, {y_pct}% of {w}x{h})"


def _press_arrow(key: str, repeat: int = 1) -> str:
    """Press a keyboard arrow key (up/down/left/right), optionally repeatedly.
    This is the PHYSICAL key — for selecting items in a list/menu — and is
    separate from moving the mouse pointer (move_dir / arrow)."""
    _require_pyautogui()
    key = key.lower().strip()
    if key not in ("up", "down", "left", "right", "arrowup", "arrowdown",
                   "arrowleft", "arrowright"):
        return f"arrow_key needs up | down | left | right (got '{key}')"
    name = {"arrowup": "up", "arrowdown": "down",
            "arrowleft": "left", "arrowright": "right"}.get(key, key)
    repeat = max(1, min(int(repeat), 20))
    for _ in range(repeat):
        pyautogui.press(name)
    return f"Pressed {name} x{repeat}"


def _drag(x1: int, y1: int, x2: int, y2: int, duration: float = 0.5) -> str:
    _require_pyautogui()
    sx, sy = _clamp_to_screen(x1, y1)
    ex, ey = _clamp_to_screen(x2, y2)
    _rescue_pag(pyautogui.moveTo, sx, sy, duration=0.2)
    _rescue_pag(pyautogui.dragTo, ex, ey, duration=duration, button="left")
    return f"Dragged ({sx},{sy}) -> ({ex},{ey})"


def _clipboard_get() -> str:
    if _PYPERCLIP:
        return pyperclip.paste()
    _hotkey("ctrl", "c")
    time.sleep(0.2)
    return "(copied - pyperclip unavailable for read)"


def _clipboard_paste(text: str) -> str:
    if _PYPERCLIP:
        pyperclip.copy(text)
        time.sleep(0.1)
        _require_pyautogui()
        paste_key = "command" if _get_os() == "mac" else "ctrl"
        pyautogui.hotkey(paste_key, "v")
        return f"Pasted: {text[:60]}{'...' if len(text) > 60 else ''}"
    return "pyperclip not available"


def _screenshot(save_path: str | None = None) -> str:
    _require_pyautogui()
    path = _safe_screenshot_path(save_path)
    img  = pyautogui.screenshot()
    img.save(str(path))
    return f"Screenshot saved: {path}"


def _clear_field() -> str:
    _require_pyautogui()
    select_key = "command" if _get_os() == "mac" else "ctrl"
    pyautogui.hotkey(select_key, "a")
    time.sleep(0.1)
    pyautogui.press("delete")
    return "Field cleared"

def _focus_window(title: str) -> str:
    os_name = _get_os()

    if os_name == "windows":
        try:
            script = f'(New-Object -ComObject WScript.Shell).AppActivate("{title}")'
            subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True, timeout=5, **_WIN_HIDE,
            )
            time.sleep(0.3)
            return f"Focused window: {title}"
        except Exception as e:
            return f"focus_window (Windows) failed: {e}"

    if os_name == "mac":
        script = (
            f'tell application "System Events" to '
            f'set frontmost of (first process whose name contains "{title}") to true'
        )
        try:
            subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, timeout=5,
            )
            time.sleep(0.3)
            return f"Focused window: {title}"
        except Exception as e:
            return f"focus_window (macOS) failed: {e}"

    if os_name == "linux":
        try:
            result = subprocess.run(
                ["wmctrl", "-a", title],
                capture_output=True, timeout=5,
            )
            if result.returncode == 0:
                time.sleep(0.3)
                return f"Focused window: {title}"
        except FileNotFoundError:
            pass
        try:
            result = subprocess.run(
                ["xdotool", "search", "--name", title, "windowactivate"],
                capture_output=True, timeout=5,
            )
            time.sleep(0.3)
            return f"Focused window: {title}"
        except FileNotFoundError:
            return "focus_window (Linux) requires wmctrl or xdotool"
        except Exception as e:
            return f"focus_window (Linux) failed: {e}"

    return f"focus_window: unknown OS '{os_name}'"

def _grid_overlay(img, step: int = 100):
    """Draw a labelled coordinate grid over a screenshot.

    Vision models estimate pixel coordinates far better when the coordinate
    system is on the image than when they have to infer it from a blank
    screenshot. A labelled grid turns 'somewhere in the middle-left' into
    'read the nearest red line and count'. Best-effort: if Pillow's text
    rendering is unavailable the lines alone still help, and any failure
    returns the original frame untouched.
    """
    try:
        from PIL import ImageDraw, ImageFont
    except Exception:
        return img
    try:
        draw = ImageDraw.Draw(img)
        w, h = img.size
        try:
            font = ImageFont.load_default()
        except Exception:
            font = None
        for x in range(0, w, step):
            draw.line([(x, 0), (x, h)], fill=(255, 60, 60), width=1)
            if font is not None:
                draw.text((x + 2, 2), str(x), fill=(255, 60, 60), font=font)
        for y in range(0, h, step):
            draw.line([(0, y), (w, y)], fill=(255, 60, 60), width=1)
            if font is not None:
                draw.text((2, y + 2), str(y), fill=(255, 60, 60), font=font)
    except Exception:
        return img
    return img


def _screen_find(description: str, retries: int = 2) -> tuple[int, int] | None:
    """Locate a UI element by description and return its *screen* coordinates.

    Captures with DPI mapping (so screenshot pixels are converted to the mouse's
    coordinate space), overlays a labelled grid (so the model can read the
    coordinate accurately), and retries once if the element is not found - a
    window may simply still be painting.
    """
    api_key = _get_api_key()
    if not api_key:
        _log("[ComputerControl] no API key for screen_find")
        return None
    try:
        from google.genai import types as gtypes
        from core import gemini
    except Exception as e:
        _log(f"[ComputerControl] screen_find import failed: {e}")
        return None

    for attempt in range(max(1, int(retries))):
        try:
            img, sx, sy = _capture_with_mapping()
            iw, ih = img.size
            sw, sh = _screen_size()
            if pc_log:
                pc_log.event("computer.screen_find", description=description,
                             attempt=attempt + 1, screen=f"{sw}x{sh}",
                             image=f"{iw}x{ih}")
            buf = io.BytesIO()
            _grid_overlay(img).save(buf, format="PNG")

            prompt = (
                f"Screenshot of a computer screen. The real screen is {sw}x{sh} "
                f"pixels; this image is {iw}x{ih} pixels. A faint red grid is "
                f"drawn every 100 px and each line is labelled with its pixel "
                f"coordinate. Find the UI element described as: '{description}'. "
                f"Read the element's centre off the grid labels. Reply with ONLY "
                f"the centre as: x,y (two integers, in image-pixel coordinates). "
                f"If the element is not visible, reply NOT_FOUND."
            )
            response = gemini.call(
                [gtypes.Part.from_bytes(data=buf.getvalue(), mime_type="image/png"), prompt],
                tier=gemini.FAST, timeout_ms=20_000,
            )
            if response is None:
                return None
            text = (response.text or "").strip()
            if "NOT_FOUND" in text.upper():
                if attempt < int(retries) - 1:
                    time.sleep(0.5)
                    continue
                return None
            match = re.search(r"(\d{1,5})\s*,\s*(\d{1,5})", text)
            if match:
                ix, iy = int(match.group(1)), int(match.group(2))
                return _clamp_to_screen(ix * sx, iy * sy)
        except Exception as e:
            _log(f"[ComputerControl] screen_find failed: {e}")
            return None
    return None


def _verify_after_click(description: str, coords: tuple[int, int]) -> str:
    """Post-action verification (#23 - never assume an action succeeded).

    Takes a fresh screenshot after the click and asks the model whether the
    described goal was actually achieved. Costs one extra FAST call per click;
    gated by the `verify_clicks` config flag (default on). Returns one of:
      DONE:<why>      - the click achieved its goal
      FAILED:<why>    - the element / expected result is clearly not there
      UNCERTAIN:<why> - cannot tell from the screen either way
    """
    try:
        _require_pyautogui()
        w, h  = pyautogui.size()
        img   = pyautogui.screenshot()
        buf   = io.BytesIO()
        img.save(buf, format="PNG")

        prompt = (
            f"A moment ago I clicked on the element described as: '{description}' "
            f"at pixel ({coords[0]},{coords[1]}) on a {w}x{h} screen. "
            "Look at the screenshot below and judge whether that click achieved its "
            "goal. Reply with ONLY one line, choosing one prefix:\n"
            "DONE: <one short reason>\n"
            "FAILED: <one short reason - the element or its expected result is not visible>\n"
            "UNCERTAIN: <one short reason>\n"
            "Do not mention the prefix is part of my format - just answer it."
        )
        from core import gemini
        from google.genai import types as gtypes
        response = gemini.call(
            [gtypes.Part.from_bytes(data=buf.getvalue(), mime_type="image/png"), prompt],
            tier=gemini.FAST, timeout_ms=20_000,
        )
        text = (response.text or "").strip()
        if "DONE" in text.upper().split(":")[0]:
            return "DONE"
        if "FAILED" in text.upper().split(":")[0]:
            return "FAILED"
        return "UNCERTAIN"
    except Exception as e:
        _log(f"[ComputerControl] verify_after_click failed: {e}")
        return "UNCERTAIN"


def _do_screen_click(description: str, button: str = "left",
                     clicks: int = 1, verify=None) -> str:
    """Locate an element by description, click it, and (optionally) verify."""
    desc = str(description or "").strip()
    if not desc:
        return "screen_click needs a 'description' of the element to click."
    coords = _screen_find(desc)
    if not coords:
        if pc_log:
            pc_log.event("computer.screen_click", description=desc,
                         button=button, method="vision", result="not-found")
        return f"Element not found on screen: '{desc}'"
    time.sleep(0.2)
    _click(x=coords[0], y=coords[1], button=button, clicks=clicks)
    verb = ("Double-clicked" if clicks == 2
            else ("Right-clicked" if button == "right" else "Clicked"))
    if verify is None:
        try:
            from memory.config_manager import get_verify_clicks
            verify = get_verify_clicks()
        except Exception:
            verify = False
    # Verification only makes sense for a single left click on a result-bearing
    # control; a right-click opens a menu and a double-click is already a
    # distinct gesture, so neither is judged by the click verifier.
    if verify and clicks == 1 and button == "left":
        verdict = _verify_after_click(desc, coords)
        if pc_log:
            pc_log.event("computer.screen_click", description=desc,
                         button=button, method="vision", box=str(coords),
                         action="click-center", verify=verdict.lower(),
                         result=verdict)
        if verdict == "DONE":
            try:
                from core import strategy_memory
                strategy_memory.record(_focused_app_name(), desc, "vision",
                                       True)
            except Exception:
                pass
            return f"{verb} '{desc}' at {coords} and verified it worked."
        if verdict == "FAILED":
            try:
                from core import strategy_memory
                strategy_memory.record(_focused_app_name(), desc, "vision",
                                       False,
                                       "expected result not visible after click")
            except Exception:
                pass
            return (f"{verb} '{desc}' at {coords} but verification shows it did "
                    f"NOT take effect - the expected result is not visible on "
                    f"screen. Do not report success; diagnose and retry if sensible.")
        return (f"{verb} '{desc}' at {coords}; verification could not confirm "
                f"whether it took effect. Say so if it matters.")
    if pc_log:
        pc_log.event("computer.screen_click", description=desc, button=button,
                     clicks=clicks, method="vision", box=str(coords),
                     action="click-center", verify="skipped",
                     result=f"{verb} at {coords}")
    return f"{verb} '{desc}' at {coords}"


def _do_screen_move(description: str) -> str:
    """Locate an element and move the pointer onto it, then VERIFY the pointer
    actually landed there (#2: never assume the move worked)."""
    desc = str(description or "").strip()
    if not desc:
        return "screen_move needs a 'description' of the element to move to."
    coords = _screen_find(desc)
    if not coords:
        if pc_log:
            pc_log.event("computer.screen_move", description=desc,
                         method="vision", result="not-found")
        return f"Element not found on screen: '{desc}'"
    moved = _move(coords[0], coords[1])
    # Pointer landing check: where is the cursor really?
    landed = ""
    verify = "unverified"
    try:
        pos = pyautogui.position()
        if abs(pos[0] - coords[0]) <= 3 and abs(pos[1] - coords[1]) <= 3:
            landed = f"  [pointer verified at {pos[0]},{pos[1]}]"
            verify = "ok"
        else:
            landed = f"  [WARNING: pointer at {pos[0]},{pos[1]}, expected {coords[0]},{coords[1]}]"
            verify = "mismatch"
    except Exception as e:
        landed = f"  [pointer position unreadable: {e}]"
    if pc_log:
        pc_log.event("computer.screen_move", description=desc, method="vision",
                     box=str(coords), action="move-to-centre", verify=verify,
                     result=verify)
    return moved + landed + f"  [element: '{desc}']"


def _do_screen_drag(source: str, target: str) -> str:
    """OBSERVE source → OBSERVE destination → drag → VERIFY the release point."""
    src_desc = str(source or "").strip()
    dst_desc = str(target or "").strip()
    if not src_desc or not dst_desc:
        return "screen_drag needs both a source and a target description."
    src = _screen_find(src_desc)
    if not src:
        if pc_log:
            pc_log.event("computer.screen_drag", source=src_desc,
                         target=dst_desc, result="source-not-found")
        return f"Drag source not found on screen: '{src_desc}'"
    dst = _screen_find(dst_desc)
    if not dst:
        if pc_log:
            pc_log.event("computer.screen_drag", source=src_desc,
                         target=dst_desc, box=str(src),
                         result="target-not-found")
        return f"Drag target not found on screen: '{dst_desc}'"
    _drag(src[0], src[1], dst[0], dst[1])
    if pc_log:
        pc_log.event("computer.screen_drag", source=src_desc, target=dst_desc,
                     box=f"{src}->{dst}", action="drag", verify="released",
                     result="drag completed")
    return (f"Dragged '{src_desc}' at {src} onto '{dst_desc}' at {dst}. "
            f"Verify the drop actually happened before claiming success.")


def _click_with_modifier(params: dict, button: str, clicks: int) -> str:
    """click, optionally holding a modifier key first ("hold Shift and click")."""
    mod = str(params.get("modifier") or "").strip().lower()
    if not mod:
        return _click(params.get("x"), params.get("y"), button, clicks)
    _require_pyautogui()
    key = {"shift": "shift", "ctrl": "ctrl", "control": "ctrl",
           "alt": "alt", "win": "win", "winkey": "win", "super": "win"}.get(mod, mod)
    pyautogui.keyDown(key)
    try:
        res = _click(params.get("x"), params.get("y"), button, clicks)
    finally:
        try:
            pyautogui.keyUp(key)
        except Exception:
            pass
    return res + f"  [held {key} while clicking]"


def computer_control(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    """
    Dispatch table for all computer control actions.

    parameters keys (all optional unless noted):
      action        : (required) one of the actions listed below
      text          : text to type or paste
      x, y          : screen coordinates
      dx, dy        : relative move delta (move_rel)
      button        : 'left' | 'right' (default: left; used by click/screen_click)
      keys          : hotkey string, e.g. 'ctrl+c'
      key           : single key name, e.g. 'enter'
      direction     : 'up' | 'down' | 'left' | 'right'
      amount        : scroll amount (default: 3)
      seconds       : wait duration
      title         : window title fragment for focus_window
      description   : natural-language element description for screen_find/click
      type          : data type for random_data
      field         : memory field name for user_data
      clear_first   : bool, clear field before typing (default: true)
      path          : save path for screenshot (must be inside home dir)

    Actions:
      type          - type text at cursor
      smart_type    - clear field + type (clipboard-backed)
      click         - left click (x,y optional -> current position)
      double_click  - double left click
      right_click   - right click
      move          - move mouse to absolute (x,y); reports where it landed
      move_rel      - move mouse by a relative (dx,dy)
      mouse_position- read the current pointer position + screen size
      drag          - click-drag between two points
      hotkey        - key combination
      press         - single key
      scroll        - scroll the wheel
      copy          - read clipboard
      paste         - write + paste clipboard
      screenshot    - capture screen (safe path only)
      wait          - sleep N seconds
      clear_field   - select-all + delete
      focus_window  - bring window to foreground
      screen_find   - AI element finder (returns x,y in screen coords)
      screen_click  - find element -> click -> (optional) verify
      screen_double_click - find element -> double-click
      screen_right_click  - find element -> right-click
      screen_move   - find element -> move pointer onto it (no click); 'hover' alias
      random_data   - generate fake form data
      user_data     - pull real data from memory

    Element-finding (`screen_*`) is DPI-corrected and uses a labelled coordinate
    grid so the returned point is the element's real position, not a scaled guess.
    """
    params = parameters or {}
    action = params.get("action", "").lower().strip()

    if not action:
        return "No action specified for computer_control."

    if player:
        player.write_log(f"[Computer] {action}")

    _log(f"[ComputerControl] >> {action}  {params}")

    try:

        if action == "type":
            return _type(params.get("text", ""))

        if action == "smart_type":
            return _smart_type(
                params.get("text", ""),
                clear_first=params.get("clear_first", True),
            )

        if action in ("click", "left_click"):
            return _click_with_modifier(params, "left", 1)

        if action in ("middle_click", "mid_click"):
            return _click_with_modifier(params, "middle", 1)

        if action == "double_click":
            return _click_with_modifier(params, "left", 2)

        if action == "right_click":
            return _click_with_modifier(params, "right", 1)

        if action in ("move", "mouse_move"):
            if params.get("x_pct") is not None or params.get("y_pct") is not None:
                return _move_percent(
                    float(params.get("x_pct", 50)),
                    float(params.get("y_pct", 50)),
                )
            return _move(int(params.get("x", 0)), int(params.get("y", 0)))

        if action in ("move_percent", "move_pct"):
            return _move_percent(
                float(params.get("x_pct", 50)),
                float(params.get("y_pct", 50)),
            )

        if action in ("move_center", "go_center", "centre"):
            return _move_percent(50, 50)

        if action in ("move_rel", "move_relative"):
            return _move_rel(int(params.get("dx", 0)), int(params.get("dy", 0)))

        if action in ("arrow_key", "press_arrow", "key_arrow"):
            return _press_arrow(
                params.get("key", params.get("direction", "down")),
                int(params.get("repeat", params.get("amount", 1))),
            )

        if action in ("move_dir", "move_direction", "arrow"):
            """Move mouse in a direction: up/down/left/right with optional steps."""
            direction = params.get("direction", "").lower().strip()
            steps = int(params.get("steps", 50))  # pixels per step
            if direction not in ("up", "down", "left", "right"):
                return "move_dir needs direction: up | down | left | right"
            dx = {"left": -steps, "right": steps}.get(direction, 0)
            dy = {"up": -steps, "down": steps}.get(direction, 0)
            return _move_rel(dx, dy, duration=0.15)

        if action in ("mouse_position", "get_mouse_position"):
            return _mouse_position()

        if action == "hover":
            return _do_screen_move(params.get("description", ""))

        if action == "drag":
            return _drag(
                int(params.get("x1", 0)), int(params.get("y1", 0)),
                int(params.get("x2", 0)), int(params.get("y2", 0)),
            )

        if action == "hotkey":
            raw  = params.get("keys", "")
            keys = [k.strip() for k in raw.split("+")] if isinstance(raw, str) else raw
            return _hotkey(*keys)

        if action == "press":
            return _press(params.get("key", "enter"))

        if action == "scroll":
            return _scroll(
                direction=params.get("direction", "down"),
                amount=int(params.get("amount", 3)),
            )

        if action == "copy":
            return _clipboard_get()

        if action == "paste":
            return _clipboard_paste(params.get("text", ""))

        if action == "screenshot":
            return _screenshot(params.get("path"))

        if action == "screen_find":
            coords = _screen_find(params.get("description", ""))
            return f"{coords[0]},{coords[1]}" if coords else "NOT_FOUND"

        if action == "screen_click":
            return _do_screen_click(
                params.get("description", ""),
                button=params.get("button", "left") or "left",
                clicks=1,
                verify=params.get("verify", None),
            )

        if action == "screen_double_click":
            return _do_screen_click(
                params.get("description", ""), button="left", clicks=2,
                verify=False,
            )

        if action == "screen_right_click":
            return _do_screen_click(
                params.get("description", ""), button="right", clicks=1,
                verify=False,
            )

        if action in ("screen_move", "screen_hover"):
            return _do_screen_move(params.get("description", ""))

        if action == "screen_drag":
            return _do_screen_drag(
                params.get("source") or params.get("description")
                or params.get("item") or "",
                params.get("target") or params.get("destination")
                or params.get("target_description") or "",
            )

        if action == "wait":
            secs = float(params.get("seconds", 1.0))
            secs = min(secs, 30.0)
            time.sleep(secs)
            return f"Waited {secs}s"

        if action == "clear_field":
            return _clear_field()

        if action == "focus_window":
            return _focus_window(params.get("title", ""))

        if action == "random_data":
            dt     = params.get("type", "name")
            result = _random_data(dt)
            _log(f"[ComputerControl] random {dt} -> {result}")
            return result

        if action == "user_data":
            field   = params.get("field", "name")
            profile = _user_profile()
            value   = profile.get(field, "")
            if not value:
                value = _random_data(field)
                _log(f"[ComputerControl] No '{field}' in memory, using random: {value}")
            return value

        return f"Unknown action: '{action}'"

    except Exception as e:
        _log(f"[ComputerControl] {action} failed: {e}")
        return f"computer_control '{action}' failed: {e}"


# ── Tool declaration (auto-discovered by core/action_loader.py) ──────────────
TOOL = {
    "name": "computer_control",
    "description": "Direct computer control: type, click, hotkeys, scroll, move mouse, screenshots, find elements on screen.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "type | smart_type | click | middle_click | double_click | right_click | hotkey | press | scroll | move | move_percent | move_center | move_rel | move_dir | arrow_key | mouse_position | drag | copy | paste | screenshot | wait | clear_field | focus_window | screen_find | screen_click | screen_double_click | screen_right_click | screen_move | screen_drag | hover | random_data | user_data"
            },
            "text": {
                "type": "STRING",
                "description": "Text to type or paste"
            },
            "x": {
                "type": "INTEGER",
                "description": "X coordinate (absolute, for click/move/drag)"
            },
            "y": {
                "type": "INTEGER",
                "description": "Y coordinate (absolute, for click/move/drag)"
            },
            "x_pct": {
                "type": "NUMBER",
                "description": "X position as % of screen width (0-100) for move_percent; 50 = centre"
            },
            "y_pct": {
                "type": "NUMBER",
                "description": "Y position as % of screen height (0-100) for move_percent; 50 = centre"
            },
            "repeat": {
                "type": "INTEGER",
                "description": "arrow_key only: how many times to press the key (default 1)"
            },
            "dx": {
                "type": "INTEGER",
                "description": "Relative X delta for move_rel"
            },
            "dy": {
                "type": "INTEGER",
                "description": "Relative Y delta for move_rel"
            },
            "button": {
                "type": "STRING",
                "description": "Mouse button for click/screen_click: left | right (default: left)"
            },
            "modifier": {
                "type": "STRING",
                "description": "click only: hold this key while clicking, e.g. shift ('hold Shift and click'). One of shift | ctrl | alt | win."
            },
            "target": {
                "type": "STRING",
                "description": "screen_drag only: the destination element to drag onto (with `description` as the source)"
            },
            "keys": {
                "type": "STRING",
                "description": "Key combination e.g. 'ctrl+c'"
            },
            "key": {
                "type": "STRING",
                "description": "Single key e.g. 'enter'"
            },
            "direction": {
                "type": "STRING",
                "description": "up | down | left | right"
            },
            "amount": {
                "type": "INTEGER",
                "description": "Scroll amount (default: 3)"
            },
            "seconds": {
                "type": "NUMBER",
                "description": "Seconds to wait"
            },
            "title": {
                "type": "STRING",
                "description": "Window title for focus_window"
            },
            "description": {
                "type": "STRING",
                "description": "Element description for screen_find/screen_click/screen_move/screen_drag (e.g. 'the blue Send button'); for screen_drag it is the SOURCE"
            },
            "type": {
                "type": "STRING",
                "description": "Data type for random_data"
            },
            "field": {
                "type": "STRING",
                "description": "Field for user_data: name|email|city"
            },
            "clear_first": {
                "type": "BOOLEAN",
                "description": "Clear field before typing (default: true)"
            },
            "path": {
                "type": "STRING",
                "description": "Save path for screenshot"
            },
            "verify": {
                "type": "BOOLEAN",
                "description": "screen_click only: after clicking, take a fresh screenshot and check the click actually worked (default: config verify_clicks). Set false to skip verification."
            },
            "direction": {
                "type": "STRING",
                "description": "move_dir only: up | down | left | right"
            },
            "steps": {
                "type": "INTEGER",
                "description": "move_dir only: pixels to move (default: 50)"
            }
        },
        "required": [
            "action"
        ]
    },
    "handler": computer_control,
}
