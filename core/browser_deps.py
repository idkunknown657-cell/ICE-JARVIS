"""
ICE — Playwright browser components: are they here, and download them if not.

The Windows installer ships Playwright's *driver* but not its browsers. Chromium
and Firefox add roughly 450 MB to every download, and `browser_control` drives
the Chrome, Edge or Firefox already on the PC first — the bundled engines are the
fallback for a machine with no browser at all. So the installer offers the
download as a tick-box and this module is what performs it, including from inside
the frozen build where `python -m playwright` cannot work: there is no python to
run it with.

Nothing here raises. Every failure comes back as
`{"ok": False, "err": "<a sentence worth showing the user>"}`, because the caller
is a first-run installer step or a background thread, not something that can hand
a traceback to a person.
"""
from __future__ import annotations

import importlib.util
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Callable, Iterable

# What `setup.py` installs, and what the fallback engine needs. Chromium is the
# one browser_control reaches for; Firefox covers the engines that only exist as
# a Firefox build.
BROWSERS: tuple[str, ...] = ("chromium", "firefox")

_TIMEOUT = 900          # a slow link moving ~450 MB


# ── where the browsers live ──────────────────────────────────────────────────

def cache_dir() -> Path:
    """Playwright's own cache location, honouring its environment override.

    Kept identical to Playwright's rule so a download made here is found by the
    driver later — a mismatch would silently re-download 450 MB.
    """
    override = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "").strip()
    if override:
        return Path(override)
    home = Path.home()
    system = platform.system()
    if system == "Windows":
        base = os.environ.get("LOCALAPPDATA") or (home / "AppData" / "Local")
        return Path(base) / "ms-playwright"
    if system == "Darwin":
        return home / "Library" / "Caches" / "ms-playwright"
    return home / ".cache" / "ms-playwright"


def installed() -> list[str]:
    """Names of the browsers already unpacked — `chromium-1234` → `chromium`."""
    found: list[str] = []
    try:
        for entry in cache_dir().iterdir():
            if entry.is_dir():
                name = entry.name.rsplit("-", 1)[0]
                if name and name not in found:
                    found.append(name)
    except Exception:
        pass            # no cache dir yet: nothing installed, not an error
    return found


def missing(names: Iterable[str] = BROWSERS) -> list[str]:
    """Which of *names* still need downloading, in the order asked for."""
    have = set(installed())
    return [n for n in names if n not in have]


def ready(names: Iterable[str] = BROWSERS) -> bool:
    """True when every requested browser is on disk."""
    return not missing(names)


# ── where the driver lives (frozen or not) ───────────────────────────────────

def driver() -> tuple[Path, Path] | None:
    """`(node, cli.js)` for the bundled Playwright driver, or None if absent.

    Three candidate roots, in order: the installed package (running from source),
    `sys._MEIPASS` (a one-file build unpacks there), and `_internal` (the onedir
    layout the installer uses). The first root that actually holds a driver wins,
    so this keeps working if the layout changes.
    """
    roots: list[Path] = []
    try:
        spec = importlib.util.find_spec("playwright")
        if spec is not None and spec.origin:
            roots.append(Path(spec.origin).resolve().parent / "driver")
    except Exception:
        pass
    unpacked = getattr(sys, "_MEIPASS", "")
    if unpacked:
        roots.append(Path(unpacked) / "playwright" / "driver")
    roots.append(Path(sys.executable).resolve().parent / "_internal"
                 / "playwright" / "driver")

    for root in roots:
        cli = root / "package" / "cli.js"
        node = root / ("node.exe" if platform.system() == "Windows" else "node")
        if cli.exists() and node.exists():
            return node, cli
    return None


def _command(names: list[str]) -> list[str] | None:
    """The command that downloads *names*, or None when it cannot be built."""
    found = driver()
    if found is not None:
        node, cli = found
        return [str(node), str(cli), "install", *names]
    if not getattr(sys, "frozen", False):
        # From source the package's own CLI is the supported path.
        return [sys.executable, "-m", "playwright", "install", *names]
    return None


# ── the download ─────────────────────────────────────────────────────────────

def install(names: Iterable[str] = BROWSERS, log: Callable | None = None,
            timeout: int = _TIMEOUT) -> dict:
    """Download the missing browsers. Blocking — call it from a worker thread.

    Returns `{"ok", "err", "installed", "detail"}`. `installed` lists what this
    call actually fetched, so a caller can say "already there" instead of
    claiming credit for someone else's download.
    """
    wanted = [n for n in names if n in BROWSERS] or list(BROWSERS)
    todo = missing(wanted)
    if not todo:
        return {"ok": True, "err": "", "installed": [], "detail": "already present"}

    cmd = _command(todo)
    if cmd is None:
        return {"ok": False, "installed": [], "detail": "",
                "err": "browser components are not bundled in this build"}

    if log:
        log(f"SYS: Downloading browser components ({', '.join(todo)}) — "
            f"one time, about 450 MB…")
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except FileNotFoundError:
        return {"ok": False, "installed": [], "detail": "",
                "err": "the bundled browser driver has no node runtime"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "installed": [], "detail": "",
                "err": "the browser download timed out"}
    except Exception as e:
        return {"ok": False, "installed": [], "detail": "",
                "err": f"{type(e).__name__}: {e}"}

    output = (proc.stderr or proc.stdout or b"").decode("utf-8", "replace").strip()
    if proc.returncode != 0:
        return {"ok": False, "installed": [], "detail": output[-400:],
                "err": f"the browser download failed (exit {proc.returncode})"}

    # Trust the disk over the exit code: a re-run that finds a half-finished
    # cache returns 0 without having finished the job.
    done = [n for n in todo if n not in missing([n])]
    if len(done) != len(todo):
        return {"ok": False, "installed": done, "detail": output[-400:],
                "err": "the browser download finished but the files are not there"}
    if log:
        log("SYS: Browser components ready.")
    return {"ok": True, "err": "", "installed": done, "detail": output[-400:]}
