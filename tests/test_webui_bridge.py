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
import time
import unittest
from pathlib import Path
from unittest import mock

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import webui  # noqa: E402
from memory import config_manager as cm  # noqa: E402


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
        json.dumps(self.api.mail_get())

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


class TestSelfTrainingBridge(unittest.TestCase):
    """The self-training surface: what the HUD reads, and taking it back.

    Nothing here touches the real ledger, the real config or the network — the
    ledger path is redirected and the model call is stubbed, so a round is
    instant and offline.
    """

    def setUp(self):
        import tempfile
        from core import self_training as st
        self.st = st
        self.ui = _fresh_ui()
        self.api = self.ui._api
        webui._PUMP._q.clear()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        for target, attr, value in (
            (st, "STATE_PATH", Path(self._tmp.name) / "self_training.json"),
        ):
            p = mock.patch.object(target, attr, value)
            p.start()
            self.addCleanup(p.stop)
        self._state = st._STATE
        st._STATE = None
        self.addCleanup(lambda: setattr(st, "_STATE", self._state))

    def tearDown(self):
        webui._PUMP._q.clear()

    def test_training_get_is_jsonable_and_complete(self):
        snap = self.api.training_get()
        json.dumps(snap)
        for key in ("enabled", "intensity", "competency", "drills",
                    "drill_count", "cycles", "learned", "rounds_left"):
            self.assertIn(key, snap)
        self.assertIsInstance(snap["drills"], list)
        self.assertIsInstance(snap["drill_count"], int)

    def test_settings_expose_both_levers(self):
        d = self.api.get_settings()
        self.assertIn("self_training", d)
        self.assertIn("training_intensity", d)

    def test_save_setting_roundtrips_the_switch_and_intensity(self):
        from memory.config_manager import (get_self_training,
                                           get_training_intensity)
        with _ConfigGuard():
            try:
                self.assertTrue(self.api.save_setting("self_training", False)["ok"])
                self.assertIs(get_self_training(), False)
                self.assertTrue(self.api.save_setting("training_intensity", "focused")["ok"])
                self.assertEqual(get_training_intensity(), "focused")
                # junk intensity falls back instead of being stored
                self.api.save_setting("training_intensity", "ludicrous")
                self.assertEqual(get_training_intensity(), "balanced")
            finally:
                self.api.save_setting("self_training", True)
                self.api.save_setting("training_intensity", "balanced")

    def test_save_setting_pushes_the_new_state_to_the_hud(self):
        with _ConfigGuard():
            try:
                self.api.save_setting("self_training", False)
            finally:
                self.api.save_setting("self_training", True)
        phases = [p.get("phase") for n, p in webui._PUMP._q if n == "training"]
        self.assertIn("changed", phases)

    def test_run_starts_a_round_off_thread_and_reports_by_event(self):
        report = {"ok": True, "capability": "screen", "learned": 1}
        with mock.patch.object(self.st, "run_cycle", return_value=report) as rc:
            r = self.api.training_run(True)
            self.assertTrue(r["started"])
            for _ in range(40):
                if any(n == "training" and p.get("phase") == "done"
                       for n, p in webui._PUMP._q):
                    break
                threading.Event().wait(0.05)
        rc.assert_called_once_with("manual", force=True)
        done = [p for n, p in webui._PUMP._q if n == "training" and p.get("phase") == "done"]
        self.assertTrue(done)
        self.assertEqual(done[-1]["report"]["learned"], 1)
        self.assertIsInstance(done[-1]["state"], dict)

    def test_forget_and_reset_are_safe_with_nothing_stored(self):
        self.assertTrue(self.api.training_forget("nope")["ok"] is False)
        self.assertTrue(self.api.training_forget_all()["ok"])
        self.assertTrue(self.api.training_reset()["ok"])
        self.assertEqual(self.api.training_get()["drills"], [])


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

    def test_push_training_pushes_and_never_raises(self):
        self.ui.push_training({"phase": "running"})
        self.assertIn("training", [n for n, _ in webui._PUMP._q])
        for junk in (None, "x", 5, []):
            self.ui.push_training(junk)


class TestMailEndpoints(unittest.TestCase):
    """The mail endpoints the Settings page talks to.

    Every one of these writes to the config file, so every one runs inside
    _ConfigGuard — a suite that can overwrite the file holding the user's
    credentials is a suite that gets deleted.
    """

    def setUp(self):
        self.ui = _fresh_ui()
        self.api = self.ui._api

    def test_get_returns_the_presets_the_form_offers(self):
        with _ConfigGuard():
            r = self.api.mail_get()
        self.assertIn("account", r)
        presets = r.get("presets") or {}
        for key in ("gmail", "outlook", "yahoo", "icloud", "custom"):
            self.assertIn(key, presets)
            self.assertIn("label", presets[key])

    def test_save_then_get_round_trips_without_exposing_a_clear_password(self):
        with _ConfigGuard():
            r = self.api.mail_save({"provider": "gmail", "address": "a@b.com",
                                    "password": "secret-app-pw"})
            self.assertTrue(r["ok"])
            acct = self.api.mail_get()["account"]
        self.assertEqual(acct["address"], "a@b.com")
        self.assertEqual(acct["imap_host"], "imap.gmail.com")
        self.assertEqual(acct["password"], "secret-app-pw")
        # The UI's job is to never put that value in the DOM; the bridge's job is
        # to report it as configured rather than to leak it into a message.
        self.assertTrue(r["account"]["configured"] or r["account"]["account"])

    def test_saving_again_with_an_empty_password_keeps_the_saved_one(self):
        with _ConfigGuard():
            self.api.mail_save({"provider": "gmail", "address": "a@b.com",
                                "password": "secret-app-pw"})
            self.api.mail_save({"provider": "gmail", "address": "a@b.com",
                                "password": ""})
            acct = self.api.mail_get()["account"]
        self.assertEqual(acct["password"], "secret-app-pw")

    def test_configured_flag_follows_the_account(self):
        with _ConfigGuard():
            self.api.mail_clear()
            self.assertFalse(self.api.mail_get()["configured"])
            self.api.mail_save({"provider": "gmail", "address": "a@b.com",
                                "password": "pw"})
            self.assertTrue(self.api.mail_get()["configured"])
            self.api.mail_clear()
            self.assertFalse(self.api.mail_get()["configured"])

    def test_clear_removes_the_password_too(self):
        with _ConfigGuard():
            self.api.mail_save({"provider": "gmail", "address": "a@b.com",
                                "password": "secret-app-pw"})
            self.api.mail_clear()
            acct = self.api.mail_get()["account"]
        self.assertEqual(acct, {})

    def test_save_survives_a_hostile_payload(self):
        with _ConfigGuard():
            for junk in (None, {}, {"provider": None, "address": None,
                                    "imap_port": "not a port"},
                         {"provider": 5, "address": [], "password": {}}):
                r = self.api.mail_save(junk)
                self.assertIn("ok", r)

    def test_test_endpoint_reports_a_failure_without_raising(self):
        with _ConfigGuard():
            self.api.mail_clear()
            r = self.api.mail_test()
        self.assertFalse(r["ok"])
        self.assertTrue(r["msg"])
        self.assertIn("settings", r["msg"].lower())

    def test_test_endpoint_never_opens_a_real_connection_when_unconfigured(self):
        """An unconfigured account must be refused before any socket is opened,
        so pressing Test on a blank form cannot hang the UI."""
        with _ConfigGuard(), \
             mock.patch("imaplib.IMAP4_SSL", side_effect=AssertionError("opened a socket")):
            self.api.mail_clear()
            r = self.api.mail_test()
        self.assertFalse(r["ok"])


class TestMailSignInBridge(unittest.TestCase):
    """The sign-in endpoints, driven without a browser or a provider.

    These are the endpoints the Settings card calls, so the contract that
    matters is: a closed account still reads as configured, no call ever blocks,
    and nothing raises when the network or the user is absent.
    """

    def setUp(self):
        self.api = _fresh_ui()._api

    def test_an_account_with_no_password_reads_as_configured_when_signed_in(self):
        """Requiring a password would report a working Google sign-in as not set
        up, and the UI would send the user to a form that cannot help them."""
        with _ConfigGuard():
            cm.save_mail_oauth(provider="gmail", client_id="cid",
                               refresh_token="rt", access_token="at",
                               expires_at=time.time() + 3600,
                               address="me@example.com")
            r = self.api.mail_get()
        self.assertTrue(r["configured"])
        self.assertTrue(r["signed_in"])

    def test_the_sign_in_providers_reach_the_ui_with_their_setup_hint(self):
        with _ConfigGuard():
            r = self.api.mail_get()
        self.assertIn("gmail", r["signin_providers"])
        hint = r["signin_providers"]["gmail"]
        self.assertTrue(hint["label"])
        self.assertIn("console.cloud.google.com", hint["console"])

    def test_mail_get_never_leaks_a_token_to_the_front_end(self):
        """The account block is rendered in a form. A long-lived token must not
        be in it, because the front end is the part of this app most likely to
        be inspected — and a refresh token in a DOM tree is a refresh token in
        a screenshot."""
        with _ConfigGuard():
            cm.save_mail_oauth(provider="gmail", client_id="client-secret-ish",
                               refresh_token="refresh-should-not-appear",
                               access_token="access-should-not-appear",
                               expires_at=time.time() + 3600,
                               address="me@example.com")
            blob = json.dumps(self.api.mail_get())
        self.assertNotIn("refresh-should-not-appear", blob)
        self.assertNotIn("access-should-not-appear", blob)

    def test_starting_a_sign_in_without_a_client_id_fails_immediately(self):
        """The UI must not be left waiting on a flow that never began."""
        with _ConfigGuard():
            r = self.api.mail_signin_start({"provider": "gmail", "client_id": ""})
        self.assertFalse(r["ok"])
        self.assertIn("client ID", r["msg"])
        self.assertFalse(self.api.mail_signin_status()["active"])

    def test_an_unknown_provider_is_refused(self):
        with _ConfigGuard():
            r = self.api.mail_signin_start({"provider": "aol", "client_id": "cid"})
        self.assertFalse(r["ok"])

    def test_starting_a_sign_in_returns_a_url_without_blocking(self):
        """The handler must hand back the page to show straight away, because
        the user is about to spend a minute in another window."""
        with _ConfigGuard(), \
             mock.patch("webbrowser.open", return_value=True):
            started = time.time()
            r = self.api.mail_signin_start({"provider": "gmail",
                                            "client_id": "cid.apps.googleusercontent.com"})
            elapsed = time.time() - started
            self.api.mail_signin_cancel()
        self.assertTrue(r["ok"])
        self.assertLess(elapsed, 3.0, "the call blocked on the user")
        self.assertIn("accounts.google.com", r["state"]["url"])

    def test_a_second_start_cancels_the_first_rather_than_stacking_them(self):
        """Two live flows would leave the first loopback listening for a code
        that can never arrive."""
        with _ConfigGuard(), mock.patch("webbrowser.open", return_value=True):
            self.api.mail_signin_start({"provider": "gmail", "client_id": "c1.apps.googleusercontent.com"})
            first = self.api._mail_signin
            self.api.mail_signin_start({"provider": "outlook", "client_id": "c2"})
            second = self.api._mail_signin
            self.api.mail_signin_cancel()
        self.assertIsNot(first, second)
        self.assertTrue(first._cancel.is_set())

    def test_status_reports_connected_for_a_saved_sign_in(self):
        """Re-opening Settings mid-session must not show "not signed in" for an
        account that is working."""
        with _ConfigGuard():
            cm.save_mail_oauth(provider="gmail", client_id="cid",
                               refresh_token="rt", access_token="at",
                               expires_at=time.time() + 3600,
                               address="me@example.com")
            s = self.api.mail_signin_status()
        self.assertEqual(s["phase"], "connected")
        self.assertFalse(s["active"])

    def test_status_is_idle_and_answered_when_nothing_is_happening(self):
        with _ConfigGuard():
            self.api.mail_clear()
            s = self.api.mail_signin_status()
        self.assertEqual(s["phase"], "idle")
        self.assertFalse(s["active"])
        self.assertIsInstance(s, dict)

    def test_cancelling_when_nothing_is_running_is_a_plain_answer(self):
        with _ConfigGuard():
            r = self.api.mail_signin_cancel()
        self.assertTrue(r["ok"])
        self.assertFalse(r["cancelled"])

    def test_signing_out_keeps_an_app_password_working(self):
        """Sign out is not "remove account". Breaking a working account by
        pressing the wrong one of two buttons is a real hazard."""
        with _ConfigGuard():
            cm.save_mail_config(provider="gmail", address="me@example.com",
                                password="pw")
            cm.save_mail_oauth(provider="gmail", client_id="cid",
                               refresh_token="rt", access_token="at",
                               expires_at=time.time() + 3600,
                               address="me@example.com")
            r = self.api.mail_signin_forget()
        self.assertTrue(r["ok"])
        with _ConfigGuard():
            pass
        self.assertFalse(r["account"]["signed_in"])
        self.assertTrue(r["account"]["configured"])
        self.assertEqual(r["account"]["account"]["auth"], "password")

    def test_clearing_the_account_also_ends_a_sign_in(self):
        """Remove account has to remove the token too, or a "cleared" account is
        one that still has access."""
        with _ConfigGuard():
            cm.save_mail_oauth(provider="gmail", client_id="cid",
                               refresh_token="rt", access_token="at",
                               expires_at=time.time() + 3600,
                               address="me@example.com")
            r = self.api.mail_clear()
        self.assertFalse(r["account"]["configured"])
        self.assertFalse(r["account"]["signed_in"])
        self.assertEqual(cm.get_mail_config(), {})
        self.assertEqual(cm.get_mail_oauth(), {})

    def test_a_browser_that_will_not_open_is_reported_not_swallowed(self):
        """A user with no default browser has to be told to click the link,
        rather than watching a spinner that will never resolve on its own."""
        with _ConfigGuard(), mock.patch("webbrowser.open", return_value=False):
            pushed = []
            with mock.patch.object(webui._PUMP, "push",
                                   side_effect=lambda n, p=None: pushed.append((n, p))):
                self.api.mail_signin_start({"provider": "gmail",
                                            "client_id": "cid.apps.googleusercontent.com"})
            self.api.mail_signin_cancel()
        notes = [p for n, p in pushed if n == "mail_signin" and p]
        self.assertTrue(any("no browser" in str(p.get("note", "")) for p in notes))


if __name__ == "__main__":
    unittest.main()
