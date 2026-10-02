"""
tests/test_qt_env.py — the contract for the headless-Qt bootstrap.

tests/qt_env.py exists because a Windows fatal exception during garbage
collection killed a whole CI run with no traceback and nothing failing.  These
tests pin the parts of it that are invisible until they are missing:

  * the widget tests import it *before* Qt, in every module that builds a real
    window;
  * a platform the developer picked is never overwritten, and the default is
    offscreen;
  * COM is asked for once, off Windows it is not asked for at all, and a COM
    that refuses is a quiet False rather than an exception;
  * nothing ever calls CoUninitialize(), because tearing the apartment down
    under Qt's Windows objects is the same crash one step later.
"""
import ast
import sys
import unittest
from pathlib import Path
from unittest import mock

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR))

import qt_env  # noqa: E402

#: Every test module that builds a real widget, and what Qt looks like in one.
WIDGET_TESTS = ("test_api_settings.py", "test_customize.py", "test_ui_redesign.py")
_QT_IMPORTS = ("import ui", "from PyQt6")


class PlatformPinTest(unittest.TestCase):
    def test_the_default_is_offscreen(self):
        # A runner with no session cannot draw; offscreen needs nothing.
        with mock.patch.dict(qt_env.os.environ, {}, clear=False):
            qt_env.os.environ.pop("QT_QPA_PLATFORM", None)
            self.assertEqual(qt_env.pin_platform(), "offscreen")
            self.assertEqual(qt_env.os.environ["QT_QPA_PLATFORM"], "offscreen")

    def test_a_platform_the_developer_chose_is_respected(self):
        # `QT_QPA_PLATFORM=windows python -m unittest ...` must still be able
        # to open a real window while working on the UI.
        with mock.patch.dict(qt_env.os.environ, {"QT_QPA_PLATFORM": "windows"}):
            self.assertEqual(qt_env.pin_platform(), "windows")

    def test_pinning_again_changes_nothing(self):
        with mock.patch.dict(qt_env.os.environ, {}, clear=False):
            qt_env.os.environ.pop("QT_QPA_PLATFORM", None)
            self.assertEqual(qt_env.pin_platform(), qt_env.pin_platform())

    def test_this_process_was_pinned_by_the_import(self):
        # Importing is the whole interface — no test may have to remember to
        # call anything before `import ui`.
        self.assertTrue(qt_env._prepared)
        self.assertEqual(qt_env.os.environ.get("QT_QPA_PLATFORM"),
                         qt_env.PLATFORM)


class ComApartmentTest(unittest.TestCase):
    def _attempts(self, coinitialize, platform="win32"):
        return qt_env._init_com(platform=platform, coinitialize=coinitialize)

    def test_a_thread_asks_for_an_apartment(self):
        calls = []

        def fake(ptr, flags):
            calls.append((ptr, flags))
            return 0                     # S_OK: created here

        self.assertTrue(self._attempts(fake))
        # None = the calling thread, 2 = COINIT_APARTMENTTHREADED, the model
        # Qt's Windows plugin uses.
        self.assertEqual(calls, [(None, 0x2)])

    def test_an_apartment_that_already_existed_still_counts(self):
        for hresult in (1,                      # S_FALSE: already STA here
                        0x80010106):            # RPC_E_CHANGED_MODE: already MTA
            with self.subTest(hresult=hex(hresult)):
                self.assertTrue(self._attempts(lambda *a: hresult))

    def test_a_refused_apartment_is_false_not_an_exception(self):
        def refuse(*a):
            return 0x80070005            # E_ACCESSDENIED

        def explode(*a):
            raise OSError("no ole32 here")

        self.assertFalse(self._attempts(refuse))
        self.assertFalse(self._attempts(explode))

    def test_com_is_not_touched_away_from_windows(self):
        def boom(*a):
            raise AssertionError("COM must not be touched here")

        for platform in ("linux", "darwin"):
            with self.subTest(platform=platform):
                self.assertFalse(self._attempts(boom, platform=platform))

    def test_prepare_only_asks_once(self):
        # A second import, or a stray call, must not re-open the apartment.
        calls = []
        real_init = qt_env._init_com
        saved = (qt_env._prepared, qt_env._com_ready)

        def fake_init(platform=None, coinitialize=None):
            calls.append(1)
            return True

        try:
            qt_env._init_com = fake_init
            qt_env._prepared, qt_env._com_ready = False, False
            self.assertTrue(qt_env.prepare_headless_qt())
            self.assertTrue(qt_env.prepare_headless_qt())
        finally:
            qt_env._init_com = real_init
            qt_env._prepared, qt_env._com_ready = saved
        self.assertEqual(len(calls), 1)

    def test_the_apartment_is_never_torn_down(self):
        # CoUninitialize() releases what Qt still holds: the same fatal
        # exception, one step later.  Read the code, not the prose — the
        # module docstring names the call it refuses to make, so this walks
        # the tree for the only COM entry point the module may reach.
        source = (TESTS_DIR / "qt_env.py").read_text(encoding="utf-8")
        touched = {node.attr
                   for node in ast.walk(ast.parse(source))
                   if isinstance(node, ast.Attribute)
                   and node.attr.lower().startswith("co")}
        self.assertEqual(touched, {"CoInitializeEx"})


class WidgetTestsUseItTest(unittest.TestCase):
    def test_every_widget_test_bootstraps_before_qt(self):
        for name in WIDGET_TESTS:
            with self.subTest(module=name):
                source = (TESTS_DIR / name).read_text(encoding="utf-8")
                self.assertIn("import qt_env", source)
                booted = source.index("import qt_env")
                for qt_import in _QT_IMPORTS:
                    if qt_import in source:
                        self.assertLess(booted, source.index(qt_import),
                                        msg=f"{qt_import} loads before the bootstrap")
                self.assertNotIn('setdefault("QT_QPA_PLATFORM"', source,
                                 msg="the platform is now qt_env's job")

    def test_the_helper_is_not_collected_as_a_test(self):
        # It has no tests to run and must not be discovered as a suite.
        self.assertFalse(Path(qt_env.__file__).name.startswith("test_"))


if __name__ == "__main__":
    unittest.main()
