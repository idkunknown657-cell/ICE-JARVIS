import platform as _platform
import subprocess as _subprocess

# ── Nuclear: force CREATE_NO_WINDOW on EVERY subprocess call on Windows ───────
# This patches Popen itself, so no per-file flag is needed anywhere.
if _platform.system() == "Windows":
    _OrigPopen = _subprocess.Popen

    class _Popen(_OrigPopen):
        def __init__(self, args, **kw):
            kw["creationflags"] = kw.get("creationflags", 0) | _subprocess.CREATE_NO_WINDOW
            kw.pop("startupinfo", None)   # drop any stale/shared STARTUPINFO
            super().__init__(args, **                       kw)

    _subprocess.Popen = _Popen


# ── Console must survive non-UTF-8 code pages ────────────────────────────────
# Every status line in this file carries an emoji, and on a legacy Windows
# console the active code page is the system one — cp1254 in Turkey, cp1251 in
# Russia, cp932 in Japan. Printing an emoji there raises UnicodeEncodeError, and
# because most of these prints sit inside the receive loop it takes the session
# down on startup. Reconfiguring to UTF-8 with a replacement fallback costs
# nothing and makes the app launch the same way in every locale.
import sys as _sys

for _stream in ("stdout", "stderr"):
    try:
        _s = getattr(_sys, _stream, None)
        if _s is not None and hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass          # pythonw / redirected pipes / anything exotic — never fatal

# ─────────────────────────────────────────────────────────────────────────────

import asyncio
import collections
import re
import threading
import time
import json
import sys
import traceback
from datetime import datetime
from pathlib import Path

import sounddevice as sd
import numpy as np
from google import genai
from google.genai import types
from webui import JarvisUI   # new WebView2 interface (ui.py kept as legacy Qt UI)
from memory.memory_manager import (
    load_memory, update_memory, format_memory_for_prompt,
    save_session_summary, pop_last_session,
    search_memory, set_trim_notifier,
)

# The file-backed tools (open_app, web_search, browser_control, …) are no longer
# imported or declared here — they self-describe via a TOOL dict in their own
# actions/*.py file and are auto-discovered by core.action_loader at startup.
# Only tools that are tied to live-session state stay inline in this file
# (screen_process, close_camera, save_memory, manage_monitor, shutdown_jarvis,
# system_status).
from actions.screen_processor  import _capture_camera, _capture_screen
from actions.system_monitor    import SystemMonitor, get_system_status
from actions.proactive         import ProactiveEngine
from actions.background_monitor import (
    add_monitor, remove_monitor, list_monitors, check_all as monitor_check_all,
)
from actions.web_search        import _news as _fetch_news_sync
from memory.config_manager     import (
    get_brief_enabled, get_media_resolution, get_proactive_audio_enabled,
    get_push_to_talk_enabled, get_thinking_enabled, get_turn_tuning, get_voice,
    get_wake_word_enabled, save_wake_word_enabled,    get_input_device, get_output_device,
    get_screen_awareness, get_observe_interval, get_proactive_enabled,
    get_memory_enabled, get_ambient_mode, get_ambient_interval_min,
    get_track_activity, get_talk_cadence, language_hint, get_screen_glance,
    get_narrate_actions, personality_hint, narration_hint, emotion_rules,
    get_screen_share_quality,
)
from core.plugin_loader        import discover_plugins
from core.screen_observer      import ScreenObserver
from core                      import undo as undo_stack
from core                      import confirm as confirm_gate
from core                      import audio_devices
from core.action_loader        import discover_actions
from core.echo                 import EchoGuard
from core.tone                 import mood_tag
from core.emotion              import delivery_tag as _emotion_delivery
from core.viseme               import VisemeStream
from core.language             import language_directive, strip_transient_prefix
from core.persona              import build_persona_block
from core                      import learning as learning_mod
from core                      import self_training
from core                      import usage as usage_mod
from core                      import reasoner as reasoner_mod
from core.screen_share        import settings_for, frame_fingerprint
from core.visual_memory       import (
    VisualMemory, change_regions, significance as _screen_significance,
    adaptive_poll as _adaptive_poll,
)
from core.wake_word            import (
    WakeWordDetector, is_ready as wake_is_ready, install_and_download as wake_install,
)

# How long the assistant stays awake with no user speech before it auto-sleeps
# again (wake-word mode only).
WAKE_SLEEP_TIMEOUT = 120.0   # seconds (2 minutes)

def get_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


def _enable_file_log():
    """Mirror all output to logs/jarvis.log — ALWAYS on, source or frozen.

    Normal mode runs windowless (pythonw), so without this every print and
    traceback vanishes — a connection failure or crash leaves zero evidence.
    Debug mode (start_debug.bat) still shows the console AND gets the file.
    Rotates at ~2 MB. Never raises.
    """
    try:
        from datetime import datetime as _dt
        log_dir = get_base_dir() / "logs"
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        log_path = log_dir / "jarvis.log"
        try:
            if log_path.exists() and log_path.stat().st_size > 2_000_000:
                bak = log_path.with_name("jarvis.bak.log")
                try:
                    if bak.exists():
                        bak.unlink()
                except Exception:
                    pass
                log_path.rename(bak)
        except Exception:
            pass
        f = open(log_path, "a", encoding="utf-8", errors="replace", buffering=1)
        f.write(f"\n===== JARVIS start {_dt.now():%Y-%m-%d %H:%M:%S} =====\n")
        f.flush()

        class _Tee:
            """Writes to the console AND the log file. pythonw has no console
            (original stdout is None) — then it is a plain pass-to-file."""

            def __init__(self, orig, logf):
                self._orig, self._f = orig, logf

            def write(self, s):
                try:
                    self._f.write(s)
                except Exception:
                    pass
                if self._orig is not None:
                    try:
                        return self._orig.write(s)
                    except Exception:
                        return len(s)
                return len(s)

            def flush(self):
                try:
                    self._f.flush()
                except Exception:
                    pass
                if self._orig is not None:
                    try:
                        self._orig.flush()
                    except Exception:
                        pass

            def __getattr__(self, name):      # isatty, fileno, encoding, …
                return getattr(self._orig, name) if self._orig is not None \
                    else (_ for _ in ()).throw(AttributeError(name))

        sys.stdout = _Tee(sys.stdout, f)
        sys.stderr = _Tee(sys.stderr, f)

        def _hook(exc_type, exc_val, exc_tb):
            import traceback as _tb
            try:
                f.write("".join(_tb.format_exception(exc_type, exc_val, exc_tb)))
                f.flush()
            except Exception:
                pass

        sys.excepthook = _hook
        try:
            import threading as _th
            _th.excepthook = lambda args: _hook(
                args.exc_type, args.exc_value, args.exc_traceback)
        except Exception:
            pass
    except Exception:
        pass

BASE_DIR        = get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"
PROMPT_PATH     = BASE_DIR / "core" / "prompt.txt"
LIVE_MODEL          = "models/gemini-3.1-flash-live-preview"
CHANNELS            = 1
SEND_SAMPLE_RATE    = 16000 
RECEIVE_SAMPLE_RATE = 24000
CHUNK_SIZE          = 1024

# RMS below which 16-bit PCM is treated as room silence; above _LEVEL_FULL it
# reads as a full-height waveform. Tuned so ordinary speech lands mid-range and
# the bars still move for a quiet talker — language- and device-independent.
_LEVEL_FLOOR = 60.0
_LEVEL_FULL  = 2600.0


def _pcm_level(samples) -> float:
    """Map a block of int16 PCM samples to a 0.0–1.0 loudness level for the HUD
    waveform. Returns 0.0 on empty/invalid input so it can never raise."""
    try:
        x = np.asarray(samples, dtype=np.float32)
        if x.size == 0:
            return 0.0
        rms = float(np.sqrt(np.mean(x * x)))
    except Exception:
        return 0.0
    if rms <= _LEVEL_FLOOR:
        return 0.0
    return min(1.0, (rms - _LEVEL_FLOOR) / (_LEVEL_FULL - _LEVEL_FLOOR))


# ── Viseme extraction ─────────────────────────────────────────────────────────
# The avatar's mouth used to be driven by one RMS value per ~200 ms write batch,
# which is five updates a second averaged over a fifth of a second — it could
# only ever flap. These read the *shape* of each 20 ms slice straight from the
# spectrum of the audio being played, so no transcript, no forced alignment and
# no language assumption: it works the same for Turkish and English.
#
# Two numbers come out. Openness tracks the first formant — F1 climbs as the jaw
# drops, so /a/ reads open and /i/ or /u/ read closed. Width tracks the second —
# F2 is high for spread vowels (/i/, /e/) and low for rounded ones (/u/, /o/).
# Extra time beyond the device's reported output latency before the microphone
# is trusted again: covers room decay and the speaker's own settling.
_TAIL_MARGIN = 0.25

_VIS_WIN = 1024        # ~43 ms analysis window at 24 kHz: enough for formants
_VIS_HOP = 480         # 20 ms between frames, i.e. 50 shapes a second

# Delay from handing the first bytes of a reply to an already-running output
# stream to hearing them: one callback period, plus whatever the DAC adds.
_FIRST_SOUND = CHUNK_SIZE / RECEIVE_SAMPLE_RATE      # ~43 ms
# How far past the device's own buffer the mouth's timeline may drift before it
# is re-anchored. The buffer is the hard limit on how much audio can be queued
# ahead, so anything beyond it plus a margin for clock error is impossible.
_CURSOR_SLACK = 0.15

# Erring early is the safe direction. A viewer tolerates a mouth that moves
# slightly before the sound far better than one that moves after it — the
# broadcast limits are about 45 ms of lag against 125 ms of lead — so where
# this is uncertain it is biased to lead.


def _pcm_visemes(samples, sr: int = 24000):
    """Slice a PCM block into (level, openness, width) frames, one per 20 ms.

    Returns [] on anything unexpected — the mouth falls back to loudness-only
    articulation rather than the caller having to handle an error.
    """
    try:
        x = np.asarray(samples, dtype=np.float32)
        if x.size < _VIS_WIN:
            return []
        win = np.hanning(_VIS_WIN).astype(np.float32)
        freqs = np.fft.rfftfreq(_VIS_WIN, 1.0 / sr)
        b_f1_lo = (freqs >= 150) & (freqs < 450)     # F1 of close vowels
        b_f1_hi = (freqs >= 450) & (freqs < 1100)    # F1 of open vowels
        b_f2_bk = (freqs >= 600) & (freqs < 1300)    # F2 of rounded vowels
        b_f2_fr = (freqs >= 1700) & (freqs < 3200)   # F2 of spread vowels
        b_hiss = (freqs >= 3800) & (freqs < 8000)    # fricatives

        # One frame per hop across the *whole* block. Stepping only while a full
        # window fits stopped 1024 - 480 samples short of the end, so a 200 ms
        # batch yielded 160 ms of schedule: the mouth ran out of frames before
        # the audio ran out of sound, and each batch no longer lined up with the
        # end of the one before it. Losing 20 % of every batch is most of why
        # the mouth did not track the words.
        out = []
        for start in range(0, x.size, _VIS_HOP):
            # The level gates closures, so it is measured over exactly this
            # 20 ms and never looks ahead. The spectrum needs a longer window
            # to resolve formants and may be short-filled at the very end.
            level = _pcm_level(x[start:start + _VIS_HOP])
            seg = x[start:start + _VIS_WIN]
            if seg.size < _VIS_WIN:
                seg = np.concatenate([seg, np.zeros(_VIS_WIN - seg.size,
                                                    dtype=np.float32)])
            if level <= 0.0:
                out.append((0.0, 0.0, 0.0))
                continue
            mag = np.abs(np.fft.rfft((seg - seg.mean()) * win))
            f1l, f1h = float(mag[b_f1_lo].sum()), float(mag[b_f1_hi].sum())
            f2b, f2f = float(mag[b_f2_bk].sum()), float(mag[b_f2_fr].sum())
            hiss = float(mag[b_hiss].sum())

            openness = f1h / (f1l + f1h + 1e-6)
            width = (f2f - f2b) / (f2f + f2b + 1e-6)
            # A wide-open jaw physically cannot purse, so openness damps width.
            # /a/ has a low enough F2 to read as "rounded" on the bands alone;
            # letting openness suppress the width term is what keeps an open
            # vowel from pursing.
            width *= (1.0 - openness) ** 0.8
            # Fricatives are formed with a nearly closed mouth.
            h = hiss / (f1l + f1h + f2b + f2f + hiss + 1e-6)
            openness *= 1.0 - 0.65 * min(1.0, h * 2.5)
            out.append((level,
                        float(min(1.0, max(0.0, openness))),
                        float(min(1.0, max(-1.0, width)))))
        return out
    except Exception:
        return []


def _describe_tools(declarations) -> str:
    """One line per capability, straight from the live tool declarations.

    Derived rather than written down: the action and plugin registries are
    discovered at startup, so whatever the user has installed is what the model
    is told it can do. Adding a plugin extends this by itself, and removing one
    stops the model from claiming an ability it no longer has.
    """
    lines = []
    for d in declarations or ():
        try:
            name = d.get("name") if isinstance(d, dict) else getattr(d, "name", None)
            desc = (d.get("description") if isinstance(d, dict)
                    else getattr(d, "description", "")) or ""
        except Exception:
            continue
        if not name:
            continue
        desc = " ".join(str(desc).split())
        lines.append(f"- {name}: {desc[:150]}" if desc else f"- {name}")
    return "\n".join(lines)


def _describe_limits(has_vision: bool, has_mic: bool) -> str:
    """The other half of self-knowledge: what is out of reach, and why.

    Derived from how the program is actually built, not from a list of refusals.
    A model that knows its boundaries stops improvising around them, and stating
    them as architecture rather than as rules keeps the answer honest in any
    language.
    """
    out = [
        "- Anything not listed above is outside your reach. Say so in one clause "
        "and offer the nearest thing you can actually do — never mime an action "
        "you cannot take, and never report a result you did not get.",
        "- You act on this machine only. You cannot reach the user's other "
        "devices, accounts or hardware except through the tools listed above.",
        "- You remember what is in the memory block and what has been said this "
        "session. Anything else you were told before is gone unless it was saved.",
    ]
    if has_vision:
        out.append(
            "- Your sight is not continuous. You see nothing until you call a "
            "vision tool, and then only that single frame at that moment — you "
            "cannot watch, monitor or notice something changing on screen.")
    else:
        out.append("- You have no sight at all in this build.")
    if has_mic:
        out.append(
            "- You hear nothing while the microphone is muted, and you cannot "
            "unmute it yourself.")
    return "\n".join(out)


def _boundary_rules() -> str:
    """The prompt half of the action policy, generated from the policy table.

    Written here rather than in prompt.txt so the categories the model is told
    to be careful about are literally the ones core/autonomy.py gates — a
    hand-written list drifts the first time one of the two is edited.
    """
    try:
        from core import autonomy
        lines = [
            "- Money: buying, paying, ordering, subscribing, transferring, "
            "anything with a price on it — the exact amount must be seen by them.",
            "- Something that cannot be undone: permanent deletion, emptying the "
            "recycle bin, formatting, wiping, overwriting.",
            "- The machine going down: restart, shut down, sign out, closing "
            "everything they have open.",
            "- Security and identity: passwords, two-factor codes, API keys, "
            "bank or card details, permissions, antivirus, firewall.",
            "- Running new software: installers, executing a downloaded file, "
            "editing the registry.",
            "- Reaching another person, when *you* thought of it rather than "
            "them: messages, emails, posts, comments, invites. When the user "
            "asks you to send something, send it — do not ask twice.",
            f"\n{autonomy.boundary_summary()}",
        ]
        return "\n".join(lines)
    except Exception:
        return ("- Money, permanent deletion, shutdown, security changes, "
                "installs, and messages you decided to send yourself.")


def _autonomy_rules() -> str:
    """What the model may do unasked, when autonomous mode is on.

    Read live, and it changes what the model is told *now* rather than at the
    next launch: with the lever off, the honest sentence is "you may suggest, "
    "you may not act", and saying that is cheaper than letting it discover the
    refusal by being blocked.
    """
    try:
        from core import autonomy
        on = autonomy.get_mode("autonomous")
        pc = autonomy.get_mode("pc_control")
    except Exception:
        return ""
    if not pc:
        return ("\n[AUTONOMY] PC control is switched OFF right now. You may look, "
                "read, answer and suggest — you may not move the mouse, click, or "
                "type on this machine. If a request needs that, say so in one "
                "clause and tell them the PC control switch is off.")
    if not on:
        return ("\n[AUTONOMY] Autonomous mode is OFF. Do exactly what is asked of "
                "you and nothing beyond it. You may still offer a next step — but "
                "only when you actually have one worth offering.")
    return ("\n[AUTONOMY] Autonomous mode is ON: the user has handed over the PC. "
            "While they are idle you may act on this machine yourself for "
            "anything harmless — open things, search, read, play music or a "
            "video, look around, organise, prepare something useful — and you do "
            "not ask permission for those. Stay light: one useful thing at a "
            "time, quiet when there is a real reason to be quiet, and idle when "
            "nothing is worth doing. The list above still waits for a human, "
            "always.")


def _initiative_note(event: str, weight: float = 1.0) -> None:
    """Tell the mood engine something happened — safe on a hot path.

    Called from the audio receive loop, so it is written to be free when
    nothing needs to happen: the import is inside the guard, the engine itself
    throttles repeats, and a failure is swallowed. A mood is not worth a stall
    in the loop that transcribes the user, hence no exception ever escapes.
    """
    try:
        from core import initiative
        initiative.note(event, weight)
    except Exception:
        pass


def _training_rules() -> str:
    """How the model may use its own practice drill.

    The tool is passive (find → point, never click) so it is safe for the
    assistant to request on its own — but the throttle, not the model, decides
    when, and this block says so plainly. The prompt and the tool's refusals are
    describing the same machine, so neither can promise what the other denies.
    """
    try:
        from core import self_training
        if not self_training.enabled():
            return ""
    except Exception:
        return ""
    return ("\n[SELF-TRAINING] You keep score of how well your own clicks, keys "
            "and screen reads land, and you practise your weakest skill while "
            "the user is quiet. You may call the training_run tool yourself with "
            "a 'target' to practise on — it is passive: it finds the element and "
            "moves the pointer onto it WITHOUT clicking. Use it sparingly: only "
            "when the user has not spoken for a while, never mid-task, and let "
            "its throttle refuse you when a round is not due — a refusal there "
            "is the schedule working, not an error to retry.")


def _render_prompt(template: str, values: dict) -> str:
    """Fill {tokens} in the prompt template.

    A plain replace rather than str.format: the file is meant to be edited by
    hand, and a stray brace in someone's own wording must never take the app
    down at startup.
    """
    out = template or ""
    for key, val in values.items():
        out = out.replace("{" + key + "}", str(val))
    return out


def _fix_schema_node(node, tool_name: str, path: str) -> None:
    """Repair one JSON-schema node in place. Never raises.

    The Live API closes the whole connection with a 1007 when a single tool
    declaration is malformed — one bad plugin then silences the entire
    assistant with no voice and no visible error. So every declaration is
    repaired here before it is sent: an ARRAY without `items` gets a STRING
    item type (the overwhelmingly common case), and anything unrepairable is
    reported so the caller can drop that property.
    """
    if not isinstance(node, dict):
        return
    try:
        if str(node.get("type", "")).strip().upper() == "ARRAY" and "items" not in node:
            node["items"] = {"type": "STRING"}
            print(f"[Tools] fixed {tool_name}: ARRAY '{path}' had no items "
                  f"-> assumed STRING items")
        sub = node.get("properties")
        if isinstance(sub, dict):
            for key, child in sub.items():
                _fix_schema_node(child, tool_name, f"{path}.{key}" if path else str(key))
        items = node.get("items")
        if isinstance(items, dict):
            _fix_schema_node(items, tool_name, path + "[]")
    except Exception:
        pass


def _sanitize_tool_declarations(declarations) -> list:
    """Return only the declarations safe to send to the Live API. Never raises.

    A declaration is dropped (with a console + UI log) only when its shape is
    broken beyond a local repair — a missing name, or parameters that are not
    an OBJECT. Everything else is repaired in place by _fix_schema_node.
    Without this, one malformed plugin schema fails the entire session setup
    and the app sits at a fake LISTENING state that can never speak.
    """
    out = []
    for d in declarations or ():
        try:
            name = d.get("name") if isinstance(d, dict) else getattr(d, "name", None)
            if not name:
                print("[Tools] dropped a declaration with no name")
                continue
            params = d.get("parameters") if isinstance(d, dict) else getattr(d, "parameters", None)
            if not isinstance(params, dict) or params.get("type") != "OBJECT":
                print(f"[Tools] dropped '{name}': parameters must be "
                      f"an OBJECT schema")
                continue
            if not isinstance(params.get("properties"), dict):
                params["properties"] = {}
            _fix_schema_node(params, str(name), "")
            out.append(d)
        except Exception as e:
            try:
                print(f"[Tools] dropped a declaration (sanitizer error: {e})")
            except Exception:
                pass
            continue
    return out


def _get_api_key() -> str:
    """Never raises — a missing file/key returns "" so startup shows a useful
    status instead of crashing the session loop."""
    try:
        with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
            return str(json.load(f).get("gemini_api_key") or "").strip()
    except Exception:
        return ""


def _load_system_prompt() -> str:
    try:
        return PROMPT_PATH.read_text(encoding="utf-8")
    except Exception:
        return (
            "You are JARVIS, Tony Stark's AI assistant. "
            "Be concise, direct, and always use the provided tools to complete tasks. "
            "Never simulate or guess results — always call the appropriate tool."
        )

_CTRL_RE = re.compile(r"<ctrl\d+>", re.IGNORECASE)

# Transcript chunks shorter than this may legitimately repeat ("evet, evet"),
# so only longer ones are treated as duplicates.
_REPEAT_MIN = 12


def _is_repeat_chunk(txt: str, buf: list) -> bool:
    """True if this transcript chunk has already been seen this turn.

    Guards against the API re-sending the tail of a response across the several
    turn_completes a tool-using turn produces.
    """
    if len(txt) < _REPEAT_MIN:
        return bool(buf) and txt == buf[-1]
    joined = " ".join(buf)
    return txt in joined

def _clean_transcript(text: str) -> str:    
    text = _CTRL_RE.sub("", text)
    text = re.sub(r"[\x00-\x08\x0b-\x1f]", "", text)
    return text.strip()

TOOL_DECLARATIONS = [
    # ── Inline tools ─────────────────────────────────────────────────────────
    # These stay here (rather than in an actions/*.py TOOL dict) because their
    # handling is woven into live-session state — vision capture/injection,
    # camera stream, memory writes, the monitor engine, and shutdown. All other
    # tools live in their own action file and are auto-discovered by
    # core.action_loader (see JarvisLive.__init__).
    {
        "name": "system_status",
        "description": (
            "Returns real-time system metrics: CPU usage, RAM, GPU load, CPU temperature, "
            "uptime, and process count. Use when the user asks about computer performance, "
            "temperature, memory, or resource usage."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {},
        }
    },
    {
        "name": "screen_process",
        "description": (
            "Captures the screen or webcam image and lets you analyze it. "
            "MUST be called when user asks what is on screen, what you see, "
            "look at camera, analyze my screen, etc. "
            "You have NO visual ability without this tool. "
            "After the image is captured it is sent directly to you — describe what you see and answer the user's question. "
            "When using camera: the live view stays open until user says close it or calls close_camera."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "angle": {"type": "STRING", "description": "'screen' to capture display, 'camera' for webcam. Default: 'screen'"},
                "text":  {"type": "STRING", "description": "The question or instruction about the captured image"}
            },
            "required": ["text"]
        }
    },
    {
        "name": "close_camera",
        "description": (
            "Closes the live camera view shown on screen. "
            "Call when the user says (in ANY language): close camera, stop camera, "
            "turn off camera, that's creepy, etc."
        ),
        "parameters": {"type": "OBJECT", "properties": {}, "required": []}
    },
    {
        "name": "manage_monitor",
        "description": (
            "Add, remove, or list background monitoring topics. "
            "JARVIS checks these topics once a day and alerts the user when there is a new development. "
            "Use 'add' when the user says 'monitor X', 'track X', 'follow X'. "
            "Use 'remove' when the user says 'stop monitoring X'. "
            "Use 'list' when the user asks what is being monitored. "
            "Do NOT add crypto, financial, or trading topics."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type":        "STRING",
                    "description": "add | remove | list",
                },
                "topic": {
                    "type":        "STRING",
                    "description": "Topic to monitor or stop monitoring (e.g. 'space exploration', 'AI news')",
                },
            },
            "required": ["action"],
        },
    },
    {
        "name": "shutdown_jarvis",
        "description": (
            "Shuts down the assistant completely. "
            "Call this when the user expresses intent to end the conversation, "
            "close the assistant, say goodbye, or stop Jarvis. "
            "The user can say this in ANY language."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {},
        }
    },
    {
        "name": "save_memory",
        "description": (
            "Save an important personal fact about the user to long-term memory. "
            "Call this silently whenever the user reveals something worth remembering: "
            "name, age, city, job, preferences, hobbies, relationships, projects, or future plans. "
            "Do NOT call for: weather, reminders, searches, or one-time commands. "
            "Do NOT announce that you are saving — just call it silently. "
            "Values must be in English regardless of the conversation language."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "category": {
                    "type": "STRING",
                    "description": (
                        "identity — name, age, birthday, city, job, language, nationality | "
                        "preferences — favorite food/color/music/film/game/sport, hobbies | "
                        "projects — active projects, goals, things being built | "
                        "relationships — friends, family, partner, colleagues | "
                        "wishes — future plans, things to buy, travel dreams | "
                        "notes — habits, schedule, anything else worth remembering"
                    )
                },
                "key":   {"type": "STRING", "description": "Short snake_case key (e.g. name, favorite_food, sister_name)"},
                "value": {"type": "STRING", "description": "Concise value in English (e.g. Fatih, pizza, older sister)"},
            },
            "required": ["category", "key", "value"]
        }
    },
    {
        "name": "recall_memory",
        "description": (
            "Look up a fact you have stored about the user but which is NOT in "
            "the memory block of your system prompt. "
            "The prompt lists the keys it did not have room for under "
            "'[ALSO REMEMBERED]' — if the user asks about anything named there, "
            "call this FIRST. "
            "Also call it before saying you do not know something personal, and "
            "when the user asks what you remember about them (leave query empty "
            "for everything). "
            "This is a local file search: it is instant and costs nothing."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {
                    "type": "STRING",
                    "description": (
                        "Keyword to search for — a name, a topic, a category "
                        "(e.g. 'ayse', 'coffee', 'projects'). "
                        "Leave empty to list everything stored."
                    ),
                },
            },
            "required": [],
        },
    },
    {
        "name": "undo",
        "description": (
            "Reverse the last change YOU made to this computer — a file you "
            "moved, renamed, created or wrote, or a setting you changed such as "
            "volume, brightness, dark mode or WiFi. "
            "Call this whenever the user says undo, revert, take it back, put it "
            "back, cancel that, or tells you that you did the wrong thing, in ANY "
            "language. "
            "Use action='list' when they ask what can be undone. "
            "This only covers your own actions — it is not the Ctrl+Z of whatever "
            "application is on screen (that is computer_settings with action 'undo')."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": "undo (default) — reverse the last change | list — show what can be undone",
                },
            },
            "required": [],
        },
    },
]

class _ReconnectSignal(Exception):
    """Raised inside the session TaskGroup to force a clean, voluntary reconnect
    (e.g. the user picked a new voice — the voice is fixed at connect time, so
    the session must be rebuilt).

    Carries `keep_context`: True for an ordinary rebuild, where the stored
    resumption handle is replayed and the conversation continues; False when the
    new session must genuinely start clean (see the voice-change note in
    _on_voice_change)."""

    def __init__(self, keep_context: bool = True):
        super().__init__()
        self.keep_context = keep_context


def _is_reconnect_signal(exc: BaseException) -> bool:
    """True if `exc` is a _ReconnectSignal, or a(n) (Base)ExceptionGroup that
    wraps one — TaskGroup bundles child exceptions into a group."""
    if isinstance(exc, _ReconnectSignal):
        return True
    if isinstance(exc, BaseExceptionGroup):
        return any(_is_reconnect_signal(sub) for sub in exc.exceptions)
    return False


def _keep_context_of(exc: BaseException) -> bool:
    """Read `keep_context` off a reconnect signal, unwrapping the group the
    TaskGroup put it in. Defaults to True: an unexpected shape must not silently
    wipe the conversation."""
    if isinstance(exc, _ReconnectSignal):
        return getattr(exc, "keep_context", True)
    if isinstance(exc, BaseExceptionGroup):
        for sub in exc.exceptions:
            if _is_reconnect_signal(sub):
                return _keep_context_of(sub)
    return True


def _human_narration(name: str, args: dict) -> str:
    """A short, human phrasing of a tool call for the on-screen log.

    The spoken narration lives in the system prompt (narration_hint); this is
    only the HUD's one-line mirror so the user can see what JARVIS is doing even
    when it ends up staying quiet. Unknown tools fall through to the plain name
    already printed in the terminal."""
    a = args or {}
    act = (a.get("action") or "").lower().strip().replace("_", "").replace(" ", "")
    _direction = (a.get("direction") or "").strip().lower()
    _snap_phrase = {
        "left": "snipping the active window to the left half",
        "right": "snipping the active window to the right half",
        "maximize": "maximising the active window",
        "minimize": "minimising the active window",
        "restore": "restoring the active window",
    }.get(_direction) or "snapping the active window"
    targets = {
        "listwindows":       "taking a look at what windows are open",
        "listprocesses":     "checking what is running",
        "findprocess":       f"hunting for process '{a.get('name') or a.get('query') or ''}'",
        "killprocess":       f"stopping process '{a.get('name') or a.get('query') or a.get('pid') or ''}'",
        "startprocess":      f"starting '{a.get('target') or a.get('name') or ''}'",
        "focuswindow":       f"bringing '{a.get('title') or a.get('target') or ''}' to the front",
        "closewindow":       "closing the active window",
        "switchwindow":      "switching to the next window",
        "showdesktop":       "clearing the desktop",
        "snapwindow":        _snap_phrase,
        "systemuptime":      "checking how long the machine has been up",
    }
    if act in targets:
        return targets[act]
    named = {
        "open_app":       f"opening {a.get('app_name', '')}" if a.get("app_name") else "opening an app",
        "web_search":     f"looking up \"{a.get('query', '')}\"" if a.get("query") else "searching the web",
        "file_processor": f"working on {a.get('file_path', '')}" if a.get("file_path") else "processing a file",
        "recall_memory":  "recalling what I know",
        "screen_process": "taking a look at the screen",
        "computer_settings": f"running {a.get('action', 'a computer command')}",
    }
    if name in named and named[name]:
        return named[name]
    return ""


class JarvisLive:
    def __init__(self, ui: JarvisUI):
        self.ui             = ui
        self._asst_name     = "JARVIS"       # updated each session from config
        self.session              = None
        self.audio_in_queue       = None
        self.out_queue            = None
        self._loop                     = None
        self._is_speaking         = False
        self._speaking_lock       = threading.Lock()
        # Local end-of-speech detection for the live session. gemini-3.1-flash
        # keeps an open mic segment until it sees audio_stream_end — without it
        # Gemini transcribes the user and then waits forever, so the assistant
        # looks 'listening' but never replies. These counters turn the mic
        # stream into discrete turns: after ~0.45s of silence following speech,
        # the turn is closed and the model starts answering.
        self._user_spoke          = False
        self._user_silent_blocks  = 0
        self._phone_active        = False   # True while phone mic is streaming; pauses PC mic
        self._pending_vision       = None    # (img_bytes, mime_type, question, angle) to inject after tool response
        self._vision_cam_active    = False   # True if camera was opened for vision → auto-close after response
        self._vision_close_pending = False   # True after vision injected; next turn_complete closes camera
        self._vision_last_time     = 0.0     # monotonic time of last screen_process call (cooldown guard)
        self._vision_busy          = False   # True while a vision capture/inject cycle is in flight
        # Always-on glance state: the loop keeps the newest frame + caption so a
        # check-in (or any screen question) reflects right-now, not a lazy snap.
        self._glance_frame         = None    # (img_bytes, mime_type, caption, monotonic_ts)
        self._glance_caption_ts    = 0.0     # when the caption was last refreshed (to throttle FAST calls)
        self._glance_attached_ts   = 0.0     # last time a frame was auto-attached to a user turn
        self._glance_hash          = ""      # fingerprint of the last stored frame (change detection)
        # Short-term visual memory: a bounded RAM trail of recent screen states
        # (fingerprint, caption, app, change regions) so conversation and
        # proactive check-ins see a CONTINUOUS scene, not unrelated snapshots.
        self._visual_memory        = VisualMemory(max_entries=24)
        self._interrupted          = False   # True while draining audio after user interrupt
        # Transcript-driven mouth shapes for the avatar. Fed from the receive
        # loop as words arrive, drained by the playback loop against the audio.
        self._visemes              = VisemeStream()
        self._last_out_logged      = ""      # de-dupes a re-sent transcript tail
        # Push-to-talk
        self._ptt_enabled          = False
        self._ptt_held             = False
        self._ptt                  = None    # core.hotkey.PushToTalk
        self._out_level            = 0.0     # level of the audio being played right now
        self._echo                 = EchoGuard()
        # `stream.write()` returns when the buffer accepts the audio, not when the
        # speaker has finished with it, so sound is still in the room after the
        # speaking flag drops. Streaming the microphone during that gap is how an
        # assistant ends up answering itself. Measured from the device rather than
        # guessed; see _play_audio.
        self._out_latency          = 0.20    # seconds, replaced with the real value
        self._tail_until           = 0.0     # monotonic time the echo tail expires
        # Wall-clock time at which the audio written next will begin to sound.
        # The mouth is scheduled against this, never against "now": batches are
        # handed to the device far faster than they play, so "now" ran the lips
        # ahead of the words and cut every schedule short. 0 = nothing playing.
        self._play_cursor          = 0.0
        self.ui.on_push_to_talk   = self.set_push_to_talk
        self.ui.ptt_hold          = self._on_ptt
        self.ui.on_text_command   = self._on_text_command
        self.ui.on_remote_clicked = self._make_remote_key
        self.ui.on_interrupt      = self.interrupt
        self.ui.on_voice_change   = self._on_voice_change     # voice picker → rebuild session
        self.ui.on_audio_device_change = self._on_audio_device_change
        self._reconnect_event: asyncio.Event | None = None
        self._reconnect_keep = True   # False → next rebuild drops the resumption handle

        # ── Session resumption ─────────────────────────────────────────
        # The server issues a resumption handle every few seconds and reissues
        # it as the conversation moves on. Before this, session_resumption was
        # switched ON in the config and the update was never read, so the handle
        # was thrown away and EVERY reconnect — a dropped packet, a voice change,
        # switching microphone — started an empty session. "Unlimited sessions"
        # leaked through exactly this hole.
        #
        # Deliberately in RAM only, never written to disk. Persisting it would
        # make a fresh launch continue yesterday's conversation, which sounds
        # appealing but breaks the session-summary flow: _save_session_summary
        # runs at shutdown and the morning briefing pops it the next day. A
        # conversation that never ends never produces a summary, and the
        # "yesterday we talked about…" line silently disappears.
        self._resume_handle: str | None = None
        self._turn_done_event: asyncio.Event | None = None
        self._dashboard     = None
        self._briefing_sent    = False          # morning briefing fires once per process
        self._sys_monitor      = SystemMonitor()  # persistent cooldown state
        self._proactive        = ProactiveEngine()
        # Autonomous initiative governor: when the user hands over the PC, this
        # decides when a contribution is welcome at all. It holds no thread and
        # does no polling of its own — the existing proactive loop asks it, so
        # autonomy costs one branch, not another background worker.
        try:
            from core import autonomy as _autonomy_mod
            self._autonomy = _autonomy_mod
        except Exception:
            self._autonomy = None
        self._idle_governor = (
            self._autonomy.IdleGovernor() if self._autonomy else None)
        self._screen_observer  = None   # core.screen_observer.ScreenObserver (started in run())
        self._last_user_speech = time.monotonic()  # updated on every user utterance
        self._session_log: list[str] = []          # conversation turns for end-of-session summary
        self._learned_marker       = 0              # how much of _session_log learning has consumed
        self._active_mic           = ""             # what the capture loop actually opened (diagnostics)
        self._last_learn_ts        = 0.0            # first monotonic time of the last actual model-learn
        self._last_usage_flush     = time.monotonic()  # usage counter persisted on a schedule
        self._recent_failures = collections.deque(maxlen=8)  # (tool, snippet) of last failures

        self._enhanced_live = True  # proactive audio; auto-disabled if the server rejects it
        self._tuned_live    = True  # turn-taking / media / thinking knobs; same fallback

        # get_base_dir() (not __file__) so the frozen exe scans the actions/
        # and plugins/ folders staged NEXT TO the exe. __file__ inside a
        # PyInstaller bundle resolves under _internal/, where those folders do
        # not exist — discovery would find zero tools and mkdir junk there.
        _base_dir = get_base_dir()
        _inline_names = {t["name"] for t in TOOL_DECLARATIONS}

        # File-backed tools: every actions/*.py with a TOOL dict, discovered the
        # same way plugins are. Reserved names = the inline tools above, so an
        # action can never shadow one.
        self._action_registry = discover_actions(
            actions_dir=_base_dir / "actions",
            reserved_names=_inline_names,
            logger=lambda msg: print(f"[Actions] {msg}"),
        )

        # Plugins must not collide with either an inline tool or a discovered action.
        _core_names = _inline_names | self._action_registry.names()
        self._plugin_registry = discover_plugins(
            plugins_dir=_base_dir / "plugins",
            core_tool_names=_core_names,
            # Console gets the full boot transcript; the activity log gets only
            # what the user has to know about. Every plugin loading correctly is
            # the expected case and does not belong in their conversation.
            logger=lambda msg: print(f"[Plugins] {msg}"),
            notify=lambda msg: self.ui.write_log(f"SYS: {msg}"),
        )
        self.ui.get_plugins = self._plugin_registry.list_for_ui
        self.ui.get_plugin_settings = self._plugin_registry.settings_schemas  # ⚙ settings tab
        self.ui.request_say = self.plugin_say   # plugins: mid-task speech channel

        # ── Wake word ────────────────────────────────────────────────────────
        # _awake gates the mic (see _listen_audio) and the background speakers.
        # It is True whenever wake word is OFF, so default behaviour is unchanged.
        self._wake_enabled     = get_wake_word_enabled()
        self._awake            = not self._wake_enabled
        self._wake_detector: WakeWordDetector | None = None
        self._wake_sleep_timeout = WAKE_SLEEP_TIMEOUT

        # Restore the saved push-to-talk preference. Doing it here rather than
        # in __init__ means the hotkey thread only exists once there is a
        # session to talk to.
        if get_push_to_talk_enabled():
            try:
                self.set_push_to_talk(True)
            except Exception as e:
                print(f"[JARVIS] ⚠ Push-to-talk unavailable: {e}")
        # UI control surface for the Wake Word settings section.
        self.ui.wake_is_ready    = wake_is_ready          # () -> bool
        self.ui.wake_get_state   = self._wake_state       # () -> dict
        self.ui.on_wake_toggle   = self._ui_wake_toggle   # (enable: bool) -> str
        self.ui.on_wake_manual   = self._ui_wake_manual   # () -> toggle awake/asleep
        self.ui.on_wake_install  = self._ui_wake_install  # () -> (ok, msg)

    # ── Wake word: state machine ─────────────────────────────────────────────

    def _wake_state(self) -> dict:
        # A loaded, running detector is definitively ready; otherwise fall back
        # to the cheap on-disk model-file check (no Model construction).
        ready = bool(self._wake_detector and self._wake_detector.ready) or wake_is_ready()
        return {"enabled": self._wake_enabled, "awake": self._awake, "ready": ready}

    def _ensure_wake_detector(self) -> bool:
        """Load the detector once (model loads on first start). Idempotent."""
        if self._wake_detector is None:
            self._wake_detector = WakeWordDetector(
                on_detect=self._on_wake_detected,
                logger=lambda m: print(f"[Wake] {m}"),
                notify=lambda m: self.ui.write_log(f"SYS: {m}"),
            )
        if not self._wake_detector.ready:
            return self._wake_detector.start()
        return True

    def _on_wake_detected(self) -> None:
        """Called from the detector thread when 'Hey Jarvis' is heard."""
        self.wake(reason="wake word")

    def wake(self, reason: str = "wake word") -> None:
        if self._awake:
            return
        self._awake = True
        self._last_user_speech = time.monotonic()   # start the auto-sleep clock now
        if not self.ui.muted:
            self.ui.set_state("LISTENING")
        self.ui.write_log(f"SYS: Awake — {reason}.")

    def sleep(self, reason: str = "timeout") -> None:
        if not self._awake:
            return
        self._awake = False
        self.set_speaking(False)
        self.ui.set_state("SLEEPING")
        self.ui.write_log(f"SYS: Sleeping — {reason}. Say 'Hey Jarvis' to wake me.")

    async def _run_sleep_watch(self) -> None:
        """Auto-sleep after the configured silence window (wake-word mode only)."""
        while True:
            await asyncio.sleep(5)
            if not self._wake_enabled or not self._awake:
                continue
            with self._speaking_lock:
                speaking = self._is_speaking
            if speaking:
                continue
            if (time.monotonic() - self._last_user_speech) > self._wake_sleep_timeout:
                self.sleep(reason="no speech for 2 minutes")

    # ── Wake word: UI callbacks (called from the Qt thread) ──────────────────

    def _ui_wake_toggle(self, enable: bool) -> str:
        """Enable/disable wake word from the settings UI. Returns a status token:
        'enabled' | 'disabled' | 'need_download'."""
        if enable:
            if not wake_is_ready():
                return "need_download"
            self._wake_enabled = True
            save_wake_word_enabled(True)
            self._ensure_wake_detector()
            self.sleep(reason="wake word enabled")
            return "enabled"
        else:
            self._wake_enabled = False
            save_wake_word_enabled(False)
            self.wake(reason="wake word disabled")
            return "disabled"

    def _ui_wake_manual(self) -> None:
        """Manual sleep/wake button in the UI."""
        if not self._wake_enabled:
            return
        if self._awake:
            self.sleep(reason="you tapped sleep")
        else:
            self.wake(reason="you tapped wake")

    def _ui_wake_install(self) -> tuple[bool, str]:
        """Download openwakeword + the model (runs in a UI worker thread)."""
        # Triggered by the user pressing the button, so its progress is exactly
        # what they are waiting to see.
        return wake_install(logger=lambda m: print(f"[Wake] {m}"),
                            notify=lambda m: self.ui.write_log(f"SYS: {m}"))

    def plugin_say(self, instruction: str) -> None:
        """
        Thread-safe speech channel for plugins: lets a plugin ask JARVIS to
        say something short WHILE its run() is still executing (plugins block
        their executor thread, so they can't speak through the tool response
        until they finish). The instruction is injected into the Live session
        exactly like a proactive check-in; Gemini phrases it naturally in the
        user's language. Silently a no-op when no session is connected.
        """
        loop = getattr(self, "_loop", None)
        if not loop or not self.session:
            return

        async def _say():
            try:
                await self.session.send_client_content(
                    turns={"role": "user", "parts": [{"text": instruction}]},
                    turn_complete=True,
                )
            except Exception as e:
                print(f"[PluginSay] {e}")

        try:
            asyncio.run_coroutine_threadsafe(_say(), loop)
        except Exception as e:
            print(f"[PluginSay] {e}")

    def request_reconnect(self, keep_context: bool = True, reason: str = ""):
        """Thread-safe: ask the run loop to tear down and rebuild the Live
        session. Called from the Qt thread. No-op until the async loop and
        reconnect event exist.

        `keep_context=False` drops the resumption handle so the new session
        starts empty — only for changes the server cannot apply to a resumed
        session."""
        loop = getattr(self, "_loop", None)
        ev   = self._reconnect_event
        self._reconnect_keep   = keep_context
        self._reconnect_reason = reason
        if loop and ev is not None:
            loop.call_soon_threadsafe(ev.set)

    def _on_voice_change(self):
        """Voice picker applied.

        The voice is baked into the session at connect time, so a rebuild is
        required. It is rebuilt WITHOUT the resumption handle on purpose:
        resuming restores the server's own session state, and the safe reading
        is that it restores the voice with it — which would make the picker
        appear to do nothing. Losing context here is acceptable because changing
        voice is a deliberate, rare act; losing it on a dropped packet was not."""
        self.request_reconnect(keep_context=False, reason="new voice")

    def _on_audio_device_change(self):
        """Microphone or speaker changed. Both streams are opened inside the
        session TaskGroup, so they can only be re-opened by rebuilding it —
        but the conversation is kept, which is the whole reason resumption
        landed before this feature did."""
        try:
            from core import audio_devices as _ad
            _ad.clear_rate_cache()      # old endpoints' rates mean nothing now
        except Exception:
            pass
        self.request_reconnect(keep_context=True, reason="audio device")

    def active_mic_name(self) -> str:
        """The microphone the capture loop is actually listening to, for the
        Settings page — the saved setting and the truth can differ when the
        saved device was unavailable and the default took over."""
        return getattr(self, "_active_mic", "") or (get_input_device() or "")

    async def _watch_reconnect(self):
        """Session-scoped task: when a voluntary reconnect is requested, raise a
        signal that unwinds the TaskGroup so the run loop rebuilds the session."""
        assert self._reconnect_event is not None
        await self._reconnect_event.wait()
        self._reconnect_event.clear()
        keep   = self._reconnect_keep
        reason = getattr(self, "_reconnect_reason", "") or "settings"
        self.ui.write_log(
            f"SYS: Applying {reason} — reconnecting"
            + ("..." if keep else " (starting a fresh conversation)...")
        )
        raise _ReconnectSignal(keep_context=keep)

    def _make_remote_key(self):
        """Called from Qt main thread when user presses Remote Control."""
        if self._dashboard is None:
            self.ui.write_log(
                "SYS: Dashboard unavailable. "
                "Run: pip install fastapi \"uvicorn[standard]\" cryptography"
            )
            return None
        key    = self._dashboard.new_key()
        url    = self._dashboard.get_url()
        manual = self._dashboard.get_manual_url()
        return url, key, f"{url}/auto-login?key={key}", manual

    def _on_text_command(self, text: str):
        if not self._loop or not self.session:
            return
        # Respect wake-word sleep: a typed command must not be answered while
        # asleep either (the sleep gate is not just for the mic). Wake first with
        # "Hey Jarvis" or the WAKE NOW button.
        if self._wake_enabled and not self._awake:
            self.ui.write_log("SYS: I'm asleep — say 'Hey Jarvis' or tap WAKE NOW first.")
            return
        _reason = ""
        try:
            if reasoner_mod.classify(text)["hard"]:
                _reason = ("[REASON] Work through this internally, then answer "
                           "directly and briefly — the final reply is what matters.")
        except Exception:
            pass
        _tagged = mood_tag(text)
        # The emotion engine sets HOW to sound for this reply (register, warmth,
        # pace) — the companion layer's per-message nudge. Empty on neutral.
        try:
            _edeliver = _emotion_delivery(text)
        except Exception:
            _edeliver = ""
        _lang = language_directive(text)
        _pfx = " ".join(x for x in (_reason, _tagged, _edeliver, _lang) if x)
        _state = self._realtime_context()

        async def _send_with_vision():
            # Eyes first: if the always-on loop has a fresh frame, slip it into
            # the session just before the command so the answer is about what the
            # user can see *right now*, without being asked to look. Throttled.
            await self._maybe_attach_fresh_glance()
            body = text
            if _pfx:
                body = f"{_pfx}\n{body}"
            if _state:
                body = f"{_state}\n\n{body}"
            await self.session.send_client_content(
                turns={"role": "user", "parts": [{"text": body}]},
                turn_complete=True
            )

        asyncio.run_coroutine_threadsafe(_send_with_vision(), self._loop)

    def _realtime_context(self) -> str:
        """A short, local, zero-model-call snapshot of what JARVIS can know is
        happening *right now* — screen metadata, the last vision glance, recent
        activity and its own recent failures — attached to the next reply so it
        is grounded in the actual moment, not just the typed words. Gated by the
        same privacy levers that own the observer; empty when nothing is known
        or allowed. Never mentions itself."""
        lines: list[str] = []
        try:
            if get_screen_awareness() and self._screen_observer is not None:
                bits = self._screen_observer.humanize()
                if bits:
                    lines.append(f"- Machine: {bits[:140]}")
                if get_track_activity():
                    tl = self._screen_observer.activity_timeline() or []
                    if tl:
                        lines.append("- Recently: " + " → ".join(str(x) for x in tl[:4])[:160])
        except Exception:
            pass
        try:
            if (get_screen_awareness() and get_screen_glance()
                    and self._glance_frame
                    and self._glance_frame[2]
                    and time.monotonic() - self._glance_frame[3] <= 25.0):
                lines.append(f"- Screen: {(self._glance_frame[2] or '')[:120]}")
                # Short-term visual memory: what the screen has been showing
                # and how it got here, so replies build on the scene instead of
                # treating each frame as unrelated.
                vm = self._visual_memory.summarize(limit=3)
                if vm:
                    for ln in vm.splitlines()[:5]:
                        lines.append(f"- {ln[:150]}")
        except Exception:
            pass
        try:
            if self._recent_failures:
                lines.append("- JARVIS just hit: " + "; ".join(
                    f"{n}: {str(t)[:60]}" for n, t in list(self._recent_failures)[-2:]))
        except Exception:
            pass
        if not lines:
            return ""
        return ("[NOW — local context, seconds fresh] Treat this as extra "
                "context and use it to answer precisely; you never mention "
                "that this note is attached.\n" + "\n".join(lines))

    async def _maybe_attach_fresh_glance(self) -> bool:
        """Attach the newest glance frame (if fresh enough) as a silent context
        turn. Returns True when one was attached. No turn_complete: the frame
        is context only, never an extra spoken answer."""
        if not self.session or not self._awake:
            return False
        if not (get_screen_awareness() and get_screen_glance()):
            return False
        if not self._glance_frame:
            return False
        data, mime, _cap, ts = self._glance_frame
        if time.monotonic() - ts > 25.0:          # too stale to bother
            return False
        if time.monotonic() - self._glance_attached_ts < 40.0:
            return False                           # throttle: eyes every ~40 s max
        self._glance_attached_ts = time.monotonic()
        import base64 as _b64
        print(f"[Glance] hence attaching fresh frame to the turn "
              f"({len(data):,} bytes)")
        await self.session.send_client_content(
            turns={"role": "user", "parts": [
                {"inline_data": {
                    "mime_type": mime,
                    "data": _b64.b64encode(data).decode("ascii"),
                }},
                {"text": ("[IMAGE SOURCE: SCREEN CAPTURE]\nFresh look at the "
                          "screen just before this message. Note anything that "
                          "matters, then answer.")},
            ]},
            turn_complete=False,
        )
        return True

    def _tail_active(self) -> bool:
        """True while the speakers may still be finishing our last sentence."""
        return time.monotonic() < self._tail_until

    def set_speaking(self, value: bool):
        with self._speaking_lock:
            self._is_speaking = value
        if value:
            self._tail_until = 0.0
        else:
            # Hold the guard open across the device's own output latency plus a
            # margin for the room. The microphone is NOT muted during it — the
            # guard still lets a genuine reply through, so answering instantly
            # still works. Only our own echo is dropped.
            self._tail_until = time.monotonic() + self._out_latency + _TAIL_MARGIN
        if not value:
            # The echo history is deliberately NOT cleared here: the tail above
            # still needs it to recognise our own voice. It is dropped when the
            # tail expires. What the guard learned about the room always stays.
            self._out_level = 0.0
        if value:
            self.ui.set_state("SPEAKING")
        elif not self.ui.muted:
            self.ui.set_state("LISTENING")

    def set_push_to_talk(self, enabled: bool) -> str:
        """Turn hold-to-talk on or off. Returns the scope actually achieved."""
        from core.hotkey import PushToTalk

        self._ptt_enabled = bool(enabled)
        self._ptt_held = False
        if not enabled:
            if self._ptt is not None:
                self._ptt.stop()
                self._ptt = None
            return "off"

        if self._ptt is None:
            self._ptt = PushToTalk(self._on_ptt)
        scope = self._ptt.start()
        # A window-scoped chord is a real limitation, not a detail — say it once
        # in the log so nobody wonders why it does nothing while another app is
        # focused. Reporting it must never be able to undo the thing it reports.
        try:
            self.ui.write_log(
                f"SYS: Push-to-talk on — hold {self._ptt.label}"
                + ("." if scope == "global"
                   else " (works while this window is focused)."))
        except Exception:
            pass
        return scope

    def _on_ptt(self, held: bool) -> None:
        """Chord pressed or released — may arrive on the hotkey thread."""
        self._ptt_held = held
        if held:
            # Holding the key is also a way to wake it, so push-to-talk works
            # without having to say the wake word first.
            if self._wake_enabled and not self._awake:
                self._awake = True
                self._last_user_speech = time.monotonic()
        try:
            self.ui.set_state("LISTENING" if held else "SLEEPING")
        except Exception:
            pass

    def interrupt(self) -> None:
        """Stop JARVIS mid-speech: drain queued audio and open mic immediately.

        Also raises the PC-control stop token, so a voice/UI interrupt halts
        any multi-step automation (goal loop) instead of only muting speech.
        A no-op when nothing cancellable is running — begin() clears it.
        """
        self._interrupted = True
        try:
            from core import cancel as _cancel
            _cancel.request()
        except Exception:
            pass
        q = self.audio_in_queue
        if q:
            drained = 0
            while True:
                try:
                    q.get_nowait()
                    drained += 1
                except Exception:
                    break
            if drained:
                print(f"[JARVIS] ✋ Interrupted — {drained} audio chunks discarded")
        self.set_speaking(False)
        # The words we were about to mouth are never going to be spoken now.
        self._visemes.reset()
        self._play_cursor = 0.0     # next batch starts a fresh timeline
        if self._turn_done_event:
            self._turn_done_event.clear()
        self.ui.write_log("SYS: Interrupted — listening...")

    def speak(self, text: str):
        if not self._loop or not self.session:
            return
        asyncio.run_coroutine_threadsafe(
            self.session.send_client_content(
                turns={"role": "user", "parts": [{"text": text}]},
                turn_complete=True
            ),
            self._loop
        )

    def speak_error(self, tool_name: str, error: str):
        short = str(error)[:120]
        self.ui.write_log(f"ERR: {tool_name} — {short}")
        self.speak(f"Sir, {tool_name} encountered an error. {short}")

    def _build_config(self) -> types.LiveConnectConfig:
        from datetime import datetime

        # Load customization from config
        try:
            _cfg = json.loads(open(API_CONFIG_PATH, encoding="utf-8").read())
            self._asst_name = (_cfg.get("assistant_name") or "JARVIS").strip()
            _user_name = (_cfg.get("user_name") or "").strip()
        except Exception:
            self._asst_name = "JARVIS"
            _user_name = ""

        memory     = load_memory()
        mem_str    = format_memory_for_prompt(memory)
        sys_prompt = _load_system_prompt()

        now      = datetime.now()
        time_str = now.strftime("%A, %B %d, %Y — %I:%M %p")
        time_ctx = (
            f"[CURRENT DATE & TIME]\n"
            f"Right now it is: {time_str}\n"
            f"Use this to calculate exact times for reminders.\n\n"
        )

        # Identity injection — overrides any hardcoded name in prompt.txt
        # Address form is a property of the language being spoken, so it is
        # stated as a principle rather than a two-language lookup — the model
        # already knows the respectful register of whatever language it is in.
        _addr = (f"ADDRESS: Always call the user '{_user_name}'."
                 if _user_name
                 else 'ADDRESS: Address the user with the ordinary respectful form '
                      'for a superior in the language you are currently speaking — '
                      '"sir" in English, its everyday equivalent in any other '
                      'language. Never an archaic or aristocratic form, and never '
                      'the form from a different language than the one you are '
                      'speaking in this sentence.')
        identity_ctx = (
            f"[IDENTITY]\n"
            f"Your name is {self._asst_name}. "
            f"Always refer to yourself as {self._asst_name}.\n"
            f"{_addr}\n\n"
        )

        # Everything the model is told about *itself* is derived here, not
        # written into prompt.txt: the name comes from config, the platform from
        # the host, the capability list from the registries that were just
        # discovered. Rename the assistant, add a plugin or move to another OS
        # and this follows without anyone editing a prompt.
        _all_decls = _sanitize_tool_declarations(
            TOOL_DECLARATIONS
            + self._action_registry.get_tool_declarations()
            + self._plugin_registry.get_tool_declarations())
        _names = {(d.get("name") if isinstance(d, dict) else getattr(d, "name", ""))
                  for d in _all_decls}
        sys_prompt = _render_prompt(sys_prompt, {
            "assistant_name": self._asst_name,
            "platform": f"{_platform.system()} {_platform.release()}".strip(),
            "capabilities": _describe_tools(_all_decls),
            "limits": _describe_limits(
                has_vision="screen_process" in _names,
                has_mic=True,
            ),
            # Ambient participation ("fill the room") — gated by config so the
            # default session is a strict one-on-one and only an explicit switch
            # lets JARVIS contribute to conversation it overhears, under cap.
            "ambient_rules": self._ambient_rules(),
            # Voice: humour/emotion level and "talk through the work" narration,
            # both config-gated (see memory/config_manager.py) and both prompt-
            # level so they cost no extra calls.
            "personality_rules":  personality_hint(),
            "action_style_rules": narration_hint(),
            "emotion_rules":      emotion_rules(),
            # Computer-use boundaries and autonomous behaviour, both derived from
            # core/autonomy.py so the words the model is given and the policy the
            # tools actually enforce can never drift apart.
            "boundary_rules": _boundary_rules(),
            "autonomy_rules": _autonomy_rules(),
            "training_rules": _training_rules(),
        })

        parts = [time_ctx, identity_ctx]
        if mem_str:
            parts.append(mem_str)
        # The personal companion layer: who JARVIS is to this person, what we
        # were last doing, standing lessons, and the conversational contract.
        # Rendered here (like identity) so the voice always matches config.
        try:
            persona_block = build_persona_block(memory, self._asst_name,
                                                _user_name)
            if persona_block:
                parts.append(persona_block)
        except Exception as e:
            print(f"[JARVIS] persona block skipped: {e}")
        # What it has been practising on its own, plus the rules it wrote itself
        # while idle — so a lesson learned with nobody watching changes the very
        # next conversation (see core/self_training.py). '' when off or empty.
        try:
            digest = self_training.curriculum_digest()
            if digest:
                parts.append(digest)
        except Exception as e:
            print(f"[JARVIS] training digest skipped: {e}")
        parts.append(sys_prompt)

        cfg = dict(
            response_modalities=["AUDIO"],
            output_audio_transcription={},
            input_audio_transcription={},
            system_instruction="\n".join(parts),
            tools=[{"function_declarations": _all_decls}],
            # Hand back the handle captured from the last session_resumption
            # update. `handle=None` is exactly the old behaviour (ask for
            # handles, start fresh), so the first connect of a run is unchanged.
            session_resumption=types.SessionResumptionConfig(
                handle=self._resume_handle
            ),
            # Sliding-window compression: session never dies from a full context
            # window — JARVIS can stay in one conversation for hours
            context_window_compression=types.ContextWindowCompressionConfig(
                sliding_window=types.SlidingWindow(),
            ),
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=get_voice()
                    )
                )
            ),
        )
        if self._enhanced_live:
            # Proactive audio: JARVIS stays silent when speech isn't addressed
            # to it (background chatter, talking to someone else in the room).
            # (Affective dialog was dropped: gemini-3.1-flash-live does not
            #  support it, and it never reliably detected tone in practice.
            #  To restore it on a 2.5 native-audio model, add back:
            #  cfg["enable_affective_dialog"] = True )
            if get_proactive_audio_enabled():
                cfg["proactivity"] = types.ProactivityConfig(proactive_audio=True)

        if self._tuned_live:
            cfg.update(self._tuning_config())

        return types.LiveConnectConfig(**cfg)

    def _tuning_config(self) -> dict:
        """The optional knobs, kept apart so one bad field can be dropped wholesale.

        Every one of these is a preview-API field. If a future model release
        stops accepting any of them the connection fails at setup, so the run
        loop turns `_tuned_live` off and reconnects on the plain config rather
        than leaving the user with an assistant that will not start.
        """
        out: dict = {}

        # How long the server waits through a pause before deciding your turn is
        # over. This — not the size of the prompt — is what most of the delay
        # before a reply actually is, and the default has to suit everybody, so
        # it is necessarily cautious.
        turn = get_turn_tuning()
        if turn.get("enabled", True):
            detect = types.AutomaticActivityDetection(
                silence_duration_ms=turn["silence_ms"],
                prefix_padding_ms=turn["prefix_ms"],
            )
            if turn["end_sensitivity"] == "high":
                detect.end_of_speech_sensitivity = types.EndSensitivity.END_SENSITIVITY_HIGH
            elif turn["end_sensitivity"] == "low":
                detect.end_of_speech_sensitivity = types.EndSensitivity.END_SENSITIVITY_LOW
            if turn["start_sensitivity"] == "high":
                detect.start_of_speech_sensitivity = types.StartSensitivity.START_SENSITIVITY_HIGH
            elif turn["start_sensitivity"] == "low":
                detect.start_of_speech_sensitivity = types.StartSensitivity.START_SENSITIVITY_LOW
            out["realtime_input_config"] = types.RealtimeInputConfig(
                automatic_activity_detection=detect)

        # Screenshots and camera frames are tokenised at this resolution and then
        # stay in the session's context. 'medium' keeps on-screen text legible
        # for a fraction of a full-resolution frame.
        res = get_media_resolution()
        if res != "default":
            out["media_resolution"] = {
                "low":    types.MediaResolution.MEDIA_RESOLUTION_LOW,
                "medium": types.MediaResolution.MEDIA_RESOLUTION_MEDIUM,
                "high":   types.MediaResolution.MEDIA_RESOLUTION_HIGH,
            }[res]

        # Thinking is left at the server default deliberately. Forcing the budget
        # to zero was measured on gemini-3.1-flash-live over interleaved trials
        # and did not make the first word arrive sooner — this model does not
        # appear to deliberate on the Live path, so pinning the field only adds a
        # way for a future release to behave differently. Set "thinking_enabled"
        # in config/api_keys.json to true to let it reason instead.
        if get_thinking_enabled():
            out["thinking_config"] = types.ThinkingConfig(thinking_budget=-1)

        return out

    async def _execute_tool(self, fc) -> types.FunctionResponse:
        name = fc.name
        args = dict(fc.args or {})

        print(f"[JARVIS] 🔧 {name}  {args}")
        # EXECUTING is its own state: the face and the header pill both show
        # "doing something concrete" rather than the generic thinking look.
        self.ui.set_state("EXECUTING")
        # Persistent usage tracking — cheap in-memory tick, flushed on a
        # schedule. Feeds the workflow-learning loop (recurring actions across
        # days become remembered preferences).
        try:
            usage_mod.record("action::" + name)
        except Exception:
            pass
        if get_narrate_actions():
            line = _human_narration(name, args)
            if line:
                self.ui.write_log(f"JARVIS: {line}…")


        if name == "save_memory":
            category = args.get("category", "notes")
            key      = args.get("key", "")
            value    = args.get("value", "")
            if key and value:
                # Privacy lever: memory writes can be switched off entirely.
                if get_memory_enabled():
                    update_memory({category: {key: {"value": value}}})
                    print(f"[Memory] 💾 save_memory: {category}/{key} = {value}")
                else:
                    print(f"[Memory] ⛔ save_memory skipped (memory_enabled=false)")
            if not self.ui.muted:
                self.ui.set_state("LISTENING")
            return types.FunctionResponse(
                id=fc.id, name=name,
                response={"result": "ok", "silent": True}
            )

        loop   = asyncio.get_event_loop()
        result = "Done."

        try:
            if name == "recall_memory":
                # Local file search: no network, no second model. Kept out of
                # the executor deliberately — it is a dictionary scan over a few
                # hundred short strings, and a thread hop would cost more than
                # the work itself.
                result = search_memory(args.get("query", ""), limit=8)

            elif name == "undo":
                if str(args.get("action", "")).lower().strip() == "list":
                    items = undo_stack.history()
                    result = ("Things I can undo, most recent first:\n"
                              + "\n".join(f"{i+1}. {t}" for i, t in enumerate(items))
                              ) if items else "I have not changed anything I can undo yet."
                else:
                    result = await loop.run_in_executor(None, undo_stack.undo_last)

            elif name == "screen_process":
                import time as _t_mod
                _now = _t_mod.monotonic()
                _cooldown = 4.0  # seconds — covers echo window after speaking ends
                if self._vision_busy or (_now - self._vision_last_time) < _cooldown:
                    _wait = max(0, _cooldown - (_now - self._vision_last_time))
                    print(f"[Vision] ⏳ Cooldown active ({_wait:.1f}s remaining) — ignoring duplicate call")
                    result = "Vision is still processing the previous request. I will not call this again."
                else:
                    self._vision_busy      = True
                    self._vision_last_time = _now
                    angle     = args.get("angle", "screen").lower()
                    user_text = args.get("text", "What do you see?")
                    if angle == "camera":
                        img_b, mime_t = await loop.run_in_executor(None, _capture_camera)
                        self.ui.start_camera_stream()
                        self._vision_cam_active = True
                        print(f"[Vision] 📷 Camera: {len(img_b):,} bytes")
                        _stall = "camera"
                    else:
                        # Privacy lever: screen capture is refused when the
                        # screen_awareness flag is off. The camera is not covered
                        # by it — that is the webcam the user can see is open.
                        if not get_screen_awareness():
                            self._vision_busy = False
                            result = ("I can't look at the screen right now — "
                                      "screen awareness is turned off in my settings. "
                                      "Enable it and I'll be able to see the screen again.")
                            print("[Vision] 🚫 Screen capture blocked (screen_awareness=false)")
                        else:
                            # Ultra-smooth path: the always-on loop is never more
                            # than ~8 s stale, so if one is that fresh we hand it
                            # over instead of snapping a second screenshot. The
                            # capture backend does the active-monitor framing.
                            if (self._glance_frame and
                                    time.monotonic() - self._glance_frame[3] <= 6.0):
                                img_b, mime_t = self._glance_frame[:2]
                                print(f"[Vision] 🖥️  Screen (from live glance, "
                                      f"{len(img_b):,} bytes)")
                            else:
                                img_b, mime_t = await loop.run_in_executor(
                                    None, _capture_screen, True)
                                print(f"[Vision] 🖥️  Screen: {len(img_b):,} bytes")
                            _stall = "screen"
                    self._pending_vision = (img_b, mime_t, user_text, angle)
                    # The image is attached to this same exchange, so there is
                    # nothing to stall for and nothing to announce. Asking for an
                    # acknowledgement here is what produced two spoken answers —
                    # the model filled that turn by answering the question from
                    # imagination, then answered it again once it could see.
                    result = (
                        f"[VISION_ACTIVE] {_stall.capitalize()} captured and attached to this "
                        f"same exchange. Do not acknowledge and do not answer yet — the image "
                        f"is arriving with this result. Reply once, from what you actually see "
                        f"in it."
                    )

            elif name == "close_camera":
                self.ui.stop_camera_stream()
                result = "Camera closed."

            elif name == "system_status":
                r = await loop.run_in_executor(None, get_system_status)
                result = str(r)

            elif name == "manage_monitor":
                action = args.get("action", "").lower().strip()
                topic  = args.get("topic", "").strip()
                if action == "add" and topic:
                    result = await asyncio.to_thread(add_monitor, topic)
                elif action == "remove" and topic:
                    result = await asyncio.to_thread(remove_monitor, topic)
                elif action == "list":
                    topics = await asyncio.to_thread(list_monitors)
                    result = ("Monitoring: " + ", ".join(topics)) if topics else "No topics are being monitored."
                else:
                    result = "Specify action (add/remove/list) and a topic."

            elif name == "shutdown_jarvis":
                self.ui.write_log("SYS: Shutdown requested.")
                async def _do_shutdown():
                    await self._save_session_summary()
                    if self.session:
                        try:
                            await self.session.send_client_content(
                                turns={"role": "user", "parts": [{"text": "Say a brief natural goodbye to the user."}]},
                                turn_complete=True,
                            )
                        except Exception:
                            pass
                    await asyncio.sleep(1.5)
                    import os as _os
                    _os._exit(0)
                asyncio.create_task(_do_shutdown())

            elif self._action_registry.has(name):
                # file_processor: fall back to the currently-uploaded file when none is given
                if name == "file_processor" and not args.get("file_path") and self.ui.current_file:
                    args["file_path"] = self.ui.current_file
                _ctx = {"player": self.ui, "speak": self.speak,
                        "response": None, "session_memory": None}
                r = await loop.run_in_executor(None, lambda: self._action_registry.run(name, args, _ctx))
                result = r or "Done."
                # web_search: mirror results to the on-screen content panel
                if (name == "web_search" and r
                        and not r.startswith("No results")
                        and not r.startswith("Search failed")):
                    _mode  = args.get("mode", "search")
                    _query = args.get("query") or ", ".join(args.get("items", []))
                    _label = f"{_mode.upper()} — {_query[:38]}" if _query else _mode.upper()
                    self.ui.show_content(_label, r)

            else:
                if self._plugin_registry.has(name):
                    r = await loop.run_in_executor(
                        None,
                        lambda: self._plugin_registry.run(name, args, player=self.ui, session_memory=None)
                    )
                    result = r or "Done."
                else:
                    result = f"Unknown tool: {name}"

        except Exception as e:
            result = f"Tool '{name}' failed: {e}"
            traceback.print_exc()
            self.speak_error(name, e)

        if not self.ui.muted:
            self.ui.set_state("LISTENING")

        print(f"[JARVIS] 📤 {name} → {str(result)[:80]}")

        # A tool that declared itself NON_BLOCKING also says when its answer may
        # re-enter the conversation. Without this the model finishes whatever it
        # was saying and then reads the result out on top of it — which, for
        # something like a phone call already ringing, is exactly the noise the
        # non-blocking call was meant to avoid. Tools that declared nothing get
        # the API default and behave as they always have.
        _sched = (self._action_registry.scheduling(name)
                  or self._plugin_registry.scheduling(name))
        _extra = {"scheduling": _sched} if _sched else {}
        return types.FunctionResponse(
            id=fc.id, name=name,
            response={"result": result},
            **_extra
        )

    def _remember_failure(self, name: str, result: str) -> None:
        """Remember recent tool failures so proactive check-ins can notice them
        and offer a fix instead of the failure being forgotten."""
        low = str(result or "").strip()
        if not low or low in ("Done.", "Unknown tool"):
            return
        if low.lower().startswith(("verified:", "done", "ok",
                                   "success", "downloaded")):
            return
        _bad = ("failed", "error", "unverified", "could not", "couldn't",
                "not found", "no results", "cannot", "can't", "unable",
                "timed out", "unknown tool", "does not appear",
                "not installed")
        if any(t in low.lower() for t in _bad):
            self._recent_failures.append((name, low[:180]))

    async def _send_realtime(self):
        while True:
            msg = await self.out_queue.get()
            if msg.get("audio_stream_end"):
                # End the user's turn explicitly. gemini-3.1-flash-live-preview
                # hears streamed voice but almost never closes the turn on its
                # own (measured: it transcribes the user, then waits forever).
                # Closing the segment is what makes the server start answering.
                try:
                    await self.session.send_realtime_input(audio_stream_end=True)
                except Exception as _e:
                    print(f"[JARVIS] turn-end failed: {_e}")
                continue
            # Gemini 3.x Live rejects the old realtime_input.media_chunks field
            # (what `media=...` maps to) and closes the socket with a 1007. Send
            # mic / phone PCM through the new `audio` field instead. Queue items
            # are {"data": <bytes>, "mime_type": <str>} from _listen_audio and
            # the phone relay.
            await self.session.send_realtime_input(
                audio=types.Blob(
                    data=msg["data"],
                    mime_type=msg.get("mime_type", "audio/pcm"),
                )
            )

    async def _listen_audio(self):
        print("[JARVIS] 🎤 Mic started")
        loop = asyncio.get_event_loop()

        def callback(indata, frames, time_info, status):
            # ── Wake-word gate ───────────────────────────────────────────────
            # While asleep, the mic audio NEVER goes to Gemini (nothing is
            # streamed, so JARVIS can't respond to speech not addressed to it and
            # nothing leaves the machine). Frames are instead handed to the local
            # detector, which runs its model in ITS OWN thread — the cost here is
            # only a queue push, so the audio path is never slowed. When wake word
            # is off (default) or we're awake, this is a single boolean check.
            if self._wake_enabled and not self._awake:
                det = self._wake_detector
                if det is not None:
                    det.feed(indata)
                return
            with self._speaking_lock:
                jarvis_speaking = self._is_speaking

            # ── Barge-in ─────────────────────────────────────────────────────
            # While JARVIS talks the mic is not streamed, but it is still worth
            # listening to locally: if the user starts speaking, cut the answer
            # short the way a person would stop when interrupted.
            #
            # The whole difficulty is echo — on speakers the mic hears JARVIS.
            # So the test is not "is the mic loud" but "is the mic louder than
            # the echo of what we are playing right now", sustained long enough
            # that a cough or a keystroke cannot trigger it.
            if jarvis_speaking:
                # Nothing is streamed while JARVIS talks.
                #
                # Interrupting by voice used to live here: `EchoGuard` can pick a
                # user out from under our own echo, and `core/echo.py` still does
                # that for the tail below. Re-enabling is small — classify each
                # block here and call interrupt() after `required_blocks` of
                # agreement — but it depends on the listener's room, so it stays
                # out until it can be tried on real hardware.
                return

            # ── Echo tail ────────────────────────────────────────────────────
            # The speaking flag has dropped but the speakers have not finished.
            # Sending this to the model is how an assistant hears itself, decides
            # it was addressed, and answers its own last sentence. The microphone
            # stays OPEN — the guard only drops blocks that are our own voice, so
            # replying the instant it stops still works.
            if self._tail_active():
                try:
                    if not self._echo.is_user_speech(
                            indata, SEND_SAMPLE_RATE, _pcm_level(indata)):
                        return
                    self._tail_until = 0.0      # a real voice ends the tail early
                except Exception:
                    return
            elif self._echo._hist:
                self._echo.reset()

            # ── Push-to-talk ─────────────────────────────────────────────────
            # When it is on the microphone is closed by default and the chord
            # opens it, which is the whole point: nothing leaves the machine
            # unless you are holding the key.
            if self._ptt_enabled and not self._ptt_held:
                return

            if not self.ui.muted and not self._phone_active:
                level = _pcm_level(indata)
                # ── End-of-speech → close the user's turn ────────────────────
                # gemini-3.1-flash-live-preview does not close a spoken turn on
                # its own: it transcribes the user and then waits (forever) for
                # audio_stream_end before answering. So the turn is closed here,
                # locally, after a short silence following speech. ~0.45s of
                # quiet: halfway between a pause mid-sentence (~0.2s) and a
                # natural reply gap after finishing (~0.8s), so answers start
                # fast while a thinking user is not cut off mid-thought.
                if level > 0.0:
                    self._user_spoke = True
                    self._user_silent_blocks = 0
                elif self._user_spoke:
                    self._user_silent_blocks += 1
                    # 1024 samples @16 kHz ≈ 64 ms per block → 7 blocks ≈ 0.45 s
                    if self._user_silent_blocks >= 7:
                        self._user_spoke = False
                        self._user_silent_blocks = 0
                        try:
                            loop.call_soon_threadsafe(
                                self.out_queue.put_nowait,
                                {"audio_stream_end": True}
                            )
                        except Exception:
                            pass  # full queue — the next silence window will close it

                data = indata.tobytes()
                loop.call_soon_threadsafe(
                    self.out_queue.put_nowait,
                    {"data": data, "mime_type": "audio/pcm"}
                )
                # Feed the live mic level to the HUD so the waveform reacts to
                # the user's actual voice while listening. Purely cosmetic — any
                # failure here must never disturb the mic.
                try:
                    self.ui.set_audio_level(level)
                except Exception:
                    pass

        try:
            def _open_mic(dev, rate=None):
                return sd.InputStream(
                    samplerate=rate or SEND_SAMPLE_RATE,
                    channels=CHANNELS,
                    dtype="int16",
                    blocksize=CHUNK_SIZE,
                    device=dev,
                    callback=callback,
                )

            # Which microphone. resolve() returns None for "system default" and
            # for a saved device that is no longer present — so a headset
            # unplugged since the last run falls back to the built-in mic
            # instead of raising on startup and taking the session with it.
            _mic_name = get_input_device()
            _mic_dev  = audio_devices.resolve(_mic_name, "input")
            if _mic_dev is not None:
                print(f"[JARVIS] 🎤 Input device: {_mic_name}")

            # Fixed-rate desktop microphones (USB interfaces, webcams,
            # Bluetooth HFP) reject 16 kHz outright. Opening at the device's
            # own rate lets PortAudio resample into exactly the 16 kHz int16
            # the pipeline expects — so the desktop failure mode disappears
            # without touching the laptop path, which opens at 16 kHz as
            # before. `_mic_rate` is None (→ SEND_SAMPLE_RATE) for the default
            # device and for anything that already accepted 16 kHz.
            _mic_rate = None
            if _mic_dev is not None:
                _mic_rate = audio_devices.input_open_rate(_mic_dev)
                if _mic_rate and _mic_rate != SEND_SAMPLE_RATE:
                    print(f"[JARVIS] 🎤 Device is fixed-rate — capturing at "
                          f"{_mic_rate} Hz and resampling to "
                          f"{SEND_SAMPLE_RATE} Hz")
            elif _mic_dev is None and not _mic_name:
                # System default can ALSO be a fixed-rate endpoint (a desktop
                # whose default is a USB interface). Find its rate without
                # naming it.
                try:
                    import sounddevice as _sd
                    _mic_rate = audio_devices.input_open_rate(_sd.default.device[0])
                except Exception:
                    _mic_rate = None

            try:
                _mic_stream = _open_mic(_mic_dev, _mic_rate)
            except Exception as _e:
                # A device the picker listed but the driver will not open right
                # now — exclusive mode, a webcam already in use, a virtual mic
                # whose source went away. Chosen hardware failing must never
                # mean the assistant cannot hear at all.
                if _mic_dev is None:
                    raise
                print(f"[JARVIS] ⚠️  Mic '{_mic_name}' failed: {_e} — using default")
                self.ui.write_log(
                    f"SYS: Microphone '{_mic_name}' unavailable — using system default."
                )
                _mic_stream = _open_mic(None)

            with _mic_stream:
                self._active_mic = (_mic_name if _mic_dev is not None
                                    else "System default")
                print("[JARVIS] 🎤 Mic stream open")
                while True:
                    await asyncio.sleep(0.1)
        except Exception as e:
            print(f"[JARVIS] ❌ Mic: {e}")
            raise

    async def _flush_pending_vision(self) -> bool:
        """Send a captured frame immediately after its tool response.

        The frame is already in hand by the time `screen_process` returns — the
        capture happened inside the tool call. The old flow still made the model
        speak a turn first and only injected the image on that turn's
        turn_complete, which cost a whole extra round trip AND produced two
        spoken answers: one improvised without the picture, then the real one.
        Sending it here means the model has the tool result and the image before
        it generates anything, so the user gets one answer, sooner.
        """
        if not (self._pending_vision and self.session):
            return False

        import base64 as _b64
        img_b, mime_t, question, angle = self._pending_vision
        self._pending_vision = None
        b64 = _b64.b64encode(img_b).decode("ascii")
        print(f"[Vision] 📤 {len(img_b):,} bytes (angle={angle}) → main session")

        # Label the source. Without it the image arrives carrying nothing but
        # the user's own sentence, and a screenshot of this app — which has a
        # face in the middle of it — got read as a photo of the user. What the
        # label *means* is explained once, in the generated [SELF] block.
        src = ("[IMAGE SOURCE: WEBCAM]" if angle == "camera"
               else "[IMAGE SOURCE: SCREEN CAPTURE]")
        await self.session.send_client_content(
            turns={"role": "user", "parts": [
                {"inline_data": {"mime_type": mime_t, "data": b64}},
                {"text": f"{src}\n\n{question}"},
            ]},
            turn_complete=True,
        )

        if self._vision_cam_active:
            # Camera: stay busy until JARVIS has finished speaking the answer,
            # then close the preview.
            self._vision_cam_active    = False
            self._vision_close_pending = True
        else:
            self._vision_busy = False
        return True

    async def _receive_audio(self):
        print("[JARVIS] 👂 Recv started")
        out_buf, in_buf = [], []

        try:
            while True:
                async for response in self.session.receive():

                    # ── Session resumption ───────────────────────────────────
                    # The server sends this periodically. `resumable` goes false
                    # while a turn is mid-flight — replaying a handle from that
                    # moment is what the flag exists to prevent — so only
                    # resumable handles are kept. This is three lines and it is
                    # the entire fix for "every reconnect forgets everything".
                    _sru = getattr(response, "session_resumption_update", None)
                    if _sru is not None:
                        if getattr(_sru, "resumable", False) and getattr(_sru, "new_handle", None):
                            if self._resume_handle is None:
                                print("[JARVIS] 🔗 Session resumption armed")
                            self._resume_handle = _sru.new_handle

                    if response.data:
                        if self._interrupted:
                            pass  # discard: interrupted
                        else:
                            if self._turn_done_event and self._turn_done_event.is_set():
                                self._turn_done_event.clear()
                            # Split into ~50 ms chunks so interrupt() stops audio within 50 ms
                            # (24000 Hz × 2 bytes/sample × 0.05 s = 2400 bytes per slice)
                            _audio_data = response.data
                            _SLICE = 2400
                            for _i in range(0, len(_audio_data), _SLICE):
                                self.audio_in_queue.put_nowait(_audio_data[_i : _i + _SLICE])

                    if response.server_content:
                        sc = response.server_content

                        if sc.output_transcription and sc.output_transcription.text:
                            txt = _clean_transcript(sc.output_transcription.text)
                            # A turn that involves a tool call passes through
                            # several turn_completes, and the API re-sends the
                            # tail of the transcript across them. Comparing only
                            # against the previous chunk missed that — once
                            # out_buf had been flushed and emptied, the repeat
                            # sailed straight back in, which logged the answer
                            # twice AND made the avatar mouth it twice.
                            if txt and not _is_repeat_chunk(txt, out_buf):
                                out_buf.append(txt)
                                # Hand the words to the mouth as they arrive, so
                                # the avatar can form the consonants the audio
                                # alone cannot show. Pure string work — it adds
                                # nothing measurable to the response path.
                                self._visemes.feed_text(txt)

                        if sc.input_transcription and sc.input_transcription.text:
                            txt = _clean_transcript(sc.input_transcription.text)
                            if txt:
                                in_buf.append(txt)
                                self._last_user_speech = time.monotonic()
                                _initiative_note("directed", 1.0)

                        if sc.turn_complete:
                            if self._turn_done_event:
                                self._turn_done_event.set()

                            # If this turn_complete ends an interrupted response, clear the
                            # flag and skip all further processing for that turn.
                            if self._interrupted:
                                self._interrupted = False
                                in_buf  = []
                                out_buf = []
                                self._visemes.reset()
                                continue

                            full_in = " ".join(in_buf).strip()
                            if full_in:
                                self._last_out_logged = ""   # new exchange
                                self.ui.write_log(f"You: {strip_transient_prefix(full_in)}")
                                self._session_log.append(
                                    f"User: {strip_transient_prefix(full_in)}")
                                if self._dashboard:
                                    asyncio.create_task(self._dashboard.broadcast({
                                        "type": "log", "speaker": "user",
                                        "text": full_in,
                                        "ts": datetime.now().isoformat(),
                                    }))
                            in_buf = []

                            full_out = " ".join(out_buf).strip()
                            # Second line of defence: even if a repeat slips
                            # into a *fresh* buffer after a flush, never log the
                            # same answer (or a tail of it) twice in a row.
                            if full_out and len(full_out) >= _REPEAT_MIN and self._last_out_logged:
                                if full_out in self._last_out_logged:
                                    full_out = ""
                            if full_out:
                                self._last_out_logged = full_out
                                self.ui.write_log(f"{self._asst_name}: {full_out}")
                                self._session_log.append(f"{self._asst_name}: {full_out}")
                                if self._dashboard:
                                    asyncio.create_task(self._dashboard.broadcast({
                                        "type": "log", "speaker": "jarvis",
                                        "text": full_out,
                                        "ts": datetime.now().isoformat(),
                                    }))
                            out_buf = []

                            if self._vision_close_pending:
                                # This turn_complete IS the vision answer — close camera + release busy flag
                                self._vision_close_pending = False
                                self._vision_busy = False
                                async def _cam_close():
                                    await asyncio.sleep(2.0)
                                    self.ui.stop_camera_stream()
                                asyncio.create_task(_cam_close())

                    if response.tool_call:
                        fn_responses = []
                        for fc in response.tool_call.function_calls:
                            print(f"[JARVIS] 📞 {fc.name}")
                            fr = await self._execute_tool(fc)
                            fn_responses.append(fr)
                            self._remember_failure(fc.name, fr)
                        await self.session.send_tool_response(
                            function_responses=fn_responses
                        )
                        await self._flush_pending_vision()
        except Exception as e:
            print(f"[JARVIS] ❌ Recv: {e}")
            traceback.print_exc()
            raise

    async def _play_audio(self):
        print("[JARVIS] 🔊 Play started")

        _spk_name = get_output_device()
        _spk_dev  = audio_devices.resolve(_spk_name, "output")
        if _spk_dev is not None:
            print(f"[JARVIS] 🔊 Output device: {_spk_name}")

        def _open_spk(dev):
            st = sd.RawOutputStream(
                samplerate=RECEIVE_SAMPLE_RATE,
                channels=CHANNELS,
                dtype="int16",
                blocksize=CHUNK_SIZE,
                device=dev,
            )
            st.start()
            return st

        try:
            stream = _open_spk(_spk_dev)
        except Exception as _e:
            # A chosen output that the host API accepts by name but refuses to
            # open (exclusive mode, wrong sample rate, device asleep) must not
            # cost the user their voice. Fall back to the default and say so.
            if _spk_dev is None:
                raise
            print(f"[JARVIS] ⚠️  Output device '{_spk_name}' failed: {_e} — using default")
            self.ui.write_log(f"SYS: Speaker '{_spk_name}' unavailable — using system default.")
            stream = _open_spk(None)

        # Ask the device how far behind the speakers actually are, rather than
        # assuming. This is what the echo tail is sized from, so a machine with a
        # large audio buffer gets a correspondingly longer guard — and one with a
        # tiny buffer is not penalised with a delay it does not need.
        try:
            lat = float(getattr(stream, "latency", 0.0) or 0.0)
            if 0.0 < lat < 1.0:
                self._out_latency = lat
            print(f"[JARVIS] 🔊 Output latency {self._out_latency*1000:.0f} ms "
                  f"→ echo tail {(self._out_latency + _TAIL_MARGIN)*1000:.0f} ms")
        except Exception:
            pass

        try:
            while True:
                try:
                    chunk = await asyncio.wait_for(
                        self.audio_in_queue.get(),
                        timeout=0.1
                    )
                except asyncio.TimeoutError:
                    if (
                        self._turn_done_event
                        and self._turn_done_event.is_set()
                        and self.audio_in_queue.empty()
                    ):
                        self.set_speaking(False)
                        self._turn_done_event.clear()
                    continue

                self.set_speaking(True)

                # Batch all immediately-available chunks into one write to reduce
                # thread-pool round-trips (was one asyncio.to_thread per 50ms slice).
                # Cap at ~200 ms so interrupt() still stops audio within ~200 ms.
                batch = bytearray(chunk)
                while len(batch) < 9600:   # 9600 bytes ≈ 200 ms at 24 kHz / 16-bit mono
                    try:
                        batch.extend(self.audio_in_queue.get_nowait())
                    except asyncio.QueueEmpty:
                        break

                # Drive the HUD waveform and the avatar's mouth from JARVIS's
                # own voice. The batch is up to 200 ms long, so we hand over a
                # *schedule* of 20 ms viseme frames instead of a single averaged
                # level and let the HUD play it out in step with the audio.
                try:
                    pcm = np.frombuffer(bytes(batch), dtype=np.int16)
                    hop = _VIS_HOP / RECEIVE_SAMPLE_RATE
                    frames = _pcm_visemes(pcm, sr=RECEIVE_SAMPLE_RATE)
                    # When does this batch become audible? The stream was
                    # started at launch and its callback has been pulling
                    # silence ever since, so the first bytes of a reply reach
                    # the speaker about one callback period later — NOT one
                    # buffer later. `stream.latency` reports the buffer's
                    # capacity, which is how much can be queued ahead, and on
                    # Windows that is commonly 300-500 ms. Anchoring on it put
                    # the entire schedule a buffer late; that is the half second
                    # of lag, and it grew with whatever the device reported.
                    #
                    # After the anchor nothing needs measuring: the device
                    # consumes at exactly realtime, so each batch sounds one
                    # batch-duration after the one before it. The cursor is
                    # re-anchored only when it leaves the range physically
                    # possible — behind `now` means the device drained and this
                    # batch starts a fresh stretch of speech, while further
                    # ahead than the buffer can hold means it has drifted.
                    now = time.time()
                    horizon = self._out_latency + _CURSOR_SLACK
                    if not (now <= self._play_cursor <= now + horizon):
                        self._play_cursor = now + _FIRST_SOUND
                    at = self._play_cursor
                    # Advance by the batch's own duration whether or not it
                    # yielded frames, so a block too short to analyse cannot
                    # shift everything after it out of step with the audio.
                    self._play_cursor += pcm.size / RECEIVE_SAMPLE_RATE
                    if frames:
                        frames = self._visemes.frames(frames, hop)
                        self.ui.push_visemes(frames, hop, at)
                        # Barge-in needs to know what we are playing, not just
                        # how loud: the guard subtracts this from the microphone.
                        self._out_level = max(f[0] for f in frames)
                        self._echo.note_output(pcm, RECEIVE_SAMPLE_RATE,
                                               self._out_level)
                    else:
                        lvl = _pcm_level(pcm)
                        self.ui.set_audio_level(lvl)
                        self._out_level = lvl
                        self._echo.note_output(pcm, RECEIVE_SAMPLE_RATE, lvl)
                except Exception:
                    pass

                try:
                    await asyncio.to_thread(stream.write, bytes(batch))
                except (RuntimeError, asyncio.CancelledError):
                    break   # executor shutting down — exit cleanly
        except Exception as e:
            print(f"[JARVIS] ❌ Play: {e}")
            raise
        finally:
            self.set_speaking(False)
            stream.stop()
            stream.close()

    # ── Morning briefing ────────────────────────────────────────────────────────

    async def _send_startup_briefing(self) -> None:
        """
        Two-phase briefing optimized for speed:
          Phase 1 — instant greeting (no tools) → speech starts in <1s
          Phase 2 — news pre-fetched in a background thread while Phase 1 plays,
                    delivered as ready text (no Gemini tool-call round-trip) and
                    shown on the UI content panel. Waits for turn_complete event
                    instead of a fixed sleep so there is no unnecessary gap.
        """
        memory   = load_memory()
        identity = memory.get("identity", {})

        def _val(k: str) -> str:
            e = identity.get(k, {})
            return (e.get("value", "") if isinstance(e, dict) else str(e)).strip()

        lang = _val("language")
        name = _val("name")
        time_str = datetime.now().strftime("%H:%M")

        # Start fetching news immediately — runs in parallel while phase 1 plays
        loop = asyncio.get_event_loop()
        news_future = loop.run_in_executor(None, _fetch_news_sync, "top world news today")

        await asyncio.sleep(0.3)
        if not self.session:
            return

        # ── Phase 1: instant greeting ─────────────────────────────────────────
        # The briefing fires before the user has said anything, so the
        # remembered language is the only signal there is. It is a starting
        # point, not a setting: the moment they reply, their language wins.
        lang_clause = (f" Speak this greeting in {lang}, then follow the "
                       f"user's own language from their first reply onward."
                       if lang else "")
        # Configured preference ('' when auto) — a sharper hint than the stored
        # language when the user has pinned Hindi / English / Hinglish.
        lang_mode_clause = language_hint()
        name_clause = f" Address the user as {name}." if name else ""

        # Inject last session context if available — pop removes it so it's never repeated
        last = await asyncio.to_thread(pop_last_session)
        session_clause = ""
        if last:
            try:
                _delta = (datetime.now() - datetime.strptime(last["date"], "%Y-%m-%d")).days
                _when  = "earlier today" if _delta == 0 else ("yesterday" if _delta == 1 else f"{_delta} days ago")
            except Exception:
                _when = "last time"
            session_clause = (
                f" Also briefly and naturally mention that {_when}: {last['summary']}"
            )

        p1 = (
            f"Greet the user warmly, mention it is {time_str}, and say you are fetching today's news now.{session_clause} "
            f"Keep it to 2 short sentences max. Do not call any tools.{lang_clause}{lang_mode_clause}{name_clause}"
        )

        # Clear the turn-done event so we can wait for Phase 1 to finish
        if self._turn_done_event:
            self._turn_done_event.clear()

        await self.session.send_client_content(
            turns={"role": "user", "parts": [{"text": p1}]},
            turn_complete=True,
        )
        print("[JARVIS] Briefing phase 1 (greeting) sent.")

        # ── Phase 2: fire as soon as Phase 1 audio is done ───────────────────
        async def _deliver_news():
            try:
                lang_str = (f" Speak in {lang} unless the user has since "
                            f"spoken another language, in which case use theirs."
                            if lang else "")
                lang_str = f"{lang_str} {lang_mode_clause}".strip()

                # Wait for news fetch (already running) and Phase 1 turn-complete
                # in parallel — whichever takes longer determines the wait time
                news_done   = asyncio.wrap_future(news_future)
                turn_waited = False
                if self._turn_done_event:
                    try:
                        await asyncio.wait_for(self._turn_done_event.wait(), timeout=6.0)
                        turn_waited = True
                    except asyncio.TimeoutError:
                        pass

                # Extra buffer: turn_complete fires when Gemini finishes *generating*
                # Phase 1, but audio may still be playing.  Waiting a beat here
                # prevents Phase 2 audio from arriving while Phase 1 is mid-sentence
                # (which sounds like a "repeated first response" to the user).
                if turn_waited:
                    await asyncio.sleep(0.8)
                else:
                    await asyncio.sleep(1.0)

                try:
                    news_text = await asyncio.wait_for(news_done, timeout=8.0)
                except Exception as e:
                    self.ui.write_log(f"SYS: News fetch timed out/failed: {e!r}")
                    news_text = ""

                if not self.session:
                    return

                failed = (not news_text) or news_text.startswith(
                    ("No news found", "Search failed", "Please provide")
                )
                if not failed:
                    # Show on UI content panel immediately
                    self.ui.show_content("NEWS — top world news today", news_text)

                    p2 = (
                        f"[BRIEFING] Here are today's top news headlines:\n{news_text}\n\n"
                        "Pick ONE headline, summarise it in one sentence, then say the full list "
                        f"is displayed on screen. Do not call any tools.{lang_str}"
                    )
                else:
                    self.ui.write_log(
                        f"SYS: News unavailable — backend returned: {news_text[:120]!r}"
                    )
                    p2 = (
                        "News headlines could not be fetched right now. "
                        f"Let the user know briefly.{lang_str}"
                    )

                await self.session.send_client_content(
                    turns={"role": "user", "parts": [{"text": p2}]},
                    turn_complete=True,
                )
                print("[JARVIS] Briefing phase 2 (news) sent.")
            except Exception as e:
                print(f"[Briefing] Phase 2 error: {e}")
                print(f"[JARVIS] Briefing phase 2 failed: {e}")
                self.ui.write_log("SYS: Could not fetch the news for the briefing.")

        asyncio.create_task(_deliver_news())

    # ── Session memory ──────────────────────────────────────────────────────────

    async def _save_session_summary(self) -> None:
        """Summarise the current session in 1-2 sentences and save to long_term.json."""
        # Privacy lever: no memory writes at all when disabled. The conversation
        # log is drained either way so it can never accumulate unbounded.
        if not get_memory_enabled():
            print("[Memory] ⛔ Session summary skipped (memory_enabled=false)")
            self._session_log = []
            return
        log = self._session_log
        if len(log) < 3:          # need at least one exchange to be worth saving
            return
        self._session_log = []    # reset immediately so the next session starts clean

        memory = load_memory()
        lang_entry = memory.get("identity", {}).get("language", {})
        lang = (lang_entry.get("value", "") if isinstance(lang_entry, dict) else str(lang_entry)).strip()
        lang = lang or "English"

        convo = "\n".join(log[-40:])   # cap at last 40 turns to stay within token budget
        prompt = (
            f"Summarize this conversation in 1-2 sentences in {lang}. "
            "Focus on what the user accomplished or discussed. "
            "Output ONLY the summary text, nothing else:\n\n" + convo
        )
        try:
            from core import gemini
            summary = await asyncio.to_thread(
                gemini.text, prompt, gemini.SMART, None, 30_000,
            )
            if summary:
                save_session_summary(summary, lang)
        except Exception as e:
            print(f"[Memory] ⚠️ Session summary failed: {e}")

        # Continuous learning on the same drained chunk: extract durable facts
        # and lessons, then a quiet self-review → applied improvements. Runs in
        # a worker thread; failures only mean nothing was learned this round.
        try:
            await self._learn_from_lines(log)
        except Exception as e:
            print(f"[Learning] ⚠️ session-end learning failed: {e}")
        self._learned_marker = 0

    # ── Continuous learning (self-improvement loop) ──────────────────────────────

    async def _run_learning_loop(self) -> None:
        """Background task: incrementally learn from the conversation in a long
        session (facts + lessons + self-review) and flush the usage counters, so
        JARVIS keeps learning while the user keeps talking. Near-continuous
        observation, throttled calls: the loop polls every ~45 s but only pays
        for a model call when real new dialogue has accumulated — bursts learn
        promptly, slow chatter never spends quota on stale repeats.
        """
        await asyncio.sleep(30)            # let a session actually start
        while True:
            await asyncio.sleep(45)
            try:
                await self._learn_from_recent()
            except Exception as e:
                print(f"[Learning] ⚠️ loop error: {e}")
            try:
                await self._maybe_train()
            except Exception as e:
                print(f"[Training] ⚠️ loop error: {e}")
            try:
                usage_mod.flush()
                if time.monotonic() - self._last_usage_flush >= 900:
                    usage_mod.prune_older_than(days=45)
                    self._last_usage_flush = time.monotonic()
            except Exception:
                pass

    async def _maybe_train(self) -> None:
        """A self-training round — but only while the user is quiet.

        Two throttles stand between this and burning API quota: this method
        refuses to interrupt speech or a sleeping session at all, and
        core/self_training.cycle_due() owns the hourly budget and the minimum
        quiet time. The round is one model call on a worker thread, and the HUD
        is told when it starts and what it learned.
        """
        if not self._awake:
            return
        with self._speaking_lock:
            if self._is_speaking:
                return
        idle = time.monotonic() - self._last_user_speech
        if not self_training.cycle_due(idle):
            return
        push = getattr(self.ui, "push_training", None)
        if push:
            try:
                push({"phase": "running"})
            except Exception:
                pass
        report = await asyncio.to_thread(self_training.run_cycle, "idle")
        if push:
            try:
                snap = await asyncio.to_thread(self_training.snapshot)
                push({"phase": "done", "report": report, "state": snap})
            except Exception:
                pass

    async def _learn_from_recent(self) -> None:
        """Consume the unconsumed tail of the conversation log (≥4 turns) and
        learn from it, honouring the memory_enabled privacy lever. Bursts (≥10
        new turns) learn right away; slow chatter waits at most ~4 minutes past
        the fourth turn, so learning stays continuous without burning quota."""
        if not get_memory_enabled():
            return
        log = list(self._session_log)
        new = len(log) - self._learned_marker
        if new < 4:
            return
        slow_ok = time.monotonic() - self._last_learn_ts >= 240
        if not slow_ok and new < 10:
            return                       # burst throttle: recent learn already
        chunk = log[self._learned_marker:]
        if len(chunk) < 4:
            return
        await self._learn_from_lines(chunk)
        self._learned_marker = len(self._session_log)
        self._last_learn_ts = time.monotonic()

    async def _learn_from_lines(self, chunk: list[str]) -> None:
        """[worker-thread] Fact+lesson extraction and a self-review pass. Both
        are model calls that never raise; a failed call learns nothing."""
        if not get_memory_enabled() or not chunk:
            return
        try:
            await asyncio.to_thread(learning_mod.learn_from_conversation, chunk)
        except Exception as e:
            print(f"[Learning] ⚠️ extraction failed: {e}")
        try:
            convo = "\n".join((str(l) for l in chunk))[-4000:]
            notes = await asyncio.to_thread(learning_mod.evaluate, convo)
            if notes:
                await asyncio.to_thread(learning_mod.apply_improvements, notes)
        except Exception as e:
            print(f"[Learning] ⚠️ review failed: {e}")

    # ── System monitor ──────────────────────────────────────────────────────────

    async def _run_system_monitor(self) -> None:
        """Background task: voice alerts when metrics exceed thresholds."""
        while True:
            await asyncio.sleep(10)
            alert = await asyncio.to_thread(self._sys_monitor.check)
            if not alert or not self.session or not self._awake:
                continue
            # Don't interrupt an active conversation
            with self._speaking_lock:
                speaking = self._is_speaking
            if speaking or (time.monotonic() - self._last_user_speech) < 10:
                continue
            try:
                await self.session.send_client_content(
                    turns={"role": "user", "parts": [{"text": alert}]},
                    turn_complete=True,
                )
            except Exception as e:
                print(f"[Monitor] ⚠️ Could not send alert: {e}")

    # ── Background monitor ──────────────────────────────────────────────────────

    async def _run_background_monitor(self) -> None:
        """Check user-configured topics once per day; speak alerts when new headlines appear."""
        await asyncio.sleep(300)          # wait 5 min after startup before first check
        while True:
            if self.session and self._awake:
                # Don't interrupt if user spoke recently or JARVIS is mid-sentence
                with self._speaking_lock:
                    speaking = self._is_speaking
                recent_speech = (time.monotonic() - self._last_user_speech) < 30
                if not speaking and not recent_speech:
                    try:
                        alerts = await asyncio.to_thread(monitor_check_all)
                        memory = load_memory()
                        lang_e = memory.get("identity", {}).get("language", {})
                        lang   = (lang_e.get("value", "") if isinstance(lang_e, dict) else str(lang_e)).strip() or "English"
                        for alert in alerts:
                            msg = (
                                f"{alert}\n\n"
                                f"Inform the user about this development naturally in {lang}. "
                                "One brief sentence only."
                            )
                            await self.session.send_client_content(
                                turns={"role": "user", "parts": [{"text": msg}]},
                                turn_complete=True,
                            )
                            print("[JARVIS] Monitor alert sent.")
                            await asyncio.sleep(6)   # gap between consecutive alerts
                    except Exception as e:
                        print(f"[Monitor] ⚠️ Background check error: {e}")
            await asyncio.sleep(1800)     # check every 30 minutes

    # ── Trading alerts (§9/§10) ──────────────────────────────────────────────────

    async def _run_trade_alerts(self) -> None:
        """Drain fired price/indicator/news trade alerts onto the session —
        same send path as the news monitor, but a 12-second cadence: a price
        alert that waits 30 minutes is worthless. Nothing is dropped while
        JARVIS is busy, speaking or asleep — the queue simply holds."""
        from core.trading import alerts as trade_alerts
        while True:
            await asyncio.sleep(12)
            try:
                if not (self.session and self._awake):
                    continue                      # keep alerts queued
                with self._speaking_lock:
                    if self._is_speaking:
                        continue                  # don't talk over the user
                msgs = trade_alerts.drain()
                if not msgs:
                    continue
                memory = load_memory()
                lang_e = memory.get("identity", {}).get("language", {})
                lang = (lang_e.get("value", "") if isinstance(lang_e, dict)
                        else str(lang_e)).strip() or "English"
                for alert in msgs[:4]:
                    await self.session.send_client_content(
                        turns={"role": "user", "parts": [{"text": (
                            f"{alert}\n\n"
                            f"Relay this market alert to the user briefly in {lang}. "
                            "One or two factual sentences — no prediction, no advice."
                        )}]},
                        turn_complete=True,
                    )
                    await asyncio.sleep(4)        # gap between consecutive alerts
            except Exception as e:
                print(f"[TradeAlerts] ⚠️ delivery error: {e}")

    # ── Ambient participation ───────────────────────────────────────────────────

    def _ambient_rules(self) -> str:
        """The [AMBIENT] prompt section. Always present so the default session is
        an explicit one-on-one; when `ambient_mode` is switched on this relaxes
        into a capped, well-mannered right to contribute to the room."""
        if not get_ambient_mode():
            return (
                "This is a one-on-one session. Speech you can hear that is not "
                "addressed to you — a conversation in the room, a video, another "
                "person talking — is background noise. Do NOT respond to it: stay "
                "silent until the user speaks to you directly."
            )

        interval = max(3, get_ambient_interval_min())
        return (
            "[AMBIENT PARTICIPATION]\n"
            "You are in the room, not just the headset: you can hear the user in "
            "conversation with other people, or the room around the microphone. "
            "You MAY contribute to that conversation even when it is not addressed "
            "to you — the way a well-mannered expert in the room would — under "
            "these hard rules:\n"
            f"- At most one contribution every {interval} minutes. Silence is the "
            "default state, and every contribution you make spends that budget.\n"
            "- Only speak when it is genuinely useful: a missing fact, a sharper "
            "wording, or a direct answer to a real question anyone in the room "
            "asked. If you are not sure it is welcome, say nothing.\n"
            "- Never answer a question that was clearly directed at someone else "
            "unless invited. Never repeat yourself, never narrate what is "
            "happening, never comment on the conversation itself.\n"
            "- The moment the user says to stop -- 'stop', 'shut up', 'bas karo', "
            "'you talk too much' -- or any language -- YOU STOP completely. Stay "
            "silent until the user speaks to you directly again.\n"
            "- Nothing here changes the ordinary rules: when the user speaks to "
            "you, answer normally; the [LANGUAGE] rules and the tool rules are "
            "still in force in every contribution."
        )

    # ── Proactive mode ──────────────────────────────────────────────────────────

    # ── Always-on screen glance ──────────────────────────────────────────────

    def set_screen_status(self, active: bool, caption: str = "") -> None:
        """Thread-safe hop to the UI thread for the Screen Awareness chip."""
        try:
            self._screen_status_sig.emit(bool(active), str(caption or ""))
        except Exception:
            pass

    async def _run_glance_loop(self) -> None:
        """Keep the newest screen frame + one-line caption in memory so proactive
        check-ins (and screen questions) reflect the screen *now*, not a lazy
        snapshot taken once a check-in fires. Designed to cost nothing when
        nothing changes: a captured frame is fingerprinted, and while the screen
        is essentially static — no caption call, no re-encode, no re-store; the
        already-decoded frame is just re-timestamped so attached context stays
        fresh for free. Cadence comes from the quality the user picked in the
        UI (light = almost zero drain, high = near-live view)."""
        while True:
            base_poll, ttl = settings_for(get_screen_share_quality())
            # Adaptive cadence: media stretches the poll, UI churn tightens it,
            # a long-static screen relaxes it. Recomputed every cycle from the
            # visual memory's trail — no extra sampling cost.
            poll = _adaptive_poll(
                base_poll,
                category=(self._screen_observer.snapshot().get("category", "")
                          if self._screen_observer else ""),
                recent_change_ratio=(self._visual_memory.last or {}).get("ratio", 0.0),
                static_secs=self._visual_memory.static_secs(),
            )
            await asyncio.sleep(poll)
            if not self.session or not self._awake:
                continue
            if not (get_screen_awareness() and get_screen_glance()):
                self._visual_memory.set_enabled(False)
                self.ui.set_screen_status(False)
                continue
            self._visual_memory.set_enabled(True)
            self.ui.set_screen_status(True,
                                      (self._screen_observer.humanize()
                                       if self._screen_observer else ""))
            with self._speaking_lock:
                speaking = self._is_speaking
            if speaking:
                continue
            try:
                data, mime = await asyncio.to_thread(_capture_screen, True)
                if not data:
                    continue
                fprint = frame_fingerprint(data)
                changed = bool(fprint) and fprint != self._glance_hash
                prev_hash = self._glance_hash
                self._glance_hash = self._glance_hash if fprint == "" else fprint
                old = self._glance_frame
                caption = old[2] if old else None
                now_mono = time.monotonic()
                if changed:
                    # WHERE did it change? A 16-cell perceptual diff of the
                    # previous vs current frame — cheap, local, no model.
                    prev_data = old[0] if old else None
                    regions = await asyncio.to_thread(
                        change_regions, prev_data, data)
                    # Observed state: app/category from the local observer.
                    obs = (self._screen_observer.snapshot()
                           if self._screen_observer else {})
                    sig, why = _screen_significance(
                        ratio=regions.get("ratio", 0.0),
                        caption_new=(caption or "") if caption else "",
                        caption_old=(old[2] or "") if old else "",
                        category=obs.get("category", ""),
                        static_secs=self._visual_memory.static_secs(),
                    )
                    # Only pay for the caption model call when the screen truly
                    # moved AND the TTL allows. High-significance frames (errors,
                    # dialogs, big layout changes) refresh sooner than the TTL.
                    want_caption = (
                        caption is None
                        or now_mono - self._glance_caption_ts >= ttl
                        or (sig >= 0.55 and
                            now_mono - self._glance_caption_ts >= ttl / 3)
                    )
                    if want_caption:
                        from actions.proactive import screen_glance
                        new_caption = await asyncio.to_thread(
                            screen_glance, data, mime)
                        if new_caption:
                            caption = new_caption
                        self._glance_caption_ts = now_mono
                    self._glance_frame = (data, mime, caption, now_mono)
                    # Feed the short-term visual memory (real changes only).
                    self._visual_memory.observe(
                        fingerprint=fprint,
                        caption=caption or "",
                        app=obs.get("app", ""),
                        title=obs.get("title", ""),
                        category=obs.get("category", ""),
                        ratio=regions.get("ratio", 0.0),
                        where=regions.get("where", ""),
                        sig=sig,
                    )
                    if caption and (want_caption or sig >= 0.4):
                        print(f"[Glance] change ({why or 'minor'}) — caption "
                              f"{'refreshed' if want_caption else 'kept'}.")
                elif old:
                    # Static screen: skip encode/caption entirely, but keep the
                    # timestamp fresh so a check-in can still attach this frame.
                    self._glance_frame = (old[0], old[1], old[2], now_mono)
            except Exception as e:
                print(f"[Glance] skipped: {e}")

    async def _run_proactive_mode(self) -> None:
        """
        Background task: periodically checks if the user has been silent long enough,
        then hands time + memory context to Gemini so it can decide what (if anything)
        to say proactively. No hardcoded rules — Gemini makes the call.
        """
        while True:
            # Cadence is read live so a config edit takes effect without a
            # restart. `live` also narrows the poll loop so near-continuous
            # presence is not hollowed out by a 60-second tick.
            cadence = self._proactive.apply_cadence(get_talk_cadence())
            tick = 15 if cadence == "chatty" else (30 if cadence == "live" else 60)
            # Handed-over PC: check in on the same brisk tick, because the
            # governor — not the sleep — is what keeps the rate sane.
            if self._autonomy and self._autonomy.get_mode("autonomous"):
                tick = min(tick, 30)
            await asyncio.sleep(tick)

            if not self.session or not self._awake:
                continue

            # Privacy lever: proactive check-ins can be switched off entirely.
            if not get_proactive_enabled():
                continue

            with self._speaking_lock:
                speaking = self._is_speaking
            if speaking:
                continue

            # Autonomous mode changes which clock applies: with the PC handed
            # over, the floor is "the user has been quiet a couple of minutes"
            # rather than "nothing has been said for fifteen". Without it, the
            # old cadence is untouched.
            idle_now = time.monotonic() - self._last_user_speech
            autonomous_tick = bool(
                self._idle_governor
                and self._idle_governor.due(idle=idle_now))
            if not autonomous_tick and not self._proactive.should_trigger(
                    self._last_user_speech):
                continue

            self._proactive.mark_triggered()
            if autonomous_tick:
                self._idle_governor.mark("idle initiative", now=time.monotonic())

            try:
                memory       = await asyncio.to_thread(load_memory)
                monitors     = await asyncio.to_thread(list_monitors)
                recent_turns = self._session_log[-8:] if self._session_log else []

                # Screen/activity context (metadata only) from the local observer,
                # owned by the same privacy flag that gates the observer itself.
                screen = None
                screen_events = None
                timeline = None
                if get_screen_awareness() and self._screen_observer is not None:
                    screen = self._screen_observer.humanize() or None
                    if screen:
                        evs = self._screen_observer.recent_events()
                        screen_events = [
                            f"left {e['before']['app'] or 'a window'} → "
                            f"{e['app'] or e['title'][:40] or 'a window'}"
                            for e in evs[-4:]
                            if e.get("before")
                        ] or None
                    if get_track_activity():
                        timeline = self._screen_observer.activity_timeline() or None

                # Fresh-eye context: the always-on glance loop already keeps the
                # newest frame + one-line caption, so an attached check-in talks
                # about the screen as it is *right now* — and we attach the real
                # image, not just its description. Gated by the same privacy
                # levers that own the loop (awareness + glance).
                screen_description = None
                frame_part = None
                visual_trail = None
                if get_screen_awareness() and get_screen_glance() and self._glance_frame:
                    data, mime, caption, ts = self._glance_frame
                    if time.monotonic() - ts <= 30.0:
                        screen_description = caption
                        frame_part = (data, mime)
                    # The trail that led here: recent significant screen changes
                    # from the short-term visual memory, so a check-in can say
                    # "you've been fighting that dialog a while" accurately.
                    if self._visual_memory.recent_changes():
                        visual_trail = self._visual_memory.recent_changes(3)

                prompt = self._proactive.build_prompt(
                    memory        = memory,
                    monitors     = monitors or None,
                    recent_turns  = recent_turns or None,
                    screen        = screen,
                    screen_events = screen_events,
                    timeline      = timeline,
                    # Companion cadences keep the thread alive rather than
                    # waiting for something "worth interrupting for".
                    keep_flow     = cadence in ("live", "chatty"),
                    screen_description = screen_description,
                    visual_trail  = visual_trail,
                    issues        = list(self._recent_failures) or None,
                    # Locally-counted recurring actions (never content) — the
                    # "notices routines without being told" half of proactivity.
                    routines      = await asyncio.to_thread(
                        usage_mod.workflow_candidates) or None,
                    # An autonomous moment may act, not just talk, so the rules
                    # that normally forbid tool calls are lifted for it.
                    allow_tools   = autonomous_tick,
                )
                move = None
                if autonomous_tick:
                    # Asked BEFORE the agenda text is added, because the move
                    # decides what the agenda is about this time.
                    move = self._initiative_move(
                        idle_now=idle_now, activity=screen or "",
                        recent=recent_turns)
                    prompt += "\n\n" + self._autonomy.idle_agenda(
                        activity=screen or "",
                        idle_seconds=idle_now,
                        recent=" | ".join(recent_turns[-3:]) if recent_turns else "",
                        autonomous=True)
                    block = self._initiative_block(move)
                    if block:
                        prompt += "\n\n" + block
                parts = [{"text": prompt}]
                if frame_part:
                    import base64 as _b64
                    data, mime = frame_part
                    print(f"[Proactive] attaching live screen frame to check-in "
                          f"({len(data):,} bytes)")
                    parts = [
                        {"inline_data": {
                            "mime_type": mime,
                            "data": _b64.b64encode(data).decode("ascii"),
                        }},
                        *parts,
                    ]
                await self.session.send_client_content(
                    turns={"role": "user", "parts": parts},
                    turn_complete=True,
                )
                print("[JARVIS] Proactive check-in.")
            except Exception as e:
                print(f"[Proactive] ⚠️ {e}")

    def _initiative_move(self, *, idle_now: float, activity: str, recent: list):
        """What JARVIS should do with the handed-over PC, or None.

        None is returned both when nothing clears the bar and when the engine
        is unavailable. The caller treats those identically on purpose: an
        engine that failed to import must never mean "improvise something to
        look busy", because that is precisely the behaviour this replaces.

        The previous move is graded first, from the control feed rather than
        from the model's account of how it went — the machine's own record is
        the only evidence that can be trusted to move a mood.
        """
        try:
            from core import initiative
            initiative.grade_in_flight(self._autonomy.feed_recent(40))
            move = initiative.next_move(
                idle_seconds=idle_now,
                user_activity=activity or "",
                topics=list(recent or [])[-4:],
            )
            if move is not None and self._idle_governor is not None:
                # A real mission was chosen, so reset the gap between
                # contributions. Without this the governor's cooldown and the
                # director's quiet both apply and it drifts to three times
                # slower than configured.
                self._idle_governor.mark(f"initiative: {move.key}",
                                         now=time.monotonic())
            return move
        except Exception as e:
            print(f"[Initiative] skipped: {e}")
            return None

    def _initiative_block(self, move) -> str:
        """The prompt block describing the mood and the chosen mission."""
        try:
            from core import initiative
            return initiative.prompt_block(move, autonomous=True,
                                           status=initiative.status())
        except Exception:
            return ""

    # ── Phone audio relay ────────────────────────────────────────────────────────

    async def _relay_phone_audio(self) -> None:
        """Forward phone mic PCM chunks from dashboard queue into the Gemini Live session."""
        q = self._dashboard._phone_audio_queue
        while True:
            try:
                chunk = await asyncio.wait_for(q.get(), timeout=1.0)
            except asyncio.TimeoutError:
                # No audio for 1 s → phone mic inactive, give PC mic back.
                # Close the phone user's turn the same way the PC mic closes
                # its own: without audio_stream_end the model transcribes and
                # then waits forever, so the phone never gets an answer.
                if self._phone_active:
                    try:
                        self.out_queue.put_nowait({"audio_stream_end": True})
                    except asyncio.QueueFull:
                        pass
                self._phone_active = False
                continue
            self._phone_active = True   # phone is streaming — silence PC mic
            with self._speaking_lock:
                speaking = self._is_speaking
            if not speaking and not self.ui.muted:
                try:
                    self.out_queue.put_nowait(chunk)
                except asyncio.QueueFull:
                    pass

    def _on_phone_connected(self) -> None:
        self.ui.write_log("SYS: Phone connected via Remote Dashboard.")
        self.ui.notify_phone_connected()

    # ── dashboard command relay ─────────────────────────────────────────────

    async def _process_dashboard_commands(self) -> None:
        while True:
            try:
                text = await asyncio.wait_for(
                    self._dashboard._command_queue.get(), timeout=0.5
                )
                if not text:
                    continue
                # Wait up to 8s for session to become ready after a wake
                for _ in range(80):
                    if self.session:
                        break
                    await asyncio.sleep(0.1)
                if self.session:
                    # A typed command is the user driving, so the mood goes
                    # sharp rather than staying wherever the last idle hour
                    # left it.
                    _initiative_note("directed", 1.0)
                    # A remote command is deliberate control and the phone user
                    # has no desktop WAKE button — so it wakes JARVIS if asleep.
                    if self._wake_enabled and not self._awake:
                        self.wake(reason="remote command")
                    _dtag = mood_tag(text)
                    try:
                        _dtag = " ".join(x for x in (_dtag, _emotion_delivery(text))
                                         if x)
                    except Exception:
                        pass
                    await self.session.send_client_content(
                        turns={"role": "user", "parts": [
                            {"text": f"{_dtag}\n{text}" if _dtag else text}
                        ]},
                        turn_complete=True,
                    )
                    self.ui.write_log(f"[Web]: {text}")
                else:
                    print(f"[Dashboard] Dropped command (no session): {text}")
            except asyncio.TimeoutError:
                pass
            except Exception as e:
                print(f"[Dashboard] Command error: {e}")
                await asyncio.sleep(0.5)

    # ── main loop ───────────────────────────────────────────────────────────

    async def run(self):
        self._loop = asyncio.get_event_loop()
        self._reconnect_event = asyncio.Event()

        # ── Wire the shared core services to the interface ───────────────────
        # The confirmation gate is useless without a way to ask, and a memory
        # trim is invisible without a way to say so. Both are bound once here
        # rather than passed down through every action signature.
        confirm_gate.bind(
            show = self.ui.show_confirm,
            hide = self.ui.hide_confirm,
            log  = self.ui.write_log,
        )
        set_trim_notifier(self.ui.write_log)

        # Every computer-control step reports itself to the HUD as it happens.
        # Bound here (not inside the actions) so the action layer never needs to
        # know whether there is an interface attached — and so a headless run
        # simply keeps the feed in memory instead of crashing on it.
        try:
            from core import autonomy as _autonomy_mod
            push = getattr(self.ui, "push_control", None)
            if callable(push):
                _autonomy_mod.bind_feed(push)
            _autonomy_mod.feed(
                "info",
                "PC control ready"
                + (" · autonomous mode ON" if _autonomy_mod.get_mode("autonomous")
                   else " · autonomous mode off"),
            )
        except Exception as e:
            print(f"[JARVIS] control feed not bound: {e}")

        # Tell the device picker the exact rates the streams open at, from the
        # constants that actually open them — so it can never list a device that
        # cannot be opened at them.
        audio_devices.configure(SEND_SAMPLE_RATE, RECEIVE_SAMPLE_RATE)

        # Enumerate audio devices off-thread. The settings drawer must never pay
        # for host-API enumeration on the Qt thread.
        audio_devices.prefetch()

        # Start dashboard (optional — needs: pip install fastapi "uvicorn[standard]" cryptography)
        try:
            from dashboard.server import DashboardServer
            self._dashboard = DashboardServer()
            self._dashboard.set_connect_callback(self._on_phone_connected)
            asyncio.create_task(self._dashboard.serve())
            # Runs for the whole lifetime, not just inside an active session
            asyncio.create_task(self._process_dashboard_commands())
        except Exception as e:
            print(f"[Dashboard] Disabled: {e}")
            self._dashboard = None

        # Screen observer — lightweight foreground-window/activity sampler (no
        # pixels, no API calls) that gives the proactive engine live context.
        # Runs as a daemon thread for the whole process lifetime; disabled when
        # the screen_awareness privacy lever is off.
        if get_screen_awareness():
            try:
                self._screen_observer = ScreenObserver(
                    interval=get_observe_interval(),
                    max_events=6,
                )
                self._screen_observer.start()
                print("[JARVIS] Screen observer active "
                      f"({get_observe_interval()}s interval).")
            except Exception as e:
                print(f"[ScreenObserver] disabled: {e}")
                self._screen_observer = None
        else:
            print("[JARVIS] Screen observer off (screen_awareness=false).")

        while True:
            try:
                # No Gemini key yet → say so ONCE per wait and idle politely.
                # Never spam tracebacks or hammer the API (the Live session
                # itself requires Gemini; free providers only cover text).
                _key = _get_api_key()
                if not _key:
                    self.ui.set_state("SLEEPING")
                    if not getattr(self, "_missing_key_logged", False):
                        self._missing_key_logged = True
                        self.ui.write_log(
                            "SYS: API key required. Open Settings → API Keys."
                        )
                        print("[JARVIS] No API key configured — waiting for one.")
                    await asyncio.sleep(5)
                    continue
                self._missing_key_logged = False

                print("[JARVIS] Connecting...")
                self.ui.set_state("THINKING")
                _resumed_with = self._resume_handle is not None
                config = self._build_config()

                # Fresh client on every reconnect — avoids stale HTTP session state
                # v1alpha carries proactive audio; if it gets rejected we fall
                # back to v1beta.
                client = genai.Client(
                    api_key=_key,
                    http_options={"api_version": "v1alpha" if self._enhanced_live else "v1beta"}
                )

                async with (
                    client.aio.live.connect(model=LIVE_MODEL, config=config) as session,
                    asyncio.TaskGroup() as tg,
                ):
                    self.session          = session
                    self.audio_in_queue   = asyncio.Queue()
                    self.out_queue        = asyncio.Queue(maxsize=200)
                    self._turn_done_event = asyncio.Event()

                    # Reset transient state that must not carry over from a previous session
                    self._pending_vision       = None
                    self._vision_cam_active    = False
                    self._vision_close_pending = False
                    self._vision_busy          = False
                    self._vision_last_time     = 0.0
                    self._interrupted          = False

                    print("[JARVIS] Connected.")
                    if _resumed_with:
                        # Say it plainly: the difference between "it reconnected"
                        # and "it reconnected and still knows what we were doing"
                        # is the whole point, and it is invisible otherwise.
                        self.ui.write_log("SYS: Reconnected — conversation restored.")

                    # Wake word: if enabled, come up ASLEEP (mic gated, silent)
                    # until the user says "Hey Jarvis" or taps wake in the UI.
                    if self._wake_enabled:
                        self._ensure_wake_detector()
                        self._awake = False
                        self.ui.set_state("SLEEPING")
                        self.ui.write_log("SYS: JARVIS online — sleeping. Say 'Hey Jarvis' to wake me.")
                    else:
                        self._awake = True
                        self.ui.set_state("LISTENING")
                        self.ui.write_log("SYS: JARVIS online.")

                    if self._dashboard:
                        await self._dashboard.broadcast({"type": "status", "state": "active"})

                    self._reconnect_event.clear()  # ignore requests from before this session
                    tg.create_task(self._watch_reconnect())
                    tg.create_task(self._send_realtime())
                    tg.create_task(self._listen_audio())
                    tg.create_task(self._receive_audio())
                    tg.create_task(self._play_audio())
                    tg.create_task(self._run_system_monitor())
                    tg.create_task(self._run_background_monitor())
                    tg.create_task(self._run_trade_alerts())
                    tg.create_task(self._run_proactive_mode())
                    tg.create_task(self._run_glance_loop())
                    tg.create_task(self._run_learning_loop())
                    tg.create_task(self._run_sleep_watch())
                    if self._dashboard:
                        tg.create_task(self._relay_phone_audio())

                    # Morning briefing — fires once per process launch (if enabled).
                    # Skipped in wake-word mode: it comes up asleep, and a briefing
                    # would mean talking while "asleep".
                    if not self._briefing_sent and get_brief_enabled() and self._awake:
                        self._briefing_sent = True
                        tg.create_task(self._send_startup_briefing())

            except KeyboardInterrupt:
                raise
            except SystemExit:
                raise
            except BaseException as e:
                # Catches both Exception and BaseExceptionGroup (Python 3.11+
                # TaskGroup raises BaseExceptionGroup when tasks are cancelled
                # externally, which `except Exception` would miss, letting the
                # exception escape the while-loop and causing asyncio.run() to
                # start shutdown — resulting in "executor after shutdown" errors).
                # Voluntary reconnect (voice change) — not an error. Rebuild the
                # session immediately with no backoff and no scary logs.
                if _is_reconnect_signal(e):
                    print("[JARVIS] Voluntary reconnect requested.")
                    if not _keep_context_of(e):
                        # A deliberate clean slate (voice change) — drop the
                        # handle so the next connect really does start empty.
                        self._resume_handle = None
                    self._conn_backoff = 0
                    continue

                # A resumption handle the server will not accept — expired, or
                # belonging to a session it has since dropped. Without this, the
                # same dead handle would be replayed on every retry and the
                # assistant would never come back at all: the feature meant to
                # survive a reconnect would be the thing preventing one. Drop it
                # once and let the next attempt start clean.
                if _resumed_with and (
                    "resum" in str(e).lower()
                    or "handle" in str(e).lower()
                    or "INVALID_ARGUMENT" in str(e)
                    or "NOT_FOUND" in str(e)
                ):
                    print("[JARVIS] 🔗 Resumption handle rejected — starting a fresh session")
                    self.ui.write_log("SYS: Could not restore the conversation — starting fresh.")
                    self._resume_handle = None
                    self._conn_backoff = 0
                    continue

                err_str = str(e)
                print(f"[JARVIS] Error ({type(e).__name__}): {e}")
                traceback.print_exc()

                # Turn-taking / media / thinking knobs rejected by the server
                # (preview API drift) — drop them first, because they are the
                # newest fields and the cheapest to lose. Proactive audio is
                # tried again on the next pass if the error persists.
                if self._tuned_live and (
                    "INVALID_ARGUMENT" in err_str
                    or "Unknown name" in err_str
                    or "unexpected keyword" in err_str
                    or "realtime_input" in err_str.lower()
                    or "media_resolution" in err_str.lower()
                    or "thinking" in err_str.lower()
                ):
                    self._tuned_live = False
                    print("[JARVIS] Live tuning rejected — reconnecting without it.")
                    continue

                # Proactive audio rejected by the server (preview API drift) —
                # drop it and reconnect with the plain config.
                if self._enhanced_live and (
                    "INVALID_ARGUMENT" in err_str
                    or "proactiv" in err_str.lower()
                    or "Unknown name" in err_str
                    or "unexpected keyword" in err_str
                ):
                    self._enhanced_live = False
                    self.ui.write_log(
                        "SYS: Proactive audio unavailable — reconnecting without it."
                    )
                    continue

                # Invalid API key — stop hammering the API, prompt re-configuration
                if "API key not valid" in err_str or "1007" in err_str:
                    self.ui.write_log("ERR: API key invalid — please re-enter your key.")
                    self.ui.set_state("SLEEPING")
                    self.ui.prompt_reconfig()
                    # Wait for setup_save to flip _ready back (it does now).
                    # If the user dismisses setup without saving we keep
                    # waiting — no reconnect hammering with the same bad key.
                    while not self.ui._win._ready and not self.ui._closed:
                        await asyncio.sleep(1)
                    print("[JARVIS] New API key saved — reconnecting...")
                    _conn_backoff = 3
                    continue

                # Network / timeout errors — log clearly and back off
                is_net_err = any(k in err_str for k in (
                    "TimeoutError", "timed out", "getaddrinfo", "CancelledError",
                    "ConnectionRefusedError", "OSError", "Cannot connect",
                ))
                if is_net_err:
                    _conn_backoff = min(getattr(self, "_conn_backoff", 3) * 2, 60)
                    self._conn_backoff = _conn_backoff
                    self.ui.write_log(
                        f"NET: Connection failed — retrying in {_conn_backoff}s. "
                        "(a VPN may be required)"
                    )
                else:
                    self._conn_backoff = 3
            finally:
                self.session = None
                # Only save if there was a real conversation (≥3 turns)
                if len(self._session_log) >= 3:
                    asyncio.create_task(self._save_session_summary())

            self.set_speaking(False)
            self.ui.set_state("SLEEPING")

            if self._dashboard:
                await self._dashboard.broadcast({"type": "status", "state": "sleeping"})

            delay = getattr(self, "_conn_backoff", 3)
            print(f"[JARVIS] Reconnecting in {delay}s...")
            await asyncio.sleep(delay)

def main():
    _enable_file_log()
    ui = JarvisUI("face.png")

    def runner():
        ui.wait_for_api_key()
        jarvis = JarvisLive(ui)
        try:
            asyncio.run(jarvis.run())
        except KeyboardInterrupt:
            print("\n🔴 Shutting down...")
        except BaseException:
            # Never die silently: full traceback goes to logs/jarvis.log
            # (stdout/stderr are redirected there by _enable_file_log).
            traceback.print_exc()
            try:
                ui.write_log("ERR: JARVIS stopped unexpectedly — see logs/jarvis.log")
                ui.set_state("SLEEPING")
            except Exception:
                pass

    threading.Thread(target=runner, daemon=True).start()
    ui.root.mainloop()

if __name__ == "__main__":
    main()