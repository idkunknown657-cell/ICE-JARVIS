"""Regression test: every shipped tool declaration must be Live-API safe.

One ARRAY-without-items in a single plugin used to fail the entire Live
session setup (server closes with 1007), leaving the app at a fake LISTENING
state that could never speak. The sanitizer in main.py must repair or drop
such declarations, never pass them through.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main as M


def _all_shipped_decls():
    base = Path(__file__).resolve().parent.parent
    inline_names = {t["name"] for t in M.TOOL_DECLARATIONS}
    actions = M.discover_actions(base / "actions", reserved_names=inline_names,
                                 logger=lambda m: None)
    plugins = M.discover_plugins(base / "plugins",
                                 core_tool_names=inline_names | actions.names(),
                                 logger=lambda m: None, notify=lambda m: None)
    return (M.TOOL_DECLARATIONS + actions.get_tool_declarations()
            + plugins.get_tool_declarations())


class SanitizeToolsTest(unittest.TestCase):
    def test_every_array_has_items_after_sanitize(self):
        decls = M._sanitize_tool_declarations(_all_shipped_decls())

        def check(node, where):
            self.assertIsInstance(node, dict, where)
            if str(node.get("type", "")).upper() == "ARRAY":
                self.assertIn("items", node, f"ARRAY without items: {where}")
                self.assertIsInstance(node["items"], dict)
            for k, v in (node.get("properties") or {}).items():
                check(v, f"{where}.{k}")
            if isinstance(node.get("items"), dict):
                check(node["items"], where + "[]")

        for d in decls:
            check(d["parameters"], d["name"])

    def test_all_shipped_plugins_load(self):
        base = Path(__file__).resolve().parent.parent
        plugins = M.discover_plugins(base / "plugins", core_tool_names=set(),
                                     logger=lambda m: None, notify=lambda m: None)
        for expected in ("clock_report", "lucky_pick", "quick_note", "unit_converter"):
            self.assertTrue(plugins.has(expected),
                            f"plugin {expected} failed to load")

    def test_sanitizer_repairs_array_without_items(self):
        bad = [{"name": "t", "description": "t",
                "parameters": {"type": "OBJECT",
                               "properties": {"x": {"type": "ARRAY"}}}}]
        out = M._sanitize_tool_declarations(bad)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["parameters"]["properties"]["x"]["items"],
                         {"type": "STRING"})

    def test_sanitizer_drops_nameless_and_non_object(self):
        bad = [{"description": "no name",
                "parameters": {"type": "OBJECT", "properties": {}}},
               {"name": "badparams", "description": "x",
                "parameters": {"type": "STRING"}}]
        self.assertEqual(M._sanitize_tool_declarations(bad), [])

    def test_sanitizer_never_raises(self):
        self.assertEqual(M._sanitize_tool_declarations(None), [])
        self.assertEqual(M._sanitize_tool_declarations([None, 42, "x"]), [])


if __name__ == "__main__":
    unittest.main()
