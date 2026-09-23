"""Tests for the computer_settings additions: state-aware Wi-Fi/Bluetooth,
real sleep, sign-out, press_key combos, and the volume-DOWN-BY-20% rule.

The desktop is never touched — every subprocess/pyautogui call is mocked.
"""
import unittest
from unittest import mock

from actions import computer_settings as cs


class ResolverTests(unittest.TestCase):
    """_detect_action: what a free-text sentence resolves to locally."""

    def test_decrease_volume_is_a_delta_not_a_target(self):
        # "decrease volume by 20%" must lower BY 20, never set TO 20.
        det = cs._detect_action("decrease volume by 20%")
        self.assertEqual(det["action"], "volume_down")
        self.assertEqual(det["value"], 20)

    def test_increase_volume_is_a_delta(self):
        det = cs._detect_action("increase volume by 15")
        self.assertEqual(det["action"], "volume_up")
        self.assertEqual(det["value"], 15)

    def test_set_volume_to_still_sets(self):
        det = cs._detect_action("set volume to 30")
        self.assertEqual(det["action"], "volume_set")
        self.assertEqual(det["value"], 30)

    def test_plain_number_next_to_volume_still_sets(self):
        det = cs._detect_action("volume 55")
        self.assertEqual(det["action"], "volume_set")
        self.assertEqual(det["value"], 55)

    def test_wifi_off_resolves_to_explicit_off(self):
        self.assertEqual(cs._detect_action("wifi off")["action"], "wifi_off")
        self.assertEqual(cs._detect_action("turn wi-fi off")["action"],
                         "wifi_off")

    def test_bluetooth_on_resolves_to_explicit_on(self):
        self.assertEqual(cs._detect_action("turn bluetooth on")["action"],
                         "bluetooth_on")

    def test_bare_bluetooth_still_toggles(self):
        self.assertEqual(cs._detect_action("bluetooth")["action"],
                         "toggle_bluetooth")

    def test_sleep_and_sign_out_phrases(self):
        self.assertEqual(cs._detect_action("put pc to sleep")["action"],
                         "sleep_pc")
        self.assertEqual(cs._detect_action("log me out")["action"],
                         "sign_out")


class PressKeyTests(unittest.TestCase):

    def test_combo_keys_go_through_hotkey(self):
        fake = mock.Mock()
        with mock.patch.object(cs, "pyautogui", fake):
            cs.press_key("win+shift+s")
        fake.hotkey.assert_called_once_with("win", "shift", "s")
        fake.press.assert_not_called()

    def test_single_key_still_presses(self):
        fake = mock.Mock()
        with mock.patch.object(cs, "pyautogui", fake):
            cs.press_key("enter")
        fake.press.assert_called_once_with("enter")
        fake.hotkey.assert_not_called()


class VolumeDeltaTests(unittest.TestCase):

    def test_volume_down_by_value_changes_by_that_percent(self):
        with mock.patch.object(cs, "volume_get", return_value=80), \
             mock.patch.object(cs, "volume_set") as setter, \
             mock.patch.object(cs, "push_undo"):
            out = cs.computer_settings({"action": "volume_down",
                                        "value": "20"})
        setter.assert_called_once_with(60)
        self.assertIn("now at 60%", out)

    def test_volume_delta_clamps_at_zero(self):
        with mock.patch.object(cs, "volume_get", return_value=10), \
             mock.patch.object(cs, "volume_set") as setter, \
             mock.patch.object(cs, "push_undo"):
            out = cs.computer_settings({"action": "volume_down",
                                        "value": "30"})
        setter.assert_called_once_with(0)
        self.assertIn("now at 0%", out)

    def test_delta_without_readable_volume_says_so(self):
        with mock.patch.object(cs, "volume_get", return_value=None), \
             mock.patch.object(cs, "volume_set") as setter:
            out = cs.computer_settings({"action": "volume_down",
                                        "value": "20"})
        setter.assert_not_called()
        self.assertIn("will not report its volume", out)

    def test_delta_result_is_an_honest_string(self):
        with mock.patch.object(cs, "volume_get", return_value=40), \
             mock.patch.object(cs, "volume_set"), \
             mock.patch.object(cs, "push_undo"):
            out = cs.computer_settings({"action": "volume_up", "value": "10"})
        self.assertEqual(out, "Raised volume by 10% — now at 50%.")


class WifiStateTests(unittest.TestCase):

    def test_already_off_never_touches_the_system(self):
        with mock.patch.object(cs, "_win_wifi_status", return_value="down"), \
             mock.patch.object(cs, "_ps") as ps:
            out = cs.set_wifi("off")
        ps.assert_not_called()
        self.assertEqual(out, "Wi-Fi is already off.")

    def test_wifi_off_is_verified_and_reported(self):
        # before: up  → attempt (adapter/radio mocked) → after: down
        with mock.patch.object(cs, "_win_wifi_status",
                               side_effect=["up", "down"]), \
             mock.patch.object(cs, "_ps"), \
             mock.patch.object(cs, "_radio_set") as radio:
            out = cs.set_wifi("off")
        radio.assert_not_called()        # adapter route worked first
        self.assertEqual(out, "Wi-Fi is now OFF.")

    def test_refusal_reports_the_real_current_state(self):
        with mock.patch.object(cs, "_win_wifi_status",
                               side_effect=["up", "up", "up"]), \
             mock.patch.object(cs, "_ps"), \
             mock.patch.object(cs, "_radio_set", return_value="NO_RADIO"):
            out = cs.set_wifi("off")
        self.assertIn("Could not switch Wi-Fi off", out)
        self.assertIn("currently on", out)
        self.assertNotIn("Done", out)


class BluetoothTests(unittest.TestCase):

    def test_windows_already_on_reports_without_touching(self):
        with mock.patch.object(cs, "_radio_state", return_value="on"), \
             mock.patch.object(cs, "_radio_set") as setter:
            out = cs.bluetooth_on()
        setter.assert_not_called()
        self.assertEqual(out, "Bluetooth is already on.")

    def test_linux_result_is_honest(self):
        powered = ["Powered: yes", "Powered: no"]
        with mock.patch.object(cs, "_OS", "Linux"), \
             mock.patch.object(cs.subprocess, "run") as run:
            run.side_effect = [
                mock.Mock(stdout=powered[0]),   # show: currently on
                mock.Mock(stdout=""),           # power off (ignored)
                mock.Mock(stdout=powered[1]),   # show: now off
            ]
            out = cs.bluetooth_off()
        self.assertEqual(out, "Bluetooth is now OFF.")


class SafetyGateTests(unittest.TestCase):

    def test_sign_out_and_wifi_off_need_a_human(self):
        self.assertIn("sign_out", cs._IRREVERSIBLE)
        self.assertIn("wifi_off", cs._IRREVERSIBLE)
        self.assertIn("toggle_wifi", cs._IRREVERSIBLE)
        self.assertIn("shutdown", cs._IRREVERSIBLE)
        # Reversible things stay fast — no confirmation prompt.
        for fast in ("bluetooth_on", "bluetooth_off", "wifi_on",
                     "sleep_pc", "toggle_bluetooth"):
            self.assertNotIn(fast, cs._IRREVERSIBLE)

    def test_new_actions_are_registered_and_advertised(self):
        for name in ("wifi_on", "wifi_off", "toggle_bluetooth",
                     "bluetooth_on", "bluetooth_off", "sleep_pc",
                     "sign_out"):
            self.assertIn(name, cs.ACTION_MAP)
            self.assertIn(name, cs.TOOL["parameters"]["properties"]
                                    ["action"]["description"])


class DispatcherResultTests(unittest.TestCase):
    """A function's own verified message must surface — never a blanket Done."""

    def test_done_msg_prefers_the_real_result(self):
        self.assertEqual(cs._done_msg("wifi_on", "Wi-Fi is now ON."),
                         "Wi-Fi is now ON.")
        self.assertEqual(cs._done_msg("some_action", None), "Done: some_action.")

    def test_dispatcher_returns_the_functions_message(self):
        with mock.patch.dict(cs.ACTION_MAP,
                             {"wifi_on": lambda: "Wi-Fi is now ON."}):
            out = cs.computer_settings({"action": "wifi_on"})
        self.assertEqual(out, "Wi-Fi is now ON.")

    def test_dispatcher_still_says_done_for_silent_actions(self):
        with mock.patch.dict(cs.ACTION_MAP, {"lock_screen": lambda: None}):
            out = cs.computer_settings({"action": "lock_screen"})
        self.assertEqual(out, "Done: lock_screen.")


if __name__ == "__main__":
    unittest.main()
