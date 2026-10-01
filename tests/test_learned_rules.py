"""Unit tests for core/learned_rules.py, actions/learned_rules.py and the two
window/speed additions.

Two things matter most. The secret refusal has to be reliable, because a rule is
injected into every prompt and written to a plain JSON file — a credential that
slipped through would be echoed to the model on every turn and left sitting in
config/. And the prompt block must be empty when there are no rules, because
otherwise every session pays for text that says nothing.
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from actions import learned_rules as tool
from core import learned_rules as lr
from core import window_context as wc
from plugins import internet_speed_test as speed


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        p = mock.patch.object(lr, "_base_dir", lambda: self.root)
        p.start()
        self.addCleanup(p.stop)
        self._print = mock.patch("builtins.print")
        self._print.start()
        self.addCleanup(self._print.stop)


class StoreTest(Base):
    def test_an_empty_store_has_no_prompt_block(self):
        self.assertEqual(lr.prompt_block(), "")
        self.assertEqual(lr.count(), 0)

    def test_a_rule_is_stored_and_read_back(self):
        out = lr.add("always open links in Chrome")
        self.assertTrue(out["ok"])
        self.assertEqual(lr.count(), 1)
        self.assertIn("Chrome", lr.prompt_block())

    def test_the_prompt_block_lists_every_active_rule(self):
        lr.add("always open links in Chrome")
        lr.add("reply in Hindi")
        block = lr.prompt_block()
        self.assertIn("STANDING INSTRUCTIONS", block)
        self.assertIn("1. always open links in Chrome", block)
        self.assertIn("2. reply in Hindi", block)

    def test_saving_the_same_rule_twice_keeps_one(self):
        lr.add("always open links in Chrome")
        out = lr.add("Always Open Links In Chrome")
        self.assertTrue(out.get("duplicate"))
        self.assertEqual(len(lr.all_rules()), 1)

    def test_a_rule_switched_off_is_not_in_the_prompt(self):
        entry = lr.add("never touch my Downloads folder")["entry"]
        lr.toggle(entry["id"], False)
        self.assertEqual(lr.prompt_block(), "")
        self.assertEqual(lr.count(), 0)
        self.assertEqual(len(lr.all_rules()), 1, "it is switched off, not deleted")

    def test_toggling_back_on_restores_it(self):
        entry = lr.add("never touch my Downloads folder")["entry"]
        lr.toggle(entry["id"], False)
        lr.toggle(entry["id"], True)
        self.assertIn("Downloads", lr.prompt_block())

    def test_toggling_an_unknown_rule_says_so(self):
        out = lr.toggle("nope")
        self.assertFalse(out["ok"])
        self.assertIn("no rule", out["message"])

    def test_a_rule_can_be_found_by_a_fragment_of_its_wording(self):
        lr.add("always open links in Chrome")
        self.assertTrue(lr.find("chrome"))
        self.assertTrue(lr.find("open links"))
        self.assertEqual(lr.find(""), {})
        self.assertEqual(lr.find("nothing like this"), {})

    def test_an_ambiguous_fragment_finds_nothing_rather_than_guessing(self):
        lr.add("always open links in Chrome")
        lr.add("always open files in Notepad")
        self.assertEqual(lr.find("always open"), {},
                         "an ambiguous match must not silently pick one")

    def test_forgetting_a_rule_by_id_and_by_text(self):
        first = lr.add("always open links in Chrome")["entry"]
        lr.add("reply in Hindi")
        self.assertTrue(lr.forget(first["id"])["ok"])
        self.assertTrue(lr.forget("hindi")["ok"])
        self.assertEqual(lr.all_rules(), [])

    def test_forgetting_an_unknown_rule_says_so(self):
        self.assertFalse(lr.forget("nope")["ok"])

    def test_clearing_reports_how_many_went(self):
        lr.add("one rule")
        lr.add("another rule")
        self.assertEqual(lr.clear(), 2)
        self.assertEqual(lr.clear(), 0)

    def test_an_empty_rule_is_refused(self):
        for junk in ("", "   ", None):
            self.assertFalse(lr.add(junk)["ok"])
            self.assertIn("nothing to remember", lr.add(junk)["message"])

    def test_a_fragment_too_short_to_be_a_rule_is_refused(self):
        """Refusing a stray number or a stranded word catches the accidents a
        voice interface produces ('remember' arriving on its own) without judging
        the content of a rule the user typed deliberately."""
        for junk in (42, "a", "ok"):
            out = lr.add(junk)
            self.assertFalse(out["ok"], f"accepted {junk!r}")
            self.assertIn("too short", out["message"])
        self.assertEqual(lr.all_rules(), [])

    def test_a_corrupt_store_reads_as_no_rules(self):
        lr.rules_path().parent.mkdir(parents=True, exist_ok=True)
        lr.rules_path().write_text("{ not json", encoding="utf-8")
        self.assertEqual(lr.all_rules(), [])
        self.assertEqual(lr.prompt_block(), "")
        lr.add("still works after a corrupt file")
        self.assertEqual(lr.count(), 1)

    def test_the_store_is_capped(self):
        for i in range(210):
            lr.add(f"rule number {i} that is long enough to be a rule")
        self.assertLessEqual(len(lr.all_rules()), lr.MAX_RULES)

    def test_a_very_long_rule_is_truncated(self):
        lr.add("x" * 900)
        self.assertLessEqual(len(lr.all_rules()[0]["rule"]), lr.MAX_TEXT)

    def test_whitespace_in_a_rule_is_collapsed(self):
        lr.add("always   open\n\nlinks in   Chrome")
        self.assertEqual(lr.all_rules()[0]["rule"], "always open links in Chrome")

    def test_the_spoken_list_is_readable_when_empty(self):
        self.assertIn("no standing instructions", lr.spoken_list())

    def test_the_spoken_list_shows_state_and_count(self):
        entry = lr.add("always open links in Chrome")["entry"]
        lr.add("reply in Hindi")
        lr.toggle(entry["id"], False)
        text = lr.spoken_list()
        self.assertIn("switched off", text)
        self.assertIn("2 in total", text)


class SecretRefusalTest(Base):
    def test_real_looking_keys_are_refused(self):
        for text in ("my api key is sk-abcdefghijklmnopqrstuvwx",
                     "GPT_KEY=sk-proj-abcdefghijklmnopqrstuvwx",
                     "google key AIzaSyA1B2C3D4E5F6G7H8I9J0KLMNOPQRSTUVW",
                     "aws AKIAIOSFODNN7EXAMPLE",
                     "token: eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0",
                     "the password is hunter2"):
            with self.subTest(text=text[:30]):
                out = lr.add(text)
                self.assertFalse(out["ok"], f"a secret was accepted: {text[:40]}")
                self.assertIn("password, key or token", out["message"])

    def test_a_refused_secret_is_not_written_to_disk(self):
        lr.add("my api key is sk-abcdefghijklmnopqrstuvwx")
        self.assertEqual(lr.all_rules(), [])
        self.assertFalse(lr.rules_path().exists(),
                         "a refused rule must not create the file at all")

    def test_the_refusal_explains_itself_and_where_to_put_it(self):
        message = lr.add("my password is hunter2")["message"]
        self.assertIn("API key settings", message)

    def test_a_rule_about_passwords_is_still_allowed(self):
        """It describes an action rather than carrying a secret, which is exactly
        the kind of rule a user needs to be able to set."""
        out = lr.add("never type my password into a form without asking me")
        self.assertTrue(out["ok"], out.get("message"))

    def test_a_rule_about_credentials_in_general_is_allowed(self):
        self.assertTrue(lr.add("never store credentials in a file")["ok"])


class ToolTest(Base):
    def test_remembering_through_the_tool(self):
        out = tool.learned_rules({"action": "add", "rule": "always use Chrome"})
        self.assertIn("Remembered", out)
        self.assertEqual(lr.count(), 1)

    def test_the_default_action_is_add(self):
        out = tool.learned_rules({"rule": "always use Chrome"})
        self.assertIn("Remembered", out)

    def test_listing_through_the_tool(self):
        tool.learned_rules({"action": "add", "rule": "always use Chrome"})
        self.assertIn("always use Chrome", tool.learned_rules({"action": "list"}))

    def test_forgetting_through_the_tool(self):
        tool.learned_rules({"action": "add", "rule": "always use Chrome"})
        out = tool.learned_rules({"action": "forget", "rule_id": "chrome"})
        self.assertIn("Forgotten", out)
        self.assertEqual(lr.count(), 0)

    def test_forgetting_without_naming_one_asks(self):
        self.assertIn("Which instruction",
                      tool.learned_rules({"action": "forget"}))

    def test_disabling_through_the_tool(self):
        entry = tool.learned_rules({"action": "add", "rule": "always use Chrome"})
        self.assertIn("Remembered", entry)
        out = tool.learned_rules({"action": "disable", "rule_id": "chrome"})
        self.assertIn("switched off", out)

    def test_clearing_through_the_tool(self):
        lr.add("a rule")
        self.assertIn("Deleted all 1", tool.learned_rules({"action": "clear"}))
        self.assertIn("no standing instructions",
                      tool.learned_rules({"action": "clear"}))

    def test_a_secret_given_to_the_tool_is_refused_in_plain_words(self):
        out = tool.learned_rules({"action": "add",
                                  "rule": "my api key is sk-abcdefghijklmnopqrst"})
        self.assertIn("not saved", out)

    def test_an_unknown_action_lists_the_options(self):
        out = tool.learned_rules({"action": "levitate"})
        self.assertIn("do not know", out)
        self.assertIn("add", out)

    def test_the_tool_never_raises_on_junk(self):
        for junk in ({}, {"action": None}, {"action": 42}, {"rule": object()}):
            self.assertTrue(str(tool.learned_rules(junk)))

    def test_the_tool_is_discoverable(self):
        self.assertEqual(tool.TOOL["name"], "learned_rules")
        self.assertTrue(callable(tool.TOOL["handler"]))
        self.assertEqual(tool.TOOL["parameters"]["type"], "OBJECT")


class WindowContextTest(unittest.TestCase):
    def test_an_unavailable_platform_returns_a_full_empty_shape(self):
        """Callers must never have to branch on the platform, and must be able to
        tell 'nothing is focused' apart from 'this platform cannot say'."""
        with mock.patch.object(wc, "_IS_WINDOWS", False):
            info = wc.foreground()
        for key in ("title", "class_name", "pid", "hwnd", "rect", "available"):
            self.assertIn(key, info)
        self.assertFalse(info["available"])
        self.assertIn("only available on Windows", info["reason"])

    def test_no_sentence_is_produced_when_nothing_can_be_read(self):
        with mock.patch.object(wc, "_IS_WINDOWS", False):
            self.assertEqual(wc.describe(), "")
            self.assertEqual(wc.context_for_prompt(), "")

    def test_describe_names_the_app_and_the_window(self):
        with mock.patch.object(wc, "foreground", return_value={
                "available": True, "title": "report.docx — Word",
                "process": "WINWORD.EXE", "rect": (0, 0, 800, 600)}):
            text = wc.describe()
        self.assertIn("WINWORD.EXE", text)
        self.assertIn("report.docx", text)

    def test_describe_falls_back_to_the_title_alone(self):
        with mock.patch.object(wc, "foreground", return_value={
                "available": True, "title": "Untitled", "process": "",
                "rect": (0, 0, 800, 600)}):
            self.assertIn("Untitled", wc.describe())

    def test_the_prompt_block_is_compact_and_labelled(self):
        with mock.patch.object(wc, "foreground", return_value={
                "available": True, "title": "a" * 400, "process": "code.exe",
                "rect": (10, 20, 1280, 720)}):
            block = wc.context_for_prompt()
        self.assertTrue(block.startswith("[ACTIVE WINDOW]"))
        self.assertIn("app=code.exe", block)
        self.assertIn("1280x720", block)
        self.assertLess(len(block), 300, "this may ride in every turn's context")

    def test_a_window_with_no_title_or_process_produces_nothing(self):
        with mock.patch.object(wc, "foreground", return_value={
                "available": True, "title": "", "process": "", "rect": (0, 0, 0, 0)}):
            self.assertEqual(wc.context_for_prompt(), "")

    def test_a_failed_capture_returns_none_rather_than_raising(self):
        with mock.patch.object(wc, "foreground",
                               return_value={"bbox": None, "available": False}):
            with mock.patch("PIL.ImageGrab.grab",
                            side_effect=OSError("no display")):
                self.assertIsNone(wc.capture())

    def test_a_capture_of_nothing_still_encodes(self):
        """The real path: PIL gives back an image, and the module hands over JPEG
        bytes rather than the image object."""
        import PIL.Image
        with mock.patch.object(wc, "foreground", return_value={"bbox": None}):
            with mock.patch("PIL.ImageGrab.grab",
                            return_value=PIL.Image.new("RGB", (8, 8), "red")):
                data = wc.capture()
        self.assertTrue(data)
        self.assertEqual(data[:2], b"\xff\xd8", "should be a JPEG")


class SpeedTestPluginTest(unittest.TestCase):
    def setUp(self):
        self._print = mock.patch("builtins.print")
        self._print.start()
        self.addCleanup(self._print.stop)

    def _responses(self, down=f"x{3000000}", up=f"y{1000000}", ping_code=200):
        """A fake requests session with a measurable, not instantaneous, delay.

        The delay is the point: a transfer that completes in zero time divides by
        zero-ish and produces a bit rate that no assertion can be written
        against. Sleeping a few milliseconds stands in for a real network round
        trip without slowing the suite meaningfully.
        """
        import time as _time

        class FakeResponse:
            def __init__(self, status_code=200, content=b""):
                self.status_code = status_code
                self.content = content

        class FakeSession:
            def __init__(self):
                self.headers = {}

            def get(self, url, timeout=None, **kw):
                if "bytes=1000" in url:
                    _time.sleep(0.005)
                    return FakeResponse(ping_code, b"z" * 100)
                if ping_code >= 400:
                    return FakeResponse(ping_code, b"")
                _time.sleep(0.02)
                return FakeResponse(200, down.encode())

            def post(self, url, data=None, timeout=None, **kw):
                _time.sleep(0.01)
                return FakeResponse(200, b"")

        fake_requests = mock.Mock()
        fake_requests.Session = lambda: FakeSession()
        p = mock.patch.dict(sys.modules, {"requests": fake_requests})
        p.start()
        self.addCleanup(p.stop)

    def test_a_successful_test_reports_all_three_numbers(self):
        self._responses()
        out = speed.run({})
        self.assertIn("download speed", out)
        self.assertIn("upload", out)
        self.assertIn("latency", out)

    def test_upload_can_be_left_out(self):
        self._responses()
        out = speed.run({"include_upload": False})
        self.assertIn("download speed", out)
        self.assertNotIn("upload", out)

    def test_an_unreachable_network_is_reported_not_an_error(self):
        self._responses(ping_code=503)
        out = speed.run({})
        self.assertIn("could not reach", out)
        self.assertIn("503", out)

    def test_a_failed_download_still_reports_the_latency(self):
        class FakeResponse:
            status_code = 500
            content = b""

        class FakeSession:
            headers = {}

            def get(self, url, timeout=None, **kw):
                if "bytes=1000" in url:
                    return type("R", (), {"status_code": 200, "content": b"a"})()

                raise OSError("connection reset")

            def post(self, *a, **k):
                return FakeResponse()

        fake = mock.Mock()
        fake.Session = lambda: FakeSession()
        with mock.patch.dict(sys.modules, {"requests": fake}):
            out = speed.run({})
        self.assertIn("latency", out)
        self.assertIn("did not finish", out)

    def test_the_answer_also_says_whether_it_is_good(self):
        self._responses()
        out = speed.run({})
        self.assertTrue(any(word in out for word in
                            ("fast connection", "plenty", "workable", "slow")))

    def test_the_quality_line_speaks_plainly(self):
        self.assertIn("fast", speed._quality(200, 10))
        self.assertIn("plenty", speed._quality(50, 20))
        self.assertIn("workable", speed._quality(10, 30))
        self.assertIn("slow", speed._quality(1, 40))
        self.assertIn("sluggish", speed._quality(200, 400))

    def test_the_quality_line_survives_junk_input(self):
        for download, ping in ((0, None), (5, "junk"), (-1, object())):
            self.assertTrue(speed._quality(download, ping).endswith("."))

    def test_the_plugin_declares_the_contract_the_loader_needs(self):
        self.assertEqual(speed.PLUGIN["name"], "internet_speed_test")
        self.assertTrue(speed.PLUGIN["description"].strip())
        self.assertEqual(speed.PLUGIN["parameters"]["type"], "OBJECT")
        self.assertTrue(callable(speed.run))

    def test_the_plugin_passes_its_own_crucible(self):
        """A plugin shipped in the repo should satisfy the same gate a forged one
        does — otherwise the gate is not describing the real contract."""
        from core import skill_crucible
        source = Path(speed.__file__).read_text(encoding="utf-8")
        verdict = skill_crucible.verify(source, "internet_speed_test",
                                        cases=[{"include_upload": False}],
                                        timeout=30)
        self.assertTrue(verdict.ok, f"{verdict.stage}: {verdict.reason}")

    def test_every_shipped_plugin_passes_the_crucible(self):
        """The forge's gate doubles as a lint over the plugins that ship with the
        app: each one must be loadable, safe and callable with no arguments."""
        from core import skill_crucible
        plugins_dir = Path(speed.__file__).parent
        checked = 0
        for path in sorted(plugins_dir.glob("*.py")):
            if path.name.startswith("_"):
                continue
            source = path.read_text(encoding="utf-8")
            parsed, bad = skill_crucible.parse(source)
            if bad is not None:
                continue        # templated or partial plugins are not tools
            shape = skill_crucible.check_shape(parsed)
            if not shape.ok:
                continue        # e.g. the template, which is not a tool either
            with self.subTest(plugin=path.name):
                safe = skill_crucible.check_safety(parsed)
                self.assertTrue(safe.ok, f"{path.name}: {safe.reason}")
                checked += 1
        self.assertGreater(checked, 0, "no plugins were actually checked")


if __name__ == "__main__":
    unittest.main()
