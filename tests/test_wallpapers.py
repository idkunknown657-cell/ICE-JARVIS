"""Unit tests for actions/wallpapers.py — folder scanning, cycling, set,
preview and the tool declaration. The real desktop is never touched: the
wallpaper folder and the apply call are mocked."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from actions import wallpapers as wp


def _make_folder(files=("a.jpg", "b.png", "c.webp", "notes.txt")):
    tmp = tempfile.TemporaryDirectory()
    folder = Path(tmp.name)
    for f in files:
        (folder / f).touch()
    return tmp, folder


class FolderScanTest(unittest.TestCase):

    def test_images_only_are_listed(self):
        tmp, folder = _make_folder()
        with mock.patch("memory.config_manager.get_wallpaper_folder",
                        return_value=str(folder)):
            images = wp._images()
        names = [p.name for p in images]
        self.assertEqual(names, ["a.jpg", "b.png", "c.webp"])
        tmp.cleanup()

    def test_missing_folder_returns_empty(self):
        with mock.patch("memory.config_manager.get_wallpaper_folder",
                        return_value="C:/does/not/exist"):
            self.assertEqual(wp._images(), [])

    def test_empty_folder_message(self):
        tmp, folder = _make_folder(files=("readme.txt",))
        with mock.patch("memory.config_manager.get_wallpaper_folder",
                        return_value=str(folder)):
            out = wp.wallpapers({"action": "list"})
        self.assertIn("No wallpapers found", out)
        tmp.cleanup()


class SteppingTest(unittest.TestCase):

    def setUp(self):
        self.tmp, self.folder = _make_folder()
        self.apply = mock.patch.object(
            wp, "_apply", autospec=True,
            side_effect=lambda p: f"Wallpaper set: {p.name}")
        self.apply.start()
        patcher = mock.patch("memory.config_manager.get_wallpaper_folder",
                             return_value=str(self.folder))
        patcher.start()
        self.addCleanup(self.apply.stop)
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def test_list_shows_wallpapers(self):
        out = wp.wallpapers({"action": "list"})
        self.assertIn("a.jpg", out)
        self.assertIn("(3)", out)
        self.assertNotIn("notes.txt", out)

    def test_next_steps_then_wraps(self):
        with mock.patch("memory.config_manager.get_wallpaper_index",
                        return_value=0), \
             mock.patch("memory.config_manager.save_wallpaper_index") as save:
            out = wp.wallpapers({"action": "next"})
        self.assertIn("Wallpaper set: b.png", out)
        self.assertIn("(2 of 3)", out)
        save.assert_called_once_with(1)

    def test_previous_wraps_back(self):
        with mock.patch("memory.config_manager.get_wallpaper_index",
                        return_value=0), \
             mock.patch("memory.config_manager.save_wallpaper_index") as save:
            out = wp.wallpapers({"action": "previous"})
        self.assertIn("Wallpaper set: c.webp", out)
        self.assertIn("(3 of 3)", out)
        save.assert_called_once_with(2)

    def test_set_by_name(self):
        with mock.patch("memory.config_manager.save_wallpaper_index") as save:
            out = wp.wallpapers({"action": "set", "name": "a.jpg"})
        self.assertIn("Wallpaper set: a.jpg", out)
        self.assertIn("(1 of 3)", out)
        save.assert_called_once_with(0)

    def test_set_partial_name(self):
        out = wp.wallpapers({"action": "set", "name": "b"})
        self.assertIn("b.png", out)

    def test_set_missing_name(self):
        out = wp.wallpapers({"action": "set", "name": "zzz.jpg"})
        self.assertIn("not found", out)

    def test_random_applies_one(self):
        with mock.patch("memory.config_manager.save_wallpaper_index") as save:
            out = wp.wallpapers({"action": "random"})
        self.assertIn("Wallpaper set:", out)
        save.assert_called_once()

    def test_default_action_is_next(self):
        with mock.patch("memory.config_manager.get_wallpaper_index",
                        return_value=2), \
             mock.patch("memory.config_manager.save_wallpaper_index") as save:
            out = wp.wallpapers({})
        self.assertIn("Wallpaper set: a.jpg", out)   # wrapped to 0
        save.assert_called_once_with(0)


class PreviewTest(unittest.TestCase):

    def test_preview_opens_in_viewer(self):
        images = [Path("C:/fakepath/a.jpg"), Path("C:/fakepath/b.png")]
        with mock.patch.object(wp, "_images", return_value=images), \
             mock.patch.object(wp.os, "startfile", create=True) as start:
            out = wp.wallpapers({"action": "preview", "name": "a.jpg"})
        start.assert_called_once()
        self.assertIn("Previewing a.jpg", out)

    def test_open_folder(self):
        with mock.patch("memory.config_manager.get_wallpaper_folder",
                        return_value="C:/fakepath"), \
             mock.patch.object(wp, "_images",
                               return_value=[Path("C:/fakepath/a.jpg")]), \
             mock.patch.object(wp.os, "startfile", create=True) as start:
            out = wp.wallpapers({"action": "open"})
        start.assert_called_once()
        self.assertIn("Opened the wallpaper folder", out)


class DefaultFolderTest(unittest.TestCase):

    def test_default_folder_is_wincux(self):
        with mock.patch("memory.config_manager.load_api_keys",
                        return_value={}):
            from memory.config_manager import get_wallpaper_folder
            folder = get_wallpaper_folder()
        self.assertIn("WinCux", folder)
        self.assertIn("wallpapers", folder)


class ToolShapeTest(unittest.TestCase):

    def test_tool_declaration(self):
        self.assertEqual(wp.TOOL["name"], "wallpapers")
        self.assertIn("list", wp.TOOL["description"])
        self.assertEqual(wp.TOOL["handler"], wp.wallpapers)
        props = wp.TOOL["parameters"]["properties"]
        self.assertIn("action", props)
        self.assertEqual(wp.TOOL["parameters"].get("required"), [])


if __name__ == "__main__":
    unittest.main()