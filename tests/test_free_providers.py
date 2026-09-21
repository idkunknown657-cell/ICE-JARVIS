"""Unit tests for core/free_providers.py + the gemini.text/as_json fallback.
requests.post is mocked everywhere; no real network calls."""
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from core import gemini, free_providers as fp


class _Resp:
    def __init__(self, status, payload=None):
        self.status_code = status
        self._payload = payload or {}

    def json(self):
        return self._payload

    @property
    def text(self):
        return json.dumps(self._payload)


class ConfigTest(unittest.TestCase):

    def test_empty_while_no_config(self):
        tmp = tempfile.TemporaryDirectory()
        with mock.patch.object(fp, "_CONFIG_PATH", Path(tmp.name) / "gone.json"):
            self.assertEqual(fp._load(), [])
        tmp.cleanup()

    def test_rows_without_key_are_skipped(self):
        tmp = tempfile.TemporaryDirectory()
        cfg = Path(tmp.name) / "api_keys.json"
        cfg.write_text(json.dumps({"free_providers": [
            {"name": "groq", "base_url": "https://a", "api_key": "k1",
             "model": "m1"},
            {"name": "cerebras", "base_url": "https://b", "api_key": "",
             "model": "m2"},
            {"name": "openrouter", "base_url": "https://c", "api_key": "k3",
             "model": ""},
            "garbage",
        ]}), encoding="utf-8")
        with mock.patch.object(fp, "_CONFIG_PATH", cfg):
            rows = fp._load()
        names = [r["name"] for r in rows]
        self.assertEqual(names, ["groq", "openrouter"])
        self.assertEqual(rows[1]["model"], fp._DEFAULT_MODEL)
        tmp.cleanup()


class TextFallbackTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = Path(self.tmp.name) / "api_keys.json"
        self.cfg.write_text(json.dumps({"free_providers": [
            {"name": "groq", "base_url": "https://groq", "api_key": "k1",
             "model": "llama-a"},
            {"name": "cerebras", "base_url": "https://cerebras", "api_key": "k2",
             "model": "llama-b"},
        ]}), encoding="utf-8")
        self.patcher = mock.patch.object(fp, "_CONFIG_PATH", self.cfg)
        self.patcher.start()
        fp._fail_until.clear()
        self.addCleanup(self.patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def _ok(self, content="answer here"):
        return _Resp(200, {"choices": [{"message": {"content": content}}]})

    def test_first_provider_answers(self):
        with mock.patch("requests.post", return_value=self._ok("hi from groq")) as post:
            got = fp.text("hello")
        self.assertEqual(got, "hi from groq")
        self.assertEqual(post.call_count, 1)

    def test_falls_through_to_second_on_5xx(self):
        def side(url, **kw):
            if "groq" in url:
                return _Resp(503, {})
            return self._ok("hi from cerebras")
        with mock.patch("requests.post", side_effect=side) as post:
            got = fp.text("hello")
        self.assertEqual(got, "hi from cerebras")
        self.assertEqual(post.call_count, 2)
        # groq is now on cooldown -> a second call skips it entirely
        with mock.patch("requests.post", side_effect=side) as post2:
            got2 = fp.text("hello again")
        self.assertEqual(got2, "hi from cerebras")
        self.assertEqual(post2.call_count, 1)

    def test_all_fail_returns_none(self):
        with mock.patch("requests.post", return_value=_Resp(500, {})):
            self.assertIsNone(fp.text("hello"))

    def test_none_configured_returns_none(self):
        self.cfg.write_text(json.dumps({"free_providers": []}), encoding="utf-8")
        with mock.patch("requests.post") as post:
            self.assertIsNone(fp.text("hello"))
        post.assert_not_called()

    def test_from_contents_converts_parts_and_text(self):
        self.assertIn("the question", fp.from_contents(
            ["the question", {"text": " also context"}]))
        self.assertEqual(
            fp.from_contents("plain string\nsecond line").splitlines()[0],
            "plain string")

    def test_v1_base_does_not_double_up(self):
        """The Hugging Face row stores "/v1" in its base; the endpoint builder
        must append "/chat/completions" to it — never a second "/v1/...", which
        404'd and made every correctly configured provider fail."""
        self.cfg.write_text(json.dumps({"free_providers": [
            {"name": "huggingface", "base_url": "https://router.huggingface.co/v1",
             "api_key": "hf_k", "model": "meta-llama/Llama-3.1-8B-Instruct"},
        ]}), encoding="utf-8")
        with mock.patch("requests.post", return_value=self._ok("hf answer")) as post:
            got = fp.text("hello")
        self.assertEqual(got, "hf answer")
        url = post.call_args[0][0]
        self.assertEqual(url, "https://router.huggingface.co/v1/chat/completions")

    def test_endpoint_helper_tolerates_both_base_shapes(self):
        from core.free_providers import _endpoint
        self.assertEqual(
            _endpoint("https://api.groq.com/openai/v1", "chat/completions"),
            "https://api.groq.com/openai/v1/chat/completions")
        self.assertEqual(
            _endpoint("https://api.groq.com/openai/v1/", "models"),
            "https://api.groq.com/openai/v1/models")
        self.assertEqual(
            _endpoint("https://api.example.com", "models"),
            "https://api.example.com/v1/models")


class GeminiFallbackTest(unittest.TestCase):

    def tearDown(self):
        fp._fail_until.clear()

    def test_text_uses_free_provider_when_gemini_answers_nothing(self):
        with mock.patch("core.gemini.call", return_value=None), \
             mock.patch.object(fp, "enabled", return_value=True), \
             mock.patch.object(fp, "from_contents", return_value="the prompt"), \
             mock.patch.object(fp, "text", return_value="fallback answer") as ft:
            got = gemini.text(["q"], default="default")
        self.assertEqual(got, "fallback answer")
        ft.assert_called_once_with("the prompt")

    def test_text_keeps_gemini_answer_when_present(self):
        class Resp:
            text = "gemini says hi"
        with mock.patch("core.gemini.call", return_value=Resp()):
            self.assertEqual(gemini.text(["q"], default="d"), "gemini says hi")

    def test_as_json_parses_free_provider_json(self):
        with mock.patch("core.gemini.call", return_value=None), \
             mock.patch.object(fp, "enabled", return_value=True), \
             mock.patch.object(fp, "from_contents", return_value="prompt"), \
             mock.patch.object(fp, "text",
                               return_value='{"language": "hinglish"}'):
            got = gemini.as_json(["q"], default={})
        self.assertEqual(got, {"language": "hinglish"})

    def test_as_json_falls_back_to_default(self):
        with mock.patch("core.gemini.call", return_value=None), \
             mock.patch.object(fp, "enabled", return_value=False):
            self.assertEqual(gemini.as_json(["q"], default={"x": 1}), {"x": 1})


if __name__ == "__main__":
    unittest.main()