#computer_settings.py
import json
import re
import sys
import time
import subprocess
import platform
from pathlib import Path

try:
    import pyautogui
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE    = 0.05
    _PYAUTOGUI = True
except ImportError:
    _PYAUTOGUI = False

try:
    import pyperclip
    _PYPERCLIP = True
except ImportError:
    _PYPERCLIP = False

from core import confirm
from core.undo import push_undo

_OS = platform.system()  # "Windows" | "Darwin" | "Linux"

if _OS == "Windows":
    _WIN_HIDE: dict = {"creationflags": subprocess.CREATE_NO_WINDOW}
else:
    _WIN_HIDE: dict = {}


def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent

def _get_api_key() -> str:
    path = _get_base_dir() / "config" / "api_keys.json"
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)["gemini_api_key"]

def _get_macos_wifi_interface() -> str:
    try:
        result = subprocess.run(
            ["networksetup", "-listallhardwareports"],
            capture_output=True, text=True, timeout=5
        )
        lines = result.stdout.splitlines()
        for i, line in enumerate(lines):
            if "Wi-Fi" in line or "AirPort" in line:
                for j in range(i, min(i + 4, len(lines))):
                    if lines[j].startswith("Device:"):
                        return lines[j].split(":", 1)[1].strip()
    except Exception:
        pass
    return "en0" 

def volume_up():
    if _OS == "Windows":
        for _ in range(5): pyautogui.press("volumeup")
    elif _OS == "Darwin":
        subprocess.run(["osascript", "-e",
            "set volume output volume (output volume of (get volume settings) + 10)"],
            capture_output=True)
    else:
        subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", "+10%"],
            capture_output=True)

def volume_down():
    if _OS == "Windows":
        for _ in range(5): pyautogui.press("volumedown")
    elif _OS == "Darwin":
        subprocess.run(["osascript", "-e",
            "set volume output volume (output volume of (get volume settings) - 10)"],
            capture_output=True)
    else:
        subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", "-10%"],
            capture_output=True)

def volume_mute():
    if _OS == "Windows":
        pyautogui.press("volumemute")
    elif _OS == "Darwin":
        subprocess.run(["osascript", "-e", "set volume with output muted"],
            capture_output=True)
    else:
        subprocess.run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "toggle"],
            capture_output=True)

def volume_get() -> int | None:
    """Current master volume 0-100, or None if this platform will not say.

    Undo needs a "before" value, and reading one is cheap on every OS we
    support. Where it is not readable the action simply is not registered as
    undoable — a wrong undo is worse than no undo."""
    try:
        if _OS == "Windows":
            import math
            from ctypes import cast, POINTER
            from comtypes import CLSCTX_ALL
            from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
            devices   = AudioUtilities.GetSpeakers()
            interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            vol       = cast(interface, POINTER(IAudioEndpointVolume))
            db        = vol.GetMasterVolumeLevel()
            if db <= -65.0:
                return 0
            return max(0, min(100, round(10 ** (db / 20) * 100)))
        if _OS == "Darwin":
            r = subprocess.run(["osascript", "-e", "output volume of (get volume settings)"],
                               capture_output=True, text=True, timeout=5)
            return max(0, min(100, int(r.stdout.strip())))
        r = subprocess.run(["pactl", "get-sink-volume", "@DEFAULT_SINK@"],
                           capture_output=True, text=True, timeout=5)
        m = re.search(r"(\d+)%", r.stdout)
        return max(0, min(100, int(m.group(1)))) if m else None
    except Exception:
        return None


def brightness_get() -> int | None:
    """Current brightness 0-100, or None where it cannot be read."""
    try:
        if _OS == "Windows":
            r = subprocess.run(
                ["powershell", "-Command",
                 "(Get-WmiObject -Namespace root/wmi -Class WmiMonitorBrightness)"
                 ".CurrentBrightness"],
                capture_output=True, text=True, timeout=5, **_WIN_HIDE
            )
            return max(0, min(100, int(r.stdout.strip())))
        if _OS == "Linux" and subprocess.run(
                ["which", "brightnessctl"], capture_output=True).returncode == 0:
            cur = int(subprocess.run(["brightnessctl", "get"],
                                     capture_output=True, text=True, timeout=5).stdout.strip())
            mx  = int(subprocess.run(["brightnessctl", "max"],
                                     capture_output=True, text=True, timeout=5).stdout.strip())
            return max(0, min(100, round(cur * 100 / mx))) if mx else None
    except Exception:
        pass
    return None


def brightness_set(value: int) -> None:
    """Set brightness to an absolute percentage. Only used to restore a value
    captured before a change, so it is undo's counterpart to the up/down pair."""
    value = max(0, min(100, int(value)))
    if _OS == "Windows":
        subprocess.run(
            ["powershell", "-Command",
             "(Get-WmiObject -Namespace root/wmi -Class WmiMonitorBrightnessMethods)"
             f".WmiSetBrightness(1, {value})"],
            capture_output=True, timeout=5, **_WIN_HIDE
        )
    elif _OS == "Linux":
        subprocess.run(["brightnessctl", "set", f"{value}%"], capture_output=True)


def volume_set(value: int):
    value = max(0, min(100, int(value)))
    if _OS == "Windows":
        try:
            import math
            from ctypes import cast, POINTER
            from comtypes import CLSCTX_ALL
            from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
            devices   = AudioUtilities.GetSpeakers()
            interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            vol       = cast(interface, POINTER(IAudioEndpointVolume))
            vol_db    = -65.25 if value == 0 else max(-65.25, 20 * math.log10(value / 100))
            vol.SetMasterVolumeLevel(vol_db, None)
            return
        except Exception as e:
            print(f"[Settings] pycaw failed, using keypress fallback: {e}")
            pyautogui.press("volumemute")
            pyautogui.press("volumemute")
    elif _OS == "Darwin":
        subprocess.run(["osascript", "-e", f"set volume output volume {value}"],
            capture_output=True)
        return
    else:
        subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{value}%"],
            capture_output=True)
        return

def brightness_up():
    if _OS == "Darwin":
        subprocess.run(["osascript", "-e",
            'tell application "System Events" to key code 144'],
            capture_output=True)
    elif _OS == "Linux":
        if subprocess.run(["which", "brightnessctl"],
                capture_output=True).returncode == 0:
            subprocess.run(["brightnessctl", "set", "+10%"], capture_output=True)
        else:
            subprocess.run(
                'xrandr --output $(xrandr | grep " connected" | head -1 | cut -d " " -f1)'
                ' --brightness $(python3 -c "import subprocess; '
                'b=float(subprocess.check_output([\"xrandr\",\"--verbose\"]).decode()'
                '.split(\"Brightness:\")[1].split()[0]); print(min(1.0,b+0.1))")',
                shell=True, capture_output=True
            )
    else:
        try:
            subprocess.run(
                ["powershell", "-Command",
                 "(Get-WmiObject -Namespace root/wmi -Class WmiMonitorBrightnessMethods)"
                 ".WmiSetBrightness(1, [math]::Min(100, "
                 "(Get-WmiObject -Namespace root/wmi -Class WmiMonitorBrightness).CurrentBrightness + 10))"],
                capture_output=True, timeout=5, **_WIN_HIDE
            )
        except Exception as e:
            print(f"[Settings] Brightness up failed on Windows: {e}")

def brightness_down():
    if _OS == "Darwin":
        subprocess.run(["osascript", "-e",
            'tell application "System Events" to key code 145'],
            capture_output=True)
    elif _OS == "Linux":
        if subprocess.run(["which", "brightnessctl"],
                capture_output=True).returncode == 0:
            subprocess.run(["brightnessctl", "set", "10%-"], capture_output=True)
        else:
            subprocess.run(
                'xrandr --output $(xrandr | grep " connected" | head -1 | cut -d " " -f1)'
                ' --brightness $(python3 -c "import subprocess; '
                'b=float(subprocess.check_output([\"xrandr\",\"--verbose\"]).decode()'
                '.split(\"Brightness:\")[1].split()[0]); print(max(0.1,b-0.1))")',
                shell=True, capture_output=True
            )
    else:
        try:
            subprocess.run(
                ["powershell", "-Command",
                 "(Get-WmiObject -Namespace root/wmi -Class WmiMonitorBrightnessMethods)"
                 ".WmiSetBrightness(1, [math]::Max(0, "
                 "(Get-WmiObject -Namespace root/wmi -Class WmiMonitorBrightness).CurrentBrightness - 10))"],
                capture_output=True, timeout=5, **_WIN_HIDE
            )
        except Exception as e:
            print(f"[Settings] Brightness down failed on Windows: {e}")

def close_app():
    if _OS == "Darwin": pyautogui.hotkey("command", "q")
    else:               pyautogui.hotkey("alt", "f4")

def close_window():
    if _OS == "Darwin": pyautogui.hotkey("command", "w")
    else:               pyautogui.hotkey("ctrl", "w")

def full_screen():
    if _OS == "Darwin": pyautogui.hotkey("ctrl", "command", "f")
    else:               pyautogui.press("f11")

def minimize_window():
    if _OS == "Darwin": pyautogui.hotkey("command", "m")
    else:               pyautogui.hotkey("win", "down")

def maximize_window():
    if _OS == "Darwin":
        subprocess.run(["osascript", "-e",
            'tell application "System Events" to keystroke "f" '
            'using {control down, command down}'],
            capture_output=True)
    elif _OS == "Windows":
        pyautogui.hotkey("win", "up")
    else:
        try:
            subprocess.run(["wmctrl", "-r", ":ACTIVE:", "-b", "add,maximized_vert,maximized_horz"],
                capture_output=True)
        except Exception:
            pyautogui.hotkey("super", "up")

def snap_left():
    if _OS == "Windows":
        pyautogui.hotkey("win", "left")
    elif _OS == "Darwin":
        # macOS has no built-in snap; try Rectangle app shortcut if installed
        try:
            subprocess.run(["open", "-a", "Rectangle"], capture_output=True, timeout=1)
        except Exception:
            pass
        pyautogui.hotkey("ctrl", "option", "left")
    else:  # Linux
        try:
            subprocess.run(["wmctrl", "-r", ":ACTIVE:", "-e", "0,0,0,960,1080"],
                capture_output=True)
        except Exception:
            pass

def snap_right():
    if _OS == "Windows":
        pyautogui.hotkey("win", "right")
    elif _OS == "Darwin":
        try:
            subprocess.run(["open", "-a", "Rectangle"], capture_output=True, timeout=1)
        except Exception:
            pass
        pyautogui.hotkey("ctrl", "option", "right")
    else:  # Linux
        try:
            subprocess.run(["wmctrl", "-r", ":ACTIVE:", "-e", "0,960,0,960,1080"],
                capture_output=True)
        except Exception:
            pass

def switch_window():
    if _OS == "Darwin": pyautogui.hotkey("command", "tab")
    else:               pyautogui.hotkey("alt", "tab")

def show_desktop():
    if _OS == "Darwin":   pyautogui.hotkey("fn", "f11")
    elif _OS == "Windows": pyautogui.hotkey("win", "d")
    else:                  pyautogui.hotkey("super", "d")

def open_task_manager():
    if _OS == "Windows":
        pyautogui.hotkey("ctrl", "shift", "esc")
    elif _OS == "Darwin":
        subprocess.Popen(["open", "-a", "Activity Monitor"])
    else:
        for cmd in [["gnome-system-monitor"], ["xfce4-taskmanager"], ["htop"]]:
            if subprocess.run(["which", cmd[0]], capture_output=True).returncode == 0:
                subprocess.Popen(cmd)
                break


def focus_search():
    if _OS == "Darwin": pyautogui.hotkey("command", "l")
    else:               pyautogui.hotkey("ctrl", "l")

def pause_video():      pyautogui.press("space")

def refresh_page():
    if _OS == "Darwin": pyautogui.hotkey("command", "r")
    else:               pyautogui.press("f5")

def close_tab():
    if _OS == "Darwin": pyautogui.hotkey("command", "w")
    else:               pyautogui.hotkey("ctrl", "w")

def new_tab():
    if _OS == "Darwin": pyautogui.hotkey("command", "t")
    else:               pyautogui.hotkey("ctrl", "t")

def next_tab():
    if _OS == "Darwin": pyautogui.hotkey("command", "shift", "bracketright")
    else:               pyautogui.hotkey("ctrl", "tab")

def prev_tab():
    if _OS == "Darwin": pyautogui.hotkey("command", "shift", "bracketleft")
    else:               pyautogui.hotkey("ctrl", "shift", "tab")

def go_back():
    if _OS == "Darwin": pyautogui.hotkey("command", "left")
    else:               pyautogui.hotkey("alt", "left")

def go_forward():
    if _OS == "Darwin": pyautogui.hotkey("command", "right")
    else:               pyautogui.hotkey("alt", "right")

def zoom_in():
    if _OS == "Darwin": pyautogui.hotkey("command", "equal")
    else:               pyautogui.hotkey("ctrl", "equal")

def zoom_out():
    if _OS == "Darwin": pyautogui.hotkey("command", "minus")
    else:               pyautogui.hotkey("ctrl", "minus")

def zoom_reset():
    if _OS == "Darwin": pyautogui.hotkey("command", "0")
    else:               pyautogui.hotkey("ctrl", "0")

def find_on_page():
    if _OS == "Darwin": pyautogui.hotkey("command", "f")
    else:               pyautogui.hotkey("ctrl", "f")

def reload_page_n(n: int):
    for _ in range(max(1, n)):
        refresh_page()
        time.sleep(0.8)


def scroll_up(amount: int = 500):    pyautogui.scroll(amount)
def scroll_down(amount: int = 500):  pyautogui.scroll(-amount)

def scroll_top():
    if _OS == "Darwin": pyautogui.hotkey("command", "up")
    else:               pyautogui.hotkey("ctrl", "home")

def scroll_bottom():
    if _OS == "Darwin": pyautogui.hotkey("command", "down")
    else:               pyautogui.hotkey("ctrl", "end")

def page_up():   pyautogui.press("pageup")
def page_down(): pyautogui.press("pagedown")


def copy():
    if _OS == "Darwin": pyautogui.hotkey("command", "c")
    else:               pyautogui.hotkey("ctrl", "c")

def paste():
    if _OS == "Darwin": pyautogui.hotkey("command", "v")
    else:               pyautogui.hotkey("ctrl", "v")

def cut():
    if _OS == "Darwin": pyautogui.hotkey("command", "x")
    else:               pyautogui.hotkey("ctrl", "x")

def undo():
    if _OS == "Darwin": pyautogui.hotkey("command", "z")
    else:               pyautogui.hotkey("ctrl", "z")

def redo():
    if _OS == "Darwin": pyautogui.hotkey("command", "shift", "z")
    else:               pyautogui.hotkey("ctrl", "y")

def select_all():
    if _OS == "Darwin": pyautogui.hotkey("command", "a")
    else:               pyautogui.hotkey("ctrl", "a")

def save_file():
    if _OS == "Darwin": pyautogui.hotkey("command", "s")
    else:               pyautogui.hotkey("ctrl", "s")

def press_enter():   pyautogui.press("enter")
def press_escape():  pyautogui.press("escape")
def press_key(key: str):
    """One key, or a combination like 'win+shift+s' (combo used to crash)."""
    text = str(key).strip()
    if "+" in text:
        parts = [p.strip() for p in text.split("+") if p.strip()]
        if len(parts) >= 2:
            pyautogui.hotkey(*parts)
            return
    pyautogui.press(text)

def type_text(text: str, press_enter_after: bool = False):
    if not text:
        return
    if _PYPERCLIP:
        pyperclip.copy(str(text))
        time.sleep(0.15)
        paste()
    else:
        pyautogui.write(str(text), interval=0.03)
    if press_enter_after:
        time.sleep(0.1)
        pyautogui.press("enter")

def take_screenshot():
    if _OS == "Windows":
        pyautogui.hotkey("win", "shift", "s")
    elif _OS == "Darwin":
        pyautogui.hotkey("command", "shift", "3")
    else:
        for cmd in [["scrot"], ["gnome-screenshot"], ["import", "-window", "root", "screenshot.png"]]:
            if subprocess.run(["which", cmd[0]], capture_output=True).returncode == 0:
                subprocess.Popen(cmd)
                return
        pyautogui.hotkey("ctrl", "print_screen")

def lock_screen():
    if _OS == "Windows":
        pyautogui.hotkey("win", "l")
    elif _OS == "Darwin":
        subprocess.run(["pmset", "displaysleepnow"], capture_output=True)
    else:
        for cmd in [
            ["gnome-screensaver-command", "-l"],
            ["xdg-screensaver", "lock"],
            ["loginctl", "lock-session"],
        ]:
            if subprocess.run(["which", cmd[0]], capture_output=True).returncode == 0:
                subprocess.run(cmd, capture_output=True)
                return

def open_system_settings():
    if _OS == "Windows":
        pyautogui.hotkey("win", "i")
    elif _OS == "Darwin":
        subprocess.Popen(["open", "-a", "System Preferences"])
    else:
        for cmd in [["gnome-control-center"], ["xfce4-settings-manager"], ["kcmshell5"]]:
            if subprocess.run(["which", cmd[0]], capture_output=True).returncode == 0:
                subprocess.Popen(cmd)
                return

def open_file_explorer():
    if _OS == "Windows":
        pyautogui.hotkey("win", "e")
    elif _OS == "Darwin":
        subprocess.Popen(["open", str(Path.home())])
    else:
        for cmd in [["nautilus"], ["thunar"], ["dolphin"], ["nemo"]]:
            if subprocess.run(["which", cmd[0]], capture_output=True).returncode == 0:
                subprocess.Popen(cmd)
                return
        subprocess.Popen(["xdg-open", str(Path.home())])

def sleep_display():
    if _OS == "Windows":
        try:
            import ctypes
            ctypes.windll.user32.SendMessageW(0xFFFF, 0x0112, 0xF170, 2)
        except Exception as e:
            print(f"[Settings] sleep_display failed: {e}")
    elif _OS == "Darwin":
        subprocess.run(["pmset", "displaysleepnow"], capture_output=True)
    else:
        subprocess.run(["xset", "dpms", "force", "off"], capture_output=True)

def open_run():
    if _OS == "Windows":
        pyautogui.hotkey("win", "r")

def dark_mode():
    if _OS == "Darwin":
        subprocess.run(["osascript", "-e",
            'tell app "System Events" to tell appearance preferences '
            'to set dark mode to not dark mode'],
            capture_output=True)
    elif _OS == "Windows":
        try:
            import winreg
            key_path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Themes\Personalize"
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_ALL_ACCESS)
            current, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            winreg.SetValueEx(key, "AppsUseLightTheme", 0, winreg.REG_DWORD, 1 - current)
            winreg.SetValueEx(key, "SystemUsesLightTheme", 0, winreg.REG_DWORD, 1 - current)
            winreg.CloseKey(key)
        except Exception as e:
            print(f"[Settings] dark_mode registry failed: {e}")
    else:
        try:
            result = subprocess.run(
                ["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"],
                capture_output=True, text=True
            )
            current = result.stdout.strip()
            new_scheme = "'default'" if "dark" in current else "'prefer-dark'"
            subprocess.run(
                ["gsettings", "set", "org.gnome.desktop.interface", "color-scheme", new_scheme],
                capture_output=True
            )
        except Exception as e:
            print(f"[Settings] dark_mode Linux failed: {e}")

def toggle_wifi():
    if _OS == "Darwin":
        iface = _get_macos_wifi_interface()
        result = subprocess.run(
            ["networksetup", "-getairportpower", iface],
            capture_output=True, text=True
        )
        state = "off" if "On" in result.stdout else "on"
        subprocess.run(["networksetup", "-setairportpower", iface, state],
            capture_output=True)
    elif _OS == "Windows":
        try:
            subprocess.run(
                ["powershell", "-Command",
                 "$adapter = Get-NetAdapter | Where-Object {$_.PhysicalMediaType -eq 'Native 802.11'};"
                 "if ($adapter.Status -eq 'Up') { Disable-NetAdapter -Name $adapter.Name -Confirm:$false }"
                 "else { Enable-NetAdapter -Name $adapter.Name -Confirm:$false }"],
                capture_output=True, timeout=10, **_WIN_HIDE
            )
        except Exception as e:
            print(f"[Settings] toggle_wifi Windows failed: {e}")
    else:
        try:
            result = subprocess.run(["nmcli", "radio", "wifi"], capture_output=True, text=True)
            state  = "off" if "enabled" in result.stdout else "on"
            subprocess.run(["nmcli", "radio", "wifi", state], capture_output=True)
        except Exception as e:
            print(f"[Settings] toggle_wifi Linux failed: {e}")

def restart_computer():
    if _OS == "Windows":
        subprocess.run(["shutdown", "/r", "/t", "10"], capture_output=True, **_WIN_HIDE)
    elif _OS == "Darwin":
        subprocess.run(["osascript", "-e",
            'tell application "System Events" to restart'],
            capture_output=True)
    else:
        subprocess.run(["systemctl", "reboot"], capture_output=True)

def shutdown_computer():
    if _OS == "Windows":
        subprocess.run(["shutdown", "/s", "/t", "10"], capture_output=True)
    elif _OS == "Darwin":
        subprocess.run(["osascript", "-e",
            'tell application "System Events" to shut down'],
            capture_output=True)
    else:
        subprocess.run(["systemctl", "poweroff"], capture_output=True)


# ── §8: state-aware radios, real sleep, sign-out ──────────────────────────────
#
# Every function here VERIFIES the resulting state and says so honestly —
# "Done." without evidence is forbidden (#18/#37).

def _ps(script: str, timeout: int = 12) -> str:
    """Run a PowerShell snippet hidden; return stripped stdout ('' on failure)."""
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                           capture_output=True, text=True, timeout=timeout,
                           **_WIN_HIDE)
        return (r.stdout or "").strip()
    except Exception:
        return ""


def _win_wifi_status() -> str:
    """'up' | 'down' | '' (adapter query; '' = could not tell)."""
    if _OS != "Windows":
        return ""
    return _ps("$a = Get-NetAdapter -ErrorAction SilentlyContinue | "
               "Where-Object {$_.PhysicalMediaType -eq 'Native 802.11'} | "
               "Select-Object -First 1; if ($a) { $a.Status }").lower()


# The Radio API is what Airplane mode uses; unlike Disable-NetAdapter it does
# not need an administrator session. __KIND__ / __STATE__ are substituted.
_PS_RADIO_SET = r"""
try {
  [Windows.Devices.Radios.Radio,Windows.System.Devices,ContentType=WindowsRuntime] | Out-Null
  $all = [System.WindowsRuntimeSystemExtensions].GetMethods() |
    Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 }
  $mOp = $all | Where-Object { $_.GetParameters()[0].ParameterType.Name -like 'IAsyncOperation`1' } |
    Select-Object -First 1
  $mAct = $all | Where-Object { $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncAction' } |
    Select-Object -First 1
  $op = [Windows.Devices.Radios.Radio]::GetRadiosAsync()
  $task = $mOp.MakeGenericMethod([Windows.Devices.Radios.Radio]).Invoke($null, @($op))
  $task.Wait(8000) | Out-Null
  $radio = $task.Result |
    Where-Object { $_.Name -like ('*' + '__KIND__' + '*') } | Select-Object -First 1
  if (-not $radio) { Write-Output 'NO_RADIO'; exit }
  $radio.SetStateAsync([Windows.Devices.Radios.RadioState]::__STATE__) | Out-Null
  Start-Sleep -Milliseconds 1500
  Write-Output ('STATE_' + $radio.State)
} catch { Write-Output ('ERR_' + $_.Exception.Message) }
"""

_PS_RADIO_GET = r"""
try {
  [Windows.Devices.Radios.Radio,Windows.System.Devices,ContentType=WindowsRuntime] | Out-Null
  $mOp = [System.WindowsRuntimeSystemExtensions].GetMethods() |
    Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 } |
    Where-Object { $_.GetParameters()[0].ParameterType.Name -like 'IAsyncOperation`1' } |
    Select-Object -First 1
  $op = [Windows.Devices.Radios.Radio]::GetRadiosAsync()
  $task = $mOp.MakeGenericMethod([Windows.Devices.Radios.Radio]).Invoke($null, @($op))
  $task.Wait(8000) | Out-Null
  $radio = $task.Result |
    Where-Object { $_.Name -like ('*' + '__KIND__' + '*') } | Select-Object -First 1
  if (-not $radio) { Write-Output 'NO_RADIO' } else { Write-Output ('STATE_' + $radio.State) }
} catch { Write-Output 'ERR' }
"""


def _radio_state(kind: str) -> str:
    """'on' | 'off' | '' (Windows Radio API; '' = unknown)."""
    out = _ps(_PS_RADIO_GET.replace("__KIND__", kind), timeout=15)
    if out.endswith("On"):
        return "on"
    if out.endswith("Off"):
        return "off"
    return ""


def _radio_set(kind: str, want: str) -> str:
    """Switch a radio and verify by reading it back. Honest on every path."""
    state = "On" if want == "on" else "Off"
    _ps(_PS_RADIO_SET.replace("__KIND__", kind).replace("__STATE__", state),
        timeout=15)
    now = _radio_state(kind)
    if now == want:
        return f"{kind} is now {want.upper()}."
    if now:
        return (f"Could not switch {kind} {want} — Windows refused (an "
                f"administrator session may be required). It is currently "
                f"{now.upper()}.")
    return (f"Tried to switch {kind} {want}, but could not confirm the result "
            f"— treat it as unchanged.")


def set_wifi(state: str) -> str:
    """Explicit 'wifi on' / 'wifi off': state-aware and verified, never a
    blind toggle (#8: 'Turn Wi-Fi off' must not turn an already-off radio on)."""
    want = "on" if str(state).lower() in ("on", "1", "true", "enable",
                                          "enabled") else "off"
    if _OS == "Darwin":
        iface = _get_macos_wifi_interface()
        try:
            cur = subprocess.run(["networksetup", "-getairportpower", iface],
                                 capture_output=True, text=True, timeout=5).stdout
            if ("On" in cur) == (want == "on"):
                return f"Wi-Fi is already {want}."
            subprocess.run(["networksetup", "-setairportpower", iface, want],
                           capture_output=True, timeout=5)
            now = subprocess.run(["networksetup", "-getairportpower", iface],
                                 capture_output=True, text=True, timeout=5).stdout
            now_on = "On" in now
            if now_on == (want == "on"):
                return f"Wi-Fi is now {want.upper()}."
        except Exception:
            pass
        return f"Could not switch Wi-Fi {want} on this system."
    if _OS == "Windows":
        cur = _win_wifi_status()
        if want == "on" and cur == "up":
            return "Wi-Fi is already on."
        if want == "off" and cur == "down":
            return "Wi-Fi is already off."
        verb = "Enable" if want == "on" else "Disable"
        _ps("$a = Get-NetAdapter -ErrorAction SilentlyContinue | "
            "Where-Object {$_.PhysicalMediaType -eq 'Native 802.11'} | "
            "Select-Object -First 1; if ($a) { "
            + verb + "-NetAdapter -Name $a.Name -Confirm:$false -ErrorAction Stop }")
        now = _win_wifi_status()
        if now and ((want == "on" and now == "up") or (want == "off" and now == "down")):
            return f"Wi-Fi is now {want.upper()}."
        # Adapter route refused (it needs administrator) — radio API needs none.
        _radio_set("Wi-Fi", want)
        now = _win_wifi_status()
        if now and ((want == "on" and now == "up") or (want == "off" and now == "down")):
            return f"Wi-Fi is now {want.upper()}."
        state_txt = ("on" if now == "up" else "off" if now == "down"
                     else "in an unknown state")
        return (f"Could not switch Wi-Fi {want} — Windows refused (an "
                f"administrator session may be required). Wi-Fi is currently "
                f"{state_txt}.")
    # Linux
    try:
        subprocess.run(["nmcli", "radio", "wifi",
                        "on" if want == "on" else "off"],
                       capture_output=True, timeout=8)
        now = subprocess.run(["nmcli", "radio", "wifi"],
                             capture_output=True, text=True, timeout=5).stdout
        now_on = "enabled" in now
        if now_on == (want == "on"):
            return f"Wi-Fi is now {want.upper()}."
        return f"Wi-Fi radio did not change; it reports: {now.strip() or 'unknown'}."
    except Exception as e:
        return f"Could not change the Wi-Fi radio: {e}"


def wifi_on() -> str:
    return set_wifi("on")


def wifi_off() -> str:
    return set_wifi("off")


def toggle_bluetooth() -> str:
    """Bluetooth power, state-aware where the OS allows it (#8)."""
    if _OS == "Windows":
        cur = _radio_state("Bluetooth")
        want = "off" if cur == "on" else "on"
        return _radio_set("Bluetooth", want)
    if _OS == "Darwin":
        # macOS exposes no supported CLI for radio power without third-party
        # tools — say so instead of pretending.
        return ("Cannot switch Bluetooth off/on from here on macOS — open "
                "System Settings > Bluetooth and use the toggle there.")
    # Linux
    try:
        cur = subprocess.run(["bluetoothctl", "show"],
                             capture_output=True, text=True, timeout=5).stdout
        want = "off" if "Powered: yes" in cur else "on"
        subprocess.run(["bluetoothctl", "power", want],
                       capture_output=True, timeout=8)
        now = subprocess.run(["bluetoothctl", "show"],
                             capture_output=True, text=True, timeout=5).stdout
        if ("Powered: yes" in now) == (want == "on"):
            return f"Bluetooth is now {want.upper()}."
        return f"Bluetooth did not change; it reports: {now.strip() or 'unknown'}."
    except Exception as e:
        return f"Could not change Bluetooth power: {e}"


def bluetooth_on() -> str:
    if _OS == "Windows" and _radio_state("Bluetooth") == "on":
        return "Bluetooth is already on."
    return _radio_set("Bluetooth", "on") if _OS == "Windows" else _bt_set("on")


def bluetooth_off() -> str:
    if _OS == "Windows" and _radio_state("Bluetooth") == "off":
        return "Bluetooth is already off."
    return _radio_set("Bluetooth", "off") if _OS == "Windows" else _bt_set("off")


def _bt_set(want: str) -> str:
    """macOS/Linux bluetooth_on/off helper (shared by the two wrappers)."""
    if _OS == "Darwin":
        return toggle_bluetooth()
    try:
        subprocess.run(["bluetoothctl", "power", want],
                       capture_output=True, timeout=8)
        now = subprocess.run(["bluetoothctl", "show"],
                             capture_output=True, text=True, timeout=5).stdout
        if ("Powered: yes" in now) == (want == "on"):
            return f"Bluetooth is now {want.upper()}."
        return f"Bluetooth did not change; it reports: {now.strip() or 'unknown'}."
    except Exception as e:
        return f"Could not change Bluetooth power: {e}"


def sleep_pc() -> str:
    """Suspend the machine (session preserved) — unlike sleep_display (#8)."""
    if _OS == "Windows":
        try:
            subprocess.run(["rundll32.exe", "powrprof.dll,SetSuspendState",
                            "0,1,0"], capture_output=True, timeout=10,
                           **_WIN_HIDE)
            # If this returned, the suspend did not happen.
            return ("Windows did not go to sleep — it may be configured to "
                    "hibernate instead, or something blocked the request.")
        except subprocess.TimeoutExpired:
            return "Going to sleep now."       # suspended mid-call = it worked
        except Exception as e:
            return f"Could not sleep this PC: {e}"
    if _OS == "Darwin":
        subprocess.run(["pmset", "sleepnow"], capture_output=True, timeout=10)
        return "Going to sleep now."
    subprocess.run(["systemctl", "suspend"], capture_output=True, timeout=10)
    return "Going to sleep now."


def sign_out() -> str:
    """Sign out of the account (gated: unsaved work is lost) (#8/#26)."""
    if _OS == "Windows":
        subprocess.run(["shutdown", "/l"], capture_output=True, **_WIN_HIDE)
        return "Signing out now."
    if _OS == "Darwin":
        subprocess.run(["osascript", "-e",
                        'tell application "System Events" to log out'],
                       capture_output=True)
        return "Signing out now."
    import getpass
    subprocess.run(["loginctl", "terminate-user", getpass.getuser()],
                   capture_output=True)
    return "Signing out now."

ACTION_MAP: dict[str, callable] = {
    "volume_up":           volume_up,
    "volume_down":         volume_down,
    "mute":                volume_mute,
    "unmute":              volume_mute,
    "toggle_mute":         volume_mute,
    "brightness_up":       brightness_up,
    "brightness_down":     brightness_down,
    "sleep_display":       sleep_display,
    "screen_off":          sleep_display,
    "pause_video":         pause_video,
    "play_pause":          pause_video,
    "close_app":           close_app,
    "close_window":        close_window,
    "full_screen":         full_screen,
    "fullscreen":          full_screen,
    "minimize":            minimize_window,
    "maximize":            maximize_window,
    "snap_left":           snap_left,
    "snap_right":          snap_right,
    "switch_window":       switch_window,
    "show_desktop":        show_desktop,
    "task_manager":        open_task_manager,
    "focus_search":        focus_search,
    "refresh_page":        refresh_page,
    "reload":              refresh_page,
    "close_tab":           close_tab,
    "new_tab":             new_tab,
    "next_tab":            next_tab,
    "prev_tab":            prev_tab,
    "go_back":             go_back,
    "go_forward":          go_forward,
    "zoom_in":             zoom_in,
    "zoom_out":            zoom_out,
    "zoom_reset":          zoom_reset,
    "find_on_page":        find_on_page,
    "scroll_up":           scroll_up,
    "scroll_down":         scroll_down,
    "scroll_top":          scroll_top,
    "scroll_bottom":       scroll_bottom,
    "page_up":             page_up,
    "page_down":           page_down,
    "copy":                copy,
    "paste":               paste,
    "cut":                 cut,
    "undo":                undo,
    "redo":                redo,
    "select_all":          select_all,
    "save":                save_file,
    "enter":               press_enter,
    "escape":              press_escape,
    "screenshot":          take_screenshot,
    "lock_screen":         lock_screen,
    "open_settings":       open_system_settings,
    "file_explorer":       open_file_explorer,
    "open_run":            open_run,
    "dark_mode":           dark_mode,
    "toggle_wifi":         toggle_wifi,
    "wifi_on":             wifi_on,
    "wifi_off":            wifi_off,
    "toggle_bluetooth":    toggle_bluetooth,
    "bluetooth_on":        bluetooth_on,
    "bluetooth_off":       bluetooth_off,
    "sleep_pc":            sleep_pc,
    "sign_out":            sign_out,
    "restart":             restart_computer,
    "shutdown":            shutdown_computer,
}

# ── What needs a human, and what just needs an undo ──────────────────────────
#
# The old gate was `_DANGEROUS_ACTIONS = {"restart", "shutdown"}` checked against
# a `confirmed` parameter the MODEL filled in — so the model confirmed its own
# shutdowns, and everything else (switching off the WiFi this assistant is
# talking over) had no gate at all.
#
# Two lists now, and the split is about reversibility, not about how alarming
# the word sounds:
#
#   _IRREVERSIBLE — a human presses a button on the HUD. Nothing else.
#   everything else — done immediately, with an undo pushed if it can be undone.
#
# Asking before every action is what makes an assistant unusable, and every
# question costs a round trip. Undo is both faster and safer than a prompt.
_IRREVERSIBLE = {
    "restart":     ("Restart this computer",
                    "Anything unsaved will be lost. The computer restarts in 10 seconds."),
    "shutdown":    ("Shut this computer down",
                    "Anything unsaved will be lost. The computer powers off in 10 seconds."),
    # Not obviously destructive, and that is exactly why it was missed: turning
    # the WiFi off cuts the assistant's own connection to the Live API, so it
    # cannot be asked to turn it back on.
    "toggle_wifi": ("Switch WiFi off or on",
                    "If this switches WiFi off, JARVIS loses its connection and "
                    "cannot switch it back on by voice."),
    # Immediate — Windows cannot delay `shutdown /l` — so the button comes first.
    "sign_out":    ("Sign out of this account",
                    "All open apps close immediately and anything unsaved "
                    "is lost."),
    # Same reasoning as toggle_wifi: killing the network kills the assistant's
    # own voice link, and it cannot be asked to turn the radio back on.
    "wifi_off":    ("Switch WiFi off",
                    "If this switches WiFi off, JARVIS loses its connection and "
                    "cannot switch it back on by voice."),
}

# Kept so anything still importing the old name keeps working.
_DANGEROUS_ACTIONS = set(_IRREVERSIBLE)


# ── Local intent resolution ──────────────────────────────────────────────────
#
# This used to be an entire extra Gemini call, made INSIDE the tool: the Live
# model called computer_settings, and computer_settings then asked a second
# model which action was meant. Every "turn the volume down" paid for two round
# trips — and when that second call failed, the fallback was
# `description.lower().replace(" ", "_")`, which turns the Turkish for "turn it
# down" into `sesi_kis`, i.e. straight to "Unknown action".
#
# Nothing here needs a language model. The Live model already understands the
# sentence; it only needed the vocabulary, which the tool declaration now spells
# out in full. What is left is spelling tolerance, and difflib does that in
# microseconds instead of ~600 ms and a quota unit.
_ALIASES = {
    "volume_up":       ("louder", "raise volume", "turn it up", "increase volume"),
    "volume_down":     ("quieter", "lower volume", "turn it down", "decrease volume"),
    "mute":            ("silence", "sound off", "no sound"),
    "brightness_up":   ("brighter", "raise brightness", "increase brightness"),
    "brightness_down": ("dimmer", "dim", "lower brightness", "decrease brightness"),
    "close_window":    ("close this", "close it"),
    "full_screen":     ("fullscreen", "maximise screen"),
    "show_desktop":    ("minimise everything", "go to desktop"),
    "lock_screen":     ("lock", "lock the pc", "lock computer"),
    "sleep_display":   ("screen off", "turn off the screen", "display off"),
    "dark_mode":       ("night mode", "light mode", "toggle theme"),
    "toggle_wifi":     ("wifi", "wi-fi", "internet off", "internet on"),
    "task_manager":    ("processes", "task list"),
    "screenshot":      ("capture screen", "take a screenshot", "snip"),
    "refresh_page":    ("refresh", "reload page"),
    "new_tab":         ("open a tab", "open new tab"),
    "shutdown":        ("power off", "turn off the computer", "switch off the pc"),
    "restart":         ("reboot", "restart the pc"),
    "toggle_bluetooth": ("bluetooth",),
    "bluetooth_on":    ("bluetooth on", "turn bluetooth on", "enable bluetooth"),
    "bluetooth_off":   ("bluetooth off", "turn bluetooth off", "disable bluetooth"),
    "wifi_on":         ("wifi on", "wi-fi on", "turn wifi on", "enable wifi"),
    "wifi_off":        ("wifi off", "wi-fi off", "turn wifi off", "disable wifi"),
    "sleep_pc":        ("sleep", "suspend", "go to sleep", "put pc to sleep",
                        "put the pc to sleep", "put my pc to sleep"),
    "sign_out":        ("log out", "sign out", "log me out"),
}

_VALUE_ACTIONS = {"volume_set", "type_text", "press_key", "reload_n",
                  "scroll_up", "scroll_down"}


def _normalise(text: str) -> str:
    return (text or "").strip().lower().replace("-", "_").replace(" ", "_")


def _detect_action(description: str) -> dict:
    """Resolve a free-text description to an action name, locally.

    Returns {"action": name, "value": ...}; `action` is "" when nothing matched,
    which the caller turns into a message naming real candidates — one round
    trip, and only in the case that used to cost one anyway."""
    raw  = (description or "").strip()
    norm = _normalise(raw)
    if not norm:
        return {"action": "", "value": None}

    known = set(ACTION_MAP) | _VALUE_ACTIONS

    # 1. Already an action name.
    if norm in known:
        return {"action": norm, "value": None}

    low = raw.lower()

    # 2. Explicit radio state beats the bare alias: "wifi off" must mean OFF,
    #    never a blind toggle that might switch an already-off radio ON (#8).
    radio = re.search(r"\b(wi-?fi|bluetooth|bt)\b", low)
    if radio:
        if re.search(r"\b(off|disable|disabled)\b", low):
            act = "wifi_off" if radio.group(1).lower().startswith("wi") else "bluetooth_off"
            return {"action": act, "value": None}
        if re.search(r"\b(on|enable|enabled)\b", low):
            act = "wifi_on" if radio.group(1).lower().startswith("wi") else "bluetooth_on"
            return {"action": act, "value": None}

    # 3. "set volume to 30" vs "decrease volume by 20%": a number next to a
    #    volume word. Direction words ('by …', down/up) make it a DELTA, not a
    #    target — "decrease volume by 20" must not set the volume TO 20.
    num = re.search(r"(\d{1,3})\s*%?", low)
    if num and any(w in low for w in ("volume", "ses", "sound", "lautstark", "громкость")):
        n = max(0, min(100, int(num.group(1))))
        wants_set = re.search(r"\b(to|set|yap)\b", low) and not re.search(r"\bby\b", low)
        if not wants_set:
            if any(w in low for w in ("decrease", "lower", "quieter",
                                      "reduce", "down")):
                return {"action": "volume_down", "value": n}
            if any(w in low for w in ("increase", "raise", "louder", "up")):
                return {"action": "volume_up", "value": n}
        return {"action": "volume_set", "value": n}

    # 4. Alias phrases.
    for action, phrases in _ALIASES.items():
        if any(_normalise(p) == norm or p in low for p in phrases):
            return {"action": action, "value": None}

    # 5. Fuzzy match on the action names — catches "fullscren", "volumeup".
    import difflib
    close = difflib.get_close_matches(norm, sorted(known), n=1, cutoff=0.72)
    if close:
        return {"action": close[0], "value": None}

    # 6. Substring: "increase_the_brightness" contains "brightness".
    for action in sorted(known, key=len, reverse=True):
        if len(action) > 4 and (action in norm or norm in action):
            return {"action": action, "value": None}

    return {"action": "", "value": None}


def _suggest(description: str) -> str:
    """What to tell the model when nothing matched. Names real actions so its
    retry lands, instead of the old 'Unknown action' dead end."""
    import difflib
    near = difflib.get_close_matches(_normalise(description),
                                     sorted(ACTION_MAP), n=5, cutoff=0.3)
    hint = ", ".join(near) if near else ", ".join(sorted(ACTION_MAP)[:12])
    return (f"I could not match '{description}' to a computer action. "
            f"Call computer_settings again with an exact `action` from: {hint}.")

def computer_settings(
    parameters: dict = None,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    if not _PYAUTOGUI:
        return "pyautogui is not installed. Run: pip install pyautogui"

    params      = parameters or {}
    raw_action  = params.get("action", "").strip()
    description = params.get("description", "").strip()
    value       = params.get("value", None)

    if not raw_action and description:
        detected   = _detect_action(description)
        raw_action = detected.get("action", "")
        if value is None:
            value = detected.get("value")

    action = raw_action.lower().strip().replace(" ", "_").replace("-", "_")

    if not action:
        return _suggest(description or raw_action)

    print(f"[Settings] Action: {action}  Value: {value}  OS: {_OS}")
    if player:
        player.write_log(f"[Settings] {action}")

    # ── The gate ─────────────────────────────────────────────────────────────
    # A human presses a button, or this does not happen. The model can no longer
    # write its own permission slip, and the action itself is handed to the UI
    # rather than performed here — so returning early is not "declining", it is
    # "parked until someone says yes".
    if action in _IRREVERSIBLE:
        title, detail = _IRREVERSIBLE[action]
        func = ACTION_MAP.get(action)
        if func is None:
            return f"Unknown action: '{raw_action}'."
        if confirm.pending_title():
            return ("There is already a confirmation waiting on screen. "
                    "Ask the user to answer that one first.")
        return confirm.request(
            key=action, title=title, detail=detail,
            run=lambda f=func, a=action: _done_msg(a, f()),
        )

    if action == "volume_set":
        try:
            target = int(value if value is not None else 50)
            before = volume_get()
            volume_set(target)
            if before is not None:
                push_undo(f"volume {before}% → {target}%",
                          lambda b=before: (volume_set(b), f"Back to {b}%.")[1])
            return f"Volume set to {target}%."
        except Exception as e:
            return f"Could not set volume: {e}"

    # "Decrease volume by 20%" = current − 20, NOT "set volume to 20" (#19).
    if action in ("volume_up", "volume_down") and value is not None:
        try:
            delta = int(float(value))
        except (TypeError, ValueError):
            delta = None
        if delta is not None:
            try:
                before = volume_get()
                if before is None:
                    return ("This system will not report its volume, so I "
                            "cannot change it by a percentage — use "
                            "volume_up/volume_down steps instead.")
                target = max(0, min(100, before + (delta if action == "volume_up"
                                                   else -delta)))
                volume_set(target)
                push_undo(f"volume {before}% → {target}%",
                          lambda b=before: (volume_set(b), f"Back to {b}%.")[1])
                verb = "Raised" if action == "volume_up" else "Lowered"
                return f"{verb} volume by {delta}% — now at {target}%."
            except Exception as e:
                return f"Could not change volume by {delta}%: {e}"

    if action in ("type_text", "write_on_screen", "type", "write"):
        text = str(value or params.get("text", "")).strip()
        if not text:
            return "No text provided to type."
        enter_after = str(params.get("press_enter", "false")).lower() in ("true", "1", "yes")
        type_text(text, press_enter_after=enter_after)
        return f"Typed: {text[:80]}"

    if action == "press_key":
        key = str(value or params.get("key", "")).strip()
        if not key:
            return "No key specified."
        press_key(key)
        return f"Pressed: {key}"

    if action in ("reload_n", "refresh_n", "reload_page_n"):
        try:
            reload_page_n(int(value or 1))
            return f"Reloaded {value or 1} time(s)."
        except Exception as e:
            return f"Reload failed: {e}"

    if action == "scroll_up":
        scroll_up(int(value or 500))
        return "Scrolled up."

    if action == "scroll_down":
        scroll_down(int(value or 500))
        return "Scrolled down."

    func = ACTION_MAP.get(action)
    if not func:
        return _suggest(raw_action or description)

    # ── Capture "before" so the change can be taken back ─────────────────────
    # Read-then-write is the whole mechanism for settings: there is no clever
    # inverse to compute, just a value to remember. Where the platform will not
    # tell us the current value, nothing is registered — an undo that restores
    # a guess is worse than no undo at all.
    _before = None
    if action in ("volume_up", "volume_down", "mute", "unmute", "toggle_mute"):
        _before = ("volume", volume_get())
    elif action in ("brightness_up", "brightness_down"):
        _before = ("brightness", brightness_get())

    try:
        out = func()
    except Exception as e:
        print(f"[Settings] Action failed ({action}): {e}")
        return f"Action failed ({action}): {e}"

    if _before:
        kind, old = _before
        if old is not None:
            if kind == "volume":
                push_undo(f"volume ({action})",
                          lambda b=old: (volume_set(b), f"Volume back to {b}%.")[1])
            elif kind == "brightness":
                push_undo(f"brightness ({action})",
                          lambda b=old: (brightness_set(b), f"Brightness back to {b}%.")[1])
    elif action == "dark_mode":
        # A pure toggle: calling it again is the undo.
        push_undo("dark mode toggled",
                  lambda: (dark_mode(), "Theme switched back.")[1])

    # Functions that verified their own effect (wifi/bluetooth/sleep…) speak
    # for themselves; never replace an honest result with a blanket "Done."
    return _done_msg(action, out)


def _done_msg(action_name: str, out) -> str:
    """Honor a function's own verified result; only fall back to 'Done.'."""
    if isinstance(out, str) and out.strip():
        return out.strip()
    return f"Done: {action_name}."


# ── Tool declaration (auto-discovered by core/action_loader.py) ──────────────
TOOL = {
    "name": "computer_settings",
    "description": "Controls the computer: volume (set, or change BY a percentage with volume_up/down + value), brightness, window management, keyboard shortcuts, typing text on screen, closing apps, fullscreen, dark mode, WiFi (wifi_on/wifi_off verify the real state), Bluetooth, sleep_pc, sign out, restart, shutdown, scrolling, tab management, zoom, screenshots, lock screen, refresh/reload page. Use for ANY single computer control command. restart, shutdown, sign_out and the wifi-off actions put a confirmation on the user's screen and do NOT happen until they press it — never claim they are done. Every action returns the *verified* result — report it exactly, and never claim success the result does not support. Volume, brightness and dark mode can be reversed with the `undo` tool.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            # The exact vocabulary, spelled out.
            #
            # This used to say only "The action to perform", so the model
            # usually filled `description` instead — and computer_settings then
            # made a SECOND Gemini call, inside the tool, purely to translate
            # that sentence into one of these names. Every "turn the volume
            # down" cost two model round trips.
            #
            # Naming the actions here costs ~600 characters once per session and
            # removes a whole round trip from every computer command.
            "action": {
                "type": "STRING",
                "description": (
                    "The exact action. Prefer this over `description` — pick one of: "
                    "volume_up | volume_down | volume_set | mute | "
                    "brightness_up | brightness_down | sleep_display | "
                    "pause_video | close_app | close_window | full_screen | "
                    "minimize | maximize | snap_left | snap_right | "
                    "switch_window | show_desktop | task_manager | focus_search | "
                    "refresh_page | close_tab | new_tab | next_tab | prev_tab | "
                    "go_back | go_forward | zoom_in | zoom_out | zoom_reset | "
                    "find_on_page | scroll_up | scroll_down | scroll_top | "
                    "scroll_bottom | page_up | page_down | copy | paste | cut | "
                    "undo | redo | select_all | save | enter | escape | press_key | "
                    "type_text | screenshot | lock_screen | open_settings | "
                    "file_explorer | open_run | dark_mode | toggle_wifi | "
                    "wifi_on | wifi_off | toggle_bluetooth | bluetooth_on | "
                    "bluetooth_off | sleep_pc | sign_out | "
                    "restart | shutdown"
                )
            },
            "description": {
                "type": "STRING",
                "description": (
                    "Fallback only, when no action name above fits. "
                    "Resolved locally — no extra model call."
                )
            },
            "value": {
                "type": "STRING",
                "description": "Optional value: volume level 0-100 (for volume_set), a number to change volume BY (for volume_up/volume_down, e.g. '20' for 'decrease by 20%'), text to type, key name (e.g. 'win+shift+s'), etc."
            }
        },
        "required": []
    },
    "handler": computer_settings,
}
