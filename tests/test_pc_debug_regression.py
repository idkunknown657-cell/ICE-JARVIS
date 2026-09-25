"""
Regression tests for the debug pass over the PC-control engine.

SAFETY: nothing here moves the real mouse, presses a real key, writes the real
clipboard or captures the real screen — the same rule as test_pc_engine.py.
Everything exercised is pure logic or explicitly mocked.
"""
import unittest
from unittest import mock

from core import pc_input
from core import pc_engine as E
from core import screen_share
from actions import pc_agent as A


class KeyTypeSurrogateTest(unittest.TestCase):
    """Astral-plane characters (emoji) do not fit the 16-bit scan field and
    used to be truncated to a wrong character. They must go out as UTF-16
    surrogate pairs, exactly as KEYEVENTF_UNICODE expects."""

    def test_bmp_characters_are_sent_directly(self):
        sent = []

        def fake_send(inp):
            sent.append(inp.ki.wScan)
            return True

        with mock.patch.object(pc_input, "_send", side_effect=fake_send), \
             mock.patch.object(pc_input, "_need_windows"):
            pc_input.key_type("hi", interval=0)
        self.assertEqual([ord("h"), ord("h"), ord("i"), ord("i")], sent)

    def test_emoji_becomes_a_surrogate_pair(self):
        # Each unit goes out as a down+up pair: high surrogate fully pressed,
        # then low surrogate. That is the sequence Windows reassembles into 😀.
        sent = []

        def fake_send(inp):
            sent.append(inp.ki.wScan)
            return True

        with mock.patch.object(pc_input, "_send", side_effect=fake_send), \
             mock.patch.object(pc_input, "_need_windows"):
            pc_input.key_type("\U0001F600", interval=0)      # 😀 U+1F600
        self.assertEqual([0xD83D, 0xD83D, 0xDE00, 0xDE00], sent)

    def test_every_astral_char_stays_in_the_utf16_range(self):
        # 2 code units x (down+up) = 4 events per character, every scan value a
        # valid surrogate — never a raw 21-bit code point.
        with mock.patch.object(pc_input, "_need_windows"):
            for ch in "\U0001F600\U0001F680\U0001F44D\U00013000":
                with mock.patch.object(pc_input, "_send", return_value=True) as sm:
                    pc_input.key_type(ch, interval=0)
                    scans = [c.args[0].ki.wScan for c in sm.call_args_list]
                self.assertEqual(4, len(scans))
                for u in scans:
                    self.assertTrue(0xD800 <= u <= 0xDFFF)


class RecoveryRetryTest(unittest.TestCase):
    """The recovery ladder used to re-run a failed vague click with the literal
    string ('that button') and vague=False, which skipped resolve_vague — a
    retry designed to fail. It must keep the vague flag so the fresh UIA walk
    is actually used."""

    def test_retry_keeps_the_vague_flag(self):
        plan = {"action": "click", "target": "that button", "vague": True}
        with mock.patch.object(A, "_step", return_value={"ok": True}) as st, \
             mock.patch.object(A.engine, "clear_uia_cache", create=True), \
             mock.patch.object(A.time, "sleep"):
            out = A._recover(plan)
        self.assertIsNotNone(out)
        args = st.call_args.args
        self.assertTrue(args[0].get("vague"))

    def test_type_retry_keeps_the_field(self):
        # (unchanged path, pinned so nobody 'simplifies' it later)
        plan = {"action": "type", "text": "hi", "field": "search box"}
        with mock.patch.object(A, "_step", return_value={"ok": True}) as st, \
             mock.patch.object(A.engine, "clear_uia_cache", create=True), \
             mock.patch.object(A.time, "sleep"):
            A._recover(plan)
        args = st.call_args.args
        self.assertEqual("hi", args[0].get("text"))


class PressTheButtonTest(unittest.TestCase):
    "\"press the button\" is a click, not a keyboard shortcut."

    def test_bare_press_routes_to_click(self):
        plan = A.plan_intent("press the button")
        self.assertIsNotNone(plan)
        self.assertEqual("click", plan["action"])
        self.assertEqual("button", plan["target"])

    def test_press_it_is_a_vague_click(self):
        plan = A.plan_intent("press it")
        self.assertEqual("click", plan["action"])
        self.assertTrue(plan.get("vague"))

    def test_real_keys_still_parse(self):
        self.assertEqual({"action": "keys", "keys": ["ctrl", "s"]},
                         A.plan_intent("press ctrl+s"))
        self.assertEqual({"action": "keys", "keys": ["enter"]},
                         A.plan_intent("press enter"))

    def test_press_a_key_name_never_clears_a_field(self):
        # The old trap: 'press the enter' → parse_keys fails → bare-target rule
        # → click on the literal words. Worse, parse_keys on 'the button' could
        # return [] only because it is not a key; either way no key was pressed.
        plan = A.plan_intent("press the submit")
        self.assertEqual("click", plan["action"])
        self.assertEqual("submit", plan["target"])


class WaitForSettleTest(unittest.TestCase):
    """wait_for_settle: block until the screen stops changing."""

    def test_returns_fast_when_the_screen_is_already_still(self):
        with mock.patch.object(E, "signature", return_value=b"\x05" * 64):
            t0 = E.time.monotonic()
            moved = E.wait_for_settle(timeout=2.0, quiet=0.2, poll=0.05)
            elapsed = E.time.monotonic() - t0
        self.assertEqual(0.0, moved)          # nothing moved
        self.assertLess(elapsed, 1.0)         # did not wait out the timeout

    def test_reports_movement_then_settles(self):
        seq = iter([b"\x00" * 64, b"\xff" * 64, b"\xff" * 64, b"\xff" * 64,
                    b"\xff" * 64, b"\xff" * 64, b"\xff" * 64, b"\xff" * 64,
                    b"\xff" * 64, b"\xff" * 64])
        with mock.patch.object(E, "signature",
                               side_effect=lambda *a, **k: next(seq)):
            moved = E.wait_for_settle(timeout=5.0, quiet=0.2, poll=0.05)
        self.assertGreater(moved, 0.0)

    def test_busy_screen_times_out_and_reports_movement(self):
        flips = iter([b"\x00" * 64, b"\xff" * 64])

        def busy(*a, **k):
            try:
                return next(flips)
            except StopIteration:
                return b"\xaa" * 64
        with mock.patch.object(E, "signature", side_effect=busy), \
             mock.patch.object(E.time, "monotonic",
                               side_effect=lambda: E.time.time()):
            moved = E.wait_for_settle(timeout=0.4, quiet=0.2, poll=0.05)
        self.assertGreater(moved, 0.0)


class ExplainChangeTest(unittest.TestCase):
    """explain_change: say what changed, in words a person can use.

    The before-tree must be captured by the CALLER before the action (that is
    the point of the fix: comparing two walks taken after the fact saw only the
    present)."""

    def test_reports_appeared_elements_by_name(self):
        before = [{"name": "OK", "type": "Button"}]
        after = [{"name": "OK", "type": "Button"},
                 {"name": "Save file", "type": "Dialog"}]
        with mock.patch.object(E, "uia_elements", return_value=after):
            out = E.explain_change(b"\x00" * 64, b"\xff" * 64,
                                   before_elements=before)
        self.assertIn("Save file", out)
        self.assertIn("appeared", out)

    def test_reports_vanished_elements(self):
        before = [{"name": "Cookie banner", "type": "Pane"}]
        with mock.patch.object(E, "uia_elements", return_value=[]):
            out = E.explain_change(b"\x00" * 64, b"\xff" * 64,
                                   before_elements=before)
        self.assertIn("gone", out)
        self.assertIn("Cookie banner", out)

    def test_falls_back_to_visual_delta_without_a_before_tree(self):
        with mock.patch.object(E, "uia_elements", return_value=[]), \
             mock.patch.object(E, "signature_distance", return_value=42.0):
            out = E.explain_change(b"\x00" * 64, b"\xff" * 64)
        self.assertIn("substantially", out)

    def test_uia_failure_still_answers(self):
        with mock.patch.object(E, "uia_elements",
                               side_effect=Exception("no uia")), \
             mock.patch.object(E, "signature_distance", return_value=42.0):
            out = E.explain_change(b"\x00" * 64, b"\xff" * 64,
                                   before_elements=[{"name": "x", "type": "b"}])
        self.assertIn("substantially", out)

    def test_no_change_is_said_plainly(self):
        with mock.patch.object(E, "uia_elements", return_value=[]), \
             mock.patch.object(E, "signature_distance", return_value=0.0):
            out = E.explain_change(b"\x07" * 64, b"\x07" * 64,
                                   before_elements=[])
        self.assertIn("did not visibly change", out)


class ScreenShareFingerprintTest(unittest.TestCase):
    """getdata() is removed in Pillow 14; the replacement must give the same
    fingerprints (the perceptual hash is compared across versions)."""

    def test_fingerprint_is_stable_bytes(self):
        png = self._png()
        fp = screen_share.frame_fingerprint(png)
        self.assertEqual(32, len(fp))                     # md5 hex digest
        self.assertEqual(fp, screen_share.frame_fingerprint(png))

    def test_grid_hashes_survive_the_api_swap(self):
        png = self._png()
        hs = screen_share._grid_hashes(png, grid=4)
        self.assertEqual(16, len(hs))
        self.assertTrue(all(len(h) == 4 for h in hs))

    @staticmethod
    def _png():
        from PIL import Image
        import io
        img = Image.new("L", (64, 48), 123)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()


if __name__ == "__main__":
    unittest.main()
