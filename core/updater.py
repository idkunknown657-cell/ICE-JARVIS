"""Auto-update via GitHub Releases.

The flow:

    1. check_for_update(repo)  — asks GitHub's Releases API for the newest
       release of `repo` ("OWNER/REPO"). If its tag is ahead of APP_VERSION,
       returns an UpdateInfo (version, notes, exe URL, sha256).
    2. download_update(info, ...) — streams the new .exe to a staging folder in
       the app directory and verifies its sha256 before it is ever used.
    3. apply_update(...) — writes a tiny pending marker and launches a detached
       helper .bat that waits for the current app to exit, swaps the new .exe
       in, and restarts with `--updated` so the app can greet the new version.

The publisher owns the other half: tools/publish_update.py builds the .exe,
computes its sha256, and attaches the .exe plus an update.json manifest to a
GitHub release. Every installed copy polls that release at startup, so one
publish reaches all users.

Design notes:
    - The update.json manifest is optional-but-recommended: it carries the
      sha256 and channel. Without it the updater falls back to the release's
      tag + body and downloads the exe unverified (still better than nothing,
      but you should always attach the manifest).
    - Staging lives in the exe directory, NOT in temp: a cross-volume rename
      would fail the swap. The helper .bat is what survives the process exit —
      a running exe cannot replace itself.
    - In source mode (running from python, not a frozen exe) everything up to
      apply_update works, and apply_update is a safe no-op that tells you so.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from core.version import APP_VERSION

GITHUB_API = "https://api.github.com/repos/{repo}/releases/latest"
EXE_NAME   = "JARVIS.exe"


# ── Data ──────────────────────────────────────────────────────────────────────

@dataclass
class UpdateInfo:
    version: str
    notes:   str            = ""
    exe_url: str            = ""
    sha256:  str            = ""
    tag:     str            = ""
    channel: str            = "stable"
    update_json_url: str    = ""


def parse_version(v: str) -> tuple:
    """'v1.2.3' / '1.2.3' → (1, 2, 3). Non-numeric suffixes are ignored."""
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", str(v))
    if not m:
        return (0, 0, 0)
    return tuple(int(g) for g in m.groups())


def _request(url: str, timeout: float = 15.0) -> bytes:
    req = urllib.request.Request(url, headers={
        "User-Agent":   "JARVIS-updater",
        "Accept":       "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


# ── Check ─────────────────────────────────────────────────────────────────────

def current_version() -> str:
    return APP_VERSION


def check_for_update(repo: str, channel: str = "stable") -> UpdateInfo | None:
    """Query GitHub Releases; return UpdateInfo when the newest release is
    ahead of the running version and matches the channel, else None.

    Raises (e.g. network down, 404, rate limit) — callers decide what that
    means; a failed check must never break startup.
    """
    repo = (repo or "").strip().strip("/")
    if not repo or "/" not in repo:
        raise ValueError("No GitHub repo configured (OWNER/REPO).")

    release = json.loads(_request(GITHUB_API.format(repo=repo)).decode("utf-8"))
    tag     = str(release.get("tag_name") or "").strip()
    notes   = str(release.get("body") or "").strip()
    assets  = release.get("assets") or []

    def _asset_url(name: str) -> str:
        for a in assets:
            if str(a.get("name", "")).lower() == name.lower():
                return str(a.get("browser_download_url") or "")
        return ""

    info = UpdateInfo(
        version=tag.lstrip("v"),
        notes=notes,
        tag=tag,
        update_json_url=_asset_url("update.json"),
    )

    # Manifest wins when present: it carries the sha256 and the channel.
    if info.update_json_url:
        try:
            man = json.loads(_request(info.update_json_url).decode("utf-8"))
            info.version     = str(man.get("version") or info.version).lstrip("v")
            info.sha256      = str(man.get("sha256") or "").lower().strip()
            info.exe_url     = str(man.get("exe_url") or info.exe_url or "").strip()
            info.notes       = str(man.get("notes") or notes).strip()
            info.channel     = str(man.get("channel") or "stable").strip().lower()
        except Exception as e:
            print(f"[Update] ⚠️ manifest unreadable ({e}); using release data")

    if not info.exe_url:
        info.exe_url = _asset_url(EXE_NAME)
    if not info.exe_url:
        print("[Update] ⚠️ release has no JARVIS.exe asset")
        return None

    if info.channel != (channel or "stable").lower():
        return None

    if parse_version(info.version) <= parse_version(APP_VERSION):
        return None

    return info


# ── Download ──────────────────────────────────────────────────────────────────

def _sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download_update(info: UpdateInfo,
                    progress: callable | None = None,
                    timeout: float = 600.0) -> Path:
    """Stream the new exe to the staging folder, verify its sha256 (when the
    manifest provided one), and return the staged path."""
    exe_dir  = _exe_dir()
    stage    = exe_dir / ".update"
    stage.mkdir(parents=True, exist_ok=True)
    dst      = stage / "JARVIS.exe.new"

    req = urllib.request.Request(info.exe_url, headers={"User-Agent": "JARVIS-updater"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        total   = int(r.headers.get("Content-Length") or 0)
        got     = 0
        h       = hashlib.sha256()
        with open(dst, "wb") as f:
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                h.update(chunk)
                got += len(chunk)
                if progress:
                    progress(got, total)

    if info.sha256:
        real = h.hexdigest()
        if real != info.sha256:
            dst.unlink(missing_ok=True)
            raise ValueError(
                f"sha256 mismatch: expected {info.sha256}, got {real} — refusing to install.")
    return dst


# ── Apply ─────────────────────────────────────────────────────────────────────

def _exe_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(tempfile.gettempdir())


def _frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def apply_update(new_exe: Path, version: str, notes: str = "") -> bool:
    """Stash the staged exe into the app directory and schedule the swap.

    Returns True when an update is staged and will replace this process on
    exit; False in source mode (nothing to replace). Safe to call twice —
    the second call overwrites the pending marker.
    """
    if not _frozen():
        print(f"[Update] Source mode — v{version} staged at {new_exe} but not installed.")
        return False

    exe_dir  = _exe_dir()
    target   = exe_dir / EXE_NAME
    stage    = new_exe if new_exe.parent == exe_dir / ".update" else exe_dir / ".update" / new_exe.name
    marker   = exe_dir / ".update" / "pending.json"
    bat      = exe_dir / ".update" / "apply_update.bat"

    marker.write_text(json.dumps({
        "version": version,
        "notes":   notes,
        "target":  str(target),
        "exe":     str(stage),
    }, indent=2), encoding="utf-8")

    _write_helper_bat(bat, stage, target, marker)

    # Detached, hidden — it outlives us and swaps once we exit.
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
    try:
        subprocess.Popen(
            [str(bat), str(stage), str(target), str(os.getpid()), str(marker)],
            creationflags=creationflags,
            close_fds=True,
        )
    except Exception as e:
        print(f"[Update] ⚠️ could not launch helper: {e}")
        return False
    return True


def _write_helper_bat(bat: Path, new: Path, target: Path, marker: Path) -> None:
    """A tiny .bat that waits for us to exit, swaps the exe, cleans up, restarts.

    Args (%1..%4): new exe, target exe, running PID, marker path.
    """
    body = "\r\n".join([
        "@echo off",
        "set NEW=%~1",
        "set TARGET=%~2",
        "set PID=%~3",
        "set MARKER=%~4",
        ":wait",
        'tasklist /FI "PID eq %PID%" 2>nul | find "%PID%" >nul',
        "if %errorlevel%==0 (",
        "  timeout /t 1 /nobreak >nul",
        "  goto wait",
        ")",
        "move /Y \"%NEW%\" \"%TARGET%\" >nul",
        'start "" "%TARGET%" --updated',
        "",
    ])
    bat.write_text(body, encoding="ascii")


def complete_update() -> str | None:
    """Called at startup: when we relaunched with --updated, read the version
    the helper bat left behind (the marker survives the swap and is deleted
    HERE, by the new process, so the greeting can name the version)."""
    if "--updated" not in sys.argv:
        return None
    try:
        exe_dir  = _exe_dir()
        marker   = exe_dir / ".update" / "pending.json"
        data     = json.loads(marker.read_text(encoding="utf-8")) if marker.exists() else {}
        version  = str(data.get("version") or "")
        marker.unlink(missing_ok=True)
        (exe_dir / ".update" / "JARVIS.exe.new").unlink(missing_ok=True)
        return version or "unknown"
    except Exception as e:
        print(f"[Update] ⚠️ complete_update: {e}")
        return "unknown"