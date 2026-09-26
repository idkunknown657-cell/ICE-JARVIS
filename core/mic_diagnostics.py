"""core/mic_diagnostics.py — is the microphone actually delivering voice?

WHY THIS EXISTS
    "JARVIS can't hear me" was being answered with a device *list*. A device
    that exists is not a device that works: the desktop failure mode was a
    headset whose microphone the default host API opened silently at zero
    signal — the stream was fine, the picker looked right, and no audio ever
    arrived. Only a real capture with a level check separates
    "detected" from "working", so that is what this module does.

WHAT IT ANSWERS
    devices()      — every recording endpoint worth showing: name, API,
                     channels, default rates, and whether Windows calls it the
                     default input or default communication device
    permission()   — the Windows microphone privacy gates, read from the
                     registry (master switch, store apps, desktop apps)
    test_device()  — opens the device for ~0.9 s and MEASURES the signal:
                     peak RMS, an estimate of the device's own noise floor and
                     how much headroom ordinary speech has. "Working" requires
                     a real waveform, not just a successful open.
    diagnose()     — the whole chain in one call: device present → permission →
                     capture → signal, ending in a plain-language problem line
                     and the fix. This is what the Settings page and the voice
                     diagnostic tool both render.

SIGNAL THRESHOLDS
    Room silence on a typical mic is RMS 40–120; ordinary speech at a normal
    distance is 800–6000. The boundaries sit in the gaps between those
    populations, and a device whose own noise floor is high gets credited for
    it rather than being failed by it (a "working" mic that hisses at RMS 300
    all day is still a working mic — a quiet-speech problem, not a dead one).

NOTHING HERE RAISES and nothing here opens a stream that stays open. The test
stream is open → measure → close, off the UI thread.
"""
from __future__ import annotations

import platform
import time

def _is_windows() -> bool:
    return platform.system() == "Windows"


# ── Signal thresholds (int16 RMS) ────────────────────────────────────────────
SILENCE_RMS = 90.0        # below this: electrically dead or fully gated
FAINT_RMS = 240.0         # above silence but below real speech
NOISE_FLOOR_MAX = 420.0   # device idle noise above this = hot/noisy input
SPEECH_HEADROOM_WANT = 3.0

# ── Windows permission registry (ConsentStore) ──────────────────────────────
_CONSENT_KEY = (r"Software\Microsoft\Windows\CurrentVersion"
                r"\CapabilityAccessManager\ConsentStore\microphone")


def _reg_value(path: str, value: str = "Value"):
    """Read one registry string. None when absent — absence is not 'denied',
    it is 'this Windows build does not track it'."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as k:
            v, _ = winreg.QueryValueEx(k, value)
            return str(v)
    except FileNotFoundError:
        return None
    except Exception:
        return None


def windows_permission() -> dict:
    """The three Windows privacy gates that can mute every microphone without
    any of them looking broken: the master switch, store apps, desktop apps.

    'unknown' means the registry key does not exist on this build (older
    Windows) — never reported as a failure, because a gate we cannot read is
    not a gate we can claim is closed."""
    if not _is_windows():
        return {"supported": False, "master": "unknown",
                "store_apps": "unknown", "desktop_apps": "unknown",
                "allowed": True}
    master = _reg_value(_CONSENT_KEY)
    store = _reg_value(_CONSENT_KEY + r"\NonPackaged")
    def _state(v) -> str:
        if v is None:
            return "unknown"
        return "allowed" if v.lower() == "allow" else "denied"
    m, s = _state(master), _state(store)
    allowed = not (m == "denied" or s == "denied")
    return {"supported": True, "master": m, "store_apps": s,
            "desktop_apps": s, "allowed": allowed}


def open_mic_settings() -> bool:
    """Open the Windows microphone privacy page. Returns whether it launched."""
    try:
        import subprocess
        if _is_windows():
            subprocess.Popen(["cmd", "/c", "start", "", "ms-settings:privacy-microphone"],
                             shell=False,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return True
    except Exception:
        pass
    return False


# ── Device inventory ─────────────────────────────────────────────────────────

def devices(refresh: bool = False) -> list[dict]:
    """Every recording endpoint worth showing, one row per hardware device.

    sounddevice returns one entry per (device × host API) — the same Realtek
    microphone appears under MME, DirectSound, WASAPI and WDM-KS. The picker
    collapses those to one row on the API the app actually opens; diagnostics
    show the row for every API that can carry audio, because 'works on WASAPI,
    dead on DirectSound' is precisely the desktop failure being diagnosed.
    """
    try:
        import sounddevice as sd
    except Exception as e:
        return [{"name": f"(sounddevice unavailable: {e})", "api": "",
                 "channels": 0, "rates": "", "default": False,
                 "default_comm": False, "index": -1, "openable": False}]

    try:
        devs = list(sd.query_devices())
        apis = [a.get("name", "") for a in sd.query_hostapis()]
        default_in = sd.default.device[0]
    except Exception:
        return []

    try:
        comm_default = _windows_comm_default_index(devs)
    except Exception:
        comm_default = None

    rows: list[dict] = []
    for idx, d in enumerate(devs):
        if (d.get("max_input_channels") or 0) <= 0:
            continue
        name = (d.get("name") or "").strip()
        api = ""
        try:
            api = apis[d["hostapi"]] if d.get("hostapi", -1) < len(apis) else ""
        except Exception:
            pass
        rates = d.get("default_samplerate") or 0
        rows.append({
            "name": name,
            "api": api,
            "channels": int(d.get("max_input_channels") or 0),
            "rates": f"{int(rates)} Hz" if rates else "unknown",
            "default": (idx == default_in),
            "default_comm": (comm_default is not None and idx == comm_default),
            "index": idx,
            "openable": True,
        })
    return rows


def _windows_comm_default_index(devs) -> int | None:
    """Which raw index is the Windows *default communication device*.

   sounddevice/PortAudio exposes only the default multimedia device, so this
    reads the console endpoint state from the registry via MMDevice API —
    best effort; None when it cannot be determined (which simply means the
    column shows nothing)."""
    try:
        if not _is_windows():
            return None
        import comtypes            # noqa: F401  (client of the MMDevice API)
        from pycaw.constants import EDataFlow, DEVICE_STATE
        from pycaw.pycaw import AudioUtilities
        enum = AudioUtilities.GetDeviceEnumerator()
        comm = enum.GetDefaultAudioEndpoint(EDataFlow.eCapture.value,
                                            1)   # eCommunications
        dev_id = comm.GetId()
        # Match by endpoint id substring against PortAudio's device ids is not
        # portable; instead match by name.
        from pycaw.constants import CLSID_MMDeviceEnumerator
        name = None
        try:
            store = AudioUtilities.CreateDevice(dev_id)
            name = (store.FriendlyName or "") if hasattr(store, "FriendlyName") else None
        except Exception:
            pass
        if not name:
            return None
        best, best_len = None, 0
        for idx, d in enumerate(devs):
            if (d.get("max_input_channels") or 0) <= 0:
                continue
            n = (d.get("name") or "").strip()
            if n and n in name and len(n) > best_len:
                best, best_len = idx, len(n)
        return best
    except Exception:
        return None


# ── The real test: open it and measure ───────────────────────────────────────

def test_device(index: int | None = None, seconds: float = 0.9) -> dict:
    """Open the microphone and MEASURE. Never raises.

    Returns {opened, delivered, peak_rms, floor_rms, speech_headroom,
             noisy, verdict, message}. `opened` means the stream started;
             `delivered` means frames actually arrived; the RMS numbers mean
             a real waveform was captured. A device can be opened AND deliver
             AND still be 'no_signal' — that is the desktop bug this module
             exists to name.
    """
    out = {"opened": False, "delivered": False, "peak_rms": 0.0,
           "floor_rms": 0.0, "speech_headroom": 0.0, "noisy": False,
           "verdict": "no_signal", "message": ""}
    try:
        import numpy as np
        import sounddevice as sd
    except Exception as e:
        out["message"] = f"audio libraries unavailable: {e}"
        return out

    blocks: list = []
    frames_seen = [0]
    opened_err = ""

    def _cb(indata, n, *_a):
        frames_seen[0] += n
        try:
            blocks.append(np.frombuffer(indata, dtype=np.int16).astype(np.float32))
        except Exception:
            pass

    try:
        st = sd.InputStream(samplerate=16000, channels=1, dtype="int16",
                            blocksize=1024, device=index, callback=_cb)
        st.start()
        out["opened"] = True
        t0 = time.monotonic()
        while time.monotonic() - t0 < max(0.3, float(seconds)):
            time.sleep(0.05)
        st.stop(); st.close()
        out["delivered"] = frames_seen[0] > 0
        if not out["delivered"]:
            out["message"] = ("the stream opened but the driver delivered no "
                              "audio frames (device occupied, gated, or a "
                              "silent endpoint)")
            return out
    except Exception as e:
        out["message"] = _explain_open_error(str(e))
        return out

    if not blocks:
        out["message"] = "frames arrived but could not be decoded"
        return out

    import numpy as _np
    pcm = _np.concatenate(blocks)
    # Per-1024-sample-block RMS: block 0 is the device's own noise floor,
    # the max over the window is the loudest thing the mic heard.
    n_blocks = max(1, len(pcm) // 1024)
    rms_per_block = []
    for i in range(n_blocks):
        chunk = pcm[i * 1024:(i + 1) * 1024]
        if chunk.size:
            rms_per_block.append(float(_np.sqrt(_np.mean(chunk * chunk))))
    floor = min(rms_per_block) if rms_per_block else 0.0
    peak = max(rms_per_block) if rms_per_block else 0.0
    out["floor_rms"] = round(floor, 1)
    out["peak_rms"] = round(peak, 1)
    out["speech_headroom"] = round(peak / floor, 1) if floor > 1 else float(peak > 0)

    if peak < SILENCE_RMS:
        out["verdict"] = "no_signal"
        out["message"] = ("no audio signal above silence — the device opens "
                          "but is muted, gated by Windows privacy, or not "
                          "the microphone you are speaking into")
    elif peak < FAINT_RMS:
        out["verdict"] = "faint"
        out["message"] = ("signal barely above silence — try speaking; if this "
                          "repeats, the input gain in Windows sound settings "
                          "is very low")
    else:
        out["noisy"] = floor > NOISE_FLOOR_MAX
        headroom = peak / floor if floor > 1 else 99.0
        if headroom < SPEECH_HEADROOM_WANT and peak < FAINT_RMS * 4:
            out["verdict"] = "faint"
            out["message"] = ("signal present but with almost no headroom over "
                              "the device's own noise — raise the input level")
        else:
            out["verdict"] = "ok"
            out["message"] = ("signal detected and it looks like a microphone "
                              "that carries speech")
    return out


def _explain_open_error(msg: str) -> str:
    """PortAudio error text → a sentence a person can act on."""
    low = (msg or "").lower()
    if "invalid device" in low or "device unavailable" in low:
        return ("the device disappeared or is unavailable — unplug/replug or "
                "pick another microphone")
    if "occupied" in low or "in use" in low or "already in use" in low:
        return ("another application is holding this microphone exclusively — "
                "close it (or its exclusive-mode setting) and retry")
    if "format is not supported" in low or "invalid sample rate" in low or \
       "unanticipated host error" in low or "unsupported" in low:
        return ("the driver rejected the capture format — Windows 'exclusive "
                "mode' or a fixed-rate device; allow shared mode in the "
                "device's Advanced settings")
    if "access denied" in low or "unauthorised" in low or "unauthorized" in low:
        return ("access denied — Windows microphone privacy is blocking "
                "desktop apps (see Permission above)")
    return msg or "the stream could not be opened"


# ── Chain diagnosis ──────────────────────────────────────────────────────────

def diagnose(index: int | None = None, seconds: float = 0.9) -> dict:
    """The full chain, honestly labelled. This is the 'what is actually
    broken' answer for the Settings page and the voice diagnostic tool."""
    perm = windows_permission()
    devs = devices()
    if index is None:
        try:
            import sounddevice as sd
            index = sd.default.device[0]
        except Exception:
            index = None
    chosen = next((d for d in devs if d["index"] == index), None)
    if chosen is None and devs:
        chosen = next((d for d in devs if d.get("default")), devs[0])

    t = test_device(index, seconds=seconds)

    problems: list[str] = []
    fixes: list[str] = []

    device_present = bool(devs)
    capture_ok = bool(t["opened"] and t["delivered"])
    signal_ok = t["verdict"] in ("ok", "faint")

    if not device_present:
        problems.append("No recording device is visible to JARVIS at all.")
        fixes.append("Check the microphone is plugged in and enabled in "
                     "Windows sound settings (some headset mics are disabled "
                     "there by default).")
    if perm["supported"] and not perm["allowed"]:
        which = ("Microphone access" if perm["master"] == "denied"
                 else "Desktop apps' microphone access")
        problems.append(f"{which} is switched off in Windows privacy settings.")
        fixes.append("Settings → Privacy & security → Microphone → allow "
                     "desktop apps (button below opens it).")
    if device_present and not capture_ok:
        problems.append("The microphone could not be captured: " + t["message"])
        if "privacy" in t["message"].lower() or "denied" in t["message"].lower():
            fixes.append("Allow desktop apps to use the microphone (button below).")
        else:
            fixes.append("Pick a different microphone below, then press Test — "
                         "and close apps that may hold the mic (Discord, Teams, "
                         "OBS).")
    if capture_ok and not signal_ok:
        problems.append("The microphone opens but delivers no usable signal: "
                        + t["message"])
        fixes.append("Speak while testing; check this is the device you are "
                     "actually talking into (Windows 'default' moves when a "
                     "headset is plugged in), and raise its input level in "
                     "Windows sound settings.")

    verdict = "ok" if (device_present and perm["allowed"] and signal_ok) else \
              ("faint" if capture_ok and t["verdict"] == "faint" else "failed")

    return {
        "device_present": device_present,
        "selected_device": (chosen or {}).get("name", ""),
        "selected_index": index,
        "permission": perm,
        "capture": "working" if capture_ok else "failed",
        "signal": ("detected" if t["verdict"] == "ok"
                   else "weak" if t["verdict"] == "faint"
                   else "no signal"),
        "levels": {"peak_rms": t["peak_rms"], "floor_rms": t["floor_rms"],
                   "speech_headroom": t["speech_headroom"], "noisy": t["noisy"]},
        "problems": problems,
        "fixes": fixes,
        "verdict": verdict,
        "message": t["message"],
    }
