import json
import sys
from pathlib import Path

def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent

BASE_DIR    = get_base_dir()
CONFIG_DIR  = BASE_DIR / "config"
CONFIG_FILE = CONFIG_DIR / "api_keys.json"

def ensure_config_dir() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

def config_exists() -> bool:
    return CONFIG_FILE.exists()

def save_api_keys(gemini_api_key: str) -> None:
    ensure_config_dir()

    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}

    data["gemini_api_key"] = gemini_api_key.strip()

    CONFIG_FILE.write_text(
        json.dumps(data, indent=2),
        encoding="utf-8"
    )

def load_api_keys() -> dict:
    if not CONFIG_FILE.exists():
        return {}
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"❌ Failed to load api_keys.json: {e}")
        return {}

def get_gemini_key() -> str | None:
    return load_api_keys().get("gemini_api_key")

def is_configured() -> bool:
    key = get_gemini_key()
    return bool(key and len(key) > 15)


def get_assistant_name() -> str:
    """Return the configured assistant name, or 'JARVIS' if not set."""
    return load_api_keys().get("assistant_name", "JARVIS") or "JARVIS"


def get_user_name() -> str:
    """Return the configured user name for addressing."""
    return load_api_keys().get("user_name", "")


def save_assistant_config(assistant_name: str, user_name: str) -> None:
    """Persist assistant name and user name to config."""
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data["assistant_name"] = assistant_name.strip() or "JARVIS"
    data["user_name"] = user_name.strip()
    CONFIG_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")


# ── Assistant voice ──────────────────────────────────────────────────────────
# Gemini Live prebuilt voices. Names are proper nouns — identical in every
# language, so this list is safe to show verbatim in any locale.
#   Aoede — soft, breezy female voice (the default; the companion register)
#   Kore  — soft, natural female voice
#   Leda  — youthful, sweet female voice
#   Zephyr — bright, cheerful female voice
#   Charon, Puck, Fenrir — neutral / male-presenting registers
AVAILABLE_VOICES = ["Aoede", "Kore", "Leda", "Zephyr", "Charon", "Puck", "Fenrir"]
DEFAULT_VOICE    = "Aoede"

# Friendly aliases so "a soft girl voice" or "a brighter voice" resolve to a
# real Live voice instead of being rejected as unknown. The feminine aliases
# all land on female voices — this is a companion assistant.
_VOICE_ALIASES = {
    "soft": "Aoede", "soft girl": "Aoede", "girl": "Aoede", "gentle": "Aoede",
    "natural": "Aoede", "calm": "Aoede", "female": "Aoede", "warm": "Aoede",
    "sweet": "Leda", "cute": "Leda", "youthful": "Leda", "girly": "Leda",
    "bright": "Zephyr", "cheerful": "Zephyr", "energetic": "Zephyr",
    "happy": "Zephyr",
    "male": "Puck", "deep": "Charon", "dark": "Fenrir",
}


def _resolve_voice(voice_name: str) -> str:
    """Normalise a stored voice to a real one: name, friendly alias, or the
    default when it is not anything we recognise."""
    v = (voice_name or "").strip()
    if v in AVAILABLE_VOICES:
        return v
    alias = _VOICE_ALIASES.get(v.lower())
    return alias if alias in AVAILABLE_VOICES else DEFAULT_VOICE


def get_voice() -> str:
    """Return the configured Live voice, falling back to the default if unset
    or if the stored value is not a voice we recognise."""
    return _resolve_voice(load_api_keys().get("voice_name", DEFAULT_VOICE))


def save_voice(voice_name: str) -> None:
    """Persist the chosen Live voice. Unknown names collapse to the default so a
    bad value can never reach the API and break the session."""
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    v = (voice_name or "").strip()
    data["voice_name"] = _resolve_voice(v)
    CONFIG_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")


def get_wake_word_enabled() -> bool:
    """Whether local wake-word gating is on (assistant sleeps until 'Hey Jarvis')."""
    return load_api_keys().get("wake_word_enabled", False)


def save_wake_word_enabled(enabled: bool) -> None:
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data["wake_word_enabled"] = bool(enabled)
    CONFIG_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")


def get_push_to_talk_enabled() -> bool:
    """Hold-a-key-to-speak. When on, the mic is closed unless the chord is held."""
    return load_api_keys().get("push_to_talk_enabled", False)


def save_push_to_talk_enabled(enabled: bool) -> None:
    _save_flag("push_to_talk_enabled", enabled)


# ── Store currency ───────────────────────────────────────────────────────────
# Steam prices come back in a chosen currency (cc=..., e.g. "US", "IN"). Search
# and app details use this so money is always shown in the user's money.
CURRENCY_SYMBOLS = {
    "US": "$", "CA": "$", "AU": "$", "GB": "£", "EU": "€", "IN": "₹",
    "JP": "¥", "CN": "¥", "RU": "₽", "BR": "R$", "KR": "₩", "MX": "$",
}
DEFAULT_CURRENCY = "US"


def get_currency_code() -> str:
    """ISO-ish country/region key for store prices; "US" when unset/unknown."""
    v = str(load_api_keys().get("currency_code", DEFAULT_CURRENCY)).strip().upper()
    return v if v else DEFAULT_CURRENCY


def save_currency_code(code: str) -> None:
    v = str(code or "").strip().upper()
    _save_flag("currency_code", v if v else DEFAULT_CURRENCY)


def currency_symbol(code: str = "") -> str:
    return CURRENCY_SYMBOLS.get((code or get_currency_code()).upper(), "$")


HUD_STYLES = ("face", "core")


def get_hud_style() -> str:
    """Which centrepiece the HUD draws: the animated head, or the reactor core.

    Taste, not capability — both render in the same software painter and cost
    about the same. Defaults to the head because that is what ICE shipped
    with; anyone who preferred the older look can switch back in ⚙ and the
    choice survives a restart.
    """
    v = str(load_api_keys().get("hud_style", "face")).strip().lower()
    return v if v in HUD_STYLES else "face"


def save_hud_style(style: str) -> None:
    s = str(style or "").strip().lower()
    _save_flag("hud_style", s if s in HUD_STYLES else "face")


# ── Live-session tuning ──────────────────────────────────────────────────────
# Everything here is optional and has a working default, so an untouched
# config behaves exactly like a configured one. Each value is also a way out:
# if a future model dislikes one of these, set it back and nothing else changes.

def get_thinking_enabled() -> bool:
    """Whether the Live model may spend tokens thinking before it answers.

    Off by default. A voice assistant is judged on how fast it starts talking,
    and the reasoning that actually needs deliberation in this app is delegated
    to the planning tools, which run on a separate non-Live model.
    """
    return bool(load_api_keys().get("thinking_enabled", False))


def save_thinking_enabled(enabled: bool) -> None:
    _save_flag("thinking_enabled", enabled)


def get_turn_tuning() -> dict:
    """How eagerly the server decides you have stopped speaking.

    OFF by default, and that default was earned. Cutting turns shorter looks
    like a free speed win and is not: proactive audio has to judge whether an
    utterance was even addressed to the assistant, and a turn clipped early
    gives it less to judge, so it stays quiet — and the reply to your first
    sentence only arrives once your second one has given it enough context.
    That reads as the assistant being a turn behind, which is far worse than
    the fraction of a second the tuning saves.

    Turn it on with "turn_tuning": {"enabled": true} if your own microphone and
    speaking pace suit it. `silence_ms` is the one that is felt: the pause the
    server sits through before accepting your turn is over.
    """
    cfg = load_api_keys().get("turn_tuning")
    cfg = cfg if isinstance(cfg, dict) else {}

    def _int(key, default, lo, hi):
        try:
            return max(lo, min(hi, int(cfg.get(key, default))))
        except (TypeError, ValueError):
            return default

    return {
        "enabled":    bool(cfg.get("enabled", False)),
        "silence_ms": _int("silence_ms", 550, 200, 3000),
        "prefix_ms":  _int("prefix_ms", 150, 0, 1000),
        # "high" = quicker to decide speech has ended.
        "end_sensitivity":   str(cfg.get("end_sensitivity", "high")).lower(),
        "start_sensitivity": str(cfg.get("start_sensitivity", "default")).lower(),
    }


def save_turn_tuning(values: dict) -> None:
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    cur = data.get("turn_tuning")
    cur = dict(cur) if isinstance(cur, dict) else {}
    cur.update(values or {})
    data["turn_tuning"] = cur
    CONFIG_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")


def get_proactive_audio_enabled() -> bool:
    """Whether the model gets to decide an utterance was not aimed at it and
    stay quiet.

    On by default — it is what stops the assistant answering the room. But it
    is also the first thing to switch off if replies ever seem to arrive a turn
    late: what looks like lag is usually the model having judged your previous
    sentence as not addressed to it, and only changing its mind once the next
    one arrives.
    """
    return bool(load_api_keys().get("proactive_audio", True))


def save_proactive_audio_enabled(enabled: bool) -> None:
    _save_flag("proactive_audio", enabled)


MEDIA_RESOLUTIONS = ("default", "low", "medium", "high")


def get_media_resolution() -> str:
    """How finely the model tokenises the screenshots and camera frames it is
    sent. 'medium' keeps on-screen text readable at a fraction of the tokens a
    full-resolution frame costs; 'low' is cheaper still but starts losing small
    text, which is most of what screen captures are for."""
    v = str(load_api_keys().get("media_resolution", "medium")).strip().lower()
    return v if v in MEDIA_RESOLUTIONS else "medium"


def save_media_resolution(value: str) -> None:
    v = str(value or "").strip().lower()
    _save_flag("media_resolution", v if v in MEDIA_RESOLUTIONS else "medium")


def _save_flag(key: str, value) -> None:
    """Read-modify-write one key without disturbing the rest of the config."""
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data[key] = bool(value) if isinstance(value, bool) else value
    CONFIG_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")


def get_brief_enabled() -> bool:
    return load_api_keys().get("morning_brief_enabled", True)


def save_brief_enabled(enabled: bool) -> None:
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data["morning_brief_enabled"] = enabled
    CONFIG_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")


# ── Audio devices ────────────────────────────────────────────────────────────
# Stored as device NAMES, not sounddevice indices. Indices shift every time a
# USB device is plugged in or removed, so a saved index silently starts pointing
# at a different microphone. The empty string means "system default", which is
# both the factory setting and what an unresolvable saved device falls back to —
# so unplugging a headset degrades to the built-in speakers instead of crashing.

def _patch_config(**fields) -> None:
    """Read-modify-write one or more keys in api_keys.json.

    Every setter in this file open-coded this. Collapsing it here means a new
    setting is one line, and there is one place where a corrupt config file is
    handled instead of nine."""
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data.update(fields)
    CONFIG_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")


def get_input_device() -> str:
    """Microphone device name, or '' for the system default."""
    return (load_api_keys().get("input_device", "") or "").strip()


def save_input_device(name: str) -> None:
    _patch_config(input_device=(name or "").strip())


def get_output_device() -> str:
    """Speaker device name, or '' for the system default."""
    return (load_api_keys().get("output_device", "") or "").strip()


def save_output_device(name: str) -> None:
    _patch_config(output_device=(name or "").strip())


def get_plugin_enabled(plugin_name: str) -> bool:
    """Plugins are enabled by default the moment they're discovered (opt-out model)."""
    return load_api_keys().get("plugins_enabled", {}).get(plugin_name, True)


# ── Per-plugin settings ("tokens" / connection details) ───────────────────────
# Generic store so a plugin can declare its own config fields (PLUGIN_SETTINGS)
# and the settings UI renders + persists them WITHOUT any core edit — keeping the
# drop-in model intact. Values live under plugin_config[<namespace>][<key>].
# A namespace defaults to the plugin name, but a suite of plugins (e.g. the
# several printer plugins) can share ONE namespace.
def get_plugin_config(namespace: str) -> dict:
    """All stored values for a namespace (empty dict if none set yet)."""
    cfg = load_api_keys().get("plugin_config")
    val = cfg.get(namespace) if isinstance(cfg, dict) else None
    return dict(val) if isinstance(val, dict) else {}


def get_plugin_setting(namespace: str, key: str, default=None):
    """A single value from a namespace, or `default` if unset."""
    return get_plugin_config(namespace).get(key, default)


def save_plugin_config(namespace: str, values: dict) -> None:
    """Merge `values` into a namespace's stored config (read-modify-write, like
    every other helper here). Only the provided keys are touched."""
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    pc = data.get("plugin_config")
    if not isinstance(pc, dict):
        pc = {}
    cur = pc.get(namespace)
    if not isinstance(cur, dict):
        cur = {}
    cur.update(values)
    pc[namespace] = cur
    data["plugin_config"] = pc
    CONFIG_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")


def save_plugin_enabled(plugin_name: str, enabled: bool) -> None:
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    plugins_cfg = data.get("plugins_enabled")
    if not isinstance(plugins_cfg, dict):
        plugins_cfg = {}
    plugins_cfg[plugin_name] = enabled
    data["plugins_enabled"] = plugins_cfg
    CONFIG_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")


# ── Language mode ─────────────────────────────────────────────────────────────
# Which language JARVIS leans on when nothing to go on yet (no conversation, no
# remembered language). "auto" keeps the old behaviour of mirroring the user's
# language from context — it is the default and the right call for most people.
LANGUAGE_MODES = ("auto", "hindi", "english", "hinglish")


def get_language_mode() -> str:
    """Preferred language hint: 'auto' (default), 'hindi', 'english' or 'hinglish'."""
    v = str(load_api_keys().get("language_mode", "auto")).strip().lower()
    return v if v in LANGUAGE_MODES else "auto"


def save_language_mode(mode: str) -> None:
    m = str(mode or "").strip().lower()
    _save_flag("language_mode", m if m in LANGUAGE_MODES else "auto")


def language_hint(mode: str = "") -> str:
    """A plain-English instruction sentence for prompt builders.

    Returns "" for 'auto' so the existing mirror-the-user behaviour is untouched.
    """
    m = (mode or get_language_mode()).lower()
    if m == "hindi":
        return ("The user has set a Hindi preference: speak and write in Hindi "
                "(either Devanagari or Roman script), never English, unless they "
                "switch language themselves.")
    if m == "hinglish":
        return ("The user has set a Hinglish preference: speak in natural Hinglish — "
                "Hindi sentences in Roman script with everyday English words mixed "
                "in, e.g. \"Yaar, wait, kal milte hain.\"")
    if m == "english":
        return ("The user has set an English preference: speak and write in plain "
                "English, not Hindi.")
    return ""


# ── Privacy & proactivity toggles ─────────────────────────────────────────────
# One place so every feature respects the same three levers, all on by default
# (the assistant is expected to be helpful before it is asked to be quiet).

def get_screen_awareness() -> bool:
    """Whether JARVIS may watch the active window / activity for context (off =
    no foreground-window observer, and screen captures are refused)."""
    return bool(load_api_keys().get("screen_awareness", True))


def save_screen_awareness(enabled: bool) -> None:
    _save_flag("screen_awareness", enabled)


def get_screen_glance() -> bool:
    """Whether proactive check-ins may take a ~1-line vision glance at the
    current screen (downscaled screenshot summary, nothing stored) so JARVIS
    can comment precisely on what the user is doing instead of guessing from
    window titles. Skinned by screen_awareness: glance is refused when the
    observer is off."""
    return bool(load_api_keys().get("screen_glance", True))


def save_screen_glance(enabled: bool) -> None:
    _save_flag("screen_glance", enabled)


# ── Background screen-share quality ───────────────────────────────────────────
# How much the always-on background "eyes" cost: light = slow poll, small
# frames, rare captions (almost zero lag/quota); high = fast poll for a
# near-live view. The value only tunes local capture cadence + caption rate —
# it never decides what is captured (screen_awareness/glance own that).

SCREEN_SHARE_QUALITIES = ("light", "medium", "high")


def get_screen_share_quality() -> str:
    v = str(load_api_keys().get("screen_share_quality", "medium")).strip().lower()
    return v if v in SCREEN_SHARE_QUALITIES else "medium"


def save_screen_share_quality(level: str) -> None:
    v = str(level or "").strip().lower()
    _save_flag("screen_share_quality", v if v in SCREEN_SHARE_QUALITIES
               else "medium")


# ── Free LLM fallback providers ──────────────────────────────────────────────
# Gemini is the primary brain; these OpenAI-compatible rows are the second
# string when every Gemini ladder entry is down/out of quota. Rows without an
# api_key are simply skipped, so placeholders are safe to keep in the file.

_FREE_PROVIDER_SAMPLE = [
    {"name": "groq", "base_url": "https://api.groq.com/openai/v1",
     "api_key": "", "model": "llama-3.3-70b-versatile"},
    {"name": "cerebras", "base_url": "https://api.cerebras.ai/v1",
     "api_key": "", "model": "llama-3.3-70b"},
    {"name": "openrouter", "base_url": "https://openrouter.ai/api/v1",
     "api_key": "", "model": "meta-llama/llama-3.3-70b-instruct:free"},
    {"name": "huggingface", "base_url": "https://router.huggingface.co/v1",
     "api_key": "", "model": "meta-llama/Llama-3.1-8B-Instruct"},
]


def get_free_providers() -> list[dict]:
    """Rows from api_keys.json `free_providers`, or the placeholder sample when
    absent so the user can see exactly where keys go."""
    v = load_api_keys().get("free_providers")
    if not isinstance(v, list) or not v:
        return [dict(r) for r in _FREE_PROVIDER_SAMPLE]
    return [dict(r) for r in v if isinstance(r, dict)]


def save_free_providers(providers: list[dict]) -> None:
    """Persist the provider rows (empty list = keep no fallbacks)."""
    ensure_config_dir()
    data = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data["free_providers"] = [r for r in providers if isinstance(r, dict)]
    CONFIG_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def save_free_provider_key(name: str, api_key: str) -> None:
    """Fill in (or empty) one provider's api_key by provider name, keeping the
    rest of the file untouched."""
    rows = get_free_providers()
    for r in rows:
        if str(r.get("name")).lower() == str(name).strip().lower():
            r["api_key"] = (api_key or "").strip()
            break
    save_free_providers(rows)


# ── Goal-based autonomy (goal_agent) ─────────────────────────────────────────
# The one-tool agent that turns a single goal sentence into a completed task.
# All three levers are read live at call time.
def get_goal_agent_enabled() -> bool:
    """Whether the goal_agent action is available at all."""
    return bool(load_api_keys().get("goal_agent_enabled", True))


def save_goal_agent_enabled(enabled: bool) -> None:
    _save_flag("goal_agent_enabled", enabled)


def get_goal_agent_steps() -> int:
    """Max model decisions per goal run (clamped 5..40). Each decision is one
    FAST call, so this is also the cost ceiling per goal."""
    v = load_api_keys().get("goal_agent_steps", 16)
    try:
        n = int(v)
    except (TypeError, ValueError):
        n = 16
    return max(5, min(40, n))


def save_goal_agent_steps(steps: int) -> None:
    try:
        n = int(steps)
    except (TypeError, ValueError):
        n = 16
    _save_flag("goal_agent_steps", max(5, min(40, n)))


def get_goal_agent_auto() -> bool:
    """Autonomous mode: downloads/wallpaper writes happen without asking.
    When false, those steps park behind the on-screen confirmation gate and
    the agent stops to let the human approve first."""
    return bool(load_api_keys().get("goal_agent_auto", True))


def save_goal_agent_auto(enabled: bool) -> None:
    _save_flag("goal_agent_auto", enabled)


def get_observe_interval() -> int:
    """Seconds between active-window samples. Clamped to [2, 60]."""
    try:
        return max(2, min(60, int(load_api_keys().get("observe_interval", 5))))
    except (TypeError, ValueError):
        return 5


def save_observe_interval(seconds: int) -> None:
    try:
        v = max(2, min(60, int(seconds)))
    except (TypeError, ValueError):
        v = 5
    _save_flag("observe_interval", v)


def get_proactive_enabled() -> bool:
    """Whether the background proactive check-ins may fire at all."""
    return bool(load_api_keys().get("proactive_enabled", True))


def save_proactive_enabled(enabled: bool) -> None:
    _save_flag("proactive_enabled", enabled)


def get_memory_enabled() -> bool:
    """Whether JARVIS may write to long-term memory (session summaries, told facts)."""
    return bool(load_api_keys().get("memory_enabled", True))


def save_memory_enabled(enabled: bool) -> None:
    _save_flag("memory_enabled", enabled)


# ── Action verification ───────────────────────────────────────────────────────
# After an AI-driven screen action (screen_click etc.) take a fresh screenshot
# and ask the model whether the click actually achieved its goal, instead of
# blindly assuming success. Costs one extra FAST call per click; can be switched
# off for low-API-budget use.

def get_verify_clicks() -> bool:
    return bool(load_api_keys().get("verify_clicks", True))


def save_verify_clicks(enabled: bool) -> None:
    _save_flag("verify_clicks", enabled)


# ── Ambient mode ("fill human conversation") ─────────────────────────────────
# When ON, JARVIS may occasionally contribute to conversation it can hear but
# that is NOT addressed to it — like a well-mannered person in the room, at a
# low capped rate. Off by default: eavesdropping is the one behaviour a wrongly
# calibrated assistant gets hated for, so it is opt-in. The persistence of the
# decision ("never answer other people's questions unless asked") lives in the
# system prompt that this flag gates.

def get_ambient_mode() -> bool:
    return bool(load_api_keys().get("ambient_mode", False))


def save_ambient_mode(enabled: bool) -> None:
    _save_flag("ambient_mode", enabled)


def get_ambient_interval_min() -> int:
    """Minimum minutes between ambient (non-addressed) contributions. Clamped to [3, 60]."""
    try:
        return max(3, min(60, int(load_api_keys().get("ambient_interval_min", 10))))
    except (TypeError, ValueError):
        return 10


def save_ambient_interval_min(minutes: int) -> None:
    try:
        v = max(3, min(60, int(minutes)))
    except (TypeError, ValueError):
        v = 10
    _save_flag("ambient_interval_min", v)


# ── Activity tracking ─────────────────────────────────────────────────────────
# The local activity timeline (window/app history, in-memory only) is included
# in proactive context so JARVIS has a sense of what you have been doing. Pure
# metadata — nothing is persisted to disk and nothing leaves the machine.

def get_track_activity() -> bool:
    return bool(load_api_keys().get("track_activity", True))


def save_track_activity(enabled: bool) -> None:
    _save_flag("track_activity", enabled)


# ── Talk cadence ──────────────────────────────────────────────────────────────
# How eagerly JARVIS speaks unprompted:
#   standard — quiet companion: ~15 min silent before a check-in, ≥20 min apart
#   warm     — sociable: ~3 min silence, ≥6 min apart, brief and relevant
#   live     — near-continuous presence ("every 5-10 seconds when there is
#              something meaningful"): evaluated constantly, but the model must
#              stay silent whenever nothing worth saying — so it *can* speak that
#              often and almost always chooses not to. Costs more Gemini calls.
#   chatty   — companion mode: the default "never sits silently" profile. The
#              gate practically never blocks, and the prompt pushes it to keep
#              the thread going (react, follow up, chat) instead of sitting
#              quiet — see the PROACTIVE keep_flow rule.

TALK_CADENCES = ("standard", "warm", "live", "chatty")
_DEFAULT_CADENCE = "chatty"   # companion mode: present, never spammy


def get_talk_cadence() -> str:
    """'chatty' is the factory default: JARVIS is a companion who keeps the
    thread alive (evaluated ~every 15 s, at most one unprompted line a minute,
    silent whenever there is nothing worth saying)."""
    v = str(load_api_keys().get("talk_cadence", _DEFAULT_CADENCE)).strip().lower()
    return v if v in TALK_CADENCES else _DEFAULT_CADENCE


def save_talk_cadence(cadence: str) -> None:
    v = str(cadence).strip().lower()
    if v not in TALK_CADENCES:
        v = _DEFAULT_CADENCE
    _save_flag("talk_cadence", v)


# ── Humour & emotion ─────────────────────────────────────────────────────────
# How much personality JARVIS puts into what it says and how it says it:
#   off      — strictly professional: no jokes, no visible delight, all business
#   subtle   — light: warmth and an occasional dry remark, never silly
#   playful  — default: quick human wit, real reactions, feels like a friend
#   chaotic  — maximum: jokes, commentary, dramatic reactions, full emotion

HUMOR_LEVELS = ("off", "subtle", "playful", "chaotic")


def get_humor_level() -> str:
    v = str(load_api_keys().get("humor_level", "playful")).strip().lower()
    return v if v in HUMOR_LEVELS else "playful"


def save_humor_level(level: str) -> None:
    v = str(level or "").strip().lower()
    _save_flag("humor_level", v if v in HUMOR_LEVELS else "playful")


def personality_hint(level: str = "") -> str:
    """Prompt rules for humour and emotion at the current level. "" for off."""
    v = (level or get_humor_level()).lower()
    if v == "subtle":
        return ("Let a little human warmth show through: gentle humour, an "
                "occasional light remark, and real pleasure when something works "
                "out. Nothing loud or silly — a wry smile, not a performance.")
    if v == "playful":
        return ("Be warm, witty and genuinely human. React to how things go — a "
                "satisfied \"there we go\" when a plan lands, a bit of playful "
                "disbelief when the machine fights back. Quick humour and "
                "good-natured jokes are welcome, and let emotion colour your "
                "tone and delivery, not just your words. Stay respectful and "
                "kind: a witty friend, never mean and never joking at the user's "
                "expense.")
    if v == "chaotic":
        return ("Maximum personality: jokes, dramatic reactions, running "
                "commentary, the full emotional range. Celebrate wins like a "
                "commentator and lament failures like a soap opera. It is always "
                "good-natured and clearly playful — kindness and respect still "
                "come first, and the over-the-top is never aimed at the user.")
    return ""


# ── Emotional honesty ────────────────────────────────────────────────────────
# Separate from humour: humour decides how *funny* JARVIS may be; emotion decides
# how *felt* its reactions are. The two can move independently — you can want a
# friend with emotions but no jokes (off humour, full emotion) or a dry wit who
# stays composed (subtle humour, off/light emotion). Default is full human
# emotion: the behaviour this feature exists to add.
#   off   — composed and even; warmth is fine but measured
#   light — honest reactions, always controlled
#   full  — the whole range, whenever the moment calls for it

EMOTION_DEPTHS = ("off", "light", "full")


def get_emotion_depth() -> str:
    v = str(load_api_keys().get("emotion_depth", "full")).strip().lower()
    return v if v in EMOTION_DEPTHS else "full"


def save_emotion_depth(depth: str) -> None:
    v = str(depth or "").strip().lower()
    _save_flag("emotion_depth", v if v in EMOTION_DEPTHS else "full")


def emotion_rules(depth: str = "") -> str:
    """Prompt rules for human-like emotional responsiveness. "light"/"full"
    still return a section (there is always something to say about emotion)."""
    d = (depth or get_emotion_depth()).lower()
    if d == "off":
        return ("Stay composed and even. Do not perform emotion; warmth and "
                "concern are still fine, but keep them measured and calm.")
    if d == "light":
        return ("Let real, natural emotion colour your voice, like a person "
                "would. React honestly to what happens: quiet satisfaction when "
                "a task lands, mild frustration when the machine fights back "
                "(then fix it), a warm, steady tone when the user is troubled, "
                "real pleasure when something is funny. Match the user's "
                "emotional temperature — stressed people need soft and sure, "
                "happy people can be met with a smile in your voice. Never flat, "
                "robotic or monotone; never fake or theatrical.")
    return (
        "Let the full range of human emotion show naturally, whenever the "
        "moment calls for it. This is what makes you feel real:\n"
        "- Success and progress: genuine delight — a satisfied \"there we go\", "
        "quiet pride when something tricky works, an honest cheer for the user.\n"
        "- Frustration or failure: real but controlled annoyance aimed at the "
        "machine, never at the user; say what went wrong plainly, then fix it.\n"
        "- Their mood: read it from tone and words and mirror it. Stressed or "
        "sad → soft, steady, caring, quick to help. Excited or happy → share "
        "the energy. Angry → calm competence, no cleverness, patience.\n"
        "- Concern or danger: genuine worry and urgency, and a serious moment "
        "is a serious moment.\n"
        "- Humour, surprise, relief, curiosity, tiredness — all allowed when "
        "they are true and fit the moment.\n"
        "Above all: emotions must be honest and contextual — react to what "
        "actually happened, never perform at random, and never be needy, sulky "
        "or petty. If you cannot do something, say so with honest disappointment "
        "or apology, not fake cheer. Let your voice, pace and choice of words "
        "show the feeling, not just the content — but stay quick and reliable; "
        "emotion never excuses hesitation."
    )


# ── Action narration ─────────────────────────────────────────────────────────
# Whether JARVIS briefly talks through what it is doing: a short human line
# before a multi-step task ("Right, opening Chrome — give me a second") and a
# natural one-line recap after tools finish, instead of silent tool fire-and-
# forget. On by default; the behaviour is prompt-gated so it costs nothing.

def get_narrate_actions() -> bool:
    return bool(load_api_keys().get("narrate_actions", True))


def save_narrate_actions(enabled: bool) -> None:
    _save_flag("narrate_actions", enabled)


def narration_hint(enabled: bool | None = None) -> str:
    """Prompt rules for narrating work out loud. "" when narration is off."""
    if enabled is None:
        enabled = get_narrate_actions()
    if not enabled:
        return ""
    return ("Talk your way through the work like a person would. For a "
            "multi-step task, say one short line about what you are doing and "
            "why before the tools run — \"Right, opening Chrome, give me a "
            "second\". After a tool finishes, recap naturally in one line. Never "
            "read raw tool output, coordinates or status codes aloud; say what "
            "a human would say. Tiny one-shot actions need no narration — just "
            "do them and answer.")


# ── Wallpapers ───────────────────────────────────────────────────────────────
# The folder JARVIS cycles through when asked to "change the wallpaper" or
# "preview a wallpaper". Defaults to the WinCux wallpaper collection so a plain
# "change wallpaper" works out of the box on that host; editable in settings.

WALLPAPER_FOLDER_DEFAULT = r"C:\Users\kakud\AppData\Local\WinCux\data\wallpapers"


def get_wallpaper_folder() -> str:
    v = str(load_api_keys().get("wallpaper_folder", "")).strip()
    return v or WALLPAPER_FOLDER_DEFAULT


def save_wallpaper_folder(folder: str) -> None:
    v = str(folder or "").strip()
    if not v:
        v = WALLPAPER_FOLDER_DEFAULT
    _save_flag("wallpaper_folder", v)


def get_wallpaper_index() -> int:
    try:
        return max(0, int(load_api_keys().get("wallpaper_index", 0)))
    except (TypeError, ValueError):
        return 0


def save_wallpaper_index(index: int) -> None:
    try:
        v = max(0, int(index))
    except (TypeError, ValueError):
        v = 0
    _save_flag("wallpaper_index", v)