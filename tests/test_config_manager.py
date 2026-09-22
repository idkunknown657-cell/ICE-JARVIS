"""Unit tests for memory/config_manager.py — settings defaults, round-trips and
clamping, isolated to a throwaway config file via monkey-patching the module's
path constants (no real config/api_keys.json is touched)."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from memory import config_manager as cm


class ConfigManagerTest(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self._patchers = [
            mock.patch.object(cm, "CONFIG_DIR", tmp),
            mock.patch.object(cm, "CONFIG_FILE", tmp / "api_keys.json"),
        ]
        for p in self._patchers:
            p.start()
            self.addCleanup(p.stop)

    # ── Defaults ────────────────────────────────────────────────────────────

    def test_defaults(self):
        self.assertEqual(cm.get_language_mode(), "auto")
        self.assertIs(cm.get_screen_awareness(), True)
        self.assertIs(cm.get_screen_glance(), True)
        self.assertIs(cm.get_proactive_enabled(), True)
        self.assertIs(cm.get_memory_enabled(), True)
        self.assertIs(cm.get_verify_clicks(), True)
        self.assertEqual(cm.get_observe_interval(), 5)
        self.assertEqual(cm.get_assistant_name(), "JARVIS")
        # Ambient / tracking / cadence additions
        self.assertIs(cm.get_ambient_mode(), False)
        self.assertIs(cm.get_track_activity(), True)
        self.assertEqual(cm.get_ambient_interval_min(), 10)
        self.assertEqual(cm.get_talk_cadence(), "chatty")   # companion default

    # ── Language mode round-trip ────────────────────────────────────────────

    def test_language_mode_round_trip(self):
        cm.save_language_mode("hindi")
        self.assertEqual(cm.get_language_mode(), "hindi")
        cm.save_language_mode("hinglish")
        self.assertEqual(cm.get_language_mode(), "hinglish")

    def test_language_mode_invalid_collapses(self):
        cm.save_language_mode("klingon")
        self.assertEqual(cm.get_language_mode(), "auto")

    def test_language_hint_content(self):
        self.assertIn("Hindi", cm.language_hint("hindi"))
        self.assertIn("Hinglish", cm.language_hint("hinglish"))
        self.assertIn("English", cm.language_hint("english"))
        self.assertEqual(cm.language_hint("auto"), "")
        self.assertEqual(cm.language_hint(), "")     # default mode is auto

    # ── Privacy + proactivity flags ─────────────────────────────────────────

    def test_privacy_flags_round_trip(self):
        cm.save_screen_awareness(False)
        self.assertIs(cm.get_screen_awareness(), False)
        cm.save_screen_awareness(True)
        self.assertIs(cm.get_screen_awareness(), True)

        cm.save_screen_glance(False)
        self.assertIs(cm.get_screen_glance(), False)
        cm.save_screen_glance(True)
        self.assertIs(cm.get_screen_glance(), True)

        cm.save_proactive_enabled(False)
        self.assertIs(cm.get_proactive_enabled(), False)

        cm.save_memory_enabled(False)
        self.assertIs(cm.get_memory_enabled(), False)

        cm.save_verify_clicks(False)
        self.assertIs(cm.get_verify_clicks(), False)

    def test_observe_interval_clamped(self):
        cm.save_observe_interval(99)
        self.assertEqual(cm.get_observe_interval(), 60)
        cm.save_observe_interval(0)
        self.assertEqual(cm.get_observe_interval(), 2)
        cm.save_observe_interval("garbage")
        self.assertEqual(cm.get_observe_interval(), 5)

    # ── Existing helpers still work on the temp file ────────────────────────

    def test_assistant_name_round_trip(self):
        cm.save_assistant_config("FRIDAY", "Tony")
        self.assertEqual(cm.get_assistant_name(), "FRIDAY")
        self.assertEqual(cm.get_user_name(), "Tony")

    # ── Ambient + talk cadence round-trips ─────────────────────────────────

    def test_ambient_mode_round_trip(self):
        cm.save_ambient_mode(True)
        self.assertIs(cm.get_ambient_mode(), True)
        cm.save_ambient_mode(False)
        self.assertIs(cm.get_ambient_mode(), False)

    def test_ambient_interval_clamped(self):
        cm.save_ambient_interval_min(0)
        self.assertEqual(cm.get_ambient_interval_min(), 3)
        cm.save_ambient_interval_min(999)
        self.assertEqual(cm.get_ambient_interval_min(), 60)
        cm.save_ambient_interval_min("garbage")
        self.assertEqual(cm.get_ambient_interval_min(), 10)

    def test_talk_cadence_round_trip_and_collapse(self):
        cm.save_talk_cadence("live")
        self.assertEqual(cm.get_talk_cadence(), "live")
        cm.save_talk_cadence("warm")
        self.assertEqual(cm.get_talk_cadence(), "warm")
        cm.save_talk_cadence("nonsense")
        self.assertEqual(cm.get_talk_cadence(), "chatty")   # collapses to the default

    def test_track_activity_round_trip(self):
        cm.save_track_activity(False)
        self.assertIs(cm.get_track_activity(), False)
        cm.save_track_activity(True)
        self.assertIs(cm.get_track_activity(), True)

    def test_default_voice_round_trip_and_collapse(self):
        self.assertEqual(cm.get_voice(), "Aoede")   # soft female default
        cm.save_voice("Kore")
        self.assertEqual(cm.get_voice(), "Kore")
        cm.save_voice("Leda")
        self.assertEqual(cm.get_voice(), "Leda")
        cm.save_voice("not-a-voice")
        self.assertEqual(cm.get_voice(), "Aoede")

    def test_voice_friendly_aliases(self):
        cm.save_voice("soft girl")
        self.assertEqual(cm.get_voice(), "Aoede")
        cm.save_voice("cute")
        self.assertEqual(cm.get_voice(), "Leda")
        cm.save_voice("bright")
        self.assertEqual(cm.get_voice(), "Zephyr")
        cm.save_voice("deep")
        self.assertEqual(cm.get_voice(), "Charon")

    def test_currency_default_and_round_trip(self):
        self.assertEqual(cm.get_currency_code(), "US")
        cm.save_currency_code("in")
        self.assertEqual(cm.get_currency_code(), "IN")
        cm.save_currency_code("")
        self.assertEqual(cm.get_currency_code(), "US")

    def test_currency_symbols(self):
        cm.save_currency_code("IN")
        self.assertEqual(cm.currency_symbol(), "₹")
        cm.save_currency_code("GB")
        self.assertEqual(cm.currency_symbol(), "£")
        cm.save_currency_code("US")

    def test_goal_agent_defaults(self):
        self.assertIs(cm.get_goal_agent_enabled(), True)
        self.assertIs(cm.get_goal_agent_auto(), True)
        self.assertEqual(cm.get_goal_agent_steps(), 16)

    def test_goal_agent_round_trips(self):
        cm.save_goal_agent_enabled(False)
        self.assertIs(cm.get_goal_agent_enabled(), False)
        cm.save_goal_agent_enabled(True)
        self.assertIs(cm.get_goal_agent_enabled(), True)
        cm.save_goal_agent_auto(False)
        self.assertIs(cm.get_goal_agent_auto(), False)
        cm.save_goal_agent_auto(True)
        self.assertIs(cm.get_goal_agent_auto(), True)
        cm.save_goal_agent_steps(9)
        self.assertEqual(cm.get_goal_agent_steps(), 9)
        cm.save_goal_agent_steps(200)
        self.assertEqual(cm.get_goal_agent_steps(), 40)
        cm.save_goal_agent_steps(2)
        self.assertEqual(cm.get_goal_agent_steps(), 5)

    def test_chatty_cadence_accepted(self):
        cm.save_talk_cadence("chatty")
        self.assertEqual(cm.get_talk_cadence(), "chatty")

    # ── Humour & narration (personality engine) ─────────────────────────────

    def test_humor_default_is_playful(self):
        self.assertEqual(cm.get_humor_level(), "playful")

    def test_humor_round_trip_and_collapse(self):
        cm.save_humor_level("subtle")
        self.assertEqual(cm.get_humor_level(), "subtle")
        cm.save_humor_level("chaotic")
        self.assertEqual(cm.get_humor_level(), "chaotic")
        cm.save_humor_level("robotic")
        self.assertEqual(cm.get_humor_level(), "playful")

    def test_narrate_actions_default_on(self):
        self.assertIs(cm.get_narrate_actions(), True)

    def test_narrate_actions_round_trip(self):
        cm.save_narrate_actions(False)
        self.assertIs(cm.get_narrate_actions(), False)
        cm.save_narrate_actions(True)
        self.assertIs(cm.get_narrate_actions(), True)

    def test_personality_hint_off_is_empty(self):
        self.assertEqual(cm.personality_hint("off"), "")

    def test_personality_hint_playful_has_humour(self):
        self.assertIn("witty", cm.personality_hint("playful"))
        self.assertIn("kind", cm.personality_hint("playful"))

    def test_personality_hint_chaotic_fullest(self):
        self.assertIn("Maximum personality", cm.personality_hint("chaotic"))

    def test_narration_hint_off_is_empty(self):
        self.assertEqual(cm.narration_hint(enabled=False), "")

    def test_narration_hint_has_recap_rule(self):
        self.assertIn("recap", cm.narration_hint(enabled=True))

    def test_hints_respect_saved_config(self):
        cm.save_humor_level("off")
        cm.save_narrate_actions(False)
        self.assertEqual(cm.personality_hint(), "")
        self.assertEqual(cm.narration_hint(), "")

    # ── Emotional honesty engine ────────────────────────────────────────────

    def test_emotion_depth_default_is_full(self):
        self.assertEqual(cm.get_emotion_depth(), "full")

    def test_emotion_depth_round_trip_and_collapse(self):
        cm.save_emotion_depth("light")
        self.assertEqual(cm.get_emotion_depth(), "light")
        cm.save_emotion_depth("off")
        self.assertEqual(cm.get_emotion_depth(), "off")
        cm.save_emotion_depth("theatrical")
        self.assertEqual(cm.get_emotion_depth(), "full")

    def test_emotion_rules_levels(self):
        self.assertIn("composed", cm.emotion_rules("off"))
        self.assertIn("quiet satisfaction", cm.emotion_rules("light"))
        self.assertIn("full range", cm.emotion_rules("full"))

    def test_emotion_rules_respect_saved_config(self):
        cm.save_emotion_depth("off")
        self.assertIn("composed", cm.emotion_rules())
        cm.save_emotion_depth("full")


if __name__ == "__main__":
    unittest.main()