"""Tests for the clean-UI redesign: Home+Settings two-view workspace, the
ConversationView chat renderer, state pill/face status wiring, mute button,
quiz/content flow and the CPU-implicit pause/resume paths.

All GUI tests run against an offscreen QApplication and never touch the real
user config (config_manager paths are pointed at a temp file where needed).
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import ui  # noqa: E402


class ConversationViewTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = None
        from PyQt6.QtWidgets import QApplication
        if QApplication.instance() is None:
            cls.app = QApplication([])

    def test_classify_roles(self):
        v = ui.ConversationView("JARVIS")
        self.assertEqual(v._classify("You: hello"), ("user", "hello"))
        self.assertEqual(v._classify("User: hi"), ("user", "hi"))
        self.assertEqual(v._classify("JARVIS: have a look"), ("ai", "have a look"))
        self.assertEqual(v._classify("SYS: config loaded"), ("sys", "config loaded"))
        self.assertEqual(v._classify("ERR: api failed"), ("err", "api failed"))

    def test_classify_respects_renamed_assistant(self):
        v = ui.ConversationView("GurG")
        self.assertEqual(v._classify("GURG: yep"), ("ai", "yep"))
        # old default name no longer matches after rename
        self.assertNotEqual(v._classify("JARVIS: nope")[0], "ai")

    def test_append_log_and_clear(self):
        v = ui.ConversationView("JARVIS")
        v.append_log("You: test")
        v.append_log("JARVIS: reply")
        v.append_log("   ")            # blank lines ignored
        v.append_log("SYS: note")
        self.assertEqual(len(v._blocks), 3)
        doc = v._view.toHtml()
        self.assertIn("reply", doc)
        v.clear()
        self.assertEqual(len(v._blocks), 0)

    def test_bound_buffer(self):
        v = ui.ConversationView("JARVIS")
        for i in range(int(v._MAX_BLOCKS * 1.5)):
            v.append_log(f"JARVIS: msg {i}")
        self.assertLessEqual(len(v._blocks), v._MAX_BLOCKS)


class MainWindowRedesignTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = None
        from PyQt6.QtWidgets import QApplication
        if QApplication.instance() is None:
            cls.app = QApplication([])
        # Config manager reads from a temp file so tests never touch the user's
        # real api_keys.json.
        cls._tmp = tempfile.TemporaryDirectory()
        cls._cfg = Path(cls._tmp.name) / "api_keys.json"
        cls._cfg.write_text("{}", encoding="utf-8")
        from memory import config_manager as cm
        cls._f_patch = mock.patch.object(cm, "CONFIG_FILE", cls._cfg)
        cls._f_patch.start()
        cls._d_patch = mock.patch.object(cm, "CONFIG_DIR", Path(cls._tmp.name))
        cls._d_patch.start()
        cls._print_patch = mock.patch("builtins.print")
        cls._print_patch.start()

    @classmethod
    def tearDownClass(cls):
        cls._f_patch.stop()
        cls._d_patch.stop()
        cls._print_patch.stop()
        cls._tmp.cleanup()

    def setUp(self):
        self.wrap = ui.JarvisUI("face.png")
        self.win = self.wrap._win

    def tearDown(self):
        try:
            self.win._summon_hotkey.release()
        except Exception:
            pass

    def test_two_view_workspace(self):
        self.assertEqual(self.win._app_stack.count(), 2)
        self.assertEqual(self.win._app_stack.currentIndex(), 0)

    def test_home_stack_has_chat_content_quiz_log(self):
        self.assertEqual(self.win._home_stack.count(), 4)

    def test_settings_has_all_sections(self):
        self.assertEqual(self.win._settings_pages.count(), 13)
        labels = [self.win._settings_nav.item(i).text()
                  for i in range(self.win._settings_nav.count())]
        for want in ("GENERAL", "AI & MODELS", "API KEYS", "VOICE & LANGUAGE",
                     "MEMORY", "SCREEN AWARENESS", "PC CONTROL", "INTEGRATIONS",
                     "APPEARANCE", "STARTUP & BACKGROUND", "PRIVACY", "ADVANCED",
                     "ABOUT"):
            self.assertIn(want, labels)

    def test_drawer_toggle_switches_pages(self):
        self.win._toggle_drawer(True)
        self.assertEqual(self.win._app_stack.currentIndex(), 1)
        self.win._toggle_drawer(False)
        self.assertEqual(self.win._app_stack.currentIndex(), 0)

    def test_state_pill_and_face_status_update(self):
        self.win._apply_state("SPEAKING")
        self.assertEqual(self.win._face_status.text(), "SPEAKING")
        self.assertEqual(self.win._state_pill.text(), "◉  SPEAKING")
        self.assertTrue(self.win.hud.speaking)

    def test_content_and_quiz_flow_through_home_stack(self):
        self.win._show_content("News", "headlines")
        self.assertEqual(self.win._home_stack.currentIndex(), 1)
        self.win._quiz_sig.emit("Space", [{
            "question": "Q?", "options": ["A", "B"], "answer": 0,
        }], None)
        from PyQt6.QtWidgets import QApplication
        QApplication.processEvents()   # let the queued quiz slot run
        self.assertEqual(self.win._home_stack.currentIndex(), 2)
        self.win._quiz_hide_sig.emit()
        self.assertEqual(self.win._home_stack.currentIndex(), 0)

    def test_mute_button_styles_round_mic(self):
        self.win._toggle_mute()
        self.assertTrue(self.win._muted)
        self.assertEqual(self.win._mic_btn.text(), "🔇")
        self.win._toggle_mute()
        self.assertFalse(self.win._muted)
        self.assertEqual(self.win._mic_btn.text(), "🎙")

    def test_pause_resume_guards(self):
        self.win._set_hud_paused(True)
        self.assertFalse(self.win.hud._tmr.isActive())
        self.win._set_hud_paused(False)
        self.assertTrue(self.win.hud._tmr.isActive())

    def test_compact_toggles_face_strip(self):
        self.assertEqual(self.win._home_face_strip.maximumHeight(), ui._FACE_H)
        self.win._toggle_compact()
        self.assertEqual(self.win._home_face_strip.maximumHeight(), ui._FACE_SMALL)
        self.win._toggle_compact()
        self.assertEqual(self.win._home_face_strip.maximumHeight(), ui._FACE_H)

    def test_file_attach_hint_shows(self):
        with mock.patch("ui.Path.home", return_value=Path(self._tmp.name)):
            with mock.patch("ui.QFileDialog.getOpenFileName",
                            return_value=(str(self._tmp.name), "")):
                self.win._attach_file()
        self.assertTrue(self.win._file_hint.isVisible())


if __name__ == "__main__":
    unittest.main()