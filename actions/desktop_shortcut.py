# desktop_shortcut.py — create a desktop shortcut for JARVIS (or any app) by voice.
import os
import platform
import subprocess
import sys
from pathlib import Path


def _base_path() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


def _launch_command() -> tuple[str, str]:
    """Return (target, args) that launches the app with NO console window.

    On Windows from source, use pythonw.exe (windowless Python) instead of
    python.exe so double-clicking the desktop icon doesn't pop a cmd window.
    Falls back to python.exe if pythonw.exe is missing. Frozen builds run the
    exe directly (already windowless)."""
    if getattr(sys, "frozen", False):
        return sys.executable, ""
    if platform.system() == "Windows":
        sibling = Path(sys.executable).with_name("pythonw.exe")
        if sibling.exists():
            return str(sibling), str(_base_path() / "main.py")
    return sys.executable, str(_base_path() / "main.py")


def _desktop_dir() -> Path:
    """Locate the real Desktop folder (handles OneDrive / redirected desktops).

    `%USERPROFILE%\\Desktop` is NOT reliable: on OneDrive-linked accounts it is
    a shadow *file*, not the folder — the actual desktop lives under OneDrive.
    Ask the shell for the real CSIDL_DESKTOPDIRECTORY path instead."""
    if platform.system() == "Windows":
        try:
            import ctypes
            buf = ctypes.create_unicode_buffer(260)
            r = ctypes.windll.shell32.SHGetFolderPathW(None, 0x10, None, 0, buf)
            p = Path(buf.value)
            if r == 0 and p.exists() and p.is_dir():
                return p
        except Exception:
            pass
        try:
            import win32api
            import win32con
            p = Path(win32api.SHGetSpecialFolderPath(0, win32con.CSIDL_DESKTOPDIRECTORY))
            if p.exists() and p.is_dir():
                return p
        except Exception:
            pass
    for env in ("USERPROFILE", "HOME"):
        p = Path(os.environ.get(env, "")).expanduser() / "Desktop"
        try:
            if p.is_dir():
                return p
        except Exception:
            pass
    return Path.home() / "Desktop"


def _desktop_shortcut(name: str, target: str, args: str = "",
                      icon: str = "") -> str:
    """Create a .lnk / .desktop shortcut. Returns a user-facing summary."""
    os_name = platform.system()
    if os_name == "Windows":
        import win32com.client
        import win32con
        import win32api
        import pythoncom

        desktop = _desktop_dir()
        desktop.mkdir(parents=True, exist_ok=True)
        lnk = desktop / f"{name}.lnk"

        pythoncom.CoInitialize()
        try:
            shell = None
            try:
                shell = win32com.client.Dispatch("WScript.Shell")
                sc = shell.CreateShortCut(str(lnk))
                sc.Targetpath = target
                if args:
                    sc.Arguments = args
                if icon:
                    sc.IconLocation = icon
                sc.WorkingDirectory = str(_base_path())
                sc.save()
            finally:
                shell = None      # release COM refs before uninitialising
        finally:
            pythoncom.CoUninitialize()
        return f"Created desktop shortcut: {lnk}"
    elif os_name == "Darwin":
        desktop = _desktop_dir()
        app = desktop / f"{name}.command"
        app.write_text(
            f'#!/bin/bash\ncd "{Path(target).parent}"\nexec "{target}" {args}\n',
            encoding="utf-8",
        )
        subprocess.run(["chmod", "+x", str(app)], check=False)
        return f"Created desktop launcher: {app}"
    else:  # Linux
        desktop = _desktop_dir()
        de = desktop / f"{name}.desktop"
        de.write_text(
            "[Desktop Entry]\n"
            f"Name={name}\n"
            f"Exec={target} {args}\n"
            f"Icon={icon or 'utilities-terminal'}\n"
            "Type=Application\n"
            "Terminal=false\n",
            encoding="utf-8",
        )
        subprocess.run(["chmod", "+x", str(de)], check=False)
        return f"Created desktop shortcut: {de}"


def desktop_shortcut(parameters: dict, response=None, player=None,
                     session_memory=None) -> str:
    """Tool handler for the desktop_shortcut action."""
    params = parameters or {}
    base = _base_path()

    target, args = _launch_command()

    # Optional: icon next to the executable / project.
    icon = ""
    try:
        icons = [base / "assets" / "jarvis.ico", base / "jarvis.ico"]
        for p in icons:
            if p.exists():
                icon = str(p)
                break
    except Exception:
        pass

    if player:
        player.write_log("[Shortcut] creating desktop shortcut")

    try:
        return _desktop_shortcut(
            name="ICE",
            target=target,
            args=args,
            icon=icon,
        )
    except Exception as e:
        return f"desktop_shortcut failed: {e}"


TOOL = {
    "name": "desktop_shortcut",
    "description": "Create a desktop shortcut icon to launch this assistant (JARVIS). "
                   "Use when the user says 'create a desktop shortcut', 'make a shortcut "
                   "on the desktop', 'add an icon on the desktop', or asks for a quick "
                   "way to open JARVIS from the desktop.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "name": {
                "type": "STRING",
                "description": "Shortcut label (defaults to JARVIS)"
            }
        },
        "required": []
    },
    "handler": desktop_shortcut,
}