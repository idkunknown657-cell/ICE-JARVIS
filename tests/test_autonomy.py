"""
Tests for core/autonomy.py and the pc_agent intent router.

These two modules carry the behaviour the brief is actually about: what JARVIS
may do without asking, when it must ask, and whether an ordinary sentence like
"move the arrow to Settings" becomes a real mouse action without a model round
trip. Everything here is pure — no mode is persisted, no pointer moves, no
config file is touched.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import autonomy as A
from actions import pc_agent as P


class ClassifyTest(unittest.TestCase):
    """Ordinary computer use is free. Money, deletion, power, security and
    installs always ask. Outward messages are free when the user asked for them
    and never when the autonomous loop invented them."""

    FREE = (
        "open YouTube and play music",
        "search the web for a good synthwave mix",
        "move the arrow to Settings",
        "click the Save button",
        "type hello into the search box",
        "scroll down a bit",
        "switch to the next tab",
        "play the next track",
        "take a screenshot",
        "organise the files on my desktop",
        "read me this page",
        "check out this video it is funny",
        "supply and demand in the market",
    )
    ALWAYS = (
        "buy the game",
        "place the order",
        "pay the electricity bill",
        "checkout",
        "delete everything in the downloads folder",
        "empty the recycle bin",
        "format the drive",
        "shut down the pc",
        "restart the machine",
        "sign out of my account",
        "type my password into the login box",
        "read me the 2FA code",
        "turn off the antivirus",
        "install this installer",
        "run the downloaded exe",
    )

    def test_ordinary_things_are_free(self):
        for text in self.FREE:
            with self.subTest(text=text):
                self.assertTrue(A.classify(text, asked_by_user=True).free, text)

    def test_harmless_actions_are_free_even_without_being_asked(self):
        # Autonomous mode must be able to do these; asking would defeat it.
        for text in ("open Spotify", "search for lofi", "scroll down"):
            with self.subTest(text=text):
                self.assertTrue(A.classify(text, asked_by_user=False).free, text)

    def test_dangerous_things_always_ask(self):
        for text in self.ALWAYS:
            with self.subTest(text=text):
                p = A.classify(text, asked_by_user=True)
                self.assertEqual(p.tier, A.ALWAYS_ASK, f"{text} -> {p.tier}")
                self.assertTrue(p.needs_confirmation)

    def test_messaging_is_gated_only_when_self_initiated(self):
        asked = A.classify("send this message on Discord", asked_by_user=True)
        unasked = A.classify("send this message on Discord", asked_by_user=False)
        self.assertTrue(asked.free, "the user asked for it — send it")
        self.assertEqual(unasked.tier, A.ASK_IF_UNASKED)

    def test_ambiguous_wordings_are_not_misread(self):
        self.assertTrue(A.classify("check out this video").free)
        self.assertTrue(A.classify("in order to continue, open chrome").free)

    def test_empty_intent_is_free_and_total(self):
        for value in ("", None, "   ", "!!!"):
            self.assertTrue(A.classify(value).free)

    def test_gate_reason_is_empty_exactly_when_allowed(self):
        self.assertEqual(A.gate_reason("open chrome", asked_by_user=True), "")
        self.assertIn("money", A.gate_reason("buy it", asked_by_user=True))

    def test_boundary_summary_names_the_gated_categories(self):
        summary = A.boundary_summary()
        for word in ("money", "destructive", "power", "security", "install"):
            self.assertIn(word, summary)


class ModeTest(unittest.TestCase):
    """The levers are read and written through config, and switching control off
    is a brake: it cancels whatever step is running."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._cfg = Path(self._tmp.name) / "api_keys.json"
        self._cfg.write_text(json.dumps({"gemini_api_key": "x" * 30}),
                             encoding="utf-8")
        from memory import config_manager as cm
        self._patches = [
            mock.patch.object(cm, "CONFIG_FILE", self._cfg),
            mock.patch.object(cm, "CONFIG_DIR", Path(self._tmp.name)),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self._tmp.cleanup()

    def test_defaults_are_hands_on_but_not_autonomous(self):
        self.assertTrue(A.get_mode("pc_control"))
        self.assertFalse(A.get_mode("autonomous"),
                         "standing permission must be opted into")
        self.assertTrue(A.get_mode("discord"))

    def test_set_and_read_back(self):
        self.assertTrue(A.set_mode("autonomous", True))
        self.assertTrue(A.get_mode("autonomous"))
        self.assertFalse(A.set_mode("autonomous", False))
        self.assertFalse(A.get_mode("autonomous"))

    def test_unknown_mode_is_refused_not_guessed(self):
        self.assertFalse(A.set_mode("nonsense", True))
        self.assertFalse(A.get_mode("nonsense"))

    def test_turning_control_off_cancels_the_running_step(self):
        from core import cancel
        cancel.clear()
        A.set_mode("autonomous", True)
        A.set_mode("autonomous", False)
        self.assertTrue(cancel.stopped(),
                        "off must stop the current action, not the next one")
        cancel.clear()

    def test_can_act_reports_why_not(self):
        A.set_mode("pc_control", False)
        allowed, why = A.can_act()
        self.assertFalse(allowed)
        self.assertIn("switched off", why)
        A.set_mode("pc_control", True)
        allowed, why = A.can_act(autonomous_only=True)
        self.assertFalse(allowed)
        self.assertIn("Autonomous", why)

    def test_spoken_handover_phrases(self):
        self.assertEqual(A.spoken_mode_change("JARVIS, take over my PC"),
                         ("autonomous", True))
        self.assertEqual(A.spoken_mode_change("do whatever you want"), ("autonomous", True))
        self.assertEqual(A.spoken_mode_change("ok give me back control"),
                         ("autonomous", False))
        self.assertIsNone(A.spoken_mode_change("open youtube"))

    def test_feed_is_bounded_and_clearable(self):
        A.feed_clear()
        for i in range(A._FEED_CAP + 40):
            A.feed("info", f"entry {i}")
        rows = A.feed_recent(500)
        self.assertLessEqual(len(rows), A._FEED_CAP)
        self.assertEqual(rows[-1]["text"], f"entry {A._FEED_CAP + 39}")
        A.feed_clear()
        self.assertEqual(A.feed_recent(), [])

    def test_feed_never_raises_on_a_broken_sink(self):
        A.feed_clear()
        A.bind_feed(lambda entry: (_ for _ in ()).throw(RuntimeError("boom")))
        A.feed("ok", "still recorded")
        self.assertEqual(A.feed_recent(1)[0]["text"], "still recorded")
        A._FEED_CB.clear()


class IdleGovernorTest(unittest.TestCase):
    """Autonomy must be quiet by default and never spam: it is event-driven, it
    respects the user's activity, and it respects its own cooldown."""

    def setUp(self):
        self.g = A.IdleGovernor(grace=120, cooldown=240, busy_suppress=45)
        self._patches = [
            mock.patch.object(A, "get_mode", side_effect=lambda n: (
                True if n in ("pc_control", "autonomous") else False)),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()

    def test_not_due_while_the_user_is_active(self):
        self.assertFalse(self.g.due(idle=5, now=1000))
        self.assertFalse(self.g.due(idle=100, now=1000))

    def test_due_after_the_grace_period(self):
        self.assertTrue(self.g.due(idle=200, now=1000))

    def test_cooldown_is_respected(self):
        self.g.mark("first", now=1000)
        self.assertFalse(self.g.due(idle=400, now=1100))
        self.assertTrue(self.g.due(idle=400, now=1300))

    def test_off_when_autonomy_is_off(self):
        with mock.patch.object(A, "get_mode", return_value=False):
            self.assertFalse(self.g.due(idle=9999, now=9999))

    def test_agenda_states_the_priority_ladder_and_the_hard_limits(self):
        text = A.idle_agenda(activity="CODING in vscode", idle_seconds=600)
        low = text.lower()
        self.assertIn("[AUTONOMOUS]", text)
        self.assertIn("CODING in vscode", text)
        for must in ("never, without being asked", "do nothing at all",
                     "do not ask permission", "send a message",
                     "restart or shut down"):
            self.assertIn(must.lower(), low)

    def test_agenda_without_autonomy_forbids_acting(self):
        text = A.idle_agenda(autonomous=False)
        self.assertIn("must not act on the machine", text)


class RouterTest(unittest.TestCase):
    """One ordinary sentence must become one concrete primitive, with no model
    call — this is where the latency win comes from."""

    def test_pointer_moves(self):
        self.assertEqual(P.plan_intent("Move the arrow to Settings."),
                         {"action": "point", "target": "settings", "vague": False})
        self.assertEqual(P.plan_intent("move the mouse to the search box")["target"],
                         "search box")
        self.assertEqual(P.plan_intent("Hover over the video.")["target"], "video")

    def test_clicks(self):
        plan = P.plan_intent("Click the Save button.")
        self.assertEqual(plan["action"], "click")
        self.assertEqual(plan["target"], "save button")
        self.assertFalse(plan["vague"])
        self.assertEqual(P.plan_intent("double click the file")["clicks"], 2)
        self.assertEqual(P.plan_intent("right click the desktop")["button"], "right")

    def test_vague_clicks_are_marked_not_guessed(self):
        self.assertTrue(P.plan_intent("Click that button.")["vague"])
        self.assertTrue(P.plan_intent("click the first one")["vague"])

    def test_typing_extracts_text_and_field(self):
        plan = P.plan_intent("type hello into the search box")
        self.assertEqual((plan["action"], plan["text"], plan["field"]),
                         ("type", "hello", "search box"))
        quoted = P.plan_intent('type "lofi beats" in the search bar')
        self.assertEqual(quoted["text"], "lofi beats")
        self.assertEqual(quoted["field"], "search bar")

    def test_key_chords(self):
        self.assertEqual(P.plan_intent("press ctrl+s")["keys"], ["ctrl", "s"])
        self.assertEqual(P.plan_intent("press enter")["keys"], ["enter"])

    def test_scrolling(self):
        self.assertEqual(P.plan_intent("scroll down")["direction"], "down")
        self.assertEqual(P.plan_intent("scroll up 5")["amount"], 5)

    def test_opening_and_focusing(self):
        self.assertEqual(P.plan_intent("open YouTube")["action"], "open")
        self.assertEqual(P.plan_intent("focus the Discord window")["target"], "discord")

    def test_searching(self):
        site = P.plan_intent("search youtube for lofi beats")
        self.assertEqual(site["site"], "youtube")
        self.assertEqual(site["query"], "lofi beats")
        self.assertEqual(P.plan_intent("search minecraft")["query"], "minecraft")

    def test_search_urls(self):
        self.assertIn("youtube.com/results", P._search_url("youtube", "lofi beats"))
        self.assertIn("google.com/search", P._search_url("", "cats"))
        self.assertIn("lofi+beats", P._search_url("youtube", "lofi beats"))

    def test_sentences_that_need_real_reasoning_return_none(self):
        self.assertIsNone(P.plan_intent("that's a nice one"))
        self.assertIsNone(P.plan_intent(""))

    def test_fillers_do_not_confuse_it(self):
        plan = P.plan_intent("can you please click Save for me")
        self.assertEqual(plan["action"], "click")
        self.assertEqual(plan["target"], "save")


class SplitIntentsTest(unittest.TestCase):
    """"Open YouTube and play music" is one instruction with two actions; it must
    run as one call, not two round trips."""

    def test_compound_instruction_splits_at_a_verb(self):
        self.assertEqual(P.split_intents("open YouTube and play music"),
                         ["open YouTube", "play music"])
        self.assertEqual(P.split_intents("search minecraft, then click the first one"),
                         ["search minecraft", "click the first one"])

    def test_single_instruction_stays_whole(self):
        for text in ("open the search box", "move the mouse to the search box",
                     "type hello into the box", "the first one"):
            self.assertEqual(P.split_intents(text), [text], text)

    def test_word_within_a_target_is_not_a_joiner(self):
        self.assertEqual(P.split_intents("open the file and folder settings"),
                         ["open the file and folder settings"])


class KeyParseTest(unittest.TestCase):
    def test_forms(self):
        self.assertEqual(P.parse_keys("ctrl+shift+s"), ["ctrl", "shift", "s"])
        self.assertEqual(P.parse_keys("ctrl and s"), ["ctrl", "s"])
        self.assertEqual(P.parse_keys("enter"), ["enter"])
        self.assertEqual(P.parse_keys("f5"), ["f5"])

    def test_prose_is_not_a_chord(self):
        self.assertEqual(P.parse_keys("the save button"), [])


class ExpectedWindowTest(unittest.TestCase):
    def test_known_apps(self):
        self.assertEqual(P.expected_window("YouTube"), "YouTube")
        self.assertEqual(P.expected_window("discord"), "Discord")

    def test_urls(self):
        self.assertEqual(P.expected_window("https://www.youtube.com/watch?v=x"),
                         "YouTube")

    def test_unknown(self):
        self.assertEqual(P.expected_window("some-obscure-thing"), "")


class VagueResolveTest(unittest.TestCase):
    """A pronoun-ish target resolves from context or from a unique control — and
    otherwise refuses to act rather than clicking something arbitrary."""

    def test_deictic_without_context_is_unresolvable(self):
        with mock.patch.object(P.engine, "last_target", return_value=None):
            self.assertEqual(P.resolve_vague("it"), "")
            self.assertEqual(P.resolve_vague("that thing"), "")

    def test_deictic_with_context_points_at_it(self):
        with mock.patch.object(P.engine, "last_target",
                               return_value=P.engine.Target("Save", 1, 2)):
            self.assertEqual(P.resolve_vague("there"), "there")

    def test_ordinal_becomes_a_visual_description(self):
        self.assertIn("first", P.resolve_vague("the first one"))
        self.assertIn("result", P.resolve_vague("the first result"))

    def test_a_lone_widget_noun_is_named_exactly_when_unique(self):
        els = [{"type": "Button", "name": "Continue", "x": 0, "y": 0, "w": 9, "h": 9},
               {"type": "Edit", "name": "Name", "x": 0, "y": 9, "w": 9, "h": 9}]
        with mock.patch.object(P.engine, "uia_elements", return_value=els):
            self.assertEqual(P.resolve_vague("that button"), "Continue")

    def test_an_ambiguous_widget_noun_is_refused(self):
        els = [{"type": "Button", "name": "A", "x": 0, "y": 0, "w": 9, "h": 9},
               {"type": "Button", "name": "B", "x": 0, "y": 9, "w": 9, "h": 9}]
        with mock.patch.object(P.engine, "uia_elements", return_value=els):
            self.assertEqual(P.resolve_vague("that button"), "")


class GateTest(unittest.TestCase):
    def test_dangerous_plans_are_gated(self):
        self.assertTrue(P._gated({"action": "click", "target": "buy now"}))
        self.assertFalse(P._gated({"action": "click", "target": "Save"}))


class ToolContractTest(unittest.TestCase):
    def test_tool_declaration_is_well_formed(self):
        self.assertEqual(P.TOOL["name"], "pc_agent")
        self.assertTrue(P.TOOL["description"])
        self.assertEqual(P.TOOL["parameters"]["type"], "OBJECT")
        for node in P.TOOL["parameters"]["properties"].values():
            if node.get("type") == "ARRAY":
                self.assertIn("items", node)
        self.assertEqual(P.TOOL["parameters"]["required"], ["action"])

    def test_unknown_action_explains_itself(self):
        out = P.pc_agent({"action": "frobnicate"})
        self.assertIn("pc_agent can:", out)

    def test_do_without_an_intent_asks_for_one(self):
        out = P.pc_agent({"action": "do"})
        self.assertIn("needs an 'intent'", out)

    def test_mode_report_lists_the_levers(self):
        out = P.pc_agent({"action": "mode"})
        self.assertIn("pc_control=", out)


if __name__ == "__main__":
    unittest.main()
