"""Tests for core/cancel.py (stop token), actions/stop_task.py (the tool that
raises it) and goal_agent's per-step check — design brief §34: the user must
always be able to halt running automation.

Nothing here touches the desktop: only the flag and mocked planner calls.
"""
import unittest
from unittest import mock

from core import cancel
import actions.stop_task as st
import actions.goal_agent as ga


def _fake_player():
    p = mock.Mock()
    p.write_log = mock.Mock()
    return p


class CancelTokenTest(unittest.TestCase):

    def tearDown(self):
        cancel.clear()

    def test_fresh_begin_is_not_stopped(self):
        cancel.begin()
        self.assertFalse(cancel.stopped())

    def test_request_makes_it_stopped(self):
        cancel.begin()
        cancel.request()
        self.assertTrue(cancel.stopped())

    def test_begin_clears_a_stale_stop(self):
        # A stop pressed while idle must never kill the NEXT run.
        cancel.request()
        cancel.begin()
        self.assertFalse(cancel.stopped())

    def test_clear_drops_the_request(self):
        cancel.begin()
        cancel.request()
        cancel.clear()
        self.assertFalse(cancel.stopped())


class StopTaskToolTest(unittest.TestCase):

    def tearDown(self):
        cancel.clear()

    def test_tool_shape(self):
        self.assertEqual(st.TOOL["name"], "stop_task")
        self.assertTrue(callable(st.TOOL["handler"]))
        self.assertIn("stop", st.TOOL["description"].lower())
        self.assertEqual(st.TOOL["parameters"]["type"], "OBJECT")

    def test_handler_requests_stop(self):
        cancel.begin()
        out = st.stop_task({})
        self.assertTrue(cancel.stopped())
        self.assertIn("Stopping", out)

    def test_second_call_reports_already_requested(self):
        cancel.begin()
        st.stop_task({})
        out = st.stop_task({})
        self.assertIn("already", out.lower())
        self.assertTrue(cancel.stopped())

    def test_never_claims_to_undo_finished_work(self):
        cancel.begin()
        out = st.stop_task({})
        self.assertIn("Finished work is left untouched", out)


class GoalAgentStopTest(unittest.TestCase):

    def tearDown(self):
        cancel.clear()

    def test_stop_halts_before_the_next_step(self):
        def plan(goal):
            cancel.request()          # user hits stop mid-run, after planning
            return ["phase one"]

        with mock.patch.object(ga, "_plan_phases", plan):
            out = ga.goal_agent({"goal": "do the thing"}, player=_fake_player())
        self.assertIn("Stopped", out)

    def test_stale_stop_cannot_kill_a_new_run(self):
        cancel.request()              # pressed while nothing was running
        with mock.patch.object(ga, "_plan_phases", lambda g: ["p"]), \
             mock.patch.object(ga, "_next_step",
                               lambda g, o: {"op": "done",
                                             "params": {"summary": "all good"}}):
            out = ga.goal_agent({"goal": "x"}, player=_fake_player())
        self.assertIn("Done", out)
        self.assertFalse(cancel.stopped())


if __name__ == "__main__":
    unittest.main()
