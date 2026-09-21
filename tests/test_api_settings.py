"""Regression tests for the API-settings panel — the freeze/crash fixes.

The old code fed raw json values straight into Qt constructors; a non-string
gemini key from a hand-edited api_keys.json raised a TypeError inside a slot,
and PyQt6 can abort the whole process on that. These tests pin the hardened
behaviour: nothing may raise for any garbage config.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

TESTS_DIR = Path(__file__).resolve().parent
ROOT = TESTS_DIR.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import ui  # noqa: E402  (needs sys.path + platform env first)
import core.api_verify as api_verify  # noqa: E402,F401


class _Resp:
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text

    def json(self):
        return {}


class CoerceTest(unittest.TestCase):
    def test_strings_strip(self):
        self.assertEqual(ui._coerce_key("  AIza123  "), "AIza123")

    def test_numbers_become_strings(self):
        self.assertEqual(ui._coerce_key(12345), "12345")

    def test_garbage_types_become_empty(self):
        for bad in (None, {}, ["a"], True, {"k": 1}):
            self.assertEqual(ui._coerce_key(bad), "", msg=f"{type(bad).__name__}")


class SanitizeRowsTest(unittest.TestCase):
    def test_cleans_garbage_rows(self):
        rows = [
            42,
            "bare-string",
            None,
            {"name": "ok", "api_key": ["not-a-str"], "base_url": 7,
             "model": {"x": 1}},
            {"name": "  no-key  ", "api_key": ""},
            {},
        ]
        out = ui._sanitize_provider_rows(rows)
        # the keyless placeholder row is kept (it's an editable slot), garbage
        # scalars are dropped, non-string values are string-cast, never raised
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["name"], "ok")
        self.assertEqual(out[0]["api_key"], "")
        self.assertEqual(out[0]["base_url"], "7")
        self.assertEqual(out[0]["model"], "{'x': 1}")
        self.assertEqual(out[1]["name"], "no-key")

    def test_non_list_is_ignored(self):
        self.assertEqual(ui._sanitize_provider_rows({"groq": "xyz"}), [])
        self.assertEqual(ui._sanitize_provider_rows("nope"), [])

    def test_base_url_trailing_slash_trimmed(self):
        out = ui._sanitize_provider_rows(
            [{"name": "g", "base_url": "https://api.groq.com/openai/v1/"}])
        self.assertEqual(out[0]["base_url"], "https://api.groq.com/openai/v1")


class OverlayConstructionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = None
        from PyQt6.QtWidgets import QApplication
        if QApplication.instance() is None:
            cls.app = QApplication([])

    def test_hostile_gemini_key_does_not_crash(self):
        for bad in (12345, {"key": "AIza"}, ["AIza"]):
            with self.subTest(bad=bad):
                ov = ui.ApiKeysOverlay(gemini_key=bad, providers=[], parent=None)
                ov.deleteLater()

    def test_garbage_providers_do_not_crash(self):
        ov = ui.ApiKeysOverlay(
            gemini_key="",
            providers=["groq", None, 7, {"name": "ok", "api_key": ["a", "b"]}],
            parent=None)
        ov.deleteLater()

    def test_normal_build_and_save_emits(self):
        got = []
        ov = ui.ApiKeysOverlay(gemini_key="AIzaX123456789012", providers=[],
                               parent=None)
        ov.saved.connect(lambda g, p: got.append((g, p)))
        ov._gemini_input.setText("  AIzaX123456789012  ")
        ov._pv_inputs["groq"].setText("gsk_x")
        ov._save()
        self.assertEqual(got[0][0], "AIzaX123456789012")
        self.assertEqual(got[0][1][0]["api_key"], "gsk_x")

    def test_test_button_flow_offscreen(self):
        ov = ui.ApiKeysOverlay(gemini_key="", providers=[], parent=None)
        with mock.patch("core.api_verify.verify_all",
                        return_value=[("gemini", False, "no key provided")]) as v:
            ov._start_test()
            ov._test_thread.join(timeout=5)
            self.assertTrue(v.called)
        ov.deleteLater()


class VerifyTest(unittest.TestCase):
    def test_gemini_valid(self):
        with mock.patch("core.api_verify.requests.get",
                        return_value=_Resp(200)) as g:
            ok, msg = api_verify.verify_gemini("AIza123", timeout=0.1)
            self.assertTrue(ok)
            self.assertIn("valid", msg)
            g.assert_called_once()

    def test_gemini_invalid(self):
        with mock.patch("core.api_verify.requests.get",
                        return_value=_Resp(401)):
            ok, msg = api_verify.verify_gemini("AIza123", timeout=0.1)
            self.assertFalse(ok)
            self.assertIn("invalid", msg)

    def test_gemini_missing_key(self):
        ok, msg = api_verify.verify_gemini("", timeout=0.1)
        self.assertFalse(ok)

    def test_timeout_never_raises(self):
        def boom(*a, **k):
            raise TimeoutError("took too long")
        with mock.patch("core.api_verify.requests.get", side_effect=boom):
            ok, msg = api_verify.verify_gemini("AIza123", timeout=0.1)
            self.assertFalse(ok)
            self.assertIn("TimeoutError", msg)

    def test_provider_valid_and_invalid(self):
        with mock.patch("core.api_verify.requests.get",
                        return_value=_Resp(200)):
            ok, _ = api_verify.verify_provider(
                "https://api.groq.com/openai/v1", "gsk_x", timeout=0.1)
            self.assertTrue(ok)
        with mock.patch("core.api_verify.requests.get",
                        return_value=_Resp(403)):
            ok, msg = api_verify.verify_provider(
                "https://api.groq.com/openai/v1", "gsk_x", timeout=0.1)
            self.assertFalse(ok)

    def test_provider_bad_base_url(self):
        ok, msg = api_verify.verify_provider("garbage", "gsk_x", timeout=0.1)
        self.assertFalse(ok)
        self.assertIn("base_url", msg)

    def test_verify_all_never_raises_on_junk(self):
        junk = [None, "str", {"name": "x", "api_key": ["no"]}]
        with mock.patch("core.api_verify.requests.get", return_value=_Resp(200)):
            results = api_verify.verify_all("", junk, timeout=0.1)
        self.assertEqual(len(results), 4)
        self.assertTrue(all(len(r) == 3 for r in results))


class RoundTripTest(unittest.TestCase):
    def _fresh(self):
        tmp = tempfile.mkdtemp(prefix="apikeys_")
        path = Path(tmp) / "api_keys.json"
        return tmp, path

    def test_atomic_write_merge_and_reload(self):
        tmp, path = self._fresh()
        path.write_text(json.dumps({"os_system": "windows", "gemini_api_key": "OLD"},
                                   indent=2), encoding="utf-8")
        with mock.patch("ui.CONFIG_DIR", tmp), mock.patch("ui.API_FILE", path):
            n = ui._write_api_keys_impl("AIzaNEW", [
                {"name": "groq", "base_url": "https://api.groq.com/openai/v1/",
                 "api_key": "gsk_x", "model": "llama-3.3-70b-versatile"},
            ])
            self.assertEqual(n, 1)
            # untouched keys survive (no wipe)
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["os_system"], "windows")
            self.assertEqual(data["gemini_api_key"], "AIzaNEW")
            self.assertEqual(data["free_providers"][0]["base_url"],
                             "https://api.groq.com/openai/v1")
            self.assertFalse(Path(str(path) + ".tmp").exists())

        # simulated restart: a fresh read sees exactly what we saved
        with mock.patch("ui.API_FILE", path):
            cfg = ui._read_api_cfg()
            self.assertEqual(cfg["gemini_api_key"], "AIzaNEW")

    def test_empty_gemini_leaves_old_value(self):
        tmp, path = self._fresh()
        path.write_text(json.dumps({"gemini_api_key": "KEEP"}), encoding="utf-8")
        with mock.patch("ui.CONFIG_DIR", tmp), mock.patch("ui.API_FILE", path):
            ui._write_api_keys_impl("", [])
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["gemini_api_key"], "KEEP")

    def test_write_over_corrupt_file(self):
        tmp, path = self._fresh()
        path.write_text("{ this is not json", encoding="utf-8")
        with mock.patch("ui.CONFIG_DIR", tmp), mock.patch("ui.API_FILE", path):
            n = ui._write_api_keys_impl("AIzaNEW", [
                {"name": "groq", "api_key": "gsk_x", "base_url": "https://x",
                 "model": "m"},
            ])
            self.assertEqual(n, 1)
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["gemini_api_key"], "AIzaNEW")


class FreeProvidersGuardTest(unittest.TestCase):
    def test_reset_cooldowns_clears(self):
        from core import free_providers
        free_providers._fail_until["groq"] = 999999.0
        free_providers.reset_cooldowns()
        self.assertNotIn("groq", free_providers._fail_until)

    def test_load_skips_non_http_base(self):
        from core import free_providers
        with mock.patch.object(free_providers, "_CONFIG_PATH", _FakePath()):
            rows = free_providers._load()
        names = [r["name"] for r in rows]
        self.assertNotIn("badbase", names)
        self.assertIn("good", names)


class _FakePath:
    def read_text(self, **kw):
        return json.dumps({"free_providers": [
            {"name": "badbase", "base_url": "not-a-url", "api_key": "x",
             "model": "m"},
            {"name": "good", "base_url": "https://api.example.com/v1",
             "api_key": "x", "model": "m"},
        ]})


if __name__ == "__main__":
    unittest.main(verbosity=2)