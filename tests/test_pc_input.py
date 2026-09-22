"""
Regression tests for core/pc_input (dependency-free PC control engine) and the
extended actions/computer_control tool.

SAFETY: these tests never move the real mouse, press real keys, write the real
clipboard, or kill processes. They cover pure functions, read-only queries and
the confirm-gate behaviour of destructive actions only.
"""
import platform
import unittest
from pathlib import Path

import core.pc_input as pc
from actions.computer_control import computer_control

_WIN = platform.system() == "Windows"


class KeyMapping(unittest.TestCase):
    def test_vk_enter(self):
        self.assertEqual(pc.vk_for_name("enter"), 0x0D)

    def test_vk_volumeup(self):
        self.assertEqual(pc.vk_for_name("volumeup"), 0xAF)

    def test_vk_win(self):
        self.assertEqual(pc.vk_for_name("win"), 0x5B)

    def test_vk_ctrl(self):
        self.assertEqual(pc.vk_for_name("ctrl"), 0x11)

    def test_vk_f_keys(self):
        self.assertEqual(pc.vk_for_name("f12"), 0x7B)
        self.assertEqual(pc.vk_for_name("f24"), 0x87)

    def test_vk_esc_alias(self):
        self.assertEqual(pc.vk_for_name("escape"), pc.vk_for_name("esc"))

    def test_vk_unknown_is_none(self):
        self.assertIsNone(pc.vk_for_name("flurble"))


class ShimSurface(unittest.TestCase):
    """The engine exposes a pyautogui-compatible shim — every name must exist."""

    def test_shim_names(self):
        for name in ("press", "hotkey", "scroll", "hscroll", "write", "typewrite",
                     "click", "doubleClick", "moveTo", "dragTo", "position", "size",
                     "screenshot"):
            self.assertTrue(callable(getattr(pc, name, None)), name)


@unittest.skipUnless(_WIN, "Win32-only queries")
class ReadOnlyQueries(unittest.TestCase):
    def test_screen_size(self):
        w, h = pc.screen_size()
        self.assertIsInstance(w, int)
        self.assertIsInstance(h, int)
        self.assertGreater(w, 0)

    def test_mouse_position(self):
        px, py = pc.mouse_position()
        self.assertIsInstance(px, int)
        self.assertIsInstance(py, int)

    def test_window_list_shape(self):
        rows = pc.window_list(limit=10)
        self.assertIsInstance(rows, list)
        self.assertTrue(all(len(r) == 3 for r in rows))

    def test_process_list_shape(self):
        procs = pc.process_list()
        self.assertIsInstance(procs, list)
        self.assertTrue(all(len(r) == 3 for r in procs))

    def test_screen_monitors(self):
        mons = pc.screen_monitors()
        self.assertTrue(any(m.get("width", 0) > 0 for m in mons))

    def test_system_info(self):
        info = pc.system_info()
        self.assertTrue(info.get("system"))

    def test_clipboard_get(self):
        self.assertIsInstance(pc.clipboard_get(), str)


class ComputerControlDispatch(unittest.TestCase):
    """Dispatch surface — never fires physical input."""

    def test_mouse_position(self):
        self.assertIn(",", computer_control({"action": "mouse_position"}))

    def test_unknown_action(self):
        self.assertIn("Unknown action", computer_control({"action": "nope_x"}))

    def test_missing_action(self):
        self.assertIn("No action", computer_control({}))

    def test_kill_missing_process_ends_nothing(self):
        """A kill request for a process that does not exist must end nothing
        and say so — never fabricate success."""
        from actions import pc_control
        out = str(pc_control.pc_control({"action": "kill_process",
                                         "name": "junk_test_proc.exe"}))
        self.assertIn("Nothing was ended", out)
        self.assertNotIn("Killed", out)
        self.assertNotIn("Terminated", out)

    def test_kill_refuses_without_confirm_interface(self):
        """With no confirmation interface wired, kill_process must refuse —
        the confirm gate is what stands between a request and a kill."""
        from actions import pc_control
        saved_confirm, saved_find = pc_control._CONFIRM, pc_control._find_pids
        try:
            pc_control._CONFIRM = False
            pc_control._find_pids = lambda name, pid=None: [object()]
            out = str(pc_control._kill_process({"name": "junk_test_proc.exe"}))
            self.assertIn("no confirmation interface", out.lower())
            self.assertNotIn("Killed", out)
        finally:
            pc_control._CONFIRM, pc_control._find_pids = saved_confirm, saved_find

    def test_kill_never_reports_done(self):
        from actions import pc_control
        out = pc_control._kill_process({"name": "junk_test_proc.exe"})
        self.assertNotIn("Killed", str(out))
        self.assertNotIn("Terminated", str(out))


class ToolDeclaration(unittest.TestCase):
    def test_tools_discovered(self):
        from core.action_loader import discover_actions
        reg = discover_actions(Path("actions"), logger=lambda _s: None)
        self.assertTrue(reg.has("computer_control"))
        self.assertTrue(reg.has("computer_settings"))


if __name__ == "__main__":
    unittest.main()
