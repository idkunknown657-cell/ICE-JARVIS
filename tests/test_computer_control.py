"""Unit tests for actions/computer_control.py — the coordinate-correctness fixes
(DPI mapping, clamping, pointer read-back) and the AI locate/click/move verbs.

The real desktop is never touched: `pyautogui` and the vision call are mocked.
"""
import unittest
from unittest import mock

from PIL import Image

from actions import computer_control as cc


class FakeGui:
    """Minimal pyautogui stand-in that records what was asked of it."""

    def __init__(self, size=(1600, 900), pos=(10, 10)):
        self._size = size
        self._pos = pos
        self.moves = []
        self.clicks = []

    def size(self):
        return self._size

    def screenshot(self, *a, **k):
        return Image.new("RGB", self._size, (20, 20, 20))

    def moveTo(self, x, y, duration=0.0):
        self.moves.append((x, y))
        self._pos = (x, y)

    def moveRel(self, dx, dy, duration=0.0):
        self._pos = (self._pos[0] + dx, self._pos[1] + dy)

    def position(self):
        return self._pos

    def click(self, x=None, y=None, button="left", clicks=1):
        if x is not None:
            self._pos = (x, y)
        self.clicks.append((x, y, button, clicks))


class ClampTest(unittest.TestCase):

    def test_clamp_inside_passthrough(self):
        with mock.patch.object(cc, "pyautogui", FakeGui(size=(1600, 900))):
            self.assertEqual(cc._clamp_to_screen(100, 200), (100, 200))

    def test_clamp_over_and_negative(self):
        with mock.patch.object(cc, "pyautogui", FakeGui(size=(1600, 900))):
            # Out-of-range clamps inside, then is nudged off the corner.
            self.assertEqual(cc._clamp_to_screen(5000, 5000), (1598, 898))
            self.assertEqual(cc._clamp_to_screen(-50, -50), (1, 1))

    def test_clamp_never_returns_a_failsafe_corner(self):
        corners = {(0, 0), (0, 899), (1599, 0), (1599, 899)}
        with mock.patch.object(cc, "pyautogui", FakeGui(size=(1600, 900))):
            for raw in [(0, 0), (1599, 899), (0, 899), (1599, 0),
                        (-5, -5), (99999, 99999)]:
                self.assertNotIn(cc._clamp_to_screen(*raw), corners)

    def test_clamp_floats_and_garbage(self):
        with mock.patch.object(cc, "pyautogui", FakeGui(size=(1600, 900))):
            self.assertEqual(cc._clamp_to_screen(10.6, 20.2), (11, 20))
            self.assertEqual(cc._clamp_to_screen("bad", None), (1, 1))


class MappingTest(unittest.TestCase):

    def test_scale_is_one_when_aware(self):
        gui = FakeGui(size=(1600, 900))
        with mock.patch.object(cc, "pyautogui", gui):
            _, sx, sy = cc._capture_with_mapping()
        self.assertEqual((sx, sy), (1.0, 1.0))

    def test_scale_corrects_dpi_mismatch(self):
        # Screenshot at physical 2x while the mouse space is half: the classic
        # 200% scaling mismatch. Image pixels must map back by 0.5.
        class ScaledGui(FakeGui):
            def screenshot(self, *a, **k):
                return Image.new("RGB", (3200, 1800), (0, 0, 0))

        with mock.patch.object(cc, "pyautogui", ScaledGui(size=(1600, 900))):
            _, sx, sy = cc._capture_with_mapping()
        self.assertEqual((sx, sy), (0.5, 0.5))

    def test_screen_find_maps_image_coords_to_screen(self):
        gui = FakeGui(size=(1600, 900))
        fake_resp = mock.Mock()
        fake_resp.text = "200,100"
        with mock.patch.object(cc, "pyautogui", gui), \
             mock.patch.object(cc, "_capture_with_mapping",
                               return_value=(Image.new("RGB", (800, 450)), 2.0, 2.0)), \
             mock.patch.object(cc, "_get_api_key", return_value="k"), \
             mock.patch("core.gemini.call", return_value=fake_resp):
            coords = cc._screen_find("the Send button", retries=1)
        self.assertEqual(coords, (400, 200))     # 200*2, 100*2

    def test_screen_find_not_found_returns_none(self):
        gui = FakeGui(size=(1600, 900))
        fake_resp = mock.Mock()
        fake_resp.text = "NOT_FOUND"
        with mock.patch.object(cc, "pyautogui", gui), \
             mock.patch.object(cc, "_capture_with_mapping",
                               return_value=(Image.new("RGB", (1600, 900)), 1.0, 1.0)), \
             mock.patch.object(cc, "_get_api_key", return_value="k"), \
             mock.patch("core.gemini.call", return_value=fake_resp):
            coords = cc._screen_find("ghost", retries=1)
        self.assertIsNone(coords)


class MoveTest(unittest.TestCase):

    def test_move_reports_landing(self):
        gui = FakeGui()
        with mock.patch.object(cc, "pyautogui", gui):
            msg = cc._move(400, 300)
        self.assertIn("(400, 300)", msg)
        self.assertEqual(gui.moves[-1], (400, 300))

    def test_move_detects_dpi_mismatch(self):
        class LyingGui(FakeGui):
            def moveTo(self, x, y, duration=0.0):
                self._pos = (x // 2, y // 2)      # simulates silent scaling

        with mock.patch.object(cc, "pyautogui", LyingGui()):
            msg = cc._move(800, 600)
        self.assertIn("dpi mismatch", msg.lower())

    def test_move_clamps_out_of_range(self):
        gui = FakeGui(size=(1600, 900))
        with mock.patch.object(cc, "pyautogui", gui):
            cc._move(99999, 99999)
        self.assertEqual(gui.moves[-1], (1598, 898))

    def test_move_rel(self):
        gui = FakeGui(pos=(100, 100))
        with mock.patch.object(cc, "pyautogui", gui):
            msg = cc._move_rel(50, -20)
        self.assertIn("(50, -20)", msg)
        self.assertIn("(150, 80)", msg)


class DispatchTest(unittest.TestCase):

    def test_screen_move_uses_located_coords(self):
        with mock.patch.object(cc, "_screen_find", return_value=(640, 360)), \
             mock.patch.object(cc, "_move", return_value="Mouse → (640, 360)") as m:
            out = cc.computer_control({"action": "screen_move",
                                       "description": "the Play button"})
        m.assert_called_once_with(640, 360)
        self.assertIn("Play button", out)

    def test_screen_click_missing_description(self):
        out = cc.computer_control({"action": "screen_click"})
        self.assertIn("needs a 'description'", out)

    def test_screen_click_not_found(self):
        with mock.patch.object(cc, "_screen_find", return_value=None):
            out = cc.computer_control({"action": "screen_click",
                                       "description": "ghost"})
        self.assertIn("not found", out.lower())

    def test_screen_click_clicks_and_skips_verify_for_right(self):
        with mock.patch.object(cc, "_screen_find", return_value=(300, 200)), \
             mock.patch.object(cc, "_click", return_value="ok") as c, \
             mock.patch.object(cc, "_verify_after_click") as v:
            out = cc.computer_control({"action": "screen_right_click",
                                       "description": "row menu"})
        c.assert_called_once_with(x=300, y=200, button="right", clicks=1)
        v.assert_not_called()
        self.assertIn("Right-clicked", out)

    def test_mouse_position_dispatch(self):
        gui = FakeGui(size=(1600, 900), pos=(123, 456))
        with mock.patch.object(cc, "pyautogui", gui):
            out = cc.computer_control({"action": "mouse_position"})
        self.assertIn("123,456", out)


class RescueTest(unittest.TestCase):

    def test_failsafe_corner_recovered_and_retried(self):
        class CorneredGui(FakeGui):
            FailSafeException = RuntimeError

            def __init__(self):
                super().__init__(size=(1600, 900), pos=(0, 0))
                self.first = True

            def click(self, x=None, y=None, button="left", clicks=1):
                if self.first:
                    self.first = False
                    raise self.FailSafeException("failsafe corner")
                return super().click(x, y, button, clicks)

        gui = CorneredGui()
        with mock.patch.object(cc, "pyautogui", gui):
            msg = cc._click(400, 300)
        self.assertIn("licked (400, 300)", msg)
        self.assertIn((800, 450), gui.moves)      # pulled off the corner first
        self.assertEqual(gui.clicks[-1][:2], (400, 300))

    def test_non_failsafe_exception_propagates(self):
        class BrokenGui(FakeGui):
            FailSafeException = RuntimeError

            def click(self, *a, **k):
                raise ValueError("driver error")

        gui = BrokenGui()
        with mock.patch.object(cc, "pyautogui", gui), \
             self.assertRaises(ValueError):
            cc._click(100, 100)

    def test_move_recovers_from_failsafe_corner(self):
        class CorneredGui(FakeGui):
            FailSafeException = RuntimeError

            def __init__(self):
                super().__init__(size=(800, 600), pos=(0, 0))
                self.FAILSAFE = True

            def moveTo(self, x, y, duration=0.0):
                # modelling pyautogui: the gate blocks every move while the
                # pointer is still on the corner, and re-checks each call
                if getattr(self, "FAILSAFE", True) and self._pos == (0, 0):
                    raise self.FailSafeException("failsafe corner")
                self._pos = (x, y)
                self.moves.append((x, y))

        gui = CorneredGui()
        with mock.patch.object(cc, "pyautogui", gui):
            msg = cc._move(400, 300)
        self.assertIn("(400, 300)", msg)
        self.assertIn((400, 300), gui.moves)


class GridTest(unittest.TestCase):

    def test_grid_returns_same_size_image(self):
        img = Image.new("RGB", (300, 200), (0, 0, 0))
        out = cc._grid_overlay(img)
        self.assertEqual(out.size, (300, 200))

    def test_tool_lists_new_actions(self):
        desc = cc.TOOL["parameters"]["properties"]["action"]["description"]
        for name in ("screen_move", "screen_double_click",
                     "screen_right_click", "move_rel", "mouse_position"):
            self.assertIn(name, desc)
        self.assertIn("dx", cc.TOOL["parameters"]["properties"])
        self.assertIn("dy", cc.TOOL["parameters"]["properties"])
        self.assertIn("button", cc.TOOL["parameters"]["properties"])


if __name__ == "__main__":
    unittest.main()
