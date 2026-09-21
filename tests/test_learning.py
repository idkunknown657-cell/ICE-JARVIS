"""Unit tests for core/learning.py — auto-extraction, lessons, improvement log.

Uses throwaway memory + improvements files and mocked model calls — never the
real store, never a real network call."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import learning
from memory import memory_manager as mm

LINES = [
    "User: hey, we should build a habit tracker app",
    "JARVIS: ooh, nice project! what's the first feature?",
    "User: just a daily check-in reminder, and I prefer dark theme",
]


class SanitizeTest(unittest.TestCase):
    def test_junk_input_becomes_nothing(self):
        for junk in (None, "nope", 42, ["a"], {}, {"memory": "str"},
                     {"memory": [None, "x", {"category": "bad", "key": "K!",
                                             "value": 7}]}):
            facts, lessons = learning._sanitize_extraction(junk)
            self.assertIsInstance(facts, list)
            self.assertIsInstance(lessons, list)
            for f in facts:
                self.assertIn(f["category"], learning._FACTS_CATEGORIES)

    def test_good_extraction_is_normalised(self):
        data = {
            "memory": [
                {"category": "projects", "key": "Habit Tracker!",
                 "value": "  daily check-in app, dark theme  "},
                {"category": "nonsense", "key": "x", "value": "skip me"},
            ],
            "lessons": [{"topic": "Tone!", "lesson": "  Mirror the user's energy  "}],
        }
        facts, lessons = learning._sanitize_extraction(data)
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0]["key"], "habit_tracker")
        self.assertEqual(lessons[0][1], "Mirror the user's energy")


class ExtractionTest(unittest.TestCase):
    def test_extract_facts_calls_model_and_sanitises(self):
        with mock.patch.object(learning, "gemini") as g:
            g.as_json.return_value = {
                "memory": [{"category": "projects",
                            "key": "habit_tracker", "value": "daily app"}],
                "lessons": [{"topic": "voice", "lesson": "stay warm"}],
            }
            r = learning.extract_facts("a long conversation")
        self.assertEqual(len(r["memory"]), 1)
        self.assertEqual(r["memory"][0]["key"], "habit_tracker")

    def test_extract_facts_never_raises(self):
        with mock.patch.object(learning, "gemini") as g:
            g.as_json.side_effect = RuntimeError("boom")
            self.assertEqual(learning.extract_facts("x"), {"memory": [], "lessons": []})

    def test_empty_conversation_skips_model(self):
        with mock.patch.object(learning, "gemini") as g:
            r = learning.extract_facts("")
            g.as_json.assert_not_called()
        self.assertEqual(r, {"memory": [], "lessons": []})


class LearnFlowTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self._mem_file = root / "long_term.json"
        self._imp_file = root / "improvements.json"
        self._mem_patch = mock.patch.object(mm, "MEMORY_PATH", self._mem_file)
        self._mem_patch.start()
        self._imp_patch = mock.patch.object(learning, "IMPROVEMENTS_PATH",
                                            self._imp_file)
        self._imp_patch.start()
        self.addCleanup(self._imp_patch.stop)
        self.addCleanup(self._mem_patch.stop)
        self._print = mock.patch("builtins.print")
        self._print.start()
        self.addCleanup(self._print.stop)

    def test_learn_merges_facts_into_memory(self):
        with mock.patch.object(learning, "extract_facts") as ef:
            ef.return_value = {
                "memory": [{"category": "preferences", "key": "dark_theme",
                            "value": "Prefers dark UI"}],
                "lessons": [("voice", "Always confirm before destructive actions")],
            }
            report = learning.learn_from_conversation(LINES)
        self.assertEqual(report["facts"], 1)
        self.assertEqual(report["lessons"], 1)
        mem = mm.load_memory()
        self.assertEqual(mem["preferences"]["dark_theme"]["value"],
                         "Prefers dark UI")
        self.assertIn("confirm", mem["lessons"]["voice"]["value"])

    def test_repeat_lesson_is_deduplicated(self):
        def _swap(data):
            return {"memory": data.get("memory", []), "lessons": data.get("lessons", [])}
        with mock.patch.object(learning, "extract_facts") as ef:
            ef.return_value = _swap({"lessons": [("a", "mirror the energy")]})
            learning.learn_from_conversation(LINES)
        with mock.patch.object(learning, "extract_facts") as ef:
            ef.return_value = _swap({"lessons": [("a", "mirror the energy")]})
            report = learning.learn_from_conversation(LINES)
        self.assertEqual(report["lessons"], 0)   # same text → not added twice

    def test_too_short_chunk_skips(self):
        report = learning.learn_from_conversation(["User: hi"])
        self.assertEqual(report, {"facts": 0, "lessons": 0, "turns": 1})

    def test_learn_never_raises_on_merge_error(self):
        with mock.patch.object(learning, "extract_facts") as ef:
            ef.return_value = {"memory": [{"category": "bad", "key": "", "value": ""}],
                               "lessons": []}
            report = learning.learn_from_conversation(LINES)
        self.assertIsInstance(report, dict)

    def test_category_capped_at_newest(self):
        facts = [{"category": "notes", "key": f"note_{i:02d}",
                  "value": f"detail number {i}"} for i in range(1, 16)]
        with mock.patch.object(learning, "extract_facts") as ef:
            ef.return_value = {"memory": facts, "lessons": []}
            report = learning.learn_from_conversation(LINES)
        self.assertEqual(report["facts"], 15)
        mem = mm.load_memory()
        notes = mem["notes"]
        self.assertLessEqual(len(notes), learning._MAX_PER_CATEGORY)
        self.assertEqual(len(notes), learning._MAX_PER_CATEGORY)
        # 15 facts into a 12 cap → the 3 oldest drop, the rest survive
        self.assertNotIn("note_01", notes)
        self.assertNotIn("note_03", notes)
        self.assertIn("note_04", notes)
        self.assertIn("note_15", notes)
        # cap holds across merges: nothing silently restores evicted entries
        with mock.patch.object(learning, "extract_facts") as ef:
            ef.return_value = {"memory": [{"category": "notes", "key": "note_00",
                                           "value": "ancient note"}],
                               "lessons": []}
            learning.learn_from_conversation(LINES)
        self.assertEqual(len(mm.load_memory()["notes"]),
                         learning._MAX_PER_CATEGORY)


class EvaluateApplyTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self._mem_file = root / "long_term.json"
        self._imp_file = root / "improvements.json"
        self._mem_patch = mock.patch.object(mm, "MEMORY_PATH", self._mem_file)
        self._mem_patch.start()
        self._imp_patch = mock.patch.object(learning, "IMPROVEMENTS_PATH",
                                            self._imp_file)
        self._imp_patch.start()
        self.addCleanup(self._imp_patch.stop)
        self.addCleanup(self._mem_patch.stop)
        self._print = mock.patch("builtins.print")
        self._print.start()
        self.addCleanup(self._print.stop)

    def test_evaluate_returns_notes_and_failure_is_quiet(self):
        with mock.patch.object(learning, "gemini") as g:
            g.as_json.return_value = {"improvements": [
                {"category": "conversation_style",
                 "note": "Open with what the user last said, not a canned line."},
            ]}
            notes = learning.evaluate("long conversation")
        self.assertEqual(len(notes), 1)
        self.assertIn("canned", notes[0]["note"])

        with mock.patch.object(learning, "gemini") as g:
            g.as_json.side_effect = RuntimeError("boom")
            self.assertEqual(learning.evaluate("x"), [])

    def test_apply_improvements_logs_reversibly(self):
        notes = [{"category": "tool_use",
                  "note": "Verify destructive tool names before running them."}]
        applied = learning.apply_improvements(notes)
        self.assertEqual(applied, 1)
        data = learning._load_improvements()
        self.assertEqual(data["count"], 1)
        entry = data["log"][0]
        self.assertEqual(entry["category"], "tool_use")
        self.assertTrue(entry["applied"])
        # reversible: entries carry old/new; lesson sits in memory to be forgotten
        mem = mm.load_memory()
        self.assertTrue(mem["lessons"])
        self.assertIn("Verify destructive", learning.recent_improvements(3))

    def test_log_improvement_never_raises(self):
        learning.log_improvement("planning", "notes")
        learning.log_improvement("planning", "x", old="a", new="b")
        self.assertEqual(learning._load_improvements()["count"], 2)


class UsageTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._path = Path(self._tmp.name) / "usage_counts.json"

    def test_detection_requires_spread(self):
        from core import usage
        with mock.patch.object(usage, "USAGE_PATH", self._path):
            usage.record("action::open_chrome")
            usage.flush()
            # same day only → no candidate (needs ≥2 days)
            self.assertEqual(usage.workflow_candidates(), [])
            # simulate a second day
            data = {
                "2026-09-01": {"action::open_chrome": 3},
                "2026-09-02": {"action::open_chrome": 2},
            }
            self._path.write_text(__import__("json").dumps(data), encoding="utf-8")
            got = usage.workflow_candidates(min_days=2, min_count=3)
            self.assertEqual(len(got), 1)
            self.assertEqual(got[0]["slot"], "action::open_chrome")
            self.assertEqual(got[0]["count"], 5)
            self.assertEqual(len(got[0]["days"]), 2)

    def test_prune_removes_old_days(self):
        from core import usage
        with mock.patch.object(usage, "USAGE_PATH", self._path):
            data = {"2020-01-01": {"action::a": 5}, "2099-12-31": {"action::b": 1}}
            self._path.write_text(__import__("json").dumps(data), encoding="utf-8")
            self.assertEqual(usage.prune_older_than(days=30), 1)
            got = usage._load_disk()
            self.assertEqual(list(got.keys()), ["2099-12-31"])


if __name__ == "__main__":
    unittest.main()