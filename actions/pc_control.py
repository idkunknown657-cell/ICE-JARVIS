"""
actions/pc_control.py — running programs and open windows.

The computer_settings tool owns the machine's *settings* (volume, brightness,
typing, dark mode, power, WiFi…) and open_app owns *launching* apps. This action
fills the gap that was actually missing: seeing what is running, ending a
program, and arranging windows.

Coverage:
    list_windows      — every visible top-level window, title + process
    focus_window      — bring a window to the front by title
    snap_window       — snap/maximise/minimise the active window (Win+arrows)
    list_processes    — running processes by CPU/memory, optional name filter
    find_process      — locate a process by name (returns pid + usage)
    kill_process      — end a process (name or pid) — goes through the on-screen
                        confirmation gate, because a running program can hold
                        your unsaved work
    start_process     — open a file, folder, URL or command path with the OS
    system_uptime     — how long the machine has been up

processes/windows are read through psutil and the Win32 API; everything else is
a pyautogui hotkey. All of it is wrapped so a missing dependency or a non-Windows
box degrades to a clear message instead of a crash.
"""
from __future__ import annotations

import os
import platform
import subprocess
import time

try:
    import psutil
    _PSUTIL = True
except ImportError:
    _PSUTIL = False

try:
    import pyautogui
    _PYAUTOGUI = True
except ImportError:
    _PYAUTOGUI = False

try:
    from core import confirm
    _CONFIRM = True
except Exception:
    _CONFIRM = False

_SYSTEM = platform.system()


# Friendly variants the model might say, mapped to canonical action names.
_ACTION_ALIASES = {
    "list_window": "list_windows", "list": "list_windows",
    "focus": "focus_window", "focuswindow": "focus_window",
    "snap": "snap_window", "snapping": "snap_window",
    "snap_window_windows": "snap_window", "snap_window": "snap_window",
    "kill": "kill_process", "end_process": "kill_process",
    "kill_process": "kill_process", "stop_process": "kill_process",
    "terminate": "kill_process",
    "start": "start_process", "start_process": "start_process",
    "open_process": "start_process", "launch_process": "start_process",
    "find": "find_process", "find_process": "find_process",
    "running": "list_processes", "list_process": "list_processes",
    "processes": "list_processes",
    "uptime": "system_uptime", "system_uptime": "system_uptime",
}


# ── Windows window walk (Win32) ───────────────────────────────────────────────
def _win_windows():
    """Dicts {hwnd, pid, title} for every visible, titled top-level window
    (Windows only)."""
    if _SYSTEM != "Windows":
        return []
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:
        return []

    user32 = ctypes.windll.user32
    out = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _cb(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value.strip()
        if not title:
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        out.append({"hwnd": int(hwnd), "pid": int(pid.value), "title": title})
        return True

    try:
        user32.EnumWindows(_cb, 0)
    except Exception:
        return []
    return out


def _process_name(pid: int) -> str:
    if not _PSUTIL:
        return ""
    try:
        p = psutil.Process(pid)
        return p.name()
    except Exception:
        return ""


def _format_windows(wins, limit):
    lines = []
    for w in wins:
        proc = _process_name(w["pid"])
        lines.append(f"• {w['title']}" + (f"  ({proc}, pid {w['pid']})" if proc else ""))
    if len(lines) > limit:
        lines = lines[:limit]
        lines.append(f"… and {len(wins) - limit} more.")
    return "\n".join(lines) or "No visible windows with a title were found."


# ── Process walk (psutil) ─────────────────────────────────────────────────────
def _walk_processes(query: str = ""):
    if not _PSUTIL:
        return []
    q = (query or "").strip().lower()
    procs = []
    for p in psutil.process_iter(["pid", "name", "cpu_percent",
                                  "memory_percent", "username"]):
        try:
            info = p.info
            if q and q not in (info.get("name") or "").lower():
                continue
            procs.append(info)
        except Exception:
            continue
    return procs


def _format_processes(procs, limit):
    if not procs:
        return "No matching processes were found."
    procs = sorted(procs, key=lambda i: (i.get("cpu_percent") or 0.0),
                   reverse=True)[:limit]
    rows = []
    for p in procs:
        cpu = f"{p.get('cpu_percent') or 0.0:.1f}% cpu"
        mem = f"{p.get('memory_percent') or 0.0:.1f}% ram"
        rows.append(f"• {p.get('name')}  (pid {p.get('pid')}, {cpu}, {mem})")
    return "\n".join(rows)


def _find_pids(query: str = "", pid: int | None = None):
    q = (query or "").strip().lower()
    found = []
    for p in psutil.process_iter(["pid", "name"]):
        try:
            if pid is not None and p.info["pid"] == pid:
                found.append(p)
            elif q and q in (p.info["name"] or "").lower():
                found.append(p)
        except Exception:
            continue
    return found


# ── Handlers ──────────────────────────────────────────────────────────────────

def _list_windows(params):
    wins = sorted(_win_windows(), key=lambda w: w["title"].lower())
    query = (params.get("query") or "").strip().lower()
    if query:
        wins = [w for w in wins if query in w["title"].lower()]
    limit = max(1, min(30, int(params.get("limit", 15) or 15)))
    return _format_windows(wins, limit)


def _focus_window(params):
    title = (params.get("title") or params.get("query") or "").strip()
    if not title:
        return "Give me a window title to focus."
    wins = _win_windows()
    if not wins:
        return "Focusing a window by title is only supported on Windows."
    match = next((w for w in wins if title.lower() in w["title"].lower()), None)
    if match is None:
        names = [w["title"] for w in wins[:8]]
        return (f"No window matched '{title}'. Open windows: "
                + "; ".join(names) if names else "none visible.")
    try:
        import ctypes
        user32 = ctypes.windll.user32
        user32.ShowWindow(match["hwnd"], 9)          # SW_RESTORE from minimised
        # Alt held down lets SetForegroundWindow escape the foreground lock that
        # stops background processes from stealing focus.
        user32.keybd_event(0x12, 0, 0, 0)            # ALT down
        user32.SetForegroundWindow(match["hwnd"])
        user32.keybd_event(0x12, 0, 2, 0)            # ALT up
        return f"Focused: {match['title']}."
    except Exception as e:
        return f"Could not focus the window: {e}"


def _snap_window(params):
    if not _PYAUTOGUI:
        return "pyautogui is required for window snapping. Run: pip install pyautogui"
    if _SYSTEM != "Windows":
        # macOS uses the same physical key in a different place; try best-effort.
        combo = {
            "left": ("option", "command", "left"),
            "right": ("option", "command", "right"),
            "maximize": ("command", "up"),
            "minimize": ("command", "m"),
            "restore": ("command", "down"),
        }
        if _SYSTEM != "Darwin":
            return "Window snapping uses Windows (or macOS) hotkeys."
    else:
        combo = {
            "left": ("win", "left"),
            "right": ("win", "right"),
            "maximize": ("win", "up"),
            "minimize": ("win", "down"),
            "restore": ("win", "down"),
        }
    direction = (params.get("direction") or "").strip().lower()
    if direction not in combo:
        return ("direction must be one of: " + ", ".join(combo))
    try:
        pyautogui.hotkey(*combo[direction])
        time.sleep(0.25)
        return f"Snapped the active window {direction}."
    except Exception as e:
        return f"Could not snap the window: {e}"


def _list_processes(params):
    query = (params.get("query") or "").strip()
    limit = max(1, min(50, int(params.get("limit", 15) or 15)))
    return _format_processes(_walk_processes(query), limit)


def _find_process(params):
    query = str(params.get("name") or params.get("query") or "").strip()
    if not query:
        return "Give me a process name to find."
    procs = [
        {"pid": p.info["pid"], "name": p.info["name"]}
        for p in _find_pids(query)
    ]
    if not procs:
        return f"No running process matched '{query}'."
    lines = [f"• {x['name']} (pid {x['pid']})" for x in procs[:15]]
    if len(procs) > 15:
        lines.append(f"… and {len(procs) - 15} more processes.")
    return "\n".join(lines)


def _kill_process(params):
    if not _PSUTIL:
        return "psutil is required to end processes. Run: pip install psutil"
    pid = params.get("pid")
    name = (params.get("name") or params.get("query") or "").strip()
    if pid is None and not name:
        return "Give me a process name or pid to end."
    target = f"pid {pid}" if pid is not None else name
    matches = _find_pids(name, int(pid) if pid is not None else None)
    if not matches:
        return f"No running process matches '{target}'. Nothing was ended."

    if not _CONFIRM or not hasattr(confirm, "request"):
        return (f"I can end process '{target}' but no confirmation interface is "
                f"available, so I have not done it.")

    detail = ("Ending a program can throw away work that has not been saved. "
              f"Processes to stop: {', '.join(p.info['name'] for p in matches[:5])}.")
    return confirm.request(
        "pc_control-kill", f"End process {target}", detail,
        lambda: _do_kill(matches),
    )


def _do_kill(matches) -> str:
    stopped = []
    for p in matches:
        try:
            p.terminate()
            stopped.append(p.info["name"])
        except Exception:
            try:
                p.kill()
                stopped.append(p.info["name"])
            except Exception:
                pass
    time.sleep(0.4)
    alive = [p for p in matches if p.is_running()]
    if not stopped:
        return f"Could not end '{matches[0].info['name']}'. Check permissions."
    msg = f"Ended {len(stopped)} process(es): {', '.join(dict.fromkeys(stopped))}."
    if alive:
        msg += f" {len(alive)} still running — it may need a force stop, or it respawns."
    return msg


def _start_process(params):
    target = (params.get("target") or params.get("name") or "").strip()
    if not target:
        return "Give me a file, folder, URL or command path to open."
    try:
        if _SYSTEM == "Windows":
            if target.lower().endswith((".exe", ".bat", ".cmd", ".com", ".msi")):
                subprocess.Popen(target, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
            else:
                os.startfile(target)              # handles files, folders, URLs
        elif _SYSTEM == "Darwin":
            subprocess.Popen(["open", target], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        else:
            subprocess.Popen(["xdg-open", target], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        return f"Started {target}."
    except Exception as e:
        return (f"Could not open '{target}': {e}. If it is an installed app, "
                f"use open_app instead.")


def _system_uptime(params=None):
    if not _PSUTIL:
        return "psutil is required for uptime. Run: pip install psutil"
    try:
        boot = psutil.boot_time()
    except Exception as e:
        return f"Could not read uptime: {e}"
    secs = int(time.time() - boot)
    parts = []
    for unit, span in (("d", 86400), ("h", 3600), ("m", 60)):
        if secs >= span:
            parts.append(f"{secs // span}{unit}")
            secs %= span
    return "Up since " + time.strftime("%A %H:%M", time.localtime(boot)) + (
        f"  ({' '.join(parts) or '0m'})"
    )


# ── Shared entry point ────────────────────────────────────────────────────────

def _resolve_action(raw: str) -> str:
    """Normalise a model-chosen action name to its canonical form: case,
    whitespace/underscore/dash tolerant, then alias-mapped. Pure, so it is
    unit-testable without touching the system."""
    norm = (raw or "").strip().lower().replace(" ", "_").replace("-", "_")
    return _ACTION_ALIASES.get(norm, norm)


def pc_control(
    parameters: dict = None,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    params = parameters or {}

    def _val(param, aliases):  # loose name mapping for the model's typos
        for a in aliases:
            if params.get(a) not in (None, ""):
                return params.get(a)
        return ""

    action = _resolve_action(_val("action", ("action", "command", "what")))
    if not action:
        return ("Tell me what to do with pc_control: list_windows, focus_window, "
                "snap_window, list_processes, find_process, kill_process, "
                "start_process or system_uptime.")

    handlers = {
        "list_windows":    lambda: _list_windows(params),
        "focus_window":    lambda: _focus_window(params),
        "snap_window":     lambda: _snap_window(params),
        "list_processes":  lambda: _list_processes(params),
        "find_process":    lambda: _find_process(params),
        "kill_process":    lambda: _kill_process(params),
        "start_process":   lambda: _start_process(params),
        "system_uptime":   lambda: _system_uptime(params),
    }
    fn = handlers.get(action)
    if fn is None:
        return ("Unknown pc_control action. Use one of: "
                + ", ".join(sorted(handlers)))

    if player:
        player.write_log(f"[pc_control] {action}")
    print(f"[pc_control] action={action} os={_SYSTEM}")
    try:
        return fn()
    except Exception as e:
        return f"pc_control '{action}' failed: {e}"


# ── Tool declaration (auto-discovered by core/action_loader.py) ───────────────
TOOL = {
    "name": "pc_control",
    "description": "Manages running programs and windows: list or find processes, end a process, start a file/folder/URL, list open windows, focus a window, snap/minimise/maximise the active window, and check system uptime. Use this for ANY question about what is running or what windows are open. For volume, brightness, typing, closing the active window, dark mode, WiFi, restart and shutdown use computer_settings instead; use open_app to launch an installed application.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "enum": [
                    "list_windows", "focus_window", "snap_window",
                    "list_processes", "find_process", "kill_process",
                    "start_process", "system_uptime",
                ],
                "description": "What to do.",
            },
            "query": {
                "type": "STRING",
                "description": "Search text, e.g. a window title or process name (list_windows / list_processes / find_process).",
            },
            "name": {
                "type": "STRING",
                "description": "Process name to find or end (find_process / kill_process), or a file/folder/URL to start (start_process).",
            },
            "pid": {
                "type": "NUMBER",
                "description": "Process id to end (kill_process).",
            },
            "target": {
                "type": "STRING",
                "description": "File, folder, URL or executable path to open (start_process).",
            },
            "title": {
                "type": "STRING",
                "description": "Window title to focus (focus_window).",
            },
            "direction": {
                "type": "STRING",
                "enum": ["left", "right", "maximize", "minimize", "restore"],
                "description": "Snap direction for the active window (snap_window).",
            },
            "limit": {
                "type": "NUMBER",
                "description": "Maximum results to return (list_windows / list_processes).",
            },
        },
        "required": ["action"],
    },
    "handler": pc_control,
}