"""Tests for the boot sequence — the start animation between double-click and
workspace.

The rules it pins:

  * the veil exists in the markup, sits above every other overlay, and carries
    an escape hatch (`.is-gone`) that needs no JS to keep working once set;
  * app.js advances it through milestones boot() really reaches — bridge,
    interface — and dismisses it on a hard timer, so a hung backend can never
    trap the app behind a pretty screen;
  * the escape timer must exist: an animation whose only exit is the happy
    path is a lock-up waiting for the first slow machine;
  * reduced motion keeps the words and stops the rings;
  * whatever ships in preview_bundle.html (the docs demo) contains it too.

These read the sources directly — they are the same files the packaged exe
loads, and the honest way to check wiring that only runs inside a webview.
"""
import sys
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

HTML = BASE / "ui_web" / "index.html"
CSS = BASE / "ui_web" / "css" / "style.css"
JS_APP = BASE / "ui_web" / "js" / "app.js"
BUNDLE = BASE / "preview_bundle.html"
DEMO = BASE / "docs" / "demo.html"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


class BootVeilMarkupTest(unittest.TestCase):

    def setUp(self):
        self.html = _read(HTML)
        self.css = _read(CSS)

    def test_the_veil_exists_with_its_readouts(self):
        for marker in ("bootVeil", "bootName", "bootStage", "data-boot-step"):
            self.assertIn(marker, self.html, f"index.html is missing {marker}")

    def test_the_veil_sits_above_every_other_overlay(self):
        # setup veil is 70, the tour 68; the boot screen must out-rank both or
        # first-run users would see the setup card fighting the animation.
        z_of = {}
        for name in ("boot-veil", "setup-veil", "guide-veil"):
            block = self.css.split("." + name + " {", 1)
            self.assertEqual(len(block), 2, f"stylesheet lost .{name}")
            for line in block[1].splitlines():
                if "z-index" in line:
                    z_of[name] = int(line.split("z-index:")[1].split(";")[0].strip())
                    break
        self.assertGreater(z_of["boot-veil"], z_of["setup-veil"])
        self.assertGreater(z_of["boot-veil"], z_of["guide-veil"])

    def test_dismissal_is_a_class_the_css_honours(self):
        self.assertIn(".boot-veil.is-gone", self.css)
        gone = self.css.split(".boot-veil.is-gone {", 1)[1].split("}", 1)[0]
        self.assertIn("opacity: 0", gone)
        self.assertIn("pointer-events: none", gone,
                      "a dismissed veil must stop eating clicks even mid-fade")

    def test_reduced_motion_holds_the_rings_but_keeps_the_words(self):
        block = self.css.rsplit("@media (prefers-reduced-motion: reduce)", 1)[1]
        self.assertIn(".boot-core", block)
        self.assertNotIn(".boot-name", block, "the name must never be hidden")
        self.assertNotIn(".boot-stage", block, "the stage line must never be hidden")

    def test_the_stage_line_starts_empty_of_lies(self):
        # The markup may name the first milestone ("Waking up…") but must not
        # bake in "Online" — that word is earned when boot() actually finishes.
        veil = self.html.split('class="boot-veil"', 1)[1].split("</div>\n\n", 1)[0]
        self.assertNotIn("Online", veil)


class BootWiringTest(unittest.TestCase):
    """app.js must move the veil through real milestones and always dismiss it."""

    def setUp(self):
        self.js = _read(JS_APP)

    def test_boot_reports_the_bridge_result_not_a_blind_timer(self):
        self.assertRegex(
            self.js, r'BootSeq\.stage\(real \? "[^"]+" : "[^"]+"\)',
            "the first stage must depend on whenBridge(), not just elapse")

    def test_every_advance_sits_inside_boot_not_beside_it(self):
        # A stage call outside the async boot path would fire before the veil
        # logic exists; pin the ordering instead of trusting it.
        boot_start = self.js.index("async function boot()")
        boot_end = self.js.index("boot();", boot_start)
        body = self.js[boot_start:boot_end]
        for call in ("BootSeq.stage", "BootSeq.done"):
            self.assertIn(call, body, f"{call} must be called inside boot()")

    def test_there_is_a_hard_escape_timer(self):
        self.assertRegex(
            self.js, r"setTimeout\(done,\s*\d+\)",
            "without a max lifetime, a hung backend traps the app on the boot screen")
        self.assertIn("clearTimeout(escape)", self.js)

    def test_done_is_idempotent_towards_the_css(self):
        # done() clears the escape timer; a second call (escape vs normal path)
        # must not re-arm anything. The clearTimeout in done() is that promise.
        done_body = self.js.split("function done() {", 1)[1].split("}", 1)[0]
        self.assertIn("clearTimeout(escape)", done_body)
        self.assertNotIn("setTimeout(done", done_body,
                         "done() must not schedule further escapes")

    def test_the_boot_name_follows_the_assistant_rename(self):
        self.assertIn('$("bootName").textContent = S.assistantName.toUpperCase()', self.js,
                      "a renamed assistant must not boot under JARVIS")


class ShippedBundleTest(unittest.TestCase):

    def test_the_bundle_and_demo_carry_the_boot_sequence(self):
        if not BUNDLE.exists():
            self.skipTest("preview_bundle.html not built in this checkout")
        for artifact in (BUNDLE, DEMO):
            text = _read(artifact)
            if not text:
                self.skipTest(f"{artifact.name} not built in this checkout")
            with self.subTest(artifact=artifact.name):
                self.assertIn("bootVeil", text)
                self.assertIn(".boot-veil", text)
                self.assertIn("BootSeq", text)


if __name__ == "__main__":
    unittest.main()
