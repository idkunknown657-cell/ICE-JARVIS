"""
core/self_heal.py — notice a crash, find the line, and offer the fix.

WHY THIS EXISTS
    Every failure that reaches a user has already been diagnosed by them: they
    read the traceback, work out which module it came from and what the missing
    check should have been. That is work the assistant is better placed to do, and
    it is the one kind of self-improvement that pays for itself immediately.

THE PIPELINE — CATCH → LOCATE → WRITE → VALIDATE → APPLY (or ASK) → VERIFY
    1. CATCH     tracebacks are handed here as they happen (main.py's tool error
                 path, action_loader's crash path, and `record_error` for anything
                 that knows its own failure).
    2. LOCATE    the innermost frame that belongs to *this* installation is the
                 suspect. Frames inside site-packages are skipped, because
                 patching a library is not something a running assistant should
                 ever do.
    3. WRITE     the reasoning model is shown ~50 lines around the failing line
                 and asked for the smallest replacement: an exact chunk of the
                 file, and what to put in its place.
    4. VALIDATE  the staged result must parse, and must still compile, before a
                 byte of the original is touched.
    5. APPLY     a timestamped backup is taken first, the file is written, and it
                 is compiled on disk. If any of that fails the backup goes back
                 and the caller is told — there is no path where a failed patch
                 leaves the file half-written.
    6. VERIFY    every patch is recorded with its backup, so `rollback` can undo
                 it, and boot_sentry can undo it automatically if the app dies
                 immediately afterwards.

WHO DECIDES
    By default JARVIS does not patch itself unasked: the patch is prepared and
    parked behind the same on-screen confirmation every other irreversible action
    uses (core/confirm.py), so the person sees what is about to change and can
    read the file first. `self_heal_auto` in settings switches that off and lets a
    validated patch apply immediately, which is how the feature behaves if you
    want it fully hands-off. Both paths run identical validation.

HARD BOUNDARIES
    * A hardcoded list of files is never patched by anyone: the updater, the
      rollback machinery itself, the permission gate, the loaders, and main.py.
      A patch engine that can edit the thing that would undo it is not a safety
      feature.
    * Nothing is written outside this installation, and only ever `*.py`.
    * The model is never asked to *write* a feature here — only to replace one
      chunk of existing code with a corrected version of itself.

NOTHING HERE RAISES. Every entry point returns a result dict.
"""
from __future__ import annotations

import ast
import json
import py_compile
import re
import shutil
import sys
import time
import uuid
from pathlib import Path

_TAG = "[SelfHeal]"

#: Files this module will not touch, whatever the traceback says.
#:
#: The first group is the self-repair machinery: editing the code that decides
#: whether a patch is safe, or that rolls one back, removes the ability to undo a
#: mistake at exactly the moment a mistake is most likely. The second group
#: governs what the assistant is *allowed* to do — a permission gate that a
#: traceback can rewrite is not a permission gate. The third is the packaging:
#: changing them cannot help a running process and can break the next start.
PROTECTED = frozenset({
    "self_heal.py", "boot_sentry.py", "skill_forge.py", "skill_crucible.py",
    "confirm.py", "undo.py", "cancel.py", "autonomy.py",
    "action_loader.py", "plugin_loader.py", "updater.py", "updater_ota.py",
    "main.py", "main.pyw", "setup.py", "requirements.txt",
    "apply_update.bat", "installer.py",
})

#: Frames from anywhere below one of these are never the suspect.
_SKIP_PATH_HINTS = ("site-packages", "dist-packages", ".venv", "venv",
                    "lib\\python", "lib/python", "node_modules", "<frozen")

MAX_CONTEXT_LINES = 50

#: A patch that changes more than this is not a hotfix, it is a rewrite, and a
#: rewrite is exactly what a bug-driven patch must never be.
MAX_PATCH_CHARS = 4000


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def history_path() -> Path:
    return _base_dir() / "config" / "patches.json"


def backups_dir() -> Path:
    return _base_dir() / "config" / "patch_backups"


def _load_history() -> list:
    try:
        data = json.loads(history_path().read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_history(entries: list) -> None:
    path = history_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(entries[-100:], indent=2, ensure_ascii=False),
                       encoding="utf-8")
        tmp.replace(path)
    except Exception as e:
        print(f"{_TAG} could not save the patch log: {e}")


# ── locate ───────────────────────────────────────────────────────────────────

_FRAME_RE = re.compile(
    r'File\s+["\']([^"\']+?\.py)["\']\s*,\s*line\s+(\d+)'
    r'(?:\s*,\s*in\s+([^\n\r]+))?', re.IGNORECASE)


def parse_traceback(text: str) -> dict:
    """Work out which file and line a traceback blames, and what went wrong.

    Walks the frames innermost-first and returns the first one that lives inside
    this installation. Frames in libraries are skipped deliberately: the correct
    response to a bug in someone else's package is to work around it in our own
    code, never to edit their file.
    """
    out = {"ok": False, "path": "", "line": 0, "function": "",
           "error_type": "", "message": "", "reason": ""}
    text = str(text or "")
    if not text.strip():
        out["reason"] = "there was no traceback to read"
        return out

    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    if lines:
        last = lines[-1]
        if ":" in last:
            head, tail = last.split(":", 1)
            out["error_type"], out["message"] = head.strip(), tail.strip()
        else:
            out["error_type"] = last

    base = _base_dir().resolve()
    for raw_path, line_str, func in reversed(_FRAME_RE.findall(text)):
        low = raw_path.replace("/", "\\").lower()
        if any(hint.replace("/", "\\").lower() in low for hint in _SKIP_PATH_HINTS):
            continue
        candidate = Path(raw_path)
        if not candidate.is_absolute():
            candidate = base / raw_path
        try:
            resolved = candidate.resolve()
        except Exception:
            continue
        if not resolved.exists() or resolved.suffix.lower() != ".py":
            continue
        if resolved.name in PROTECTED:
            out["reason"] = (f"{resolved.name} is one of the files I will not patch "
                             f"myself — it is part of how I recover from mistakes")
            continue
        try:
            resolved.relative_to(base)
        except ValueError:
            out["reason"] = "the failing file is outside my own folder"
            continue
        out.update({"ok": True, "path": str(resolved), "function": str(func).strip(),
                    "line": int(line_str or 0), "reason": ""})
        return out

    if not out["reason"]:
        out["reason"] = "no file of mine appeared in that traceback"
    return out


# ── write ────────────────────────────────────────────────────────────────────

_SYSTEM = """\
You are repairing a bug in JARVIS, a Windows voice assistant written in Python.

You are shown the traceback and the code around the failing line. Produce the
SMALLEST change that removes the failure.

Rules:
  * `target_chunk` must be an exact, verbatim copy of a contiguous block from the
    code shown, including its indentation. It is matched with a plain string
    search, so one wrong space means the patch is rejected.
  * `replacement_chunk` is what takes its place. Keep the same indentation.
  * Fix the cause, not the symptom. Add the missing None check, guard the empty
    container, handle the exception the code actually hit. Do not delete the
    feature, do not comment the failing line out, and do not wrap the whole
    function in a bare try/except that hides every future error.
  * Never change a function's signature or its return type: other modules call it.
  * If the bug cannot be fixed without seeing more code, say so honestly by
    returning {"unfixable": "why"} — a wrong patch is far worse than no patch.
  * Return ONLY JSON:
    {"explanation": "one sentence",
     "target_chunk": "...",
     "replacement_chunk": "..."}
"""


def local_context(path: Path, line: int) -> str:
    """The code around the failing line, as text the model can quote from."""
    try:
        source_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return ""
    half = MAX_CONTEXT_LINES // 2
    start = max(1, line - half)
    end = min(len(source_lines), line + half)
    return "\n".join(source_lines[start - 1:end])


def synthesize(diagnosis: dict, context_note: str = "",
               timeout_ms: int = 60_000) -> dict:
    """Ask the model for a minimal replacement chunk. Never raises."""
    if not diagnosis.get("ok"):
        return {"ok": False, "reason": diagnosis.get("reason", "nothing to repair")}
    path = Path(diagnosis["path"])
    context = local_context(path, int(diagnosis["line"]))
    if not context.strip():
        return {"ok": False, "reason": "the failing file could not be read"}

    prompt = (
        f"{_SYSTEM}\n\n"
        f"File: {path.name}\n"
        f"Failing line: {diagnosis['line']}"
        + (f" (in {diagnosis['function']})" if diagnosis.get("function") else "")
        + f"\nException: {diagnosis.get('error_type', '')}: "
          f"{diagnosis.get('message', '')[:400]}\n"
    )
    if context_note:
        prompt += f"Extra context: {context_note[:600]}\n"
    prompt += f"\nCode around the failure:\n```python\n{context}\n```\n"

    try:
        from core import gemini
        reply = gemini.as_json(prompt, tier=gemini.SMART, timeout_ms=timeout_ms,
                               default=None)
    except Exception as e:
        return {"ok": False, "reason": f"the model could not be reached ({e})"}

    if not isinstance(reply, dict):
        return {"ok": False, "reason": "the model did not return a usable patch"}
    if reply.get("unfixable"):
        return {"ok": False, "unfixable": True,
                "reason": f"it cannot be fixed from here: {reply['unfixable']}"}

    target = reply.get("target_chunk")
    replacement = reply.get("replacement_chunk")
    if not isinstance(target, str) or not target.strip():
        return {"ok": False, "reason": "the patch had nothing to replace"}
    if not isinstance(replacement, str) or not replacement.strip():
        return {"ok": False, "reason": "the patch replaced the code with nothing"}
    if len(replacement) > MAX_PATCH_CHARS:
        return {"ok": False, "reason": "the patch was a rewrite, not a fix"}

    source = path.read_text(encoding="utf-8", errors="replace")
    if source.count(target) != 1:
        return {"ok": False,
                "reason": ("the block it wanted to replace does not appear exactly "
                           f"once in {path.name} ({source.count(target)} times)")}

    patched = source.replace(target, replacement, 1)
    try:
        ast.parse(patched, filename=path.name)
    except SyntaxError as e:
        return {"ok": False,
                "reason": f"the patch had a syntax error on line {e.lineno}",
                "detail": str(e.msg)}

    return {"ok": True, "path": str(path), "source": source, "patched": patched,
            "target": target, "replacement": replacement,
            "explanation": str(reply.get("explanation") or "Hotfix.")[:300],
            "line": int(diagnosis["line"])}


# ── apply / roll back ────────────────────────────────────────────────────────

def _backup(path: Path) -> Path:
    folder = backups_dir()
    folder.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = folder / f"{path.stem}.{stamp}.{uuid.uuid4().hex[:6]}.bak"
    shutil.copy2(path, target)
    return target


def apply_patch(patch: dict, note: str = "", source: str = "manual") -> dict:
    """Write a validated patch, with a backup and a compile check either side.

    This is the only function in the project that rewrites source code, so it is
    deliberately paranoid: the staged text is compiled *before* the write, the
    original is copied aside, the write is followed by a real compile of the file
    on disk, and any failure at any step restores the backup and says so.
    """
    if not patch.get("ok"):
        return {"ok": False, "message": patch.get("reason", "nothing to apply")}

    path = Path(patch["path"])
    if path.name in PROTECTED:
        return {"ok": False, "message": f"{path.name} is protected and will not be patched"}

    patched = patch.get("patched", "")
    try:
        ast.parse(patched, filename=path.name)
    except SyntaxError as e:
        return {"ok": False, "message": f"the patch had a syntax error on line {e.lineno}"}

    try:
        backup = _backup(path)
    except Exception as e:
        return {"ok": False, "message": f"could not back up {path.name} before patching ({e})"}

    try:
        path.write_text(patched, encoding="utf-8")
        py_compile.compile(str(path), doraise=True)
    except Exception as e:
        try:
            shutil.copy2(backup, path)
        except Exception as restore_error:                  # pragma: no cover
            print(f"{_TAG} CRITICAL: could not restore {path.name}: {restore_error}")
            return {"ok": False,
                    "message": f"the patch failed and {path.name} could not be "
                               f"restored. Its original is at {backup}."}
        return {"ok": False,
                "message": f"the patch did not compile, so {path.name} was put back: {e}"}

    entry = {
        "id": uuid.uuid4().hex[:8],
        "at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "when": time.time(),
        "file": str(path),
        "name": path.name,
        "line": int(patch.get("line") or 0),
        "explanation": str(patch.get("explanation") or "")[:300],
        "backup": str(backup),
        "status": "applied",
        "source": source,
        "note": str(note or "")[:300],
    }
    entries = _load_history()
    entries.append(entry)
    _save_history(entries)
    # Arm the boot sentry before anything else can go wrong: if this process dies
    # before it reaches a working session, that watch is what undoes this write.
    try:
        from core import boot_sentry
        boot_sentry.arm(entry["id"], str(path), str(backup))
    except Exception as e:
        print(f"{_TAG} could not arm the boot sentry: {e}")
    print(f"{_TAG} patched {path.name}:{entry['line']} (patch {entry['id']})")
    return {"ok": True, "patch_id": entry["id"], "path": str(path),
            "backup": str(backup), "explanation": entry["explanation"],
            "message": f"Patched {path.name} at line {entry['line']}: "
                       f"{entry['explanation']}"}


def rollback(patch_id: str = "latest") -> dict:
    """Restore a patched file from its backup. Never raises."""
    entries = _load_history()
    for entry in reversed(entries):
        if patch_id not in ("latest", entry.get("id"), entry.get("name")):
            continue
        if entry.get("status") != "applied":
            continue
        path = Path(str(entry.get("file") or ""))
        backup = Path(str(entry.get("backup") or ""))
        if not backup.exists():
            return {"ok": False,
                    "message": f"the backup for patch {entry.get('id')} is missing, "
                               f"so I cannot undo it"}
        try:
            shutil.copy2(backup, path)
        except Exception as e:
            return {"ok": False, "message": f"could not restore {path.name}: {e}"}
        entry["status"] = "rolled_back"
        entry["rolled_back_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _save_history(entries)
        try:
            from core import boot_sentry
            watch = boot_sentry.armed()
            if watch.get("patch_id") == entry.get("id"):
                boot_sentry.disarm()
        except Exception:
            pass
        print(f"{_TAG} rolled back {path.name} (patch {entry['id']})")
        return {"ok": True, "path": str(path),
                "message": f"Undid the patch to {path.name} and restored the "
                           f"previous version."}
    return {"ok": False, "message": "there is no applied patch matching that"}


def applied_patches(limit: int = 5) -> list:
    entries = [e for e in _load_history() if e.get("status") == "applied"]
    return list(reversed(entries))[:limit]


def recent_patch_note() -> str:
    """A one-line summary for the system prompt: what changed about itself lately.
    Kept short — this rides in every session's instructions."""
    entries = applied_patches(1)
    if not entries:
        return ""
    entry = entries[0]
    return (f"A hotfix I applied to myself is live: {entry['name']} line "
            f"{entry['line']} — {entry['explanation']}")


# ── capture and act ──────────────────────────────────────────────────────────

_last_error: str = ""


def note_error(traceback_text: str, context: str = "") -> None:
    """Remember the most recent traceback so `heal` can be called without one.
    Called from the tool error paths, which are exactly where the interesting
    failures happen. Never raises."""
    global _last_error
    try:
        blob = str(traceback_text or "")
        if context:
            blob += f"\n# context: {context}"
        _last_error = blob[-8000:]
    except Exception:
        pass


def last_error() -> str:
    return _last_error


def diagnose_and_prepare(traceback_text: str = "", context: str = "") -> dict:
    """The read-only half of healing: locate, synthesise, validate — change
    nothing. Split out so a caller can show a person what would change before
    anything does, and so tests can exercise it without writing files."""
    text = traceback_text or _last_error
    diagnosed = parse_traceback(text)
    if not diagnosed.get("ok"):
        return {"ok": False, "stage": "locate",
                "message": diagnosed.get("reason", "I could not find the cause."),
                "diagnosis": diagnosed}
    patch = synthesize(diagnosed, context_note=context)
    if not patch.get("ok"):
        return {"ok": False, "stage": "synthesize",
                "message": patch.get("reason", "I could not write a fix."),
                "diagnosis": diagnosed}
    return {"ok": True, "stage": "ready", "diagnosis": diagnosed, "patch": patch}


def heal(traceback_text: str = "", context: str = "", force: bool = False) -> dict:
    """Diagnose, prepare, and either apply the fix or put it behind the gate.

    `force` is for the caller that has already obtained consent (the confirmation
    callback, or a user who switched self_heal_auto on).
    """
    prepared = diagnose_and_prepare(traceback_text, context)
    if not prepared.get("ok"):
        prepared.setdefault("stage", "prepare")
        return prepared

    patch = prepared["patch"]
    diagnosis = prepared["diagnosis"]

    auto = False
    try:
        from memory import config_manager as cfg
        auto = bool(cfg.get_self_heal_auto())
    except Exception:
        auto = False

    if auto or force:
        result = apply_patch(patch, note=diagnosis.get("message", ""),
                             source="auto" if auto else "confirmed")
        result.setdefault("diagnosis", diagnosis)
        return result

    # Park it. The banner shows what will change; the callable applies it if the
    # user accepts. Nothing is written until they do.
    try:
        from core import confirm
        if confirm.pending_title():
            return {"ok": False, "stage": "busy",
                    "message": "There is already a confirmation waiting on screen.",
                    "patch": patch, "diagnosis": diagnosis}
        message = confirm.request(
            "self-heal",
            "Apply a fix to my own code?",
            f"{diagnosis['path'].split(chr(92))[-1].split('/')[-1]} line "
            f"{diagnosis['line']}: {patch.get('explanation', '')}",
            lambda: apply_patch(patch, note=diagnosis.get("message", ""),
                                source="confirmed").get("message", "Done."))
        return {"ok": True, "stage": "awaiting_confirmation", "message": message,
                "patch": patch, "diagnosis": diagnosis}
    except Exception as e:
        return {"ok": False, "stage": "gate",
                "message": f"I prepared a fix but could not ask you to confirm it: {e}",
                "patch": patch, "diagnosis": diagnosis}


def status() -> str:
    """A spoken summary, used by the tool and by the settings panel."""
    patches = applied_patches(10)
    try:
        from memory import config_manager as cfg
        auto = cfg.get_self_heal_auto()
    except Exception:
        auto = False
    lines = [
        "SELF-REPAIR STATUS",
        f"  Apply fixes without asking: {'yes' if auto else 'no (they wait for your go-ahead)'}",
        f"  Patches live now: {len(patches)}",
        f"  Files I will never patch: {len(PROTECTED)} (the repair and permission machinery)",
    ]
    if patches:
        lines.append("  Most recent:")
        for entry in patches[:3]:
            lines.append(f"    - {entry['name']} line {entry['line']}: "
                         f"{entry['explanation']}")
    else:
        lines.append("  No patches applied — nothing has needed one.")
    if _last_error:
        first = parse_traceback(_last_error)
        if first.get("ok"):
            lines.append(f"  Waiting to be fixed: {Path(first['path']).name} "
                         f"line {first['line']} ({first.get('error_type', '')})")
    return "\n".join(lines)


def why_not_patchable(path: str) -> str:
    """Plain-language answer for a file on the protected list — used by the tool
    so "why won't you fix that one" has a real answer."""
    name = Path(str(path)).name
    if name in PROTECTED:
        return (f"{name} is one of the files I will not edit myself: it is part of "
                f"how I start, update or undo my own changes.")
    return f"{name} is not on the protected list."


def forget_history() -> int:
    """Clear the patch log (the backups stay on disk). Returns how many went."""
    entries = _load_history()
    _save_history([])
    return len(entries)


def backup_folder() -> str:
    return str(backups_dir())


__all__ = ["parse_traceback", "local_context", "synthesize", "apply_patch",
           "rollback", "applied_patches", "recent_patch_note", "note_error",
           "last_error", "diagnose_and_prepare", "heal", "status",
           "why_not_patchable", "forget_history", "backup_folder", "PROTECTED",
           "history_path", "backups_dir"]
