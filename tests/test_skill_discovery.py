"""Tests for core/skill_discovery.py and actions/skill_discovery.py — noticing
repeated requests and offering, once, to learn them.

What is pinned here, and why each of it exists:

  * the dedupe key survives rewording ("can you convert 3 miles, please" and
    "convert 12 miles to km" are one wish) — a counter that splits on wording
    never reaches its threshold and the whole feature is dead weight;
  * questions are never recorded — they are answers, not repeatable tasks, and
    a watch list full of them would offer to "learn" the weather;
  * secrets are refused — the store rides into a prompt, exactly like the
    standing instructions it borrows the shapes from;
  * the offer happens exactly once per candidate, enforced by the act of
    reading, not by trusting the model to bookkeep;
  * a successful forge retires the candidate that asked for it, even when the
    model rephrases the goal;
  * the switch works, in both directions, and the memory switch outranks it.

Every test runs against a temp store: the real config/ is never touched.
"""
import importlib.util
import inspect
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from core import skill_discovery as sd          # noqa: E402
from actions import skill_discovery as tool     # noqa: E402


class DiscoveryStoreTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "skill_watch.json"
        patcher = mock.patch.object(sd, "watch_path", return_value=self.path)
        patcher.start()
        self.addCleanup(patcher.stop)

    # ── recording ──────────────────────────────────────────────────────────

    def test_rewording_the_same_wish_counts_once(self):
        sd.note("can you convert 3 miles to km, please")
        sd.note("convert 12 miles to km")
        items = sd.suggestions()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["count"], 2)

    def test_filler_and_file_names_do_not_split_the_count(self):
        sd.note("make me a summary of report.pdf please")
        sd.note("make me a summary of notes.docx")
        self.assertEqual(len(sd.suggestions()), 1)
        self.assertEqual(sd.suggestions()[0]["count"], 2)

    def test_questions_are_not_tasks(self):
        for text in ("what is the weather in Paris",
                     "who is the prime minister",
                     "where is the nearest station",
                     "why is the sky blue"):
            self.assertIsNone(sd.note(text), text)
        self.assertEqual(sd.suggestions(), [])

    def test_a_credential_is_never_stored(self):
        self.assertIsNone(sd.note("use my api key sk-abcdefghijklmnop1234567890"))
        self.assertIsNone(sd.note("the password is hunter2hunter2"))
        self.assertEqual(sd.suggestions(), [])

    def test_a_too_short_fragment_is_not_a_wish(self):
        self.assertIsNone(sd.note("ok"))
        self.assertIsNone(sd.note("do it"))

    def test_tool_guesses_are_recorded_as_their_own_signal(self):
        # "Unknown tool: check_internet_speed" — the model wishing a tool
        # existed, short name and all, is exactly the signal worth keeping.
        entry = sd.note("check_internet_speed", source="tool")
        self.assertIsNotNone(entry)
        self.assertEqual(entry["count"], 1)

    def test_an_existing_taught_skill_is_not_a_candidate(self):
        with mock.patch.object(sd, "_looks_like_a_secret", return_value=False):
            with mock.patch("core.skill_registry.find_matching_skill",
                            return_value=("internet_speed_test", {})):
                self.assertIsNone(sd.note("check my internet speed please"))
        self.assertEqual(sd.suggestions(), [])

    def test_switching_discovery_off_stops_the_counting(self):
        with mock.patch("memory.config_manager.get_skill_discovery_enabled",
                        return_value=False):
            self.assertIsNone(sd.note("convert 3 miles to km please"))
        self.assertEqual(sd.suggestions(), [])

    def test_the_memory_switch_outranks_it(self):
        from memory import config_manager as cfg
        with mock.patch.object(cfg, "load_api_keys",
                               return_value={"skill_discovery_enabled": True,
                                             "memory_enabled": False}):
            self.assertFalse(cfg.get_skill_discovery_enabled())

    def test_a_corrupt_store_reads_as_empty_and_never_raises(self):
        self.path.write_text("{not json", encoding="utf-8")
        self.assertEqual(sd.suggestions(), [])
        self.assertEqual(sd.candidates(), [])
        self.assertIsNotNone(sd.note("convert 3 miles to km"))

    # ── ripening ───────────────────────────────────────────────────────────

    def test_three_repeats_make_a_candidate(self):
        for _ in range(2):
            sd.note("check the printer ink level")
        self.assertEqual(sd.candidates(), [])
        sd.note("check the printer ink level")
        self.assertEqual(len(sd.candidates()), 1)

    def test_an_explicit_wish_ripens_a_repeat_early(self):
        # "I wish you could" is stripped for the count (so it lines up with the
        # plain phrasing) but must still lower the threshold — the wish is the
        # signal, and it is gone from the key by then.
        sd.note("I wish you could rename these files in bulk")
        sd.note("rename these files in bulk, please")
        self.assertEqual(len(sd.suggestions()), 1, "the two phrasings are one wish")
        self.assertEqual(len(sd.candidates()), 1)

    def test_the_offer_is_made_once_and_the_read_is_what_makes_it(self):
        for _ in range(3):
            sd.note("check the printer ink level")
        block = sd.prompt_block()
        self.assertIn("check the printer ink level", block)
        self.assertIn("Offer ONCE", block)
        self.assertEqual(sd.prompt_block(), "", "the same offer must not repeat")

    def test_no_offer_while_writing_skills_is_off(self):
        for _ in range(3):
            sd.note("check the printer ink level")
        with mock.patch("memory.config_manager.get_self_forge_enabled",
                        return_value=False):
            self.assertEqual(sd.prompt_block(), "")
        # ...and the candidate is still there, unoffered, for when it is on.
        self.assertEqual(len(sd.candidates()), 1)

    def test_the_ripest_candidate_is_the_one_offered(self):
        for _ in range(3):
            sd.note("check the printer ink level")
        for _ in range(4):
            sd.note("resize every screenshot in this folder")
        self.assertIn("resize every screenshot", sd.prompt_block())

    # ── retiring ───────────────────────────────────────────────────────────

    def test_a_forge_retires_the_candidate_even_when_rephrased(self):
        for _ in range(3):
            sd.note("check the printer ink level")
        self.assertTrue(sd.mark_forged("check printer ink level", "printer_ink"))
        self.assertEqual(sd.candidates(), [])
        entry = sd.suggestions()[0]
        self.assertEqual(entry["forged"], "printer_ink")

    def test_a_forge_for_something_else_leaves_the_candidate_alone(self):
        for _ in range(3):
            sd.note("check the printer ink level")
        self.assertFalse(sd.mark_forged("book me a flight to Tokyo", "flight_finder"))
        self.assertEqual(len(sd.candidates()), 1)

    def test_dismiss_silences_without_deleting(self):
        for _ in range(3):
            sd.note("check the printer ink level")
        self.assertIn("will not bring up", sd.dismiss("printer ink"))
        self.assertEqual(sd.candidates(), [])
        self.assertEqual(len(sd.suggestions()), 1, "the count is still there")

    def test_forget_deletes_it(self):
        for _ in range(3):
            sd.note("check the printer ink level")
        sd.forget("printer ink")
        self.assertEqual(sd.suggestions(), [])

    def test_clear_empties_the_store_and_reports_how_much(self):
        sd.note("check the printer ink level")
        sd.note("resize every screenshot in this folder")
        self.assertEqual(sd.clear(), 2)
        self.assertEqual(sd.suggestions(), [])


class DiscoveryToolTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch.object(sd, "watch_path",
                                    return_value=Path(self.tmp.name) / "w.json")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_list_says_so_when_there_is_nothing(self):
        self.assertIn("not watching anything", tool.skill_discovery({"action": "list"}))

    def test_list_names_the_repeats_and_their_state(self):
        for _ in range(3):
            sd.note("check the printer ink level")
        sd.prompt_block()          # offer it
        out = tool.skill_discovery({"action": "list"})
        self.assertIn("check the printer ink level", out)
        self.assertIn("offered once", out)

    def test_forge_runs_the_real_pipeline_and_retires_the_candidate(self):
        for _ in range(3):
            sd.note("check the printer ink level")
        with mock.patch("core.skill_forge.forge",
                        return_value={"ok": True, "name": "printer_ink",
                                      "message": "I learned 'printer_ink'."}) as forge:
            out = tool.skill_discovery({"action": "forge"})
        self.assertIn("I learned", out)
        forge.assert_called_once()
        self.assertIn("check the printer ink level", forge.call_args[0][0])
        self.assertEqual(sd.candidates(), [])

    def test_a_failed_forge_leaves_the_candidate_for_a_retry(self):
        for _ in range(3):
            sd.note("check the printer ink level")
        with mock.patch("core.skill_forge.forge",
                        return_value={"ok": False, "message": "The gate refused it."}):
            tool.skill_discovery({"action": "forge"})
        self.assertEqual(len(sd.candidates()), 1)

    def test_an_unknown_action_is_named_not_guessed(self):
        self.assertIn("do not know", tool.skill_discovery({"action": "polish"}))

    def test_the_tool_declares_the_actions_it_has(self):
        self.assertEqual(tool.TOOL["name"], "skill_discovery")
        self.assertIn("action", tool.TOOL["parameters"]["required"])


class InstallerStripTest(unittest.TestCase):
    """The watch list is user data and must never ride in a download."""

    def test_clean_payload_strips_the_watch_file(self):
        path = BASE / "installer" / "build_installer.py"
        spec = importlib.util.spec_from_file_location("ice_build_installer_watch", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        source = inspect.getsource(module.clean_payload)
        self.assertIn("config/skill_watch.json", source)


if __name__ == "__main__":
    unittest.main()
