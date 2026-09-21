"""Unit tests for core/screen_share.py — the quality tables and change-detection
fingerprint that keep the background screen loop cheap (no work on static
screens, cadence tuned by the level the user chose)."""
import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import PIL.Image

from core.screen_share import (
    POLL_SECS, CAPTION_TTL_SECS, frame_fingerprint, settings_for,
)


def _jpeg(rgb=(40, 80, 120)):
    img = PIL.Image.new("RGB", (320, 200), rgb)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=82)
    return buf.getvalue()


class SettingsForTest(unittest.TestCase):
    def test_each_level_has_sane_values(self):
        pl, ttl = settings_for("light")
        self.assertGreater(pl, settings_for("high")[0])   # light polls slower
        self.assertGreater(ttl, settings_for("high")[1])  # and captions rarer

    def test_medium_is_the_fallback(self):
        self.assertEqual(settings_for("medium"), settings_for("bogus"))
        self.assertEqual(settings_for(""), settings_for("medium"))
        self.assertEqual(settings_for("HIGH"), settings_for("high"))

    def test_tables_are_consistent(self):
        for q in POLL_SECS:
            self.assertIn(q, CAPTION_TTL_SECS)
            poll, ttl = settings_for(q)
            self.assertEqual((poll, ttl), (POLL_SECS[q], CAPTION_TTL_SECS[q]))


class FingerprintTest(unittest.TestCase):
    def test_static_screen_hashes_identical(self):
        data = _jpeg()
        self.assertEqual(frame_fingerprint(data), frame_fingerprint(data))

    def test_changed_screen_hashes_differ(self):
        self.assertNotEqual(frame_fingerprint(_jpeg((40, 80, 120))),
                            frame_fingerprint(_jpeg((200, 40, 40))))

    def test_noise_within_quantisation_is_absorbed(self):
        a = _jpeg((100, 100, 100))
        b = _jpeg((104, 103, 101))       # tiny encoder/shine variance
        self.assertEqual(frame_fingerprint(a), frame_fingerprint(b))

    def test_empty_and_bad_data_return_empty(self):
        self.assertEqual(frame_fingerprint(b""), "")
        self.assertEqual(frame_fingerprint(None), "")
        self.assertEqual(frame_fingerprint(b"not-an-image"), "")


if __name__ == "__main__":
    unittest.main()