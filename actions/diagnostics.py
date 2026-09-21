#actions/diagnostics.py
"""
Self-diagnostics action — checks the local install and reports what works.

The point (spec §62): when something misbehaves, have JARVIS run its own health
check and give the user a specific, useful answer instead of a vague "something
went wrong". Every check is local and read-only — no network, no writes — so it
is safe to run any time.
"""
from __future__ import annotations

import json
import platform
import shutil
import sys
from pathlib import Path


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


_BASE       = _base_dir()
_CONFIG     = _BASE / "config" / "api_keys.json"
_MEMORY     = _BASE / "memory" / "long_term.json"


def _has(mod: str) -> bool:
    try:
        __import__(mod)
        return True
    except Exception:
        return False


def _check_config() -> str:
    try:
        data = json.loads(_CONFIG.read_text(encoding="utf-8"))
    except Exception as e:
        return f"config: BROKEN ({e})"
    key = str(data.get("gemini_api_key", "") or "")
    if not key:
        return "config: no API key (setup overlay required)"
    if len(key) < 30:
        return "config: API key looks too short — check it"
    return f"config: OK (key set, {len(data)} keys, name='{data.get('assistant_name') or 'default'}' )"


def _check_deps() -> str:
    wanted = ["google.genai", "numpy", "sounddevice", "PIL", "mss", "cv2",
              "pyautogui", "psutil", "requests"]
    missing = [m for m in wanted if not _has(m)]
    return "deps: OK (all 9 present)" if not missing else f"deps: MISSING {missing}"


def _check_audio() -> str:
    try:
        import sounddevice as sd
        devs = sd.query_devices()
        name = system_name = None
        ins, outs = [], []
        for i, d in enumerate(devs):
            if d.get("max_input_channels", 0) > 0:
                ins.append((i, d.get("name", "?")))
            if d.get("max_output_channels", 0) > 0:
                outs.append((i, d.get("name", "?")))
        # De-duplicate names (Windows lists one entry per host-API)
        ins  = sorted({n for _, n in ins})
        outs = sorted({n for _, n in outs})
        return f"audio: OK ({len(ins)} input, {len(outs)} output devices)"
    except Exception as e:
        return f"audio: FAIL ({e})"


def _check_screen() -> str:
    if not _has("mss"):
        return "screen: mss not installed"
    try:
        import mss
        with mss.mss() as sct:
            counts = len(sct.monitors) - 1
            shot   = sct.grab(sct.monitors[1] if counts >= 1 else sct.monitors[0])
            px     = shot.size
        return f"screen: OK (captured {px.width}×{px.height}, {counts} monitor(s))"
    except Exception as e:
        return f"screen: FAIL ({e})"


def _check_mouse() -> str:
    if not _has("pyautogui"):
        return "mouse: pyautogui not installed"
    try:
        import pyautogui
        w, h   = pyautogui.size()
        x, y   = pyautogui.position()
        return f"mouse: OK ({w}x{h} display, cursor at {x},{y})"
    except Exception as e:
        return f"mouse: FAIL ({e})"


def _check_memory() -> str:
    try:
        data = json.loads(_MEMORY.read_text(encoding="utf-8"))
    except Exception as e:
        return f"memory: BROKEN ({e})"
    entries = sum(len(v) for v in data.values() if isinstance(v, dict))
    size    = _MEMORY.stat().st_size / 1024
    return f"memory: OK ({entries} entries, {size:.0f} KB)"


def _check_disk() -> str:
    try:
        usage = shutil.disk_usage(_BASE)
        gb    = 1024 ** 3
        free  = usage.free / gb
        total = usage.total / gb
        state = "OK" if free > 5 else "LOW"
        return f"disk: {state} ({free:.1f} GB free of {total:.0f} GB)"
    except Exception as e:
        return f"disk: FAIL ({e})"


def _check_platform() -> str:
    load = "n/a"
    if _has("psutil"):
        try:
            import psutil
            load = f"{psutil.getloadavg()[0]:.2f}"
        except Exception:
            load = "n/a"
    return (f"platform: {platform.system()} {platform.release()} "
            f"(Python {platform.python_version()}, load {load})")


def diagnostics(parameters: dict, player=None, speak=None) -> str:
    scope = str((parameters or {}).get("scope", "full")).lower().strip()

    checks = [_check_config, _check_deps, _check_audio, _check_mouse,
              _check_memory, _check_disk, _check_platform]
    if scope != "quick":
        checks.insert(3, _check_screen)

    lines = ["[DIAGNOSTICS]"]
    for fn in checks:
        lines.append("- " + fn())
    report = "\n".join(lines)

    if player:
        player.write_log("SYS: Diagnostics run.")

    # Give the spoken answer a hint, but never hardcode the words.
    print(report)
    return report


TOOL = {
    "name": "diagnostics",
    "description": (
        "Run a local health check of my own install: config, dependencies, "
        "microphone/speakers, screen capture, mouse, long-term memory, disk "
        "space and platform. Safe, read-only, no network. Report the results "
        "in plain words — point out what is broken and what to do about it."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "scope": {
                "type": "STRING",
                "description": "'full' runs every check including a screen capture (default); 'quick' skips the screen check."
            }
        },
        "required": [],
    },
    "handler": diagnostics,
}