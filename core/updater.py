"""
updater.py — self-update for the JARVIS distribution ("push updates from home").

THE FLOW
    You (the developer) run:  python make_update.py --url <public zip URL>
        → packs dist/JARVIS into JARVIS_update.zip + writes update.json
          (version, notes, sha256) for you to upload anywhere static — a GitHub
          Release, a raw file, a bucket, your own server.

    The app (recipient side) then:
        check()                — fetch the manifest, compare versions
        download_and_stage()   — fetch the zip, verify sha256, unpack to staging
        apply_on_restart()     — write apply_update.bat, hand over, restart

THE BAT (Windows can't overwrite a running exe, so the swap happens after exit)
    1. wait for the app's PID to disappear (≤60 s)
    2. robocopy the staged payload OVER the app folder (/E — never /MIR, the
       user's config/ must survive every update)
    3. keep the old exe as JARVIS.exe.bak, delete staging, relaunch, self-delete

SAFETY RULES
    - HTTPS only.  sha256 verified when the manifest carries one.
    - Never touches config/api_keys.json, memory/, or any personal store:
      updates copy code and assets over, they never delete.
    - Every failure is a dict {"ok": False, "err": …} — never an exception
      crossing into the UI thread.

THE DEFAULT MANIFEST URL is a constant below — point it at YOUR hosted
update.json to make every shipped exe check your channel. Users (or you, per
install) can override it with the "update_manifest_url" key in api_keys.json.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent.parent

VERSION_FILE   = BASE_DIR / "VERSION"
_CONFIG_FILE   = BASE_DIR / "config" / "api_keys.json"

STAGING_DIR    = BASE_DIR / "update_staging"     # zip + unpacked payload
STAGED_MARK    = STAGING_DIR / "payload"
STAGED_VERSION = STAGING_DIR / "STAGED_VERSION"
APPLY_BAT      = BASE_DIR / "apply_update.bat"

# ── point this at YOUR update channel (any HTTPS URL serving update.json) ────
# GitHub Releases serves the manifest with the right content-type and no
# redirects, and it survives release edits (unlike /releases/download/… links
# that break when assets are replaced). Update by publishing a new release.
DEFAULT_MANIFEST_URL = (
    "https://github.com/idkunknown657-cell/ICE-JARVIS/releases/latest"
    "/download/update.json"
)

_DOWNLOAD_TIMEOUT = 30      # per-request seconds
_MAX_ZIP_BYTES    = 2 * 1024 * 1024 * 1024   # sanity cap: 2 GB


# ── version handling ─────────────────────────────────────────────────────────

def current_version() -> str:
    try:
        return VERSION_FILE.read_text(encoding="utf-8").strip() or "0.0.0"
    except Exception:
        return "0.0.0"


def _as_tuple(v: str) -> tuple:
    """'2026.9.20' → (2026, 9, 20). Non-numeric chunks are dropped, so a stray
    suffix never breaks the comparison."""
    out: list[int] = []
    for part in str(v or "").strip().split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out) or (0, 0, 0)


def is_newer(latest: str, current: str) -> bool:
    return _as_tuple(latest) > _as_tuple(current)


def _manifest_url() -> str:
    """Config override wins; else the baked-in default channel."""
    try:
        cfg = json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
        url = str(cfg.get("update_manifest_url") or "").strip()
        if url.startswith("https://"):
            return url
    except Exception:
        pass
    return DEFAULT_MANIFEST_URL


# ── check ────────────────────────────────────────────────────────────────────

def check(timeout: float = _DOWNLOAD_TIMEOUT) -> dict:
    """Fetch the update manifest. Never raises. Returns:
    {"ok": bool, "update_available": bool, "current": str, "latest": str,
     "notes": str, "url": str, "sha256": str, "err": str}
    """
    import requests

    current = current_version()
    out = {"ok": False, "update_available": False, "current": current,
           "latest": "", "notes": "", "url": "", "sha256": "", "err": ""}
    url = _manifest_url()
    if not url:
        out["err"] = "no update source configured"
        return out
    try:
        r = requests.get(url, timeout=timeout, headers={"Cache-Control": "no-cache"})
    except Exception as e:
        out["err"] = f"manifest unreachable ({type(e).__name__})"
        return out
    if r.status_code != 200:
        out["err"] = f"manifest HTTP {r.status_code}"
        return out
    try:
        m = r.json()
    except Exception:
        out["err"] = "manifest is not JSON"
        return out
    if not isinstance(m, dict):
        out["err"] = "manifest is not a JSON object"
        return out
    latest = str(m.get("version") or "").strip()
    if not latest:
        out["err"] = "manifest has no version"
        return out
    out.update(ok=True, latest=latest,
               notes=str(m.get("notes") or "")[:300],
               url=str(m.get("url") or "").strip(),
               sha256=str(m.get("sha256") or "").strip().lower())
    out["update_available"] = is_newer(latest, current) and bool(out["url"])
    if out["update_available"] and not out["url"].startswith("https://"):
        out.update(update_available=False,
                   err="update url is not https — refusing")
    return out


# ── download + stage ─────────────────────────────────────────────────────────

def _sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download_and_stage(progress=None) -> dict:
    """Download the manifest's zip, verify it, unpack into update_staging/.
    Returns {"ok": bool, "err": str, "path": str}. Never raises."""
    import requests

    info = check()
    if not info["ok"]:
        return {"ok": False, "err": info["err"] or "check failed", "path": ""}
    if not info["update_available"]:
        return {"ok": False,
                "err": f"already up to date ({info['current']})", "path": ""}

    try:
        STAGING_DIR.mkdir(parents=True, exist_ok=True)
        zip_path = STAGING_DIR / "update.zip"
        with requests.get(info["url"], stream=True, timeout=_DOWNLOAD_TIMEOUT) as r:
            r.raise_for_status()
            total = int(r.headers.get("content-length") or 0)
            done = 0
            with zip_path.open("wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
                    done += len(chunk)
                    if done > _MAX_ZIP_BYTES:
                        raise RuntimeError("update download exceeds size cap")
                    if progress and total:
                        progress(done / total)
        if info["sha256"] and _sha256_of(zip_path) != info["sha256"]:
            return {"ok": False, "err": "sha256 mismatch — download corrupted",
                    "path": ""}

        payload = STAGED_MARK
        shutil.rmtree(payload, ignore_errors=True)
        payload.mkdir(parents=True)
        with zipfile.ZipFile(zip_path) as z:
            names = z.namelist()
            if any(n.startswith(("/", "\\")) or ".." in Path(n).parts for n in names):
                return {"ok": False, "err": "unsafe zip layout — refused",
                        "path": ""}
            z.extractall(payload)

        # a flat zip (exe at root) and a wrapped zip (dist/JARVIS/…) both work
        root = _payload_root(payload)
        if not ((root / "JARVIS.exe").exists() or (root / "main.py").exists()):
            return {"ok": False, "err": "payload has no JARVIS.exe/main.py",
                    "path": ""}
        # keep the version we are moving to, for the record (written OUTSIDE
        # the root so _payload_root's single-wrapper detection still works)
        (payload / "STAGED_VERSION").write_text(info["latest"], encoding="utf-8")
        return {"ok": True, "err": "", "path": str(root),
                "version": info["latest"], "notes": info["notes"]}
    except Exception as e:
        return {"ok": False, "err": f"{type(e).__name__}: {str(e)[:120]}",
                "path": ""}


def _payload_root(payload: Path) -> Path:
    """Strip a single wrapper folder from the extracted payload if present."""
    entries = [p for p in payload.iterdir() if p.name != "STAGED_VERSION"]
    if len(entries) == 1 and entries[0].is_dir() \
            and not (payload / "JARVIS.exe").exists():
        return entries[0]
    return payload


# ── apply ────────────────────────────────────────────────────────────────────

def apply_on_restart() -> dict:
    """Write apply_update.bat and spawn it detached. The CALLER must exit right
    after (the UI closes the window); the bat waits for this process to die,
    swaps the files and relaunches. Never raises."""
    try:
        payload = STAGED_MARK
        if not payload.exists():
            return {"ok": False, "err": "nothing staged"}
        if os.name != "nt":
            # POSIX (dev machine): swap in place, no bat needed
            src_root = _payload_root(payload)
            for item in src_root.iterdir():
                dst = BASE_DIR / item.name
                if item.is_dir():
                    shutil.copytree(item, dst, dirs_exist_ok=True)
                else:
                    shutil.copy2(item, dst)
            shutil.rmtree(STAGING_DIR, ignore_errors=True)
            return {"ok": True, "err": "", "relaunch": False}

        exe = Path(sys.executable) if getattr(sys, "frozen", False) \
            else Path(sys.executable)
        pid = os.getpid()
        src = str(_payload_root(payload)) + "\\*"
        # NOTE: goto-style flow on purpose — %tries% inside a parenthesized
        # block would expand at parse time and never increment.
        bat = (
            "@echo off\r\n"
            "title JARVIS updater\r\n"
            f"set /a tries=0\r\n"
            ":wait\r\n"
            f"tasklist /FI \"PID eq {pid}\" 2>nul | find /I \"{pid}\" >nul\r\n"
            "if errorlevel 1 goto done\r\n"
            "timeout /t 1 /nobreak >nul\r\n"
            "set /a tries+=1\r\n"
            "if %tries% LSS 60 goto wait\r\n"
            ":done\r\n"
            f"if exist \"{exe}\" copy /y \"{exe}\" \"{exe}.bak\" >nul\r\n"
            f"robocopy \"{src}\" \"{BASE_DIR}\" /E /NFL /NDL /NJH /NJS /NP /R:2 /W:1\r\n"
            f"rmdir /s /q \"{STAGING_DIR}\"\r\n"
            f"start \"\" \"{exe}\"\r\n"
            "del \"%~f0\"\r\n"
        )
        APPLY_BAT.write_text(bat, encoding="utf-8")
        # DETACHED so the helper outlives this process no matter how it exits
        subprocess.Popen(
            ["cmd", "/c", "start", "/min", str(APPLY_BAT)],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) |
                          getattr(subprocess, "DETACHED_PROCESS", 0),
            close_fds=True)
        return {"ok": True, "err": "", "relaunch": True}
    except Exception as e:
        return {"ok": False, "err": f"{type(e).__name__}: {str(e)[:120]}"}


def pending() -> bool:
    """True when a verified update is staged and waiting to be applied."""
    return STAGED_MARK.exists()


def pending_version() -> str:
    """The version recorded at staging time ("" when nothing is staged).
    Lets the UI offer "Restart & install vX" without another network check."""
    if not pending():
        return ""
    try:
        return STAGED_VERSION.read_text(encoding="utf-8").strip()
    except Exception:
        return ""
