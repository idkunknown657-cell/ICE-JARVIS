"""Unit tests for actions/pc_drills.py — the self-training practice tool.

The drill is passive by design (find → point, never click), so every test
either patches the engine entirely or exercises a refusal path. No real mouse
ever moves, no real screen is ever read, the ledger stays in a temp file.
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import self_training as st
from actions import pc_drills


def _fake_target():
    from core.pc_engine import Target
    return Target(label="the Send button", x=610, y=580, w=80, h=28,
                  source="uia", confidence=0.9)


class Base(unittest.TestCase):
    """Throwaway ledger, training on, memory on, throttles wide open."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        p = mock.patch.object(st, "STATE_PATH", root / "self_training.json")
        p.start()
        self.addCleanup(p.stop)
        self._state = st._STATE
        st._STATE = None
        self.addCleanup(lambda: setattr(st, "_STATE", self._state))
        cfg = mock.patch.object(st, "_config", return_value=(True, "focused", True))
        cfg.start()
        self.addCleanup(cfg.stop)
        self._print = mock.patch("builtins.print")
        self._print.start()
        self.addCleanup(self._print.stop)


class GateTest(Base):
    def test_no_target_is_an_honest_ask(self):
        out = pc_drills.training_run({})
        self.assertIn("needs something to look for", out)

    def test_pc_control_off_refuses_and_is_not_scored_as_an_aim_miss(self):
        with mock.patch.object(pc_drills.autonomy, "get_mode",
                               side_effect=lambda name: name != "pc_control"):
            out = pc_drills.training_run({"target": "the Send button"})
        self.assertIn("PC control is off", out)
        # power != aim: the recorded note names the off switch, not a drill aim
        cap = st._load()["competency"].get("pc_control", {})
        self.assertTrue(all("pc control is off" in n for n in cap.get("failures", [])))
        self.assertNotIn("tool_use", st._load()["competency"])

    def test_policy_gate_blocks_when_the_table_says_so(self):
        with mock.patch.object(pc_drills.autonomy, "gate_reason",
                               return_value=("money — always asks. The user must "
                                             "confirm this on screen before it happens.")):
            out = pc_drills.training_run({"target": "x"})
        self.assertIn("not allowed", out)
        # the block was recorded as tool_use evidence, not pc_control
        self.assertIn("tool_use", st._load()["competency"])

    def test_passive_drill_is_allowed_by_the_real_policy_table(self):
        """The whole point: a read-only drill must pass the gate the user was
        shown. If this ever fails, the tool and the HUD disagree."""
        self.assertEqual(pc_drills._policy_gate(False), "")

    def test_throttled_drill_refuses_without_touching_the_screen(self):
        with mock.patch.object(st, "cycle_due", return_value=False), \
             mock.patch.object(pc_drills.pc_engine, "point_at") as pa, \
             mock.patch.object(pc_drills.pc_engine, "describe_screen") as ds:
            out = pc_drills.training_run({"target": "the Send button"})
        self.assertIn("not due", out)
        pa.assert_not_called()
        ds.assert_not_called()


class DrillFlowTest(Base):
    def setUp(self):
        super().setUp()
        self.target = _fake_target()
        # a caller that has been quiet for an hour — the throttle then passes
        import time as _t
        self.quiet_player = type("P", (), {})()
        self.quiet_player._last_user_speech = _t.monotonic() - 3600

    def _run(self, target="the Send button", **kw):
        return pc_drills.training_run({"target": target, **kw},
                                      player=self.quiet_player)

    def test_pass_read_records_evidence_and_reports(self):
        with mock.patch.object(pc_drills.pc_engine, "describe_screen",
                               return_value="a browser window with a compose box"), \
             mock.patch.object(pc_drills.pc_engine, "point_at") as pa, \
             mock.patch.object(pc_drills.autonomy, "feed") as fed:
            pa.return_value = type("R", (), {"ok": True,
                                             "detail": "Pointer on 'the Send button' at 610,580 via uia"})()
            out = self._run("the Send button", asked_by_user=True)
        pa.assert_called_once_with("the Send button")
        self.assertIn("Drill done", out)
        self.assertIn("No click", out)
        fed.assert_called_once()                      # the feed saw the drill
        rows = {r["capability"]: r for r in st.competency()}
        self.assertEqual(rows["pc_control"]["attempts"], 1)
        self.assertEqual(rows["pc_control"]["wins"], 1)
        self.assertEqual(rows["screen"]["wins"], 1)   # the read half passed too

    def test_miss_records_a_fail_and_never_claims_success(self):
        with mock.patch.object(pc_drills.pc_engine, "describe_screen",
                               return_value="a browser window"), \
             mock.patch.object(pc_drills.pc_engine, "point_at") as pa:
            pa.return_value = type("R", (), {"ok": False,
                                             "detail": "I could not find 'nope' on screen, "
                                                       "so I did not move the pointer."})()
            out = self._run("nope")
        self.assertIn("honest miss", out)
        rows = {r["capability"]: r for r in st.competency()}
        self.assertEqual(rows["pc_control"]["wins"], 0)
        self.assertEqual(rows["pc_control"]["attempts"], 1)
        # the failure note is in the ledger, so the next idle cycle can drill it
        cap = st._load()["competency"]["pc_control"]
        self.assertTrue(any("drill" in n for n in cap["failures"]))

    def test_broken_describer_counts_as_screen_fail_but_drill_continues(self):
        with mock.patch.object(pc_drills.pc_engine, "describe_screen",
                               side_effect=RuntimeError("no mss")), \
             mock.patch.object(pc_drills.pc_engine, "point_at") as pa:
            pa.return_value = type("R", (), {"ok": True, "detail": "pointed at 10,10"})()
            out = self._run("something")
        self.assertIn("Drill done", out)
        rows = {r["capability"]: r for r in st.competency()}
        self.assertEqual(rows["screen"]["wins"], 0)   # read failed…
        self.assertEqual(rows["pc_control"]["wins"], 1)   # …but the aim still passed

    def test_self_initiated_drill_reports_the_schedule_not_an_error(self):
        with mock.patch.object(st, "cycle_due", return_value=False):
            out = pc_drills.training_run({"target": "x", "asked_by_user": False},
                                         player=self.quiet_player)
        self.assertIn("not due", out)
        self.assertIn("practise while", out)


class ToolContractTest(Base):
    def test_tool_shape_matches_the_loader_contract(self):
        self.assertEqual(pc_drills.TOOL["name"], "training_run")
        self.assertTrue(callable(pc_drills.TOOL["handler"]))
        self.assertEqual(pc_drills.TOOL["parameters"]["type"], "OBJECT")
        self.assertIn("target", pc_drills.TOOL["parameters"]["required"])
        for name, spec in pc_drills.TOOL["parameters"]["properties"].items():
            self.assertIn("type", spec, name)
            self.assertTrue(spec.get("description", "").strip(), name)

    def test_handler_signature_matches_the_dispatch(self):
        import inspect
        sig = inspect.signature(pc_drills.TOOL["handler"])
        self.assertEqual(list(sig.parameters)[0], "parameters")

    def test_description_promises_only_what_the_code_does(self):
        """The description must never promise a click — that promise is the
        safety contract the autonomy gate was told about."""
        desc = pc_drills.TOOL["description"].lower()
        self.assertIn("no click", desc.replace("without clicking", "no click")
                      .replace("(no click)", "no click"))
        self.assertNotIn("clicks the", desc)


class MainWiringTest(unittest.TestCase):
    def test_training_rules_block_is_prompt_ready(self):
        import main as M
        rules = M._training_rules()
        self.assertIn("training_run", rules)
        self.assertIn("WITHOUT clicking", rules)

    def test_training_rules_empty_when_training_off(self):
        import main as M
        from core import self_training
        with mock.patch.object(self_training, "enabled", return_value=False):
            self.assertEqual(M._training_rules(), "")


if __name__ == "__main__":
    unittest.main()
