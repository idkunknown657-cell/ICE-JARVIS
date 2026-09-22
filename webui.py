"""
webui.py — the new JARVIS interface: a native WebView2 window (via pywebview)
talking to the existing JARVIS core over a small, explicit bridge.

WHY A WEB UI
    The previous interface was 7,000 lines of PyQt that repainted the whole
    window 60 times a second. This one is ~100 KB of HTML/CSS/JS rendered by
    the operating system's own browser engine (WebView2 on Windows, WebKit
    elsewhere) with no bundled browser, no Electron, no WebEngine download.
    The face is a 2D canvas capped at 30 fps that pauses when the window is
    hidden, and every panel is plain DOM.

ARCHITECTURE (spec §20)
    UI (ui_web/)  ←js→  Bridge (JarvisAPI + _push)  ←py→  JARVIS core (main.py)
    The UI never contains AI logic; the core never touches the DOM. The bridge
    is the whole contract: main.py calls the JarvisUI duck-type below, the
    frontend calls JarvisAPI methods, events flow back as JS callbacks.

THREADING
    * pywebview calls JarvisAPI methods on its own worker threads. Anything
      slow (API key tests, device enumeration, plugin scans) runs there —
      the UI thread is the browser's, and it is never blocked.
    * Events from the core arrive on arbitrary threads and are marshalled to
      the JS side through a queue drained by the webview thread.
    * main.py's `while not self.ui._win._ready` loop needs a real window
      object with a `_ready` flag — provided by _WinShim below.
"""
from __future__ import annotations

import base64
import json
import os
import platform
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil

if platform.system() == "Windows":
    _WIN_HIDE: dict = {"creationflags": subprocess.CREATE_NO_WINDOW}
else:
    _WIN_HIDE: dict = {}

import webview  # pywebview

# Drag regions must match ANCESTORS of the event target, not only the exact
# target: the titlebar's children (logo, text, clock) receive the mousedown,
# and without this the frameless window cannot be moved at all.
webview.settings["DRAG_REGION_DIRECT_TARGET_ONLY"] = False

# ── project imports (same ones the old UI used) ─────────────────────────────
from memory.config_manager import (
    get_brief_enabled, save_brief_enabled,
    get_currency_code, save_currency_code,
    get_assistant_name, save_assistant_config,
    get_user_name,
    get_voice, save_voice,
    get_talk_cadence, save_talk_cadence,
    get_humor_level, save_humor_level,
    get_emotion_depth, save_emotion_depth,
    get_language_mode,
    get_thinking_enabled, save_thinking_enabled,
    get_verify_clicks, save_verify_clicks,
    get_goal_agent_enabled, save_goal_agent_enabled,
    get_goal_agent_auto, save_goal_agent_auto,
    get_memory_enabled, save_memory_enabled,
    get_track_activity, save_track_activity,
    get_screen_awareness, save_screen_awareness,
    get_screen_glance, save_screen_glance,
    get_screen_share_quality, save_screen_share_quality,
    get_observe_interval, save_observe_interval,
    get_narrate_actions, save_narrate_actions,
    get_push_to_talk_enabled, save_push_to_talk_enabled,
    get_wake_word_enabled, save_wake_word_enabled,
    get_media_resolution, save_media_resolution,
    get_input_device, save_input_device,
    get_output_device, save_output_device,
    get_proactive_enabled, save_proactive_enabled,
    get_free_providers, save_free_provider_key,
    get_hud_style,
)
from core import audio_devices
from core import confirm as confirm_gate
from core import undo as undo_stack
from core import api_verify
from core import free_providers
from core import updater
from core.wake_word import is_ready as wake_is_ready

BASE_DIR = (Path(sys.executable).resolve().parent
            if getattr(sys, "frozen", False)
            else Path(__file__).resolve().parent)
CONFIG_DIR = BASE_DIR / "config"
API_FILE = CONFIG_DIR / "api_keys.json"
APP_VERSION = "ICE"

APP_TITLE = "JARVIS"


# ════════════════════════════════════════════════════════════════════════════
#  Config helpers (mirror the old UI's atomic, never-raising pattern)
# ════════════════════════════════════════════════════════════════════════════
def _read_full_config() -> dict:
    try:
        return json.loads(API_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _coerce_key(v) -> str:
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return str(v).strip()
    return ""


def _sanitize_provider_rows(rows) -> list[dict]:
    out: list[dict] = []
    if isinstance(rows, (list, tuple)):
        for r in rows:
            if not isinstance(r, dict):
                continue
            try:
                name = str(r.get("name") or "").strip()
                if not name:
                    continue
                out.append({
                    "name": name,
                    "base_url": str(r.get("base_url") or "").strip().rstrip("/"),
                    "api_key": _coerce_key(r.get("api_key")),
                    "model": str(r.get("model") or "").strip(),
                })
            except Exception:
                continue
    return out


def _write_api_keys_impl(gemini_key: str, rows: list[dict]) -> int:
    """Atomic merge-write of api_keys.json. Safe from any thread."""
    os.makedirs(CONFIG_DIR, exist_ok=True)
    data = _read_full_config()
    if gemini_key:
        data["gemini_api_key"] = gemini_key
    rows = _sanitize_provider_rows(rows)
    data["free_providers"] = rows
    tmp = API_FILE.with_name(API_FILE.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=4, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, API_FILE)
    return sum(1 for p in rows if (p.get("api_key") or "").strip())


def _check_config() -> bool:
    if not API_FILE.exists():
        return False
    try:
        d = json.loads(API_FILE.read_text(encoding="utf-8"))
        return bool(d.get("gemini_api_key")) and bool(d.get("os_system"))
    except Exception:
        return False


# ════════════════════════════════════════════════════════════════════════════
#  Event pump: core threads → JS
# ════════════════════════════════════════════════════════════════════════════
class _EventPump:
    """Queues (name, payload) pairs and delivers them to the JS side from the
    webview thread. Batches coalesce identical cheap events so a chatty
    backend can never flood the bridge."""

    def __init__(self):
        self._q: list[tuple[str, object]] = []
        self._lock = threading.Lock()
        self._win = None
        self._dropped_level = None

    def attach(self, win):
        self._win = win

    def push(self, name: str, payload=None):
        with self._lock:
            if name == "audio_level":
                # coalesce: only the newest level matters
                self._dropped_level = payload
                if any(n == "audio_level" for n, _ in self._q):
                    return
            self._q.append((name, payload))
            if len(self._q) > 400:            # safety valve
                self._q = self._q[-200:]

    def flush(self):
        """Called repeatedly from the webview thread (see _drain_loop)."""
        if _WINDOW_GONE:
            return
        with self._lock:
            if not self._q:
                return
            batch, self._q = self._q[:60], self._q[60:]
        if self._win is None:
            return
        try:
            js = ";".join(
                f"window.__jarvisEvent({json.dumps(n)}, {json.dumps(p if p is not None else {})})"
                for n, p in batch
            )
            self._win.evaluate_js(js)
        except Exception:
            # window closing / not ready — drop the batch, the next one carries on
            pass


_PUMP = _EventPump()


def _drain_loop():
    # runs on a daemon thread; evaluate_js is thread-safe in pywebview 5/6
    while True:
        _PUMP.flush()
        time.sleep(0.03)


# ════════════════════════════════════════════════════════════════════════════
#  JarvisAPI — the surface the frontend calls
# ════════════════════════════════════════════════════════════════════════════
class JarvisAPI:
    """Every public method becomes window.pywebview.api.<name>() in JS.
    All methods return JSON-serialisable values and never raise."""

    def __init__(self):
        self.ui: "JarvisUI" | None = None       # back-reference, set by JarvisUI
        self._perf = {"cpu": 0.0, "mem": 0.0, "gpu": -1.0, "tmp": -1.0,
                      "net": 0.0, "uptime": "--", "procs": 0}
        self._perf_lock = threading.Lock()
        self._start_ts = time.time()
        self._last_net = psutil.net_io_counters()
        self._last_net_t = time.time()
        self._update_busy = False        # one update download at a time
        threading.Thread(target=self._perf_loop, daemon=True).start()

    # ── boot / initial state ────────────────────────────────────────────────
    def ready(self) -> bool:
        return True

    def get_initial(self) -> dict:
        cfg = _read_full_config()
        return {
            "configured": _check_config(),
            "assistant_name": (cfg.get("assistant_name") or "JARVIS"),
            "user_name": (cfg.get("user_name") or ""),
            "accent": (cfg.get("ui_color") or ""),
            "hud_style": _safe(get_hud_style, "face"),
            "compact": bool(cfg.get("compact", False)),
            "muted": bool(self.ui._muted) if self.ui else False,
            "wake": self._wake_state(),
            "screen": {"active": _safe(get_screen_awareness, False), "caption": ""},
            "memory_enabled": _safe(get_memory_enabled, True),
            "avatar": self._avatar_cfg(),
        }

    def _avatar_cfg(self) -> dict:
        cfg = _read_full_config()
        return {
            "mode": cfg.get("avatar_mode", "classic") or "classic",
            "size": float(cfg.get("avatar_size", 1.0)),
            "x": float(cfg.get("avatar_x", 0.0)),
            "y": float(cfg.get("avatar_y", 0.0)),
            "anim": float(cfg.get("avatar_anim", 1.0)),
            "expr": float(cfg.get("avatar_expr", 1.0)),
            "light": cfg.get("avatar_light", "") or "#38bdf8",
            "perf": cfg.get("avatar_perf", "balanced") or "balanced",
            "visible": bool(cfg.get("avatar_visible", True)),
        }

    def _wake_state(self) -> dict:
        ui = self.ui
        if ui and ui.wake_get_state:
            try:
                return ui.wake_get_state() or {}
            except Exception:
                pass
        return {"enabled": _safe(get_wake_word_enabled, False),
                "awake": True, "ready": wake_is_ready()}

    # ── conversation ────────────────────────────────────────────────────────
    def send_text(self, text: str) -> dict:
        text = (text or "").strip()
        if not text:
            return {"ok": False}
        ui = self.ui
        if ui and ui.on_text_command:
            threading.Thread(target=ui.on_text_command, args=(text,),
                             daemon=True).start()
        return {"ok": True}

    def interrupt(self) -> dict:
        ui = self.ui
        if ui and ui.on_interrupt:
            try:
                ui.on_interrupt()
            except Exception:
                pass
        return {"ok": True}

    def toggle_mute(self) -> dict:
        ui = self.ui
        if ui:
            ui._muted = not ui._muted
            _PUMP.push("muted", {"muted": ui._muted})
            _PUMP.push("log", {"line": "SYS: Microphone muted." if ui._muted
                                else "SYS: Microphone active."})
        return {"muted": bool(ui._muted) if ui else False}

    def set_ptt(self, held: bool) -> dict:
        """Call button press/release. Falls back to wake/sleep tap when
        push-to-talk is not enabled."""
        ui = self.ui
        if not ui:
            return {}
        if ui.on_push_to_talk:
            try:
                ui.on_push_to_talk(bool(held))
                return {"ptt": True}
            except Exception:
                pass
        if held and ui.on_wake_manual:
            try:
                ui.on_wake_manual()
            except Exception:
                pass
        return {"fallback": True}

    def quick_action(self, phrase: str) -> dict:
        return self.send_text(phrase)

    # ── attachments ─────────────────────────────────────────────────────────
    def open_file_dialog(self) -> dict | None:
        ui = self.ui
        if not ui or not ui._win:
            return None
        try:
            result = ui._win.create_file_dialog(
                webview.OPEN_DIALOG, allow_multiple=False)
            if result and len(result) > 0:
                p = Path(result[0])
                ui._current_file = str(p)
                return {"path": str(p), "name": p.name}
        except Exception:
            pass
        return None

    def clear_attach(self) -> dict:
        ui = self.ui
        if ui:
            ui._current_file = None
        return {"ok": True}

    # ── settings: read + write ──────────────────────────────────────────────
    def get_settings(self) -> dict:
        cfg = _read_full_config()
        return {
            "assistant_name": (cfg.get("assistant_name") or "JARVIS"),
            "user_name": (cfg.get("user_name") or ""),
            "app_version": _safe(updater.current_version, "?"),
            "voice": _safe(get_voice, "Aoede"),
            "talk_cadence": _safe(get_talk_cadence, "chatty"),
            "humor": _safe(get_humor_level, "subtle"),
            "emotion_depth": _safe(get_emotion_depth, "light"),
            "language_mode": _safe(get_language_mode, "auto"),
            "thinking": _safe(get_thinking_enabled, False),
            "verify_clicks": _safe(get_verify_clicks, True),
            "goal_agent": _safe(get_goal_agent_enabled, True),
            "goal_agent_auto": _safe(get_goal_agent_auto, False),
            "memory_enabled": _safe(get_memory_enabled, True),
            "track_activity": _safe(get_track_activity, True),
            "screen_awareness": _safe(get_screen_awareness, False),
            "screen_glance": _safe(get_screen_glance, True),
            "screen_share_quality": _safe(get_screen_share_quality, "medium"),
            "observe_interval": _safe(get_observe_interval, 12),
            "narrate_actions": _safe(get_narrate_actions, True),
            "ptt": _safe(get_push_to_talk_enabled, False),
            "brief": _safe(get_brief_enabled, True),
            "autostart": self._autostart_status(),
            "input_device": _safe(get_input_device, ""),
            "output_device": _safe(get_output_device, ""),
            "hud_style": _safe(get_hud_style, "face"),
            "accent": (cfg.get("ui_color") or ""),
            "compact": bool(cfg.get("compact", False)),
            "avatar": self._avatar_cfg(),
            "proactive": _safe(get_proactive_enabled, True),
            "media_resolution": _safe(get_media_resolution, "medium"),
            "currency": _safe(get_currency_code, "USD"),
            "update_source": (cfg.get("update_manifest_url") or ""),
            "update_pending": _safe(updater.pending, False),
            "update_pending_version": _safe(updater.pending_version, ""),
            "wake_word": self._wake_state(),
        }

    def save_setting(self, key: str, value) -> dict:
        """One switch, one save. `value` arrives JSON-typed from JS."""
        try:
            if key == "assistant_name":
                save_assistant_config((str(value) or "JARVIS").strip()[:40], get_user_name())
                if self.ui:
                    self.ui._assistant_name = (str(value) or "JARVIS").strip()[:40]
                _PUMP.push("config", {"assistant_name": (str(value) or "JARVIS").strip()[:40]})
            elif key == "user_name":
                save_assistant_config(get_assistant_name(), str(value or "").strip()[:40])
            elif key == "voice":
                save_voice(str(value))
                self._reconnect("new voice")
            elif key == "input_device":
                save_input_device(str(value or ""))
                self._reconnect("audio device", keep=True)
            elif key == "output_device":
                save_output_device(str(value or ""))
                self._reconnect("audio device", keep=True)
            elif key == "ptt":
                save_push_to_talk_enabled(bool(value))
                if self.ui and self.ui.on_push_to_talk:
                    try:
                        self.ui.on_push_to_talk(bool(value))
                    except Exception:
                        pass
            elif key == "wake_word":
                return self.wake_toggle(bool(value))
            elif key == "autostart":
                ok = self._set_autostart(bool(value))
                return {"ok": ok, "autostart": bool(value) if ok else self._autostart_status()}
            elif key == "brief":
                save_brief_enabled(bool(value))
            elif key == "currency":
                save_currency_code(str(value))
            elif key == "thinking":
                save_thinking_enabled(bool(value))
            elif key == "verify_clicks":
                save_verify_clicks(bool(value))
            elif key == "goal_agent":
                save_goal_agent_enabled(bool(value))
            elif key == "goal_agent_auto":
                save_goal_agent_auto(bool(value))
            elif key == "memory_enabled":
                save_memory_enabled(bool(value))
            elif key == "track_activity":
                save_track_activity(bool(value))
            elif key == "screen_awareness":
                save_screen_awareness(bool(value))
                _PUMP.push("screen", {"active": bool(value), "caption": ""})
            elif key == "screen_glance":
                save_screen_glance(bool(value))
            elif key == "screen_share_quality":
                save_screen_share_quality(str(value))
            elif key == "observe_interval":
                save_observe_interval(int(value))
            elif key == "narrate_actions":
                save_narrate_actions(bool(value))
            elif key == "talk_cadence":
                save_talk_cadence(str(value))
            elif key == "humor":
                save_humor_level(str(value))
            elif key == "emotion_depth":
                save_emotion_depth(str(value))
            elif key == "media_resolution":
                save_media_resolution(str(value))
            elif key == "hud_style":
                _write_config_key("hud_style", str(value))
            elif key == "accent":
                _write_config_key("ui_color", str(value))
                _PUMP.push("config", {"accent": str(value)})
            elif key == "compact":
                _write_config_key("compact", bool(value))
            elif key.startswith("avatar_"):
                _write_config_key(key, value)
                _PUMP.push("avatar", self._avatar_cfg())
            elif key == "proactive":
                save_proactive_enabled(bool(value))
            else:
                return {"ok": False, "err": "unknown key: " + str(key)}
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "err": str(e)[:120]}

    def _reconnect(self, reason: str, keep: bool = False):
        ui = self.ui
        if ui and ui.on_voice_change and reason == "new voice":
            try:
                ui.on_voice_change()
            except Exception:
                pass
        # audio device changes go through the same reconnect path
        if ui and hasattr(ui, "on_audio_device_change") and ui.on_audio_device_change and reason == "audio device":
            try:
                ui.on_audio_device_change()
            except Exception:
                pass

    # ── autostart (registry / LaunchAgent / .desktop) ───────────────────────
    def _autostart_status(self) -> bool:
        try:
            if platform.system() == "Windows":
                import winreg
                k = winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Run", 0,
                    winreg.KEY_READ)
                try:
                    winreg.QueryValueEx(k, "JARVIS")
                    return True
                finally:
                    winreg.CloseKey(k)
            else:
                return (BASE_DIR / ".autostart_flag").exists()
        except Exception:
            return False

    def _set_autostart(self, enable: bool) -> bool:
        try:
            if platform.system() == "Windows":
                import winreg
                k = winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Run", 0,
                    winreg.KEY_SET_VALUE)
                try:
                    if enable:
                        exe = sys.executable
                        # Frozen builds point at the exe itself; source runs
                        # at "python main.py".
                        script = "" if getattr(sys, "frozen", False) else str(BASE_DIR / "main.py")
                        cmd = f'"{exe}"' if not script else f'"{exe}" "{script}"'
                        winreg.SetValueEx(k, "JARVIS", 0, winreg.REG_SZ, cmd)
                    else:
                        try:
                            winreg.DeleteValue(k, "JARVIS")
                        except FileNotFoundError:
                            pass
                finally:
                    winreg.CloseKey(k)
                return True
            else:
                flag = BASE_DIR / ".autostart_flag"
                if enable:
                    flag.write_text("1")
                elif flag.exists():
                    flag.unlink()
                return True
        except Exception:
            return False

    # ── API keys (all network I/O off-thread — never blocks the UI) ────────
    def api_keys_get(self) -> dict:
        cfg = _read_full_config()
        return {
            "gemini": {"key": _coerce_key(cfg.get("gemini_api_key")),
                       "valid": None, "msg": ""},
            "providers": _sanitize_provider_rows(cfg.get("free_providers")),
        }

    def api_keys_test(self, data: dict) -> list:
        """Runs on pywebview's worker thread — the browser UI stays live."""
        gemini_key = _coerce_key((data or {}).get("gemini_key"))
        rows = _sanitize_provider_rows((data or {}).get("providers"))
        return [[name, ok, msg] for name, ok, msg in
                api_verify.verify_all(gemini_key, rows, timeout=8.0)]

    def api_keys_save(self, data: dict) -> dict:
        try:
            d = data or {}
            if d.get("disable"):
                rows = [p for p in _sanitize_provider_rows(_read_full_config().get("free_providers"))
                        if p["name"] != d["disable"]]
                _write_api_keys_impl("", rows)
                free_providers.reset_cooldowns()
                return {"ok": True}
            if d.get("provider"):
                pr = d["provider"]
                rows = _sanitize_provider_rows(_read_full_config().get("free_providers"))
                for p in rows:
                    if p["name"] == pr.get("name"):
                        p["api_key"] = _coerce_key(pr.get("api_key"))
                        if pr.get("base_url"):
                            p["base_url"] = str(pr["base_url"]).strip().rstrip("/")
                        if pr.get("model"):
                            p["model"] = str(pr["model"]).strip()
                        break
                else:
                    rows.append({
                        "name": str(pr.get("name") or "provider").strip(),
                        "base_url": str(pr.get("base_url") or "").strip().rstrip("/"),
                        "api_key": _coerce_key(pr.get("api_key")),
                        "model": str(pr.get("model") or "").strip(),
                    })
                _write_api_keys_impl("", rows)
                free_providers.reset_cooldowns()
                return {"ok": True}
            if d.get("gemini_key") is not None:
                _write_api_keys_impl(_coerce_key(d.get("gemini_key")),
                                     _sanitize_provider_rows(_read_full_config().get("free_providers")))
                return {"ok": True}
            return {"ok": False, "err": "nothing to save"}
        except Exception as e:
            return {"ok": False, "err": str(e)[:120]}

    def setup_test_key(self, key: str) -> dict:
        ok, msg = api_verify.verify_gemini(_coerce_key(key), timeout=8.0)
        return {"ok": ok, "msg": msg}

    def setup_save(self, data: dict) -> dict:
        try:
            d = data or {}
            key = _coerce_key(d.get("gemini_key"))
            if not key:
                return {"ok": False, "msg": "A Gemini key is required."}
            rows = _sanitize_provider_rows(d.get("providers"))
            _write_api_keys_impl(key, rows)
            cfg = _read_full_config()
            cfg["assistant_name"] = (str(d.get("assistant_name") or "JARVIS").strip() or "JARVIS")[:40]
            cfg["user_name"] = str(d.get("user_name") or "").strip()[:40]
            cfg.setdefault("os_system", platform.system().lower() or "windows")
            tmp = API_FILE.with_name(API_FILE.name + ".tmp")
            tmp.write_text(json.dumps(cfg, indent=4, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, API_FILE)
            free_providers.reset_cooldowns()
            if self.ui:
                self.ui._assistant_name = cfg["assistant_name"]
            _PUMP.push("config", {"assistant_name": cfg["assistant_name"]})
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "msg": str(e)[:160]}

    # ── self-update ─────────────────────────────────────────────────────────
    # Runs on pywebview worker threads (never the UI thread). Every path is
    # non-raising; updater.* returns result dicts by design.
    def updater_status(self) -> dict:
        try:
            cfg = _read_full_config()
            return {
                "version": updater.current_version(),
                "pending": updater.pending(),
                "pending_version": updater.pending_version(),
                "source": cfg.get("update_manifest_url") or updater.DEFAULT_MANIFEST_URL,
                "is_default_source": not bool(cfg.get("update_manifest_url")),
            }
        except Exception as e:
            return {"version": "?", "pending": False, "pending_version": "",
                    "source": "", "err": str(e)[:120]}

    def updater_check(self) -> dict:
        try:
            info = updater.check()
            # keep the last result so the UI can render it after reload
            if info.get("ok"):
                _write_config_key("update_last_check", {
                    "ts": time.strftime("%Y-%m-%d %H:%M"),
                    "latest": info.get("latest"),
                    "available": info.get("update_available"),
                    "notes": info.get("notes"),
                })
            return info
        except Exception as e:
            return {"ok": False, "update_available": False, "err": str(e)[:120]}

    def updater_download(self) -> dict:
        """Download on a worker thread and stream progress to the UI as
        update_progress events. The call returns immediately with
        {"ok": True, "started": True}; the finish event carries the result.
        A second call while one runs is ignored (busy flag) so a double-click
        can never start two parallel downloads."""
        if self._update_busy:
            return {"ok": False, "err": "download already in progress"}
        self._update_busy = True

        def _run():
            try:
                res = updater.download_and_stage(
                    progress=lambda f: _PUMP.push("update_progress", {
                        "stage": "download", "frac": max(0.0, min(1.0, float(f)))}))
            except Exception as e:
                res = {"ok": False, "err": str(e)[:120]}
            self._update_busy = False
            payload = {"stage": "done", "ok": bool(res.get("ok")),
                       "err": res.get("err", ""),
                       "version": res.get("version") or ""}
            _PUMP.push("update_progress", payload)

        threading.Thread(target=_run, daemon=True).start()
        return {"ok": True, "started": True}

    def updater_apply(self) -> dict:
        """Stage → bat → hand over. The window closes so the swap can run."""
        try:
            res = updater.apply_on_restart()
            if res.get("ok") and res.get("relaunch"):
                def _close():
                    time.sleep(0.6)
                    try:
                        if self.ui and self.ui._win and self.ui._win.win:
                            self.ui._win.win.destroy()
                    except Exception:
                        pass
                threading.Thread(target=_close, daemon=True).start()
            return res
        except Exception as e:
            return {"ok": False, "err": str(e)[:120]}

    def updater_set_source(self, url: str) -> dict:
        try:
            u = str(url or "").strip()
            if u and not u.startswith("https://"):
                return {"ok": False, "err": "must be an https:// URL"}
            _write_config_key("update_manifest_url", u)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "err": str(e)[:120]}

    # ── memory browser ──────────────────────────────────────────────────────
    def memory_get(self) -> dict:
        try:
            from memory.memory_manager import all_entries_for_ui
            return all_entries_for_ui() or {}
        except Exception:
            return {}

    def memory_forget(self, cat: str, key: str) -> dict:
        try:
            from memory.memory_manager import forget
            forget(key, category=cat)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "err": str(e)[:120]}

    # ── plugins ─────────────────────────────────────────────────────────────
    def plugins_get(self) -> list:
        ui = self.ui
        if ui and ui.get_plugins:
            try:
                return ui.get_plugins() or []
            except Exception:
                pass
        return []

    def plugin_set_enabled(self, name: str, enabled: bool) -> dict:
        try:
            from memory.config_manager import save_plugin_enabled
            save_plugin_enabled(str(name), bool(enabled))
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "err": str(e)[:120]}

    def open_plugins_folder(self) -> dict:
        p = BASE_DIR / "plugins"
        p.mkdir(exist_ok=True)
        self.open_path(str(p))
        return {"ok": True}

    def create_shortcut(self) -> dict:
        """Create a desktop shortcut for JARVIS (named ICE)."""
        try:
            if platform.system() != "Windows":
                return {"ok": False, "err": "Desktop shortcuts only supported on Windows"}
            
            import win32com.client
            import win32con
            import pythoncom

            # The real Desktop: %USERPROFILE%\Desktop can be a shadow *file* on
            # OneDrive-linked accounts. Ask the shell for the actual folder.
            desktop = None
            try:
                import ctypes
                buf = ctypes.create_unicode_buffer(260)
                r = ctypes.windll.shell32.SHGetFolderPathW(None, 0x10, None, 0, buf)
                p = Path(buf.value)
                if r == 0 and p.exists() and p.is_dir():
                    desktop = p
            except Exception:
                desktop = None
            if desktop is None:
                try:
                    import win32api
                    desktop = Path(win32api.SHGetSpecialFolderPath(0, win32con.CSIDL_DESKTOPDIRECTORY))
                except Exception:
                    desktop = None
            if desktop is None:
                desktop = Path(os.environ.get("USERPROFILE", "")) / "Desktop"

            shortcut_path = desktop / "ICE.lnk"
            
            # Determine target (exe or python main.py). From source on Windows
            # use pythonw.exe (windowless) so the icon doesn't pop a cmd window.
            if getattr(sys, "frozen", False):
                target = sys.executable
                args = ""
            else:
                exe = Path(sys.executable)
                pw = exe.with_name("pythonw.exe")
                if platform.system() == "Windows" and pw.exists():
                    target = str(pw)
                else:
                    target = str(exe)
                args = str(BASE_DIR / "main.py")
            
            pythoncom.CoInitialize()
            try:
                shell = win32com.client.Dispatch("WScript.Shell")
                shortcut = shell.CreateShortCut(str(shortcut_path))
                shortcut.Targetpath = target
                if args:
                    shortcut.Arguments = args
                shortcut.WorkingDirectory = str(BASE_DIR)
                shortcut.IconLocation = str(BASE_DIR / "assets" / "jarvis.ico")
                shortcut.Description = "ICE JARVIS - Voice AI Assistant"
                shortcut.save()
            finally:
                pythoncom.CoUninitialize()
            
            return {"ok": True, "path": str(shortcut_path)}
        except Exception as e:
            return {"ok": False, "err": str(e)[:120]}

    def open_path(self, path: str) -> dict:
        try:
            if platform.system() == "Windows":
                os.startfile(path)  # noqa: S606
            elif platform.system() == "Darwin":
                subprocess.Popen(["open", path], **_WIN_HIDE)
            else:
                subprocess.Popen(["xdg-open", path], **_WIN_HIDE)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "err": str(e)[:120]}

    # ── wake word ───────────────────────────────────────────────────────────
    def wake_toggle(self, enable: bool) -> dict:
        ui = self.ui
        if ui and ui.on_wake_toggle:
            try:
                token = ui.on_wake_toggle(bool(enable))
                state = self._wake_state()
                _PUMP.push("wake", state)
                return {"token": token, "state": state}
            except Exception:
                pass
        save_wake_word_enabled(bool(enable))
        state = self._wake_state()
        _PUMP.push("wake", state)
        return {"token": "enabled" if enable else "disabled", "state": state}

    def wake_install(self) -> dict:
        ui = self.ui
        if ui and ui.on_wake_install:
            try:
                ok, msg = ui.on_wake_install()
                state = self._wake_state()
                _PUMP.push("wake", state)
                return {"ok": ok, "msg": msg}
            except Exception as e:
                return {"ok": False, "msg": str(e)[:160]}
        return {"ok": False, "msg": "Wake word backend not connected yet."}

    def wake_manual(self) -> dict:
        ui = self.ui
        if ui and ui.on_wake_manual:
            try:
                ui.on_wake_manual()
            except Exception:
                pass
        state = self._wake_state()
        _PUMP.push("wake", state)
        return state

    # ── devices / perf ──────────────────────────────────────────────────────
    def devices_get(self) -> dict:
        try:
            return {"input": audio_devices.list_devices("input"),
                    "output": audio_devices.list_devices("output")}
        except Exception:
            return {"input": [], "output": []}

    def perf_get(self) -> dict:
        with self._perf_lock:
            return dict(self._perf)

    def _perf_loop(self):
        while True:
            try:
                cpu = psutil.cpu_percent(interval=None)
                mem = psutil.virtual_memory().percent
                nc = psutil.net_io_counters()
                now = time.time()
                dt = now - self._last_net_t
                net = 0.0
                if dt > 0:
                    net = ((nc.bytes_sent - self._last_net.bytes_sent) +
                           (nc.bytes_recv - self._last_net.bytes_recv)) / dt / (1024 * 1024)
                self._last_net = nc
                self._last_net_t = now
                up = int(now - self._start_ts)
                uptime = f"{up // 3600}h {up % 3600 // 60}m"
                with self._perf_lock:
                    self._perf.update(cpu=cpu, mem=mem, net=round(net, 2),
                                      uptime=uptime,
                                      procs=len(psutil.pids()))
                if self._perf.get("gpu", -1) < 0:
                    g = _gpu_percent()
                    if g >= 0:
                        self._perf["gpu"] = g
                t = _cpu_temp()
                if t > 0:
                    self._perf["tmp"] = t
                _PUMP.push("perf", dict(self._perf))
            except Exception:
                pass
            time.sleep(2.0)

    # ── confirm gate / undo ─────────────────────────────────────────────────
    def confirm_answer(self, accepted: bool) -> dict:
        confirm_gate.resolve(bool(accepted))
        # always clear the banner, even if the gate was never bound (the UI
        # must never be left with a stuck modal)
        _PUMP.push("confirm_hide", {})
        return {"ok": True}

    def undo_last(self) -> dict:
        try:
            if not undo_stack.can_undo():
                return {"ok": False}
            msg = undo_stack.undo_last()
            _PUMP.push("toast", {"text": "Undone — " + str(msg)[:80], "kind": "ok"})
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "err": str(e)[:120]}

    # ── window / background ─────────────────────────────────────────────────
    def window(self, op: str) -> dict:
        ui = self.ui
        # NOTE: the real pywebview Window lives at ui._win.win — the shim only
        # carries the _ready flag. Calling minimize()/maximize() on the shim is
        # what made those buttons silently do nothing.
        w = ui._win.win if (ui and ui._win) else None
        if w is None:
            return {}
        try:
            if op == "minimize":
                w.minimize()
            elif op == "maximize":
                if getattr(w, "maximized", False):
                    w.restore()
                else:
                    w.maximize()
            elif op == "fullscreen":
                w.fullscreen = not w.fullscreen
            elif op == "announce_ready":
                ui._win._ready = True
                _PUMP.push("state", _last_state.get("state", "LISTENING"))
            return {"ok": True}
        except Exception:
            return {"ok": False}

    def enter_background(self) -> dict:
        ui = self.ui
        if ui:
            ui.enter_background()
            _PUMP.push("app_hidden", {})   # pause avatar rendering while hidden
        return {"ok": True}

    def stop_camera(self) -> dict:
        ui = self.ui
        if ui:
            ui.stop_camera_stream()
        return {"ok": True}


# ════════════════════════════════════════════════════════════════════════════
#  helpers
# ════════════════════════════════════════════════════════════════════════════
def _safe(fn, default):
    try:
        return fn()
    except Exception:
        return default


def _write_config_key(key: str, value) -> None:
    cfg = _read_full_config()
    cfg[key] = value
    tmp = API_FILE.with_name(API_FILE.name + ".tmp")
    tmp.write_text(json.dumps(cfg, indent=4, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, API_FILE)


def _gpu_percent() -> float:
    try:
        import pynvml
        pynvml.nvmlInit()
        h = pynvml.nvmlDeviceGetHandleByIndex(0)
        return float(pynvml.nvmlDeviceGetUtilizationRates(h).gpu)
    except Exception:
        return -1.0


def _cpu_temp() -> float:
    try:
        temps = psutil.sensors_temperatures()
        for name in ("coretemp", "k10temp", "cpu_thermal", "acpitz"):
            if name in temps and temps[name]:
                return float(temps[name][0].current)
    except Exception:
        pass
    return -1.0


_last_state = {"state": "LISTENING"}
_WINDOW_GONE = False   # True once webview.start() has returned (window closed)


# ════════════════════════════════════════════════════════════════════════════
#  JarvisUI — the duck-type main.py and plugins program against
# ════════════════════════════════════════════════════════════════════════════
class _WinShim:
    """The tiny slice of 'window' main.py needs: a `_ready` flag."""

    def __init__(self):
        self._ready = False
        self.win = None


class JarvisUI:
    """Drop-in replacement for ui.JarvisUI. Same attribute contract as the
    old class (main.py sets callbacks, calls set_state/write_log/… from any
    thread); everything funnels into the event pump."""

    def __init__(self, face_path: str = "", size=None):
        self._muted = False
        self._current_file: str | None = None
        self._assistant_name = _read_full_config().get("assistant_name") or "JARVIS"

        # callback slots (assigned by JarvisLive in main.py)
        self.on_text_command = None
        self.on_remote_clicked = None
        self.on_interrupt = None
        self.on_voice_change = None
        self.on_audio_device_change = None
        self.on_push_to_talk = None
        self.ptt_hold = None
        self.on_wake_toggle = None
        self.on_wake_manual = None
        self.on_wake_install = None
        self.wake_get_state = None
        self.wake_is_ready = wake_is_ready
        self.get_plugins = None
        self.get_plugin_settings = None
        self.request_say = None

        self._api = JarvisAPI()
        self._api.ui = self
        self._win = _WinShim()
        self._win_shim = self._win
        self._closed = False

        self._win.win = webview.create_window(
            APP_TITLE,
            url=str(BASE_DIR / "ui_web" / "index.html"),
            js_api=self._api,
            width=(size[0] if size else 1020), height=(size[1] if size else 680),
            min_size=(760, 540),
            background_color="#05070d",
            text_select=True,
            frameless=True,
            easy_drag=False,          # dragging via .pywebview-drag-region only
            on_top=False,
        )
        _PUMP.attach(self._win.win)

    # ── mainloop ────────────────────────────────────────────────────────────
    class _RootShim:
        def __init__(self, ui_ref):
            self._ui = ui_ref

        def mainloop(self):
            global _WINDOW_GONE
            threading.Thread(target=_drain_loop, daemon=True).start()
            webview.start(debug=False)
            _WINDOW_GONE = True
            self._ui._closed = True

        def protocol(self, *_):
            pass

    @property
    def root(self):
        return JarvisUI._RootShim(self)

    # ── core → UI (thread-safe via the pump) ────────────────────────────────
    def set_state(self, state: str):
        _last_state["state"] = state
        _PUMP.push("state", state)

    def write_log(self, text: str):
        _PUMP.push("log", {"line": text})

    def set_audio_level(self, level: float):
        try:
            _PUMP.push("audio_level", {"level": float(level)})
        except Exception:
            pass

    def push_visemes(self, frames, hop: float, at: float):
        """Backend posts (level, openness, width) frames + the wall-clock time
        they begin to sound. The frontend renders the *current* frame, so we
        run a tiny scheduler here instead of shipping the whole timeline."""
        try:
            if not frames:
                return
            hop = max(1e-3, float(hop))
            at = float(at)

            def _worker():
                now = time.time()
                if at > now + 0.05:
                    time.sleep(min(at - now, 0.25))
                # emit at ~25 Hz while the batch is current; the pump coalesces
                i = 0
                n = len(frames)
                start = max(at, time.time())
                while i < n:
                    target = start + i * hop
                    now = time.time()
                    if now < target:
                        time.sleep(min(target - now, 0.05))
                    elif now - target > hop * 4:
                        i = int((now - start) / hop) + 1
                        continue
                    f = frames[i]
                    _PUMP.push("viseme_now", {"level": f[0], "openness": f[1], "width": f[2]})
                    i += 1

            threading.Thread(target=_worker, daemon=True).start()
        except Exception:
            pass

    def show_content(self, title: str, text: str):
        _PUMP.push("content", {"title": str(title)[:60], "text": str(text)[:6000]})

    def show_review(self, title: str, summary: str, findings, unclear=None):
        _PUMP.push("review", {"title": str(title)[:60], "summary": str(summary)[:2000],
                              "findings": list(findings or [])[:24],
                              "unclear": list(unclear or [])[:12]})

    def show_quiz(self, topic: str, questions, grade=None):
        # quizzes are a plugin surface; render as content (the old UI's quiz
        # cards were never wired to a settings page either)
        lines = [f"Quiz: {topic}"]
        for q in (questions or [])[:10]:
            if isinstance(q, dict):
                lines.append("• " + str(q.get("question", q)))
            else:
                lines.append("• " + str(q))
        self.show_content("QUIZ", "\n".join(lines))

    def hide_quiz(self):
        pass

    def show_confirm(self, title: str, detail: str):
        _PUMP.push("confirm", {"title": str(title)[:120], "detail": str(detail)[:400]})

    def hide_confirm(self):
        _PUMP.push("confirm_hide", {})

    def prompt_reconfig(self):
        _PUMP.push("setup", {})

    def notify_phone_connected(self):
        _PUMP.push("phone", {})

    def set_screen_status(self, active: bool, caption: str = ""):
        _PUMP.push("screen", {"active": bool(active), "caption": str(caption or "")[:24]})

    def show_camera_frame(self, img_bytes: bytes):
        try:
            _PUMP.push("camera_frame", {"b64": base64.b64encode(img_bytes).decode("ascii")})
        except Exception:
            pass

    def start_camera_stream(self):
        _PUMP.push("camera_start", {})

    def stop_camera_stream(self):
        _PUMP.push("camera_stop", {})

    def glance(self, dx: float, dy: float, hold: float = 1.1):
        pass   # cosmetic only; the 2D face glances on its own

    # ── properties the core reads ───────────────────────────────────────────
    @property
    def muted(self) -> bool:
        return self._muted

    @muted.setter
    def muted(self, v: bool):
        if v != self._muted:
            self._muted = bool(v)
            _PUMP.push("muted", {"muted": self._muted})

    @property
    def current_file(self) -> str | None:
        return self._current_file

    @property
    def assistant_name(self) -> str:
        return self._assistant_name

    # ── background / tray ───────────────────────────────────────────────────
    def enter_background(self):
        """Hide the window, keep the core alive, offer a tray icon."""
        try:
            if self._win.win:
                self._win.win.hide()
        except Exception:
            pass
        threading.Thread(target=self._tray_loop, daemon=True).start()

    def _tray_loop(self):
        try:
            import pystray
            from PIL import Image, ImageDraw
        except Exception:
            return
        if getattr(self, "_tray_running", False):
            return
        self._tray_running = True

        def _make_icon():
            img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
            d = ImageDraw.Draw(img)
            d.ellipse((6, 6, 58, 58), outline=(56, 189, 248, 255), width=4)
            d.ellipse((22, 22, 42, 42), fill=(56, 189, 248, 255))
            return img

        def _show(icon, item):
            try:
                if self._win.win:
                    self._win.win.show()
                    _PUMP.push("app_shown", {})   # resume avatar rendering
            except Exception:
                pass

        def _quit(icon, item):
            icon.stop()
            os._exit(0)

        try:
            icon = pystray.Icon("JARVIS", _make_icon(), "JARVIS — running in background")
            icon.menu = pystray.Menu(
                pystray.MenuItem("Show JARVIS", _show, default=True),
                pystray.MenuItem("Quit", _quit),
            )
            icon.run_detached()
        except Exception:
            pass

    # ── misc legacy shims ───────────────────────────────────────────────────
    def wait_for_api_key(self):
        """Blocks the backend runner thread until the window reports ready.
        Mirrors the old contract exactly (main.py's runner calls this)."""
        while not self._win._ready and not self._closed:
            time.sleep(0.1)

    def start_speaking(self):
        self.set_state("SPEAKING")

    def stop_speaking(self):
        if not self._muted:
            self.set_state("LISTENING")


# ════════════════════════════════════════════════════════════════════════════
#  Standalone launch (for development): python webui.py
# ════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    ui = JarvisUI("face.png")
    ui.root.mainloop()
