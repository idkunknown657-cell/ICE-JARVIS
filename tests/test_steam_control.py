import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import actions.steam_control as sc


class SmokeUriTests(unittest.TestCase):
    def test_search_uri_quotes(self):
        self.assertEqual(sc._smuri("search", "half life 2|mod"),
                         "steam://url/StoreSearchPage/half%20life%202%7Cmod")

    def test_basic_sections(self):
        self.assertEqual(sc._smuri("store", "440"), "steam://store/440")
        self.assertEqual(sc._smuri("install", 440), "steam://install/440")
        self.assertEqual(sc._smuri("run", "440"), "steam://run/440")
        self.assertEqual(sc._smuri("nav", "library"), "steam://nav/library")

    def test_rejects_nonints(self):
        with self.assertRaises(ValueError):
            sc._smuri("store", "not-a-number")


class VdfParseTests(unittest.TestCase):
    SAMPLE = (
        '"libraryfolders"\n{\n'
        '\t"0"\n\t{\n\t\t"path"\t\t"C:\\\\Steam"\n'
        '\t\t"apps"\n\t\t{\n\t\t\t"440"\t\t"1"\n\t\t}\n\t}\n'
        '\t"1"\n\t{\n\t\t"path"\t\t"D:\\\\Games\\\\SteamLibrary"\n'
        '\t\t"apps"\n\t\t{\n\t\t\t"730"\t\t"1"\n\t\t\t"240"\t\t"2"\n\t\t}\n\t}\n}\n'
    )

    def test_wrapped_root(self):
        data = sc._vdf_parse(self.SAMPLE)
        self.assertEqual(list(data.keys()), ["libraryfolders"])
        inner = sc._vdf_unwrap(data)
        self.assertEqual(list(inner.keys()), ["0", "1"])
        self.assertEqual(inner["0"]["path"].replace("\\\\", "\\"),
                         "C:\\Steam")
        self.assertEqual(list(inner["1"]["apps"].keys()), ["730", "240"])

    def test_unwrap_peels_only_single_named_root(self):
        data = {"k": {"a": 1}}
        self.assertEqual(sc._vdf_unwrap(data), {"a": 1})
        data2 = {"a": 1, "b": 2}
        self.assertEqual(sc._vdf_unwrap(data2), data2)

    def test_scalar_values(self):
        data = sc._vdf_parse('"AppState"\n{\n\t"appid" "240"\n\t"Nested"\n\t{\n\t\t"x" "1"\n\t}\n}\n')
        body = sc._vdf_unwrap(data)
        self.assertEqual(body["appid"], "240")
        self.assertEqual(body["Nested"]["x"], "1")


class LocalInstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

        lib_dir = self.root / "SteamLibrary"
        steamapps = lib_dir / "steamapps"
        steamapps.mkdir(parents=True)

        (self.root / "steamapps").mkdir(parents=True)
        # The VDF always lives in the install root's own steamapps folder.
        lib_path_escaped = str(lib_dir).replace("\\", "\\\\")
        (self.root / "steamapps" / "libraryfolders.vdf").write_text(
            '"libraryfolders"\n{\n'
            '\t"0"\n\t{\n\t\t"path"\t\t"%s"\n' % lib_path_escaped +
            '\t\t"apps"\n\t\t{\n\t\t\t"1062090"\t\t"1"\n\t\t}\n\t}\n}\n',
            encoding="utf-8")
        (steamapps / "appmanifest_1062090.acf").write_text(
            '"AppState"\n{\n\t"appid"\t\t"1062090"\n\t"name"\t\t"Timberborn"\n'
            '\t"installdir"\t\t"Timberborn"\n\t"SizeOnDisk"\t\t"8589934592"\n}\n',
            encoding="utf-8")
        cfg = self.root / "userdata" / "12345" / "config"
        cfg.mkdir(parents=True)
        (cfg / "localconfig.vdf").write_text(
            '"UserLocalConfigStore"\n{\n\t"Software"\n\t{\n\t\t"Valve"\n\t\t{\n'
            '\t\t\t"Steam"\n\t\t\t{\n\t\t\t\t"Apps"\n\t\t\t\t{\n'
            '\t\t\t\t\t"1062090"\t\t"1"\n\t\t\t\t\t"413150"\t\t"1"\n\t\t\t\t}\n'
            '\t\t\t}\n\t\t}\n\t}\n}\n',
            encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_library_folders(self):
        folders = sc._library_folders(self.root)
        self.assertEqual(len(folders), 1)
        self.assertEqual(folders[0]["apps"], [1062090])

    def test_app_manifests(self):
        games = sc._app_manifests(self.root)
        self.assertEqual(len(games), 1)
        g = games[0]
        self.assertEqual(g["appid"], 1062090)
        self.assertEqual(g["name"], "Timberborn")
        self.assertEqual(g["size"], 8589934592)

    def test_owned_appids_from_localconfig(self):
        self.assertEqual(sc._owned_appids(self.root), {1062090, 413150})

    def test_resolve_appid(self):
        self.assertEqual(sc._resolve_appid({"appid": "413150"}, self.root), 413150)
        self.assertEqual(sc._resolve_appid({"query": "timberborn"}, self.root), 1062090)
        with patch("actions.steam_control._store_search", return_value=[{"appid": 999900}]):
            self.assertEqual(sc._resolve_appid({"query": "some other game"}, self.root), 999900)
        with patch("actions.steam_control._store_search", return_value=[]):
            self.assertIsNone(sc._resolve_appid({"query": "nonexistent"}, self.root))

    def test_launch_local_game(self):
        with patch("actions.steam_control._steam_root", return_value=self.root), \
             patch("actions.steam_control._ensure_running", return_value=None), \
             patch("actions.steam_control._open_uri", return_value=True) as uri:
            out = sc._launch_app({"query": "timberborn"})
            uri.assert_called_once_with("steam://run/1062090")
            self.assertIn("Launching timberborn", out)

    def test_launch_aborts_when_steam_fails(self):
        with patch("actions.steam_control._steam_root", return_value=self.root), \
             patch("actions.steam_control._ensure_running",
                   return_value="Could not start Steam (failed_to_launch:x)"), \
             patch("actions.steam_control._open_uri", return_value=True) as uri:
            out = sc._launch_app({"query": "timberborn"})
            uri.assert_not_called()
            self.assertIn("start Steam", out)

    def test_purchase_goes_to_confirm_gate(self):
        called = {}

        class FakeConfirm:
            @staticmethod
            def request(*a, **k):
                called["yes"] = True
                return "[CONFIRMATION_PENDING]"

        with patch("actions.steam_control._steam_root", return_value=self.root), \
             patch("actions.steam_control.confirm", FakeConfirm), \
             patch("actions.steam_control._ensure_running", return_value=None), \
             patch("actions.steam_control._open_uri", return_value=True) as uri:
            out = sc._purchase_app({"query": "timberborn"})
            self.assertTrue(called.get("yes"))
            uri.assert_not_called()
            self.assertIn("[CONFIRMATION_PENDING]", out)


class ActionOutputTests(unittest.TestCase):
    def test_unknown_resolution_message(self):
        with patch("actions.steam_control._store_search", return_value=[]):
            out = sc.steam_control({"action": "purchase", "query": "zzz not a real game"})
        self.assertIn("Could not resolve", out)
        self.assertNotIn("Confirmation", out)

    def test_unknown_action_help(self):
        out = sc.steam_control({"action": "frobnicate"})
        self.assertIn("steam_control can:", out)

    def test_find_and_price_aliases_route_to_search(self):
        finder = [{"name": "Hades", "appid": 1145360,
                   "price": "$24.99", "is_free": False, "tiny_image": "",
                   "currency": "USD"}]
        with patch("actions.steam_control._store_search", return_value=finder):
            out = sc.steam_control({"action": "find", "query": "hades"})
            self.assertIn("Hades", out)
            self.assertIn("$24.99", out)
        with patch("actions.steam_control._store_search", return_value=finder):
            out = sc.steam_control({"action": "price", "query": "hades"})
            self.assertIn("Hades", out)


class StorePriceTests(unittest.TestCase):
    def test_fmt_local_price_usd(self):
        self.assertEqual(sc._fmt_local_price(2499, "USD"), "$ 24.99")

    def test_fmt_local_price_inr_no_paise(self):
        self.assertEqual(sc._fmt_local_price(130000, "INR"), "₹ 1,300")

    def test_fmt_local_price_empty(self):
        self.assertEqual(sc._fmt_local_price(None, "USD"), "")

    def test_search_uses_configured_currency_then_us_fallback(self):
        row = {"id": 440, "name": "TF2", "is_free": True,
               "price": {"currency": "INR", "final": 0}}
        resp = type("R", (), {"raise_for_status": lambda self: None,
                              "json": lambda self: {"items": [row]}})()
        with patch("actions.steam_control.requests.get") as get:
            get.return_value = resp
            with patch("actions.steam_control._cc", return_value="IN"):
                out = sc._store_search("tf2", n=1)
        self.assertEqual(out[0]["currency"], "INR")
        self.assertEqual(out[0]["appid"], 440)
        self.assertEqual(get.call_args.kwargs["params"]["cc"], "IN")

    def test_search_retries_us_when_configured_currency_empty(self):
        empty = type("R", (), {"raise_for_status": lambda self: None,
                               "json": lambda self: {"items": []}})()
        full = type("R", (), {"raise_for_status": lambda self: None,
                              "json": lambda self: {
                                  "items": [{"id": 440, "name": "TF2",
                                             "is_free": True, "price": {}}]}})()
        with patch("actions.steam_control.requests.get") as get:
            get.side_effect = [empty, full]
            with patch("actions.steam_control._cc", return_value="XX"):
                out = sc._store_search("tf2", n=1)
        self.assertEqual(out[0]["appid"], 440)
        self.assertEqual(get.call_args_list[1].kwargs["params"]["cc"], "US")

    def test_search_fails_clean(self):
        with patch("actions.steam_control.requests.get", side_effect=RuntimeError("down")):
            self.assertEqual(sc._store_search("tf2"), [])


if __name__ == "__main__":
    unittest.main()
