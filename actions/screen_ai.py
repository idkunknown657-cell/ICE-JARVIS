"""
actions/screen_ai.py — precise, verifiable pointer and reading control.

computer_control's screen_click already finds an element from a screenshot with
a labelled grid and clicks it, but grid estimating is fuzzy. screen_ai* keeps a
2-layer pipeline:

  1. UIA (Windows, via pywinauto) — the accessibility tree gives exact element
     names, types, rects and values, so "click the Save button" becomes a real
     rectangle in physical pixels instead of a guess. Multi-match ambiguity is
     reported as a numbered list instead of guessing.
  2. Vision fallback — when there is no UIA tree (non-Windows, or a raw canvas
     like a game), it degrades to computer_control's grid screenshot + FAST
     model, including post-action verification.

Actions:
  window         - set or pick the target window for the next calls
  describe       - plain-text inventory of the active/target window
  list_elements  - exact UIA dump (names, types, rects, values)
  find           - locate {item} and report its centre / candidates
  click / right_click / double_click - click {item}, exact via UIA or vision
  hover          - move the pointer onto {item} and verify what is under it
  read_text      - read text/values out of {item} (UIA value, or vision)

Every click's coordinates come from an element rect or a verified screenshot;
never from a blind constant.
"""
from __future__ import annotations

import re

try:
    import pywinauto
    from pywinauto.application import Application
    from pywinauto.controls.uiawrapper import UIAWrapper
    _PYWINAUTO = True
except Exception:
    _PYWINAUTO = False

# Imported lazily to keep module import cheap and non-Windows friendly.
# Reused from computer_control for DPI-aware capture + grid + verify.
try:
    import pyautogui as _PAG
except Exception:
    _PAG = None

# Structured PC-control debug log (never raises; see core/pc_log.py).
try:
    from core import pc_log
except Exception:                                   # pragma: no cover
    pc_log = None

# The shared engine is imported for two reasons that matter even though the
# clicking here stays in this module:
#   * importing it declares the process per-monitor DPI aware, without which a
#     UIA rectangle and pyautogui's coordinate space disagree on a scaled
#     display and every click lands offset by the scale factor;
#   * it keeps one context memory, so a target found here can be the "there"
#     that the next sentence refers to.
try:
    from core import pc_engine as _engine
except Exception:                                   # pragma: no cover
    _engine = None


def _clamp(x: int, y: int) -> tuple[int, int]:
    """Keep a point inside the real virtual desktop, corner-abort points aside."""
    try:
        if _engine is not None:
            return _engine.clamp_screen(x, y)
    except Exception:
        pass
    return int(x), int(y)


def _remember_target_ctx(label: str, x: int, y: int, w: int, h: int,
                         source: str) -> None:
    """Feed the shared context memory so "move it there" has something to mean."""
    try:
        if _engine is not None:
            _engine.remember_target(_engine.Target(
                label=str(label)[:60], x=int(x), y=int(y), w=int(w), h=int(h),
                source=source, confidence=0.9))
    except Exception:
        pass

_TARGET_WINDOW: str | None = None   # module state set by the "window" action

_INTERESTING = {
    "Button", "Edit", "Text", "Document", "Hyperlink", "TabItem", "CheckBox",
    "RadioButton", "ListItem", "MenuItem", "ComboBox", "TreeItem", "Slider",
    "HeaderItem", "DataItem", "List", "Menu", "ToolBar", "StatusBar",
    "Tree", "Spinner", "ProgressBar", "Group", "Pane", "Custom", "TitleBar",
}
_INTERESTING_SMALL = {
    "Button", "Edit", "Text", "Document", "Hyperlink", "TabItem",
    "CheckBox", "RadioButton", "ListItem", "MenuItem", "ComboBox", "DataItem",
}


def _log(msg: str) -> None:
    print(f"[screen_ai] {msg}")


def _current_app() -> str:
    """Best-effort name of the focused application, for strategy memory.
    Falls back to the targeted window title when the foreground query fails
    (the automation itself can steal focus on some setups)."""
    try:
        from win32gui import GetForegroundWindow, GetWindowText
        title = GetWindowText(GetForegroundWindow()) or ""
    except Exception:
        title = _TARGET_WINDOW or ""
    return (title.split(" - ")[-1].split(" — ")[-1].strip() or "unknown")[:40]


def _remember(app: str, item: str, strategy: str, ok: bool,
              note: str = "") -> None:
    """Feed one verified outcome into the strategy store (never raises)."""
    try:
        from core import strategy_memory
        strategy_memory.record(app, item, strategy, ok, note)
    except Exception:
        pass


def _safe_pag(fn, *args, **kwargs):
    """pyautogui mouse op with fail-safe corner recovery. If the pointer sits
    at the (0,0) fail-safe point (a virtualised/remote session can idle it
    there), pyautogui aborts every move and click — and re-checks before the
    clearing move too. We temporarily switch the fail-safe off, pull the
    pointer to centre, restore it, and retry once."""
    if _PAG is None:
        raise RuntimeError("pyautogui not installed")
    try:
        return fn(*args, **kwargs)
    except Exception as e:
        if isinstance(e, _PAG.FailSafeException):
            import time as _t
            w, h = _PAG.size()
            _PAG.FAILSAFE = False
            try:
                _PAG.moveTo(max(w // 2, 8), max(h // 2, 8), duration=0.1)
                _t.sleep(0.05)
            finally:
                _PAG.FAILSAFE = True
            return fn(*args, **kwargs)
        raise


# ── UIA primitives ───────────────────────────────────────────────────────────

def _uia_available() -> bool:
    return _PYWINAUTO and _is_windows()


def _is_windows() -> bool:
    import platform
    return platform.system() == "Windows"


def _focused_window():
    """The foreground window's ElementInfo, or None."""
    try:
        from win32gui import GetForegroundWindow
        handle = GetForegroundWindow()
        if not handle:
            return None
        return pywinauto.Desktop(backend="uia").window(handle=handle).element_info
    except Exception:
        return None


def _element_rect(elm) -> tuple[int, int, int, int]:
    try:
        r = getattr(elm, "rectangle", None) or elm.rect
        if r is None:
            return (0, 0, 0, 0)
        return (int(r.left), int(r.top), int(r.right), int(r.bottom))
    except Exception:
        try:
            r = elm.rect
            return (int(r.left), int(r.top), int(r.right), int(r.bottom))
        except Exception:
            return (0, 0, 0, 0)


def _walk(elms: list, out: list, depth: int = 0, cap: int = 500):
    """Flatten a UIA subtree into stable dicts with the info we care about.
    `out` is mutated; recursion is bounded to never blow up on huge windows."""
    if not elms or len(out) >= cap or depth > 24:
        return
    for elm in elms:
        if len(out) >= cap:
            return
        try:
            ctype = elm.control_type or ""
        except Exception:
            ctype = ""
        name = ""
        try:
            name = (elm.name or "").strip()
        except Exception:
            pass
        aid = ""
        try:
            aid = elm.automation_id or ""
        except Exception:
            pass
        if not ctype:
            continue
        left, top, right, bottom = _element_rect(elm)
        if left == right and top == bottom:
            continue                     # zero-size ghost, unclickable
        rec = {
            "type": ctype,
            "name": name,
            "aid": aid,
            "x": left, "y": top, "w": right - left, "h": bottom - top,
        }
        try:
            value = elm.value
            if value:
                rec["value"] = value
        except Exception:
            pass
        if ctype in _INTERESTING:
            out.append(rec)
        kids = []
        try:
            kids = elm.children() or []
        except Exception:
            kids = []
        _walk(kids, out, depth + 1, cap)


def _target_element_info(parameters: dict):
    """ElementInfo for the requested or focused window, or None."""
    if not _uia_available():
        return None
    win_name = (parameters.get("window") or _TARGET_WINDOW or "").strip()
    try:
        if win_name:
            try:
                wins = pywinauto.findwindows.find_windows(
                    title_re=rf".*{re.escape(win_name)}.*",
                    backend="uia", visible_only=True)
            except Exception:
                wins = []
            if not wins:
                return None
            return pywinauto.Desktop(backend="uia").window(handle=wins[0]).element_info
        return _focused_window()
    except Exception as e:
        _log(f"window resolve failed: {e}")
        return None


def _uia_inventory(parameters: dict) -> tuple[list, str | None]:
    """(elements, error). error set when the tree cannot genuinely be opened."""
    win = _target_element_info(parameters)
    if win is None:
        return [], "foreground window is not UIA-inspectable"
    try:
        kids = win.children() or []
    except Exception as e:
        return [], f"cannot read UIA tree: {e}"
    out = []
    _walk(kids, out)
    return out, None


def _exact_hit(query: str, elements: list) -> list:
    q = (query or "").strip().lower()
    if not q:
        return []
    eq = [e for e in elements if (e.get("name") or "").strip().lower() == q]
    if eq:
        return eq
    return [e for e in elements
            if q in (e.get("name") or "").lower() or q in (e.get("value") or "").lower()]


def _center(rec: dict) -> tuple[int, int]:
    return (rec.get("x", 0) + rec.get("w", 1) // 2,
            rec.get("y", 0) + rec.get("h", 1) // 2)


def _summary_line(rec: dict, idx: int = -1) -> str:
    pre = f"{idx}. " if idx >= 0 else ""
    return (f"{pre}[{rec['type']}] '{rec.get('name') or ''}'"
            f"{(' = ' + rec['value']) if rec.get('value') else ''} "
            f"at {rec['x']},{rec['y']} {rec['w']}x{rec['h']}")


def _pick_match(query: str, elements: list):
    """Exact name hit wins; then single fuzzy; ambiguity returns the list."""
    exact = [e for e in elements if (e.get("name") or "").strip().lower() == (query or "").strip().lower()]
    if len(exact) == 1:
        return exact[0], None, False
    if len(exact) > 1:
        return None, exact, True
    fuzz = [e for e in elements
            if (query or "").lower() in (e.get("name") or "").lower()]
    if len(fuzz) == 1:
        return fuzz[0], None, False
    if len(fuzz) > 1:
        return None, fuzz, True
    return None, [], False


def _element_at_point(x: int, y: int) -> str:
    """UIA element name under a physical point (used to verify moves)."""
    try:
        elm = pywinauto.Desktop(backend="uia").element_from_point(x, y)
        return (elm.name or "").strip()
    except Exception:
        return ""


# ── Vision fallback (imported lazily from computer_control) ──────────────────

def _vision_click(description: str, button: str = "left", clicks: int = 1,
                  verify=None) -> str:
    try:
        from actions.computer_control import _do_screen_click
    except Exception as e:
        return f"screen_ai click unavailable (no vision stack): {e}"
    result = _do_screen_click(description, button=button, clicks=clicks,
                              verify=verify)
    # Feed the verdict into the strategy store so the vision route is
    # remembered per-app when it is (or is not) the reliable one.
    try:
        if clicks == 1 and button == "left" and (
                "verified it worked" in result
                or "verified it took" in result):
            _remember(_current_app(), description, "vision", True)
        elif clicks == 1 and button == "left" and "NOT take effect" in result:
            _remember(_current_app(), description, "vision", False,
                      "click did not visibly take effect")
    except Exception:
        pass
    return result


def _vision_find(description: str):
    try:
        from actions.computer_control import _screen_find
    except Exception:
        return None
    return _screen_find(description)


def _vision_describe() -> str:
    """Screen inventory from a grid screenshot + FAST model."""
    try:
        import io
        from google.genai import types as gtypes
        from core import gemini
        from actions.computer_control import (_capture_with_mapping, _grid_overlay)
    except Exception as e:
        return f"vision describe unavailable: {e}"
    try:
        img, _, _ = _capture_with_mapping()
        buf = io.BytesIO()
        _grid_overlay(img).save(buf, format="PNG")
        prompt = (
            "Screenshot of a computer screen with a faint red coordinate grid "
            "(labelled every 100 px). Produce a short plain-text inventory for "
            "an assistant that controls the desktop with a mouse: the window "
            "titles in the foreground, then each visible interactive element "
            "(button, text field, menu, tab, link) with its grid coordinates, "
            "e.g. 'Button Save at 812,540'. Keep it under 400 words. If you "
            "cannot tell what an element is, skip it rather than guess."
        )
        resp = gemini.call(
            [gtypes.Part.from_bytes(data=buf.getvalue(), mime_type="image/png"), prompt],
            tier=gemini.FAST, timeout_ms=20_000)
        return (resp.text or "").strip() if resp else "no vision response"
    except Exception as e:
        return f"vision describe failed: {e}"


def _vision_read_text(item: str) -> str:
    try:
        import io
        from google.genai import types as gtypes
        from core import gemini
        from actions.computer_control import (_capture_with_mapping, _grid_overlay)
    except Exception as e:
        return f"vision read unavailable: {e}"
    try:
        img, _, _ = _capture_with_mapping()
        buf = io.BytesIO()
        _grid_overlay(img).save(buf, format="PNG")
        prompt = (
            f"Screenshot of a computer screen with a faint red grid. "
            f"Transcribe exactly and completely the text shown inside the region "
            f"described as '{item}'. Reply with ONLY the transcribed text. If the "
            f"region is an editable field, include its current text."
        )
        resp = gemini.call(
            [gtypes.Part.from_bytes(data=buf.getvalue(), mime_type="image/png"), prompt],
            tier=gemini.FAST, timeout_ms=20_000)
        return (resp.text or "").strip() if resp else "no vision response"
    except Exception as e:
        return f"vision read failed: {e}"


# ── Action bodies ────────────────────────────────────────────────────────────

def _window_action(parameters: dict) -> str:
    global _TARGET_WINDOW
    name = (parameters.get("window") or parameters.get("name") or "").strip()
    if not name:
        return "Give a window title (substring) to target, e.g. window 'Notepad'."
    if not _uia_available():
        _TARGET_WINDOW = name
        return f"Targeting window '{name}' (no UIA; vision mode)."
    wins = []
    try:
        wins = pywinauto.findwindows.find_windows(
            title_re=rf".*{re.escape(name)}.*", backend="uia", visible_only=True)
    except Exception:
        wins = []
    if not wins:
        _TARGET_WINDOW = name
        return (f"No visible window matching '{name}' right now. I will target "
                f"'{name}' if it appears.")
    _TARGET_WINDOW = name
    info = pywinauto.Desktop(backend="uia").window(handle=wins[0]).element_info
    try:
        info.set_focus()
    except Exception:
        pass
    try:
        title = info.name
    except Exception:
        title = name
    return f"Now targeting window '{title}'."


def _describe_action(parameters: dict) -> str:
    parts = []
    if _uia_available():
        elements, err = _uia_inventory(parameters)
        if not err and elements:
            try:
                wname = _target_element_info(parameters).name
            except Exception:
                wname = "foreground"
            lines = [f"{wname} — {len(elements)} controls:"]
            for i, e in enumerate(elements[:60]):
                lines.append(_summary_line(e, i))
            parts.append("\n".join(lines))
        if not elements:
            parts.append(f"(UIA tree empty: {err})")
    if not parts:
        parts.append(_vision_describe())
    return "\n".join(parts)


def _list_elements_action(parameters: dict) -> str:
    if not _uia_available():
        return "list_elements needs UIA (Windows + pywinauto); falling back to describe:\n" + _vision_describe()
    elements, err = _uia_inventory(parameters)
    if err:
        return f"list_elements: {err}"
    if not elements:
        return "No interactive elements found in the window tree."
    lines = [f"{len(elements)} elements:"]
    for e in elements:
        lines.append(_summary_line(e))
    return "\n".join(lines)


def _hints(elements: list, n: int = 6) -> str:
    lines = [e.get("name") or e["type"] for e in elements
             if (e.get("name") or "").strip()][:n]
    return ("What I see available: " + ", ".join(f"'{x}'" for x in lines)) if lines else ""


def _resolve(parameters: dict, item: str):
    """Common element resolution. Returns (mode, payload):
      ("hit", rec)     - a single precise UIA element
      ("many", list)   - ambiguous candidates
      ("none", str)    - genuinely absent from the window's tree; no guessing
      ("fallback", None) - no usable UIA tree, vision path is the only option
    """
    if not (item or "").strip():
        return "none", "give an 'item' to target"
    if _uia_available():
        elements, err = _uia_inventory(parameters)
        if elements:
            hit, many, _ = _pick_match(item, elements)
            if many:
                return "many", many
            if hit:
                return "hit", hit
            return "none", _hints(elements)
        # No interactive elements in the tree at all (game / canvas / image):
        # this is exactly the case vision exists for.
    return "fallback", None


def _find_action(parameters: dict) -> str:
    item = (parameters.get("item") or parameters.get("query")
            or parameters.get("name") or "").strip()
    if not item:
        return "find needs an 'item' to locate, e.g. find 'OK button'."
    mode, payload = _resolve(parameters, item)
    if mode == "hit":
        cx, cy = _center(payload)
        if pc_log:
            pc_log.event("screen_ai.find", app=_current_app(), item=item,
                         method="uia", box=f"{cx},{cy}", result="found")
        under = _element_at_point(cx, cy)
        suffix = f" | under pointer: '{under}'" if under else ""
        return (f"'{item}' — [{payload['type']}] '{payload.get('name') or ''}' "
                f"at centre {cx},{cy}{suffix}")
    if mode == "many":
        lines = [f"'{item}' is ambiguous — pick one:"]
        for i, e in enumerate(payload):
            lines.append(_summary_line(e, i))
        return "\n".join(lines)
    if mode == "none":
        if pc_log:
            pc_log.event("screen_ai.find", app=_current_app(), item=item,
                         method="uia", result="not-found")
        return f"'{item}' was not found in the window's accessibility tree. {payload}"
    coords = _vision_find(item)
    if coords:
        if pc_log:
            pc_log.event("screen_ai.find", app=_current_app(), item=item,
                         method="vision", box=f"{coords[0]},{coords[1]}",
                         result="found")
        return f"'{item}' found at {coords[0]},{coords[1]} (screen)"
    if pc_log:
        pc_log.event("screen_ai.find", app=_current_app(), item=item,
                     method="vision", result="not-found")
    return f"'{item}' was not found on the screen."


def _click_action(parameters: dict, button: str = "left", clicks: int = 1) -> str:
    item = (parameters.get("item") or parameters.get("query")
            or parameters.get("name") or "").strip()
    if not item:
        return f"{button} click needs an 'item' to click."
    mode, payload = _resolve(parameters, item)
    if mode == "many":
        lines = [f"'{item}' is ambiguous — pick one:"]
        for i, e in enumerate(payload):
            lines.append(_summary_line(e, i))
        return "\n".join(lines)
    if mode == "none":
        return (f"'{item}' was not found in the window's accessibility tree, "
                f"so I did not click. {payload}")
    if mode == "hit":
        cx, cy = _center(payload)
        cx, cy = _clamp(cx, cy)
        if _PAG is None:
            return (f"'{item}' is at {cx},{cy} but pyautogui is missing; "
                    f"cannot physically click.")
        _safe_pag(_PAG.click, cx, cy, button=button, clicks=clicks)
        _remember_target_ctx(payload.get("name") or item, payload.get("x", cx),
                             payload.get("y", cy), payload.get("w", 0),
                             payload.get("h", 0), "uia")
        under = _element_at_point(cx, cy)
        verb = ("Double-clicked" if clicks == 2
                else ("Right-clicked" if button == "right" else "Clicked"))
        app = _current_app()
        if pc_log:
            pc_log.event("screen_ai.click", app=app, item=item, method="uia",
                         box=f"{cx},{cy}", button=button, clicks=clicks,
                         action="click-center",
                         verify=("ok" if under else "unverified"),
                         result=under or "no-element-reported")
        if under and under.strip().lower() == (payload.get("name") or "").lower():
            if clicks == 1 and button == "left":
                _remember(app, item, "uia", True,
                          f"element name '{under}' confirmed under pointer")
            return f"{verb} '{item}' at ({cx},{cy}) and verified it took." \
                   f"  [under pointer: '{under}']"
        warn = (f" | WARNING: pointer is over '{under}', not the expected "
                f"'{payload.get('name') or item}'") if under else ""
        if clicks == 1 and button == "left":
            _remember(app, item, "uia", False,
                      f"pointer ended over '{under or 'unknown element'}'")
        return f"{verb} '{item}' at ({cx},{cy}){warn}"
    return _vision_click(item, button=button, clicks=clicks)


def _hover_action(parameters: dict) -> str:
    item = (parameters.get("item") or parameters.get("query")
            or parameters.get("name") or "").strip()
    if not item:
        return "hover needs an 'item' to point at."
    mode, payload = _resolve(parameters, item)
    if mode == "many":
        lines = [f"'{item}' is ambiguous — pick one:"]
        for i, e in enumerate(payload):
            lines.append(_summary_line(e, i))
        return "\n".join(lines)
    if mode == "none":
        return (f"'{item}' was not found in the window's accessibility tree, "
                f"so I did not move the pointer. {payload}")
    if mode == "hit":
        cx, cy = _center(payload)
        cx, cy = _clamp(cx, cy)
        if _PAG is None:
            return "pyautogui missing; cannot physically move the mouse."
        _safe_pag(_PAG.moveTo, cx, cy, duration=0.3)
        _remember_target_ctx(payload.get("name") or item, payload.get("x", cx),
                             payload.get("y", cy), payload.get("w", 0),
                             payload.get("h", 0), "uia")
        under = _element_at_point(cx, cy)
        ok = under and under.strip().lower() == (payload.get("name") or "").lower()
        if pc_log:
            pc_log.event("screen_ai.hover", app=_current_app(), item=item,
                         method="uia", box=f"{cx},{cy}", action="move-to-centre",
                         verify=("ok" if ok else (under or "unverified")),
                         result=under or "no-element-reported")
        state = "verified" if ok else (f"but UIA reports '{under}'" if under else "unverified")
        return (f"Pointer on '{item}' at ({cx},{cy}) — {state}.")
    coords = _vision_find(item)
    if coords:
        try:
            from actions.computer_control import _move
            return _move(coords[0], coords[1]) + f"  [item: '{item}']"
        except Exception:
            pass
    return f"'{item}' was not found on the screen."


def _read_text_action(parameters: dict) -> str:
    item = (parameters.get("item") or parameters.get("query")
            or parameters.get("name") or "").strip()
    if not item:
        return "read_text needs an 'item' whose text/value you want, e.g. read_text 'search box'."
    mode, payload = _resolve(parameters, item)
    if mode == "many":
        lines = [f"'{item}' is ambiguous — pick one:"]
        for i, e in enumerate(payload):
            lines.append(_summary_line(e, i))
        return "\n".join(lines)
    if mode == "none":
        return (f"'{item}' was not found in the window's accessibility tree. "
                f"{payload}")
    if mode == "hit":
        if payload.get("value"):
            return f"[{payload['type']}] '{item}' = {payload['value']}"
        name = payload.get("name") or ""
        if payload["type"] in ("Text", "Document", "Hyperlink", "Edit"):
            if name and name.lower() != item.lower():
                return f"[{payload['type']}] '{item}' is labelled: {name}"
            return f"[{payload['type']}] '{item}' has no readable text value."
        return f"[{payload['type']}] '{item}' has no readable text value."
    return _vision_read_text(item)


# ── Entry point ──────────────────────────────────────────────────────────────

def screen_ai(
    parameters: dict = None,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    params = parameters or {}

    action = str(params.get("action") or "").strip().lower().replace(" ", "_")
    action = {
        "window": "window", "target_window": "window", "focus": "window",
        "describe": "describe", "inspect": "describe", "whats_on_screen": "describe",
        "list": "list_elements", "elements": "list_elements", "list_elements": "list_elements",
        "find": "find", "locate": "find", "where_is": "find",
        "click": "click", "click_element": "click",
        "right_click": "right_click", "rightclick": "right_click",
        "double_click": "double_click", "doubleclick": "double_click",
        "hover": "hover", "move_to": "hover", "point": "hover",
        "read": "read_text", "read_text": "read_text", "get_text": "read_text",
    }.get(action, action)

    if player:
        player.write_log(f"[screen_ai] {action}")

    try:
        # Learned strategies for this app go in front of the action — the
        # model reads how the same UI behaved last time before it acts.
        try:
            from core import strategy_memory
            hint = strategy_memory.hints_for(_current_app(),
                                             str(params.get("item") or ""))
        except Exception:
            hint = ""
        if action == "window":
            return _window_action(params)
        if action == "describe":
            return _describe_action(params)
        if action == "list_elements":
            return _list_elements_action(params)
        if action == "find":
            return _find_action(params)
        if action == "click":
            return (f"{hint}\n" if hint else "") + _click_action(params)
        if action == "right_click":
            return _click_action(params, button="right")
        if action == "double_click":
            return _click_action(params, clicks=2)
        if action == "hover":
            return _hover_action(params)
        if action == "read_text":
            return _read_text_action(params)
        return ("screen_ai can: window, describe, list_elements, find, click, "
                "right_click, double_click, hover, read_text.")
    except Exception as e:
        return f"screen_ai '{action}' failed: {e}"


# ── Tool declaration (auto-discovered by core/action_loader.py) ──────────────
TOOL = {
    "name": "screen_ai",
    "description": (
        "Precise pointer and reading control for the current screen/window. "
        "Uses the Windows UI Automation tree first (exact element names, types, "
        "rectangles, values — no guessing), and falls back to a vision model on "
        "a labelled screenshot when the app has no accessibility tree. "
        "describe: quick inventory of the foreground window. list_elements: full "
        "accessibility dump. find: locate \"{item}\". click / right_click / "
        "double_click: click \"{item}\" and verify the pointer landed on it. "
        "hover: point at an item without clicking. read_text: read a field's "
        "value or a region's text. window: target a title substring for the next "
        "calls. Use this instead of computer_control's screen_click for anything "
        "inside a normal desktop app — it is far more precise."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "enum": ["window", "describe", "list_elements", "find", "click",
                         "right_click", "double_click", "hover", "read_text"],
                "description": "What to do.",
            },
            "item": {
                "type": "STRING",
                "description": "The text on/next to the element, e.g. \"Save\", \"search box\" (find / click / right_click / double_click / hover / read_text).",
            },
            "query": {
                "type": "STRING",
                "description": "Same purpose as item (find / click / hover / read_text).",
            },
            "name": {
                "type": "STRING",
                "description": "Element name / label (same purpose as item).",
            },
            "window": {
                "type": "STRING",
                "description": "Window title substring to target (window action and any other action to scope it).",
            },
        },
        "required": ["action"],
    },
    "handler": screen_ai,
}