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


class ProbeRateLadderTest(unittest.TestCase):
    """The probe must walk the same rate ladder as the capture layer.

    Regression: test_device asked every device for 16 kHz and nothing else, so a
    fixed-rate desktop microphone — USB interface, webcam, Bluetooth HFP — failed
    to open in the diagnostic while main.py was recording from the very same
    device happily. The user was then told their voice was not reaching JARVIS,
    which was not true: only the rate we asked for was wrong.
    """

    def _sd_that_only_accepts(self, good_rate):
        """A fake sounddevice whose InputStream rejects every rate but one."""
        import types
        sd = types.ModuleType("sounddevice")
        attempts = []

        class Stream:
            def __init__(self, samplerate, **kw):
                attempts.append(samplerate)
                if samplerate != good_rate:
                    raise Exception("Invalid sample rate")
                self._cb = kw.get("callback")

            def start(self):
                rng = np.random.default_rng(11)
                for _ in range(3):
                    block = rng.integers(-3000, 3000, 1024).astype(np.int16)
                    self._cb(block.reshape(-1, 1), 1024, None, None)
            def stop(self): pass
            def close(self): pass

        sd.InputStream = lambda **kw: Stream(**kw)
        return sd, attempts

    def test_fixed_rate_mic_is_measured_not_declared_dead(self):
        sd, attempts = self._sd_that_only_accepts(48000)
        with mock.patch.dict(sys.modules, {"sounddevice": sd}):
            r = md.test_device(0, seconds=0.4)
        self.assertTrue(r["opened"], "a 48 kHz-only mic must still open")
        self.assertEqual(r["rate"], 48000)
        self.assertTrue(r["delivered"])
        self.assertNotEqual(r["verdict"], "no_signal")
        # The preferred rate is still tried first, so a laptop mic is untouched.
        self.assertEqual(attempts[0], 16000)
        self.assertIn(48000, attempts)

    def test_the_probe_and_the_capture_layer_share_one_ladder(self):
        self.assertEqual(md._rates_to_try(), ad.input_rate_ladder())
        self.assertEqual(ad.input_rate_ladder()[0], 16000)

    def test_a_device_that_opens_at_no_rate_reports_an_actionable_message(self):
        sd = types_module()
        sd.InputStream = lambda **kw: (_ for _ in ()).throw(
            Exception("Invalid sample rate"))
        with mock.patch.dict(sys.modules, {"sounddevice": sd}):
            r = md.test_device(0)
        self.assertFalse(r["opened"])
        self.assertEqual(r["rate"], 0)
        self.assertIn("exclusive", r["message"].lower())

    def test_diagnose_reports_the_rate_it_opened_at(self):
        devs = [{"name": "USB Mic", "hostapi": 0, "max_input_channels": 1,
                 "default_samplerate": 48000.0, "default": True,
                 "default_comm": False, "index": 0, "openable": True}]
        with mock.patch.dict(sys.modules, {"sounddevice": _sd(devs, default_in=0)}), \
             mock.patch.object(md, "test_device",
                               return_value={"opened": True, "rate": 48000,
                                             "delivered": True, "peak_rms": 3000.0,
                                             "floor_rms": 80.0,
                                             "speech_headroom": 37.0,
                                             "noisy": False, "verdict": "ok",
                                             "message": ""}):
            d = md.diagnose(0)
        self.assertEqual(d["verdict"], "ok")
        self.assertEqual(d["rate"], 48000)

    def test_diagnose_tolerates_a_probe_that_reports_no_rate(self):
        # webui and older callers may hand diagnose a stubbed probe result.
        with mock.patch.object(md, "test_device",
                               return_value={"opened": False, "delivered": False,
                                             "peak_rms": 0.0, "floor_rms": 0.0,
                                             "speech_headroom": 0.0,
                                             "noisy": False,
                                             "verdict": "no_signal",
                                             "message": ""}):
            d = md.diagnose(0)
        self.assertEqual(d["rate"], 0)


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


class QuietRoomTest(unittest.TestCase):
    """A silent room is not a broken microphone.

    Regression, measured on a real desktop PC: three consecutive 1.2 s windows
    of the SAME working microphone — same host API, nothing moved — read 202.8,
    20.9 and 133.1. That spread is wider than the gap between the silence and
    speech thresholds, so one short sample cannot classify a device. The old
    probe classified from exactly one, which is how a perfectly good mic in a
    quiet room was told, in red, that "nothing is reaching me".
    """

    def _stream(self, per_attempt_rms):
        """Fake sounddevice delivering different blocks on each open."""
        import types
        sd = types.ModuleType("sounddevice")
        opens = {"n": 0}

        class Stream:
            def __init__(self, **kw):
                self._cb = kw.get("callback")
                i = min(opens["n"], len(per_attempt_rms) - 1)
                self._rms = per_attempt_rms[i]
                opens["n"] += 1

            def start(self):
                for rms in self._rms:
                    amp = int(min(32767, rms * 1.7321))
                    block = (np.random.default_rng(5).integers(
                        -amp, max(amp, 1), size=1024)).astype(np.int16)
                    self._cb(block.reshape(-1, 1), 1024, None, None)

            def stop(self): pass
            def close(self): pass

        sd.InputStream = lambda **kw: Stream(**kw)
        return sd

    def _run(self, per_attempt_rms):
        with mock.patch.dict(sys.modules, {"sounddevice": self._stream(per_attempt_rms)}):
            return md.test_device(0, seconds=0.3)

    def test_a_quiet_room_is_inconclusive_not_a_failure(self):
        r = self._run([[60, 70, 65, 62]])
        self.assertTrue(r["opened"] and r["delivered"])
        self.assertEqual(r["verdict"], "quiet")
        self.assertNotEqual(r["verdict"], "no_signal")
        self.assertIn("say something", r["message"].lower())

    def test_a_transient_dead_sample_does_not_condemn_the_microphone(self):
        # First open digitally silent, second one plainly carrying speech.
        r = self._run([[2, 3, 1], [80, 2600, 2400]])
        self.assertEqual(r["verdict"], "ok")
        self.assertEqual(r["attempts_used"], 2)
        self.assertEqual(len(r["peaks"]), 2)
        self.assertGreaterEqual(r["peak_rms"], md.DEAD_RMS)

    def test_a_genuinely_dead_device_is_still_reported_dead(self):
        # The retry must not become a way of never believing a dead reading.
        r = self._run([[2, 3, 1]])
        self.assertEqual(r["verdict"], "no_signal")
        self.assertEqual(r["attempts_used"], md.DEFAULT_ATTEMPTS)

    def test_a_good_reading_is_not_re_measured(self):
        r = self._run([[2600, 2500]])
        self.assertEqual(r["verdict"], "ok")
        self.assertEqual(r["attempts_used"], 1)

    def test_the_sample_is_long_enough_for_a_person_to_react(self):
        self.assertGreaterEqual(md.DEFAULT_TEST_SECONDS, 2.0)
        # ...but never long enough to feel like a hang.
        self.assertLessEqual(md.DEFAULT_TEST_SECONDS, 5.0)

    def test_dead_is_far_below_a_quiet_room(self):
        # A quiet room really does read in the tens-to-low-hundreds; the old
        # 90 RMS "silence" line sat INSIDE that population, which is the bug.
        self.assertLess(md.DEAD_RMS, 40.0)


_QUIET_PROBE = {"opened": True, "delivered": True, "peak_rms": 64.0,
                "floor_rms": 58.0, "speech_headroom": 1.1, "noisy": False,
                "blocks": 30, "attempts_used": 1, "peaks": [64.0],
                "verdict": "quiet", "message": "the microphone is delivering "
                                                  "audio, but nothing above room "
                                                  "noise arrived"}
_DEAD_PROBE = {"opened": True, "delivered": True, "peak_rms": 3.0,
               "floor_rms": 2.0, "speech_headroom": 1.5, "noisy": False,
               "blocks": 30, "attempts_used": 2, "peaks": [3.0, 2.0],
               "verdict": "no_signal", "message": "every sample was "
                                                    "digitally silent"}


def _one_default_mic():
    return [{"name": "Desktop Mic", "hostapi": 0, "max_input_channels": 1,
             "default_samplerate": 44100.0, "default": True,
             "default_comm": False, "index": 0, "openable": True}]


class DiagnoseQuietTest(unittest.TestCase):
    """The chain diagnosis must not turn an inconclusive reading into a fault."""

    def test_a_quiet_room_is_reported_as_inconclusive(self):
        with mock.patch.dict(sys.modules,
                             {"sounddevice": _sd(_one_default_mic(), default_in=0)}), \
             mock.patch.object(md, "test_device", return_value=dict(_QUIET_PROBE)):
            d = md.diagnose(0)
        self.assertEqual(d["verdict"], "quiet")
        self.assertTrue(d["inconclusive"])
        self.assertNotEqual(d["verdict"], "failed")
        # Nothing is broken, so nothing is reported as broken...
        self.assertEqual(d["problems"], [])
        self.assertEqual(d["capture"], "working")
        self.assertEqual(d["signal"], "quiet")
        # ...but the user is still told what to do about it.
        self.assertTrue(d["fixes"])
        self.assertTrue(any("speak" in f.lower() for f in d["fixes"]))

    def test_a_digitally_silent_device_is_still_a_problem(self):
        with mock.patch.dict(sys.modules,
                             {"sounddevice": _sd(_one_default_mic(), default_in=0)}), \
             mock.patch.object(md, "test_device", return_value=dict(_DEAD_PROBE)):
            d = md.diagnose(0)
        self.assertEqual(d["verdict"], "failed")
        self.assertFalse(d["inconclusive"])
        self.assertTrue(d["problems"])
        self.assertTrue(any("silent" in p.lower() for p in d["problems"]))

    def test_diagnose_gives_the_probe_time_to_hear_speech(self):
        with mock.patch.dict(sys.modules,
                             {"sounddevice": _sd(_one_default_mic(), default_in=0)}), \
             mock.patch.object(md, "test_device",
                               return_value=dict(_QUIET_PROBE)) as probe:
            md.diagnose(0)
        self.assertEqual(probe.call_args.kwargs.get("seconds"),
                         md.DEFAULT_TEST_SECONDS)


class WebVerdictContractTest(unittest.TestCase):
    """Every verdict the backend can return must have a rendering in the UI.

    A verdict with no entry in the web UI's MIC_VERDICT table falls through to
    ``failed`` — a red error panel. That is exactly how a healthy microphone in
    a quiet room got painted as broken, so the two lists are pinned together
    here rather than left to drift.
    """

    def _ui_verdict_keys(self):
        js = (Path(__file__).resolve().parent.parent
              / "ui_web" / "js" / "app.js").read_text(encoding="utf-8")
        start = js.index("const MIC_VERDICT = {")
        body = js[start:js.index("\n  };", start)]
        keys = set()
        for line in body.splitlines()[1:]:
            stripped = line.strip()
            if stripped.endswith(": {"):
                keys.add(stripped[:-3].strip())
        return keys

    def test_every_backend_verdict_is_rendered(self):
        keys = self._ui_verdict_keys()
        self.assertIn("ok", keys)
        for verdict in ("ok", "faint", "quiet", "no_signal"):
            self.assertIn(verdict, keys,
                          f"{verdict} would fall through to the red failure "
                          f"panel in the UI")

    def test_no_signal_is_the_only_red_signal_verdict(self):
        js = (Path(__file__).resolve().parent.parent
              / "ui_web" / "js" / "app.js").read_text(encoding="utf-8")
        start = js.index("const MIC_VERDICT = {")
        body = js[start:js.index("\n  };", start)]
        for name in ("ok", "faint", "quiet"):
            block_start = body.index(name + ": {")
            block = body[block_start:block_start + 260]
            self.assertNotIn('tone: "bad"', block,
                             f"a {name} reading must not be painted as an error")


if __name__ == "__main__":
    unittest.main()
