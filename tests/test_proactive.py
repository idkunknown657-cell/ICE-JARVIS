"""Unit tests for actions/proactive.py — trigger gating and the context-rich
prompt builder (including screen context and the configured language mode)."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from actions.proactive import ProactiveEngine, screen_glance


class TriggerGateTest(unittest.TestCase):

    def test_silence_and_cooldown_gate(self):
        eng = ProactiveEngine(min_silence_secs=60, check_cooldown=120)
        now = 1_000.0
        with mock.patch("time.monotonic", return_value=now):
            self.assertFalse(eng.should_trigger(last_user_speech=now - 30))
            # silent long enough but zero cooldown elapsed since last trigger?
            # first trigger: _last_triggered starts at 0.0 (epoch) — eligible
            self.assertTrue(eng.should_trigger(last_user_speech=now - 70))
            eng.mark_triggered()
            # immediately after → cooldown blocks
            self.assertFalse(eng.should_trigger(last_user_speech=now - 70))
        # past cooldown → allowed again
        with mock.patch("time.monotonic", return_value=now + 150):
            self.assertTrue(eng.should_trigger(last_user_speech=now - 70))


class BuildPromptTest(unittest.TestCase):

    def test_prompt_core_sections(self):
        eng = ProactiveEngine()
        text = eng.build_prompt(memory={})
        self.assertIn("[PROACTIVE_CHECK]", text)
        self.assertIn("Context about this person", text)
        self.assertIn("do not call any tools", text.lower())

    def test_screen_context_included(self):
        eng = ProactiveEngine()
        text = eng.build_prompt(
            memory={},
            screen="CODING in code — main.py — hero-insert",
            screen_events=["left chrome → code"],
        )
        self.assertIn("CODING in code", text)
        self.assertIn("left chrome", text)

    def test_monitors_and_recent_turns_included(self):
        eng = ProactiveEngine()
        text = eng.build_prompt(
            memory={},
            monitors=["rust"],
            recent_turns=["User: hi", "JARVIS: hello"],
        )
        self.assertIn("rust", text)
        self.assertIn("User: hi", text)

    def test_language_hint_reflects_language_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            with mock.patch("memory.config_manager.CONFIG_DIR", tmp_path), \
                 mock.patch("memory.config_manager.CONFIG_FILE", tmp_path / "api_keys.json"):
                from memory import config_manager as cm
                cm.save_language_mode("hinglish")
                try:
                    eng = ProactiveEngine()
                    text = eng.build_prompt(memory={})
                finally:
                    cm.save_language_mode("auto")
        self.assertIn("Hinglish", text)

    def test_timeline_context_included(self):
        eng = ProactiveEngine()
        text = eng.build_prompt(
            memory={},
            timeline=["3:04 PM — Chrome — youtube.com (6 min)"],
        )
        self.assertIn("3:04 PM", text)
        self.assertIn("youtube.com", text)

    def test_keep_flow_replaces_the_silence_rule(self):
        eng = ProactiveEngine()
        quiet = eng.build_prompt(memory={})
        self.assertIn("stay silent", quiet)
        flow = eng.build_prompt(memory={}, keep_flow=True)
        self.assertIn("companion session", flow)
        self.assertNotIn("If nothing genuinely useful comes to mind, stay silent", flow)

    def test_screen_description_glance_included(self):
        eng = ProactiveEngine()
        text = eng.build_prompt(
            memory={},
            screen_description="Editing main.py in a code editor, cursor in the proactive loop.",
        )
        self.assertIn("actual snapshot of their screen", text)
        self.assertIn("Editing main.py", text)
        self.assertIn("never mention that you looked", text)

    def test_screen_description_absent_by_default(self):
        eng = ProactiveEngine()
        text = eng.build_prompt(memory={})
        self.assertNotIn("actual snapshot of their screen", text)

    def test_personality_and_emotion_rules_voiced(self):
        eng = ProactiveEngine()
        with mock.patch("memory.config_manager.personality_hint",
                        return_value="warm wit"), \
             mock.patch("memory.config_manager.emotion_rules",
                        return_value="honest reactions"):
            text = eng.build_prompt(memory={})
        self.assertIn("Voice to use", text)
        self.assertIn("warm wit", text)
        self.assertIn("honest reactions", text)

    def test_personality_block_absent_when_off(self):
        eng = ProactiveEngine()
        with mock.patch("memory.config_manager.personality_hint",
                        return_value=""), \
             mock.patch("memory.config_manager.emotion_rules",
                        return_value=""):
            text = eng.build_prompt(memory={})
        self.assertNotIn("Voice to use", text)

    def test_recent_issues_included(self):
        eng = ProactiveEngine()
        text = eng.build_prompt(
            memory={},
            issues=[("steam_control", "store search failed: network down")],
        )
        self.assertIn("Recent problems JARVIS ran into", text)
        self.assertIn("steam_control", text)
        self.assertIn("offer to fix", text)

    def test_recent_issues_absent_by_default(self):
        eng = ProactiveEngine()
        text = eng.build_prompt(memory={})
        self.assertNotIn("Recent problems", text)
        self.assertNotIn("offer to fix", text)

    def test_usage_routines_included(self):
        eng = ProactiveEngine()
        text = eng.build_prompt(
            memory={},
            routines=[
                {"slot": "action::open_chrome", "count": 12, "days": ["2026-09-01", "2026-09-02"]},
                {"slot": "action::play_music", "count": 5, "days": ["2026-09-01"]},
            ],
        )
        self.assertIn("several different days", text)
        self.assertIn("open_chrome (12x over 2 days)", text)
        self.assertIn("never command, nag, or recite", text.lower())

    def test_usage_routines_absent_by_default(self):
        eng = ProactiveEngine()
        text = eng.build_prompt(memory={})
        self.assertNotIn("several different days", text)


class ScreenGlanceTest(unittest.TestCase):

    def test_returns_none_when_capture_fails(self):
        with mock.patch("actions.screen_processor._capture_screen", side_effect=RuntimeError("no mss")):
            self.assertIsNone(screen_glance())

    def test_returns_none_when_screen_unreadable(self):
        with mock.patch("actions.screen_processor._capture_screen",
                        return_value=(b"jpgdata", "image/jpeg")), \
             mock.patch("core.gemini.text", return_value="NOTHING") as call:
            self.assertIsNone(screen_glance())
            call.assert_called_once()

    def test_returns_summary_text(self):
        with mock.patch("actions.screen_processor._capture_screen",
                        return_value=(b"jpgdata", "image/jpeg")), \
             mock.patch("core.gemini.text",
                        return_value="They are writing code in an editor.\n"):
            self.assertEqual(screen_glance(), "They are writing code in an editor.")

    def test_returns_none_on_missing_response(self):
        with mock.patch("actions.screen_processor._capture_screen",
                        return_value=(b"jpgdata", "image/jpeg")), \
             mock.patch("core.gemini.text", return_value=""):
            self.assertIsNone(screen_glance())


class CadenceTest(unittest.TestCase):

    def test_cadence_table(self):
        self.assertEqual(ProactiveEngine.CADENCES["standard"], (900, 1200))
        self.assertEqual(ProactiveEngine.CADENCES["warm"], (180, 360))
        self.assertEqual(ProactiveEngine.CADENCES["live"], (30, 90))
        self.assertEqual(ProactiveEngine.CADENCES["chatty"], (15, 60))

    def test_apply_cadence_switches_gates(self):
        eng = ProactiveEngine()
        self.assertEqual(eng.apply_cadence("chatty"), "chatty")
        self.assertEqual((eng.min_silence_secs, eng.check_cooldown), (15, 60))
        self.assertEqual(eng.apply_cadence("live"), "live")
        self.assertEqual((eng.min_silence_secs, eng.check_cooldown), (30, 90))
        self.assertEqual(eng.apply_cadence("warm"), "warm")
        self.assertEqual((eng.min_silence_secs, eng.check_cooldown), (180, 360))
        # unknown → silent fallback to standard, never raise
        self.assertEqual(eng.apply_cadence("chaotic"), "standard")
        self.assertEqual((eng.min_silence_secs, eng.check_cooldown), (900, 1200))
        self.assertEqual(eng.apply_cadence(None), "standard")

    def test_live_cadence_triggers_often(self):
        eng = ProactiveEngine()
        eng.apply_cadence("live")
        now = 10_000.0
        with mock.patch("time.monotonic", return_value=now):
            # 40 s since last user speech > 30 s silence gate
            self.assertTrue(eng.should_trigger(last_user_speech=now - 40))
            eng.mark_triggered()
            # 45 s later → cooldown (90) not yet elapsed
            self.assertFalse(eng.should_trigger(last_user_speech=now - 40))
        with mock.patch("time.monotonic", return_value=now + 100):
            self.assertTrue(eng.should_trigger(last_user_speech=now - 40))


if __name__ == "__main__":
    unittest.main()