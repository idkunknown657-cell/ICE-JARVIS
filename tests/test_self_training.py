"""Unit tests for core/self_training.py — the idle self-training loop.

Never touches the real ledger, the real memory store or the network: every test
points the state/memory/improvement paths at a temp directory and patches the
model call.
"""
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import learning
from core import self_training as st
from memory import memory_manager as mm


def _rules(*texts):
    return {"rules": [{"situation": "the dialog has two buttons",
                       "rule": t} for t in texts]}


class Base(unittest.TestCase):
    """Throwaway ledger + memory files, training on, memory on."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        for target, attr, value in (
            (st, "STATE_PATH", root / "self_training.json"),
            (mm, "MEMORY_PATH", root / "long_term.json"),
            (learning, "IMPROVEMENTS_PATH", root / "improvements.json"),
        ):
            p = mock.patch.object(target, attr, value)
            p.start()
            self.addCleanup(p.stop)
        self._state_before = st._STATE
        st._STATE = None
        self.addCleanup(self._restore_state)
        self.addCleanup(mock.patch.object(st, "_CONFIG_CACHE",
                                          (0.0, True, "balanced", True)).stop)
        patcher = mock.patch.object(st, "_config",
                                    return_value=(True, "balanced", True))
        patcher.start()
        self.addCleanup(patcher.stop)
        self._print = mock.patch("builtins.print")
        self._print.start()
        self.addCleanup(self._print.stop)

    def _restore_state(self):
        st._STATE = self._state_before


class LedgerTest(Base):
    def test_record_outcome_tracks_a_real_win_rate(self):
        for ok in (True, True, False, True):
            st.record_outcome("pc_control", ok)
        rows = st.competency()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["attempts"], 4)
        self.assertEqual(rows[0]["wins"], 3)
        self.assertAlmostEqual(rows[0]["score"], 0.75, places=3)

    def test_unknown_capability_is_not_lost(self):
        st.record_outcome("telepathy", True)
        self.assertEqual(st.competency()[0]["capability"], "tool_use")

    def test_record_outcome_never_raises_on_junk(self):
        for junk in (None, 42, "", object()):
            st.record_outcome(junk, True, note=junk)
        st.record_outcome("pc_control", True, note=object())
        self.assertTrue(st.competency())

    def test_pc_log_verdicts_become_evidence(self):
        st.note_pc_event("pc_engine.click", {"verify": "ok", "item": "Play"})
        st.note_pc_event("pc_engine.click", {"verify": "no-visible-change",
                                             "item": "Play"})
        st.note_pc_event("screen_ai.find", {"verify": "failed", "item": "Save"})
        rows = {r["capability"]: r for r in st.competency()}
        self.assertEqual(rows["pc_control"]["attempts"], 2)
        self.assertEqual(rows["pc_control"]["wins"], 1)
        self.assertEqual(rows["screen"]["attempts"], 1)
        self.assertEqual(rows["screen"]["wins"], 0)

    def test_unreadable_verdicts_are_ignored_not_guessed(self):
        for verdict in ("", "unverified", "skipped", "mystery"):
            st.note_pc_event("pc_engine.click", {"verify": verdict})
        self.assertEqual(st.competency(), [])

    def test_weakest_is_lowest_score_with_evidence(self):
        for _ in range(4):
            st.record_outcome("keyboard", True)
        for _ in range(4):
            st.record_outcome("screen", False)
        st.record_outcome("planning", True)          # 1 attempt: no evidence
        cap, _ = st._weakest(st._load())
        self.assertEqual(cap, "screen")

    def test_weakest_without_evidence_practises_conversation(self):
        cap, _ = st._weakest(st._load())
        self.assertEqual(cap, "conversation")


class CycleTest(Base):
    def test_cycle_writes_rules_to_memory_and_the_audit_log(self):
        # two attempts: one failure is not evidence enough to drill
        st.record_outcome("screen", True, "")
        st.record_outcome("screen", False, "screen_ai.find: no match")
        with mock.patch.object(st, "gemini") as g:
            g.as_json.return_value = _rules("press Tab to the next unnamed control")
            report = st.run_cycle("idle")
        self.assertTrue(report["ok"])
        self.assertEqual(report["capability"], "screen")
        self.assertEqual(report["learned"], 1)
        self.assertTrue(report["self_scored"])
        mem = mm.load_memory()
        drills = [k for k in mem["training"] if k.startswith("drill_screen_")]
        self.assertEqual(len(drills), 1)
        self.assertIn("press Tab", mem["training"][drills[0]]["value"])
        self.assertEqual(st.stats()["cycles"], 1)
        self.assertEqual(len(st.drills()), 1)
        log = learning._load_improvements().get("log") or []
        self.assertTrue(any(it["category"] == "self_training" for it in log))

    def test_second_identical_round_learns_nothing_and_says_so(self):
        st.record_outcome("screen", False, "screen_ai.find: no match")
        with mock.patch.object(st, "gemini") as g:
            g.as_json.return_value = _rules("press Tab to the next unnamed control")
            st.run_cycle("idle")
        with mock.patch.object(st, "gemini") as g:
            g.as_json.return_value = _rules("press Tab to the next unnamed control")
            report = st.run_cycle("idle")
        self.assertEqual(report["learned"], 0)
        self.assertEqual(len(st.drills()), 1)        # not stored twice
        self.assertEqual(st.stats()["cycles"], 2)    # but the round is recorded

    def test_empty_answer_is_a_correct_answer(self):
        with mock.patch.object(st, "gemini") as g:
            g.as_json.return_value = {"rules": []}
            report = st.run_cycle("idle")
        self.assertTrue(report["ok"])
        self.assertEqual(report["learned"], 0)
        self.assertEqual(st.drills(), [])

    def test_broken_model_learns_nothing_and_never_raises(self):
        with mock.patch.object(st, "gemini") as g:
            g.as_json.side_effect = RuntimeError("boom")
            report = st.run_cycle("idle")
        self.assertTrue(report["ok"])
        self.assertEqual(report["learned"], 0)

    def test_junk_rules_are_discarded(self):
        junk = {"rules": [None, "x", {"rule": ""}, {"rule": "short"},
                          {"situation": 5, "rule": "do " + "x" * 500}]}
        with mock.patch.object(st, "gemini") as g:
            g.as_json.return_value = junk
            report = st.run_cycle("idle")
        self.assertEqual(report["learned"], 1)       # only the long-but-real one
        self.assertLessEqual(len(st.drills()[0]["rule"]), st._MAX_RULE_CHARS)

    def test_memory_off_refuses_even_a_forced_round(self):
        with mock.patch.object(st, "_config", return_value=(True, "balanced", False)):
            report = st.run_cycle("manual", force=True)
        self.assertFalse(report["ok"])
        self.assertEqual(report["reason"], "memory-off")

    def test_disabled_refuses_but_force_overrides(self):
        with mock.patch.object(st, "_config", return_value=(False, "balanced", True)):
            self.assertFalse(st.run_cycle("idle")["ok"])
            self.assertFalse(st.enabled())
            with mock.patch.object(st, "gemini") as g:
                g.as_json.return_value = {"rules": []}
                self.assertTrue(st.run_cycle("manual", force=True)["ok"])

    def test_only_three_rules_per_round(self):
        with mock.patch.object(st, "gemini") as g:
            g.as_json.return_value = {"rules": [
                {"situation": "no name on the control",
                 "rule": "walk the window with Tab"},
                {"situation": "dialog opened",
                 "rule": "re-read the window before clicking again"},
                {"situation": "field already had text",
                 "rule": "clear the field first, then type"},
                {"situation": "window moved mid-read",
                 "rule": "discard a stale reading and look once more"},
            ]}
            report = st.run_cycle("idle")
        self.assertEqual(report["learned"], st._MAX_RULES_PER_CYCLE)

    def test_near_identical_rules_are_one_rule(self):
        """Four paraphrases of the same instruction are one lesson, not four."""
        with mock.patch.object(st, "gemini") as g:
            g.as_json.return_value = _rules("re-read the window before clicking again",
                                            "re-read the window before you click again",
                                            "before clicking, re-read the window again")
            report = st.run_cycle("idle")
        self.assertEqual(report["learned"], 1)


class ThrottleTest(Base):
    def test_quiet_time_is_respected(self):
        # balanced = 90 s of quiet before a round is even considered
        self.assertFalse(st.cycle_due(5))
        self.assertTrue(st.cycle_due(120))

    def test_hourly_budget_is_a_hard_cap(self):
        for _ in range(2):                       # balanced allows 2/hour
            st.record_outcome("pc_control", True)
            st._load()["history"].append({"ts": time.time(), "learned": 0})
        self.assertFalse(st.cycle_due(600))
        with mock.patch.object(st, "_config", return_value=(True, "focused", True)):
            self.assertTrue(st.cycle_due(600))   # focused allows 5

    def test_old_rounds_leave_the_budget(self):
        two_hours_ago = time.time() - 7200
        st._load()["history"] = [{"ts": two_hours_ago, "learned": 0},
                                 {"ts": two_hours_ago, "learned": 0}]
        self.assertTrue(st.cycle_due(600))

    def test_disabled_or_memory_off_never_passes(self):
        with mock.patch.object(st, "_config", return_value=(False, "balanced", True)):
            self.assertFalse(st.cycle_due(3600))
        with mock.patch.object(st, "_config", return_value=(True, "balanced", False)):
            self.assertFalse(st.cycle_due(3600))

    def test_garbage_idle_values_do_not_pass(self):
        for bad in (None, "ages", -5, object()):
            self.assertFalse(st.cycle_due(bad))


class SnapshotTest(Base):
    def test_snapshot_sends_both_the_list_and_the_count(self):
        """Regression: `drills` was one key, and stats()'s integer count
        overwrote the list of drills on the way to the UI."""
        st.record_outcome("pc_control", False)
        with mock.patch.object(st, "gemini") as g:
            g.as_json.return_value = _rules("re-read the window before clicking again")
            st.run_cycle("idle")
        snap = st.snapshot()
        self.assertIsInstance(snap["drills"], list)
        self.assertEqual(snap["drill_count"], len(snap["drills"]))
        self.assertEqual(snap["drills"][0]["rule"],
                         "re-read the window before clicking again")
        self.assertIsInstance(snap["competency"], list)
        self.assertIn("enabled", snap)

    def test_digest_names_the_weakest_and_its_rules(self):
        st.record_outcome("pc_control", False)
        with mock.patch.object(st, "gemini") as g:
            g.as_json.return_value = _rules("wait for the window to settle after a click")
            st.run_cycle("idle")
        text = st.curriculum_digest()
        self.assertIn("PC control", text)
        self.assertIn("wait for the window to settle", text)

    def test_digest_is_empty_when_training_is_off(self):
        with mock.patch.object(st, "_config", return_value=(False, "balanced", True)):
            self.assertEqual(st.curriculum_digest(), "")

    def test_snapshot_never_raises_on_a_corrupt_ledger(self):
        st.STATE_PATH.write_text("{not json", encoding="utf-8")
        st._STATE = None
        snap = st.snapshot()
        self.assertEqual(snap["cycles"], 0)
        self.assertEqual(snap["competency"], [])


class ReversibilityTest(Base):
    def _one_drill(self):
        st.record_outcome("keyboard", False, "discord.send: box not found")
        with mock.patch.object(st, "gemini") as g:
            g.as_json.return_value = _rules("read the message box back before pressing Enter")
            st.run_cycle("idle")
        return st.drills()[0]

    def test_forget_removes_the_rule_from_memory_too(self):
        drill = self._one_drill()
        self.assertTrue(st.forget(drill["id"]))
        self.assertEqual(st.drills(), [])
        self.assertEqual((mm.load_memory().get("training") or {}), {})

    def test_forget_unknown_id_is_just_false(self):
        self.assertFalse(st.forget("nope"))
        self.assertFalse(st.forget(""))
        self.assertFalse(st.forget(None))

    def test_forget_all_and_reset(self):
        self._one_drill()
        before = learning._load_improvements().get("log") or []
        self.assertEqual(st.forget_all(), 1)
        self.assertEqual(st.drills(), [])
        st.record_outcome("pc_control", True)
        st.reset()
        self.assertEqual(st.competency(), [])
        self.assertEqual(st.stats()["cycles"], 0)
        # the audit log is not a setting: what happened stays on record
        self.assertEqual(learning._load_improvements().get("log"), before)

    def test_forget_survives_a_missing_memory_store(self):
        drill = self._one_drill()
        with mock.patch.object(mm, "load_memory", side_effect=RuntimeError("gone")):
            self.assertTrue(st.forget(drill["id"]))
        self.assertEqual(st.drills(), [])


class PayoffTest(Base):
    """VERIFY: a rule is judged by the outcomes that arrived after it was written.

    The loop used to drill and remember without ever asking whether the drilling
    worked, so the playbook could only grow. These tests pin the opposite: a
    verdict needs new evidence, a rule that did not help is reported as such,
    and a capability that stops moving is practised differently instead of
    harder — including on the smarter model.
    """

    def _round(self, *rules):
        with mock.patch.object(st, "gemini") as g:
            g.as_json.return_value = _rules(*rules)
            return st.run_cycle("idle"), g

    def test_a_rule_is_measuring_until_there_is_evidence(self):
        st.record_outcome("screen", False, "screen_ai.find: no match")
        st.record_outcome("screen", True)
        report, _ = self._round("check the window list before searching again")
        self.assertEqual(report["learned"], 1)
        # the round that wrote it cannot score it: nothing has happened since
        self.assertEqual(report["checked"], [])
        self.assertEqual(st.drills()[0]["verdict"], "measuring")
        self.assertIsNone(st.drills()[0]["delta"])

    def test_a_rule_that_improved_the_record_says_helped(self):
        for _ in range(3):
            st.record_outcome("screen", False, "screen_ai.find: no match")
        self._round("check the window list before searching again")
        # real evidence arrives after the rule was written, and it is better
        for _ in range(5):
            st.record_outcome("screen", True)
        report, _ = self._round("a different rule this time round")
        self.assertEqual(len(report["checked"]), 1)
        check = report["checked"][0]
        self.assertEqual(check["capability"], "screen")
        self.assertEqual(check["verdict"], "helped")
        self.assertGreater(check["delta"], 0)
        self.assertEqual(check["attempts"], 5)
        self.assertEqual(st.drills()[1]["verdict"], "helped")
        self.assertEqual(st.stats()["helped"], 1)

    def test_a_rule_that_changed_nothing_says_flat_not_helped(self):
        for _ in range(4):
            st.record_outcome("keyboard", False, "discord.send: box not found")
        self._round("read the message box back before pressing Enter")
        st.record_outcome("keyboard", False)          # same record, one new try
        report, _ = self._round("try a different focus route")
        self.assertEqual(report["checked"][0]["verdict"], "flat")
        self.assertEqual(st.stats()["helped"], 0)
        self.assertEqual(st.stats()["flat"], 1)

    def test_a_regression_is_practised_before_a_stable_capability(self):
        """Planning reads 75% and screen 62%, so a plain "lowest score wins"
        rule would drill screen — but planning has just fallen off a cliff while
        screen is only mediocre, and the fall is the thing still worth stopping."""
        for ok in (True,) * 9 + (False,) * 3:      # planning: 75%, but −0.50 trend
            st.record_outcome("planning", ok)
        for ok in (1, 0, 1, 0, 1, 0, 1, 1, 0, 1, 0, 1, 1):
            st.record_outcome("screen", bool(ok))  # screen: 62%, no real trend
        rows = {r["capability"]: r for r in st.competency()}
        self.assertGreater(rows["planning"]["score"], rows["screen"]["score"])
        cap, _ = st._weakest(st._load())
        self.assertEqual(cap, "planning")

    def test_a_steady_capability_is_still_practised_first_when_it_is_worse(self):
        """The regression penalty re-orders near-ties, it does not chase noise:
        a genuinely worse record is still the one that gets drilled."""
        for ok in (True,) * 4 + (False,) * 4:      # planning: 50%, falling slowly
            st.record_outcome("planning", ok)
        for ok in (1, 0, 1, 0, 1, 0, 1, 1, 0, 1, 0, 1, 1):
            st.record_outcome("screen", bool(ok))  # screen: 62%, worse penalty-free
        cap, _ = st._weakest(st._load())
        self.assertEqual(cap, "planning")

    def test_stalled_practice_is_drilled_differently_and_on_the_smarter_model(self):
        for _ in range(4):
            st.record_outcome("screen", False, "screen_ai.find: no match")
        st._load()["stuck"] = {"screen": 1}      # one earlier round went nowhere
        report, g = self._round("stop and ask before guessing at a control")
        self.assertTrue(report["escalated"])
        self.assertEqual(g.as_json.call_args.kwargs["tier"], st._ESCALATED_TIER)
        prompt = g.as_json.call_args.args[0]
        self.assertIn("HAS NOT MOVED", prompt)

    def test_a_clean_capability_stays_on_the_cheap_model(self):
        for _ in range(3):
            st.record_outcome("screen", False, "screen_ai.find: no match")
        report, g = self._round("check the window list before searching again")
        self.assertFalse(report["escalated"])
        self.assertNotEqual(g.as_json.call_args.kwargs["tier"], st._ESCALATED_TIER)

    def test_a_round_that_writes_nothing_counts_as_stalled(self):
        with mock.patch.object(st, "gemini") as g:
            g.as_json.return_value = {"rules": []}
            st.run_cycle("idle")
        self.assertEqual(st.stats()["focus_stuck"], 1)
        with mock.patch.object(st, "gemini") as g:
            g.as_json.return_value = {"rules": []}
            report = st.run_cycle("idle")
        self.assertTrue(report["escalated"])

    def test_the_digest_prefers_rules_that_proved_themselves(self):
        for _ in range(3):
            st.record_outcome("screen", False, "screen_ai.find: no match")
        self._round("check the window list before searching again")
        for _ in range(5):
            st.record_outcome("screen", True)
        self._round("switch to the keyboard route after two misses")  # scoring round
        self.assertEqual(st.drills()[1]["verdict"], "helped")
        # one proven rule and one still measuring, both for the same capability:
        # the digest must lead with the one that has already paid off.
        text = st.curriculum_digest(limit=1)
        self.assertIn("check the window list", text)
        self.assertNotIn("switch to the keyboard route", text)
        self.assertIn("weakest", text)

    def test_payoff_reports_what_the_playbook_is_worth(self):
        for _ in range(3):
            st.record_outcome("screen", False, "screen_ai.find: no match")
        self._round("check the window list before searching again")
        p = st.payoff()
        self.assertEqual(p["total"], 1)
        self.assertEqual(p["counts"]["measuring"], 1)
        self.assertIn("helped", st.snapshot())
        self.assertIsInstance(st.snapshot()["slipping"], list)

    def test_verdicts_survive_a_reload_of_the_ledger(self):
        for _ in range(3):
            st.record_outcome("screen", False, "screen_ai.find: no match")
        self._round("check the window list before searching again")
        for _ in range(5):
            st.record_outcome("screen", True)
        report, _ = self._round("switch to the keyboard route after two misses")
        self.assertEqual(report["checked"][0]["verdict"], "helped")
        st.flush()
        st._STATE = None                           # read it back from disk
        self.assertEqual(st.drills()[1]["verdict"], "helped")
        self.assertEqual(st.stats()["helped"], 1)

    def test_a_corrupt_stuck_map_is_ignored_not_believed(self):
        st._load()["stuck"] = {"screen": "lots", "keyboard": None}
        st.flush()
        st._STATE = None
        self.assertEqual(st.stats()["focus_stuck"], 0)
        self.assertTrue(st.snapshot())


if __name__ == "__main__":
    unittest.main()
