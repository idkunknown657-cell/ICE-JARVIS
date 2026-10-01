"""Unit tests for core/boot_sentry.py — the undo for a patch that broke the boot.

The sentry exists for the one failure a self-patching assistant cannot recover
from on its own, so the tests are about the handshake being exactly right: it must
fire when a patched run never became healthy, and stay completely out of the way
in every other case. A sentry that rolls back a good patch is worse than no sentry
at all, which is why the "nothing armed" and "unreadable watch" paths are tested
as carefully as the recovery itself.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import boot_sentry, self_heal


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        for module in (boot_sentry, self_heal):
            p = mock.patch.object(module, "_base_dir", lambda: self.root)
            p.start()
            self.addCleanup(p.stop)
        self.target = self.root / "actions"
        self.target.mkdir(parents=True, exist_ok=True)
        self.file = self.target / "demo.py"
        self.file.write_text("ORIGINAL\n", encoding="utf-8")
        self.backup = self.root / "config" / "patch_backups" / "demo.bak"
        self.backup.parent.mkdir(parents=True, exist_ok=True)
        self.backup.write_text("ORIGINAL\n", encoding="utf-8")
        self._print = mock.patch("builtins.print")
        self._print.start()
        self.addCleanup(self._print.stop)

    def patch_the_file(self):
        """Simulate what self_heal does: back the file up, then write a patch."""
        self.file.write_text("PATCHED\n", encoding="utf-8")


class HandshakeTest(Base):
    def test_nothing_armed_is_silent(self):
        self.assertFalse(boot_sentry.armed())
        self.assertEqual(boot_sentry.check_and_recover(), {"recovered": False})

    def test_arming_records_what_would_be_undone(self):
        boot_sentry.arm("abc123", str(self.file), str(self.backup))
        watch = boot_sentry.armed()
        self.assertEqual(watch["patch_id"], "abc123")
        self.assertEqual(watch["file"], str(self.file))
        self.assertEqual(watch["backup"], str(self.backup))
        self.assertTrue(watch["armed_at"])

    def test_a_healthy_start_retires_the_watch(self):
        boot_sentry.arm("abc123", str(self.file), str(self.backup))
        self.assertTrue(boot_sentry.mark_healthy())
        self.assertFalse(boot_sentry.armed())
        self.assertFalse(boot_sentry.mark_healthy(),
                         "second call has nothing left to confirm")

    def test_confirming_health_means_the_next_start_will_not_roll_back(self):
        self.patch_the_file()
        boot_sentry.arm("abc123", str(self.file), str(self.backup))
        boot_sentry.mark_healthy()
        self.assertFalse(boot_sentry.check_and_recover()["recovered"])
        self.assertEqual(self.file.read_text(encoding="utf-8"), "PATCHED\n")


class RecoveryTest(Base):
    def test_an_unhealthy_patch_is_rolled_back(self):
        self.patch_the_file()
        boot_sentry.arm("abc123", str(self.file), str(self.backup))
        out = boot_sentry.check_and_recover()
        self.assertTrue(out["recovered"], out)
        self.assertEqual(self.file.read_text(encoding="utf-8"), "ORIGINAL\n")
        self.assertFalse(boot_sentry.armed())
        self.assertIn("did not start cleanly", out["message"])

    def test_the_patch_log_stops_claiming_the_patch_is_live(self):
        self.patch_the_file()
        self_heal._save_history([{"id": "abc123", "status": "applied",
                                  "file": str(self.file), "name": "demo.py",
                                  "line": 3, "explanation": "x"}])
        boot_sentry.arm("abc123", str(self.file), str(self.backup))
        boot_sentry.check_and_recover()
        entry = self_heal._load_history()[0]
        self.assertEqual(entry["status"], "reverted_on_boot")
        self.assertTrue(entry["reverted_at"])
        self.assertEqual(self_heal.applied_patches(), [])

    def test_another_patch_in_the_log_is_left_alone(self):
        self.patch_the_file()
        self_heal._save_history([
            {"id": "older", "status": "applied", "file": "x", "name": "x.py",
             "line": 1, "explanation": "x"},
            {"id": "abc123", "status": "applied", "file": str(self.file),
             "name": "demo.py", "line": 3, "explanation": "x"}])
        boot_sentry.arm("abc123", str(self.file), str(self.backup))
        boot_sentry.check_and_recover()
        statuses = {e["id"]: e["status"] for e in self_heal._load_history()}
        self.assertEqual(statuses["older"], "applied")
        self.assertEqual(statuses["abc123"], "reverted_on_boot")

    def test_a_missing_backup_is_reported_rather_than_guessed_at(self):
        self.patch_the_file()
        boot_sentry.arm("abc123", str(self.file), str(self.root / "gone.bak"))
        out = boot_sentry.check_and_recover()
        self.assertFalse(out["recovered"])
        self.assertIn("missing", out["reason"])
        self.assertEqual(self.file.read_text(encoding="utf-8"), "PATCHED\n",
                         "the file must not be touched when the backup is gone")
        self.assertFalse(boot_sentry.armed())

    def test_a_watch_pointing_outside_the_installation_is_refused(self):
        outside = Path(self._tmp.name).parent / "not_mine.py"
        outside.write_text("PATCHED\n", encoding="utf-8")
        self.addCleanup(lambda: outside.unlink(missing_ok=True))
        boot_sentry.arm("abc123", str(outside), str(self.backup))
        out = boot_sentry.check_and_recover()
        self.assertFalse(out["recovered"])
        self.assertIn("not inside my own folder", out["reason"])
        self.assertEqual(outside.read_text(encoding="utf-8"), "PATCHED\n")

    def test_a_watch_naming_no_file_is_discarded(self):
        boot_sentry.arm("abc123", "", str(self.backup))
        out = boot_sentry.check_and_recover()
        self.assertFalse(out["recovered"])
        self.assertFalse(boot_sentry.armed())

    def test_an_unreadable_watch_is_discarded_not_acted_on(self):
        """Guessing at a rollback from a half-written file is how one broken file
        becomes two."""
        self.patch_the_file()
        boot_sentry.watch_path().parent.mkdir(parents=True, exist_ok=True)
        boot_sentry.watch_path().write_text("{ not json", encoding="utf-8")
        out = boot_sentry.check_and_recover()
        self.assertFalse(out["recovered"])
        self.assertFalse(boot_sentry.armed())
        self.assertEqual(self.file.read_text(encoding="utf-8"), "PATCHED\n")

    def test_a_watch_that_is_valid_json_but_not_an_object_is_discarded(self):
        boot_sentry.watch_path().parent.mkdir(parents=True, exist_ok=True)
        boot_sentry.watch_path().write_text('["a list, not an object"]',
                                            encoding="utf-8")
        self.assertFalse(boot_sentry.check_and_recover()["recovered"])

    def test_disarming_clears_a_manual_rollback(self):
        boot_sentry.arm("abc123", str(self.file), str(self.backup))
        boot_sentry.disarm()
        self.assertFalse(boot_sentry.check_and_recover()["recovered"])


class IntegrationTest(Base):
    """The handshake as self_heal actually uses it."""

    def test_applying_a_patch_arms_and_confirming_retires_it(self):
        patch = {"ok": True, "path": str(self.file), "source": "ORIGINAL\n",
                 "patched": "ORIGINAL\n# fixed\n", "target": "ORIGINAL\n",
                 "replacement": "ORIGINAL\n# fixed\n",
                 "explanation": "Add the missing guard.", "line": 1}
        out = self_heal.apply_patch(patch)
        self.assertTrue(out["ok"], out.get("message"))
        self.assertTrue(boot_sentry.armed())
        self.assertTrue(boot_sentry.mark_healthy())
        self.assertEqual(self.file.read_text(encoding="utf-8"), "ORIGINAL\n# fixed\n")

    def test_a_patch_that_breaks_the_next_start_is_undone_on_it(self):
        """The whole point, end to end: patch applied, process never confirms,
        next start restores the original."""
        patch = {"ok": True, "path": str(self.file), "source": "ORIGINAL\n",
                 "patched": "ORIGINAL\n# broken in a way that killed the boot\n",
                 "target": "ORIGINAL\n",
                 "replacement": "ORIGINAL\n# broken in a way that killed the boot\n",
                 "explanation": "A fix that was not one.", "line": 1}
        self.assertTrue(self_heal.apply_patch(patch)["ok"])
        # ... the process dies here; it never calls mark_healthy ...
        out = boot_sentry.check_and_recover()
        self.assertTrue(out["recovered"])
        self.assertEqual(self.file.read_text(encoding="utf-8"), "ORIGINAL\n")


if __name__ == "__main__":
    unittest.main()
