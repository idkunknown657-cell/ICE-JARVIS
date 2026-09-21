"""Unit tests for core/language.py — Hinglish/Hindi/English detection."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.language import (
    detect_language, language_directive, last_directive, strip_transient_prefix,
)


class DetectLanguageTest(unittest.TestCase):
    def test_devanagari_is_hindi(self):
        for s in ("आप कैसे हैं", "तुम कहाँ हो?", "हाँ, ठीक है"):
            self.assertEqual(detect_language(s), "hindi", msg=s)

    def test_mixed_line_with_devanagari_stays_hindi(self):
        self.assertEqual(detect_language("theek hai, wifi chalo अब"), "hindi")
        self.assertEqual(detect_language("ok theek hai, wifi chalo"), "hinglish")

    def test_hinglish_marker_lines(self):
        for s in ("kal wala kaam bata", "yaar yeh nahi ho raha hai",
                  "kya tu theek hai bro", "chalo abhi karo na"):
            self.assertEqual(detect_language(s), "hinglish", msg=s)

    def test_english_stays_english(self):
        for s in ("Hello, how are you?", "Please open chrome and play music",
                  "What time is it in japan?", "Thanks a lot!"):
            self.assertEqual(detect_language(s), "english", msg=s)

    def test_weak_marker_alone_is_english(self):
        # "kal" on its own is not enough to flip a Latin line to Hinglish.
        self.assertEqual(detect_language("test will run then"), "english")
        self.assertEqual(detect_language(""), "other")

    def test_directive_mirrors_mix(self):
        self.assertIn("LANG", language_directive("kal wala kaam bata"))
        self.assertIn("LANG", language_directive("आप कैसे हैं"))
        self.assertEqual(language_directive("please open chrome"), "")
        self.assertEqual(language_directive(""), "")

    def test_last_directive_uses_most_recent_user_line(self):
        lines = ["User: hi",
                 "JARVIS: hey!",
                 "User: kal wala kaam bata"]
        d = last_directive(lines)
        self.assertIn("Hinglish", d)
        self.assertEqual(last_directive([]), "")
        self.assertEqual(last_directive(["JARVIS: hello"]), "")

    def test_strip_transient_prefix_removes_scaffolding(self):
        state = ("[NOW — local context, seconds fresh] Treat this as extra "
                 "context\n- Machine: coding in vscode\n- Screen: main.py")
        s = f"{state}\n\n[REASON] Work this through internally\n[MOOD: you sound focused — calm] [LANG: Hinglish]\nopen chrome"
        cleaned = strip_transient_prefix(s)
        self.assertNotIn("[NOW —", cleaned)
        self.assertNotIn("[REASON]", cleaned)
        self.assertEqual(cleaned, "[MOOD: you sound focused — calm] [LANG: Hinglish]\nopen chrome")

    def test_strip_transient_prefix_idempotent_and_harmless(self):
        self.assertEqual(strip_transient_prefix("open chrome"), "open chrome")
        self.assertEqual(strip_transient_prefix(""), "")
        self.assertEqual(strip_transient_prefix("[NOW — nothing else"), "[NOW — nothing else")


if __name__ == "__main__":
    unittest.main()