"""
core/boot_sentry.py — the undo button for a patch that broke the boot.

WHY THIS EXISTS
    core/self_heal.py can rewrite JARVIS's own source while it runs. A validated
    patch is very likely correct — it parses, it compiles, and the failure it
    targets is fixed — but "likely" is not "certain", and the one failure mode a
    self-patching assistant genuinely cannot recover from on its own is the patch
    that stops it from starting. Nothing inside a process that never reaches the
    UI can offer to undo anything.

THE MECHANISM
    Two files and one handshake:

      arm()          called the instant a patch is written. Records which patch,
                     which file and which backup, in config/patch_watch.json.
      mark_healthy() called by main.py once the session is genuinely up. Removes
                     the watch: the patch survived contact with reality.
      check_and_recover() runs at startup. If a watch is still armed then the
                     process that wrote it never became healthy, so the patch is
                     undone from its backup before anything else loads.

    The gap between arm() and mark_healthy() is deliberately short — the watch is
    cleared once the assistant is running, not at shutdown — so a patch is only
    ever at risk during the seconds it takes to find out whether it worked.

WHAT IT WILL NOT DO
    * It never restores a file it did not back up itself, and it refuses if the
      backup is missing rather than writing something guessed.
    * It refuses to touch a file outside this installation.
    * It reports exactly what it undid, so a user who lost a good patch knows to
      ask for it again instead of wondering what happened.
    * A corrupt or unreadable watch is discarded, not acted on: guessing at a
      rollback from a half-written file is how you turn one broken file into two.

NOTHING HERE RAISES. Checked at startup, before anything else has a chance to
depend on the answer.
"""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

_TAG = "[BootSentry]"


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def watch_path() -> Path:
    return _base_dir() / "config" / "patch_watch.json"


def _read_watch() -> dict:
    try:
        data = json.loads(watch_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_watch(data: dict) -> None:
    path = watch_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(path)
    except Exception as e:
        print(f"{_TAG} could not arm the watch: {e}")


def arm(patch_id: str, file_path: str, backup: str) -> None:
    """Arm the watch for a patch that has just been written. Never raises."""
    try:
        _write_watch({
            "patch_id": str(patch_id),
            "file": str(file_path),
            "backup": str(backup),
            "armed_at": time.time(),
            "armed_at_str": time.strftime("%Y-%m-%d %H:%M:%S"),
        })
        print(f"{_TAG} watching patch {patch_id} until the session is up")
    except Exception:
        pass


def disarm() -> None:
    """Clear the watch — used when a patch is rolled back deliberately."""
    try:
        watch_path().unlink(missing_ok=True)
    except Exception:
        pass


def mark_healthy() -> bool:
    """The patched process reached a working session: the patch is accepted.

    Returns True when a watch was actually cleared, so the caller can log that
    something was proven rather than assuming.
    """
    if not watch_path().exists():
        return False
    watch = _read_watch()
    disarm()
    if watch.get("patch_id"):
        print(f"{_TAG} patch {watch['patch_id']} survived startup — accepted")
    return True


def armed() -> dict:
    return _read_watch()


def check_and_recover() -> dict:
    """Undo the last patch if the run that carried it never came up.

    Returns {"recovered": bool, ...} and is safe to call on every start when
    nothing is armed — which is the overwhelmingly common case.
    """
    watch = _read_watch()
    if not watch:
        # Either nothing was ever armed, or the file was unreadable. Deleting a
        # corrupt watch is the right move: leaving it would make the next start
        # attempt a rollback from data nobody could parse.
        if watch_path().exists():
            print(f"{_TAG} discarding an unreadable watch file")
            disarm()
        return {"recovered": False}

    path = Path(str(watch.get("file") or ""))
    backup = Path(str(watch.get("backup") or ""))
    patch_id = str(watch.get("patch_id") or "?")

    if not str(watch.get("file") or "").strip():
        print(f"{_TAG} watch has no target file — discarding it")
        disarm()
        return {"recovered": False, "reason": "the watch named no file"}

    if not backup.exists():
        print(f"{_TAG} backup for patch {patch_id} is gone — cannot recover")
        disarm()
        return {"recovered": False, "patch_id": patch_id,
                "reason": "the backup for that patch is missing"}

    try:
        base = _base_dir().resolve()
        path.resolve().relative_to(base)
    except Exception:
        print(f"{_TAG} watch points outside my folder — refusing to touch it")
        disarm()
        return {"recovered": False, "patch_id": patch_id,
                "reason": "that file is not inside my own folder"}

    try:
        shutil.copy2(backup, path)
    except Exception as e:
        print(f"{_TAG} could not restore {path.name}: {e}")
        return {"recovered": False, "patch_id": patch_id,
                "reason": f"the restore failed: {e}"}

    _mark_reverted(patch_id)
    disarm()
    message = (f"JARVIS did not start cleanly after a patch to {path.name}, so it "
               f"has been undone. Nothing is lost — the fix can be applied again.")
    print(f"{_TAG} RECOVERED: restored {path.name} from {backup.name}")
    return {"recovered": True, "patch_id": patch_id, "file": str(path),
            "backup": str(backup), "message": message}


def _mark_reverted(patch_id: str) -> None:
    """Record in the patch log that the boot sentry undid this one, so the history
    never claims a patch is live when it is not."""
    try:
        from core import self_heal
        entries = self_heal._load_history()
        for entry in entries:
            if entry.get("id") == patch_id and entry.get("status") == "applied":
                entry["status"] = "reverted_on_boot"
                entry["reverted_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        self_heal._save_history(entries)
    except Exception as e:
        print(f"{_TAG} could not update the patch log: {e}")


__all__ = ["arm", "disarm", "mark_healthy", "armed", "check_and_recover",
           "watch_path"]
