"""Tests for core/strategy_memory — learned UI-interaction strategies."""
import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import strategy_memory as sm


class StrategyMemoryTest(unittest.TestCase):
    def setUp(self):
        self._tmp = mock.patch.object(
            sm, "STRATEGIES_PATH",
            Path(__file__).parent / "_tmp_ui_strategies.json")
        self._tmp.start()
        self._file = sm.STRATEGIES_PATH
        try:
            self._file.unlink()
        except FileNotFoundError:
            pass

    def tearDown(self):
        self._tmp.stop()
        try:
            self._file.unlink()
        except FileNotFoundError:
            pass

    def test_record_and_trusted_hint(self):
        for _ in range(2):
            sm.record("chrome.exe", "download button", "uia", True,
                      "element confirmed")
        hint = sm.hints_for("chrome", "download")
        self.assertIn("download button", hint)
        self.assertIn("uia", hint)

    def test_untrusted_until_min_seen(self):
        sm.record("chrome", "download button", "uia", True)
        self.assertEqual(sm.hints_for("chrome", "download"), "")

    def test_low_ratio_not_hinted(self):
        for ok in (True, True, False, False):
            sm.record("vscode", "run button", "uia", ok)
        self.assertEqual(sm.hints_for("vscode", "run"), "")

    def test_app_isolation(self):
        sm.record("chrome", "search box", "uia", True)
        sm.record("chrome", "search box", "uia", True)
        self.assertEqual(sm.hints_for("firefox"), "")
        self.assertIn("search box", sm.hints_for("chrome"))

    def test_fuzzy_item_match(self):
        sm.record("steam", "big blue install button", "uia", True)
        sm.record("steam", "big blue install button", "uia", True)
        self.assertIn("install", sm.hints_for("steam", "install button"))

    def test_summary_and_clear(self):
        self.assertEqual("0", sm.summary().split()[0])
        sm.record("app", "thing", "uia", True)
        sm.record("app", "thing", "uia", True)
        self.assertIn("1 learned", sm.summary())
        sm.clear()
        self.assertEqual(sm.hints_for("app"), "")

    def test_corrupt_store_is_tolerated(self):
        self._file.parent.mkdir(parents=True, exist_ok=True)
        self._file.write_text("{not json", encoding="utf-8")
        sm.record("app", "x", "uia", True)          # must not raise
        self.assertEqual(sm.hints_for("app"), "")

    def test_hints_capped_at_five_lines(self):
        for i in range(8):
            for _ in range(2):
                sm.record("app", f"button {i}", "uia", True)
        hint = sm.hints_for("app")
        self.assertLessEqual(hint.count("\n- "), 5)


if __name__ == "__main__":
    unittest.main()
