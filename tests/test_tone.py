"""Unit tests for core/tone.py — the deterministic mood detector and the
[MOOD: …] tag it produces for typed user messages."""
import unittest

from core.tone import detect_mood, mood_tag


class MoodDetectionTest(unittest.TestCase):

    def test_neutral_message_stays_neutral(self):
        r = detect_mood("please open chrome")
        self.assertEqual(r["mood"], "neutral")
        self.assertEqual(r["strength"], 0.0)
        self.assertEqual(mood_tag("please open chrome"), "")

    def test_empty_and_none(self):
        for blank in (None, "", "   "):
            r = detect_mood(blank)
            self.assertEqual(r["mood"], "neutral")
            self.assertEqual(r["strength"], 0.0)

    def test_frustrated_by_keyword(self):
        r = detect_mood("this stupid thing keeps crashing")
        self.assertEqual(r["mood"], "frustrated")
        self.assertIn("calm", r["delivery"])
        self.assertGreaterEqual(r["strength"], 0.6)
        self.assertTrue(mood_tag("this stupid thing keeps crashing").startswith("[MOOD: you sound frustrated"))

    def test_angry(self):
        r = detect_mood("i hate this trash")
        self.assertEqual(r["mood"], "angry")
        self.assertIn("no jokes", r["delivery"])

    def test_excited_emphatic_punctuation(self):
        r = detect_mood("finally!!")
        self.assertEqual(r["mood"], "excited")
        self.assertGreaterEqual(r["strength"], 0.85)
        self.assertTrue(mood_tag("finally!!"))

    def test_excited_by_keyword_only(self):
        r = detect_mood("i am so happy")
        self.assertEqual(r["mood"], "excited")
        self.assertGreaterEqual(r["strength"], 0.6)

    def test_sad_and_tired(self):
        r = detect_mood("i'm so sad today")
        self.assertEqual(r["mood"], "sad")
        r = detect_mood("im exhausted")
        self.assertEqual(r["mood"], "tired")
        self.assertIn("gentle", r["delivery"])

    def test_anxious(self):
        r = detect_mood("i'm worried about the exam")
        self.assertEqual(r["mood"], "anxious")
        self.assertIn("reassuring", r["delivery"])

    def test_joking_and_playful(self):
        r = detect_mood("haha that would be the day")
        self.assertEqual(r["mood"], "joking")
        self.assertIn("humour", r["delivery"])
        r = detect_mood("hehe very cheeky 😜")
        self.assertIn(r["mood"], ("joking", "playful"))

    def test_surprised(self):
        r = detect_mood("no way!!!")
        self.assertEqual(r["mood"], "surprised")
        self.assertGreaterEqual(r["strength"], 0.6)

    def test_serious_overrides(self):
        r = detect_mood("seriously, no jokes now")
        self.assertEqual(r["mood"], "serious")
        self.assertGreaterEqual(r["strength"], 0.7)

    def test_emphatic_caps(self):
        r = detect_mood("I CANNOT BELIEVE this")
        self.assertIn(r["mood"], ("frustrated", "angry"))

    def test_shouting_alone_is_emphatic(self):
        r = detect_mood("YES!")
        self.assertGreater(r["strength"], 0.0)

    def test_tag_never_on_weak_signal(self):
        self.assertEqual(mood_tag("ok"), "")
        self.assertEqual(mood_tag("fine"), "")


class MoodTagTest(unittest.TestCase):

    def test_tag_format(self):
        tag = mood_tag("you are winding me up, haha 😂")
        self.assertTrue(tag.startswith("[MOOD: you sound "))
        self.assertTrue(tag.endswith("]"))

    def test_custom_threshold(self):
        # A plain keyword hit scores 0.6; a stricter floor kills the tag.
        self.assertEqual(mood_tag("i hate this", threshold=1.5), "")
        self.assertTrue(mood_tag("i hate this", threshold=0.5))

    def test_no_double_tag(self):
        tag = mood_tag("finally!!")
        self.assertEqual(tag.count("[MOOD:"), 1)


if __name__ == "__main__":
    unittest.main()