"""Unit tests for the microphone diagnostics and capture fallback.

SAFETY: no test opens a real audio stream. sounddevice, numpy and the Windows
registry are mocked; the real machine is only ever touched through
`windows_permission()` in one read-only test that tolerates any answer.
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import mic_diagnostics as md
from core import audio_devices as ad

import numpy as np     # imported once, at module level: re-importing per test
                       # raises 'cannot load module more than per process'


def _sd(devs, default_in=0, apis=("MME", "Windows DirectSound", "Windows WASAPI")):
    """A fake sounddevice module whose query_devices returns `devs`."""
    import types
    sd = types.ModuleType("sounddevice")
    sd.query_devices = lambda *a, **k: devs
    sd.query_hostapis = lambda *a, **k: [{"name": n} for n in apis]
    sd.default = types.SimpleNamespace(device=(default_in, default_in + 1))
    return sd


class DevicesTest(unittest.TestCase):
    def test_input_rows_are_listed_with_api_and_defaults(self):
        devs = [
            {"name": "Mic A", "hostapi": 0, "max_input_channels": 2,
             "default_samplerate": 44100.0},
            {"name": "Mic B", "hostapi": 2, "max_input_channels": 1,
             "default_samplerate": 48000.0},
            {"name": "Speakers", "hostapi": 0, "max_input_channels": 0,
             "default_samplerate": 48000.0},   # output only — never listed
        ]
        with mock.patch.dict(sys.modules, {"sounddevice": _sd(devs, default_in=0)}):
            rows = md.devices()
        names = [r["name"] for r in rows]
        self.assertIn("Mic A", names)
        self.assertIn("Mic B", names)
        self.assertNotIn("Speakers", names)
        a = next(r for r in rows if r["name"] == "Mic A")
        self.assertTrue(a["default"])
        self.assertFalse(next(r for r in rows if r["name"] == "Mic B")["default"])

    def test_no_sounddevice_is_an_honest_row_not_a_crash(self):
        with mock.patch.dict(sys.modules, {"sounddevice": None}):
            rows = md.devices()
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0]["openable"])


class PermissionTest(unittest.TestCase):
    def test_denied_desktop_apps_is_reported(self):
        def rv(path, value="Value"):
            if path.endswith("NonPackaged"):
                return "deny"
            return "allow"
        with mock.patch.object(md, "_reg_value", rv):
            p = md.windows_permission()
        self.assertFalse(p["allowed"])
        self.assertEqual(p["desktop_apps"], "denied")

    def test_master_denied_beats_everything(self):
        with mock.patch.object(md, "_reg_value", lambda *a, **k: "deny"):
            p = md.windows_permission()
        self.assertFalse(p["allowed"])
        self.assertEqual(p["master"], "denied")

    def test_missing_keys_are_unknown_not_denied(self):
        with mock.patch.object(md, "_reg_value", lambda *a, **k: None), \
             mock.patch.object(md, "_is_windows", return_value=True):
            p = md.windows_permission()
        self.assertTrue(p["allowed"])          # unreadable ≠ denied
        self.assertEqual(p["master"], "unknown")

    def test_non_windows_is_supported_false_and_allowed(self):
        with mock.patch.object(md, "_is_windows", return_value=False):
            p = md.windows_permission()
        self.assertFalse(p["supported"])
        self.assertTrue(p["allowed"])

    def test_real_registry_read_only_never_raises(self):
        p = md.windows_permission()    # whatever this machine says
        self.assertIsInstance(p.get("allowed"), bool)


class SignalTest(unittest.TestCase):
    def _feed(self, rms_blocks):
        """Run test_device with a fake sounddevice whose callback receives
        int16 noise of the given per-block RMS values."""
        blocks_rms = rms_blocks
        captured = []

        def cb(indata, n, *_a):
            captured.append(True)

        class FakeStream:
            def __init__(self, **kw):
                self._cb = kw.get("callback")
            def start(self):
                # deliver one int16 block per requested RMS. Uniform noise in
                # [-a, a) has RMS a/√3, so scale by √3 to hit the value.
                for rms in blocks_rms:
                    amp = int(min(32767, rms * 1.7321))
                    block = (np.random.default_rng(7).integers(
                        -amp, max(amp, 1), size=1024)).astype(np.int16)
                    self._cb(block.reshape(-1, 1), 1024, None, None)
            def stop(self): pass
            def close(self): pass

        sd = types_module()
        sd.InputStream = lambda **kw: FakeStream(**kw)
        with mock.patch.dict(sys.modules, {"sounddevice": sd}):
            return md.test_device(0, seconds=0.5)

    def test_speech_level_counts_as_ok(self):
        r = self._feed([60, 3000, 2500, 3200])
        self.assertEqual(r["verdict"], "ok")
        self.assertTrue(r["delivered"])

    def test_dead_silence_is_no_signal_not_ok(self):
        r = self._feed([3, 5, 4, 2])
        self.assertEqual(r["verdict"], "no_signal")
        # the exact desktop failure: opened AND delivered AND still no signal
        self.assertTrue(r["opened"])
        self.assertTrue(r["delivered"])

    def test_faint_signal_is_reported_as_faint(self):
        r = self._feed([30, 150, 120, 140])
        self.assertEqual(r["verdict"], "faint")

    def test_high_noise_floor_is_flagged(self):
        r = self._feed([600, 3000, 2800, 3100])
        self.assertTrue(r["noisy"])
        self.assertEqual(r["verdict"], "ok")

    def test_open_failure_is_explained_not_thrown(self):
        sd = types_module()

        def boom(**kw):
            raise Exception("Unanticipated host error [PaErrorCode -9999]")
        sd.InputStream = boom
        with mock.patch.dict(sys.modules, {"sounddevice": sd}):
            r = md.test_device(0)
        self.assertFalse(r["opened"])
        self.assertNotEqual(r["message"], "")

    def test_open_error_messages_are_actionable(self):
        for err, want in (
            ("Device unavailable", "unavailable"),
            ("device is already in use", "another application"),
            ("Invalid sample rate", "exclusive"),
            ("access denied", "privacy"),
        ):
            self.assertIn(want, md._explain_open_error(err).lower())


def types_module():
    import types
    return types.ModuleType("sounddevice")


class DiagnoseTest(unittest.TestCase):
    def test_diagnose_composes_the_chain(self):
        devs = [{"name": "Headset Mic", "hostapi": 0, "max_input_channels": 1,
                 "default_samplerate": 48000.0, "default": True,
                 "default_comm": False, "index": 0, "openable": True}]
        with mock.patch.dict(sys.modules, {"sounddevice": _sd(devs, default_in=0)}), \
             mock.patch.object(md, "test_device",
                               return_value={"opened": True, "delivered": True,
                                             "peak_rms": 3000.0, "floor_rms": 80.0,
                                             "speech_headroom": 37.0, "noisy": False,
                                             "verdict": "ok", "message": ""}):
            d = md.diagnose(0)
        self.assertEqual(d["verdict"], "ok")
        self.assertEqual(d["capture"], "working")
        self.assertEqual(d["signal"], "detected")
        self.assertEqual(d["selected_device"], "Headset Mic")
        self.assertEqual(d["problems"], [])

    def test_permission_denied_produces_a_fix(self):
        devs = [{"name": "Mic", "hostapi": 0, "max_input_channels": 1,
                 "default_samplerate": 48000.0, "default": True,
                 "default_comm": False, "index": 0, "openable": True}]
        with mock.patch.dict(sys.modules, {"sounddevice": _sd(devs, default_in=0)}), \
             mock.patch.object(md, "windows_permission",
                               return_value={"supported": True, "allowed": False,
                                             "master": "unknown",
                                             "store_apps": "unknown",
                                             "desktop_apps": "denied"}), \
             mock.patch.object(md, "test_device",
                               return_value={"opened": False, "delivered": False,
                                             "peak_rms": 0.0, "floor_rms": 0.0,
                                             "speech_headroom": 0.0, "noisy": False,
                                             "verdict": "failed",
                                             "message": "access denied — Windows "
                                                        "microphone privacy is blocking"}):
            d = md.diagnose(0)
        self.assertEqual(d["verdict"], "failed")
        self.assertTrue(any("privacy" in p.lower() or "permission" in p.lower()
                            for p in d["problems"]))
        self.assertTrue(any("privacy" in f.lower() for f in d["fixes"]))

    def test_no_devices_at_all(self):
        with mock.patch.dict(sys.modules, {"sounddevice": _sd([], default_in=-1)}):
            d = md.diagnose()
        self.assertFalse(d["device_present"])
        self.assertTrue(d["problems"])
        self.assertEqual(d["verdict"], "failed")


class RateFallbackTest(unittest.TestCase):
    """The root-cause fix: a fixed-rate desktop mic must open at its own rate."""

    def test_input_rates_carry_the_common_device_rates(self):
        rates = ad._input_rates()
        for want in (16000, 48000, 44100, 96000):
            self.assertIn(want, rates)
        self.assertEqual(rates[0], 16000)     # laptop path is untouched

    def test_input_open_rate_falls_through_to_the_device_rate(self):
        opened = []
        sd = types_module()

        class Stream:
            def __init__(self, samplerate, **kw):
                if samplerate != 48000:
                    raise Exception("Invalid sample rate")
                opened.append(samplerate)
            def start(self): pass
            def stop(self): pass
            def close(self): pass
        sd.InputStream = lambda samplerate, **kw: Stream(samplerate, **kw)
        with mock.patch.dict(sys.modules, {"sounddevice": sd}):
            ad.clear_rate_cache()
            self.assertEqual(ad.input_open_rate(3), 48000)
            self.assertEqual(opened, [48000])
            # cached: no second open
            self.assertEqual(ad.input_open_rate(3), 48000)
            self.assertEqual(len(opened), 1)

    def test_unusable_device_is_none_not_an_exception(self):
        sd = types_module()
        sd.InputStream = lambda **kw: (_ for _ in ()).throw(Exception("no"))
        with mock.patch.dict(sys.modules, {"sounddevice": sd}):
            ad.clear_rate_cache()
            self.assertIsNone(ad.input_open_rate(9))

    def test_clear_rate_cache_forgets_after_a_replug(self):
        sd = types_module()

        class Stream:
            def __init__(self, **kw): pass
            def start(self): pass
            def stop(self): pass
            def close(self): pass
        sd.InputStream = lambda **kw: Stream(**kw)
        with mock.patch.dict(sys.modules, {"sounddevice": sd}):
            ad.clear_rate_cache()
            self.assertEqual(ad.input_open_rate(1), 16000)
            ad.clear_rate_cache()
            self.assertEqual(ad.input_open_rate(1), 16000)   # re-probed fresh


if __name__ == "__main__":
    unittest.main()
