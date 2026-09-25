"""
actions/discord_control.py — send a Discord message the way a person would.

    FOCUS DISCORD → FIND THE RIGHT CONVERSATION → FIND THE MESSAGE BOX →
    FOCUS IT → TYPE → VERIFY THE TEXT → SEND → VERIFY IT WENT

WHY IT IS WRITTEN THIS WAY
    "Send this on Discord" has one unacceptable failure mode: the message landing
    in the wrong channel. So nothing here types blind. The conversation is
    selected through Discord's own quick switcher (Ctrl+K), which is the same
    route a person uses and the only one stable across Discord's frequent UI
    changes; the message box is located through the accessibility tree, or by
    vision when Discord exposes no tree; the text is read back before Enter is
    pressed; and after sending, the box being *empty* is the proof it went out —
    a non-empty box is how you catch a message that never sent.

    The recipient is never guessed from the visible screen. If the quick switcher
    does not turn up the name it was given, the message is not sent and the user
    is told exactly that.

SAFETY
    Sending is an outward action. It happens when the user asked for it in this
    turn. The autonomous loop may not message anyone on its own initiative unless
    the user has explicitly switched that on (discord_autonomous_send), because
    "JARVIS messaged my friend while I was out" is not a feature anyone wants by
    accident.
"""

from __future__ import annotations

import platform
import time

_SYSTEM = platform.system()

try:
    from core import pc_engine as engine
except Exception:                                   # pragma: no cover
    engine = None

try:
    from core import autonomy
except Exception:                                   # pragma: no cover
    autonomy = None

try:
    from core import confirm
except Exception:                                   # pragma: no cover
    confirm = None

try:
    from core import pc_log
except Exception:                                   # pragma: no cover
    pc_log = None

_DISCORD_TITLE = "Discord"
_DISCORD_WEB = "https://discord.com/app"
_QUICK_SWITCH = ("ctrl", "k")
_MESSAGE_BOX_HINTS = (
    "message", "message #", "text input", "chat input", "type a message",
)


def _note(text: str, kind: str = "discord") -> None:
    if autonomy is not None:
        try:
            autonomy.feed(kind, text)
        except Exception:
            pass


def _log(msg: str) -> None:
    try:
        print(str(msg).encode("ascii", "replace").decode("ascii"))
    except Exception:
        pass


def _unavailable() -> str | None:
    if engine is None:
        return "The computer-control engine is unavailable in this build."
    if not engine.state().get("input"):
        return "Physical input is unavailable in this build."
    return None


# ════════════════════════════════════════════════════════════════════════════
#  Discord window
# ════════════════════════════════════════════════════════════════════════════

def _find_discord(*, launch: bool = True, timeout: float = 12.0) -> tuple[bool, str]:
    """Bring Discord forward, launching it when there is nothing to bring.

    The desktop app is preferred over the web app: it keeps the user's existing
    session and does not open a Chromium automation window on top of it.
    """
    if engine.focus_window(_DISCORD_TITLE):
        return True, "Discord is in front."
    if not launch:
        return False, "Discord is not running."
    # Nothing to focus: open it. The desktop app first; its window follows.
    engine.open_uri("discord://")
    if engine.wait_for_window(_DISCORD_TITLE, timeout=timeout):
        return True, "Opened the Discord app."
    opened = engine.open_uri(_DISCORD_WEB)
    if opened.ok and engine.wait_for_window(_DISCORD_TITLE, timeout=timeout):
        return True, "Opened Discord in the browser."
    return False, ("Discord is not running and I could not start it. Open it once "
                   "yourself and I will take it from there.")


def _message_box(description: str = "") -> "engine.Target | None":
    """Locate the message input inside the selected conversation."""
    desc = (description or "").strip() or "the message input box at the bottom of the conversation"
    # The UIA tree names it consistently; vision is the fallback for the web app
    # in a browser that exposes nothing useful.
    return engine.locate("message box") or engine.locate(desc)


# ════════════════════════════════════════════════════════════════════════════
#  Send
# ════════════════════════════════════════════════════════════════════════════

def send_message(text: str, *, to: str = "", autonomous: bool = False,
                 attachment_note: str = "") -> str:
    """Send one message to one named conversation, with verification."""
    problem = _unavailable()
    if problem:
        return problem

    body = str(text if text is not None else "").strip()
    if not body:
        return "There is no message text to send."
    if autonomy is not None and not autonomy.get_mode("discord"):
        return ("Discord control is switched off — turn it on in Settings under "
                "Autonomy before I touch Discord.")

    target = str(to or "").strip()
    if not target:
        return ("Tell me who or which channel to send it to — I will not guess a "
                "recipient, because the wrong channel cannot be unsent.")

    if autonomous:
        # Standing permission to DM people is a deliberately separate switch.
        try:
            from memory import config_manager
            if not bool(config_manager.load_api_keys().get(
                    "discord_autonomous_send", False)):
                return ("I have autonomous control, but sending messages on my own "
                        "initiative is switched off. Ask me to send it and I will.")
        except Exception:
            return ("I cannot check the autonomous-send setting, so I have not "
                    "messaged anyone.")

    log = []
    ok_discord, why = _find_discord()
    log.append(why)
    if not ok_discord:
        return " ".join(log)
    time.sleep(0.4)

    # ── select the conversation through the quick switcher ─────────────────
    if not engine.press_keys(*_QUICK_SWITCH).ok:
        return "I could not open Discord's quick switcher."
    time.sleep(0.5)
    typed = engine.type_text(target, verify=False)
    if not typed.ok:
        engine.press_keys("esc")
        return f"I could not type the recipient name: {typed.detail}"
    time.sleep(0.9)                      # let the results list settle

    before = engine.signature()
    engine.press_keys("enter")
    time.sleep(0.7)
    if engine.signature_distance(before, engine.signature()) < 1.0:
        log.append(f"The quick switcher found nothing for {target!r}.")
        engine.press_keys("esc")
        return " ".join(log + ["I have not sent anything."])
    log.append(f"Selected {target!r}.")

    # ── find and focus the message box ─────────────────────────────────────
    box = _message_box()
    if box is None:
        return " ".join(log + ["I could not find the message box, so nothing was "
                               "sent."])
    cx, cy = box.center if (box.w or box.h) else (box.x, box.y)
    engine.click_at(cx, cy)
    time.sleep(0.25)

    # ── type, then verify the text really is there ─────────────────────────
    if attachment_note:
        body = f"{body}\n{attachment_note}" if body else attachment_note
    typed = engine.type_text(body, verify=True)
    if not typed.ok:
        return " ".join(log + [f"Typing failed: {typed.detail}. Nothing was sent."])
    typed_now = engine.focused_value()
    if typed_now and body.strip() not in typed_now:
        return " ".join(log + [
            f"The message box does not contain the text I typed, so I did not "
            f"press send. It currently reads: {typed_now[:80]!r}"])
    log.append("Text verified in the box.")

    # ── send, then verify ──────────────────────────────────────────────────
    engine.press_keys("enter")
    time.sleep(0.8)
    remaining = engine.focused_value()
    if remaining and body.strip() in remaining:
        return " ".join(log + ["The text is still sitting in the box — the message "
                               "did not send."])
    log.append("Sent.")
    if pc_log:
        pc_log.event("discord.send", app="Discord", item=target,
                     verify="ok", result=f"text<{len(body)} chars>")
    _note(f"Discord → {target}: sent {len(body)} characters", "ok")
    return " ".join(log)


# ════════════════════════════════════════════════════════════════════════════
#  Read
# ════════════════════════════════════════════════════════════════════════════

def read_conversation(*, limit: int = 0) -> str:
    """Read what is on screen in the current Discord conversation."""
    problem = _unavailable()
    if problem:
        return problem
    ok, why = _find_discord(launch=False)
    if not ok:
        return why
    try:
        text = engine.describe_screen()
    except Exception as e:
        return f"I could not read the Discord window: {e}"
    if limit:
        text = text[:int(limit)]
    return f"Discord — current view:\n{text}"


# ════════════════════════════════════════════════════════════════════════════
#  Entry point
# ════════════════════════════════════════════════════════════════════════════

def discord_control(parameters: dict = None, response=None, player=None,
                    session_memory=None) -> str:
    params = parameters or {}
    action = str(params.get("action") or "send").strip().lower().replace("-", "_")
    action = {"message": "send", "dm": "send", "post": "send", "open": "focus",
              "read": "read", "look": "read"}.get(action, action)

    if player:
        try:
            player.write_log(f"[discord] {action}")
        except Exception:
            pass
    _log(f"[discord] {action}")

    if action == "focus":
        problem = _unavailable()
        if problem:
            return problem
        ok, why = _find_discord()
        return why
    if action == "read":
        return read_conversation(limit=int(params.get("limit") or 0))
    if action == "send":
        return send_message(
            str(params.get("text") or params.get("message") or ""),
            to=str(params.get("to") or params.get("target")
                   or params.get("channel") or params.get("person") or ""),
            autonomous=bool(params.get("autonomous")),
            attachment_note=str(params.get("attachment_note") or ""),
        )
    return "discord_control can: send, read, focus."


TOOL = {
    "name": "discord_control",
    "description": (
        "Sends exactly one Discord message to exactly one named person, channel "
        "or server, after finding and focusing the real message box and verifying "
        "the text that is in it. Use this whenever the user asks to message "
        "somebody on Discord. The recipient goes in `to` (the name as it appears "
        "in Discord) and the words to send go in `text`, verbatim. It never "
        "guesses a recipient and never messages anyone on its own initiative."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "enum": ["send", "read", "focus"],
                "description": "send = message someone (default); read = describe "
                               "the conversation on screen; focus = bring Discord "
                               "to the front.",
            },
            "to": {
                "type": "STRING",
                "description": "Who or which channel to message, exactly as it "
                               "appears in Discord (a person's display name, "
                               "#channel, or server name).",
            },
            "text": {
                "type": "STRING",
                "description": "The message to send, word for word.",
            },
            "autonomous": {
                "type": "BOOLEAN",
                "description": "True only when JARVIS is acting on its own "
                               "initiative; leave false when the user asked.",
            },
            "attachment_note": {
                "type": "STRING",
                "description": "Optional extra line (e.g. a link) to include.",
            },
            "limit": {
                "type": "NUMBER",
                "description": "Character limit for read.",
            },
        },
        "required": ["action"],
    },
    "handler": discord_control,
}
