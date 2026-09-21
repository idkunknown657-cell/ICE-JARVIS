"""Unit tests for actions/goal_agent.py — the Understand→Search→Navigate→Act
loop: planning, op dispatch, verification, failure handling, and confirmation."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import actions.goal_agent as ga

# Hardcode the auto flag away from user config in tests
AUTONOMOUS = True


def _fake_player():
    p = mock.Mock()
    p.write_log = mock.Mock()
    return p


class PlannerLoopTests(unittest.TestCase):

    def test_requires_a_goal(self):
        out = ga.goal_agent({}, player=_fake_player())
        self.assertIn("Tell me a goal", out)

    def test_respects_off_switch(self):
        with mock.patch("memory.config_manager.get_goal_agent_enabled",
                        return_value=False):
            out = ga.goal_agent({"goal": "do anything"}, player=_fake_player())
        self.assertIn("turned off", out)
        self.assertIn("goal_agent_enabled", out)

    def test_phases_logged_then_done(self):
        p = _fake_player()
        with mock.patch("actions.goal_agent.gemini.as_json",
                        side_effect=[
                            {"phases": ["search", "open", "find", "download"]},
                            {"op": "done", "params": {"summary": "set the wallpaper."}},
                        ]):
            out = ga.goal_agent({"goal": "set a wallpaper"}, player=p)
        self.assertTrue(out.startswith("Done."))
        self.assertIn("set the wallpaper", out)
        self.assertTrue(any("Plan:" in str(c) for c in p.write_log.call_args_list))

    def test_ask_op_returns_question(self):
        with mock.patch("actions.goal_agent.gemini.as_json",
                        side_effect=[
                            {"phases": ["search"]},
                            {"op": "ask", "params": {
                                "question": "which style do you want?"}},
                        ]):
            out = ga.goal_agent({"goal": "find a wallpaper"}, player=_fake_player())
        self.assertIn("which style do you want?", out)

    def test_never_writes_json_fences(self):
        with mock.patch("actions.goal_agent.gemini.as_json",
                        side_effect=[
                            {"phases": ["search"]},
                            {"op": "search", "params": {"query": "4k wallpaper"},
                             "expect": "results"},
                            {"op": "done", "params": {"summary": "ok."}},
                        ]):
            with mock.patch("actions.goal_agent._op_search",
                            return_value="wallhaven search results") as s:
                out = ga.goal_agent({"goal": "a wallpaper"}, player=_fake_player())
        s.assert_called_once()
        self.assertIn("ok.", out)

    def test_budget_exhausted_message(self):
        with mock.patch("actions.goal_agent._max_steps", return_value=5), \
             mock.patch("actions.goal_agent.gemini.as_json",
                        side_effect=[{"phases": ["a"]}] + [
                            {"op": "search", "params": {"query": "x"},
                             "expect": "y"}] * 10):
            with mock.patch("actions.goal_agent._op_search",
                            return_value="some results"):
                out = ga.goal_agent({"goal": "loop"}, player=_fake_player())
        self.assertIn("ran out of steps", out)


class FailureHandlingTests(unittest.TestCase):

    def test_three_failures_stops_with_ask(self):
        with mock.patch("actions.goal_agent.gemini.as_json",
                        side_effect=[{"phases": ["s"]}] + [
                            {"op": "search", "params": {"query": "x"},
                             "expect": "y"}] * 3):
            with mock.patch("actions.goal_agent._op_search",
                            return_value="FAILED: no results"):
                out = ga.goal_agent({"goal": "x"}, player=_fake_player())
        self.assertIn("three approaches failed", out)

    def test_three_unverified_stops(self):
        with mock.patch("actions.goal_agent.gemini.as_json",
                        side_effect=[{"phases": ["s"]}] + [
                            {"op": "verify_screen", "params": {"expect": "x"},
                             "expect": "y"}] * 3):
            with mock.patch("actions.goal_agent._op_verify_screen",
                            return_value="UNVERIFIED: nope"):
                out = ga.goal_agent({"goal": "x"}, player=_fake_player())
        self.assertIn("kept failing", out)

    def test_verification_resets_failure_counter(self):
        scripted = [{"phases": ["s"]},
                    {"op": "search", "params": {"query": "x"}, "expect": "r"},
                    {"op": "browser", "params": {"action": "get_url"},
                     "expect": "url"},
                    {"op": "done", "params": {"summary": "ok."}}]
        with mock.patch("actions.goal_agent.gemini.as_json",
                        side_effect=scripted), \
             mock.patch("actions.goal_agent._op_search",
                        return_value="FAILED: try again"), \
             mock.patch("actions.goal_agent._op_browser",
                        return_value="VERIFIED: url ok"):
            with mock.patch("actions.goal_agent._looks_like_failure",
                            side_effect=lambda t: "FAILED" in t):
                out = ga.goal_agent({"goal": "x"}, player=_fake_player())
        self.assertTrue(out.startswith("Done."))


class OperationDispatchTests(unittest.TestCase):

    def test_open_url_passes_through_to_browser(self):
        import actions.browser_control as bc
        with mock.patch.object(bc, "browser_control", return_value="Opened it"):
            out = ga._op_open_url({"url": "https://x.example"}, None)
        self.assertEqual(out, "Opened it")

    def test_browser_unknown_action_fails(self):
        out = ga._op_browser({"action": "fly"}, None)
        self.assertIn("FAILED", out)

    def test_desktop_unknown_action_fails(self):
        out = ga._op_desktop({"action": "beam"}, None)
        self.assertIn("FAILED", out)

    def test_download_writes_file(self):
        buf = bytes(2048)
        with mock.patch("actions.goal_agent._autonomous", return_value=True), \
             mock.patch("actions.goal_agent._really_download",
                        return_value="Downloaded x.jpg to D (2.0 KB)."):
            out = ga._op_download({"url": "https://x/y.jpg"}, None)
        self.assertIn("Downloaded", out)

    def test_download_asks_when_not_autonomous(self):
        called = {}
        with mock.patch("actions.goal_agent._autonomous", return_value=False), \
             mock.patch.object(ga, "_CONFIRM", True), \
             mock.patch.object(ga.confirm, "pending_title", return_value=""), \
             mock.patch.object(ga.confirm, "request") as req:
            req.side_effect = lambda *a, **k: called.setdefault("yes", True)
            out = ga._op_download({"url": "https://x/y.jpg"}, None)
        self.assertIn("[CONFIRMATION_PENDING]", out)
        self.assertTrue(called.get("yes"))

    def test_verify_file_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "x.zip"
            f.write_bytes(b"x" * 5000)
            out = ga._op_verify_file({"path": str(f), "min_bytes": 1000}, None)
        self.assertTrue(out.startswith("VERIFIED:"))

    def test_verify_file_missing(self):
        out = ga._op_verify_file({"path": "C:/nonexistent/x.zip"}, None)
        self.assertTrue(out.startswith("UNVERIFIED:"))
        self.assertIn("not found", out)

    def test_unknown_op_fails(self):
        self.assertIn("unknown operation", ga._run_op("warp", {}, None))


class RealDownloadTests(unittest.TestCase):

    def test_really_download_streams_and_reports_size(self):
        resp = mock.MagicMock()
        resp.__enter__.return_value = resp
        resp.read.side_effect = [b"x" * 1024, b""]
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "f.bin"
            with mock.patch("urllib.request.urlopen", return_value=resp), \
                 mock.patch("urllib.request.Request") as req:
                req.return_value = object()
                out = ga._really_download("https://x/f", dest)
            self.assertIn("f.bin", out)
            self.assertIn("KB", out)

    def test_really_download_error(self):
        with mock.patch("urllib.request.urlopen", side_effect=RuntimeError("net")):
            out = ga._really_download("https://x/f", Path("dummy.bin"))
        self.assertTrue(out.startswith("FAILED:"))


class TOOLShapeTest(unittest.TestCase):

    def test_tool_shape(self):
        self.assertEqual(ga.TOOL["name"], "goal_agent")
        self.assertIs(ga.TOOL["handler"], ga.goal_agent)
        self.assertEqual(ga.TOOL["parameters"]["type"], "OBJECT")
        self.assertIn("goal", ga.TOOL["parameters"]["required"])


if __name__ == "__main__":
    unittest.main()