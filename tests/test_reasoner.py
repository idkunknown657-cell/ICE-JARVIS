"""Unit tests for core/reasoner.py — adaptive task classification."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import gemini
from core.reasoner import classify, pick_tier, nature_tag


class ClassifyTest(unittest.TestCase):
    def test_social_smalltalk_is_fast(self):
        for s in ("hi", "hello", "Heyy, how are you?", "thanks a lot",
                  "good morning", "lol", "okay sure", "kaise ho bro"):
            r = classify(s)
            self.assertTrue(r["smalltalk"], msg=s)
            self.assertEqual(r["tier"], gemini.FAST, msg=s)

    def test_complex_reasoning_is_smart(self):
        for s in ("explain why this script fails and how to fix it",
                  "compare these two approaches and recommend one",
                  "should i use sqlite or postgres here?",
                  "refactor this function to be more efficient"):
            r = classify(s)
            self.assertTrue(r["hard"], msg=s)
            self.assertEqual(r["nature"], "complex", msg=s)
            self.assertEqual(r["tier"], gemini.SMART, msg=s)

    def test_vision_request_is_smart(self):
        r = classify("what do you see on my screen right now?")
        self.assertTrue(r["vision"])
        self.assertEqual(r["tier"], gemini.SMART)

    def test_concrete_task_verb_is_task(self):
        r = classify("open youtube and search for jazz")
        self.assertEqual(r["nature"], "task")
        self.assertTrue(r["hard"])

    def test_short_plain_uses_fast(self):
        r = classify("good")
        self.assertEqual(r["tier"], gemini.FAST)

    def test_long_plain_uses_smart(self):
        long_q = "here is a long detail " * 12
        r = classify(long_q)
        self.assertEqual(r["tier"], gemini.SMART)

    def test_empty_input(self):
        r = classify("")
        self.assertEqual(r["nature"], "plain")

    def test_pick_tier_matches_classify(self):
        self.assertEqual(pick_tier("hello there"), gemini.FAST)
        self.assertEqual(pick_tier("analyze the whole codebase"),
                         gemini.SMART)

    def test_nature_tag(self):
        self.assertEqual(nature_tag("hi"), "social")
        self.assertEqual(nature_tag("generic sentence"), "")


if __name__ == "__main__":
    unittest.main()