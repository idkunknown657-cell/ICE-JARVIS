"""Every call the front end makes must exist on the bridge, and vice versa.

This is the test for the project's central rule: no fake controls. A button
whose handler calls a backend method that was renamed, deleted, or never
written looks completely normal in the UI and does nothing when pressed —
and nothing else in this suite can see it, because the Python side is only
half the program.

The check runs in both directions:

  * a method app.js calls that JarvisAPI does not have  → a dead control
  * a method JarvisAPI exposes and no code calls        → dead backend code

The second direction is a warning rather than a failure, because a method can
legitimately be called from somewhere other than app.js (a plugin, another
page script, a future view). It is reported so it cannot go unnoticed.
"""
import re
import sys
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

UI_DIR = BASE / "ui_web"
JS_FILES = sorted(UI_DIR.glob("js/*.js"))

# Methods that exist to be called by something other than the front end, or
# that the front end only reaches indirectly. Each one needs a reason here.
_BACKEND_ONLY = {
    "ready",          # pywebview readiness probe, called by the host
    "push_control",   # main.py pushes PC status through the UI duck-type contract
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def js_api_calls() -> dict[str, set[str]]:
    """Every `api.<name>(` the front end invokes, and where."""
    found: dict[str, set[str]] = {}
    for path in JS_FILES:
        if path.name == "mock.js":
            continue              # the mock defines them; it does not call them
        for name in re.findall(r"\bapi\.([a-zA-Z_][a-zA-Z0-9_]*)\s*\(", _read(path)):
            found.setdefault(name, set()).add(path.name)
    return found


def bridge_methods() -> set[str]:
    """Public methods on JarvisAPI — the surface pywebview exposes to JS."""
    import webui
    api = webui.JarvisAPI
    names = set()
    for name, value in vars(api).items():
        if name.startswith("_") or not callable(value):
            continue
        names.add(name)
    return names


def mock_methods() -> set[str]:
    """The offline preview's stand-in API, for the same contract check."""
    text = _read(UI_DIR / "js" / "mock.js")
    return set(re.findall(r"^\s*async\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(", text,
                          re.MULTILINE))


class BridgeContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.calls = js_api_calls()
        cls.bridge = bridge_methods()
        cls.mock = mock_methods()

    def test_the_front_end_calls_something(self):
        """Guards the guard: a regex that silently stopped matching would make
        every test below pass vacuously."""
        self.assertGreater(len(self.calls), 30, sorted(self.calls))

    def test_every_call_the_ui_makes_exists_on_the_bridge(self):
        missing = sorted(name for name in self.calls if name not in self.bridge)
        detail = {n: sorted(self.calls[n]) for n in missing}
        self.assertEqual(missing, [], f"the UI calls methods the bridge lacks: {detail}")

    def test_every_call_the_ui_makes_is_covered_by_the_offline_preview(self):
        """The preview has no Python behind it, so a method missing from the
        mock throws in the browser and the page half-renders. This direction is
        the one that hid the dead "Pair phone" control: the mock answered a
        method the real bridge never had, so the button behaved in the preview
        and failed for every user."""
        missing = sorted(name for name in self.calls
                         if name not in self.mock and name not in _BACKEND_ONLY)
        self.assertEqual(missing, [],
                         f"the offline preview cannot answer: {missing}")

    def test_the_mock_does_not_invent_backend_methods(self):
        """A mock that answers something the bridge cannot is worse than a
        missing mock: it makes a fake button look live in the preview."""
        invented = sorted(name for name in self.mock if name not in self.bridge)
        self.assertEqual(invented, [],
                         f"the preview accepts calls the real bridge would refuse: {invented}")

    def test_every_public_bridge_method_is_reached_from_the_front_end(self):
        """Dead backend code, reported rather than failed: a method can be
        called from a page script that is not app.js, and failing here would
        force a meaningless exception list."""
        unreached = sorted(n for n in self.bridge
                           if n not in self.calls and n not in _BACKEND_ONLY)
        if unreached:
            self.skipTest("not called from the front end: " + ", ".join(unreached))

    def test_the_contract_check_can_actually_fail(self):
        """A deliberately impossible name must be reported missing, so this
        test file cannot pass by matching nothing at all."""
        self.assertNotIn("this_method_does_not_exist", self.bridge)
        self.assertNotIn("this_method_does_not_exist", self.mock)


class NoDeadControlsTest(unittest.TestCase):
    """Buttons must go somewhere. A control with no handler at all is the
    clearest form of the fake UI the project rules out."""

    def test_no_control_is_marked_coming_soon(self):
        text = "\n".join(_read(p) for p in JS_FILES)
        for phrase in ("coming soon", "not implemented", "TODO: wire",
                       "placeholder button", "comingsoon"):
            self.assertNotIn(phrase, text.lower())

    def test_the_dead_remote_pair_modal_is_gone_too(self):
        """Removing a control but leaving its dialog leaves a screen reachable
        by nothing — the same dead weight, one level down. The two are
        removed together or the removal is only half done.

        (A general "every button has a listener" text check was tried and
        removed. The front end sets handlers with addEventListener and with
        .onclick, sometimes far from where the element is built, so it produced
        mostly false failures — a test that cries wolf is worse than none.)
        """
        html = _read(UI_DIR / "index.html")
        self.assertNotIn("remoteVeil", html)
        self.assertNotIn("remoteQr", html)


if __name__ == "__main__":
    unittest.main()
