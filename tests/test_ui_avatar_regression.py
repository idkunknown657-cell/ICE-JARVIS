"""Frontend regression guards for the web UI (no JS test runner exists, so these
assert the invariants that broke in the wild).

The bug: the avatar canvas sized itself from its own width/height ATTRIBUTES
(`max-width/max-height` + `aspect-ratio` around an intrinsically sized canvas).
The renderer rewrites those attributes to the current CSS box, so the moment the
stage had no box — which happens the instant you open Settings, because the home
view becomes display:none — the renderer wrote 0x0 into them, the element
collapsed, and the canvas stayed 0x0 permanently. The face went blank for good
and never reacted to voice again, which is exactly what "switching the avatar
mode to ORBIT/HELIX broke everything" looked like: you have to open Settings to
change it, so the face was already dead by the time the pills were clicked.

These tests are deliberately about the invariant, not the implementation: the
canvas box must never depend on the canvas' own mutable attributes, and the
renderer must never write a zero-size backing store.
"""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CSS = ROOT / "ui_web" / "css" / "style.css"
JS_AVATAR = ROOT / "ui_web" / "js" / "avatar.js"
JS_APP = ROOT / "ui_web" / "js" / "app.js"
HTML = ROOT / "ui_web" / "index.html"
BUNDLE = ROOT / "preview_bundle.html"


def _read(p):
    return p.read_text(encoding="utf-8")


def css_rule(text, selector):
    """Return the declaration block for the first exact selector match."""
    m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", text)
    return m.group(1) if m else ""


class AvatarCanvasCannotCollapseTest(unittest.TestCase):

    def test_face_box_is_pinned_to_the_stage(self):
        block = css_rule(_read(CSS), "#face")
        self.assertTrue(block, "#face rule disappeared from style.css")
        self.assertIn("position: absolute", block,
                      "#face must not be sized from its own canvas attributes")
        self.assertIn("inset: 0", block)
        self.assertNotIn("aspect-ratio", block,
                         "an intrinsic-size-driven box can collapse to 0x0 again")

    def test_renderer_never_writes_a_zero_backing_store(self):
        js = _read(JS_AVATAR)
        self.assertRegex(js, r"if \(cssW < 2 \|\| cssH < 2\) return;")
        # and it still clamps a real box to at least one pixel
        self.assertIn("Math.max(1, Math.round(cssW * dpr))", js)
        self.assertIn("Math.max(1, Math.round(cssH * dpr))", js)

    def test_avatar_loop_keeps_running_when_the_view_is_hidden(self):
        js = _read(JS_AVATAR)
        # the guard must sit inside render(), before anything is drawn
        body = js[js.index("function render()"):]
        self.assertLess(body.index("cssW < 2"), body.index("ctx.setTransform"))


class AvatarModeSwitchSafetyTest(unittest.TestCase):

    def test_crossfade_releases_the_stage_even_if_the_swap_throws(self):
        js = _read(JS_APP)
        body = js[js.index("function crossfadeAvatar"):]
        body = body[:body.index("\n  }\n")]
        self.assertIn("try { applyFn(); }", body)
        self.assertIn("catch", body)
        # a queued click must not be silently dropped
        self.assertIn("_fadePending", body)
        # and a throttled/frozen rAF can never strand the stage at opacity 0
        self.assertIn("setTimeout(finish", body)

    def test_mode_switch_reports_itself_in_the_hud(self):
        app = _read(JS_APP)
        self.assertIn("function paintHud", app)
        self.assertIn("hudMode", app)
        # the sight readout drives a body flag that the CSS keys off
        self.assertIn("dataset.sight", app)
        self.assertIn("data-sight", _read(CSS))
        html = _read(HTML)
        for ident in ("hudMode", "hudSight", "hudVu"):
            self.assertIn('id="%s"' % ident, html)


class VoiceMeterTest(unittest.TestCase):

    def test_voice_vars_are_published_on_the_stage(self):
        js = _read(JS_AVATAR)
        self.assertIn('document.getElementById("faceStage")', js)
        for var in ("--voice", "--open", "--wide"):
            self.assertIn('setProperty("%s"' % var, js)

    def test_meter_bars_read_the_shared_voice_variable(self):
        block = css_rule(_read(CSS), ".hud-vu i")
        self.assertIn("--voice", block)
        self.assertIn("--th", block)
        # every bar needs its own threshold or the meter flashes as one blob
        thresholds = re.findall(r"\.hud-vu i:nth-child\(\d\) \{ --th:", _read(CSS))
        self.assertEqual(len(thresholds), 7)


class LivingMotionTest(unittest.TestCase):
    """The core must look alive, and it must never be able to break itself.

    The bug that cost an afternoon: spring("tilt") integrates MOTION.tilt AND
    MOTION.tiltv, but the state object declared `vtilt`/`vpulse`. Undeclared
    fields read `undefined`, `undefined += x` is NaN, NaN reached the canvas
    radius and `createRadialGradient` threw every single frame — the whole
    avatar froze. A single name mismatch is invisible in review and silent in
    a browser, so the invariant is asserted instead: every channel a spring
    integrates has BOTH its position and its velocity declared, the published
    values are clamped near the centre, and the CSS vars the halo rides are
    really published.
    """

    def _motion_fields(self):
        js = _read(JS_AVATAR)
        block = js[js.index("const MOTION = {"):]
        block = block[:block.index("};\n")]
        block = re.sub(r"//[^\n]*", "", block)          # comments hold prose
        return js, set(re.findall(r"(\w+)\s*:", block))

    def test_every_spring_channel_declares_position_and_velocity(self):
        js, declared = self._motion_fields()
        channels = set(re.findall(r'spring\("(\w+)"', js))
        self.assertTrue(channels, "the motion driver stopped driving springs")
        for ch in sorted(channels):
            self.assertIn(ch, declared,
                          "spring(%r) has no MOTION.%s to integrate" % (ch, ch))
            self.assertIn(ch + "v", declared,
                          "spring(%r) integrates MOTION.%sv; an undeclared "
                          "velocity field is `undefined`, which becomes NaN and "
                          "kills the frame" % (ch, ch))

    def test_a_poisoned_channel_heals_instead_of_freezing_the_frame(self):
        js = _read(JS_AVATAR)
        body = js[js.index("function spring("):]
        body = body[:body.index("\n  }\n")]
        self.assertIn("!isFinite(MOTION[ch])", body)
        self.assertIn('!isFinite(MOTION[ch + "v"])', body)
        # the sanitising must happen BEFORE anything integrates
        self.assertLess(body.index("!isFinite(MOTION[ch])"),
                        body.index("MOTION[ch + \"v\"] +="))

    def test_published_motion_stays_near_the_centre(self):
        js = _read(JS_AVATAR)
        for field, limit in (("cx", 0.12), ("cy", 0.12), ("cz", 0.12), ("rot", 0.12)):
            m = re.search(
                r"MOTION\.%s = Math\.max\((-?[\d.]+), Math\.min\((-?[\d.]+)"
                % field, js)
            self.assertIsNotNone(
                m, "MOTION.%s is published unclamped — the core could leave the "
                   "centre" % field)
            lo, hi = float(m.group(1)), float(m.group(2))
            self.assertLessEqual(max(abs(lo), abs(hi)), limit,
                                 "MOTION.%s clamp is wider than %.0f%% of the "
                                 "core radius" % (field, limit * 100))
        m = re.search(r"MOTION\.swell = 1 \+ Math\.max\((-?[\d.]+), "
                      r"Math\.min\((-?[\d.]+)", js)
        self.assertIsNotNone(m, "the scale swell is no longer bounded")
        self.assertGreaterEqual(float(m.group(1)), -0.05)
        self.assertLessEqual(float(m.group(2)), 0.15)

    def test_motion_is_published_to_the_hud_as_one_body(self):
        js = _read(JS_AVATAR)
        for var in ("--mx", "--my", "--mz", "--mrot", "--mswell"):
            self.assertIn('setProperty("%s"' % var, js,
                          "the HUD halo no longer rides %s" % var)
        # a non-finite value must never be written into a CSS variable
        for var in ("--mx", "--my", "--mz", "--mrot", "--mswell"):
            line = [l for l in js.splitlines() if 'setProperty("%s"' % var in l]
            self.assertTrue(line, "%s vanished" % var)
            self.assertIn("isFinite", line[0],
                          "%s can be published as NaN" % var)

    def test_halo_motion_composes_with_centring_instead_of_overwriting_it(self):
        css = _read(CSS)
        block = css_rule(css, ".hud-core-glow")
        self.assertIn("translate: calc(-50%", block,
                      "the halo must keep its centring inside translate")
        self.assertIn("--mrot", block)
        self.assertIn("--mswell", block)
        self.assertNotIn("transform:", block,
                         "a transform here would overwrite the centring")
        # and the core underneath still centres itself with transform, because
        # its breathing keyframes animate transform and nothing else
        core = css_rule(css, ".hud-core")
        self.assertIn("transform: translate(-50%, -50%)", core)
        self.assertIn("--mx", core)


class BundleIsFreshTest(unittest.TestCase):

    def test_preview_bundle_was_rebuilt_after_the_frontend_changes(self):
        html = _read(HTML)
        css = _read(CSS)
        js = _read(JS_AVATAR)
        bundle = _read(BUNDLE)

        link = '<link rel="stylesheet" href="css/style.css">'
        self.assertNotIn(link, bundle, "make_bundle.py did not inline the CSS")
        # the fixes must be present in the shipped bundle too, not just the sources
        self.assertIn(css_rule(css, "#face").splitlines()[0].strip(), bundle)
        self.assertIn("if (cssW < 2 || cssH < 2) return;", bundle)
        self.assertIn("hud-vu", bundle)
        # every element the bundle's markup expects is actually inlined
        self.assertTrue(all(('id="%s"' % i) in bundle for i in ("hudMode", "hudSight", "hudVu")),
                        "the bundle's markup is out of date")


if __name__ == "__main__":
    unittest.main()
