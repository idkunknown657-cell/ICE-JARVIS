"""Tests for the Windows packaging: the frozen app, the installer, and the rules
that stop the two from disagreeing.

Every test here exists because the packaging got it wrong at least once:

  * the exe names in `JARVIS.spec` and `ICE-Setup.iss` have to match, or the
    installer ships shortcuts to a file that is not there;
  * `actions/` must travel as *files* — `core/action_loader.py` discovers tools by
    scanning that folder, so baking it in as compiled modules produced an
    installed app with 42 abilities missing and no error anywhere;
  * the dashboard's pages must travel with their module, not beside the exe;
  * nothing personal (api_keys.json, certs, memory state) may reach a download.

The tests read the packaging files directly: they are configuration, and the only
honest way to check configuration is to check what it says.
"""
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

SPEC_TEXT = (BASE / "installer" / "jarvis.spec").read_text(encoding="utf-8")
ISS_TEXT = (BASE / "installer" / "ICE-Setup.iss").read_text(encoding="utf-8")
WORKFLOW = BASE / ".github" / "workflows" / "release.yml"


def _load_builder():
    """Import installer/build_installer.py, which is a script, not a package."""
    path = BASE / "installer" / "build_installer.py"
    spec = importlib.util.spec_from_file_location("ice_build_installer", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


builder = _load_builder()


class SpecAndInstallerAgreeTest(unittest.TestCase):
    """The two halves meet at dist/JARVIS — they must agree on the file names."""

    def test_the_executable_names_match(self):
        for name in ("JARVIS", "JARVIS-Debug"):
            self.assertIn(f'name="{name}"', SPEC_TEXT)
            self.assertIn(name + ".exe", ISS_TEXT)

    def test_the_windowed_build_has_no_console_and_the_debug_one_does(self):
        self.assertIn("console=False", SPEC_TEXT)
        self.assertIn("console=True", SPEC_TEXT)

    def test_actions_travel_as_files_because_the_loader_scans_for_them(self):
        self.assertIn("actions", builder.DATA_DIRS)
        loader = (BASE / "core" / "action_loader.py").read_text(encoding="utf-8")
        self.assertIn('glob("*.py")', loader,
                      "if discovery stops scanning files, this build rule is wrong")

    def test_dashboard_pages_travel_with_their_module(self):
        # server.py resolves STATIC_DIR as Path(__file__).parent / "static".
        self.assertIn("dashboard/static", SPEC_TEXT)

    def test_the_version_module_does_not_keep_its_own_copy(self):
        from core import version
        declared = (BASE / "VERSION").read_text(encoding="utf-8").strip()
        self.assertEqual(version.APP_VERSION, declared)
        self.assertNotIn('APP_VERSION = "1.',
                         (BASE / "core" / "version.py").read_text(encoding="utf-8"))


class InstallerRulesTest(unittest.TestCase):
    """The promises the installer makes to a user's machine."""

    def test_it_installs_per_user_without_an_admin_prompt(self):
        self.assertIn("PrivilegesRequired=lowest", ISS_TEXT)
        self.assertIn(r"DefaultDirName={localappdata}\Programs\JARVIS", ISS_TEXT)

    def test_the_app_id_is_a_frozen_guid(self):
        # A changing AppId turns every upgrade into a second install.
        for line in ISS_TEXT.splitlines():
            if line.startswith("AppId="):
                self.assertIn("{9C1E4A73-5B2F-4C58-9E1D-3A7B6F0C2D84}", line)
                self.assertNotIn("{code:", line)
                break
        else:
            self.fail("AppId is missing")

    def test_user_data_is_never_shipped(self):
        files_section = ISS_TEXT.split("[Files]", 1)[1].split("[Icons]", 1)[0]
        self.assertIn("config\\api_keys.json", files_section)   # as an Excludes entry
        for rel in builder.FORBIDDEN_IN_PAYLOAD:
            self.assertNotIn(f'Source: "{{#SourceDir}}\\{rel}"', files_section)

    def test_the_uninstaller_keeps_your_data_unless_you_say_otherwise(self):
        code = ISS_TEXT.split("[Code]", 1)[1]
        self.assertIn("MB_DEFBUTTON2", code)

    def test_it_will_install_the_webview2_runtime_when_it_is_missing(self):
        # Without WebView2 there is no window at all, so it is checked before the
        # install rather than explained afterwards.
        self.assertIn("{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}", ISS_TEXT)
        self.assertIn("WebView2Missing", ISS_TEXT)
        self.assertIn("MicrosoftEdgeWebview2Setup.exe", ISS_TEXT)
        self.assertIn("go.microsoft.com", builder.WEBVIEW2_URL)

    def test_the_installer_offers_a_desktop_shortcut_and_a_debug_console(self):
        self.assertIn("desktopicon", ISS_TEXT)
        self.assertIn("JARVIS-Debug.exe", ISS_TEXT)


class ManifestAndVersionTest(unittest.TestCase):

    def test_quad_fills_the_windows_version_resource(self):
        self.assertEqual(builder.quad("1.1.0"), "1.1.0.0")
        self.assertEqual(builder.quad("2"), "2.0.0.0")
        self.assertEqual(builder.quad("1.1.0.4"), "1.1.0.4")

    def test_legacy_modules_are_excluded_on_purpose_and_named(self):
        from core import selftest
        self.assertIn("core.avatar", selftest.NOT_SHIPPED)
        self.assertTrue(selftest.NOT_SHIPPED["core.avatar"].strip())
        self.assertNotIn("core.avatar", selftest.shipped_modules(["main", "core.avatar"]))

    def test_app_modules_walks_the_packages_and_skips_junk(self):
        from core import selftest
        names = selftest.app_modules(BASE)
        self.assertIn("main", names)
        self.assertIn("webui", names)
        self.assertIn("actions.browser_control", names)
        self.assertFalse([n for n in names if "__pycache__" in n or n.startswith("tests")])

    def test_the_release_workflow_publishes_the_installer_on_a_tag(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn('tags: ["v*"]', text)
        self.assertIn("build_installer.py", text)
        self.assertIn("ICE-Setup.exe", text)
        self.assertIn("contents: write", text)
        # The frozen build is verified before it is uploaded.
        self.assertIn("--selftest", text)


class SelfTestCheckerTest(unittest.TestCase):
    """A checker that cannot fail is worse than none, so both answers are tested."""

    def _fake_install(self, tmp: str) -> Path:
        root = Path(tmp)
        for rel in ("ui_web/index.html", "ui_web/css/style.css", "ui_web/js/app.js",
                    "ui_web/js/avatar.js", "assets/jarvis.ico", "VERSION"):
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("x", encoding="utf-8")
        return root

    def test_a_complete_build_passes(self):
        from core import selftest
        with tempfile.TemporaryDirectory() as tmp:
            root = self._fake_install(tmp)
            (root / selftest.MANIFEST_NAME).write_text(
                json.dumps({"modules": ["json", "math"]}), encoding="utf-8")
            self.assertEqual(selftest.run(root, verbose=False), 0)

    def test_a_missing_manifest_fails(self):
        from core import selftest
        with tempfile.TemporaryDirectory() as tmp:
            root = self._fake_install(tmp)
            self.assertEqual(selftest.run(root, verbose=False), 1)

    def test_a_module_the_build_claims_is_checked(self):
        from core import selftest
        with tempfile.TemporaryDirectory() as tmp:
            root = self._fake_install(tmp)
            (root / selftest.MANIFEST_NAME).write_text(
                json.dumps({"modules": ["definitely_not_a_module_xyz"]}),
                encoding="utf-8")
            self.assertEqual(selftest.run(root, verbose=False), 1)

    def test_a_missing_asset_fails(self):
        from core import selftest
        with tempfile.TemporaryDirectory() as tmp:
            root = self._fake_install(tmp)
            (root / "ui_web" / "js" / "app.js").unlink()
            (root / selftest.MANIFEST_NAME).write_text(
                json.dumps({"modules": []}), encoding="utf-8")
            self.assertEqual(selftest.run(root, verbose=False), 1)


class BrowserComponentsTest(unittest.TestCase):

    def setUp(self):
        from core import browser_deps
        self.mod = browser_deps
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch.dict(os.environ,
                                  {"PLAYWRIGHT_BROWSERS_PATH": self.tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_an_empty_cache_reports_everything_missing(self):
        self.assertEqual(sorted(self.mod.missing()), sorted(self.mod.BROWSERS))
        self.assertFalse(self.mod.ready())

    def test_an_unpacked_browser_counts_as_installed(self):
        (Path(self.tmp.name) / "chromium-1234").mkdir()
        self.assertIn("chromium", self.mod.installed())
        self.assertEqual(self.mod.missing(), ["firefox"])

    def test_installing_nothing_is_a_success_not_a_download(self):
        for name in self.mod.BROWSERS:
            (Path(self.tmp.name) / f"{name}-1").mkdir()
        result = self.mod.install()
        self.assertTrue(result["ok"])
        self.assertEqual(result["installed"], [])
        self.assertEqual(result["detail"], "already present")

    def test_no_driver_is_reported_in_words(self):
        with mock.patch.object(self.mod, "_command", return_value=None):
            result = self.mod.install()
        self.assertFalse(result["ok"])
        self.assertIn("not bundled", result["err"])

    def test_a_bad_download_path_never_raises(self):
        with mock.patch.object(self.mod, "_command", return_value=["nope-not-real"]):
            result = self.mod.install()
        self.assertFalse(result["ok"])
        self.assertTrue(result["err"])
        for key in ("ok", "err", "installed", "detail"):
            self.assertIn(key, result)

    def test_only_known_browsers_are_accepted(self):
        with mock.patch.object(self.mod, "_command", return_value=None) as cmd:
            self.mod.install(names=["malware", "chromium"])
        # "malware" is dropped; chromium is still wanted, so the command was built.
        self.assertEqual(len(cmd.call_args[0][0]), 1)


if __name__ == "__main__":
    unittest.main()
