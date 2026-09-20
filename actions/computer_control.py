#computer_control.py
import io
import json
import platform
import random
import re
import string
import subprocess
import sys
import time
from pathlib import Path

# The engine for every physical action. core/pc_input is a dependency-free
# Windows backend (ctypes / SendInput), so this whole tool works inside the
# one-file ICE.exe where pyautogui is deliberately NOT shipped. pyautogui
# remains only as the non-Windows fallback.
try:
    import pyautogui
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE    = 0.05
    _PYAUTOGUI = True
except ImportError:
    _PYAUTOGUI = False

from core import pc_input    # noqa: E402 — engine must exist for this module to load
from core import confirm      # noqa: E402 — irreversible-process gate
from core.undo import push_undo  # noqa: E402 — reversible changes register here

if platform.system() == "Windows":
    _WIN_HIDE: dict = {"creationflags": subprocess.CREATE_NO_WINDOW}
else:
    _WIN_HIDE: dict = {}

try:
    import pyperclip
    _PYPERCLIP = True
except ImportError:
    _PYPERCLIP = False


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


_BASE         = _base_dir()
_CONFIG_PATH  = _BASE / "config" / "api_keys.json"
_MEMORY_PATH  = _BASE / "memory" / "long_term.json"


def _load_config() -> dict:
    try:
        return json.loads(_CONFIG_PATH.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def _platform_os() -> str:
    return {"Windows": "windows", "Darwin": "mac", "Linux": "linux"}.get(
        platform.system(), "linux"
    )


def _get_os() -> str:
    return _load_config().get("os_system", _platform_os()).lower()


def _get_api_key() -> str:
    return _load_config().get("gemini_api_key", "")


_SAFE_SCREENSHOT_ROOTS = (Path.home(),)


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
            data     = json.loads(_MEMORY_PATH.read_text(encoding="utf-8-sig"))
            identity = data.get("identity", {})
            return {k: v.get("value", "") for k, v in identity.items()}
    except Exception:
        pass
    return {}


# ── engine helpers ────────────────────────────────────────────────────────────

def _engine(fn, *args, **kwargs):
    """Run a pc_input operation, turning failures into a spoken result string."""
    try:
        result = fn(*args, **kwargs)
        return result if isinstance(result, str) else "" if result is None else str(result)
    except pc_input._WinNotSupported as e:
        return f"Not available here: {e}"
    except Exception as e:
        return f"Failed: {e}"


def _type(text: str, interval: float = 0.03) -> str:
    time.sleep(0.3)
    return pc_input.key_type(text, interval=interval) or f"Typed: {text[:60]}{'…' if len(text) > 60 else ''}"


def _smart_type(text: str, clear_first: bool = True) -> str:
    if clear_first:
        _clear_field()
        time.sleep(0.1)

    if len(text) > 20 and _PYPERCLIP:
        try:
            pyperclip.copy(text)
            time.sleep(0.1)
            paste_key = "command" if _get_os() == "mac" else "ctrl"
            if _get_os() == "mac" and _PYAUTOGUI:
                pyautogui.hotkey("command", "v")
            else:
                pc_input.key_hotkey("ctrl", "v")
            return f"Smart-typed (clipboard): {text[:60]}{'…' if len(text) > 60 else ''}"
        except Exception:
            pass  # fall through to plain typing

    pc_input.key_type(text, interval=0.04)
    return f"Smart-typed: {text[:60]}{'…' if len(text) > 60 else ''}"


def _click(x=None, y=None, button: str = "left", clicks: int = 1) -> str:
    try:
        pc_input.mouse_click(x, y, button=button, clicks=clicks)
    except pc_input._WinNotSupported as e:
        if _PYAUTOGUI:
            try:
                if x is not None and y is not None:
                    pyautogui.click(x, y, button=button, clicks=clicks)
                else:
                    pyautogui.click(button=button, clicks=clicks)
                return f"Clicked at ({x}, {y}) [{button}]" if x is not None else f"Clicked at current position [{button}]"
            except Exception as e2:
                return f"click failed: {e2}"
        return f"Not available here: {e}"
    where = f" at ({x}, {y})" if x is not None and y is not None else ""
    label = "Double-clicked" if clicks == 2 else "Clicked"
    return f"{label} [{button}]{where}"


def _hotkey(*keys) -> str:
    try:
        pc_input.key_hotkey(*keys)
    except pc_input._WinNotSupported as e:
        if _PYAUTOGUI:
            try:
                pyautogui.hotkey(*keys)
            except Exception as e2:
                return f"hotkey failed: {e2}"
            return f"Hotkey: {'+'.join(keys)}"
        return f"Not available here: {e}"
    return f"Hotkey: {'+'.join(keys)}"


def _press(key: str, hold_ms: float = 0) -> str:
    try:
        if hold_ms and hold_ms > 0:
            pc_input.key_hold(key, hold_ms / 1000.0)
        else:
            pc_input.key_press(key)
    except pc_input._WinNotSupported as e:
        if _PYAUTOGUI:
            try:
                pyautogui.press(key)
            except Exception as e2:
                return f"press failed: {e2}"
            return f"Pressed: {key}"
        return f"Not available here: {e}"
    return f"Pressed: {key}"


def _scroll(direction: str = "down", amount: int = 3) -> str:
    direction = direction.lower()
    if direction in ("up", "down"):
        delta = int(amount) if direction == "up" else -int(amount)
        try:
            pc_input.mouse_scroll(delta)
        except pc_input._WinNotSupported as e:
            if _PYAUTOGUI:
                try:
                    pyautogui.scroll(delta)
                except Exception as e2:
                    return f"scroll failed: {e2}"
                return f"Scrolled {direction} ×{amount}"
            return f"Not available here: {e}"
        except Exception as e:
            return f"scroll failed: {e}"
        return f"Scrolled {direction} ×{amount}"
    if direction in ("left", "right"):
        delta = int(amount) if direction == "right" else -int(amount)
        try:
            pc_input.mouse_wheel_raw(delta, horizontal=True)
        except pc_input._WinNotSupported as e:
            if _PYAUTOGUI:
                try:
                    pyautogui.hscroll(delta)
                except Exception as e2:
                    return f"scroll failed: {e2}"
                return f"Scrolled {direction} ×{amount}"
            return f"Not available here: {e}"
        except Exception as e:
            return f"scroll failed: {e}"
        return f"Scrolled {direction} ×{amount}"
    return f"Unknown scroll direction: '{direction}' (use up|down|left|right)"


def _move(x: int, y: int, relative: bool = False) -> str:
    try:
        pc_input.mouse_move(x, y, relative=relative)
    except pc_input._WinNotSupported as e:
        if _PYAUTOGUI:
            try:
                pyautogui.moveTo(x, y, duration=0.3)
            except Exception as e2:
                return f"move failed: {e2}"
            return f"Mouse → ({x}, {y})"
        return f"Not available here: {e}"
    return f"Mouse → ({x}, {y})" + (" (relative)" if relative else "")


def _drag(x1: int, y1: int, x2: int, y2: int) -> str:
    return _engine(pc_input.mouse_drag, x1, y1, x2, y2) or f"Dragged ({x1},{y1}) → ({x2},{y2})"


def _clipboard_get() -> str:
    return pc_input.clipboard_get()


def _clipboard_set(text: str, paste: bool = True) -> str:
    try:
        pc_input.clipboard_set(text)
    except Exception as e:
        return f"clipboard_set failed: {e}"
    if paste:
        try:
            _hotkey("ctrl", "v")
        except Exception:
            pass
    return f"Clipboard set: {text[:60]}{'…' if len(text) > 60 else ''}"


def _screenshot(save_path: str | None = None) -> str:
    path = _safe_screenshot_path(save_path)
    try:
        # pc_input.screenshot saves when a path is given and returns it.
        result = pc_input.screenshot(str(path))
        return f"Screenshot saved: {result}"
    except pc_input._WinNotSupported as e:
        if _PYAUTOGUI:
            try:
                img = pyautogui.screenshot()
                img.save(str(path))
                return f"Screenshot saved: {path}"
            except Exception as e2:
                return f"screenshot failed: {e2}"
        return f"Not available here: {e}"
    except Exception as e:
        return f"Screenshot failed: {e}"


def _clear_field() -> str:
    try:
        _hotkey("ctrl", "a")
        time.sleep(0.1)
        _press("delete")
    except Exception as e:
        return f"clear_field failed: {e}"
    return "Field cleared"


# ── windows / processes / system ──────────────────────────────────────────────

def _window_list(limit: int = 30) -> str:
    try:
        rows = pc_input.window_list(limit=int(limit))
    except pc_input._WinNotSupported as e:
        return f"Not available here: {e}"
    except Exception as e:
        return f"window_list failed: {e}"
    if not rows:
        return "No visible windows found."
    return "\n".join(f"{hwnd} | {title} | pid {pid}"
                     for hwnd, title, pid in rows[:int(limit)])


def _window_op(action: str, title: str, **kw):
    fn = {
        "window_focus":   pc_input.window_focus,
        "window_minimize": pc_input.window_minimize,
        "window_maximize": pc_input.window_maximize,
        "window_restore":  pc_input.window_restore,
        "window_close":    pc_input.window_close,
    }.get(action)
    if fn is None:
        return f"Unknown window action: '{action}'"
    try:
        ok = fn(title)
    except pc_input._WinNotSupported as e:
        return f"Not available here: {e}"
    except Exception as e:
        return f"{action} failed: {e}"
    if not ok:
        return f"Window not found: '{title}'"
    verb = action.removeprefix("window_").replace("_", " ")
    return f"{verb.title()} window: {title}"


def _window_move(title: str, x: int, y: int, width=None, height=None) -> str:
    try:
        ok = pc_input.window_move(title, int(x), int(y),
                                  int(width) if width is not None else None,
                                  int(height) if height is not None else None)
    except pc_input._WinNotSupported as e:
        return f"Not available here: {e}"
    except Exception as e:
        return f"window_move failed: {e}"
    if not ok:
        return f"Window not found: '{title}'"
    dims = f", {width}×{height}" if width or height else ""
    return f"Moved window '{title}' to ({x}, {y}){dims}"


def _foreground_window() -> str:
    return pc_input.foreground_window_title() or "(none visible)"


def _process_list(filter_str: str = "") -> str:
    try:
        rows = pc_input.process_list(filter_str)
    except Exception as e:
        return f"process_list failed: {e}"
    if not rows:
        return f"No matching processes" + (f" for '{filter_str}'" if filter_str else "") + "."
    lines = [f"{pid} | {name} | {mem:.0f} MB" for pid, name, mem in rows[:40]]
    total = f" ({len(rows)} total)" if len(rows) > 40 else ""
    return "Processes:\n" + "\n".join(lines) + total


def _process_start(command: str) -> str:
    if not command:
        return "process_start needs a command, app name or path."
    try:
        return pc_input.process_start(command)
    except Exception as e:
        return f"process_start failed: {e}"


def _process_kill(name_or_pid) -> str:
    try:
        return pc_input.process_kill(name_or_pid)
    except Exception as e:
        return f"process_kill failed: {e}"


# ── volumes / brightness / media with undo ────────────────────────────────────

def _volume_up_down(action: str) -> str:
    before = pc_input.volume_get()
    try:
        if action == "volume_up":
            pc_input.volume_up(steps=int(1))
        elif action == "volume_down":
            pc_input.volume_down(steps=int(1))
        else:
            pc_input.volume_mute()
    except pc_input._WinNotSupported as e:
        return f"Not available here: {e}"
    except Exception as e:
        return f"{action} failed: {e}"
    if before is not None:
        push_undo(f"volume ({action})",
                  lambda b=before: (pc_input.volume_set(b), f"Volume back to {b}%.")[1])
    return f"Done: {action}."


def _volume_set(value: int) -> str:
    try:
        before = pc_input.volume_get()
        pc_input.volume_set(int(value))
    except pc_input._WinNotSupported as e:
        return f"Not available here: {e}"
    except Exception as e:
        return f"volume_set failed: {e}"
    if before is not None:
        push_undo(f"volume {before}% → {int(value)}%",
                  lambda b=before: (pc_input.volume_set(b), f"Volume back to {b}%.")[1])
    return f"Volume set to {int(value)}%."


def _brightness_set(value: int) -> str:
    try:
        before = pc_input.brightness_get()
        pc_input.brightness_set(int(value))
    except pc_input._WinNotSupported as e:
        return f"Not available here: {e}"
    except Exception as e:
        return f"brightness_set failed: {e}"
    if before is not None:
        push_undo(f"brightness {before}% → {int(value)}%",
                  lambda b=before: (pc_input.brightness_set(b), f"Brightness back to {b}%.")[1])
    return f"Brightness set to {int(value)}%."


def _media(action: str) -> str:
    fn = {
        "prev":        pc_input.media_prev,
        "next":        pc_input.media_next,
        "play_pause":  pc_input.media_play_pause,
        "stop":        pc_input.media_stop,
    }.get(action)
    if fn is None:
        return f"Unknown media action: '{action}' (use prev|next|play_pause|stop)"
    try:
        fn()
    except pc_input._WinNotSupported as e:
        return f"Not available here: {e}"
    except Exception as e:
        return f"media {action} failed: {e}"
    return f"Media: {action}"


def _system_info() -> str:
    try:
        info = pc_input.system_info()
    except Exception as e:
        return f"system_info failed: {e}"
    parts = [f"OS {info.get('system')} {info.get('release')} ({info.get('machine')})",
             f"{info.get('cpu_count', 0)} CPUs"]
    if info.get("processor"):
        parts.append(info["processor"])
    if info.get("ram_total_gb"):
        parts.append(f"RAM {info.get('ram_used_gb')}/{info.get('ram_total_gb')} GB used "
                     f"({info.get('ram_percent')}%)")
    if info.get("disk_total_gb"):
        parts.append(f"Disk {info.get('disk_free_gb')}/{info.get('disk_total_gb')} GB free")
    if info.get("uptime_hours"):
        parts.append(f"Up {info.get('uptime_hours')}h")
    if info.get("battery_percent") is not None:
        plug = "plugged in" if info.get("battery_plugged") else "on battery"
        parts.append(f"Battery {info.get('battery_percent')}% ({plug})")
    return "System: " + " | ".join(parts)


def _screen_size() -> str:
    try:
        w, h = pc_input.screen_size()
        return f"Screen: {w}×{h}"
    except pc_input._WinNotSupported as e:
        return f"Not available here: {e}"
    except Exception as e:
        return f"screen_size failed: {e}"


def _monitors() -> str:
    try:
        mons = pc_input.screen_monitors()
    except pc_input._WinNotSupported as e:
        return f"Not available here: {e}"
    except Exception as e:
        return f"monitors failed: {e}"
    return f"{len(mons)} monitor(s): " + "; ".join(
        f"{m['width']}×{m['height']} at ({m['x']},{m['y']})" for m in mons)


# ── screen find (vision) ──────────────────────────────────────────────────────

def _screen_find(description: str) -> tuple[int, int] | None:
    api_key = _get_api_key()
    if not api_key:
        print("[ComputerControl] ⚠️ No API key for screen_find")
        return None

    try:
        from google import genai
        from google.genai import types as gtypes

        w, h = pc_input.screen_size()
        img  = pc_input.screenshot()          # PIL image (pillow ships in exe)
        if not hasattr(img, "save"):
            print("[ComputerControl] ⚠️ screenshot backend returned no image")
            return None
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        image_bytes = buf.getvalue()

        prompt = (
            f"This is a screenshot of a {w}×{h} pixel screen. "
            f"Locate the UI element described as: '{description}'. "
            f"Reply with ONLY the center coordinates as: x,y "
            f"If the element is not visible, reply: NOT_FOUND"
        )

        from core import gemini
        response = gemini.call(
            [gtypes.Part.from_bytes(data=image_bytes, mime_type="image/png"), prompt],
            tier=gemini.FAST, timeout_ms=20_000,
        )
        if response is None:
            return None

        text = (response.text or "").strip()
        if "NOT_FOUND" in text.upper():
            return None

        match = re.search(r"(\d+)\s*,\s*(\d+)", text)
        if match:
            return int(match.group(1)), int(match.group(2))

    except Exception as e:
        print(f"[ComputerControl] ⚠️ screen_find failed: {e}")

    return None


# ── main dispatch ─────────────────────────────────────────────────────────────

def computer_control(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    """
    Full PC control: mouse, keyboard, clipboard, windows, processes, volume,
    brightness, media, screenshots and system queries. Works with no extra
    packages (even inside the ICE.exe build).

    parameters keys (all optional unless noted):
      action        : (required) one of the actions listed below
      text          : text to type, paste, or put on the clipboard
      x, y          : coordinates (or dx,dy when relative=1)
      x1, y1, x2, y2: for drag
      button        : 'left' | 'right' | 'middle' (default: left)
      clicks        : number of clicks (default: 1)
      relative      : 1 = move by delta instead of absolute
      keys          : hotkey string, e.g. 'ctrl+c'
      key           : single key name, e.g. 'enter'
      hold_ms       : hold a key for this many milliseconds
      direction     : 'up' | 'down' | 'left' | 'right'
      amount        : scroll amount (default: 3)
      horizontal    : scroll sideways instead of vertically
      seconds       : wait duration
      title         : window title fragment for window actions
      width, height : window size
      name / pid    : process name or PID
      command       : command / path / URL to launch
      value         : volume or brightness level 0-100
      filter        : process name filter
      description   : natural-language element description for screen_find/click
      type          : data type for random_data
      field         : memory field name for user_data
      clear_first   : bool, clear field before typing (default: true)
      path          : save path for screenshot (must be inside home dir)

    Actions:
      Mouse:       mouse_position | mouse_move | mouse_click | click |
                   double_click | right_click | middle_click | drag | scroll |
                   wheel_up | wheel_down
      Keyboard:    type | smart_type | hotkey | press | key_hold |
                   clear_field
      Clipboard:   copy | clipboard_get | clipboard_set | clipboard_clear
      Windows:     window_list | window_focus | window_minimize |
                   window_maximize | window_restore | window_move |
                   window_close | foreground_window
      Processes:   process_list | process_start | process_kill (asks the user
                   to confirm on screen first)
      Audio:       volume_get | volume_set | volume_up | volume_down | mute
                   (all reversible with the `undo` tool)
      Media keys:  media (prev|next|play_pause|stop)
      System:      brightness_set (undoable) | lock_screen | sleep_display |
                   show_desktop | run_dialog | system_info | screen_size |
                   monitors | screenshot
      Vision:      screen_find | screen_click
      Utilities:   random_data | user_data | wait
    """
    params = parameters or {}
    action = params.get("action", "").lower().strip()

    if not action:
        return "No action specified for computer_control."

    if player:
        player.write_log(f"[Computer] {action}")

    print(f"[ComputerControl] ▶ {action}  {params}")

    try:

        # ── mouse ───────────────────────────────────────────────────────────
        if action == "mouse_position":
            try:
                x, y = pc_input.mouse_position()
                return f"{x},{y}"
            except pc_input._WinNotSupported as e:
                if _PYAUTOGUI:
                    return f"{pyautogui.position()[0]},{pyautogui.position()[1]}"
                return f"Not available here: {e}"
            except Exception as e:
                return f"mouse_position failed: {e}"

        if action == "mouse_move":
            return _move(int(params.get("x", 0)), int(params.get("y", 0)),
                         relative=bool(params.get("relative", False)))

        if action in ("mouse_click", "click", "left_click"):
            return _click(params.get("x"), params.get("y"), "left",
                          int(params.get("clicks", 1) or 1))

        if action == "double_click":
            return _click(params.get("x"), params.get("y"), "left", 2)

        if action == "right_click":
            return _click(params.get("x"), params.get("y"), "right", 1)

        if action == "middle_click":
            return _click(params.get("x"), params.get("y"), "middle", 1)

        if action == "move":
            return _move(int(params.get("x", 0)), int(params.get("y", 0)))

        if action == "drag":
            return _drag(
                int(params.get("x1", 0)), int(params.get("y1", 0)),
                int(params.get("x2", 0)), int(params.get("y2", 0)),
            )

        if action == "scroll":
            return _scroll(
                direction=params.get("direction", "down"),
                amount=int(params.get("amount", 3)),
            )

        if action == "wheel_up":
            return _engine(pc_input.mouse_wheel_up, int(params.get("amount", 1))) or "Scrolled up"

        if action == "wheel_down":
            return _engine(pc_input.mouse_wheel_down, int(params.get("amount", 1))) or "Scrolled down"

        # ── keyboard ────────────────────────────────────────────────────────
        if action == "type":
            return _type(params.get("text", ""))

        if action == "smart_type":
            return _smart_type(
                params.get("text", ""),
                clear_first=params.get("clear_first", True),
            )

        if action == "hotkey":
            raw  = params.get("keys", "")
            keys = [k.strip() for k in raw.split("+")] if isinstance(raw, str) else raw
            return _hotkey(*keys)

        if action == "press":
            return _press(params.get("key", "enter"),
                          float(params.get("hold_ms", 0) or 0))

        if action == "key_hold":
            return _press(params.get("key", ""),
                          float(params.get("hold_ms", 500) or 500))

        if action == "clear_field":
            return _clear_field()

        # ── clipboard ───────────────────────────────────────────────────────
        if action == "copy":
            text = _clipboard_get()
            return f"Clipboard: {text[:200]}" if text else "(clipboard empty)"

        if action == "clipboard_get":
            text = _clipboard_get()
            return text if text else "(clipboard empty)"

        if action == "clipboard_set":
            return _clipboard_set(params.get("text", ""), paste=False)

        if action == "clipboard_clear":
            try:
                pc_input.clipboard_clear()
                return "Clipboard cleared."
            except Exception as e:
                return f"clipboard_clear failed: {e}"

        if action == "paste":
            return _clipboard_set(params.get("text", ""), paste=True)

        # ── windows ─────────────────────────────────────────────────────────
        if action == "window_list":
            return _window_list(int(params.get("limit", 30)))

        if action in ("window_focus", "window_minimize", "window_maximize",
                      "window_restore", "window_close"):
            title = params.get("title", "")
            if not title:
                return f"{action} needs a window `title`."
            return _window_op(action, title)

        if action == "window_move":
            title = params.get("title", "")
            if not title:
                return "window_move needs a window `title`, x and y."
            return _window_move(
                title,
                int(params.get("x", 0)), int(params.get("y", 0)),
                params.get("width"), params.get("height"),
            )

        if action == "foreground_window":
            return f"Foreground window: {_foreground_window()}"

        # ── processes ───────────────────────────────────────────────────────
        if action == "process_list":
            return _process_list(params.get("filter", ""))

        if action == "process_start":
            return _process_start(params.get("command", ""))

        if action == "process_kill":
            target = params.get("name") or params.get("pid")
            if target is None:
                return "process_kill needs a process `name` or `pid`."
            if confirm.pending_title():
                return ("There is already a confirmation waiting on screen. "
                        "Ask the user to answer that one first.")
            return confirm.request(
                key=f"kill_{target}",
                title=f"Kill process '{target}'?",
                detail=("This force-stops the process. Anything it has not "
                        "saved yet will be lost."),
                run=lambda t=target: _process_kill(t),
            )

        # ── audio / display ─────────────────────────────────────────────────
        if action == "volume_get":
            v = pc_input.volume_get()
            return f"Volume is {v}%" if v is not None else "Volume level not readable here."

        if action == "volume_set":
            return _volume_set(int(params.get("value", 50) or 50))

        if action in ("volume_up", "volume_down", "mute"):
            return _volume_up_down(action)

        if action == "media":
            return _media(str(params.get("key", "")).lower().replace(" ", "_"))

        if action == "brightness_set":
            return _brightness_set(int(params.get("value", 50) or 50))

        if action in ("lock_screen", "sleep_display", "show_desktop", "run_dialog"):
            fn = {
                "lock_screen":   pc_input.lock_screen,
                "sleep_display": pc_input.sleep_display,
                "show_desktop":  pc_input.show_desktop,
                "run_dialog":    pc_input.run_dialog,
            }[action]
            try:
                fn()
            except pc_input._WinNotSupported as e:
                if _PYAUTOGUI:
                    try:
                        if action == "lock_screen":
                            pyautogui.hotkey("win", "l")
                        elif action == "show_desktop":
                            pyautogui.hotkey("win", "d")
                        elif action == "run_dialog":
                            pyautogui.hotkey("win", "r")
                        else:
                            return f"Not available here: {e}"
                        return f"Done: {action}."
                    except Exception as e2:
                        return f"{action} failed: {e2}"
                return f"Not available here: {e}"
            except Exception as e:
                return f"{action} failed: {e}"
            return f"Done: {action}."

        # ── system info ─────────────────────────────────────────────────────
        if action == "system_info":
            return _system_info()

        if action == "screen_size":
            return _screen_size()

        if action == "monitors":
            return _monitors()

        if action == "screenshot":
            return _screenshot(params.get("path"))

        # ── vision / utilities ──────────────────────────────────────────────
        if action == "screen_find":
            coords = _screen_find(params.get("description", ""))
            return f"{coords[0]},{coords[1]}" if coords else "NOT_FOUND"

        if action == "screen_click":
            desc   = params.get("description", "")
            coords = _screen_find(desc)
            if coords:
                time.sleep(0.2)
                _click(x=coords[0], y=coords[1])
                return f"Clicked '{desc}' at {coords}"
            return f"Element not found on screen: '{desc}'"

        if action == "wait":
            secs = float(params.get("seconds", 1.0))
            secs = min(secs, 30.0)
            time.sleep(secs)
            return f"Waited {secs}s"

        if action == "random_data":
            dt     = params.get("type", "name")
            result = _random_data(dt)
            print(f"[ComputerControl] 🎲 random {dt} → {result}")
            return result

        if action == "user_data":
            field   = params.get("field", "name")
            profile = _user_profile()
            value   = profile.get(field, "")
            if not value:
                value = _random_data(field)
                print(f"[ComputerControl] ⚠️ No '{field}' in memory, using random: {value}")
            return value

        return f"Unknown action: '{action}'"

    except Exception as e:
        print(f"[ComputerControl] ❌ {action}: {e}")
        return f"computer_control '{action}' failed: {e}"


# ── Tool declaration (auto-discovered by core/action_loader.py) ──────────────
TOOL = {
    "name": "computer_control",
    "description": "Full extreme PC control in one tool: mouse (move, click, drag, scroll, wheel, position — left/right/middle/double), keyboard (type ANY text including Hindi, hotkeys like ctrl+c or win+d, key holds), clipboard (get/set/clear), windows (list, focus, minimize, maximize, restore, move, close), processes (list, start, kill — kill asks the user to confirm on screen first), volume & brightness (set or nudge, reversible with the `undo` tool), media keys (play/pause/next/prev), lock screen, sleep display, show desktop, run dialog, system info, screen size, multi-monitor info, screenshots, and AI screen-finding. Works with zero extra packages — usable on any PC even inside the app's .exe.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "mouse_position | mouse_move | mouse_click | click | double_click | right_click | middle_click | drag | scroll | wheel_up | wheel_down | type | smart_type | hotkey | press | key_hold | clear_field | copy | clipboard_get | clipboard_set | clipboard_clear | paste | window_list | window_focus | window_minimize | window_maximize | window_restore | window_move | window_close | foreground_window | process_list | process_start | process_kill | volume_get | volume_set | volume_up | volume_down | mute | media | brightness_set | lock_screen | sleep_display | show_desktop | run_dialog | system_info | screen_size | monitors | screenshot | screen_find | screen_click | random_data | user_data | wait"
            },
            "text": {
                "type": "STRING",
                "description": "Text to type, paste, or put on the clipboard"
            },
            "x": {
                "type": "INTEGER",
                "description": "X coordinate (or dx when relative=1)"
            },
            "y": {
                "type": "INTEGER",
                "description": "Y coordinate (or dy when relative=1)"
            },
            "x1": {"type": "INTEGER", "description": "Drag start X"},
            "y1": {"type": "INTEGER", "description": "Drag start Y"},
            "x2": {"type": "INTEGER", "description": "Drag end X"},
            "y2": {"type": "INTEGER", "description": "Drag end Y"},
            "button": {
                "type": "STRING",
                "description": "left | right | middle (default: left)"
            },
            "clicks": {
                "type": "INTEGER",
                "description": "Number of clicks (default: 1; 2 = double)"
            },
            "relative": {
                "type": "BOOLEAN",
                "description": "Move by delta (x,y) instead of absolute coordinates"
            },
            "keys": {
                "type": "STRING",
                "description": "Key combination e.g. 'ctrl+c'"
            },
            "key": {
                "type": "STRING",
                "description": "Single key e.g. 'enter' (or media action for `media`)"
            },
            "hold_ms": {
                "type": "INTEGER",
                "description": "Hold a key for this many milliseconds"
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
                "description": "Window title fragment for window actions"
            },
            "width": {"type": "INTEGER", "description": "Window width for window_move"},
            "height": {"type": "INTEGER", "description": "Window height for window_move"},
            "name": {
                "type": "STRING",
                "description": "Process name for process_kill / process_list filter"
            },
            "pid": {
                "type": "INTEGER",
                "description": "Process PID for process_kill"
            },
            "command": {
                "type": "STRING",
                "description": "Command / app / path / URL for process_start"
            },
            "value": {
                "type": "INTEGER",
                "description": "Volume or brightness level 0-100"
            },
            "filter": {
                "type": "STRING",
                "description": "Process name filter for process_list"
            },
            "limit": {"type": "INTEGER", "description": "Max entries (window_list/process_list)"},
            "description": {
                "type": "STRING",
                "description": "Element description for screen_find/screen_click"
            },
            "type": {"type": "STRING", "description": "Data type for random_data"},
            "field": {"type": "STRING", "description": "Field for user_data: name|email|city"},
            "clear_first": {"type": "BOOLEAN", "description": "Clear field before typing (default: true)"},
            "path": {"type": "STRING", "description": "Save path for screenshot"}
        },
        "required": ["action"]
    },
    "handler": computer_control,
}