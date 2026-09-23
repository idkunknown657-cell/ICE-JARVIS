"""
tests/test_webui_bridge.py — the contract the new interface must hold.

main.py talks to JarvisUI through a fixed duck-type surface; the frontend
talks to JarvisAPI through window.pywebview.api. These tests pin both sides
so a future edit cannot silently break the bridge in either direction.
pywebview itself is NOT started here — no window, no event loop.
"""
import json
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import webui  # noqa: E402


class _FakeApi:
    """Stands in for the pywebview JS-bridge object."""

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def _call(*a, **k):
            self.calls.append((name, a, k))
            return True
        return _call


def _fresh_ui():
    """A JarvisUI that never opens a window: webview.create_window mocked."""
    with mock.patch.object(webui.webview, "create_window", return_value=_FakeApi()):
        ui = webui.JarvisUI("face.png")
    return ui


class _ConfigGuard:
    """Backs up api_keys.json and restores it byte-for-byte afterwards, so
    tests can exercise config writes without touching real settings."""

    def __init__(self):
        self.path = webui.API_FILE
        self.backup = None
        self.had_file = self.path.exists()

    def __enter__(self):
        if self.had_file:
            self.backup = self.path.read_bytes()
        return self

    def __exit__(self, *exc):
        try:
            if self.backup is not None:
                self.path.write_bytes(self.backup)
            elif self.path.exists():
                self.path.unlink()
        except Exception:
            pass
        return False


class TestDuckTypeContract(unittest.TestCase):
    """Everything main.py sets or calls on self.ui must exist."""

    ATTRS = [
        "on_text_command", "on_remote_clicked", "on_interrupt", "on_voice_change",
        "on_audio_device_change", "on_push_to_talk", "ptt_hold",
        "on_wake_toggle", "on_wake_manual", "on_wake_install",
        "wake_get_state", "get_plugins", "get_plugin_settings", "request_say",
    ]

    def test_callback_slots_exist(self):
        ui = _fresh_ui()
        for a in self.ATTRS:
            self.assertTrue(hasattr(ui, a), "missing callback slot: " + a)

    def test_main_py_calls_exist(self):
        ui = _fresh_ui()
        for m in ("set_state", "write_log", "show_content", "push_visemes",
                  "set_audio_level", "set_screen_status", "show_confirm",
                  "hide_confirm", "prompt_reconfig", "notify_phone_connected",
                  "start_camera_stream", "stop_camera_stream", "show_camera_frame",
                  "show_review", "show_quiz", "hide_quiz", "wait_for_api_key",
                  "glance", "start_speaking", "stop_speaking"):
            self.assertTrue(callable(getattr(ui, m, None)), "missing method: " + m)

    def test_muted_property_roundtrip(self):
        ui = _fresh_ui()
        self.assertFalse(ui.muted)
        ui.muted = True
        self.assertTrue(ui.muted)

    def test_current_file_roundtrip(self):
        ui = _fresh_ui()
        self.assertIsNone(ui.current_file)
        ui._current_file = "x.txt"
        self.assertEqual(ui.current_file, "x.txt")

    def test_win_shim_ready_flag(self):
        ui = _fresh_ui()
        self.assertFalse(ui._win._ready)
        ui._win._ready = True
        self.assertTrue(ui._win._ready)

    def test_wait_for_api_key_returns_when_ready(self):
        ui = _fresh_ui()
        t = threading.Timer(0.2, lambda: setattr(ui._win, "_ready", True))
        t.start()
        ui.wait_for_api_key()          # must not hang


class TestEventPump(unittest.TestCase):
    def test_push_then_flush_delivers_js(self):
        pump = webui._EventPump()
        win = _FakeApi()
        pump.attach(win)
        pump.push("state", "LISTENING")
        pump.push("log", {"line": "SYS: hi"})
        pump.flush()
        self.assertEqual(len(win.calls), 1)
        name, args, _ = win.calls[0]
        self.assertEqual(name, "evaluate_js")
        self.assertIn("__jarvisEvent", args[0])

    def test_audio_level_coalesces(self):
        pump = webui._EventPump()
        win = _FakeApi()
        pump.attach(win)
        for lvl in range(20):
            pump.push("audio_level", {"level": lvl / 20})
        pump.flush()
        # all 20 coalesced into ONE evaluate_js call
        self.assertEqual(len(win.calls), 1)
        js = win.calls[0][1][0]
        self.assertEqual(js.count("audio_level"), 1)

    def test_queue_cap(self):
        pump = webui._EventPump()
        for i in range(1000):
            pump.push("log", {"line": str(i)})
        self.assertLessEqual(len(pump._q), 400)

    def test_flush_without_window_is_safe(self):
        pump = webui._EventPump()
        pump.push("state", "THINKING")
        pump.flush()          # must not raise


class TestJarvisAPI(unittest.TestCase):
    def setUp(self):
        self.ui = _fresh_ui()
        self.api = self.ui._api

    def test_every_api_method_returns_jsonable(self):
        json.dumps(self.api.get_initial())
        json.dumps(self.api.get_settings())
        json.dumps(self.api.devices_get())
        json.dumps(self.api.plugins_get())
        json.dumps(self.api.api_keys_get())
        json.dumps(self.api.memory_get())
        json.dumps(self.api.perf_get())

    def test_send_text_reaches_callback(self):
        seen = []
        self.ui.on_text_command = lambda t: seen.append(t)
        self.api.send_text("hello there")
        # dispatched on a worker thread — give it a moment
        for _ in range(20):
            if seen:
                break
            threading.Event().wait(0.05)
        self.assertEqual(seen, ["hello there"])

    def test_send_text_empty_is_noop(self):
        seen = []
        self.ui.on_text_command = lambda t: seen.append(t)
        self.api.send_text("   ")
        threading.Event().wait(0.1)
        self.assertEqual(seen, [])

    def test_interrupt_and_mute(self):
        called = []
        self.ui.on_interrupt = lambda: called.append(1)
        self.api.interrupt()
        self.assertEqual(called, [1])
        r = self.api.toggle_mute()
        self.assertTrue(r["muted"])
        self.assertTrue(self.ui.muted)
        r = self.api.toggle_mute()
        self.assertFalse(r["muted"])

    def test_ptt_fallback_without_handler(self):
        r = self.api.set_ptt(True)
        self.assertEqual(r, {"fallback": True})

    def test_save_setting_unknown_key(self):
        r = self.api.save_setting("not_a_real_key", 1)
        self.assertFalse(r["ok"])

    def test_save_setting_screen_awareness_roundtrip(self):
        from memory.config_manager import get_screen_awareness
        with _ConfigGuard():
            before = get_screen_awareness()
            try:
                r = self.api.save_setting("screen_awareness", not before)
                self.assertTrue(r["ok"])
                self.assertEqual(get_screen_awareness(), not before)
            finally:
                webui.save_screen_awareness(before)

    def test_api_keys_save_and_get(self):
        with _ConfigGuard():
            r = self.api.api_keys_save({"gemini_key": "test-key-xyz"})
            self.assertTrue(r["ok"])
            data = self.api.api_keys_get()
            self.assertEqual(data["gemini"]["key"], "test-key-xyz")

    def test_api_keys_always_lists_sample_providers(self):
        with _ConfigGuard():
            rows = self.api.api_keys_get()["providers"]
            names = {p["name"] for p in rows}
            self.assertTrue({"groq", "cerebras", "openrouter", "huggingface"} <= names)

    def test_api_keys_disable_keeps_row_delete_removes(self):
        with _ConfigGuard():
            self.api.api_keys_save({
                "provider": {"name": "testprov", "base_url": "https://x.example",
                             "api_key": "k123", "model": "m"}})
            r = self.api.api_keys_save({"disable": "testprov"})
            self.assertTrue(r["ok"])
            rows = self.api.api_keys_get()["providers"]
            kept = [p for p in rows if p["name"] == "testprov"]
            self.assertEqual(len(kept), 1)          # row survives a disable
            self.assertFalse(kept[0]["enabled"])
            self.assertEqual(kept[0]["api_key"], "k123")   # key survives too
            r = self.api.api_keys_save({"enable": "testprov"})
            self.assertTrue(r["ok"])
            rows = self.api.api_keys_get()["providers"]
            self.assertTrue([p for p in rows if p["name"] == "testprov"][0]["enabled"])
            r = self.api.api_keys_save({"delete": "testprov"})
            self.assertTrue(r["ok"])
            rows = self.api.api_keys_get()["providers"]
            self.assertFalse(any(p["name"] == "testprov" for p in rows))

    def test_api_keys_primary_selection(self):
        with _ConfigGuard():
            r = self.api.api_keys_save({"primary": "groq"})
            self.assertTrue(r["ok"])
            data = self.api.api_keys_get()
            self.assertEqual(data["primary"], "groq")
            self.assertEqual(data["providers"][0]["name"], "groq")  # primary first

    def test_api_keys_clear_gemini_and_add_provider_keep_key(self):
        with _ConfigGuard():
            self.api.api_keys_save({"gemini_key": "keep-me"})
            self.api.api_keys_save({"provider": {"name": "p1", "base_url": "https://x.example/v1",
                                                 "api_key": "k", "model": "m"}})
            # saving a provider must NOT wipe the stored Gemini key
            self.assertEqual(webui._read_full_config().get("gemini_api_key"), "keep-me")
            # clearing with an explicit empty string must actually clear
            r = self.api.api_keys_save({"gemini_key": ""})
            self.assertTrue(r["ok"])
            self.assertEqual(webui._read_full_config().get("gemini_api_key"), "")

    def test_setup_save_allows_empty_key(self):
        with _ConfigGuard():
            r = self.api.setup_save({"gemini_key": "", "assistant_name": "ICE"})
            self.assertTrue(r["ok"])       # first run must work with no keys
            cfg = webui._read_full_config()
            self.assertEqual(cfg.get("gemini_api_key"), "")
            self.assertTrue(cfg.get("os_system"))

    def test_setup_save_writes_config(self):
        with _ConfigGuard():
            r = self.api.setup_save({
                "gemini_key": "setup-key-1",
                "assistant_name": "ICE",
                "user_name": "Tester",
            })
            self.assertTrue(r["ok"])
            cfg = webui._read_full_config()
            self.assertEqual(cfg.get("gemini_api_key"), "setup-key-1")
            self.assertEqual(cfg.get("assistant_name"), "ICE")

    def test_setup_test_key_never_raises(self):
        r = self.api.setup_test_key("")     # no key → graceful message
        self.assertFalse(r["ok"])
        self.assertTrue(isinstance(r["msg"], str))

    def test_confirm_answer_safe_without_pending(self):
        r = self.api.confirm_answer(True)
        self.assertEqual(r, {"ok": True})

    def test_undo_without_history(self):
        r = self.api.undo_last()
        self.assertFalse(r["ok"])

    def test_quick_action_is_send_text(self):
        seen = []
        self.ui.on_text_command = lambda t: seen.append(t)
        self.api.quick_action("Open YouTube.")
        for _ in range(20):
            if seen:
                break
            threading.Event().wait(0.05)
        self.assertEqual(seen, ["Open YouTube."])

    def test_window_ops_safe_without_real_window(self):
        # _FakeApi accepts any call; nothing may raise
        for op in ("minimize", "maximize", "fullscreen", "announce_ready"):
            self.api.window(op)

    def test_wake_state_jsonable_without_backend(self):
        json.dumps(self.api._wake_state())


class TestSanitizers(unittest.TestCase):
    def test_coerce_key(self):
        self.assertEqual(webui._coerce_key("  k  "), "k")
        self.assertEqual(webui._coerce_key(123), "123")
        self.assertEqual(webui._coerce_key({"a": 1}), "")
        self.assertEqual(webui._coerce_key(None), "")

    def test_sanitize_rows(self):
        rows = webui._sanitize_provider_rows([
            {"name": "groq", "base_url": "https://x/", "api_key": "k", "model": "m"},
            "garbage",
            {"name": ""},
            {"name": "n", "base_url": 5, "api_key": 9, "model": None},
        ])
        self.assertEqual(len(rows), 2)
        # fields are str-cast so a malformed file can never feed a non-string
        # into the bridge (same behaviour the old settings panel relied on)
        self.assertEqual(rows[1]["base_url"], "5")
        self.assertEqual(rows[1]["api_key"], "9")
        self.assertEqual(rows[1]["model"], "")


class TestCoreToUIPush(unittest.TestCase):
    def setUp(self):
        self.ui = _fresh_ui()
        webui._PUMP._q.clear()

    def tearDown(self):
        webui._PUMP._q.clear()

    def test_set_state_pushes(self):
        self.ui.set_state("THINKING")
        names = [n for n, _ in webui._PUMP._q]
        self.assertIn("state", names)

    def test_write_log_pushes(self):
        self.ui.write_log("JARVIS: hello")
        names = [n for n, _ in webui._PUMP._q]
        self.assertIn("log", names)

    def test_show_confirm_pushes(self):
        self.ui.show_confirm("Shutdown", "Are you sure?")
        names = [n for n, _ in webui._PUMP._q]
        self.assertIn("confirm", names)

    def test_push_visemes_never_raises(self):
        self.ui.push_visemes([(0.5, 0.4, 0.3)] * 5, 0.02, 0.0)
        self.ui.push_visemes([], 0.02, 0.0)
        self.ui.push_visemes(None, 0.02, 0.0)
        self.ui.push_visemes("garbage", "x", None)   # hostile input

    def test_set_audio_level_never_raises(self):
        self.ui.set_audio_level(0.5)
        self.ui.set_audio_level("bad")
        self.ui.set_audio_level(None)


if __name__ == "__main__":
    unittest.main()
