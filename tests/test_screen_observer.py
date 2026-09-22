"""Unit tests for core/screen_observer.py — activity classification and the
observer's change-event logic. The live window query is monkey-patched so tests
never touch the real desktop."""
import time
import unittest
from unittest import mock

from core.screen_observer import ScreenObserver, classify_activity


class ClassifyTest(unittest.TestCase):

    def assert_cat(self, app, title, expected):
        snap = {"app": app, "title": title}
        cat, sub = classify_activity(snap)
        self.assertEqual(cat, expected, f"app={app!r} title={title!r} → {cat}")

    def test_browsing(self):
        self.assert_cat("chrome", "github - Google Chrome", "BROWSING")
        self.assert_cat("", "YouTube — Mozilla Firefox", "BROWSING")

    def test_coding(self):
        self.assert_cat("Code", "server.py — visual-studio-code", "CODING")
        self.assert_cat("", "foo.py - Notepad++", "CODING")
        self.assert_cat("WindowsTerminal", "bash", "CODING")

    def test_gaming(self):
        self.assert_cat("steam", "Cyberpunk", "GAMING")
        self.assert_cat("", "VALORANT", "GAMING")

    def test_watching_video(self):
        self.assert_cat("", "youtube.com", "WATCHING_VIDEO")
        self.assert_cat("vlc", "movie.mkv", "WATCHING_VIDEO")

    def test_communication_and_other(self):
        self.assert_cat("discord", "friends", "COMMUNICATION")
        self.assert_cat("spotify", "playlist", "MUSIC")
        self.assert_cat("explorer", "Documents", "FILE_MANAGEMENT")

    def test_unknown(self):
        cat, _ = classify_activity({"app": "some-random-app", "title": "???"})
        self.assertEqual(cat, "UNKNOWN")


class ScreenObserverTest(unittest.TestCase):

    def _fake_windows(self, app, title):
        return lambda: {"title": title, "app": app, "pid": 999}

    def test_sample_populates_snapshot(self):
        with mock.patch("core.screen_observer._active_window",
                        self._fake_windows("chrome", "Google - Search")):
            obs = ScreenObserver(enabled=True)
            snap = obs.sample_once(now=100.0)
        self.assertEqual(snap["category"], "BROWSING")
        self.assertIs(snap["active"], True)
        self.assertEqual(snap["app"], "chrome")

    def test_change_records_event_once(self):
        fake = self._fake_windows("Code", "app.py")
        with mock.patch("core.screen_observer._active_window", fake):
            obs = ScreenObserver(enabled=True)
            obs.sample_once(now=100.0)
            # same window again (before debounce) → no second event
            obs.sample_once(now=100.5)
        self.assertEqual(len(obs.events), 1)

        # new window after debounce → second event with 'before' context
        with mock.patch("core.screen_observer._active_window",
                        self._fake_windows("chrome", "youtube.com")):
            obs.sample_once(now=110.0)
        self.assertEqual(len(obs.events), 2)
        ev = obs.events[-1]
        self.assertEqual(ev["before"]["app"], "code")

    def test_humanize(self):
        with mock.patch("core.screen_observer._active_window",
                        self._fake_windows("vlc", "movie.mkv")):
            obs = ScreenObserver(enabled=True)
            obs.sample_once(now=1.0)
        txt = obs.humanize()
        self.assertIn("WATCHING_VIDEO", txt)
        self.assertIn("vlc", txt)

    def test_disabled_observer_stays_empty(self):
        with mock.patch("core.screen_observer._active_window",
                        self._fake_windows("chrome", "x")):
            obs = ScreenObserver(enabled=False)
            obs.sample_once(now=1.0)
        self.assertEqual(obs.snapshot()["active"], False)

    def test_activity_timeline_records_spans(self):
        obs = ScreenObserver(enabled=True)
        with mock.patch("core.screen_observer._active_window",
                        self._fake_windows("Code", "app.py")):
            obs.sample_once(now=100.0)
            with mock.patch("core.screen_observer._active_window",
                            self._fake_windows("chrome", "youtube.com")):
                obs.sample_once(now=200.0)
        lines = obs.activity_timeline(now=1000.0)
        self.assertEqual(len(lines), 1)              # first span finalized
        self.assertIn("code", lines[0])
        self.assertIn("app.py", lines[0])
        self.assertIn("(2 min)", lines[0])           # 100 s → ~2 min duration
        # nothing yet for the still-open span (chrome)

    def test_timeline_empty_when_no_history(self):
        obs = ScreenObserver(enabled=True)
        self.assertEqual(obs.activity_timeline(now=1000.0), [])

    def test_disable_clears_timeline(self):
        obs = ScreenObserver(enabled=True)
        with mock.patch("core.screen_observer._active_window",
                        self._fake_windows("Code", "app.py")):
            obs.sample_once(now=100.0)
        obs.set_enabled(False)
        self.assertEqual(obs.activity_timeline(now=1000.0), [])


if __name__ == "__main__":
    unittest.main()