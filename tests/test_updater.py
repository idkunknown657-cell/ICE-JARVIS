"""Unit tests for core/updater.py — version logic, manifest check, staging and
the restart-swap script. All network I/O is mocked; no real calls."""
import io
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from core import updater


class _Resp:
    """Fake requests.Response: JSON body for manifests, streamed bytes for the
    zip download, context-manager shape for `with requests.get(...) as r`."""
    def __init__(self, content=b"", status=200, json_data=None):
        self._content = content
        self.status_code = status
        self._json = json_data

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    @property
    def headers(self):
        return {"content-length": str(len(self._content))}

    def iter_content(self, size):
        yield self._content

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class VersionTest(unittest.TestCase):
    def test_is_newer_numeric_compare(self):
        self.assertTrue(updater.is_newer("2026.9.21", "2026.9.20"))
        self.assertTrue(updater.is_newer("2026.10.0", "2026.9.20"))
        self.assertFalse(updater.is_newer("2026.9.20", "2026.9.20"))
        self.assertFalse(updater.is_newer("2026.9.19", "2026.9.20"))

    def test_is_newer_tolerates_garbage(self):
        self.assertTrue(updater.is_newer("2026.9.21-beta", "2026.9.20"))
        self.assertFalse(updater.is_newer("", "2026.9.20"))
        self.assertFalse(updater.is_newer("garbage", "garbage"))

    def test_current_version_read_and_fallback(self):
        tmp = tempfile.TemporaryDirectory()
        vf = Path(tmp.name) / "VERSION"
        with mock.patch.object(updater, "VERSION_FILE", vf):
            vf.write_text("2026.9.20\n", encoding="utf-8")
            self.assertEqual(updater.current_version(), "2026.9.20")
            vf.unlink()
            self.assertEqual(updater.current_version(), "0.0.0")
        tmp.cleanup()


class CheckTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vf = Path(self.tmp.name) / "VERSION"
        self.vf.write_text("2026.9.20", encoding="utf-8")
        self.patches = [
            mock.patch.object(updater, "VERSION_FILE", self.vf),
            mock.patch.object(updater, "_manifest_url",
                              lambda: "https://host.example/jarvis/update.json"),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)

    def test_update_available(self):
        manifest = {"version": "2026.9.21", "notes": "softer voice",
                    "url": "https://host.example/jarvis_update.zip",
                    "sha256": "abc"}
        with mock.patch("requests.get", return_value=_Resp(json_data=manifest)):
            r = updater.check()
        self.assertTrue(r["ok"])
        self.assertTrue(r["update_available"])
        self.assertEqual(r["current"], "2026.9.20")
        self.assertEqual(r["latest"], "2026.9.21")

    def test_same_version_not_available(self):
        manifest = {"version": "2026.9.20",
                    "url": "https://host.example/z.zip"}
        with mock.patch("requests.get", return_value=_Resp(json_data=manifest)):
            r = updater.check()
        self.assertTrue(r["ok"])
        self.assertFalse(r["update_available"])

    def test_non_json_manifest_never_raises(self):
        with mock.patch("requests.get", return_value=_Resp(content=b"nope")):
            r = updater.check()
        self.assertFalse(r["ok"])
        self.assertIn("JSON", r["err"])

    def test_unreachable_manifest_never_raises(self):
        with mock.patch("requests.get", side_effect=TimeoutError("slow")):
            r = updater.check()
        self.assertFalse(r["ok"])
        self.assertIn("unreachable", r["err"])

    def test_insecure_update_url_refused(self):
        manifest = {"version": "2026.9.21", "url": "http://host.example/z.zip"}
        with mock.patch("requests.get", return_value=_Resp(json_data=manifest)):
            r = updater.check()
        self.assertTrue(r["ok"])
        self.assertFalse(r["update_available"])
        self.assertIn("https", r["err"])


class DownloadTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.staging = Path(self.tmp.name) / "update_staging"
        self.vf = Path(self.tmp.name) / "VERSION"
        self.vf.write_text("2026.9.20", encoding="utf-8")
        for attr, val in (("STAGING_DIR", self.staging),
                          ("STAGED_MARK", self.staging / "payload"),
                          ("VERSION_FILE", self.vf)):
            p = mock.patch.object(updater, attr, val)
            p.start()
            self.addCleanup(p.stop)

    def _manifest(self, zip_bytes: bytes, sha: str) -> dict:
        return {"version": "2026.9.21",
                "url": "https://host.example/jarvis_update.zip",
                "sha256": sha}

    def test_flat_zip_staged(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("JARVIS.exe", b"fake exe")
            z.writestr("ui_web/index.html", b"<html></html>")
        zip_bytes = buf.getvalue()
        manifest = {
            "version": "2026.9.21",
            "url": "https://host.example/jarvis_update.zip",
            "sha256": __import__("hashlib").sha256(zip_bytes).hexdigest(),
        }
        responses = [_Resp(json_data=manifest), _Resp(content=zip_bytes)]
        with mock.patch("requests.get", side_effect=responses):
            r = updater.download_and_stage()
        self.assertTrue(r["ok"], r.get("err"))
        payload = Path(r["path"])
        self.assertTrue((payload / "JARVIS.exe").exists())
        self.assertEqual((payload / "STAGED_VERSION").read_text(encoding="utf-8"),
                         "2026.9.21")
        self.assertTrue(updater.pending())

    def test_wrapped_zip_root_is_stripped(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("JARVIS/JARVIS.exe", b"fake exe")
        zip_bytes = buf.getvalue()
        manifest = {"version": "2026.9.21",
                    "url": "https://host.example/jarvis_update.zip",
                    "sha256": __import__("hashlib").sha256(zip_bytes).hexdigest()}
        with mock.patch("requests.get",
                        side_effect=[_Resp(json_data=manifest),
                                     _Resp(content=zip_bytes)]):
            r = updater.download_and_stage()
        self.assertTrue(r["ok"], r.get("err"))
        self.assertTrue((Path(r["path"]) / "JARVIS.exe").exists())

    def test_sha_mismatch_refused(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("JARVIS.exe", b"fake exe")
        manifest = {"version": "2026.9.21",
                    "url": "https://host.example/jarvis_update.zip",
                    "sha256": "0" * 64}
        with mock.patch("requests.get",
                        side_effect=[_Resp(json_data=manifest),
                                     _Resp(content=buf.getvalue())]):
            r = updater.download_and_stage()
        self.assertFalse(r["ok"])
        self.assertIn("sha256", r["err"])

    def test_up_to_date_short_circuits(self):
        manifest = {"version": "2026.9.20",
                    "url": "https://host.example/jarvis_update.zip"}
        with mock.patch("requests.get",
                        return_value=_Resp(json_data=manifest)) as g:
            r = updater.download_and_stage()
        self.assertFalse(r["ok"])
        self.assertIn("up to date", r["err"])
        g.assert_called_once()      # manifest only — no zip download


@unittest.skipUnless(os.name == "nt", "the restart-swap bat is Windows-only")
class ApplyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.staging = Path(self.tmp.name) / "update_staging"
        payload = self.staging / "payload" / "JARVIS"
        payload.mkdir(parents=True)
        (payload / "JARVIS.exe").write_bytes(b"new exe")
        for attr, val in (("STAGING_DIR", self.staging),
                          ("STAGED_MARK", self.staging / "payload"),
                          ("APPLY_BAT", Path(self.tmp.name) / "apply_update.bat")):
            p = mock.patch.object(updater, attr, val)
            p.start()
            self.addCleanup(p.stop)

    def test_bat_written_and_spawned_detached(self):
        with mock.patch.object(updater.subprocess, "Popen") as popen:
            r = updater.apply_on_restart()
        self.assertTrue(r["ok"], r.get("err"))
        self.assertTrue(r["relaunch"])
        popen.assert_called_once()
        bat = Path(updater.APPLY_BAT).read_text(encoding="utf-8")
        self.assertIn("tasklist /FI", bat)
        self.assertIn(f"PID eq {os.getpid()}", bat)
        self.assertIn("robocopy", bat)
        self.assertIn("start \"\"", bat)
        # /E copy — never /MIR, the user's config must survive updates
        self.assertNotIn("/MIR", bat)

    def test_nothing_staged_is_clean_no(self):
        p = mock.patch.object(updater, "STAGED_MARK",
                              Path(self.tmp.name) / "nowhere")
        p.start()
        self.addCleanup(p.stop)
        r = updater.apply_on_restart()
        self.assertFalse(r["ok"])
        self.assertIn("nothing staged", r["err"])


if __name__ == "__main__":
    unittest.main()
