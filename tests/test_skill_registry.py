"""Unit tests for core/skill_registry.py — manifests, the vault, and the router.

Everything here runs against a temp installation root: the real config/skills.json
is never written, no real plugin folder is scanned for deletion, and the vault is
a throwaway directory. The router tests are the interesting ones — they pin down
what it must NOT match, because a router that guesses would run the wrong tool
without the model getting a chance to notice.
"""
import json
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import skill_registry as sr


def write_plugin(root: Path, name: str, description: str = "Does a thing.",
                 parameters: dict | None = None, triggers: list | None = None,
                 aliases: list | None = None) -> Path:
    """A real plugin file in the temp installation's plugins/ folder."""
    folder = root / "plugins"
    folder.mkdir(parents=True, exist_ok=True)
    meta = {"name": name, "description": description,
            "parameters": parameters or {"type": "OBJECT", "properties": {}}}
    if triggers:
        meta["triggers"] = triggers
    if aliases:
        meta["aliases"] = aliases
    path = folder / f"{name}.py"
    path.write_text(
        "PLUGIN = " + json.dumps(meta) + "\n\n"
        "def run(parameters, player=None, session_memory=None):\n"
        "    return 'ran " + name + "'\n",
        encoding="utf-8")
    return path


def write_package(root: Path, name: str, description: str = "A package skill.",
                  parameters: dict | None = None, body: str | None = None,
                  manifest: dict | None = None, with_code: bool = True) -> Path:
    """A skills/<name>/ package, the richer layout for a ported skill."""
    folder = root / "skills" / name
    folder.mkdir(parents=True, exist_ok=True)
    data = manifest if manifest is not None else {
        "name": name, "description": description,
        "parameters": parameters or {"type": "OBJECT", "properties": {}},
        "triggers": [], "aliases": [], "version": "1.0.0",
    }
    (folder / "manifest.json").write_text(json.dumps(data), encoding="utf-8")
    if with_code:
        (folder / "skill.py").write_text(textwrap.dedent(body or """
            def execute(**kwargs):
                return {"summary": "package ran", "title": "done"}
        """).strip() + "\n", encoding="utf-8")
    (folder / "test_cases.json").write_text("[]", encoding="utf-8")
    return folder


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        p = mock.patch.object(sr, "_base_dir", lambda: self.root)
        p.start()
        self.addCleanup(p.stop)
        sr.reset_cache()
        self.addCleanup(sr.reset_cache)
        self._print = mock.patch("builtins.print")
        self._print.start()
        self.addCleanup(self._print.stop)


class ManifestTest(Base):
    def test_recording_and_reading_back(self):
        sr.record("speed_test", description="Checks the line speed.",
                  parameters={"type": "OBJECT", "properties": {}},
                  path=str(self.root / "plugins" / "speed_test.py"),
                  source="forged", verdict={"stage": "complete"})
        entry = sr.get("speed_test")
        self.assertEqual(entry["name"], "speed_test")
        self.assertEqual(entry["source"], "forged")
        self.assertTrue(entry["active"])
        self.assertEqual(entry["verdict"]["stage"], "complete")
        self.assertTrue(sr.is_known("speed_test"))
        self.assertIn("speed_test", sr.names())

    def test_an_unknown_skill_reads_back_empty_not_an_error(self):
        self.assertEqual(sr.get("never_heard_of_it"), {})
        self.assertFalse(sr.is_known("never_heard_of_it"))

    def test_a_corrupt_registry_file_is_just_an_empty_book(self):
        (self.root / "config").mkdir(parents=True, exist_ok=True)
        sr.config_path().write_text("{ this is not json", encoding="utf-8")
        self.assertEqual(sr.list_skills(), [])
        sr.record("still_works", description="yes")
        self.assertTrue(sr.is_known("still_works"))

    def test_recording_twice_keeps_one_entry_and_the_first_created_at(self):
        sr.record("dup", description="first")
        first = sr.get("dup")["created_at"]
        sr.record("dup", description="second")
        self.assertEqual(len(sr.list_skills()), 1)
        self.assertEqual(sr.get("dup")["description"], "second")
        self.assertEqual(sr.get("dup")["created_at"], first)

    def test_listing_is_newest_first(self):
        sr.record("older", description="o")
        sr.record("newer", description="n")
        book = sr.list_skills()
        self.assertEqual(book[-1]["name"], "older")

    def test_toggling_off_and_on(self):
        sr.record("toggle_me", description="x")
        self.assertFalse(sr.toggle("toggle_me")["active"])
        self.assertTrue(sr.toggle("toggle_me")["active"])
        self.assertFalse(sr.toggle("toggle_me", False)["active"])
        self.assertEqual(sr.toggle("not_here"), {})

    def test_invocations_and_errors_are_counted(self):
        sr.record("counted", description="x")
        sr.record_invocation("counted")
        sr.record_invocation("counted")
        sr.record_error("counted", "it broke")
        entry = sr.get("counted")
        self.assertEqual(entry["invocations"], 2)
        self.assertEqual(entry["last_error"], "it broke")

    def test_forgetting_removes_the_manifest(self):
        sr.record("gone", description="x")
        self.assertTrue(sr.forget("gone"))
        self.assertFalse(sr.is_known("gone"))
        self.assertFalse(sr.forget("gone"))

    def test_forgetting_deletes_the_code_only_for_forged_skills(self):
        forged = write_plugin(self.root, "written_by_jarvis")
        sr.record("written_by_jarvis", source="forged", path=str(forged))
        human = write_plugin(self.root, "written_by_a_person")
        sr.record("written_by_a_person", source="plugin", path=str(human))

        sr.forget("written_by_jarvis", remove_file=True)
        sr.forget("written_by_a_person", remove_file=True)
        self.assertFalse(forged.exists(), "a forged skill's file should be removed")
        self.assertTrue(human.exists(), "a hand-written plugin must never be deleted")

    def test_forgetting_never_reaches_outside_the_installation(self):
        outside = Path(self._tmp.name).parent / "somewhere_else.py"
        outside.write_text("PLUGIN = {}\n", encoding="utf-8")
        self.addCleanup(lambda: outside.unlink(missing_ok=True))
        sr.record("sneaky", source="forged", path=str(outside))
        sr.forget("sneaky", remove_file=True)
        self.assertTrue(outside.exists(), "deletion is bounded to the install root")

    def test_stats_summarises_the_book(self):
        sr.record("a", description="a")
        sr.record("b", description="b")
        sr.toggle("b", False)
        sr.record_invocation("a")
        stats = sr.stats()
        self.assertEqual(stats["forged"], 2)
        self.assertEqual(stats["active"], 1)
        self.assertEqual(stats["invocations"], 1)
        self.assertEqual(stats["last"]["name"], "b")


class VaultTest(Base):
    def test_a_package_is_discovered(self):
        write_package(self.root, "iss_tracker", description="Tracks the station.")
        found = sr.load_vault(refresh=True)
        self.assertIn("iss_tracker", found)
        self.assertFalse(found["iss_tracker"].error)
        self.assertEqual(found["iss_tracker"].description, "Tracks the station.")

    def test_a_package_without_code_reports_why(self):
        write_package(self.root, "broken_pkg", with_code=False)
        found = sr.load_vault(refresh=True)
        self.assertIn("broken_pkg", found)
        self.assertIn("skill.py", found["broken_pkg"].error)
        self.assertEqual(sr.active_vault_skills(), [])

    def test_a_package_without_a_manifest_reports_why(self):
        folder = self.root / "skills" / "no_manifest"
        folder.mkdir(parents=True)
        (folder / "skill.py").write_text("def execute(**k): return 'x'\n")
        found = sr.load_vault(refresh=True)
        self.assertIn("manifest.json", found["no_manifest"].error)

    def test_an_empty_vault_is_empty_not_an_error(self):
        self.assertEqual(sr.load_vault(refresh=True), {})
        self.assertEqual(sr.vault_declarations(), [])

    def test_only_readable_packages_are_declared(self):
        write_package(self.root, "good_pkg", description="Works.",
                      parameters={"type": "OBJECT",
                                  "properties": {"q": {"type": "STRING"}}})
        write_package(self.root, "bad_pkg", with_code=False)
        decls = sr.vault_declarations()
        self.assertEqual([d["name"] for d in decls], ["good_pkg"])
        self.assertIn("q", decls[0]["parameters"]["properties"])

    def test_a_package_can_be_switched_off_from_the_registry(self):
        write_package(self.root, "toggle_pkg")
        sr.record("toggle_pkg", source="vault", active=False)
        self.assertEqual(sr.active_vault_skills(), [])
        sr.toggle("toggle_pkg", True)
        self.assertEqual(len(sr.active_vault_skills()), 1)

    def test_a_package_can_switch_itself_off_in_its_own_manifest(self):
        write_package(self.root, "off_pkg",
                      manifest={"name": "off_pkg", "description": "d",
                                "active": False})
        self.assertEqual(sr.active_vault_skills(), [])

    def test_running_a_package_returns_its_summary(self):
        write_package(self.root, "runner")
        self.assertEqual(sr.run_vault_skill("runner", {}), "package ran")

    def test_running_a_package_that_raises_is_reported_not_propagated(self):
        write_package(self.root, "exploder",
                      body="def execute(**kwargs):\n    raise RuntimeError('nope')\n")
        out = sr.run_vault_skill("exploder", {})
        self.assertIn("failed", out)
        self.assertIn("nope", out)
        self.assertIn("nope", sr.get("exploder").get("last_error", ""))

    def test_running_a_package_that_is_not_there_is_reported(self):
        self.assertIn("no skill", sr.run_vault_skill("imaginary", {}))

    def test_an_unknown_package_reports_its_brokenness(self):
        write_package(self.root, "halfbuilt", with_code=False)
        self.assertIn("could not be loaded", sr.run_vault_skill("halfbuilt", {}))

    def test_a_package_returning_a_string_is_returned_as_is(self):
        write_package(self.root, "talker",
                      body="def execute(**kwargs):\n    return 'plain sentence'\n")
        self.assertEqual(sr.run_vault_skill("talker", {}), "plain sentence")


class RouterTest(Base):
    def test_an_exact_name_match_routes(self):
        write_plugin(self.root, "internet_speed_test",
                     description="Measures the internet connection speed.")
        match = sr.find_matching_skill("run the internet speed test")
        self.assertIsNotNone(match)
        self.assertEqual(match[0], "internet_speed_test")

    def test_a_declared_trigger_phrase_routes(self):
        write_plugin(self.root, "crypto_price",
                     description="Reports a live crypto price.",
                     triggers=["how much is bitcoin"])
        match = sr.find_matching_skill("hey how much is bitcoin right now")
        self.assertEqual(match[0], "crypto_price")

    def test_an_alias_routes(self):
        write_plugin(self.root, "space_station",
                     description="Shows where the station is.",
                     aliases=["iss"])
        match = sr.find_matching_skill("where is the iss")
        self.assertEqual(match[0], "space_station")

    def test_domain_words_alone_are_enough(self):
        write_plugin(self.root, "internet_speed_test",
                     description="Measures download latency.")
        self.assertIsNotNone(sr.find_matching_skill("check my internet speed"))

    def test_an_unrelated_sentence_routes_nowhere(self):
        write_plugin(self.root, "internet_speed_test",
                     description="Measures download latency.")
        self.assertIsNone(sr.find_matching_skill("make me a cup of tea"))
        self.assertIsNone(sr.find_matching_skill("what is the weather in Paris"))

    def test_a_single_shared_word_is_not_enough(self):
        """The threshold is the whole safety property: a weak match would run the
        wrong tool with no chance for anyone to notice."""
        write_plugin(self.root, "speed_test", description="Measures speed.")
        self.assertIsNone(sr.find_matching_skill("test"))

    def test_an_empty_or_junk_sentence_routes_nowhere(self):
        write_plugin(self.root, "anything", description="Does things.")
        for junk in ("", "   ", None, 42, "the and of to"):
            self.assertIsNone(sr.find_matching_skill(junk))

    def test_asking_for_the_latest_skill_gives_the_newest(self):
        sr.record("first_skill", description="one")
        sr.record("second_skill", description="two")
        match = sr.find_matching_skill("use the latest skill")
        self.assertEqual(match, ("second_skill", {}))

    def test_the_latest_shortcut_does_not_hijack_a_real_request(self):
        """'use the skill that checks crypto' names a domain, so the domain must
        win over recency."""
        sr.record("unrelated_newest", description="nothing to do with money")
        write_plugin(self.root, "crypto_price",
                     description="Reports the live crypto price.")
        match = sr.find_matching_skill("use the skill that checks the crypto price")
        self.assertEqual(match[0], "crypto_price")

    def test_a_switch_off_skill_is_not_routed_to(self):
        write_plugin(self.root, "disabled_skill",
                     description="Measures the internet connection speed.")
        sr.record("disabled_skill", source="plugin", active=False)
        self.assertIsNone(sr.find_matching_skill("internet speed test"))

    def test_a_vault_package_can_be_routed_to(self):
        write_package(self.root, "stock_prices",
                      description="Looks up the live share price for a ticker.",
                      parameters={"type": "OBJECT", "properties": {
                          "ticker": {"type": "STRING",
                                     "description": "Share ticker"}}})
        match = sr.find_matching_skill("what is the stock price today")
        self.assertIsNotNone(match)
        self.assertEqual(match[0], "stock_prices")

    def test_a_place_parameter_is_taken_from_the_sentence(self):
        write_plugin(self.root, "city_weather",
                     description="Reports the weather for a city.",
                     parameters={"type": "OBJECT", "properties": {
                         "city": {"type": "STRING",
                                  "description": "The city to look up"}}})
        name, args = sr.find_matching_skill("city weather in Kolkata please")
        self.assertEqual(name, "city_weather")
        self.assertEqual(args.get("city"), "Kolkata")

    def test_a_quoted_option_in_the_description_is_matched(self):
        write_plugin(self.root, "unit_convert",
                     description="Converts units. Modes: 'km' or 'mi'.",
                     parameters={"type": "OBJECT", "properties": {
                         "unit": {"type": "STRING",
                                  "description": "Target unit, 'km' or 'mi'"}}})
        name, args = sr.find_matching_skill("use the unit convert skill for mi")
        self.assertEqual(name, "unit_convert")
        self.assertEqual(args.get("unit"), "mi")

    def test_a_duration_parameter_is_taken_from_the_number(self):
        write_plugin(self.root, "timed_scan",
                     description="Scans for a while and reports.",
                     parameters={"type": "OBJECT", "properties": {
                         "seconds": {"type": "INTEGER",
                                     "description": "How long to scan for"}}})
        name, args = sr.find_matching_skill("run the timed scan for 45 seconds")
        self.assertEqual(name, "timed_scan")
        self.assertEqual(args.get("seconds"), 45)

    def test_a_skill_with_no_parameters_yields_no_arguments(self):
        write_plugin(self.root, "no_args", description="Reports the time now.")
        name, args = sr.find_matching_skill("run the no args skill")
        self.assertEqual(name, "no_args")
        self.assertEqual(args, {})

    def test_a_plugin_file_that_will_not_parse_is_skipped(self):
        folder = self.root / "plugins"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "garbage.py").write_text("PLUGIN = {{{not python\n")
        write_plugin(self.root, "fine_after_it", description="Works normally.")
        self.assertEqual(
            sr.find_matching_skill("run fine after it")[0], "fine_after_it")


if __name__ == "__main__":
    unittest.main()
