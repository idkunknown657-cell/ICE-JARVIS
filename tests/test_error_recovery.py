"""Unit tests for core/error_recovery.py — what to do about a failed step.

The classifier's value is in the invariants it holds regardless of what the model
says: nothing critical is skipped, nothing is retried past the attempt budget, and
anything touching money or accounts aborts. Those are tested against a model that
deliberately returns the wrong answer, because a model that agrees with the rules
proves nothing about whether the rules are enforced.
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import error_recovery as er


class Base(unittest.TestCase):
    def setUp(self):
        self._print = mock.patch("builtins.print")
        self._print.start()
        self.addCleanup(self._print.stop)

    def model(self, reply):
        """Stub the model. `None` means it returned nothing usable."""
        p = mock.patch("core.gemini.as_json", lambda *a, **k: reply)
        p.start()
        self.addCleanup(p.stop)

    def step(self, tool="web_search", critical=False, **over):
        data = {"step": 2, "tool": tool, "description": "find the price",
                "critical": critical, "goal": "check the price"}
        data.update(over)
        return data


class LocalReadingTest(Base):
    """The no-model fallback — never worse than the heuristic it replaces."""

    def setUp(self):
        super().setUp()
        self.model(None)

    def test_a_timeout_is_worth_another_attempt(self):
        out = er.decide(self.step(), "Connection timed out after 30s", attempt=1)
        self.assertEqual(out["decision"], er.RETRY)
        self.assertEqual(out["source"], "local")

    def test_a_lock_is_worth_another_attempt(self):
        out = er.decide(self.step(tool="file_controller"),
                        "The process cannot access the file because it is being "
                        "used by another process", attempt=1)
        self.assertEqual(out["decision"], er.RETRY)

    def test_an_http_429_is_worth_another_attempt(self):
        out = er.decide(self.step(), "429 Too Many Requests", attempt=1)
        self.assertEqual(out["decision"], er.RETRY)

    def test_a_missing_file_on_a_critical_step_becomes_a_replan(self):
        out = er.decide(self.step(tool="file_controller", critical=True),
                        "No such file or directory: report.pdf", attempt=1)
        self.assertEqual(out["decision"], er.REPLAN)

    def test_a_missing_file_on_a_throwaway_step_is_skipped(self):
        out = er.decide(self.step(tool="file_controller", critical=False),
                        "No such file or directory: report.pdf", attempt=1)
        self.assertEqual(out["decision"], er.SKIP)

    def test_a_missing_package_is_never_retried(self):
        out = er.decide(self.step(critical=True),
                        "ModuleNotFoundError: No module named 'sgp4'", attempt=1)
        self.assertEqual(out["decision"], er.REPLAN)

    def test_an_unrecognised_error_replans_rather_than_retrying(self):
        """The same call has usually already been made once, so the conservative
        reading of an unknown failure is that the approach is wrong."""
        out = er.decide(self.step(tool="file_controller"),
                        "something indescribable happened", attempt=1)
        self.assertEqual(out["decision"], er.REPLAN)

    def test_a_flaky_sounding_tool_gets_one_more_go_even_unrecognised(self):
        out = er.decide(self.step(tool="browser_control"),
                        "the page did not respond as expected", attempt=1)
        self.assertEqual(out["decision"], er.RETRY)

    def test_the_retry_budget_is_respected(self):
        out = er.decide(self.step(), "Connection timed out", attempt=2,
                        max_attempts=2)
        self.assertEqual(out["decision"], er.REPLAN)

    def test_an_unsafe_action_aborts(self):
        out = er.decide(self.step(tool="trade_order", critical=True),
                        "This would execute a payment", attempt=1)
        self.assertEqual(out["decision"], er.ABORT)

    def test_ordinary_prose_containing_a_risky_word_does_not_abort(self):
        """Every one of these reads like an unsafe action and is not one. A false
        abort ends work the user still wanted, so they are pinned here."""
        for error in ("Failed in order to parse the response",
                      "invalid format string for the date",
                      "Could not account for the missing rows",
                      "the output format was unexpected"):
            with self.subTest(error=error):
                out = er.decide(self.step(), error, attempt=2, max_attempts=2)
                self.assertNotEqual(out["decision"], er.ABORT)

    def test_a_real_commerce_phrase_still_aborts(self):
        for error in ("This would place an order on the exchange",
                      "Ready to confirm the payment",
                      "This would format the drive"):
            with self.subTest(error=error):
                self.assertEqual(
                    er.decide(self.step(), error, attempt=1)["decision"], er.ABORT)

    def test_a_decision_always_carries_a_usable_reason(self):
        out = er.decide(self.step(), "", attempt=1)
        self.assertTrue(out["reason"])
        self.assertIn(out["decision"], er.DECISIONS)


class ModelReadingTest(Base):
    def test_a_valid_model_decision_is_used(self):
        self.model({"decision": "replan", "reason": "the site changed",
                    "fix_suggestion": "use the API instead", "say": "Trying the API."})
        out = er.decide(self.step(), "element not found", attempt=1)
        self.assertEqual(out["decision"], er.REPLAN)
        self.assertEqual(out["source"], "model")
        self.assertEqual(out["fix_suggestion"], "use the API instead")
        self.assertEqual(out["say"], "Trying the API.")

    def test_an_unrecognised_decision_falls_back_to_the_local_reading(self):
        self.model({"decision": "escalate", "reason": "?"})
        out = er.decide(self.step(), "Connection timed out", attempt=1)
        self.assertEqual(out["source"], "local")
        self.assertEqual(out["decision"], er.RETRY)

    def test_a_model_reply_that_is_not_an_object_is_ignored(self):
        self.model("just some prose")
        out = er.decide(self.step(), "Connection timed out", attempt=1)
        self.assertEqual(out["source"], "local")

    def test_a_missing_decision_key_is_ignored(self):
        self.model({"reason": "no decision here"})
        out = er.decide(self.step(), "Connection timed out", attempt=1)
        self.assertEqual(out["source"], "local")


class InvariantsTest(Base):
    """What the model is not allowed to overrule, however confident it sounds."""

    def test_a_critical_step_is_never_skipped(self):
        self.model({"decision": "skip", "reason": "not essential"})
        out = er.decide(self.step(critical=True), "element not found", attempt=1)
        self.assertEqual(out["decision"], er.REPLAN)

    def test_a_non_critical_step_may_be_skipped(self):
        self.model({"decision": "skip", "reason": "not essential"})
        out = er.decide(self.step(critical=False), "element not found", attempt=1)
        self.assertEqual(out["decision"], er.SKIP)

    def test_nothing_is_retried_past_the_budget(self):
        self.model({"decision": "retry", "reason": "transient"})
        out = er.decide(self.step(), "Connection timed out", attempt=3,
                        max_attempts=2)
        self.assertEqual(out["decision"], er.REPLAN)

    def test_a_retry_is_still_allowed_inside_the_budget(self):
        self.model({"decision": "retry", "reason": "transient"})
        out = er.decide(self.step(), "Connection timed out", attempt=1,
                        max_attempts=2)
        self.assertEqual(out["decision"], er.RETRY)

    def test_an_unsafe_error_overrides_a_confident_replan(self):
        self.model({"decision": "replan", "reason": "try another way"})
        out = er.decide(self.step(tool="trade_order"),
                        "This would place an order and is irreversible", attempt=1)
        self.assertEqual(out["decision"], er.ABORT)

    def test_an_unsafe_error_overrides_a_confident_retry(self):
        self.model({"decision": "retry", "reason": "transient"})
        out = er.decide(self.step(), "the account password was rejected", attempt=1)
        self.assertEqual(out["decision"], er.ABORT)


class RobustnessTest(Base):
    def test_junk_inputs_do_not_raise(self):
        self.model(None)
        for step in (None, {}, "a string", 42, {"tool": None}):
            for error in (None, "", 42, object()):
                out = er.decide(step, error, attempt=0, max_attempts=0)
                self.assertIn(out["decision"], er.DECISIONS)

    def test_an_unreachable_model_does_not_break_the_decision(self):
        p = mock.patch("core.gemini.as_json",
                       side_effect=RuntimeError("no key"))
        p.start()
        self.addCleanup(p.stop)
        out = er.decide(self.step(), "Connection timed out", attempt=1)
        self.assertEqual(out["decision"], er.RETRY)
        self.assertEqual(out["source"], "local")

    def test_an_attempt_count_of_zero_is_treated_as_the_first(self):
        self.model(None)
        out = er.decide(self.step(), "Connection timed out", attempt=0,
                        max_attempts=2)
        self.assertEqual(out["decision"], er.RETRY)

    def test_every_decision_has_a_spoken_fallback(self):
        for decision in er.DECISIONS:
            line = er.default_line(decision)
            self.assertTrue(line)
            self.assertTrue(line.endswith("."))
        self.assertTrue(er.default_line("nonsense"))


if __name__ == "__main__":
    unittest.main()
