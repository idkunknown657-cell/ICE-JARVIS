"""
actions/text_assist.py — precise text entry and writing help.

Two very different jobs live here:

1. Mechanical text entry that lands EXACTLY — through the clipboard (paste) so
   nothing gets eaten by keyboard timing, with a deliberate per-character fallback
   for apps that ignore paste (some terminals, game chats).

2. Writing assistance — draft, rewrite, expand, condense, proofread, translate —
   generated with the FAST tier. Text can come from a {text} parameter or a
   file ({file}); results can be handed back as text or written to {save_to}.

The writing and the typing are separate actions: models should first ask the
user to approve generated content before inserting it into a document.
"""
from __future__ import annotations

try:
    from core import gemini
    _GEMINI = True
except Exception:
    _GEMINI = False


def _log(msg: str) -> None:
    print(f"[text_assist] {msg}")


def _take(params: dict, *keys: str, default: str = "") -> str:
    for k in keys:
        v = params.get(k)
        if v not in (None, ""):
            return str(v)
    return default


def _g(text: str) -> str:
    """One FAST generation call; returns text or '' on failure."""
    if not _GEMINI or not (text or "").strip():
        return ""
    try:
        resp = gemini.call([text], tier=gemini.FAST, timeout_ms=30_000)
        return (resp.text or "").strip() if resp else ""
    except Exception as e:
        _log(f"gemini failed: {e}")
        return ""


def _read_file(path: str) -> str:
    from pathlib import Path
    p = Path(str(path)).expanduser()
    if not p.exists():
        raise FileNotFoundError(f"file not found: {p}")
    return p.read_text(encoding="utf-8", errors="replace")


def _write_file(path: str, content: str) -> str:
    from pathlib import Path
    p = Path(str(path)).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return str(p)


# ── Mechanical entry ─────────────────────────────────────────────────────────

def _paste_action(params: dict) -> str:
    text = _take(params, "text", "content")
    if not text:
        return "paste needs 'text' to insert."
    try:
        from actions.computer_control import _clipboard_paste
    except Exception as e:
        return f"paste unavailable: {e}"
    return _clipboard_paste(text)


def _replace_action(params: dict) -> str:
    text = _take(params, "text", "content")
    if not text:
        return "replace needs 'text' to replace the current field with."
    try:
        from actions import computer_control as cc
    except Exception as e:
        return f"replace unavailable: {e}"
    try:
        cc._clipboard_paste(text)          # clipboard first (exact)
    except Exception:
        return cc._smart_type(text)
    cleared = cc._clear_field()
    return f"{cleared}; then pasted: {text[:60]}{'...' if len(text) > 60 else ''}"


def _type_action(params: dict) -> str:
    text = _take(params, "text", "content")
    if not text:
        return "type needs 'text' to type into the focused field."
    try:
        from actions.computer_control import _smart_type
    except Exception as e:
        return f"type unavailable: {e}"
    return _smart_type(text)


# ── Writing assistance ───────────────────────────────────────────────────────

_MODE_PROMPTS = {
    "draft": (
        "You are a sharp, natural writer. Using the topic/situation below, write "
        "{type} (a {tone} {type}, aimed at {audience}). Make it specific, alive "
        "and human — no corporate filler, no clichéd openings. Output ONLY the "
        "final {type} text, ready to paste."
    ),
    "rewrite": (
        "Rewrite the text below to be {tone} and roughly {length}. Keep all "
        "facts and meaning intact; improve flow, rhythm and precision. "
        "Output ONLY the rewritten text."
    ),
    "expand": (
        "Expand the text below into a fuller, more detailed version (~{length}). "
        "Stay faithful to its meaning; add concrete detail where the original "
        "is vague. Output ONLY the expanded text."
    ),
    "condense": (
        "Condense the text below into a tight summary (~{length}). Keep the "
        "essential facts and the tone. Output ONLY the condensed text."
    ),
    "proofread": (
        "Proofread the text below: fix grammar, spelling, punctuation and "
        "awkward phrasing without changing the author's voice or facts. "
        "Then output: the corrected text, a blank line, then a short "
        "'Fixes:' list (one line per fix)."
    ),
    "translate": (
        "Translate the text below into {target}. Keep the meaning and natural "
        "register; do not translate names. Output ONLY the translation."
    ),
}

_DEFAULTS = {
    "tone": "clear and natural",
    "length": "similar length",
    "audience": "the reader",
    "type": "short piece",
    "target": "the same language as the user's request",
}


def _prepare(params: dict, mode: str) -> str:
    """Assemble the source text + prompt for a writing mode."""
    text = _take(params, "text", "content")
    file_path = _take(params, "file")
    if file_path:
        text = _read_file(file_path)
    if not (text or "").strip() and mode != "draft":
        raise ValueError(f"{mode} needs 'text' (or a 'file' to read).")

    d = _DEFAULTS
    starter = f"Source text:\n\n{text.strip()}\n"
    if mode == "draft":
        topic = _take(params, "topic", "topic_or_situation", "about", "prompt")
        if not topic:
            raise ValueError("draft needs a 'topic' to write about.")
        starter = f"Topic / situation:\n\n{topic.strip()}\n"
    desc = _MODE_PROMPTS[mode].format(
        type=_take(params, "type", default=d["type"]),
        tone=_take(params, "tone", default=d["tone"]),
        length=_take(params, "length", default=d["length"]),
        audience=_take(params, "audience", default=d["audience"]),
        target=_take(params, "to", "target_language", default=d["target"]),
    )
    return f"{desc}\n\n{starter}"


def _writing_action(params: dict, mode: str) -> str:
    try:
        prompt = _prepare(params, mode)
    except (ValueError, FileNotFoundError) as e:
        return str(e)
    result = _g(prompt)
    if not result:
        return f"{mode} produced nothing (check the Gemini tier)."
    save_to = _take(params, "save_to", "output_file", "out")
    if save_to:
        try:
            path = _write_file(save_to, result)
        except Exception as e:
            return f"{mode} done, but saving failed: {e}\n\n{result}"
        return f"{mode} saved to {path}:\n\n{result[:400]}{'…' if len(result) > 400 else ''}"
    return result


# ── Entry point ──────────────────────────────────────────────────────────────

def text_assist(
    parameters: dict = None,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    params = parameters or {}

    action = str(params.get("action") or "").strip().lower().replace(" ", "_")
    action = {
        "paste": "paste", "insert": "paste", "type_in": "paste",
        "replace": "replace", "replace_text": "replace", "overwrite": "replace",
        "type": "type", "type_text": "type", "keyboard": "type",
        "draft": "draft", "full": "draft", "write": "draft", "generate": "draft",
        "rewrite": "rewrite", "rephrase": "rewrite",
        "expand": "expand", "elaborate": "expand", "lengthen": "expand",
        "condense": "condense", "shorten": "condense", "summarize": "condense",
        "proofread": "proofread", "fix_writing": "proofread",
        "translate": "translate",
    }.get(action, action)

    if player:
        player.write_log(f"[text_assist] {action}")
    _log(f"action={action}")

    try:
        if action == "paste":
            return _paste_action(params)
        if action == "replace":
            return _replace_action(params)
        if action == "type":
            return _type_action(params)
        if action in _MODE_PROMPTS:
            return _writing_action(params, action)
        return ("text_assist can: paste, replace, type, draft, rewrite, expand, "
                "condense, proofread, translate.")
    except Exception as e:
        return f"text_assist '{action}' failed: {e}"


# ── Tool declaration (auto-discovered by core/action_loader.py) ──────────────
TOOL = {
    "name": "text_assist",
    "description": (
        "Precise text entry and writing help. paste: insert 'text' exactly via "
        "clipboard into whatever field is focused. replace: wipe the current field "
        "and paste new 'text'. type: type 'text' character by character into the "
        "focused field. draft: write something new from a 'topic'. rewrite / expand / "
        "condense / proofread / translate: transform the 'text' (or a file via "
        "'file'); optionally save the result to 'save_to'. Writing actions return "
        "the text instead of inserting it — show the user before pasting."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "enum": ["paste", "replace", "type", "draft", "rewrite", "expand",
                         "condense", "proofread", "translate"],
                "description": "What to do.",
            },
            "text": {
                "type": "STRING",
                "description": "Text to paste/type or the text to transform (paste / replace / type / rewrite / expand / condense / proofread / translate).",
            },
            "content": {
                "type": "STRING",
                "description": "Same purpose as text.",
            },
            "file": {
                "type": "STRING",
                "description": "Read source text from this path instead of 'text' (rewrite / expand / condense / proofread / translate).",
            },
            "save_to": {
                "type": "STRING",
                "description": "Write the writing result to this path instead of returning it.",
            },
            "topic": {
                "type": "STRING",
                "description": "What to write about (draft).",
            },
            "type": {
                "type": "STRING",
                "description": "What kind of output for draft: email, tweet, message, reply, essay...",
            },
            "tone": {
                "type": "STRING",
                "description": "Tone for draft/rewrite, e.g. friendly, formal, sharp.",
            },
            "length": {
                "type": "STRING",
                "description": "Target length, e.g. 'a short paragraph', '250 words'.",
            },
            "to": {
                "type": "STRING",
                "description": "Target language for translate.",
            },
        },
        "required": ["action"],
    },
    "handler": text_assist,
}