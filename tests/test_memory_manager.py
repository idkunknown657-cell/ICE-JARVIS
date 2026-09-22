"""Unit tests for memory/memory_manager.py — save/load/search/format round-trips
against a throwaway long_term.json (never touches the real memory store)."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from memory import memory_manager as mm


class MemoryManagerTest(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self._memory_file = tmp / "long_term.json"
        self._patcher = mock.patch.object(mm, "MEMORY_PATH", self._memory_file)
        self._patcher.start()
        self.addCleanup(self._patcher.stop)
        # memory_manager prints emoji status lines that crash on cp1252 consoles
        self._print = mock.patch("builtins.print")
        self._print.start()
        self.addCleanup(self._print.stop)

    def test_update_and_load_round_trip(self):
        mm.update_memory({"identity": {"name": {"value": "Mark"}}})
        data = mm.load_memory()
        self.assertEqual(
            data["identity"]["name"]["value"], "Mark",
        )

    def test_search_memory_finds_stored_value(self):
        mm.update_memory({"projects": {"jarvis": {"value": "screen-aware assistant"}}})
        result = mm.search_memory("screen-aware assistant", limit=5)
        self.assertIn("screen-aware", result)

    def test_format_memory_for_prompt_contains_entries(self):
        mm.update_memory({"identity": {"language": {"value": "Hindi"}}})
        text = mm.format_memory_for_prompt(mm.load_memory())
        self.assertIn("Hindi", text)

    def test_remember_and_forget(self):
        self.assertIn("Remembered", mm.remember("birthday", "March 1", "identity"))
        found = mm.search_memory("March", limit=5)
        self.assertIn("March", found)
        self.assertIn("Forgotten", mm.forget("birthday", "identity"))

    def test_session_summary_round_trip(self):
        mm.save_session_summary("We discussed the API.", "English")
        last = mm.pop_last_session()
        self.assertIsNotNone(last)
        self.assertIn("API", last["summary"])
        # popped once — a second pop returns nothing
        self.assertIsNone(mm.pop_last_session())


if __name__ == "__main__":
    unittest.main()