"""Unit tests for core/skill_crucible.py — the gate a self-written tool must pass.

The crucible is the only thing between model output and code that runs inside the
assistant, so most of these tests are about *refusals*: every banned capability
has one, and a refusal must be specific enough to act on. The behavioural gate
genuinely spawns child interpreters — that is the point of it, so faking it would
test the wrong thing. Each subprocess is short and there are only a handful.
"""
import sys
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import skill_crucible as crucible


def plugin(body: str, name: str = "demo_skill", description: str = "Does a demo.",
           parameters: str | None = None, header: str = 'A demo skill.') -> str:
    """Build a candidate plugin around `body`, the way the forge would.

    Assembled line by line rather than from one dedented template: an
    interpolated multi-line body inside a triple-quoted f-string defeats
    textwrap.dedent, which silently produces a file that will not compile and
    makes every gate failure look the same.
    """
    params = parameters if parameters is not None else \
        '{"type": "OBJECT", "properties": {}}'
    body = textwrap.dedent(body).strip()
    indented = "\n".join(("    " + line) if line.strip() else line
                         for line in body.splitlines())
    return (
        f'"""{header}"""\n'
        "PLUGIN = {\n"
        f'    "name": "{name}",\n'
        f'    "description": "{description}",\n'
        f'    "parameters": {params},\n'
        "}\n\n"
        "def run(parameters: dict, player=None, session_memory=None) -> str:\n"
        f"{indented}\n"
    )


GOOD = plugin('return "Done."')


class ShapeTest(unittest.TestCase):
    def test_a_real_plugin_passes(self):
        tree, bad = crucible.parse(GOOD)
        self.assertIsNone(bad)
        verdict = crucible.check_shape(tree)
        self.assertTrue(verdict.ok, verdict.reason)

    def test_empty_source_is_refused(self):
        _, bad = crucible.parse("")
        self.assertIsNotNone(bad)
        self.assertEqual(bad.stage, "shape")
        self.assertIn("empty", bad.reason)

    def test_oversized_source_is_refused(self):
        _, bad = crucible.parse("x = 1\n" * 20_000)
        self.assertIsNotNone(bad)
        self.assertIn("too large", bad.reason)

    def test_syntax_error_names_the_line(self):
        _, bad = crucible.parse(GOOD + "\ndef broken(:\n    pass\n")
        self.assertIsNotNone(bad)
        self.assertEqual(bad.stage, "shape")
        self.assertIn("line", bad.reason)

    def test_missing_plugin_declaration_is_refused(self):
        source = ("def run(parameters, player=None, session_memory=None):\n"
                  "    # a tool with no declaration at all, long enough to parse\n"
                  "    return 'hi there friend'\n")
        tree, bad = crucible.parse(source)
        self.assertIsNone(bad)
        verdict = crucible.check_shape(tree)
        self.assertFalse(verdict.ok)
        self.assertIn("no PLUGIN", verdict.reason)

    def test_missing_entry_point_is_refused(self):
        source = GOOD.replace("def run(parameters", "def renamed(parameters")
        tree, _ = crucible.parse(source)
        verdict = crucible.check_shape(tree)
        self.assertFalse(verdict.ok)
        self.assertIn("neither run", verdict.reason)

    def test_declaration_that_is_not_a_dict_is_refused(self):
        source = GOOD.replace("PLUGIN = {", 'PLUGIN = "a string"\n_OLD = {')
        tree, bad = crucible.parse(source)
        self.assertIsNone(bad)
        verdict = crucible.check_shape(tree)
        self.assertFalse(verdict.ok)
        self.assertIn("declaration is not", verdict.reason)

    def test_name_mismatch_is_refused(self):
        tree, _ = crucible.parse(GOOD)
        verdict = crucible.check_shape(tree, expected_name="something_else")
        self.assertFalse(verdict.ok)
        self.assertIn("declares itself", verdict.reason)

    def test_missing_description_is_refused(self):
        source = GOOD.replace('"description": "Does a demo.",', "")
        tree, _ = crucible.parse(source)
        verdict = crucible.check_shape(tree)
        self.assertFalse(verdict.ok)
        self.assertIn("description", verdict.reason)

    def test_plugin_parameters_must_be_an_object_schema(self):
        source = GOOD.replace('"parameters": {"type": "OBJECT", "properties": {}},',
                              '"parameters": {"type": "STRING"},')
        tree, _ = crucible.parse(source)
        verdict = crucible.check_shape(tree)
        self.assertFalse(verdict.ok)
        self.assertIn("OBJECT", verdict.reason)

    def test_a_feature_package_style_skill_is_also_accepted(self):
        """The second contract, kept loadable for skills written that way."""
        source = textwrap.dedent('''
            FEATURE_METADATA = {"name": "pkg_skill", "description": "A package skill."}

            def execute(**kwargs):
                return "package done"
        ''').strip() + "\n"
        tree, _ = crucible.parse(source)
        verdict = crucible.check_shape(tree)
        self.assertTrue(verdict.ok, verdict.reason)

    def test_async_entry_point_is_accepted(self):
        source = GOOD.replace("def run(parameters", "async def run(parameters")
        tree, _ = crucible.parse(source)
        self.assertTrue(crucible.check_shape(tree).ok)


class SafetyTest(unittest.TestCase):
    BANNED = (
        '"""format c: /y"""',
        'subprocess.run("diskpart /s script.txt")',
        'path = "C:/Windows/system32/drivers"',
        'key = open("config/api_keys.json")',
        'subprocess.run("schtasks /create /tn x")',
        'subprocess.run("netsh advfirewall set allprofiles state off")',
        'open(".ssh/id_rsa")',
        'subprocess.run("vssadmin delete shadows /all")',
        'subprocess.run("bcdedit /set safeboot minimal")',
        'reg = "Software/Microsoft/Windows/CurrentVersion/Run"',
    )

    def test_every_banned_capability_is_caught(self):
        for needle in self.BANNED:
            with self.subTest(needle=needle):
                source = plugin(f'{needle}\n{textwrap.indent("return \'ok\'", "")}')
                tree, _ = crucible.parse(source)
                verdict = crucible.check_safety(tree)
                self.assertFalse(verdict.ok, f"not caught: {needle}")
                self.assertEqual(verdict.stage, "safety")
                self.assertTrue(verdict.reason)

    def test_refusal_says_why_in_plain_words(self):
        tree, _ = crucible.parse(plugin('"""format c: /y"""\nreturn "ok"'))
        verdict = crucible.check_safety(tree)
        self.assertIn("reformat the system drive", verdict.reason)

    def test_a_clean_plugin_passes_the_screen(self):
        tree, _ = crucible.parse(plugin('return "nothing bad here at all"'))
        self.assertTrue(crucible.check_safety(tree).ok)

    def test_a_banned_string_hidden_in_a_docstring_is_still_refused(self):
        """A plan to do the thing is still a refusal — this is a screen, and a
        screen that lets prose through is one an attacker only has to phrase
        differently."""
        source = plugin('return "ok"', header="I will run format c: and be done.")
        tree, _ = crucible.parse(source)
        self.assertFalse(crucible.check_safety(tree).ok)


class ImportGateTest(unittest.TestCase):
    def test_stdlib_imports_are_not_dependencies(self):
        source = plugin("import json, os, re, sqlite3\nreturn 'ok'")
        tree, _ = crucible.parse(source)
        self.assertEqual(crucible.extract_dependencies(tree), [])

    def test_project_modules_are_not_dependencies(self):
        source = plugin("from core import gemini\nreturn 'ok'")
        tree, _ = crucible.parse(source)
        self.assertEqual(crucible.extract_dependencies(tree), [])

    def test_a_missing_package_is_named_and_refused(self):
        deps = ["definitely_not_installed_xyz"]
        verdict = crucible.check_dependencies(deps)
        self.assertFalse(verdict.ok)
        self.assertEqual(verdict.stage, "imports")
        self.assertIn("definitely_not_installed_xyz", verdict.reason)

    def test_nothing_is_installed_on_our_behalf(self):
        """The refusal must be the end of it: no subprocess, no pip. Checked by
        watching the one module that could do either."""
        import subprocess as sp
        calls = []
        original = sp.run
        sp.run = lambda *a, **k: (calls.append(a), original(*a, **k))[1]
        try:
            crucible.check_dependencies(["definitely_not_installed_xyz"])
        finally:
            sp.run = original
        self.assertEqual(calls, [])

    def test_an_installed_package_passes(self):
        self.assertTrue(crucible.check_dependencies(["json"]).ok)


class BehaviourTest(unittest.TestCase):
    def test_a_working_skill_returns_output(self):
        verdict = crucible.run_smoke_test(GOOD, "demo_skill", timeout=30)
        self.assertTrue(verdict.ok, f"{verdict.reason} {verdict.detail}")
        self.assertIn("ran cleanly", verdict.detail)
        self.assertEqual(verdict.telemetry.get("runs"), 1)

    def test_output_is_captured_for_the_caller(self):
        source = plugin('return "the answer is 42"')
        verdict = crucible.run_smoke_test(source, "demo_skill", timeout=30)
        self.assertTrue(verdict.ok)
        self.assertIn("42", verdict.telemetry.get("output", ""))

    def test_a_raising_skill_is_refused_with_the_exception(self):
        verdict = crucible.run_smoke_test(plugin('raise ValueError("boom")'),
                                          "demo_skill", timeout=30)
        self.assertFalse(verdict.ok)
        self.assertEqual(verdict.stage, "behaviour")
        self.assertIn("ValueError", verdict.reason)
        self.assertIn("boom", verdict.reason)

    def test_returning_an_error_dict_counts_as_a_failure(self):
        verdict = crucible.run_smoke_test(
            plugin('return {"error": "could not reach the server"}'),
            "demo_skill", timeout=30)
        self.assertFalse(verdict.ok)
        self.assertIn("could not reach the server", verdict.reason)

    def test_returning_success_false_counts_as_a_failure(self):
        verdict = crucible.run_smoke_test(
            plugin('return {"success": False, "message": "no dice"}'),
            "demo_skill", timeout=30)
        self.assertFalse(verdict.ok)

    def test_an_immune_style_error_sentence_counts_as_a_failure(self):
        verdict = crucible.run_smoke_test(plugin('return "Error: bad thing"'),
                                          "demo_skill", timeout=30)
        self.assertFalse(verdict.ok)

    def test_a_skill_that_never_returns_is_stopped_by_the_timeout(self):
        verdict = crucible.run_smoke_test(
            plugin("import time\ntime.sleep(60)\nreturn 'late'"),
            "demo_skill", timeout=2.0)
        self.assertFalse(verdict.ok)
        self.assertIn("did not finish", verdict.reason)

    def test_a_skill_that_breaks_on_import_is_refused(self):
        verdict = crucible.run_smoke_test(
            plugin("import sys\nsys.exit(3)\nreturn 'ok'"), "demo_skill", timeout=30)
        self.assertFalse(verdict.ok)

    def test_the_skill_never_imports_into_this_process(self):
        crucible.run_smoke_test(plugin('return "ok"'), "isolated_probe", timeout=30)
        self.assertNotIn("candidate_skill", sys.modules)
        self.assertNotIn("isolated_probe", sys.modules)

    def test_an_empty_parameter_call_is_always_tested(self):
        """The most common failure of all: a handler that assumes its arguments
        exist. A skill needing a required argument must still survive {}."""
        source = plugin('text = parameters.get("text") or "fallback"\n'
                        'return f"got {text}"',
                        parameters='{"type": "OBJECT", "properties": '
                                   '{"text": {"type": "STRING"}}, "required": ["text"]}')
        self.assertTrue(crucible.run_smoke_test(source, "demo_skill", timeout=30).ok)

    def test_a_skill_that_needs_a_required_argument_without_a_default_is_refused(self):
        source = plugin('return parameters["text"]',
                        parameters='{"type": "OBJECT", "properties": '
                                   '{"text": {"type": "STRING"}}, "required": ["text"]}')
        verdict = crucible.run_smoke_test(source, "demo_skill", timeout=30)
        self.assertFalse(verdict.ok)
        self.assertIn("KeyError", verdict.reason)


class VerifyTest(unittest.TestCase):
    def test_a_good_candidate_passes_every_gate(self):
        verdict = crucible.verify(GOOD, "demo_skill", timeout=30)
        self.assertTrue(verdict.ok, verdict.reason)
        self.assertEqual(verdict.stage, "complete")

    def test_safety_is_checked_before_shape(self):
        """A destructive candidate must be refused for *being destructive*, not
        told to go and fix its declaration."""
        source = plugin('"""format c: /y"""\nreturn "ok"', name="other_name")
        verdict = crucible.verify(source, "demo_skill", timeout=30)
        self.assertFalse(verdict.ok)
        self.assertEqual(verdict.stage, "safety")

    def test_behaviour_can_be_skipped_for_the_repair_loop(self):
        verdict = crucible.verify(plugin('raise ValueError("boom")'), "demo_skill",
                                  skip_behaviour=True)
        self.assertTrue(verdict.ok)

    def test_verdict_serialises_for_the_registry(self):
        data = crucible.verify(GOOD, "demo_skill", timeout=30).as_dict()
        self.assertIsInstance(data, dict)
        for key in ("ok", "stage", "reason", "detail", "warnings", "telemetry"):
            self.assertIn(key, data)


if __name__ == "__main__":
    unittest.main()
