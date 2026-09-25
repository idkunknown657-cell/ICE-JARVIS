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
