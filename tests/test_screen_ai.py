import unittest
from unittest.mock import patch

import actions.screen_ai as sai


class RecorderPAG:
    def __init__(self):
        self.calls = []
    def click(self, *a, **k):
        self.calls.append(("click", a, k))
    def moveTo(self, *a, **k):
        self.calls.append(("moveTo", a, k))


BTN = {"type": "Button", "name": "Save", "aid": "", "x": 100, "y": 200,
       "w": 80, "h": 24}
EDIT = {"type": "Edit", "name": "Prompt", "aid": "", "x": 10, "y": 500,
        "w": 700, "h": 30}


class PickTests(unittest.TestCase):
    def test_center(self):
        self.assertEqual(sai._center(BTN), (140, 212))

    def test_exact_single(self):
        elems = [BTN, EDIT]
        hit, many, amb = sai._pick_match("Save", elems)
        self.assertFalse(amb)
        self.assertIs(hit, BTN)

    def test_exact_multiple_ambiguous(self):
        elems = [BTN, {**BTN, "name": "Save", "x": 999}]
        hit, many, amb = sai._pick_match("Save", elems)
        self.assertTrue(amb)
        self.assertIsNone(hit)
        self.assertEqual(len(many), 2)

    def test_fuzzy_single(self):
        elems = [EDIT, BTN]
        hit, many, amb = sai._pick_match("prompt", elems)
        self.assertIs(hit, EDIT)
        self.assertFalse(amb)

    def test_no_match(self):
        elems = [BTN, EDIT]
        hit, many, amb = sai._pick_match("Delete", elems)
        self.assertIsNone(hit)
        self.assertEqual(many, [])
        self.assertFalse(amb)


class ResolveTests(unittest.TestCase):
    def _mock_uia(self, elements):
        patch_uia = patch("actions.screen_ai._uia_available", return_value=True)
        patch_inv = patch("actions.screen_ai._uia_inventory", return_value=(elements, None))
        patch_uia.start()
        patch_inv.start()
        self.addCleanup(patch_uia.stop)
        self.addCleanup(patch_inv.stop)

    def test_hit(self):
        self._mock_uia([BTN, EDIT])
        mode, payload = sai._resolve({}, "Save")
        self.assertEqual(mode, "hit")
        self.assertIs(payload, BTN)

    def test_many(self):
        self._mock_uia([BTN, {**BTN, "x": 5}])
        mode, payload = sai._resolve({}, "Save")
        self.assertEqual(mode, "many")
        self.assertEqual(len(payload), 2)

    def test_none(self):
        self._mock_uia([BTN])
        mode, payload = sai._resolve({}, "Delete")
        self.assertEqual(mode, "none")
        self.assertIn("Save", payload)   # hints mention what IS available

    def test_fallback_when_empty_tree(self):
        # UIA available but tree empty → vision-only path
        patch_uia = patch("actions.screen_ai._uia_available", return_value=True)
        patch_inv = patch("actions.screen_ai._uia_inventory", return_value=([], None))
        patch_uia.start()
        patch_inv.start()
        self.addCleanup(patch_uia.stop)
        self.addCleanup(patch_inv.stop)
        mode, payload = sai._resolve({}, "some game button")
        self.assertEqual(mode, "fallback")

    def test_fallback_when_no_uia(self):
        patch_uia = patch("actions.screen_ai._uia_available", return_value=False)
        patch_uia.start()
        self.addCleanup(patch_uia.stop)
        mode, payload = sai._resolve({}, "anything")
        self.assertEqual(mode, "fallback")


class ClickTests(unittest.TestCase):
    def test_uia_click_precise(self):
        rec = RecorderPAG()
        with patch("actions.screen_ai._uia_available", return_value=True), \
             patch("actions.screen_ai._uia_inventory", return_value=([BTN], None)), \
             patch("actions.screen_ai._element_at_point", return_value="Save"), \
             patch.object(sai, "_PAG", rec):
            out = sai._click_action({"item": "Save"})
        self.assertEqual(rec.calls, [("click", (140, 212), {"button": "left", "clicks": 1})])
        self.assertIn("verified", out)

    def test_uia_click_warns_on_mismatch(self):
        rec = RecorderPAG()
        with patch("actions.screen_ai._uia_available", return_value=True), \
             patch("actions.screen_ai._uia_inventory", return_value=([BTN], None)), \
             patch("actions.screen_ai._element_at_point", return_value="OtherThing"), \
             patch.object(sai, "_PAG", rec):
            out = sai._click_action({"item": "Save"})
        self.assertIn("WARNING", out)

    def test_ambiguous_refuses_to_guess(self):
        rec = RecorderPAG()
        elems = [BTN, {**BTN, "x": 5}]
        with patch("actions.screen_ai._uia_available", return_value=True), \
             patch("actions.screen_ai._uia_inventory", return_value=(elems, None)), \
             patch.object(sai, "_PAG", rec):
            out = sai._click_action({"item": "Save"})
        self.assertEqual(rec.calls, [])
        self.assertIn("ambiguous", out)

    def test_not_found_does_not_click(self):
        rec = RecorderPAG()
        with patch("actions.screen_ai._uia_available", return_value=True), \
             patch("actions.screen_ai._uia_inventory", return_value=([EDIT], None)), \
             patch.object(sai, "_PAG", rec):
            out = sai._click_action({"item": "Delete"})
        self.assertEqual(rec.calls, [])
        self.assertIn("did not click", out)

    def test_vision_fallback_when_no_uia(self):
        with patch("actions.screen_ai._uia_available", return_value=False), \
             patch("actions.screen_ai._vision_click", return_value="vision clicked the ghost"):
            out = sai._click_action({"item": "the ghost"})
        self.assertEqual(out, "vision clicked the ghost")

    def test_right_and_double(self):
        rec = RecorderPAG()
        with patch("actions.screen_ai._uia_available", return_value=True), \
             patch("actions.screen_ai._uia_inventory", return_value=([BTN], None)), \
             patch("actions.screen_ai._element_at_point", return_value=""), \
             patch.object(sai, "_PAG", rec):
            sai._click_action({"item": "Save"}, button="right")
            sai._click_action({"item": "Save"}, clicks=2)
        self.assertEqual(rec.calls[0][2]["button"], "right")
        self.assertEqual(rec.calls[1][2]["clicks"], 2)


class FindTests(unittest.TestCase):
    def test_not_found_never_consults_vision(self):
        boom = RuntimeError("vision must not be consulted when UIA has elements")
        with patch("actions.screen_ai._uia_available", return_value=True), \
             patch("actions.screen_ai._uia_inventory", return_value=([EDIT], None)), \
             patch("actions.screen_ai._vision_find", side_effect=boom):
            out = sai._find_action({"item": "Delete"})
        self.assertIn("was not found", out)

    def test_vision_when_no_uia(self):
        with patch("actions.screen_ai._uia_available", return_value=False), \
             patch("actions.screen_ai._vision_find", return_value=(400, 300)):
            out = sai._find_action({"item": "ghost"})
        self.assertIn("400,300", out)

    def test_uia_find_reports_centre(self):
        with patch("actions.screen_ai._uia_available", return_value=True), \
             patch("actions.screen_ai._uia_inventory", return_value=([BTN], None)):
            out = sai._find_action({"item": "Save"})
        self.assertIn("140,212", out)


class DispatcherTests(unittest.TestCase):
    def test_alias_normalisation(self):
        self.assertIn("can:", sai.screen_ai({"action": "frobnicate"}))

    def test_missing_item_message(self):
        out = sai.screen_ai({"action": "click"})
        self.assertIn("item", out)

    def test_aliases(self):
        rec = RecorderPAG()
        with patch("actions.screen_ai._uia_available", return_value=True), \
             patch("actions.screen_ai._uia_inventory", return_value=([EDIT], None)), \
             patch.object(sai, "_PAG", rec):
            sai.screen_ai({"action": "frobnicate"})
            rela = sai.screen_ai({"action": "right_click", "item": "Prompt"})
            dbl = sai.screen_ai({"action": "double click", "item": "Prompt"})
            hov = sai.screen_ai({"action": "point", "item": "Prompt"})
        self.assertIn("Right-clicked", rela)
        self.assertIn("Double-clicked", dbl)
        self.assertIn("Pointer", hov)


if __name__ == "__main__":
    unittest.main()