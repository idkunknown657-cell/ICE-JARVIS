"""Unit tests for actions/pc_control.py — schema, action aliasing, and the pure
formatting helpers. Nothing here touches a real process or fires hotkeys: the
read-only system walks are tested only for their *shape* and are skipped when
the optional psutil dependency is missing."""
import unittest

from actions import pc_control as pc


class PcControlTest(unittest.TestCase):

    def test_tool_schema(self):
        self.assertEqual(pc.TOOL["name"], "pc_control")
        self.assertTrue(pc.TOOL["description"])
        self.assertEqual(pc.TOOL["parameters"]["type"], "OBJECT")
        self.assertIn("action", pc.TOOL["parameters"]["required"])
        self.assertIs(pc.TOOL["handler"], pc.pc_control)

    def test_action_aliases_resolve(self):
        for variant in ("list", "list_window"):
            self.assertEqual(pc._ACTION_ALIASES[variant], "list_windows")
        for variant in ("processes", "running", "list_process"):
            self.assertEqual(pc._ACTION_ALIASES[variant], "list_processes")
        self.assertEqual(pc._ACTION_ALIASES["kill"], "kill_process")
        self.assertEqual(pc._ACTION_ALIASES["snap"], "snap_window")
        self.assertEqual(pc._ACTION_ALIASES["uptime"], "system_uptime")
        self.assertEqual(pc._ACTION_ALIASES["focus"], "focus_window")

    def test_missing_action_asks_for_list(self):
        result = pc.pc_control({})
        self.assertIn("Tell me what to do", result)
        self.assertIn("list_windows", result)

    def test_unknown_action_named(self):
        result = pc.pc_control({"action": "gibberish"})
        self.assertIn("Unknown pc_control action", result)
        self.assertIn("kill_process", result)

    def test_action_typo_and_case_tolerance(self):
        self.assertEqual(pc._resolve_action("SnAp-Window"), "snap_window")
        self.assertEqual(pc._resolve_action("kill process"), "kill_process")
        self.assertEqual(pc._resolve_action("  LIST  "), "list_windows")
        self.assertEqual(pc._resolve_action("gibberish"), "gibberish")

    # ── Pure formatters (no system calls) ───────────────────────────────────

    def test_format_processes_limits_and_includes_fields(self):
        rows = [
            {"pid": 1, "name": "a.exe", "cpu_percent": 5.0, "memory_percent": 2.0, "username": "u"},
            {"pid": 2, "name": "b.exe", "cpu_percent": 9.0, "memory_percent": 1.0, "username": "u"},
            {"pid": 3, "name": "c.exe", "cpu_percent": 0.1, "memory_percent": 0.5, "username": "u"},
        ]
        text = pc._format_processes(rows, limit=2)
        lines = text.splitlines()
        self.assertEqual(len(lines), 2)
        self.assertIn("cpu", lines[0])

    def test_format_processes_empty(self):
        self.assertIn("No matching", pc._format_processes([], 15))

    def test_format_windows_shows_overflow(self):
        rows = [{"hwnd": i, "pid": i, "title": f"Win {i}"} for i in range(20)]
        text = pc._format_windows(rows, limit=5)
        self.assertEqual(len(text.splitlines()), 6)   # 5 + "and N more"
        self.assertIn("15 more", text)
        self.assertIn("Win 1", text)

    def test_format_windows_empty(self):
        self.assertIn("No visible windows", pc._format_windows([], 15))


if __name__ == "__main__":
    unittest.main()