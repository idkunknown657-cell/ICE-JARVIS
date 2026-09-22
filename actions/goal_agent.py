"""
actions/goal_agent.py — Understand → Search → Navigate → Act.

Turn ONE goal sentence into a completed task, unsupervised:

    search → open the right result → look at the screen → click/type/scroll →
    download → verify → report.

It reuses the real tool handlers (web_search, browser_control,
computer_control, screen_ai, desktop) so every op speaks the same language the
Live model already speaks — same DPI/verification/alias logic, no second
interpretation layer. On any step it either:
  * verifies the result and continues, or
  * notices a failure, tries one different approach, and continues, or
  * hits a genuine ambiguity/roadblock and ASKS the user instead of guessing.

Safety: nothing destructive is exposed here (no kill, no buy, no uninstall).
Downloads are reversible; in non-autonomous mode (goal_agent_auto=false) they
park behind the on-screen confirmation gate first.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

try:
    from core import gemini
    _GEMINI = True
except Exception:
    _GEMINI = False

try:
    from core import confirm
    _CONFIRM = True
except Exception:
    _CONFIRM = False

_MODEL_MS   = 20_000
_LOG_LIMIT  = 6          # observations fed back to the model (oldest tail)
_ASSUME_DONE = ("done", "finished", "success", "completed")
_FAILURE_HINTS = ("failed", "cannot", "can't", "could not", "error",
                  "not found", "unable", "no results", "timed out")

# The exact operation surface the planner may choose. One reply = one op.
_OPS_DOC = (
    "- search:   web search the query. params: query.\n"
    "- open_url: open a URL in the browser. params: url.\n"
    "- browser:  browser automation. params: action in [click, type, press, "
    "scroll, screenshot, get_text, get_url], selector/text/key/amount.\n"
    "- desktop:  desktop input. params: action in [type, paste, click, "
    "right_click, double_click, scroll, drag, hotkey, press, wait], "
    "text/target/button/x/y/dxy.\n"
    "- screen:   inspect & drive the active app via its UI tree. params: "
    "action in [find, click, right_click, double_click, hover, type, "
    "read_text, describe, window], target/text/name.\n"
    "- download: save a file from a URL to disk. params: url, folder, "
    "filename.\n"
    "- verify_screen: confirm an on-screen result. params: expect.\n"
    "- verify_page:   confirm a browser result. params: expect, url_contains, "
    "text_contains.\n"
    "- verify_file:   confirm a file exists. params: path, min_bytes.\n"
    "- ask:     stop and ask the user. params: question.\n"
    "- done:    finish the task. params: summary."
)


def _log(player, msg: str) -> None:
    try:
        if player is not None and hasattr(player, "write_log"):
            player.write_log(msg)
    except Exception:
        pass


def _downloads_dir() -> Path:
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes
            buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
            if ctypes.windll.shell32.SHGetFolderPathW(
                    None, 5, None, 0, buf) == 0 and buf.value:
                return Path(buf.value)
        except Exception:
            pass
    return Path(os.path.expanduser("~")) / "Downloads"


def _fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


# ── Operations ───────────────────────────────────────────────────────────────

def _op_search(params: dict, player) -> str:
    import actions.web_search as ws
    query = str(params.get("query") or "").strip()
    if not query:
        return "FAILED: search needs a query."
    return (ws._search(query)[:2000] or "No search results.")


def _op_open_url(params: dict, player) -> str:
    import actions.browser_control as bc
    url = str(params.get("url") or "").strip()
    if not url:
        return "FAILED: open_url needs a url."
    return bc.browser_control({"action": "go_to", "url": url}, player=player)


def _op_browser(params: dict, player) -> str:
    import actions.browser_control as bc
    action = str(params.get("action") or "").strip().lower()
    allowed = {"click", "type", "press", "scroll", "screenshot",
               "get_text", "get_url"}
    if action not in allowed:
        return (f"FAILED: browser action must be one of {sorted(allowed)}.")
    call = {"action": action}
    for k in ("selector", "text", "key", "amount", "url"):
        if params.get(k) not in (None, ""):
            call[k] = params[k]
    return bc.browser_control(call, player=player) or (f"{action} returned nothing")


def _op_desktop(params: dict, player) -> str:
    import actions.computer_control as cc
    action = str(params.get("action") or "").strip().lower()
    allowed = {"type", "paste", "click", "right_click", "double_click",
               "scroll", "drag", "hotkey", "press", "wait"}
    if action not in allowed:
        return (f"FAILED: desktop action must be one of {sorted(allowed)}.")
    call = {"action": action}
    for k in ("text", "target", "button", "x", "y", "dx", "dy",
              "key", "keys", "amount", "seconds"):
        if params.get(k) not in (None, ""):
            call[k] = params[k]
    return cc.computer_control(call, player=player) or f"{action} returned nothing"


def _op_screen(params: dict, player) -> str:
    import actions.screen_ai as sa
    action = str(params.get("action") or "").strip().lower()
    allowed = {"find", "click", "right_click", "double_click", "hover",
               "type", "read_text", "describe", "window"}
    if action not in allowed:
        return (f"FAILED: screen action must be one of {sorted(allowed)}.")
    call = {"action": action}
    for k in ("target", "name", "text"):
        if params.get(k) not in (None, ""):
            call[k] = params[k]
    return sa.screen_ai(call, response=None, player=player,
                        session_memory=None) or f"{action} returned nothing"


def _op_download(params: dict, player) -> str:
    import urllib.parse
    import urllib.request

    url = str(params.get("url") or "").strip()
    if not url:
        return "FAILED: download needs a url."
    folder = Path(str(params.get("folder") or _downloads_dir())).expanduser()
    folder.mkdir(parents=True, exist_ok=True)
    fname = str(params.get("filename") or "").strip() or Path(
        urllib.parse.urlparse(url).path).name or "download"
    if not Path(fname).suffix:
        fname += (Path(url.split("?")[0]).suffix or ".bin")
    dest = folder / fname

    if not _autonomous():
        if _CONFIRM and not confirm.pending_title():
            ok = {"done": False}
            confirm.request(
                "goal_download", f"Save {fname} to {folder}?",
                f"Download from {url}", run=lambda: _really_download(url, dest)
            )
            return ("[CONFIRMATION_PENDING] I need your OK before downloading "
                    f"{fname} to {folder}. Say yes to proceed.")
        if _CONFIRM and confirm.pending_title():
            return ("[CONFIRMATION_PENDING] A download is still waiting for "
                    "your approval on screen.")
    return _really_download(url, dest)


def _really_download(url: str, dest: Path) -> str:
    import urllib.request
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0 (goal_agent)"})
        with urllib.request.urlopen(req, timeout=30) as resp, open(
                dest, "wb") as out:
            while True:
                chunk = resp.read(64 * 1024)
                if not chunk:
                    break
                out.write(chunk)
        size = dest.stat().st_size
        if size <= 0:
            return "FAILED: download produced an empty file."
        return (f"Downloaded {dest.name} to {dest.parent} ({_fmt_size(size)}).")
    except Exception as e:
        return f"FAILED: download error — {e}"


def _op_verify_screen(params: dict, player) -> str:
    expect = str(params.get("expect") or "").strip()
    if not expect:
        return "FAILED: verify_screen needs 'expect'."
    try:
        from actions.screen_processor import _capture_screen
        data, _mime = _capture_screen()
    except Exception as e:
        return f"FAILED: could not capture the screen to verify — {e}"
    if not data:
        return "FAILED: empty screen capture."
    try:
        from google.genai import types as gtypes
        parts = [gtypes.Part.from_bytes(data=data, mime_type="image/jpeg")]
        prompt = (
            f"Here is a screenshot. The last action should have produced: "
            f"{expect}. Answer with exactly one word: DONE if it clearly did, "
            "FAILED if it clearly did not, or UNKNOWN if the screenshot is "
            "ambiguous."
        )
        resp = gemini.call([*parts, prompt], tier=gemini.FAST,
                           timeout_ms=_MODEL_MS)
        verdict = (resp.text or "").strip().upper() if resp else "UNKNOWN"
        verdict = verdict.split()[0] if verdict.split() else "UNKNOWN"
        if "DONE" in verdict:
            return "VERIFIED: the result is on screen."
        if "FAILED" in verdict:
            return f"UNVERIFIED: the screen does not show '{expect}'."
        return "UNKNOWN: could not confirm visually."
    except Exception as e:
        return f"UNKNOWN: verification model failed — {e}"


def _op_verify_page(params: dict, player) -> str:
    import actions.browser_control as bc
    checks = []
    if params.get("url_contains"):
        url = bc.browser_control({"action": "get_url"}, player=player)
        if not isinstance(url, str):
            return "FAILED: could not read the current URL."
        if params["url_contains"] in url:
            checks.append("url ok")
        else:
            return (f"UNVERIFIED: url is '{url}', expected to contain "
                    f"'{params['url_contains']}'")
    if params.get("text_contains"):
        text = bc.browser_control({"action": "get_text"}, player=player)
        if params["text_contains"].lower() in str(text).lower():
            checks.append("text ok")
        else:
            return (f"UNVERIFIED: page text does not contain "
                    f"'{params['text_contains']}'")
    expect = str(params.get("expect") or "")
    if expect:
        checks.append(expect)
    return "VERIFIED: " + ", ".join(checks) if checks else "UNKNOWN: nothing to verify"


def _op_verify_file(params: dict, player) -> str:
    path = Path(str(params.get("path") or "")).expanduser()
    if not path.exists():
        return f"UNVERIFIED: file not found at {path}"
    size = path.stat().st_size
    min_bytes = params.get("min_bytes")
    try:
        need = int(min_bytes) if min_bytes not in (None, "") else 0
    except (TypeError, ValueError):
        need = 0
    if need and size < need:
        return (f"UNVERIFIED: {path.name} is only {_fmt_size(size)}, "
                f"expected at least {_fmt_size(need)}")
    return f"VERIFIED: {path.name} on disk ({_fmt_size(size)})."


def _autonomous() -> bool:
    try:
        from memory.config_manager import get_goal_agent_auto
        return bool(get_goal_agent_auto())
    except Exception:
        return True


def _max_steps() -> int:
    try:
        from memory.config_manager import get_goal_agent_steps
        return int(get_goal_agent_steps())
    except Exception:
        return 16


def _run_op(op: str, params: dict, player) -> str:
    """Dispatch one planner-chosen operation to the real handlers."""
    dispatch = {
        "search":        _op_search,
        "open_url":      _op_open_url,
        "browser":       _op_browser,
        "desktop":       _op_desktop,
        "screen":        _op_screen,
        "download":      _op_download,
        "verify_screen": _op_verify_screen,
        "verify_page":   _op_verify_page,
        "verify_file":   _op_verify_file,
    }
    fn = dispatch.get(str(op).lower().strip())
    if fn is None:
        return f"FAILED: unknown operation '{op}'."
    return fn(params or {}, player)


def _looks_like_failure(text: str) -> bool:
    low = str(text or "").lower()
    return any(h in low for h in _FAILURE_HINTS)


def _plan_phases(goal: str) -> list[str]:
    """A short 2-5 item phase plan so the user can see the shape of the run."""
    if not _GEMINI:
        return ["search", "act"]
    try:
        prompt = (
            "[GOAL_AGENT] Summarise the following goal as a short list of 2-5 "
            "phase names (one or two words each) describing the plan to "
            "complete it. Output ONLY JSON: "
            '{"phases": ["search", "open", "tap"/..." ]}.\nGoal: '
            + goal
        )
        data = gemini.as_json([prompt], default=None, tier=gemini.FAST,
                              timeout_ms=_MODEL_MS)
        if isinstance(data, dict):
            phases = data.get("phases") or []
            if isinstance(phases, list):
                clean = [str(p).strip().replace(" ", "_") for p in phases]
                return [p for p in clean if p][:5]
        return ["search", "act"]
    except Exception:
        return ["search", "act"]


def _strategy_hint(goal: str) -> str:
    """Learned UI strategies worth reminding the planner about, if any."""
    try:
        from core import strategy_memory
        hint = strategy_memory.hints_for("")          # cross-app summary
        return f"{hint}\n" if hint else ""
    except Exception:
        return ""


def _next_step(goal: str, observations: list[str]) -> dict:
    """Ask the FAST model what to do next. Returns a parsed op dict
    {op, params, expect} or {} on any failure."""
    if not _GEMINI:
        return {}
    tail = "\n".join(observations[-_LOG_LIMIT:]) or "(no observations yet)"
    prompt = (
        "[GOAL_AGENT] You are executing one real task on a real computer, "
        "autonomously.\n"
        f"Goal: {goal}\n"
        "Operations you may choose (one per reply):\n"
        f"{_OPS_DOC}\n"
        + _strategy_hint(goal)
        + "Rules:\n"
        "- Pick exactly ONE operation per reply and return ONLY JSON:\n"
        '  {"op": "...", "params": {...}, "expect": "what success looks like"}.\n'
        "- After any action, on a later turn run a verify_* op unless the "
        "result is already certain from the tool output.\n"
        "- Never invent coordinates or element positions from memory: use "
        "screen find/hover with a plain-language target first.\n"
        "- If the last action clearly failed, try ONE different approach "
        "before considering asking.\n"
        "- Ask the user only after 3 consecutive failures, or when the next "
        "genuinely correct step truly depends on something only the user "
        "knows.\n"
        "- When the task's goal is achieved (verified), reply "
        '{"op": "done", "params": {"summary": "..."}}. If stuck, reply '
        '{"op": "ask", "params": {"question": "..."}}.\n'
        "Observations of what has actually happened so far (oldest last):\n"
        f"{tail}\n"
        "Now: the single best next op."
    )
    try:
        data = gemini.as_json([prompt], default=None, tier=gemini.FAST,
                              timeout_ms=_MODEL_MS)
    except Exception as e:
        print(f"[goal_agent] planner failed: {e}")
        return {}
    if not isinstance(data, dict):
        return {}
    op = str(data.get("op") or "").strip().lower()
    if not op:
        return {}
    params = data.get("params")
    if not isinstance(params, dict):
        params = {}
    return {"op": op, "params": params,
            "expect": str(data.get("expect") or "")}


def _digest(text: str) -> str:
    """One human line from a tool result for the observation log."""
    one = " ".join(str(text).split())
    return one[:240]


# ── Entry point ──────────────────────────────────────────────────────────────

def goal_agent(
    parameters: dict = None,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    params = parameters or {}
    goal = str(params.get("goal") or params.get("task") or "").strip()

    try:
        from memory.config_manager import get_goal_agent_enabled
        if not goal:
            return ("Tell me a goal and I will work through it on my own, e.g. "
                    "'find a 4K wallpaper and download it'.")
        if not get_goal_agent_enabled():
            return ("The goal agent is turned off in my settings "
                    "(goal_agent_enabled).")
    except Exception:
        pass

    _log(player, f"[goal] Understanding: {goal}")
    phases = _plan_phases(goal)
    _log(player, f"[goal] Plan: {len(phases)} phases — "
                 f"{', '.join(phases)}")

    observations: list[str] = []
    failures = 0
    budget = _max_steps()

    for step in range(budget):
        try:
            picked = _next_step(goal, observations)
        except Exception as e:
            return (f"I couldn't continue planning the goal '{goal}' "
                    f"({e}). The steps so far: "
                    + " ".join(observations[-_LOG_LIMIT:]))
        if not picked:
            return ("I could not plan any further step for that goal. So far: "
                    + (" ".join(observations[-_LOG_LIMIT:]) or "nothing yet"))

        op     = picked["op"]
        params = picked.get("params") or {}
        expect = picked.get("expect") or ""

        # end conditions chosen by the planner
        if op == "done":
            summary = str(params.get("summary") or "done.")
            _log(player, f"[goal] Done — {summary}")
            return f"Done. {summary}"
        if op == "ask":
            q = str(params.get("question") or params.get("text") or "").strip()
            _log(player, f"[goal] Asking you: {q}")
            return (f"I need you to help me finish this: {q or 'can you '
                    f'clarify what you meant?'}")
        if op == "wait" or op == "sleep":
            secs = float(params.get("seconds") or 1)
            time.sleep(min(10.0, max(0.25, secs)))
            observations.append(f"waited {secs:.1f}s")
            continue

        _log(player, f"[goal] {step + 1}/{budget} {op}")
        result = _run_op(op, params, player)
        digest = _digest(result)
        observations.append(f"{op}: {digest}")
        _log(player, f"  -> {digest}")

        # verification results are satisfying; explicit failures count
        if result.upper().startswith("VERIFIED:"):
            failures = 0
        elif result.upper().startswith(("UNVERIFIED:", "[CONFIRMATION_PENDING]")):
            failures += 1
            if failures >= 3:
                return (
                    f"I've tried a few ways but kept failing at '{goal}'. "
                    f"Latest: {digest} — can you tell me what to do differently?"
                )
            if op in ("download",) and result.upper().startswith(
                    "[CONFIRMATION_PENDING]"):
                return result
        elif _looks_like_failure(result):
            failures += 1
            if failures >= 3:
                return (
                    f"I couldn't complete '{goal}' — three approaches failed. "
                    f"Latest attempt: {digest}. Maybe check if I'm on the right "
                    f"track, and tell me what to change."
                )

    return ("I ran out of steps for that goal before verifying completion. "
            "So far: " + (" ".join(observations[-_LOG_LIMIT:])
                          or "nothing happened yet")
            + " — want me to keep going with a higher step budget?")


# ── Tool declaration (auto-discovered by core/action_loader.py) ──────────────
TOOL = {
    "name": "goal_agent",
    "description": (
"Autonomously completes a whole goal you describe in one sentence, by "
        "searching, opening results, reading the screen/browser, clicking, "
        "typing, scrolling and downloading files, "
        "verifying each step, and correcting itself when something fails. Meant "
        "for multi-step tasks like 'look up X and open the best result', "
        "'find me a nice wallpaper and download it', "
        "or 'fill this form and submit'. "
        "It asks you only when genuinely stuck. Use this for open-ended goals; "
        "use specific tools instead when the task is a single well-known step."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "goal": {
                "type": "STRING",
                "description": "The whole task, in plain language, e.g. 'find a nice "
                               "4K wallpaper and set it as my desktop background'.",
            },
            "task": {
                "type": "STRING",
                "description": "Same as goal; whichever you prefer.",
            },
        },
        "required": ["goal"],
    },
    "handler": goal_agent,
}