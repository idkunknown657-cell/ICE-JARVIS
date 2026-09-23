"""Tests for core/pc_log.py — the PC-control debug log (design brief §30).

Rules under test: structured line written, spaces quoted, None fields skipped,
typed-text contents NEVER logged, rotation happens, and the whole thing can
never raise into a control action.
"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import pc_log


class PcLogTest(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "pc_control.log"

    def tearDown(self):
        self.dir.cleanup()

    def _event(self, name="screen_ai.click", **fields):
        with mock.patch.object(pc_log, "_log_path", return_value=self.path):
            pc_log.event(name, **fields)

    def test_writes_a_structured_line(self):
        self._event(app="Chrome", item="Download", method="uia",
                    box="1020,340", action="click-center", verify="ok")
        text = self.path.read_text(encoding="utf-8")
        self.assertIn("screen_ai.click", text)
        self.assertIn("app=Chrome", text)
        self.assertIn("method=uia", text)
        self.assertIn("verify=ok", text)

    def test_none_and_empty_fields_are_skipped(self):
        self._event("x", app=None, item="")
        text = self.path.read_text(encoding="utf-8")
        self.assertIn("x", text)
        self.assertNotIn("app", text)
        self.assertNotIn("item", text)

    def test_values_with_spaces_are_quoted(self):
        self._event("y", result="clicked the thing")
        text = self.path.read_text(encoding="utf-8")
        self.assertIn('result="clicked the thing"', text)

    def test_typed_text_contents_are_never_logged(self):
        secret = pc_log.note_text("hunter2-password")
        self.assertEqual(secret, "text<16 chars>")
        self.assertNotIn("hunter2", secret)
        # And even if a raw text field arrives, only pc_log.note_text's length
        # form should be used by callers — event itself must not crash on it.
        self._event("z", text=secret)
        self.assertIn("text<16 chars>", self.path.read_text(encoding="utf-8"))

    def test_never_raises_when_the_file_cannot_be_written(self):
        with mock.patch("builtins.open", side_effect=OSError("disk full")):
            pc_log.event("nope", app="X")     # must simply return

    def test_never_raises_on_a_hostile_field(self):
        class Bad:
            def __str__(self):
                raise ValueError("unstringable")

        with mock.patch.object(pc_log, "_log_path", return_value=self.path):
            pc_log.event("bad", field=Bad())  # must simply return

    def test_rotates_a_large_log(self):
        self.path.write_bytes(b"x" * (512 * 1024 + 10))
        self._event("after-rotate")
        text = self.path.read_text(encoding="utf-8")
        self.assertIn("after-rotate", text)
        self.assertNotIn("x" * 1000, text)
        self.assertTrue((Path(self.dir.name) / "pc_control.log.1").exists())


if __name__ == "__main__":
    unittest.main()
