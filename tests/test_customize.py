"""Unit tests for ui.CustomizeOverlay — construction with hostile config values
never crashes, and the new Eyes/Reactions controls read + write config_manager
against a throwaway config file (never the real one)."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class CustomizeOverlayTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = None
        from PyQt6.QtWidgets import QApplication
        if QApplication.instance() is None:
            cls.app = QApplication([])

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._cfg = Path(self._tmp.name) / "api_keys.json"
        self._cfg.write_text("{}", encoding="utf-8")
        from memory import config_manager as cm
        self._f_patch = mock.patch.object(cm, "CONFIG_FILE", self._cfg)
        self._f_patch.start()
        self._d_patch = mock.patch.object(cm, "CONFIG_DIR", Path(self._tmp.name))
        self._d_patch.start()
        self.addCleanup(self._d_patch.stop)
        self.addCleanup(self._f_patch.stop)
        self._print = mock.patch("builtins.print")
        self._print.start()
        self.addCleanup(self._print.stop)

    def _open(self, **kw):
        from ui import CustomizeOverlay
        return CustomizeOverlay(parent=None, **kw)

    def test_constructs_and_builds_all_sections(self):
        ov = self._open()
        for attr, label in (
            ("_name_input", "name box"), ("_share_btn", "screen share toggle"),
            ("_glance_btn", "glance toggle"), ("_quality_btns", "quality pills"),
            ("_cadence_btns", "cadence pills"), ("_humour_btns", "humour pills"),
            ("_emotion_btns", "emotion pills"), ("_proactive_btn", "proactive toggle"),
            ("_memory_btn", "memory toggle"), ("_timeline_btn", "timeline toggle"),
        ):
            self.assertTrue(hasattr(ov, attr), msg=f"missing {label}")
        self.assertEqual(set(ov._quality_btns), {"light", "medium", "high"})
        self.assertEqual(set(ov._cadence_btns),
                         {"standard", "warm", "live", "chatty"})

    def test_constructs_with_hostile_config_values(self):
        # Garbage in the store must not take the overlay down.
        self._cfg.write_text(
            '{"screen_awareness": "yes", "observe_interval": "oops", '
            '"talk_cadence": "chaos", "screen_share_quality": 9}', encoding="utf-8")
        ov = self._open(assistant_name=12345, ui_color=999)
        self.assertIsNotNone(ov._share_btn)

    def test_toggle_flips_the_saved_setting_instantly(self):
        from memory.config_manager import get_screen_awareness, get_screen_glance
        ov = self._open()
        self.assertTrue(get_screen_awareness())      # default on
        ov._share_btn.click()                        # → OFF
        self.assertFalse(get_screen_awareness())
        self.assertTrue(get_screen_glance())
        ov._glance_btn.click()                       # → OFF
        self.assertFalse(get_screen_glance())
        ov._share_btn.click()                        # → ON again
        self.assertTrue(get_screen_awareness())

    def test_quality_and_cadence_pills_persist(self):
        from memory.config_manager import (
            get_screen_share_quality, get_talk_cadence, get_humor_level,
        )
        ov = self._open()
        ov._on_quality_pick("light")
        self.assertEqual(get_screen_share_quality(), "light")
        ov._on_cadence_pick("chatty")
        self.assertEqual(get_talk_cadence(), "chatty")
        ov._on_humour_pick("chaotic")
        self.assertEqual(get_humor_level(), "chaotic")

    def test_apply_emits_identity_signature(self):
        ov = self._open(assistant_name="JARVIS", user_name="Mark",
                        ui_color="#00d4ff", voice="Kore")
        with mock.patch.object(ov, "hide"):
            results = []
            ov.saved.connect(lambda *a: results.append(a))
            ov._save()
        self.assertEqual(len(results), 1)
        name, user, color, voice = results[0]
        self.assertEqual((name, user), ("JARVIS", "Mark"))


if __name__ == "__main__":
    unittest.main()