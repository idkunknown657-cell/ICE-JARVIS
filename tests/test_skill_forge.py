"""Unit tests for core/skill_forge.py and actions/skill_forge.py.

The model is always stubbed — these tests are about the pipeline around it, which
is the part that has to be right: what gets written, what is allowed to reach
plugins/, what happens when the model is wrong twice in a row, and what a name
collision does to a plugin that already works. Nothing here touches the real
plugins/ folder, the real config, or the network.
"""
import json
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from actions import skill_forge as tool
from core import skill_forge as forge
from core import skill_registry as sr


def reply(name="word_count", description="Counts the words in a sentence.",
          code=None, parameters=None, test_cases=None, **extra):
    """A model answer in the shape the forge expects."""
    params = parameters if parameters is not None else {
        "type": "OBJECT",
        "properties": {"text": {"type": "STRING", "description": "the text"}},
        "required": [],
    }
    body = code if code is not None else textwrap.dedent('''
        PLUGIN = {
            "name": "%s",
            "description": "%s",
            "parameters": %s,
        }

        def run(parameters: dict, player=None, session_memory=None) -> str:
            text = (parameters or {}).get("text") or "one two three"
            return f"That is {len(str(text).split())} words."
    ''' % (name, description, json.dumps(params))).strip() + "\n"
    out = {"name": name, "description": description, "parameters": params,
           "code": body, "test_cases": test_cases or [{}]}
    out.update(extra)
    return out


class Base(unittest.TestCase):
    """A throwaway installation: its own plugins/, config/ and skills/."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.plugins = self.root / "plugins"
        self.staging = self.root / "config" / "forge_staging"
        for target, attr, value in (
            (forge, "_base_dir", lambda: self.root),
            (sr, "_base_dir", lambda: self.root),
        ):
            p = mock.patch.object(target, attr, value)
            p.start()
            self.addCleanup(p.stop)
        sr.reset_cache()
        self.addCleanup(sr.reset_cache)
        self._print = mock.patch("builtins.print")
        self._print.start()
        self.addCleanup(self._print.stop)
        # A live registry that accepts what it is given. It starts empty, but has()
        # answers yes, because that is what a real loader does for a plugin it just
        # accepted — and the forge checks has() after the rescan to notice a name
        # the loader refused. Tests that need a refusal set it back to False.
        self.registry = mock.Mock()
        self.registry.plugins.return_value = {}
        self.registry.has.return_value = True
        self.registry.reload.return_value = 0
        p = mock.patch("core.plugin_loader.active_registry",
                       return_value=self.registry)
        p.start()
        self.addCleanup(p.stop)

    def answer(self, *replies):
        """Stub the model with a queue of replies."""
        queue = list(replies)
        calls = []

        def fake(prompt, timeout_ms=0):
            calls.append(prompt)
            if not queue:
                return {}
            item = queue.pop(0)
            return item if isinstance(item, dict) else {}

        p = mock.patch.object(forge, "_ask_model", fake)
        p.start()
        self.addCleanup(p.stop)
        return calls

    def config(self, enabled=True, auto=True):
        """Patch the real config module's flags.

        Patching sys.modules does NOT work here: `from memory import
        config_manager` resolves against the already-imported `memory` package
        before it ever consults sys.modules, so a stand-in module is silently
        ignored and the test then passes for the wrong reason.
        """
        from memory import config_manager
        for attr, value in (("get_self_forge_enabled", enabled),
                            ("get_self_forge_auto", auto)):
            p = mock.patch.object(config_manager, attr, return_value=value)
            p.start()
            self.addCleanup(p.stop)

    def live_file(self, name="word_count"):
        return self.plugins / f"{name}.py"


class HappyPathTest(Base):
    def test_a_good_skill_is_written_verified_and_published(self):
        self.config()
        self.answer(reply())
        result = forge.forge("count the words in a sentence")

        self.assertTrue(result["ok"], result.get("message"))
        self.assertEqual(result["name"], "word_count")
        self.assertTrue(result["activated"])
        self.assertTrue(self.live_file().exists())
        self.assertEqual(result["verdict"]["stage"], "complete")

    def test_the_published_file_declares_the_name_it_was_published_under(self):
        """The loader keys off PLUGIN['name'], so a mismatch would leave a tool
        on disk that silently never becomes callable."""
        self.config()
        self.answer(reply())
        forge.forge("count words")
        source = self.live_file().read_text(encoding="utf-8")
        import ast
        meta = None
        for node in ast.parse(source).body:
            if isinstance(node, ast.Assign) and node.targets[0].id == "PLUGIN":
                meta = ast.literal_eval(node.value)
        self.assertIsNotNone(meta)
        self.assertEqual(meta["name"], "word_count")
        self.assertIn("description", meta)

    def test_the_registry_records_what_let_it_through(self):
        self.config()
        self.answer(reply())
        forge.forge("count words")
        entry = sr.get("word_count")
        self.assertEqual(entry["source"], "forged")
        self.assertTrue(entry["active"])
        self.assertEqual(entry["verdict"]["stage"], "complete")
        self.assertIn("count words", " ".join(entry["triggers"]).lower())

    def test_the_skill_lands_in_the_live_plugins_folder_not_staging(self):
        self.config()
        self.answer(reply())
        forge.forge("count words")
        self.assertTrue(self.live_file().exists())
        self.assertFalse((self.staging / "word_count.py").exists())

    def test_a_model_reply_whose_name_differs_is_renamed_not_refused(self):
        """The forge may have to rename to dodge a collision, and the model's own
        copy of the name goes stale the moment it does. Rewriting the declaration
        is what keeps a collision from failing the forge outright."""
        self.config()
        (self.plugins).mkdir(parents=True, exist_ok=True)
        self.live_file().write_text("PLUGIN = {'name': 'word_count'}\n")
        self.answer(reply(name="word_count"))
        result = forge.forge("count words again")
        self.assertTrue(result["ok"], result.get("message"))
        self.assertEqual(result["name"], "word_count_2")
        self.assertTrue(self.live_file("word_count_2").exists())

    def test_extra_keys_the_model_added_to_the_declaration_survive(self):
        self.config()
        code = reply()["code"].replace(
            '"parameters": {', '"triggers": ["count my words"],\n    "parameters": {')
        self.answer(dict(reply(), code=code))
        forge.forge("count words")
        import ast
        source = self.live_file().read_text(encoding="utf-8")
        for node in ast.parse(source).body:
            if isinstance(node, ast.Assign) and node.targets[0].id == "PLUGIN":
                meta = ast.literal_eval(node.value)
                self.assertEqual(meta["triggers"], ["count my words"])

    def test_a_code_reply_with_the_declaration_missing_gets_one_added(self):
        self.config()
        bare = textwrap.dedent('''
            def run(parameters: dict, player=None, session_memory=None) -> str:
                return "Bare handler, no declaration at all."
        ''').strip() + "\n"
        self.answer(dict(reply(), code=bare))
        result = forge.forge("count words")
        self.assertTrue(result["ok"], result.get("message"))
        self.assertIn("PLUGIN = {", self.live_file().read_text(encoding="utf-8"))

    def test_a_fenced_reply_is_unwrapped(self):
        self.config()
        fenced = "```python\n" + reply()["code"] + "```\n"
        self.answer(dict(reply(), code=fenced))
        result = forge.forge("count words")
        self.assertTrue(result["ok"], result.get("message"))
        self.assertNotIn("```", self.live_file().read_text(encoding="utf-8"))

    def test_the_loader_is_asked_to_pick_it_up(self):
        self.config()
        self.registry.reload.return_value = 1
        self.answer(reply())
        result = forge.forge("count words")
        self.assertTrue(self.registry.reload.called)
        self.assertTrue(result["hot"])


class RefusalTest(Base):
    def test_a_destructive_skill_is_refused_and_never_published(self):
        self.config()
        self.answer(dict(reply(name="wiper", description="Wipes things.",
                               code='"""format c: /y"""\n\n'
                                    'def run(parameters, player=None, session_memory=None):\n'
                                    '    return "done"\n')))
        result = forge.forge("wipe my disk clean")
        self.assertFalse(result["ok"])
        self.assertEqual(result["stage"], "safety")
        self.assertFalse(self.live_file("wiper").exists())
        self.assertFalse(sr.is_known("wiper"))

    def test_a_destructive_skill_is_never_even_offered_a_repair_round(self):
        """Safety is not a misunderstanding to iterate on — a repair round would
        just be the model asking to be made sneakier."""
        self.config()
        calls = self.answer(dict(reply(
            code='"""format c: /y"""\n\n'
                 'def run(parameters, player=None, session_memory=None):\n'
                 '    return "done"\n')))
        forge.forge("wipe my disk")
        self.assertEqual(len(calls), 1, "no repair prompt should have been sent")

    def test_an_unfixable_skill_is_staged_and_reported_not_dropped(self):
        # Genuinely unfixable: it raises, and both repair rounds hand back the
        # same raising code. (A candidate that merely lacks its declaration is
        # fixed automatically by _build_source, so it is not a good example.)
        self.config()
        broken = dict(reply(code="""
            PLUGIN = {"name": "word_count", "description": "d",
                      "parameters": {"type": "OBJECT", "properties": {}}}

            def run(parameters, player=None, session_memory=None):
                raise RuntimeError("permanently broken")
        """))
        self.answer(broken, {"code": broken["code"]}, {"code": broken["code"]})
        result = forge.forge("do something impossible")
        self.assertFalse(result["ok"])
        self.assertTrue(result.get("staged_path"))
        self.assertTrue(Path(result["staged_path"]).exists(),
                        "the failed candidate must be kept for inspection")
        self.assertFalse(self.live_file("word_count").exists())

    def test_a_skill_that_raises_is_repaired_then_published(self):
        self.config()
        raising = reply(code=textwrap.dedent('''
            PLUGIN = {"name": "talker", "description": "Talks.",
                      "parameters": {"type": "OBJECT", "properties": {}}}

            def run(parameters, player=None, session_memory=None):
                raise RuntimeError("not implemented yet")
        ''').strip() + "\n")
        fixed = reply(name="talker", description="Talks.",
                      code=textwrap.dedent('''
            PLUGIN = {"name": "talker", "description": "Talks.",
                      "parameters": {"type": "OBJECT", "properties": {}}}

            def run(parameters, player=None, session_memory=None):
                return "Now it works properly."
        ''').strip() + "\n")
        self.answer(raising, fixed)
        result = forge.forge("make yourself able to talk")
        self.assertTrue(result["ok"], result.get("message"))
        self.assertEqual(result["repair_rounds"], 1)

    def test_the_repair_prompt_says_what_actually_went_wrong(self):
        self.config()
        raising = reply(code=textwrap.dedent('''
            PLUGIN = {"name": "talker", "description": "Talks.",
                      "parameters": {"type": "OBJECT", "properties": {}}}

            def run(parameters, player=None, session_memory=None):
                raise RuntimeError("the specific failure")
        ''').strip() + "\n")
        calls = self.answer(raising, {})
        forge.forge("make yourself able to talk")
        self.assertEqual(len(calls), 2)
        self.assertIn("the specific failure", calls[1])

    def test_the_repair_prompt_states_the_required_name(self):
        self.config()
        raising = dict(reply(code=textwrap.dedent('''
            PLUGIN = {"name": "wrong", "description": "d",
                      "parameters": {"type": "OBJECT", "properties": {}}}
            def run(parameters, player=None, session_memory=None):
                raise RuntimeError("boom")
        ''').strip() + "\n"))
        calls = self.answer(raising, raising, raising)
        forge.forge("count words")
        self.assertIn('must be exactly "word_count"', calls[1])

    def test_an_empty_goal_asks_for_one(self):
        self.config()
        self.answer(reply())
        result = forge.forge("   ")
        self.assertFalse(result["ok"])
        self.assertIn("Tell me what", result["message"])

    def test_the_model_returning_nothing_is_reported_clearly(self):
        self.config()
        self.answer({})
        result = forge.forge("do the impossible")
        self.assertFalse(result["ok"])
        self.assertIn("could not write", result["message"])

    def test_a_broken_reply_is_not_mistaken_for_code(self):
        self.config()
        self.answer({"name": "x", "description": "y"})   # no "code" key at all
        result = forge.forge("do something")
        self.assertFalse(result["ok"])
        self.assertFalse(self.live_file("x").exists())

    def test_switching_self_forge_off_refuses_before_calling_the_model(self):
        self.config(enabled=False)
        calls = self.answer(reply())
        result = forge.forge("count words")
        self.assertFalse(result["ok"])
        self.assertEqual(calls, [], "the model must not be called at all")
        self.assertIn("not allowed", result["message"])

    def test_a_collision_never_overwrites_the_working_plugin(self):
        self.config()
        self.plugins.mkdir(parents=True, exist_ok=True)
        original = "PLUGIN = {'name': 'word_count', 'description': 'the real one'}\n"
        self.live_file().write_text(original, encoding="utf-8")
        self.answer(reply())
        result = forge.forge("count words")
        self.assertEqual(result["name"], "word_count_2")
        self.assertEqual(self.live_file().read_text(encoding="utf-8"), original)

    def test_a_collision_with_a_core_tool_name_is_avoided(self):
        """Bundled tools are discovered as actions/*.py, so their names are part
        of what a new skill must not take."""
        self.config()
        (self.root / "actions").mkdir(parents=True, exist_ok=True)
        (self.root / "actions" / "web_search.py").write_text("TOOL = {}\n")
        self.answer(reply(name="web_search", description="Searches the web."))
        result = forge.forge("search the web better")
        self.assertNotEqual(result["name"], "web_search")

    def test_a_name_the_loader_refuses_is_pulled_back_out_of_plugins(self):
        """The inline core tools live in main.py, so their names cannot be seen
        by reading the filesystem. The check is therefore made after the rescan,
        on what the loader actually accepted."""
        self.config()
        self.answer(reply(name="reserved_name"))
        self.registry.reload.return_value = 1
        self.registry.has.return_value = False      # the loader refused it
        result = forge.forge("do a reserved thing")
        self.assertFalse(result["ok"])
        self.assertEqual(result["stage"], "collision")
        self.assertFalse(self.live_file("reserved_name").exists())
        self.assertTrue((self.staging / "reserved_name.py").exists())
        self.assertFalse(sr.is_known("reserved_name"))


class ApprovalTest(Base):
    def test_with_auto_off_the_skill_waits_and_says_where(self):
        self.config(auto=False)
        self.answer(reply())
        result = forge.forge("count words")
        self.assertTrue(result["ok"])
        self.assertFalse(result["activated"])
        self.assertFalse(self.live_file().exists())
        self.assertTrue((self.staging / "word_count.py").exists())
        self.assertIn("go-ahead", result["message"])

    def test_a_held_back_skill_is_not_recorded_as_live(self):
        self.config(auto=False)
        self.answer(reply())
        forge.forge("count words")
        self.assertFalse(sr.is_known("word_count"))


class StagingTest(Base):
    def test_staged_lists_what_failed_newest_first(self):
        self.config()
        bad_code = ("PLUGIN = {'name': 'second', 'description': 'd',\n"
                    "          'parameters': {'type': 'OBJECT', 'properties': {}}}\n"
                    "def run(parameters, player=None, session_memory=None):\n"
                    "    raise RuntimeError('broken')\n")
        self.answer(dict(reply(), code=bad_code), {"code": bad_code},
                    {"code": bad_code})
        forge.forge("first failure")
        items = forge.staged()
        self.assertTrue(items)
        self.assertTrue(all(item["size"] > 0 for item in items))

    def test_staged_is_empty_when_nothing_failed(self):
        self.assertEqual(forge.staged(), [])

    def test_clearing_one_or_all(self):
        self.staging.mkdir(parents=True, exist_ok=True)
        (self.staging / "one.py").write_text("x = 1\n")
        (self.staging / "two.py").write_text("x = 2\n")
        self.assertEqual(forge.forget_staged("one"), 1)
        self.assertEqual([p.name for p in self.staging.glob("*.py")], ["two.py"])
        self.assertEqual(forge.forget_staged(), 1)

    def test_clearing_nothing_is_harmless(self):
        self.assertEqual(forge.forget_staged(), 0)
        self.assertEqual(forge.forget_staged("never_existed"), 0)


class NamingTest(Base):
    def test_clean_name_removes_anything_unusable(self):
        self.assertEqual(forge._clean_name("Hello World!"), "hello_world")
        self.assertEqual(forge._clean_name("  spaced  out  "), "spaced_out")
        self.assertEqual(forge._clean_name("1st_attempt"), "skill_1st_attempt")
        self.assertEqual(forge._clean_name(""), "")
        self.assertEqual(forge._clean_name(None), "")
        self.assertEqual(forge._clean_name("a" * 200), "a" * 64)

    def test_a_name_derived_from_the_goal_drops_the_filler(self):
        self.assertEqual(
            forge._name_from_goal("learn how to check my internet speed"),
            "check_internet_speed")
        self.assertEqual(forge._name_from_goal(""), "")

    def test_unique_name_appends_a_number_only_when_needed(self):
        self.assertEqual(forge._unique_name("fresh_name", "goal"), "fresh_name")
        self.plugins.mkdir(parents=True, exist_ok=True)
        (self.plugins / "taken.py").write_text("PLUGIN = {}\n")
        self.assertEqual(forge._unique_name("taken", "goal"), "taken_2")

    def test_unique_name_falls_back_to_the_goal_when_none_was_given(self):
        self.assertEqual(forge._unique_name("", "learn how to check internet speed"),
                         "check_internet_speed")

    def test_unique_name_never_returns_empty(self):
        self.assertTrue(forge._unique_name("", ""))


class ParameterSchemaTest(Base):
    def test_a_malformed_schema_becomes_an_empty_one(self):
        for junk in (None, "OBJECT", {"type": "STRING"}, [], 42):
            self.assertEqual(forge._sanitize_parameters(junk),
                             {"type": "OBJECT", "properties": {}})

    def test_unknown_parameter_types_become_strings(self):
        out = forge._sanitize_parameters({"type": "OBJECT", "properties": {
            "when": {"type": "DATETIME", "description": "d"}}})
        self.assertEqual(out["properties"]["when"]["type"], "STRING")

    def test_an_array_parameter_gets_an_item_type(self):
        out = forge._sanitize_parameters({"type": "OBJECT", "properties": {
            "items": {"type": "ARRAY"}}})
        self.assertEqual(out["properties"]["items"]["items"], {"type": "STRING"})

    def test_required_only_keeps_parameters_that_exist(self):
        out = forge._sanitize_parameters({
            "type": "OBJECT",
            "properties": {"real": {"type": "STRING"}},
            "required": ["real", "imaginary"]})
        self.assertEqual(out["required"], ["real"])

    def test_non_dict_properties_are_dropped(self):
        out = forge._sanitize_parameters({"type": "OBJECT",
                                          "properties": {"good": {"type": "STRING"},
                                                         "bad": "just a string"}})
        self.assertEqual(list(out["properties"]), ["good"])

    def test_a_very_long_description_is_truncated(self):
        out = forge._sanitize_parameters({"type": "OBJECT", "properties": {
            "x": {"type": "STRING", "description": "d" * 900}}})
        self.assertLessEqual(len(out["properties"]["x"]["description"]), 300)

    def test_an_empty_call_is_always_the_first_test_case(self):
        cases = forge._test_cases([], {})
        self.assertIn({}, cases)
        self.assertEqual(forge._test_cases(None, {})[0], {})

    def test_supplied_cases_come_first_and_are_capped(self):
        cases = forge._test_cases([{"a": 1}, {"b": 2}, {"c": 3}], {})
        self.assertEqual(cases[0], {"a": 1})
        self.assertLessEqual(len(cases), 3)


class InventoryTest(Base):
    def test_inventory_says_so_when_there_is_nothing_yet(self):
        text = forge.inventory()
        self.assertIn("not taught myself anything", text)

    def test_inventory_lists_forged_skills_and_packages(self):
        sr.record("speed_test", description="Measures the line.")
        (self.root / "skills" / "iss" ).mkdir(parents=True)
        (self.root / "skills" / "iss" / "manifest.json").write_text(json.dumps(
            {"name": "iss", "description": "Tracks the station."}))
        (self.root / "skills" / "iss" / "skill.py").write_text(
            "def execute(**kwargs):\n    return 'ok'\n")
        text = forge.inventory()
        self.assertIn("speed_test", text)
        self.assertIn("iss", text)

    def test_describe_skill_handles_all_three_states(self):
        sr.record("known", description="Something.")
        self.assertIn("Something", forge.describe_skill("known"))
        self.assertIn("not found", forge.describe_skill("nobody"))
        (self.root / "skills" / "pkg").mkdir(parents=True)
        (self.root / "skills" / "pkg" / "manifest.json").write_text(
            json.dumps({"name": "pkg", "description": "A package."}))
        self.assertIn("could not load", forge.describe_skill("pkg"))


class ToolTest(Base):
    def test_an_unknown_action_explains_the_options(self):
        out = tool.skill_forge({"action": "polish"}, player=None)
        self.assertIn("do not know", out)
        self.assertIn("forge", out)

    def test_forge_without_a_goal_asks_for_one(self):
        out = tool.skill_forge({"action": "forge"}, player=None)
        self.assertIn("Tell me what", out)

    def test_status_reports_the_switches(self):
        sr.record("mine", description="Mine.")
        out = tool.skill_forge({"action": "status"}, player=None)
        self.assertIn("Writing new skills", out)
        self.assertIn("Skills I wrote: 1", out)

    def test_list_falls_back_to_a_readable_sentence(self):
        out = tool.skill_forge({"action": "list"}, player=None)
        self.assertTrue(out.strip())

    def test_staged_reports_emptiness_clearly(self):
        self.assertIn("Nothing is waiting", tool.skill_forge({"action": "staged"}))

    def test_clear_staged_reports_the_count(self):
        self.staging.mkdir(parents=True, exist_ok=True)
        (self.staging / "x.py").write_text("x = 1\n")
        self.assertIn("Deleted 1 staged", tool.skill_forge({"action": "clear_staged"}))

    def test_enable_and_disable_need_a_name(self):
        self.assertIn("Which skill", tool.skill_forge({"action": "disable"}))

    def test_disabling_and_enabling_a_known_skill(self):
        sr.record("toggle_me", description="x")
        self.assertIn("switched off", tool.skill_forge(
            {"action": "disable", "skill_name": "toggle_me"}))
        self.assertIn("switched on", tool.skill_forge(
            {"action": "enable", "skill_name": "toggle_me"}))

    def test_deleting_refuses_a_plugin_it_did_not_write(self):
        sr.record("by_hand", description="x", source="plugin")
        out = tool.skill_forge({"action": "delete", "skill_name": "by_hand"})
        self.assertIn("will not delete", out)

    def test_deleting_a_forged_skill_removes_it(self):
        self.plugins.mkdir(parents=True, exist_ok=True)
        (self.plugins / "mine.py").write_text("PLUGIN = {}\n")
        sr.record("mine", description="x", source="forged",
                  path=str(self.plugins / "mine.py"))
        out = tool.skill_forge({"action": "delete", "skill_name": "mine"})
        self.assertIn("Deleted", out)
        self.assertFalse(sr.is_known("mine"))
        self.assertFalse((self.plugins / "mine.py").exists())

    def test_route_explains_its_decision(self):
        out = tool.skill_forge({"action": "route", "query": "make me a sandwich"})
        self.assertIn("None of my", out)

    def test_route_without_a_sentence_asks_for_one(self):
        self.assertIn("Give me a sentence", tool.skill_forge({"action": "route"}))

    def test_run_without_a_name_or_sentence_asks(self):
        self.assertIn("Which skill", tool.skill_forge({"action": "run"}))

    def test_run_refuses_a_switched_off_skill(self):
        sr.record("sleeping", description="x", active=False)
        out = tool.skill_forge({"action": "run", "skill_name": "sleeping"})
        self.assertIn("switched off", out)

    def test_run_uses_the_router_when_given_a_sentence(self):
        record = mock.Mock()
        record.description = "Measures the internet connection speed."
        record.parameters = {"type": "OBJECT", "properties": {}}
        record.file = "speed_test.py"
        self.registry.plugins.return_value = {"speed_test": record}
        self.registry.has.return_value = True
        self.registry.run.return_value = "42 megabits."
        out = tool.skill_forge({"action": "run", "query": "internet speed test"})
        self.assertIn("42 megabits", out)

    def test_run_reports_a_failing_skill_without_raising(self):
        sr.record("boom", description="Explodes.")
        self.registry.has.return_value = True
        self.registry.run.side_effect = RuntimeError("it exploded")
        out = tool.skill_forge({"action": "run", "skill_name": "boom"})
        self.assertIn("failed", out)
        self.assertIn("it exploded", out)

    def test_the_tool_is_discoverable_by_the_action_loader(self):
        self.assertEqual(tool.TOOL["name"], "skill_forge")
        self.assertTrue(tool.TOOL["description"].strip())
        self.assertTrue(callable(tool.TOOL["handler"]))
        self.assertEqual(tool.TOOL["parameters"]["type"], "OBJECT")

    def test_a_handler_looking_skill_is_recognised_as_an_error(self):
        self.assertTrue(tool._errorish(""))
        self.assertTrue(tool._errorish("Error: it broke"))
        self.assertTrue(tool._errorish("Could not reach the server"))
        self.assertFalse(tool._errorish("You are getting 42 megabits."))

    def test_the_first_string_parameter_is_found_for_a_retry(self):
        self.assertEqual(tool._first_string_param(
            {"type": "OBJECT", "properties": {"q": {"type": "STRING"}}}), "q")
        self.assertEqual(tool._first_string_param(
            {"type": "OBJECT", "properties": {"n": {"type": "INTEGER"}}}), "")
        self.assertEqual(tool._first_string_param({}), "")


class RunNewSkillTest(Base):
    """The payoff: a freshly forged skill answers the request that prompted it."""

    def test_a_working_skill_is_used_immediately(self):
        self.registry.has.return_value = True
        self.registry.run.return_value = "You are getting 94 megabits."
        out = tool._run_new_skill("speed_test", "check my internet speed", None, None)
        self.assertIn("94 megabits", out)

    def test_a_skill_that_could_not_answer_is_retried_with_the_goal(self):
        sr.record("speed_test", description="x", parameters={
            "type": "OBJECT", "properties": {"query": {"type": "STRING"}}})
        self.registry.has.return_value = True
        self.registry.run.side_effect = ["Could not run it.", "Retried fine."]
        out = tool._run_new_skill("speed_test", "check my internet speed", None, None)
        self.assertEqual(out, "Retried fine.")
        self.assertEqual(self.registry.run.call_count, 2)
        args = self.registry.run.call_args[0][1]
        self.assertEqual(args.get("query"), "check my internet speed")

    def test_a_skill_with_no_string_parameter_is_not_retried(self):
        sr.record("no_args", description="x", parameters={
            "type": "OBJECT", "properties": {"n": {"type": "INTEGER"}}})
        self.registry.has.return_value = True
        self.registry.run.return_value = "Could not do it."
        tool._run_new_skill("no_args", "do the thing", None, None)
        self.assertEqual(self.registry.run.call_count, 1)

    def test_no_registry_means_no_attempt(self):
        with mock.patch("core.plugin_loader.active_registry", return_value=None):
            self.assertEqual(tool._run_new_skill("x", "y", None, None), "")

    def test_a_skill_the_registry_does_not_have_is_not_run(self):
        self.registry.has.return_value = False
        self.assertEqual(tool._run_new_skill("missing", "y", None, None), "")
        self.assertFalse(self.registry.run.called)


if __name__ == "__main__":
    unittest.main()
