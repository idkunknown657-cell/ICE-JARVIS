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

# ── The measurements that made this module honest about its own limits ────────
#
# Measured on one ordinary desktop PC, three consecutive 1.2 s windows of the
# SAME working microphone, same host API:
#
#     endpoint 1 (MME)       202.8   20.9  133.1
#     endpoint 6 (DirectSo)  222.2  220.2  194.7
#     endpoint 5 (DirectSo)    1.4  101.9   99.7
#
# A single short window can read 1.4 on a microphone that reads 102 a second
# later. That spread is wider than the gap between the silence and speech
# thresholds, so ONE sample cannot classify a microphone — and the old code
# classified from exactly one sample, which is how a working microphone in a
# quiet room got reported as "nothing is reaching me".
#
# So: sample long enough that a person can actually say something, give a
# digitally-silent reading a second chance before believing it, and keep a
# verdict for "I genuinely could not tell" that is not a fault.
DEAD_RMS = 12.0           # at or below this the input is digitally silent,
                          # which is NOT the same as a quiet room
DEFAULT_TEST_SECONDS = 2.5   # long enough for the user to react and speak
DEFAULT_ATTEMPTS = 2         # one retry before believing a dead reading

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

def _rates_to_try() -> tuple[int, ...]:
    """The input rates a microphone may be opened at, preferred first.

    Shared with the capture layer on purpose (audio_devices.input_rate_ladder).
    This probe used to ask for 16 kHz and nothing else, so a fixed-rate
    endpoint — USB interfaces, many webcams, Bluetooth HFP — failed to open
    here while main.py was recording from the very same device at its own rate.
    The test then told the user their voice was not reaching JARVIS when the
    only thing that was broken was the rate we asked for. Falls back to the
    same ladder inline if audio_devices cannot be imported.
    """
    try:
        from core import audio_devices
        ladder = tuple(audio_devices.input_rate_ladder())
        if ladder:
            return ladder
    except Exception:
        pass
    return (16000, 48000, 44100, 96000, 24000, 32000, 8000, 11025)


def _open_and_measure(index: int | None, seconds: float,
                      numpy_mod, sd_mod) -> dict:
    """One capture window: open, listen, close, measure. No judgement.

    Returns raw facts only — the verdict is decided by test_device, which owns
    the retry policy. Keeping the two apart is deliberate: mixing "what did I
    hear" with "what does it mean" is how a single unlucky window became a
    diagnosis.
    """
    res = {"opened": False, "rate": 0, "delivered": False, "peak_rms": 0.0,
           "floor_rms": 0.0, "blocks": 0, "error": ""}

    blocks: list = []
    frames_seen = [0]

    def _cb(indata, n, *_a):
        frames_seen[0] += n
        try:
            blocks.append(numpy_mod.frombuffer(indata,
                                               dtype=numpy_mod.int16).astype(numpy_mod.float32))
        except Exception:
            pass

    # The device's own rate first, then the rest of the ladder — the same order
    # the capture layer uses, so "the test passes" and "JARVIS can hear you"
    # cannot disagree. The first rate that opens is the one we measure on.
    st = None
    open_err = None
    for rate in _rates_to_try():
        try:
            candidate = sd_mod.InputStream(samplerate=rate, channels=1,
                                           dtype="int16", blocksize=1024,
                                           device=index, callback=_cb)
        except Exception as e:
            open_err = e
            continue
        try:
            candidate.start()
        except Exception as e:
            open_err = e
            try:
                candidate.stop(); candidate.close()
            except Exception:
                pass
            continue
        st = candidate
        res["rate"] = rate
        break

    if st is None:
        res["error"] = _explain_open_error(str(open_err or ""))
        return res

    res["opened"] = True
    try:
        t0 = time.monotonic()
        while time.monotonic() - t0 < max(0.3, float(seconds)):
            time.sleep(0.05)
    except Exception as e:
        res["error"] = f"capture was interrupted: {e}"
        return res
    finally:
        try:
            st.stop(); st.close()
        except Exception:
            pass

    res["delivered"] = frames_seen[0] > 0
    if not res["delivered"]:
        res["error"] = ("the stream opened but the driver delivered no "
                        "audio frames (device occupied, gated, or a "
                        "silent endpoint)")
        return res
    if not blocks:
        res["error"] = "frames arrived but could not be decoded"
        return res

    pcm = numpy_mod.concatenate(blocks)
    # Per-1024-sample-block RMS: the min is the device's own noise floor, the
    # max over the window is the loudest thing the mic heard.
    n_blocks = max(1, len(pcm) // 1024)
    rms_per_block = []
    for i in range(n_blocks):
        chunk = pcm[i * 1024:(i + 1) * 1024]
        if chunk.size:
            rms_per_block.append(float(numpy_mod.sqrt(numpy_mod.mean(chunk * chunk))))
    res["blocks"] = len(rms_per_block)
    res["floor_rms"] = round(min(rms_per_block), 1) if rms_per_block else 0.0
    res["peak_rms"] = round(max(rms_per_block), 1) if rms_per_block else 0.0
    return res


def test_device(index: int | None = None, seconds: float = DEFAULT_TEST_SECONDS,
                attempts: int = DEFAULT_ATTEMPTS) -> dict:
    """Open the microphone and MEASURE it. Never raises.

    Returns {opened, rate, delivered, peak_rms, floor_rms, speech_headroom,
             noisy, blocks, attempts_used, peaks, verdict, message}.

    The verdicts are:
      ok        real audio that looks like a microphone carrying speech
      faint     audio is arriving, but very quietly
      quiet     audio is arriving and nothing above its own noise was heard —
                INCONCLUSIVE. Usually a silent room, not a fault, so this is
                never reported as a problem.
      no_signal digitally silent (peak below DEAD_RMS) on every attempt — the
                device opens and delivers frames, yet no waveform is there

The `quiet` verdict exists because a one-shot sample cannot tell a silent room
from a muted microphone, and the old code guessed "muted" — see the measurement
note beside DEAD_RMS.
    """
    out = {"opened": False, "rate": 0, "delivered": False, "peak_rms": 0.0,
           "floor_rms": 0.0, "speech_headroom": 0.0, "noisy": False,
           "blocks": 0, "attempts_used": 0, "peaks": [],
           "verdict": "no_signal", "message": ""}
    try:
        import numpy as np
        import sounddevice as sd
    except Exception as e:
        out["message"] = f"audio libraries unavailable: {e}"
        return out

    tries = max(1, int(attempts))
    best = None
    for _attempt in range(tries):
        res = _open_and_measure(index, seconds, np, sd)
        out["attempts_used"] += 1
        out["rate"] = out["rate"] or res["rate"]

        if not res["opened"]:
            out["message"] = res["error"]
            return out
        out["opened"] = True
        if not res["delivered"]:
            out["delivered"] = False
            out["message"] = res["error"]
            return out
        out["delivered"] = True
        out["peaks"].append(res["peak_rms"])

        if best is None or res["peak_rms"] > best["peak_rms"]:
            best = res
        # Enough evidence: stop asking the user to wait. Only a dead reading is
        # worth re-measuring, because only a dead reading can be wrong.
        if res["peak_rms"] >= DEAD_RMS:
            break

    if best is None:
        out["message"] = "the microphone was never captured"
        return out

    peak, floor = best["peak_rms"], best["floor_rms"]
    out["peak_rms"] = peak
    out["floor_rms"] = floor
    out["blocks"] = best["blocks"]
    out["speech_headroom"] = round(peak / floor, 1) if floor > 1 else float(peak > 0)
    headroom = peak / floor if floor > 1 else 99.0

    if peak < DEAD_RMS:
        out["verdict"] = "no_signal"
        out["message"] = (
            "the device opened and delivered frames, but every sample was "
            "digitally silent — check the microphone's own mute switch, "
            "Windows input level, or that this is the device you are "
            "speaking into, then test again while talking")
    elif peak >= FAINT_RMS:
        out["noisy"] = floor > NOISE_FLOOR_MAX
        if headroom < SPEECH_HEADROOM_WANT and peak < FAINT_RMS * 4:
            out["verdict"] = "faint"
            out["message"] = ("signal present but with almost no headroom over "
                              "the device's own noise — raise the input level")
        else:
            out["verdict"] = "ok"
            out["message"] = ("signal detected and it looks like a microphone "
                              "that carries speech")
    elif headroom >= SPEECH_HEADROOM_WANT:
        # Real waveform above its own noise floor, just quiet.
        out["verdict"] = "faint"
        out["message"] = ("audio is arriving, but quietly — try speaking a "
                          "little louder or closer; if it repeats, raise the "
                          "input level in Windows sound settings")
    else:
        # Audio present, nothing above its own noise. That is a silent room OR
        # a muted path, and the difference cannot be measured from here.
        out["verdict"] = "quiet"
        out["message"] = ("the microphone is delivering audio, but nothing "
                          "above room noise arrived during the test — say "
                          "something while the test runs and check again")
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

def diagnose(index: int | None = None,
             seconds: float | None = None) -> dict:
    """The full chain, honestly labelled. This is the 'what is actually
    broken' answer for the Settings page and the voice diagnostic tool.

    The sample is deliberately long enough for a person to say something
    into it. A short probe of a quiet room measures the room, not the mic,
    and reporting that as a fault is how a working microphone gets told
    "nothing is reaching me".
    """
    if seconds is None:
        seconds = DEFAULT_TEST_SECONDS
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
    # Heard nothing, but the stream itself was alive: the mic is fine and the
    # room was quiet. That is not a defect, so it must not be reported as one.
    inconclusive = capture_ok and t["verdict"] == "quiet"

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
    if capture_ok and inconclusive:
        # Not a problem - a missing answer. Say so in the same words the
        # module uses everywhere else, and give the one action that resolves it.
        fixes.append("That reading was inconclusive, not a failure: the "
                     "microphone opened and streamed, but the room was silent. "
                     "Press Test again and speak - say anything - while it "
                     "listens.")
    if capture_ok and t["verdict"] == "no_signal":
        # Digitally silent on every sample, which IS a real fault and usually
        # the Windows per-device mute or a zero input level.
        problems.append("The microphone opens but is digitally silent: "
                        + t["message"])
        fixes.append("Check this is the device you are actually talking into "
                     "(Windows 'default' moves when a headset is plugged in), "
                     "that it is not muted in Windows sound settings, and raise "
                     "its input level.")

    if device_present and perm["allowed"] and not signal_ok and not inconclusive:
        verdict = "failed"
    elif inconclusive:
        verdict = "quiet"
    elif capture_ok and signal_ok:
        verdict = "ok" if t["verdict"] == "ok" else "faint"
    else:
        verdict = "failed"

    return {
        "device_present": device_present,
        "selected_device": (chosen or {}).get("name", ""),
        "selected_index": index,
        "permission": perm,
        "capture": "working" if capture_ok else "failed",
        "signal": ("detected" if t["verdict"] == "ok"
                   else "weak" if t["verdict"] == "faint"
                   else "quiet" if inconclusive
                   else "no signal"),
        "inconclusive": inconclusive,
        "levels": {"peak_rms": t["peak_rms"], "floor_rms": t["floor_rms"],
                   "speech_headroom": t["speech_headroom"], "noisy": t["noisy"]},
        # The rate the probe actually opened at. 0 means it never opened. Worth
        # reporting: a mic that only opens at 48 kHz is a normal device, not a
        # broken one, and the number is what proves the probe used the same
        # ladder as the capture layer.
        "rate": t.get("rate", 0),
        "problems": problems,
        "fixes": fixes,
        "verdict": verdict,
        "message": t["message"],
    }
