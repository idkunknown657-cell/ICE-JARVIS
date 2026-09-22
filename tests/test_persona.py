"""Unit tests for core/persona.py — relationship continuity + lessons block."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.persona import (
    build_persona_block, lessons_block, relationship_context, companion_rules,
    style_block,
)


def _memory(name="Mark"):
    return {
        "identity": {"name": {"value": name}},
        "projects": {"jarvis": {"value": "screen-aware assistant"}},
        "sessions": [{"date": "2026-09-18",
                      "summary": "We polished the wallpaper tool."}],
        "lessons": {
            "corrections": {"value": "Always confirm before closing files."},
        },
    }


class PersonaBlockTest(unittest.TestCase):
    def test_block_contains_name_and_relationship(self):
        block = build_persona_block(_memory(), "JARVIS", user_name="Mark")
        self.assertIn("JARVIS", block)
        self.assertIn("personal AI companion", block)
        self.assertIn("Mark", block)          # the user's name
        self.assertIn("screen-aware assistant", block)   # standing project
        self.assertIn("wallpaper tool", block)           # last session

    def test_lessons_block_riders_through(self):
        block = build_persona_block(_memory(), "JARVIS")
        self.assertIn("confirm before closing files", block.lower())

    def test_lessons_block_empty_when_no_lessons(self):
        mem = _memory()
        mem["lessons"] = {}
        self.assertEqual(lessons_block(mem), "")

    def test_no_name_yet(self):
        mem = _memory("")
        block = relationship_context(mem, "JARVIS")
        self.assertIn("name isn't known yet", block)
        self.assertNotIn("Mark", block)

    def test_rules_never_canned_opener(self):
        rules = companion_rules("Mark")
        self.assertIn("never like a customer-service script", rules)
        self.assertIn("how can i assist", rules.lower())

    def test_soft_companion_layer_present(self):
        rules = companion_rules("Mark")
        # The soft layer: cute reactions + humour + the anti-overact leash.
        self.assertIn("SOFT COMPANION LAYER", rules)
        self.assertIn("DO NOT OVERACT", rules)
        self.assertIn("One emotional beat per reply", rules)

    def test_style_block_empty_without_style_facts(self):
        self.assertEqual(style_block(_memory()), "")

    def test_style_block_renders_learned_preferences(self):
        mem = _memory()
        mem["preferences"] = {"style_humor": {"value": "Loves dry one-liners"},
                               "other": {"value": "not a style fact"}}
        block = style_block(mem)
        self.assertIn("Loves dry one-liners", block)
        self.assertNotIn("not a style fact", block)

    def test_persona_block_includes_style(self):
        mem = _memory()
        mem["preferences"] = {"style_length": {"value": "Prefers short answers"}}
        block = build_persona_block(mem, "JARVIS")
        self.assertIn("Prefers short answers", block)


if __name__ == "__main__":
    unittest.main()