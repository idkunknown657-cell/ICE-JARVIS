import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import actions.text_assist as ta


class Resp:
    def __init__(self, text):
        self.text = text


class MechanicalTests(unittest.TestCase):

    def test_paste_delegates_to_clipboard(self):
        with patch("actions.computer_control._clipboard_paste", return_value="Pasted: hello") as paste:
            out = ta.text_assist({"action": "paste", "text": "hello world"})
        paste.assert_called_once_with("hello world")
        self.assertIn("hello", out)

    def test_paste_needs_text(self):
        self.assertIn("needs 'text'", ta.text_assist({"action": "paste"}))

    def test_replace_clears_then_pastes(self):
        with patch("actions.computer_control._clear_field", return_value="Field cleared"), \
             patch("actions.computer_control._clipboard_paste", return_value="Pasted: hi") as paste:
            out = ta.text_assist({"action": "replace", "text": "hi there"})
        paste.assert_called_once_with("hi there")
        self.assertIn("pasted", out)

    def test_type_fallback_smart_type(self):
        with patch("actions.computer_control._smart_type", return_value="Typed hello") as st:
            out = ta.text_assist({"action": "type", "text": "hello"})
        st.assert_called_once_with("hello")
        self.assertIn("hello", out)

    def test_aliases(self):
        with patch("actions.computer_control._clipboard_paste", return_value="Pasted: x"):
            self.assertIn("Pasted", ta.text_assist({"action": "insert", "text": "x"}))
        with patch("actions.computer_control._smart_type", return_value="Typed x"):
            self.assertIn("Typed", ta.text_assist({"action": "keyboard", "text": "x"}))


class WritingTests(unittest.TestCase):

    def _mock_gemini(self, text):
        patcher = patch.object(ta.gemini, "call", return_value=Resp(text))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_draft_uses_topic(self):
        self._mock_gemini("Subject: coffee")
        with patch.object(ta, "_g", wraps=ta._g) as wrapped:
            out = ta.text_assist({"action": "draft", "topic": "morning coffee"})
        self.assertEqual(out, "Subject: coffee")
        sent = wrapped.call_args.args[0]
        self.assertIn("morning coffee", sent)

    def test_draft_needs_topic(self):
        out = ta.text_assist({"action": "draft", "topic": ""})
        self.assertIn("needs a 'topic'", out)

    def test_rewrite_needs_text(self):
        out = ta.text_assist({"action": "rewrite"})
        self.assertIn("needs 'text'", out)

    def test_rewrite_passes_source(self):
        self._mock_gemini("Better words.")
        with patch.object(ta, "_g", wraps=ta._g) as wrapped:
            out = ta.text_assist({"action": "rewrite", "text": "This is ok I guess"})
        self.assertEqual(out, "Better words.")
        sent = wrapped.call_args.args[0]
        self.assertIn("This is ok I guess", sent)

    def test_proofread_prompt_asks_for_fixes(self):
        self._mock_gemini("Fixed text.")
        with patch.object(ta, "_g", wraps=ta._g) as wrapped:
            ta.text_assist({"action": "proofread", "text": "i m going to the shops"})
        self.assertIn("Fixes", wrapped.call_args.args[0])

    def test_translate_target_language(self):
        self._mock_gemini("Bonjour.")
        with patch.object(ta, "_g", wraps=ta._g) as wrapped:
            ta.text_assist({"action": "translate", "text": "Hello", "to": "French"})
        self.assertIn("French", wrapped.call_args.args[0])

    def test_file_source_and_save_to(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "note.txt"
            dst = Path(tmp) / "out.txt"
            src.write_text("rough draft line", encoding="utf-8")
            self._mock_gemini("polished line")
            out = ta.text_assist({"action": "rewrite", "file": str(src),
                                  "save_to": str(dst)})
            self.assertIn("polished line", out)
            self.assertEqual(dst.read_text(encoding="utf-8"), "polished line")

    def test_missing_file_message(self):
        out = ta.text_assist({"action": "rewrite", "file": "Z:/no/such/file.txt"})
        self.assertIn("file not found", out)

    def test_write_save_failure_returns_text(self):
        self._mock_gemini("some content")
        out = ta.text_assist({"action": "rewrite", "text": "x",
                              "save_to": "Z:/root/forbidden/out.txt"})
        self.assertIn("some content", out)

    def test_gemini_failure_message(self):
        with patch.object(ta.gemini, "call", return_value=None):
            out = ta.text_assist({"action": "rewrite", "text": "x"})
        self.assertIn("produced nothing", out)

    def test_summarize_alias(self):
        self._mock_gemini("short")
        out = ta.text_assist({"action": "summarize", "text": "long content here"})
        self.assertEqual(out, "short")

    def test_unknown_action_help(self):
        out = ta.text_assist({"action": "frobnicate"})
        self.assertIn("text_assist can:", out)


if __name__ == "__main__":
    unittest.main()