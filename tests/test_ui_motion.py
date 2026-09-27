"""Contract tests for the shared front-end motion module and transition policy.

These are static checks over the front-end source, which is unusual for this
suite — they exist because the defects they guard against are invisible to a
Python test and trivially reintroduced:

  * Both avatars had grown their OWN copy of the same spring, and both copies
    advanced by `value * dt * k` per frame. That is frame-rate dependent, so
    JARVIS moved differently on a slow machine than on a fast one (measured: the
    pulse channel settled to 0.60 at 8 fps against 0.93 at 60 fps), and the
    mouth whose easing is k=14 cleared 90% of its distance in a single frame
    below 20 fps — a snap where there should have been an ease.
  * Interactive chrome used `transition: all`, which animates every property
    that changes, including width/padding/offset. A transition on a
    layout-affecting property forces layout on the main thread every frame
    instead of compositing transform and opacity.

The behaviours themselves are verified numerically in the browser; what is
pinned here is the STRUCTURE that makes those behaviours hold — one shared
implementation, wired into both loaders, and no per-frame integration left
behind.
"""
import json
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

MOTION_JS = ROOT / "ui_web" / "js" / "motion.js"
AVATAR_JS = ROOT / "ui_web" / "js" / "avatar.js"
AVATAR3D_JS = ROOT / "ui_web" / "js" / "avatar3d.js"
INDEX_HTML = ROOT / "ui_web" / "index.html"
STYLE_CSS = ROOT / "ui_web" / "css" / "style.css"
BUNDLER = ROOT / "make_bundle.py"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


class MotionModuleTest(unittest.TestCase):
    def test_the_module_exists_and_publishes_itself(self):
        self.assertTrue(MOTION_JS.exists())
        src = _read(MOTION_JS)
        self.assertIn("window.JarvisMotion", src)
        for fn in ("smooth", "toward", "step", "stepFlat", "blinkScale",
                   "blinkAdvance", "drift"):
            self.assertRegex(src, rf"M\.{fn}\s*=\s*function",
                             f"{fn} must be part of the shared surface")

    def test_easing_is_exponential_not_a_clamped_linear_step(self):
        """The linear form clamps at 1, so rate*dt ≥ 1 teleports. The exponential
        form cannot exceed the target at any frame rate."""
        src = _read(MOTION_JS)
        self.assertIn("Math.exp(-", src)

    def test_the_spring_is_substepped(self):
        """A spring integrated at the frame interval changes shape as the
        interval grows; a fixed sub-step makes it frame-rate independent."""
        src = _read(MOTION_JS)
        self.assertRegex(src, r"MAX_STEP\s*=")
        self.assertIn("remaining", src)

    def test_a_blink_is_never_fewer_than_three_frames(self):
        src = _read(MOTION_JS)
        m = re.search(r"BLINK_MIN_FRAMES\s*=\s*(\d+)", src)
        self.assertIsNotNone(m, "BLINK_MIN_FRAMES must be declared")
        self.assertGreaterEqual(int(m.group(1)), 3,
                                "a two-frame blink is a flash, not an eyelid")

    def test_short_values_are_left_alone(self):
        """Nothing here may assert an exact frame count beyond the floor."""
        src = _read(MOTION_JS)
        self.assertNotIn("setInterval", src)
        self.assertNotIn("performance.now", src,
                         "motion primitives take dt; they must not read a clock")


class AvatarsUseTheSharedModuleTest(unittest.TestCase):
    def test_the_2d_avatar_defers_to_the_shared_module(self):
        src = _read(AVATAR_JS)
        self.assertIn("JarvisMotion", src)
        self.assertIn("stepFlat", src)
        # the local per-frame integrator must be gone
        self.assertNotIn("MOTION[ch + \"v\"] += (target", src)

    def test_the_3d_avatar_defers_to_the_shared_module(self):
        src = _read(AVATAR3D_JS)
        self.assertIn("JarvisMotion", src)
        self.assertIn("JM.step(", src)
        # the per-frame integrator that used to live in spring3()
        self.assertNotIn("ch.v += (target - ch.p) * k * dt;", src)

    def test_both_avatars_use_the_eased_blink(self):
        for path in (AVATAR_JS, AVATAR3D_JS):
            src = _read(path)
            self.assertIn("blinkAdvance", src, f"{path.name} must advance a blink phase")
            self.assertIn("blinkScale", src, f"{path.name} must use the eased lid")
            self.assertNotIn("blinkPhase += dt * 6.5", src,
                             "the raw per-frame blink advance must be gone")

    def test_neither_avatar_zeroes_its_frame_accumulator(self):
        """Zeroing `acc` quantises the draw cadence to whole rAF slots, so the
        frame gaps alternate between two and three slots and the movement reads
        as uneven even though the animation clock is correct. The remainder must
        be kept — the constructor's `this.acc = 0` initialisation is fine and is
        deliberately not matched here."""
        src2d = _read(AVATAR_JS)
        self.assertNotIn("acc = 0; tickVisemes", src2d)
        self.assertIn("acc - interval", src2d)

        src3d = _read(AVATAR3D_JS)
        self.assertNotIn("const step = this.acc; this.acc = 0;", src3d)
        self.assertIn("this.acc - interval", src3d)

    def test_a_fallback_exists_for_a_missing_module(self):
        """If motion.js fails to load the avatars must still run, not throw."""
        for path in (AVATAR_JS, AVATAR3D_JS):
            src = _read(path)
            self.assertRegex(src, r"JarvisMotion\s*\|\|",
                             f"{path.name} needs a fallback for a missing motion.js")


class LoadingTest(unittest.TestCase):
    def test_index_html_loads_motion_before_the_avatars(self):
        html = _read(INDEX_HTML)
        self.assertIn("js/motion.js", html)
        self.assertLess(html.index("js/motion.js"), html.index("js/avatar.js"),
                        "motion.js must be defined before avatar.js runs")

    def test_the_bundler_inlines_it(self):
        src = _read(BUNDLER)
        self.assertIn("js/motion.js", src)
        self.assertLess(src.index("js/motion.js"), src.index("js/avatar.js"))


@unittest.skipUnless(shutil.which("node"), "node is needed to run the module")
class MotionBehaviourTest(unittest.TestCase):
    """The module actually RUN, where a JavaScript engine is available.

    A structural test can only prove the code says the right thing; these prove
    it does the right thing, which is the part that matters for a spring that
    once fed NaN into a canvas gradient and froze the whole avatar.
    """

    @staticmethod
    def _run(script: str):
        out = subprocess.run(
            ["node", "-e", script], cwd=str(ROOT), capture_output=True,
            text=True, timeout=60)
        if out.returncode != 0:
            raise AssertionError(f"node failed:\n{out.stderr}")
        return json.loads(out.stdout.strip().splitlines()[-1])

    def test_a_poisoned_channel_heals_instead_of_sticking(self):
        got = self._run("""
          global.window = {};
          require('./ui_web/js/motion.js');
          const M = window.JarvisMotion;
          const ch = { p: NaN, v: undefined };
          const a = M.step(ch, 1, 1/60, 6, 5);
          const b = M.step(ch, 1, 1/60, 6, 5);
          console.log(JSON.stringify({ first: [a, ch.v], second: [b, ch.v],
                                       finite: isFinite(a) && isFinite(b) }));
        """)
        self.assertTrue(got["finite"], "a poisoned channel must not stay NaN")
        self.assertNotEqual(got["first"], got["second"],
                            "a healed channel must actually move again")

    def test_the_spring_is_frame_rate_independent(self):
        got = self._run("""
          global.window = {};
          require('./ui_web/js/motion.js');
          const M = window.JarvisMotion;
          function settle(fps, seconds) {
            const ch = { p: 0, v: 0 };
            const dt = 1 / fps;
            for (let i = 0; i < fps * seconds; i++) M.step(ch, 1, dt, 6, 5);
            return ch.p;
          }
          console.log(JSON.stringify({ f60: settle(60, 2), f30: settle(30, 2),
                                       f15: settle(15, 2), f8: settle(8, 2) }));
        """)
        spread = max(got.values()) - min(got.values())
        self.assertLess(spread, 0.01,
                        f"the same 2 s of settling must land in the same place at "
                        f"every frame rate; got {got}")

    def test_easing_never_overshoots_or_snaps(self):
        got = self._run("""
          global.window = {};
          require('./ui_web/js/motion.js');
          const M = window.JarvisMotion;
          const out = {};
          for (const fps of [60, 30, 15, 8]) {
            let v = 0;
            for (let i = 0; i < fps; i++) v = M.smooth(v, 1, 14, 1 / fps);
            out[fps] = 1 - v;      // remaining gap after 1 s (keep precision)
          }
          console.log(JSON.stringify(out));
        """)
        # after one second of a rate-14 ease the remaining gap is e^-14 at any rate
        for fps, remaining in got.items():
            self.assertAlmostEqual(remaining, 8.3e-7, delta=5e-7,
                                   msg=f"{fps} fps eased differently: {remaining}")
            self.assertGreaterEqual(remaining, 0.0, "an ease must not overshoot")

    def test_a_blink_gets_at_least_three_frames_at_every_rate(self):
        got = self._run("""
          global.window = {};
          require('./ui_web/js/motion.js');
          const M = window.JarvisMotion;
          const out = {};
          for (const fps of [60, 30, 15, 12, 8]) {
            let phase = 0.001, n = 0;
            while (phase > 0 && n < 60) { phase = M.blinkAdvance(phase, 1 / fps); n++; }
            out[fps] = n;
          }
          console.log(JSON.stringify(out));
        """)
        for fps, frames in got.items():
            self.assertGreaterEqual(frames, 3,
                                    f"a blink at {fps} fps took only {frames} frame(s)")

    def test_the_lid_curve_is_smooth_at_both_ends(self):
        got = self._run("""
          global.window = {};
          require('./ui_web/js/motion.js');
          const M = window.JarvisMotion;
          const out = { mid: M.blinkScale(0.5), open: M.blinkScale(0),
                        closed: M.blinkScale(1),
                        slopeNearOpen: M.blinkScale(0.02) - M.blinkScale(0) };
          console.log(JSON.stringify(out));
        """)
        self.assertAlmostEqual(got["open"], 1.0, places=6)
        self.assertAlmostEqual(got["closed"], 1.0, places=6)
        self.assertLess(got["mid"], 0.12, "the lid must actually close")
        # a raised cosine starts flat: no visible snap as the blink begins
        self.assertLess(abs(got["slopeNearOpen"]), 0.01,
                        "a triangle-wave blink snaps; a raised cosine does not")


class TransitionPolicyTest(unittest.TestCase):
    def test_no_transition_all_in_the_stylesheet(self):
        """`transition: all` animates layout-affecting properties too, so a
        hover can force layout on the main thread every frame."""
        # Comments must go first: this file documents the rule it obeys, and a
        # scanner that reads prose as CSS would flag its own explanation.
        css = re.sub(r"/\*.*?\*/", "", _read(STYLE_CSS), flags=re.S)
        offenders = [f"line {i}: {line.strip()}"
                     for i, line in enumerate(css.splitlines(), 1)
                     if re.search(r"transition:\s*all\b", line)]
        self.assertEqual(offenders, [],
                         "name the properties instead:\n" + "\n".join(offenders))

    def test_the_shared_transition_token_exists_and_is_named(self):
        css = _read(STYLE_CSS)
        self.assertIn("--t-ui:", css)
        self.assertGreater(css.count("var(--t-ui)"), 5,
                           "the token must actually be used by the chrome")
        # and it must not smuggle a layout property back in
        token = re.search(r"--t-ui:(.*?);", css, re.S).group(1)
        for bad in ("width", "height", "top", "left", "margin", "padding"):
            self.assertNotIn(bad, token,
                             f"'{bad}' is layout-affecting and must not transition")


if __name__ == "__main__":
    unittest.main()
