"""Unit tests for actions/screen_processor.py — the screen capture backend
fallback (mss → PIL ImageGrab) and failure behaviour."""

import io
import unittest
from unittest import mock

# Pre-load the lazy PIL submodule so mock.patch("PIL.ImageGrab") can bind a
# target attribute (it does not exist until ImageGrab is imported once).
import PIL.ImageGrab  # noqa: F401

import actions.screen_processor as sp


class CaptureBackendTests(unittest.TestCase):

    def test_pil_fallback_when_mss_missing(self):
        with mock.patch.object(sp, "_MSS", False), \
             mock.patch.object(sp, "_PIL", True), \
             mock.patch("PIL.ImageGrab") as ig:
            from PIL import Image
            ig.grab.return_value = Image.new("RGB", (320, 180), "navy")
            data, mime = sp._capture_screen()
        self.assertEqual(mime, "image/jpeg")
        self.assertTrue(data)

    def test_pil_fallback_when_mss_raises(self):
        with mock.patch.object(sp, "_MSS", True), \
             mock.patch.object(sp, "_PIL", True), \
             mock.patch("mss.mss", side_effect=RuntimeError("bad dpi")), \
             mock.patch("PIL.ImageGrab") as ig:
            from PIL import Image
            ig.grab.return_value = Image.new("RGB", (320, 180), "navy")
            data, mime = sp._capture_screen()
        self.assertEqual(mime, "image/jpeg")
        self.assertTrue(data)

    def test_raises_when_nothing_available(self):
        with mock.patch.object(sp, "_MSS", False), \
             mock.patch.object(sp, "_PIL", False):
            with self.assertRaises(RuntimeError):
                sp._capture_screen()


class CompressTests(unittest.TestCase):

    def test_compresses_to_jpeg(self):
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (600, 400), "red").save(buf, format="PNG")
        data, mime = sp._compress(buf.getvalue(), "PNG")
        self.assertEqual(mime, "image/jpeg")
        self.assertLess(len(data), 50_000)

    def test_passes_through_without_pil(self):
        raw = b"rawbytes"
        with mock.patch.object(sp, "_PIL", False):
            data, mime = sp._compress(raw, "PNG")
        self.assertEqual(data, raw)
        self.assertEqual(mime, "image/png")


class ActiveMonitorTests(unittest.TestCase):

    _MONITORS = [
        {"left": 0,    "top": 0, "width": 1920, "height": 1080},  # [0] all
        {"left": 0,    "top": 0, "width": 1920, "height": 1080},  # [1] primary
        {"left": 1920, "top": 0, "width": 1280, "height": 1024},  # [2] right
    ]

    def test_falls_back_to_primary_when_no_centre(self):
        with mock.patch.object(sp, "_fg_window_center", return_value=None):
            self.assertEqual(sp._active_monitor_index(self._MONITORS), 1)

    def test_picks_monitor_of_foreground_window(self):
        with mock.patch.object(sp, "_fg_window_center",
                               return_value=(2200, 550)):
            self.assertEqual(sp._active_monitor_index(self._MONITORS), 2)

    def test_capture_prefers_active_monitor_first(self):
        """The mss probe order must start with the active monitor, then the
        others, so a later monitor's failed grab rolls forward but the primary
        is never silently prioritised."""
        import numpy as np
        fake_sct = mock.MagicMock()
        fake_sct.__enter__.return_value = fake_sct
        fake_sct.monitors = self._MONITORS
        fake_sct.grab.return_value = mock.Mock(rgb=np.zeros((10, 10, 3), "u1"),
                                               size=(10, 10))
        with mock.patch.object(sp, "_fg_window_center",
                               return_value=(2200, 550)), \
             mock.patch("mss.mss", return_value=fake_sct), \
             mock.patch("mss.tools.to_png", return_value=b"jpegdata"), \
             mock.patch.object(sp, "_compress",
                               return_value=(b"jpg", "image/jpeg")):
            sp._capture_screen(active=True)
        order = [c.args[0]["left"] for c in fake_sct.grab.call_args_list]
        self.assertEqual(order[0], 1920)        # active monitor tried first
        self.assertEqual(len(order), 1)         # first grab succeeded → break

    def test_capture_non_active_uses_primary_order(self):
        import numpy as np
        fake_sct = mock.MagicMock()
        fake_sct.__enter__.return_value = fake_sct
        fake_sct.monitors = self._MONITORS
        fake_sct.grab.return_value = mock.Mock(rgb=np.zeros((8, 8, 3), "u1"),
                                               size=(8, 8))
        with mock.patch("mss.mss", return_value=fake_sct), \
             mock.patch("mss.tools.to_png", return_value=b"jpegdata"), \
             mock.patch.object(sp, "_compress",
                               return_value=(b"jpg", "image/jpeg")):
            sp._capture_screen(active=False)
        order = [c.args[0]["left"] for c in fake_sct.grab.call_args_list]
        self.assertEqual(order[0], 0)           # primary tried first when inactive


def test_capture_rolls_to_next_when_active_monitor_fails(self):
        """If the active monitor grab itself fails, the probe keeps going and
        lands on the next real monitor instead of dying."""
        import numpy as np
        fake_sct = mock.MagicMock()
        fake_sct.__enter__.return_value = fake_sct
        fake_sct.monitors = self._MONITORS

        def side(mon):
            if mon["left"] == 1920:
                raise RuntimeError("bad monitor")
            return mock.Mock(rgb=np.zeros((8, 8, 3), "u1"), size=(8, 8))
        fake_sct.grab.side_effect = side
        with mock.patch.object(sp, "_fg_window_center",
                               return_value=(2200, 550)), \
             mock.patch("mss.mss", return_value=fake_sct), \
             mock.patch("mss.tools.to_png", return_value=b"jpegdata"), \
             mock.patch.object(sp, "_compress",
                               return_value=(b"jpg", "image/jpeg")):
            sp._capture_screen(active=True)
        order = [c.args[0]["left"] for c in fake_sct.grab.call_args_list]
        self.assertEqual(order, [1920, 0])      # failed first, succeeded second


if __name__ == "__main__":
    unittest.main()