"""Unit tests for core/self_heal.py and actions/self_heal.py.

This module can rewrite source files, so the tests care most about the paths where
it must NOT: a protected file, a file outside the installation, a patch whose
target text does not appear exactly once, a patch that will not compile. In every
one of those the original file must come out byte-identical, and that is asserted
rather than assumed. Everything runs against a throwaway installation and a
stubbed model; the real config, backups and patch log are never touched.
"""
import json
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from actions import self_heal as tool
from core import boot_sentry, self_heal as sh

BUGGY = textwrap.dedent('''
    """A module with a bug in it."""


    def describe(value):
        """Return a description of value."""
        return value.strip()


    def unrelated():
        return "this must not change at all"
''').lstrip("\n")

#: The failing line and its fix, taken from the fixture above so a future edit to
#: BUGGY cannot silently leave the whole file testing a patch that changes nothing
#: (a replacement whose target is absent just writes the file back unchanged).
BUG_LINE = "    return value.strip()"
FIXED_LINE = "    return str(value).strip()"
assert BUG_LINE in BUGGY and BUG_LINE not in BUGGY.replace(BUG_LINE, "", 1), \
    "the fixture must contain exactly one copy of the failing line"


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.actions = self.root / "actions"
        self.actions.mkdir(parents=True, exist_ok=True)
        for target, attr in ((sh, "_base_dir"), (boot_sentry, "_base_dir")):
            p = mock.patch.object(target, attr, lambda: self.root)
            p.start()
            self.addCleanup(p.stop)
        self.bug = self.actions / "demo.py"
        self.bug.write_text(BUGGY, encoding="utf-8")
        self.original = self.bug.read_text(encoding="utf-8")
        # _last_error is module state; leaking it between tests makes the tool's
        # "have you seen any errors" answers order-dependent.
        self._saved_error = sh._last_error
        sh._last_error = ""
        self.addCleanup(lambda: setattr(sh, "_last_error", self._saved_error))
        self._print = mock.patch("builtins.print")
        self._print.start()
        self.addCleanup(self._print.stop)

    def traceback_for(self, path=None, line=6, error="AttributeError",
                      message="'NoneType' object has no attribute 'strip'"):
        path = path or self.bug
        return (f'Traceback (most recent call last):\n'
                f'  File "{path}", line {line}, in describe\n'
                f'    return value.strip()\n'
                f'{error}: {message}\n')

    def model_says(self, target=None, replacement=None, **extra):
        target = target if target is not None else BUG_LINE
        replacement = replacement if replacement is not None else FIXED_LINE
        reply = {"explanation": "Guard against a None value.",
                 "target_chunk": target, "replacement_chunk": replacement}
        reply.update(extra)
        p = mock.patch.object(sh, "synthesize",
                              lambda d, context_note="", timeout_ms=0:
                              ({"ok": True, "path": d["path"],
                                "source": self.original,
                                "patched": self.original.replace(target, replacement, 1),
                                "target": target, "replacement": replacement,
                                "explanation": "Guard against a None value.",
                                "line": int(d["line"])}
                               if target in self.original else
                               {"ok": False,
                                "reason": f"the block it wanted to replace does not "
                                          f"appear exactly once in demo.py"}))
        p.start()
        self.addCleanup(p.stop)

    def config(self, auto=False):
        from memory import config_manager
        p = mock.patch.object(config_manager, "get_self_heal_auto",
                              return_value=auto)
        p.start()
        self.addCleanup(p.stop)


class LocateTest(Base):
    def test_an_empty_traceback_says_so(self):
        out = sh.parse_traceback("")
        self.assertFalse(out["ok"])
        self.assertIn("no traceback", out["reason"])

    def test_the_failing_file_and_line_are_found(self):
        out = sh.parse_traceback(self.traceback_for())
        self.assertTrue(out["ok"], out["reason"])
        self.assertEqual(Path(out["path"]).name, "demo.py")
        self.assertEqual(out["line"], 6)
        self.assertEqual(out["function"], "describe")
        self.assertEqual(out["error_type"], "AttributeError")
        self.assertIn("NoneType", out["message"])

    def test_library_frames_are_never_the_suspect(self):
        """Patching site-packages is not something a running assistant should do,
        however plainly the traceback points at it."""
        text = (f'Traceback (most recent call last):\n'
                f'  File "{self.bug}", line 6, in describe\n'
                f'    return value.strip()\n'
                f'  File "C:/Python/lib/site-packages/requests/api.py", line 10\n'
                f'    raise TypeError("x")\n')
        out = sh.parse_traceback(text)
        self.assertTrue(out["ok"])
        self.assertEqual(Path(out["path"]).name, "demo.py")

    def test_a_protected_file_is_refused_with_a_reason(self):
        protected = self.root / "core"
        protected.mkdir(exist_ok=True)
        (protected / "self_heal.py").write_text("x = 1\n", encoding="utf-8")
        out = sh.parse_traceback(self.traceback_for(protected / "self_heal.py"))
        self.assertFalse(out["ok"])
        self.assertIn("will not patch", out["reason"])

    def test_a_file_outside_the_installation_is_refused(self):
        outside = Path(self._tmp.name).parent / "not_mine.py"
        outside.write_text("x = 1\n", encoding="utf-8")
        self.addCleanup(lambda: outside.unlink(missing_ok=True))
        out = sh.parse_traceback(self.traceback_for(outside))
        self.assertFalse(out["ok"])
        self.assertIn("outside my own folder", out["reason"])

    def test_a_file_that_does_not_exist_is_skipped(self):
        out = sh.parse_traceback(self.traceback_for(self.root / "ghost.py"))
        self.assertFalse(out["ok"])

    def test_context_is_the_lines_around_the_failure(self):
        text = sh.local_context(self.bug, 6)
        self.assertIn("def describe", text)
        self.assertIn("value.strip", text)


class SynthesizeTest(Base):
    def _with_reply(self, reply):
        p = mock.patch("core.gemini.as_json", lambda *a, **k: reply)
        p.start()
        self.addCleanup(p.stop)
        return sh.synthesize({"ok": True, "path": str(self.bug), "line": 6,
                              "function": "describe", "error_type": "AttributeError",
                              "message": "'NoneType' has no attribute 'strip'"})

    def test_a_clean_reply_becomes_a_validated_patch(self):
        out = self._with_reply({
            "explanation": "Guard the None.",
            "target_chunk": BUG_LINE,
            "replacement_chunk": FIXED_LINE})
        self.assertTrue(out["ok"], out.get("reason"))
        self.assertIn("str(value).strip()", out["patched"])
        self.assertNotEqual(out["patched"], out["source"])
        self.assertEqual(out["line"], 6)

    def test_a_patch_that_will_not_parse_is_refused(self):
        out = self._with_reply({"target_chunk": BUG_LINE,
                                "replacement_chunk": "    return (((("})
        self.assertFalse(out["ok"])
        self.assertIn("syntax error", out["reason"])

    def test_a_target_that_appears_twice_is_refused(self):
        """Ambiguous replacement is how a hotfix silently rewrites the wrong
        function."""
        self.bug.write_text(self.original + f"\n\ndef other(value):\n{BUG_LINE}\n",
                            encoding="utf-8")
        self.original = self.bug.read_text(encoding="utf-8")
        out = self._with_reply({"target_chunk": BUG_LINE,
                                "replacement_chunk": "    return 1"})
        self.assertFalse(out["ok"])
        self.assertIn("exactly once", out["reason"])

    def test_a_target_that_is_absent_is_refused(self):
        out = self._with_reply({"target_chunk": "  not in the file at all  ",
                                "replacement_chunk": "  whatever  "})
        self.assertFalse(out["ok"])
        self.assertIn("does not appear", out["reason"])

    def test_an_empty_replacement_is_refused(self):
        out = self._with_reply({"target_chunk": BUG_LINE,
                                "replacement_chunk": "   "})
        self.assertFalse(out["ok"])
        self.assertIn("nothing", out["reason"])

    def test_a_rewrite_sized_patch_is_refused(self):
        out = self._with_reply({"target_chunk": BUG_LINE,
                                "replacement_chunk": "x = 1\n" * 2000})
        self.assertFalse(out["ok"])
        self.assertIn("rewrite", out["reason"])

    def test_the_model_admitting_it_cannot_fix_it_is_respected(self):
        out = self._with_reply({"unfixable": "that whole class needs redesigning"})
        self.assertFalse(out["ok"])
        self.assertTrue(out.get("unfixable"))
        self.assertIn("redesigning", out["reason"])

    def test_a_model_that_returns_nothing_is_reported(self):
        out = self._with_reply(None)
        self.assertFalse(out["ok"])
        self.assertIn("usable patch", out["reason"])

    def test_an_unreachable_model_is_reported_not_raised(self):
        p = mock.patch("core.gemini.as_json",
                       side_effect=RuntimeError("no key"))
        p.start()
        self.addCleanup(p.stop)
        out = self._with_reply.__wrapped__(None) if hasattr(
            self._with_reply, "__wrapped__") else None
        out = sh.synthesize({"ok": True, "path": str(self.bug), "line": 6,
                             "function": "", "error_type": "", "message": ""})
        self.assertFalse(out["ok"])
        self.assertIn("could not be reached", out["reason"])

    def test_an_unfixable_diagnosis_short_circuits(self):
        out = sh.synthesize({"ok": False, "reason": "no file of mine"})
        self.assertFalse(out["ok"])
        self.assertIn("no file of mine", out["reason"])


class ApplyTest(Base):
    def _patch(self, **over):
        patch = {"ok": True, "path": str(self.bug), "source": self.original,
                 "patched": self.original.replace(BUG_LINE, FIXED_LINE, 1),
                 "target": BUG_LINE, "replacement": FIXED_LINE,
                 "explanation": "Guard the None.", "line": 6}
        patch.update(over)
        return patch

    def test_a_good_patch_is_written_backed_up_and_logged(self):
        out = sh.apply_patch(self._patch())
        self.assertTrue(out["ok"], out.get("message"))
        self.assertIn("str(value).strip()", self.bug.read_text(encoding="utf-8"))
        self.assertTrue(Path(out["backup"]).exists())
        self.assertIn("this must not change at all",
                      Path(out["backup"]).read_text(encoding="utf-8"))
        self.assertEqual(len(sh.applied_patches()), 1)
        self.assertEqual(sh.applied_patches()[0]["name"], "demo.py")

    def test_applying_a_patch_arms_the_boot_sentry(self):
        """Without the watch, a patch that breaks the next start could never be
        undone by the process it broke."""
        out = sh.apply_patch(self._patch())
        watch = boot_sentry.armed()
        self.assertEqual(watch["patch_id"], out["patch_id"])
        self.assertEqual(Path(watch["file"]).name, "demo.py")
        self.assertEqual(watch["backup"], out["backup"])

    def test_a_patch_that_does_not_compile_leaves_the_file_untouched(self):
        out = sh.apply_patch(self._patch(patched=self.original + "\n(this is not python\n"))
        self.assertFalse(out["ok"])
        self.assertEqual(self.bug.read_text(encoding="utf-8"), self.original)
        self.assertEqual(sh.applied_patches(), [])

    def test_a_protected_file_is_refused_at_apply_time_too(self):
        protected = self.bug.with_name("confirm.py")
        protected.write_text(self.original, encoding="utf-8")
        out = sh.apply_patch(self._patch(path=str(protected)))
        self.assertFalse(out["ok"])
        self.assertIn("protected", out["message"])
        self.assertEqual(protected.read_text(encoding="utf-8"), self.original)

    def test_a_patch_with_no_target_is_refused(self):
        self.assertFalse(sh.apply_patch({"ok": False, "reason": "nope"})["ok"])

    def test_a_failed_backup_write_does_not_touch_the_file(self):
        p = mock.patch.object(sh, "_backup",
                              side_effect=OSError("disk full"))
        p.start()
        self.addCleanup(p.stop)
        out = sh.apply_patch(self._patch())
        self.assertFalse(out["ok"])
        self.assertEqual(self.bug.read_text(encoding="utf-8"), self.original)


class RollbackTest(Base):
    def _apply(self):
        patch = {"ok": True, "path": str(self.bug), "source": self.original,
                 "patched": self.original.replace("value.strip()", "str(value).strip()", 1),
                 "target": "value.strip()", "replacement": "str(value).strip()",
                 "explanation": "Guard.", "line": 6}
        return sh.apply_patch(patch)

    def test_rollback_restores_the_previous_version(self):
        applied = self._apply()
        out = sh.rollback("latest")
        self.assertTrue(out["ok"], out.get("message"))
        self.assertEqual(self.bug.read_text(encoding="utf-8"), self.original)
        self.assertEqual(sh.applied_patches(), [])

    def test_rollback_by_id_and_by_name_both_work(self):
        applied = self._apply()
        self.assertTrue(sh.rollback(applied["patch_id"])["ok"])
        self._apply()
        self.assertTrue(sh.rollback("demo.py")["ok"])

    def test_rollback_with_nothing_applied_says_so(self):
        out = sh.rollback("latest")
        self.assertFalse(out["ok"])
        self.assertIn("no applied patch", out["message"])

    def test_a_rolled_back_patch_is_not_offered_twice(self):
        self._apply()
        self.assertTrue(sh.rollback("latest")["ok"])
        self.assertFalse(sh.rollback("latest")["ok"])

    def test_a_missing_backup_is_reported_rather_than_guessed_at(self):
        applied = self._apply()
        Path(applied["backup"]).unlink()
        out = sh.rollback("latest")
        self.assertFalse(out["ok"])
        self.assertIn("missing", out["message"])

    def test_rolling_back_retires_the_watch(self):
        self._apply()
        self.assertTrue(boot_sentry.armed())
        sh.rollback("latest")
        self.assertFalse(boot_sentry.armed())


class HealFlowTest(Base):
    def test_a_traceback_with_no_first_party_frame_stops_at_locate(self):
        out = sh.heal(self.traceback_for(self.root / "ghost.py"))
        self.assertFalse(out["ok"])
        self.assertEqual(out["stage"], "locate")

    def test_without_auto_the_patch_waits_for_confirmation(self):
        """The default must not change any code. The banner goes up instead."""
        self.config(auto=False)
        self.model_says()
        requests = []
        p = mock.patch("core.confirm.request",
                       lambda key, title, detail, run: (
                           requests.append((key, title, detail, run)),
                           "waiting on screen")[1])
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch("core.confirm.pending_title", lambda: "")
        p.start()
        self.addCleanup(p.stop)

        out = sh.heal(self.traceback_for())
        self.assertTrue(out["ok"])
        self.assertEqual(out["stage"], "awaiting_confirmation")
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0][0], "self-heal")
        self.assertIn("demo.py", requests[0][2])
        self.assertEqual(self.bug.read_text(encoding="utf-8"), self.original,
                         "nothing may be written before the user confirms")
        # Accepting the banner is what applies it.
        requests[0][3]()
        self.assertIn("str(value).strip()", self.bug.read_text(encoding="utf-8"))

    def test_with_auto_on_the_patch_is_applied_immediately(self):
        self.config(auto=True)
        self.model_says()
        out = sh.heal(self.traceback_for())
        self.assertTrue(out["ok"])
        self.assertIn("str(value).strip()", self.bug.read_text(encoding="utf-8"))

    def test_force_applies_even_with_auto_off(self):
        self.config(auto=False)
        self.model_says()
        out = sh.heal(self.traceback_for(), force=True)
        self.assertTrue(out["ok"])
        self.assertTrue(out.get("patch_id"))

    def test_a_second_banner_is_not_stacked_on_the_first(self):
        self.config(auto=False)
        self.model_says()
        p = mock.patch("core.confirm.pending_title", lambda: "Something else")
        p.start()
        self.addCleanup(p.stop)
        out = sh.heal(self.traceback_for())
        self.assertFalse(out["ok"])
        self.assertEqual(out["stage"], "busy")

    def test_heal_falls_back_to_the_remembered_error(self):
        self.config(auto=True)
        self.model_says()
        sh.note_error(self.traceback_for(), context="tool web_search")
        self.assertTrue(sh.last_error())
        out = sh.heal()
        self.assertTrue(out["ok"])

    def test_heal_with_nothing_to_go_on_is_honest(self):
        out = sh.heal("")
        self.assertFalse(out["ok"])
        self.assertEqual(out["stage"], "locate")


class ReportingTest(Base):
    def test_note_error_keeps_the_crash_for_later(self):
        sh.note_error("Traceback: boom", context="tool x")
        self.assertIn("boom", sh.last_error())
        self.assertIn("tool x", sh.last_error())

    def test_note_error_survives_junk(self):
        sh.note_error(None)
        sh.note_error(object())

    def test_recent_patch_note_is_empty_when_nothing_was_patched(self):
        self.assertEqual(sh.recent_patch_note(), "")

    def test_recent_patch_note_describes_the_latest_patch(self):
        bug = self.bug
        original = bug.read_text(encoding="utf-8")
        sh.apply_patch({"ok": True, "path": str(bug), "source": original,
                        "patched": original.replace("value.strip()",
                                                    "str(value).strip()", 1),
                        "target": "value.strip()", "replacement": "str(value).strip()",
                        "explanation": "Guard.", "line": 6})
        note = sh.recent_patch_note()
        self.assertIn("demo.py", note)
        self.assertIn("Guard.", note)

    def test_status_reads_sensibly_with_no_patches(self):
        text = sh.status()
        self.assertIn("SELF-REPAIR STATUS", text)
        self.assertIn("No patches applied", text)

    def test_status_names_the_switch_position(self):
        self.assertIn("no (they wait", sh.status())
        self.config_true = mock.patch("memory.config_manager.get_self_heal_auto",
                                     return_value=True)
        self.config_true.start()
        self.addCleanup(self.config_true.stop)
        self.assertIn("yes", sh.status())

    def test_why_not_patchable_explains_a_protected_file(self):
        self.assertIn("will not edit myself", sh.why_not_patchable("core/self_heal.py"))
        self.assertIn("not on the protected list",
                      sh.why_not_patchable("actions/demo.py"))

    def test_forget_history_clears_the_log(self):
        original = self.bug.read_text(encoding="utf-8")
        sh.apply_patch({"ok": True, "path": str(self.bug), "source": original,
                        "patched": original.replace("value.strip()",
                                                    "str(value).strip()", 1),
                        "target": "value.strip()", "replacement": "str(value).strip()",
                        "explanation": "Guard.", "line": 6})
        self.assertEqual(sh.forget_history(), 1)
        self.assertEqual(sh.applied_patches(), [])

    def test_a_corrupt_patch_log_is_just_empty(self):
        sh.history_path().parent.mkdir(parents=True, exist_ok=True)
        sh.history_path().write_text("not json at all", encoding="utf-8")
        self.assertEqual(sh.applied_patches(), [])
        self.assertIn("SELF-REPAIR", sh.status())


class ToolTest(Base):
    def test_an_unknown_action_lists_the_options(self):
        out = tool.self_heal({"action": "vanish"})
        self.assertIn("do not know", out)
        self.assertIn("heal", out)

    def test_heal_with_nothing_to_work_on_is_honest(self):
        out = tool.self_heal({"action": "heal"})
        self.assertIn("no recent error", out)

    def test_heal_says_nothing_was_changed_when_it_waits(self):
        self.config_off = mock.patch("memory.config_manager.get_self_heal_auto",
                                     return_value=False)
        self.config_off.start()
        self.addCleanup(self.config_off.stop)
        p = mock.patch("core.confirm.pending_title", lambda: "")
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch("core.confirm.request", lambda *a: "on screen")
        p.start()
        self.addCleanup(p.stop)
        # A patch cannot be synthesised without a model, so this exercises the
        # honest failure path the user actually hears.
        p = mock.patch("core.gemini.as_json", lambda *a, **k: None)
        p.start()
        self.addCleanup(p.stop)
        out = tool.self_heal({"action": "heal",
                              "traceback": self.traceback_for()})
        self.assertIn("could not write a safe fix", out)

    def test_history_is_empty_before_any_patch(self):
        self.assertIn("have not patched myself", tool.self_heal({"action": "history"}))

    def test_rollback_with_nothing_applied_says_so(self):
        self.assertIn("no applied patch",
                      tool.self_heal({"action": "rollback"}))

    def test_errors_lists_what_it_has_seen(self):
        self.assertIn("have not seen any errors",
                      tool.self_heal({"action": "errors"}))

    def test_status_is_reported(self):
        self.assertIn("SELF-REPAIR", tool.self_heal({"action": "status"}))

    def test_why_lists_the_protected_files(self):
        out = tool.self_heal({"action": "why"})
        self.assertIn("self_heal.py", out)
        self.assertIn("never patch", out)

    def test_why_answers_about_one_file(self):
        self.assertIn("will not edit myself",
                      tool.self_heal({"action": "why", "file": "core/updater.py"}))

    def test_verify_confirms_a_pending_patch_and_says_so(self):
        boot_sentry.arm("abc123", str(self.bug), str(self.bug))
        self.assertIn("confirmed good", tool.self_heal({"action": "verify"}))
        self.assertIn("no pending patch", tool.self_heal({"action": "verify"}))

    def test_clear_reports_the_count(self):
        self.assertIn("already empty", tool.self_heal({"action": "clear"}))

    def test_the_tool_is_discoverable_by_the_action_loader(self):
        self.assertEqual(tool.TOOL["name"], "self_heal")
        self.assertTrue(callable(tool.TOOL["handler"]))
        self.assertEqual(tool.TOOL["parameters"]["type"], "OBJECT")

    def test_the_tool_never_raises_on_junk(self):
        for junk in ({}, {"action": None}, {"action": 42},
                     {"action": "heal", "traceback": object()}):
            self.assertTrue(str(tool.self_heal(junk)))


if __name__ == "__main__":
    unittest.main()
