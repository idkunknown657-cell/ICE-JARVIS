"""Tests for core/emotion.py — the emotion → delivery → voice engine."""
import unittest

from core.emotion import (
    detect_emotion, delivery_tag, emotion_of, voice_params, EMOTIONS,
)


class EmotionDetectionTest(unittest.TestCase):

    def test_empty_and_none_stay_neutral(self):
        for blank in (None, "", "   "):
            r = detect_emotion(blank)
            self.assertEqual(r["emotion"], "neutral")
            self.assertEqual(r["intensity"], 0.0)

    def test_palette_is_bounded(self):
        self.assertIn("neutral", EMOTIONS)
        self.assertLessEqual(len(EMOTIONS), 12)
        # Every emotion must have voice params and a delivery line.
        for e in EMOTIONS:
            self.assertIsInstance(voice_params(e), dict)

    def test_greeting_is_happy(self):
        r = detect_emotion("hey jarvis")
        self.assertEqual(r["emotion"], "happy")
        self.assertEqual(r["source"], "greeting")
        r = detect_emotion("Hello!")
        self.assertEqual(r["emotion"], "happy")

    def test_success_moment_is_excited(self):
        r = detect_emotion("I finally fixed the problem!")
        self.assertEqual(r["emotion"], "excited")
        self.assertGreaterEqual(r["intensity"], 0.7)

    def test_failure_moment_is_supportive(self):
        r = detect_emotion("this isn't working")
        self.assertEqual(r["emotion"], "supportive")
        r = detect_emotion("it keeps crashing")
        self.assertEqual(r["emotion"], "supportive")

    def test_frustration_maps_to_supportive(self):
        r = detect_emotion("this stupid thing keeps crashing")
        # tone's "frustrated" keyword read wins → supportive register
        self.assertEqual(r["emotion"], "supportive")
        self.assertGreaterEqual(r["intensity"], 0.6)

    def test_joke_maps_to_amused(self):
        r = detect_emotion("haha that's hilarious lol")
        self.assertEqual(r["emotion"], "amused")

    def test_emoji_signal(self):
        r = detect_emotion("look at this 😂")
        self.assertEqual(r["emotion"], "amused")
        r = detect_emotion("we did it 🎉🔥")
        self.assertEqual(r["emotion"], "excited")

    def test_gratitude_is_happy_quiet(self):
        r = detect_emotion("thanks, that helped")
        self.assertEqual(r["emotion"], "happy")
        self.assertLess(r["intensity"], 0.6)

    def test_working_is_focused(self):
        r = detect_emotion("I'm debugging the parser, give me a sec")
        self.assertEqual(r["emotion"], "focused")

    def test_question_is_curious(self):
        r = detect_emotion("what's the weather like tomorrow?")

    def test_plain_statement_is_neutral(self):
        r = detect_emotion("please open chrome")
        self.assertEqual(r["emotion"], "neutral")


class DeliveryTagTest(unittest.TestCase):

    def test_neutral_gets_no_tag(self):
        self.assertEqual(delivery_tag("please open chrome"), "")
        self.assertEqual(delivery_tag(None), "")

    def test_strong_emotion_gets_tag(self):
        tag = delivery_tag("I finally fixed the problem!")
        self.assertTrue(tag.startswith("[DELIVER: "))
        self.assertIn("bright", tag.lower())

    def test_weak_signal_below_threshold(self):
        # Gratitude at 0.45 sits under the 0.5 default threshold.
        self.assertEqual(delivery_tag("thanks"), "")

    def test_tag_mentions_restraint(self):
        tag = delivery_tag("YESSS! I DID IT!!")
        # The anti-overact leash lives inside the delivery wording.
        self.assertTrue(any(w in tag.lower() for w in ("one", "do not", "then")))


class VoiceParamsTest(unittest.TestCase):

    def test_params_are_gentle(self):
        # The anti-overact rule: nudges stay small (±10%).
        for emo in EMOTIONS:
            p = voice_params(emo)
            for v in p.values():
                self.assertGreater(v, 0.9)
                self.assertLess(v, 1.1)

    def test_unknown_emotion_is_default(self):
        self.assertEqual(voice_params("nonexistent"), {})

    def test_emotion_of_label(self):
        self.assertEqual(emotion_of("hey there"), "happy")
        self.assertEqual(emotion_of("ok"), "neutral")


if __name__ == "__main__":
    unittest.main()
