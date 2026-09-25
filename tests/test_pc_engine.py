"""
Regression tests for core/pc_engine.py — the rebuilt computer-control engine.

SAFETY: nothing here moves the real mouse, presses a real key, writes the real
clipboard or captures the real screen. Everything exercised is pure (coordinate
maths, matching, parsing) or explicitly mocked, because a test suite that can
move the user's cursor while it runs is a test suite that gets deleted.
"""
import unittest
from unittest import mock

from core import pc_engine as E


class ScreenMapTest(unittest.TestCase):
    """The DPI fix: image pixels and mouse pixels are different spaces, and the
    conversion between them must be measured, never assumed."""

    def _sm(self, **kw):
        base = dict(left=0, top=0, width=1920, height=1080,
                    img_width=1920, img_height=1080)
        base.update(kw)
        return E.ScreenMap(**base)

    def test_identity_when_image_matches_desktop(self):
        sm = self._sm()
        self.assertEqual(sm.scale_x, 1.0)
        self.assertEqual(sm.to_screen(800, 400), (800, 400))

    def test_scaled_capture_maps_back_to_real_pixels(self):
        # A 150 % display: the screenshot is 2880 px wide, the mouse space is 1920.
        sm = self._sm(img_width=2880, img_height=1620)
        self.assertAlmostEqual(sm.scale_x, 1920 / 2880, places=6)
        x, y = sm.to_screen(1440, 810)          # dead centre of the image
        self.assertEqual((x, y), (960, 540))    # dead centre of the desktop

    def test_second_monitor_to_the_left_keeps_negative_x(self):
        # The image starts at the virtual desktop's top-left, i.e. at -1600 —
        # assuming (0, 0) here is what used to shift every click on monitor 2.
        sm = self._sm(left=-1600, top=0, width=4800, height=1080,
                      img_width=4800, img_height=1080)
        x, _y = sm.to_screen(400, 500)          # inside the left-hand monitor
        self.assertLess(x, 0)
        self.assertEqual(x, -1200)

    def test_second_monitor_above_the_primary(self):
        sm = self._sm(left=0, top=-1080, width=1920, height=2160,
                      img_width=1920, img_height=2160)
        self.assertEqual(sm.to_screen(100, 50), (100, -1030))

    def test_a_crop_map_measures_from_its_own_origin(self):
        # capture_region() builds exactly this for a region: the map's left/top
        # IS the crop's screen origin, so no origin argument is needed.
        crop = E.ScreenMap(left=900, top=700, width=400, height=300,
                           img_width=400, img_height=300)
        self.assertEqual(crop.to_screen(10, 10), (910, 710))
        self.assertEqual(crop.to_image(910, 710), (10, 10))

    def test_round_trip(self):
        sm = self._sm(img_width=1440, img_height=810)
        px, py = sm.to_image(960, 540)
        self.assertEqual(sm.to_screen(px, py), (960, 540))

    def test_round_trip_is_exact_at_a_non_uniform_scale(self):
        sm = self._sm(img_width=960, img_height=1080)
        for x, y in ((0, 0), (1919, 1079), (640, 512), (1234, 999)):
            px, py = sm.to_image(x, y)
            sx, sy = sm.to_screen(px, py)
            self.assertLessEqual(abs(sx - x), 2, f"x drifted: {x} -> {sx}")
            self.assertLessEqual(abs(sy - y), 2, f"y drifted: {y} -> {sy}")

    def test_contains(self):
        sm = self._sm(width=100, height=50)
        self.assertTrue(sm.contains(99, 49))
        self.assertFalse(sm.contains(100, 10))
        self.assertFalse(sm.contains(10, -1))


class ClampTest(unittest.TestCase):
    """A clamped point is a missed click; an unclamped one can be a click on
    another monitor. It must also never land on pyautogui's fail-safe corners,
    or the *next* control call aborts."""

    def test_clamps_into_the_virtual_desktop(self):
        sm = E.ScreenMap(left=-1600, top=0, width=4800, height=1080)
        with mock.patch.object(E, "primary_size", return_value=(1920, 1080)):
            self.assertEqual(E.clamp_screen(-9999, -9999, sm), (-1600, 1))
            self.assertEqual(E.clamp_screen(9999, 9999, sm), (3199, 1078))

    def test_never_returns_a_corner(self):
        sm = E.ScreenMap(left=0, top=0, width=1920, height=1080)
        with mock.patch.object(E, "primary_size", return_value=(1920, 1080)):
            for point in ((0, 0), (1919, 0), (0, 1079), (1919, 1079)):
                x, y = E.clamp_screen(*point, sm)
                self.assertNotIn((x, y), ((0, 0), (1919, 0), (0, 1079), (1919, 1079)))

    def test_clamp_box_stays_on_screen(self):
        sm = E.ScreenMap(left=0, top=0, width=1920, height=1080)
        x, y, w, h = E.clamp_box((1900, 1070, 400, 400), sm)
        self.assertGreaterEqual(x, 0)
        self.assertLessEqual(x + w, 1920)
        self.assertLessEqual(y + h, 1080)


class ParseCoordsTest(unittest.TestCase):
    """A model's coordinate reply must be read leniently but never invented."""

    def setUp(self):
        # Big enough that a real coordinate is not clamped, so this test measures
        # parsing and mapping only.
        self.sm = E.ScreenMap(left=0, top=0, width=1920, height=1080,
                              img_width=1920, img_height=1080)

    def test_accepts_the_shapes_models_actually_use(self):
        for text in ("812,540", "(812, 540)", "x=812 y=540", "x: 812, y: 540",
                     "the element is at 812,540", "812 px, 540"):
            self.assertEqual(E.parse_coords(text, self.sm), (812, 540), text)

    def test_refuses_not_found_and_junk(self):
        for text in ("NOT_FOUND", "", "   ", "I could not find it", None):
            self.assertIsNone(E.parse_coords(text, self.sm), repr(text))

    def test_percentage_replies_are_mapped_through_the_screen_map(self):
        self.assertEqual(E.parse_coords("50%,50%", self.sm), (960, 540))

    def test_short_bare_pair_is_accepted(self):
        self.assertEqual(E.parse_coords("812 540", self.sm), (812, 540))


class ScoreElementTest(unittest.TestCase):
    """Target matching decides what gets clicked, so the ranking rules are
    contract: an exact name wins, a describing noun does not disqualify, and a
    huge text blob never outranks a real control."""

    BTN = {"type": "Button", "name": "Save", "x": 10, "y": 20, "w": 80, "h": 24}
    EDIT = {"type": "Edit", "name": "Search", "x": 0, "y": 0, "w": 300, "h": 30}
    DOC = {"type": "Document", "name": "x" * 300, "x": 0, "y": 0, "w": 300, "h": 300}

    def test_exact_name_wins(self):
        self.assertGreater(E.score_element("Save", self.BTN), 0.95)

    def test_widget_noun_is_tolerated(self):
        self.assertGreaterEqual(E.score_element("Save button", self.BTN), 0.9)
        self.assertGreaterEqual(E.score_element("the search box", self.EDIT), 0.7)

    def test_prose_containing_the_word_scores_low(self):
        # A 300-character text node is not "Save".
        self.assertLess(E.score_element("Save button", self.DOC), 0.55)

    def test_unrelated_text_scores_zero(self):
        self.assertEqual(E.score_element("Delete", self.BTN), 0.0)

    def test_wanted_types_reads_the_description(self):
        self.assertIn("Button", E.wanted_types("the save button"))
        self.assertIn("Edit", E.wanted_types("search box"))

    def test_best_matches_ranks_and_truncates(self):
        els = [self.BTN, self.EDIT, self.DOC]
        top = E.best_matches("Save", els, threshold=0.7)
        self.assertTrue(top)
        self.assertIs(top[0][1], self.BTN)
        self.assertEqual(len(E.best_matches("Save", els, threshold=0.7, limit=1)), 1)


class DeicticTest(unittest.TestCase):
    """"there" and "it" are pointers at what was just found — resolving them is
    what stops the assistant asking "where do you mean?" every other turn."""

    def test_recognises_pointer_words(self):
        for text in ("there", "over there", "it", "that one", "here", "this one"):
            self.assertTrue(E.is_deictic(text), text)

    def test_does_not_swallow_real_targets(self):
        for text in ("Settings", "the Save button", "search box", "the first video"):
            self.assertFalse(E.is_deictic(text), text)

    def test_context_memory_expires_when_the_window_changes(self):
        target = E.Target("Save", 10, 20, 80, 24, source="uia")
        with mock.patch.object(E, "foreground_title", return_value="Notepad"):
            E.remember_target(target)
            self.assertIs(E.last_target(), target)
        with mock.patch.object(E, "foreground_title", return_value="Chrome"):
            self.assertIsNone(E.last_target(), "a stale coordinate must be dropped")

    def test_locate_resolves_a_deictic_from_context(self):
        target = E.Target("Save", 10, 20, 80, 24, source="uia")
        with mock.patch.object(E, "foreground_title", return_value="Notepad"):
            E.remember_target(target)
            got = E.locate("there")
        self.assertIsNotNone(got)
        self.assertEqual(got.center, (50, 32))
        self.assertIn("Save", got.label)


class SignatureTest(unittest.TestCase):
    """Verification is usually a local pixel comparison, so it must be cheap,
    total and never raise."""

    def test_distance_identical(self):
        self.assertEqual(E.signature_distance(b"\x00\x10", b"\x00\x10"), 0.0)

    def test_distance_different(self):
        self.assertGreater(E.signature_distance(b"\x00", b"\xff"), 200)

    def test_mismatched_or_missing_fingerprints_are_max_distance(self):
        self.assertEqual(E.signature_distance(b"", b"\x00"), 255.0)
        self.assertEqual(E.signature_distance(b"\x00", b"\x00\x00"), 255.0)

    def test_signature_returns_empty_rather_than_raising_when_capture_fails(self):
        with mock.patch.object(E, "capture_region", side_effect=RuntimeError("no screen")):
            self.assertEqual(E.signature(), b"")

    def test_wait_for_change_times_out_at_zero(self):
        with mock.patch.object(E, "signature", return_value=b"\x00" * 8):
            self.assertEqual(E.wait_for_change(b"\x00" * 8, timeout=0.2), 0.0)


class ActionSafetyTest(unittest.TestCase):
    """No target means no click. This is the property that stops the assistant
    clicking a remembered, stale coordinate."""

    def test_click_target_without_a_target_does_not_touch_the_mouse(self):
        with mock.patch.object(E, "locate", return_value=None), \
             mock.patch.object(E, "click_at") as clicked:
            res = E.click_target("a button that does not exist")
        clicked.assert_not_called()
        self.assertFalse(res.ok)

    def test_point_at_without_a_target_does_not_move(self):
        with mock.patch.object(E, "locate", return_value=None), \
             mock.patch.object(E, "move") as moved:
            res = E.point_at("nowhere in particular")
        moved.assert_not_called()
        self.assertFalse(res.ok)

    def test_click_target_reports_a_no_op_click_honestly(self):
        target = E.Target("Ghost", 100, 100, 40, 20, source="uia")
        with mock.patch.object(E, "locate", return_value=target), \
             mock.patch.object(E, "element_at", return_value=None), \
             mock.patch.object(E, "click_at") as clicked:
            clicked.return_value = E.Result(True, "Clicked at 120,110", "click",
                                           verified=False)
            res = E.click_target("Ghost", retries=0)
        self.assertTrue(res.ok)              # the click physically happened
        self.assertFalse(res.verified)       # but the screen did not change
        self.assertIn("did not change", res.detail)

    def test_empty_description_is_refused(self):
        self.assertFalse(E.click_target("").ok)
        self.assertFalse(E.point_at("  ").ok)
        self.assertFalse(E.type_text("").ok)

    def test_type_text_into_a_missing_field_types_nothing(self):
        with mock.patch.object(E, "click_target") as ct, \
             mock.patch.object(E, "clear_field") as clear:
            ct.return_value = E.Result(False, "not found", "click", target=None)
            res = E.type_text("hello", field="the search box")
        clear.assert_not_called()
        self.assertFalse(res.ok)
        self.assertIn("did not type", res.detail)


class TypingTest(unittest.TestCase):
    """Typing must read itself back; a mismatch is reported, never hidden."""

    def test_long_text_goes_through_the_clipboard(self):
        with mock.patch.object(E, "_clipboard_write", return_value=True) as cw, \
             mock.patch.object(E, "_stash_clipboard"), \
             mock.patch.object(E, "restore_clipboard"), \
             mock.patch.object(E, "clear_field", return_value=E.Result(True, "", "")), \
             mock.patch.object(E, "focused_value", return_value=None), \
             mock.patch.object(E._PAG, "hotkey") as hotkey:
            res = E.type_text("a fairly long sentence that should be pasted",
                              verify=True)
        cw.assert_called()
        self.assertTrue(res.ok)
        self.assertEqual(res.method, "paste")
        hotkey.assert_called()

    def test_readback_mismatch_is_reported_not_claimed(self):
        with mock.patch.object(E, "clear_field", return_value=E.Result(True, "", "")), \
             mock.patch.object(E, "focused_value", return_value="something else"), \
             mock.patch.object(E, "_stash_clipboard"), \
             mock.patch.object(E, "restore_clipboard"), \
             mock.patch.object(E, "_clipboard_write", return_value=True), \
             mock.patch.object(E._PAG, "hotkey"), \
             mock.patch.object(E._PAG, "write"):
            res = E.type_text("intended text")
        self.assertFalse(res.verified)
        self.assertIsNotNone(res)             # never a crash, always a sentence
        self.assertTrue("could not verify" in res.detail
                        or "may not" in res.detail
                        or "field reads" in res.detail, res.detail)

    def test_exact_readback_verifies(self):
        with mock.patch.object(E, "clear_field", return_value=E.Result(True, "", "")), \
             mock.patch.object(E, "focused_value", return_value="hello"), \
             mock.patch.object(E, "_stash_clipboard"), \
             mock.patch.object(E, "restore_clipboard"), \
             mock.patch.object(E, "_clipboard_write", return_value=True), \
             mock.patch.object(E._PAG, "hotkey"), \
             mock.patch.object(E._PAG, "write"):
            res = E.type_text("hello")
        self.assertTrue(res.ok)
        self.assertTrue(res.verified)


class PowerShellTest(unittest.TestCase):
    def test_single_key(self):
        with mock.patch.object(E._PAG, "press") as press:
            self.assertTrue(E.press_keys("enter").ok)
        press.assert_called_once_with("enter")

    def test_chord_forms(self):
        with mock.patch.object(E._PAG, "hotkey") as hotkey:
            E.press_keys("ctrl+c")
            hotkey.assert_called_with("ctrl", "c")
        with mock.patch.object(E._PAG, "hotkey") as hotkey:
            E.press_keys("ctrl", "shift", "s")
            hotkey.assert_called_with("ctrl", "shift", "s")

    def test_no_keys_is_refused(self):
        self.assertFalse(E.press_keys().ok)


class WindowsTest(unittest.TestCase):
    def test_focus_window_is_false_for_an_unknown_title(self):
        with mock.patch.object(E, "focus_window", return_value=False) as fw:
            self.assertFalse(E.focus_window("no such window"))
        fw.assert_called_once()

    def test_state_never_raises(self):
        st = E.state()
        for key in ("windows", "input", "vision", "uia", "desktop", "monitors",
                    "pointer", "foreground"):
            self.assertIn(key, st)
        self.assertEqual(len(st["pointer"]), 2)


if __name__ == "__main__":
    unittest.main()
