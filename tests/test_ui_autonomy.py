"""
tests/test_ui_autonomy.py — the contract for autonomous PC mode in the interface.

Three things are being pinned here, and each of them broke at least once in
development:

  * the markup the new code reaches for actually exists, and the shipped bundle
    contains it (a rebuilt bundle is the difference between "works in dev" and
    "works for the user");
  * every mood the Python engine can be in has a colour the stylesheet knows
    about — the alternative is a card that silently falls back to looking
    curious while JARVIS is bored, and nothing in either file would notice;
  * the microphone control no longer paints itself red to mean "muted", because
    a mute the user chose is a state, not a fault. That regression is asserted
    against the source rather than a screenshot.

No browser, no pywebview window, no event loop. The bridge tests instantiate the
real JarvisAPI (which only spins a metrics thread) and mock the config writes.
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import webui                                     # noqa: E402
from core import autonomy                        # noqa: E402
from core import initiative                      # noqa: E402

HTML = BASE / "ui_web" / "index.html"
CSS = BASE / "ui_web" / "css" / "style.css"
JS_APP = BASE / "ui_web" / "js" / "app.js"
JS_MOCK = BASE / "ui_web" / "js" / "mock.js"
BUNDLE = BASE / "preview_bundle.html"

#: What the card, the tour and the microphone need to exist under. Kept as data
#: so a failure names the missing id instead of pointing at a giant string.
NEEDED_IDS = (
    "presenceCard", "presenceBadge", "presenceToggle", "presenceToggleLabel",
    "presenceMood", "presenceMoodName", "presenceActivity",
    "presenceActivityLabel", "presenceBlurb", "presenceFound",
    "presenceFoundList", "presenceForget",
    "guideVeil", "guideTitle", "guideText", "guideExtra", "guideStep",
    "guideBack", "guideNext", "guideSkip", "guideDots", "btnGuide",
    "btnMicIco",
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


class MarkupTest(unittest.TestCase):
    """The elements the new code addresses must be in the markup."""

    def setUp(self):
        self.html = _read(HTML)

    def test_every_new_element_exists(self):
        missing = [i for i in NEEDED_IDS if ('id="%s"' % i) not in self.html]
        self.assertEqual(missing, [], "markup is missing: " + ", ".join(missing))

    def test_the_hand_over_switch_is_a_button_with_a_pressed_state(self):
        card = self.html[self.html.find('id="presenceCard"'):]
        card = card[:card.find("status-grid")]
        self.assertIn('class="presence-toggle"', card)
        self.assertIn('aria-pressed="false"', card)
        self.assertIn("AUTONOMOUS PC MODE", card.upper())

    def test_the_microphone_button_has_its_own_icon_slot(self):
        """app.js used to set textContent on the whole button, which would wipe
        any ring element inside it — so the icon needs its own node."""
        self.assertIn('class="dock-btn dock-mic"', self.html)
        self.assertIn('class="dock-ring"', self.html)
        self.assertIn('data-mic="idle"', self.html)

    def test_the_tour_is_an_accessible_dialog(self):
        self.assertIn('role="dialog"', self.html)
        self.assertIn('aria-modal="true"', self.html)


class ScriptTest(unittest.TestCase):
    """Behaviour that has to stay true in the source, not just right now."""

    def setUp(self):
        self.js = _read(JS_APP)

    def test_muting_no_longer_paints_the_button_red(self):
        """The complaint was literal: a red microphone icon on mute read as a
        fault. The state is a data attribute now, and the stylesheet owns it."""
        self.assertNotIn('$("btnMic").style.color', self.js)
        self.assertIn('mb.dataset.mic = m ? "muted" : "live"', self.js)

    def test_the_card_reads_the_initiative_block_from_the_backend(self):
        self.assertIn("st.initiative", self.js)
        self.assertIn("c.initiative", self.js)
        self.assertIn("paintPresence()", self.js)

    def test_the_toggle_and_the_rail_share_one_switch(self):
        """Handing over from the card and from the rail must be the same lever,
        or the two controls can disagree about who has the PC."""
        card = self.js[self.js.find('$("presenceToggle").addEventListener'):]
        self.assertIn('save("autonomous"', card[:600])

    def test_the_tour_saves_that_it_was_seen(self):
        self.assertIn('save_setting("guide_seen", true)', self.js)

    def test_forgetting_is_reachable_from_the_card(self):
        self.assertIn("api.initiative_forget()", self.js)

    def test_the_dock_ring_is_driven_by_real_audio_levels(self):
        self.assertIn("micGlow(a && a.level)", self.js)

    def test_the_demo_backend_can_actually_show_a_muted_microphone(self):
        """The mock returned `{muted: false}` unconditionally, so the one state
        this control is judged on could never be seen in the demo."""
        mock_js = _read(JS_MOCK)
        self.assertNotIn("toggle_mute() { return { muted: false } }", mock_js)
        self.assertIn("async toggle_mute()", mock_js)

    def test_the_demo_backend_serves_the_initiative_block(self):
        mock_js = _read(JS_MOCK)
        self.assertIn("initiative: autoSnapshot()", mock_js)
        self.assertIn("async initiative_forget()", mock_js)


class StyleTest(unittest.TestCase):
    """Every mood the engine can hold must have a colour, and the new states
    must respect the reduced-motion switch."""

    def setUp(self):
        self.css = _read(CSS)

    def test_every_mood_has_a_tone_rule(self):
        missing = [m for m in initiative.MOODS
                   if ('.presence-card[data-mood="%s"]' % m) not in self.css]
        self.assertEqual(missing, [], "no tone rule for: " + ", ".join(missing))

    def test_the_tone_token_is_the_mood_name_lowercased(self):
        """The stylesheet keys off `data-mood`, which the UI fills from the
        event's *name*; the tone travels with the payload as the same word in
        lower case. Two spellings of one mood is how a palette drifts."""
        for mood in initiative.MOODS:
            self.assertEqual(str(initiative.MOOD_INFO[mood]["tone"]), mood.lower(), mood)

    def test_each_tone_rule_declares_a_variable(self):
        """Whitespace-insensitively: the rules are column-aligned in the file,
        and a test that breaks when someone re-aligns a block teaches people to
        stop aligning blocks."""
        flat = " ".join(self.css.split())
        for mood in initiative.MOODS:
            rule = (' .presence-card[data-mood="%s"] { --mood: ' % mood)
            self.assertIn(" ".join(rule.split()), flat, mood)

    def test_muted_is_calm_and_only_real_failure_is_red(self):
        self.assertIn('.dock-mic[data-mic="muted"] { color: var(--amber); }', self.css)
        self.assertIn('.mic-result[data-tone="bad"]', self.css)
        self.assertIn('.mic-result[data-tone="ok"]', self.css)

    def test_the_new_animation_is_switchable_off(self):
        tail = self.css[self.css.find("LAYER 3"):]
        reduced = tail.find("prefers-reduced-motion")
        self.assertGreater(reduced, -1, "the new layer ignores reduced motion")
        window = tail[reduced:reduced + 700]
        for selector in (".pm-dot", ".pa-dots b", ".guide-card"):
            self.assertIn(selector, window, selector + " still animates")

    def test_the_new_layer_ships_after_the_older_rules(self):
        """It relies on cascade order to win; before the old rules it is dead
        code that looks alive."""
        self.assertGreater(self.css.find("LAYER 3"), self.css.find(".mic-diag-fix"))


class BundleTest(unittest.TestCase):
    """The shipped single-file preview must contain all of the above."""

    def setUp(self):
        self.bundle = _read(BUNDLE)
        if not self.bundle:
            self.skipTest("preview_bundle.html not built in this checkout")

    def test_the_bundle_has_the_markup(self):
        missing = [i for i in NEEDED_IDS if ('id="%s"' % i) not in self.bundle]
        self.assertEqual(missing, [], "bundle is stale, missing: " + ", ".join(missing))

    def test_the_bundle_has_the_styles(self):
        self.assertIn("LAYER 3", self.bundle)
        self.assertIn('.presence-card[data-mood="BORED"]', self.bundle)
        self.assertIn('.dock-mic[data-mic="muted"]', self.bundle)

    def test_the_bundle_has_the_scripts(self):
        self.assertIn("initiative_forget", self.bundle)
        self.assertIn("guide_seen", self.bundle)


class BridgeTest(unittest.TestCase):
    """The API the interface calls, with the config mocked out."""

    def test_forget_is_exposed_and_pushes_a_snapshot(self):
        api = webui.JarvisAPI()
        with mock.patch.object(initiative, "forget") as forget, \
             mock.patch.object(webui._PUMP, "push") as push:
            out = api.initiative_forget()
        self.assertTrue(out["ok"])
        forget.assert_called_once()
        self.assertTrue(push.called, "the HUD was never told")

    def test_forget_survives_an_engine_that_raises(self):
        api = webui.JarvisAPI()
        with mock.patch.object(initiative, "forget", side_effect=RuntimeError("boom")):
            self.assertTrue(api.initiative_forget()["ok"])

    def test_guide_seen_is_written_through_the_settings_bridge(self):
        api = webui.JarvisAPI()
        with mock.patch.object(webui, "_write_config_key") as write:
            self.assertTrue(api.save_setting("guide_seen", True)["ok"])
        write.assert_called_once_with("guide_seen", True)

    def test_initial_state_reports_whether_the_tour_was_seen(self):
        api = webui.JarvisAPI()
        with mock.patch.object(webui, "_read_full_config", return_value={"guide_seen": True}):
            self.assertIs(api.get_initial()["guide_seen"], True)
        with mock.patch.object(webui, "_read_full_config", return_value={}):
            self.assertIs(api.get_initial()["guide_seen"], False)

    def test_pc_status_carries_the_autonomous_state(self):
        api = webui.JarvisAPI()
        fake = {"mood": {"name": "BORED", "label": "Bored"}, "activity": None,
                "discoveries": [], "discovery_count": 0, "stats": {}}
        with mock.patch.object(initiative, "status", return_value=fake):
            st = api.pc_status()
        self.assertEqual(st["initiative"]["mood"]["name"], "BORED")
        self.assertIn("modes", st)

    def test_the_lever_the_ui_writes_is_the_one_the_engine_reads(self):
        """The card, the rail and the idle loop must be describing one switch.
        If the UI wrote one config key and core/autonomy.py read another, the
        interface would claim the PC was handed over while the engine stayed
        convinced it had no permission — invisibly, in both directions."""
        api = webui.JarvisAPI()
        with mock.patch.object(webui, "save_autonomous_mode") as save:
            self.assertTrue(api.save_setting("autonomous", True)["ok"])
        save.assert_called_once_with(True)
        # ...and that function writes the key the policy table declares.
        from memory import config_manager
        with mock.patch.object(config_manager, "_save_flag") as flag:
            config_manager.save_autonomous_mode(True)
        self.assertEqual(flag.call_args[0][0], autonomy.MODES["autonomous"][0])


if __name__ == "__main__":
    unittest.main()
