from __future__ import annotations

import collections
import json
import math
import os
import platform
import random
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil

if platform.system() == "Windows":
    _WIN_HIDE: dict = {"creationflags": subprocess.CREATE_NO_WINDOW}
else:
    _WIN_HIDE: dict = {}

from PyQt6.QtCore import (
    QAbstractNativeEventFilter, QEasingCurve, QLineF, QMimeData, QObject,
    QParallelAnimationGroup, QPointF, QPropertyAnimation, QRect, QRectF, QSize,
    Qt, QTimer, QUrl, pyqtSignal,
)
from PyQt6.QtGui import (
    QAction, QBrush, QColor, QConicalGradient, QDragEnterEvent, QDropEvent, QFont,
    QFontDatabase, QIcon, QKeySequence, QLinearGradient, QPainter, QPainterPath,
    QPen, QPixmap, QRadialGradient, QShortcut,
)
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMainWindow, QMenu, QPushButton, QScrollArea,
    QSizePolicy, QStackedWidget, QSystemTrayIcon, QTextEdit, QVBoxLayout, QWidget,
    QProgressBar,
)

try:
    from core.avatar import HoloAvatar
except Exception:      # pragma: no cover — HUD must never die over cosmetics
    HoloAvatar = None


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent

BASE_DIR   = _base_dir()
CONFIG_DIR = BASE_DIR / "config"
API_FILE   = CONFIG_DIR / "api_keys.json"


def _read_full_config() -> dict:
    """Read api_keys.json config dict. Returns {} on any error."""
    try:
        return json.loads(API_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _coerce_key(v) -> str:
    """API-key values must always reach Qt as plain strings. Anything that is
    not a string/number (a hand-edited or corrupt config can contain a dict or
    list) becomes \"\" — a non-string feeding QLineEdit raises a fatal TypeError
    inside a Qt slot, which aborts the whole process."""
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return str(v).strip()
    return ""


def _sanitize_provider_rows(rows) -> list[dict]:
    """Coerce free_providers config into clean [{name, base_url, api_key,
    model}] dicts. Drops non-dict rows and rows without a name; string-casts
    every field so a malformed file can never crash the settings panel."""
    out: list[dict] = []
    if isinstance(rows, (list, tuple)):
        for r in rows:
            if not isinstance(r, dict):
                continue
            try:
                name = str(r.get("name") or "").strip()
                if not name:
                    continue
                out.append({
                    "name": name,
                    "base_url": str(r.get("base_url") or "").strip().rstrip("/"),
                    "api_key": _coerce_key(r.get("api_key")),
                    "model": str(r.get("model") or "").strip(),
                })
            except Exception:
                continue
    return out


def _read_api_cfg() -> dict:
    """The merged api_keys.json body, always a dict. Never raises."""
    data = _read_full_config()
    return data if isinstance(data, dict) else {}


def _write_api_keys_impl(gemini_key: str, rows: list[dict]) -> int:
    """Atomic merge-write of api_keys.json (temp file + os.replace so a crash
    mid-save can never leave a corrupt config behind). Returns the number of
    fallback keys saved. Pure function — safe to call from a worker thread."""
    os.makedirs(CONFIG_DIR, exist_ok=True)
    data = _read_api_cfg()
    if gemini_key:
        data["gemini_api_key"] = gemini_key
    rows = _sanitize_provider_rows(rows)
    data["free_providers"] = rows
    tmp = API_FILE.with_name(API_FILE.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=4, ensure_ascii=False),
                   encoding="utf-8")
    os.replace(tmp, API_FILE)
    return sum(1 for p in rows if (p.get("api_key") or "").strip())


# Single source of truth for the release name — the window title, the header
# badge and the readme must never disagree again.
APP_VERSION  = "ICE"
APP_PROTOCOL = APP_VERSION.split()[-1]

_DEFAULT_W, _DEFAULT_H = 980, 700
_MIN_W,     _MIN_H     = 820, 580
_LEFT_W  = 148
_RIGHT_W = 340
_FACE_H  = 402    # height of the Home face strip (the face is the identity)
_FACE_SMALL = 64   # compact/focus mode collapsed height

# ── Typography ────────────────────────────────────────────────────────────────
# One modern UI family everywhere. Courier was the terminal look this redesign
# removes; Segoe UI is the native Windows app voice (and falls back cleanly on
# macOS/Linux via the families after it). Sizes are in pt and the two mono
# constants are kept only for the few genuinely technical readouts.
FONT_UI      = "Segoe UI"
FONT_DISPLAY = FONT_UI          # titles use the same family, heavier weights
FONT_UI_EMOJI = "Segoe UI Emoji"  # emoji-capable face for icon-only buttons
# The same family as a stylesheet value (quoted), for font-family: declarations.
FONT_STACK   = '"Segoe UI", "SF Pro Text", "Ubuntu", "Roboto", Arial'
FONT_MONO    = '"Cascadia Mono", "Consolas", monospace'

_OS = platform.system()  # "Windows" | "Darwin" | "Linux"


class C:
    # Deep blue → cyan → soft white. Calm and premium: dark navy glass panels,
    # cyan primary, soft periwinkle status colours, no neon anywhere.
    BG        = "#050912"   # deep space navy — near-black but blue, not grey
    PANEL     = "#0b1322"   # card body — one step up from the void
    PANEL2    = "#101a2e"   # raised card / input body
    BORDER    = "#1a2740"   # subtle hairline — never bright
    BORDER_B  = "#2b3d63"   # hover border
    BORDER_A  = "#243352"   # active border
    PRI       = "#35c3f0"   # clean cyan — the identity colour
    PRI_DIM   = "#1379ad"
    PRI_GHO   = "#0a2338"   # ghost fill for selected states
    ACC       = "#8b6df5"   # purple — optional secondary accent, used sparingly
    ACC2      = "#a78bfa"
    GREEN     = "#31d9ae"   # calm teal for "listening/ok" — not neon
    GREEN_D   = "#178a66"
    RED       = "#ff5c7a"
    MUTED_C   = "#ff5470"
    TEXT      = "#d6e6f7"
    TEXT_DIM  = "#506282"
    TEXT_MED  = "#8499bd"
    WHITE     = "#f2f9ff"
    DARK      = "#081020"   # chrome (header/footer/nav) — deeper than cards
    BAR_BG    = "#0d1626"


# Keys tied to the accent colour — status colours (ACC, GREEN, RED…) stay fixed
_HUE_LINKED = (
    "BG", "PANEL", "PANEL2", "BORDER", "BORDER_B", "BORDER_A",
    "PRI", "PRI_DIM", "PRI_GHO", "TEXT", "TEXT_DIM", "TEXT_MED",
    "WHITE", "DARK", "BAR_BG",
)
_PALETTE_DEFAULTS: dict[str, str] = {k: getattr(C, k) for k in _HUE_LINKED}

DEFAULT_UI_COLOR = _PALETTE_DEFAULTS["PRI"]


def apply_ui_accent(accent_hex: str) -> bool:
    """
    Re-derives the whole teal-family palette from the chosen accent colour
    (hue shift — brightness/saturation ratios are preserved, design stays intact).
    Painted elements (HUD, waveform, metrics) pick up the new colour on the next
    frame; stylesheet-based panels pick it up when they are rebuilt.
    """
    import colorsys

    accent_hex = (accent_hex or "").strip().lower()
    if not (accent_hex.startswith("#") and len(accent_hex) == 7):
        return False
    try:
        int(accent_hex[1:], 16)
    except ValueError:
        return False

    def _hsv(h: str) -> tuple[float, float, float]:
        r = int(h[1:3], 16) / 255
        g = int(h[3:5], 16) / 255
        b = int(h[5:7], 16) / 255
        return colorsys.rgb_to_hsv(r, g, b)

    base_h            = _hsv(_PALETTE_DEFAULTS["PRI"])[0]
    acc_h, acc_s, _av = _hsv(accent_hex)
    dh   = acc_h - base_h
    grey = acc_s < 0.08   # near-grey accent → the whole theme is desaturated

    for key, hex0 in _PALETTE_DEFAULTS.items():
        h, s, v = _hsv(hex0)
        if grey:
            s *= 0.15
        r, g, b = colorsys.hsv_to_rgb((h + dh) % 1.0, s, v)
        setattr(C, key, "#{:02x}{:02x}{:02x}".format(
            int(r * 255 + 0.5), int(g * 255 + 0.5), int(b * 255 + 0.5)))
    return True


def current_palette() -> dict[str, str]:
    """A snapshot of the accent-linked colours currently on class C."""
    return {k: getattr(C, k) for k in _HUE_LINKED}


def retheme_all_widgets(old: dict[str, str], new: dict[str, str]) -> None:
    """
    LIVE full theme change. Replaces the old palette colours with the new ones
    in EVERY widget's stylesheet across the app and repaints them. This way the
    colour change applies INSTANTLY across the whole interface — panels, buttons,
    borders included — not just the painted elements. No restart needed.
    """
    mapping = {old[k].lower(): new[k].lower()
               for k in old if old[k].lower() != new.get(k, old[k]).lower()}
    if not mapping:
        return
    app = QApplication.instance()
    if app is None:
        return
    for w in app.allWidgets():
        try:
            ss = w.styleSheet()
            if ss:
                s2 = ss
                for o, n in mapping.items():
                    if o in s2:
                        s2 = s2.replace(o, n)
                if s2 != ss:
                    w.setStyleSheet(s2)
            w.update()
        except Exception:
            pass


def qcol(h: str, a: int = 255) -> QColor:
    c = QColor(h); c.setAlpha(a); return c


def _pill_style(fg: str, bg: str, bd: str, pad: str = "3px 12px") -> str:
    """One shared status-pill look — the header pill and the home status chip
    are the same component, so they cannot drift apart."""
    return (f"color: {fg}; background: {bg}; border: 1px solid {bd};"
            f"border-radius: 12px; padding: {pad}; font-weight: 600;")


# Hue window of the ICE design family: cyan/blue through violet (about
# 165°–300°). Accents outside it (greens, teals, ambers, reds) would drag the
# theme back to the old look, so they are ignored on load.
_ACCENT_HUE_MIN, _ACCENT_HUE_MAX = 0.46, 0.84


def _accent_in_family(hex_str: str) -> bool:
    """True when `hex_str` is a saturated enough colour inside the design
    family's hue window. Greys pass (they just desaturate the theme)."""
    import colorsys
    h = (hex_str or "").strip().lower()
    if not (h.startswith("#") and len(h) == 7):
        return False
    try:
        r, g, b = (int(h[i:i + 2], 16) / 255.0 for i in (1, 3, 5))
    except ValueError:
        return False
    hue, sat, _v = colorsys.rgb_to_hsv(r, g, b)
    return sat < 0.08 or _ACCENT_HUE_MIN <= hue <= _ACCENT_HUE_MAX


def _state_colour(state: str) -> str:
    """State → colour. Read live from C so an accent re-theme re-tints the
    status pills on the next call. Shared by the header pill and the
    face-side status chip, so one state is one colour everywhere."""
    return {
        "SPEAKING":    C.PRI,
        "LISTENING":   C.GREEN,
        "THINKING":    C.ACC2,
        "PROCESSING":  C.ACC2,
        "EXECUTING":   C.ACC,
        "SLEEPING":    C.TEXT_DIM,
        "ERROR":       C.MUTED_C,
        "MUTED":       C.MUTED_C,
    }.get(state, C.TEXT_MED)


# ── Windows GPU via NVML DLL (no subprocess, no console window) ──────────────
_nvml_lib: object = None   # cached ctypes DLL
_nvml_ok:  object = None   # None=untested, True=works, False=unavailable


def _nvml_gpu_windows() -> float:
    """Return NVIDIA GPU utilisation % using nvml.dll directly — zero subprocess."""
    global _nvml_lib, _nvml_ok
    if _nvml_ok is False:
        return -1.0
    try:
        import ctypes

        class _Util(ctypes.Structure):
            _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]

        if _nvml_lib is None:
            for dll_name in ("nvml", r"C:\Windows\System32\nvml.dll"):
                try:
                    lib = ctypes.WinDLL(dll_name)
                    lib.nvmlInit_v2()
                    _nvml_lib = lib
                    break
                except Exception:
                    continue

        if _nvml_lib is None:
            import pynvml  # type: ignore
            pynvml.nvmlInit()
            h = pynvml.nvmlDeviceGetHandleByIndex(0)
            _nvml_ok = True
            return float(pynvml.nvmlDeviceGetUtilizationRates(h).gpu)

        dev = ctypes.c_void_p()
        _nvml_lib.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(dev))
        util = _Util()
        _nvml_lib.nvmlDeviceGetUtilizationRates(dev, ctypes.byref(util))
        _nvml_ok = True
        return float(util.gpu)
    except Exception:
        _nvml_ok = False
        return -1.0


class _SysMetrics:
    def __init__(self):
        self.cpu  = 0.0
        self.mem  = 0.0
        self.net  = 0.0   
        self.gpu  = -1.0  
        self.tmp  = -1.0  
        self._lock = threading.Lock()
        self._last_net = psutil.net_io_counters()
        self._last_net_t = time.time()
        self._running = True
        # Probe caches — GPU (NVML) and temperature (WMI) are the expensive
        # queries; initialise their handles once and reuse them instead of
        # rebuilding a connection on every poll.
        self._slow_tick = 0            # gpu/temp refreshed every 3rd cycle
        self._pynvml    = None         # cached pynvml module + device handle
        self._pynvml_h  = None
        self._pynvml_ok = None         # None=untested, False=unavailable here
        self._nv_unix   = None         # cached (lib, dev) for Linux/macOS NVML
        self._wmi_conn  = None         # cached WMI connection (creating one is slow)
        self._wmi_ok    = None         # None=untested, False=unavailable here
        t = threading.Thread(target=self._loop, daemon=True)
        t.start()

    def _loop(self):
        while self._running:
            try:
                self._update()
            except Exception:
                pass
            time.sleep(2.0)

    def _update(self):
        cpu = psutil.cpu_percent(interval=None)
        mem = psutil.virtual_memory().percent

        nc  = psutil.net_io_counters()
        now = time.time()
        dt  = now - self._last_net_t
        if dt > 0:
            sent = (nc.bytes_sent - self._last_net.bytes_sent) / dt
            recv = (nc.bytes_recv - self._last_net.bytes_recv) / dt
            net  = (sent + recv) / (1024 * 1024)
        else:
            net = 0.0
        self._last_net   = nc
        self._last_net_t = now

        # GPU and temperature change slowly and are the most expensive probes
        # (NVML / WMI) — refresh them every 3rd cycle (~6 s) instead of every
        # cycle, reusing the previous reading in between.
        self._slow_tick = (self._slow_tick + 1) % 3
        if self._slow_tick == 1:
            gpu = self._get_gpu()
            tmp = self._get_temp()
        else:
            gpu = self.gpu
            tmp = self.tmp

        with self._lock:
            self.cpu = cpu
            self.mem = mem
            self.net = net
            self.gpu = gpu
            self.tmp = tmp

    def _get_gpu(self) -> float:
        # pynvml — subprocess-free; initialise once and reuse the handle.
        # Re-initialising NVML on every poll is slow, so cache it and stop
        # retrying pynvml entirely once it proves unavailable here.
        if self._pynvml_ok is not False:
            try:
                if self._pynvml_h is None:
                    import pynvml  # type: ignore
                    pynvml.nvmlInit()
                    self._pynvml    = pynvml
                    self._pynvml_h  = pynvml.nvmlDeviceGetHandleByIndex(0)
                    self._pynvml_ok = True
                return float(self._pynvml.nvmlDeviceGetUtilizationRates(self._pynvml_h).gpu)
            except Exception:
                self._pynvml_ok = False

        # Windows: nvml.dll via ctypes (already cached in _nvml_gpu_windows)
        if _OS == "Windows":
            return _nvml_gpu_windows()

        # Linux / macOS: libnvidia-ml shared lib via ctypes — init once, reuse
        try:
            import ctypes

            class _Util(ctypes.Structure):
                _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]

            if self._nv_unix is None:
                _lib = "libnvidia-ml.so.1" if _OS == "Linux" else "libnvidia-ml.dylib"
                nv = ctypes.CDLL(_lib)
                nv.nvmlInit_v2()
                dev = ctypes.c_void_p()
                nv.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(dev))
                self._nv_unix = (nv, dev)

            nv, dev = self._nv_unix
            u = _Util()
            nv.nvmlDeviceGetUtilizationRates(dev, ctypes.byref(u))
            return float(u.gpu)
        except Exception:
            pass

        return -1.0   # N/A — zero subprocess on all platforms

    def _get_temp(self) -> float:
        # psutil — works on Linux; occasionally Windows with driver support
        try:
            temps = psutil.sensors_temperatures()
            for name in ["coretemp", "k10temp", "cpu_thermal", "acpitz",
                         "cpu-thermal", "zenpower", "it8688"]:
                if name in temps and temps[name]:
                    return temps[name][0].current
            for entries in temps.values():
                if entries:
                    return entries[0].current
        except Exception:
            pass

        # Windows: wmi module (pure Python COM, zero subprocess). Reuse a single
        # connection — building a fresh wmi.WMI() on every poll spins up a COM
        # connection each time and is very slow. Give up after one failure.
        if _OS == "Windows" and self._wmi_ok is not False:
            try:
                if self._wmi_conn is None:
                    import wmi  # type: ignore
                    self._wmi_conn = wmi.WMI(namespace="root/wmi")
                tz = self._wmi_conn.MSAcpi_ThermalZoneTemperature()
                if tz:
                    return (tz[0].CurrentTemperature / 10.0) - 273.15
            except Exception:
                self._wmi_ok   = False
                self._wmi_conn = None

        return -1.0   # N/A — zero subprocess on all platforms

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "cpu": self.cpu,
                "mem": self.mem,
                "net": self.net,
                "gpu": self.gpu,
                "tmp": self.tmp,
            }


_metrics = _SysMetrics()

class HudCanvas(QWidget):
    def __init__(self, face_path: str, assistant_name: str = "J.A.R.V.I.S", parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setMinimumSize(300, 300)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self.muted    = False
        self.speaking = False
        self.state    = "INITIALISING"
        self._assistant_name = assistant_name

        # The holographic head that fills the HUD. If it could not be imported
        # we fall back to the old glowing core so the panel is never empty.
        self._avatar = None
        if HoloAvatar is not None:
            try:
                self._avatar = HoloAvatar()
            except Exception:
                self._avatar = None

        # Which centrepiece to draw. Read once here and changed live by the
        # settings toggle; the avatar object is kept either way so switching
        # back is instant and costs no reload.
        try:
            from memory.config_manager import get_hud_style
            self.hud_style = get_hud_style()
        except Exception:
            self.hud_style = "face"
        self._core_phase = 0.0

        self._tick       = 0
        self._scale      = 1.0
        self._tgt_scale  = 1.0
        self._halo       = 55.0
        self._tgt_halo   = 55.0
        self._last_t     = time.time()
        self._step_t     = time.time()
        self._blink      = True
        self._blink_tick = 0

        # Rescaled-face cache: the smooth rescale is expensive, so we keep the
        # last result and only rebuild it when the (quantised) size changes.

        # Static grid-dot layer, pre-rendered once per size/theme into a pixmap
        # so paintEvent blits it in one call instead of thousands of drawPoint()s.
        self._grid_cache: QPixmap | None = None
        self._grid_key = None
        # Repaint throttle counter (idle frames drop to ~20 Hz — see _step()).
        self._paint_tick = 0

        # Live audio reactivity: _live_amp is written from the audio threads
        # (0.0–1.0), _amp_disp is the smoothed value the paint code reads.
        self._live_amp  = 0.0
        self._amp_disp  = 0.0
        # (frames, start_time, hop) posted by the playback thread — see
        # push_visemes(). None means "no schedule; use the plain level".
        self._visemes = None
        self._vis_i = None        # first schedule frame not yet handed to the mouth
        self._base_scale = 1.0    # slow "breathing" target; amp is added per-frame
        self._base_halo  = 55.0

        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._step)
        self._tmr.start(16)

    def pause(self) -> None:
        """Freeze the animation loop — used when the window is hidden or the
        user is away. The avatar's HoloAvatar loop is a heavyweight import;
        this one call stops the whole paint engine, which is the biggest
        background-CPU win a hidden UI can make."""
        try:
            self._tmr.stop()
        except Exception:
            pass

    def resume(self) -> None:
        """Restart the animation loop. Idle screens run a slow cadence; the
        _step() painter already throws away most idle frames anyway."""
        try:
            if not self._tmr.isActive():
                self._tmr.start(160 if self.state in ("SLEEPING", "INITIALISING")
                                else 16)
        except Exception:
            pass

    def glance(self, dx: float, dy: float, hold: float = 1.1) -> None:
        """Ask the avatar to look somewhere for a moment (see HoloAvatar.glance)."""
        try:
            if self._avatar is not None:
                self._avatar.glance(dx, dy, hold)
        except Exception:
            pass

    def push_visemes(self, frames, hop: float, at: float) -> None:
        """Thread-safe: hand over a schedule of (level, openness, width) frames.

        The playback thread writes up to 200 ms of audio in one go, so a single
        averaged level would only move the mouth five times a second — enough to
        flap, nowhere near enough to articulate. It instead posts the whole
        slice's worth of 20 ms frames here and `_step()` plays them out against
        the wall clock, in step with the audio going to the speakers.

        `at` is the wall-clock time this batch will *begin to sound*, which the
        caller tracks as a playback cursor. It is not the time of the call, and
        the difference is the whole point: `stream.write` returns once the buffer
        accepts the samples, so consecutive batches are handed over far faster
        than they play. Anchoring each one to "now" made every batch start while
        its predecessor was still sounding, so each schedule replaced the last
        after a couple of frames and the mouth only ever played the opening
        instant of every 200 ms — the reason it did not match the words.

        Successive batches are therefore *appended* into one continuous
        timeline, not swapped in. A paragraph is one schedule; the mouth stops
        falling into a gap at every chunk boundary and having to climb back out.
        """
        try:
            if not frames:
                return
            hop = max(1e-3, float(hop))
            at = float(at)
            new = list(frames)
            cur = self._visemes
            if cur is not None:
                old, t0, ohop = cur
                if abs(ohop - hop) < 1e-6:
                    # Where in the existing timeline does this batch land?
                    i = int(round((at - t0) / hop))
                    if 0 <= i <= len(old) + 1:
                        # Continues (or slightly overlaps) what is already
                        # queued: extend rather than restart. Drop whatever has
                        # already been played so the list cannot grow without
                        # bound over a long reply.
                        merged = old[:i] + new
                        played = int((time.time() - t0) / hop) - 2
                        if played > 60:
                            merged = merged[played:]
                            t0 += played * hop
                            if self._vis_i is not None:
                                self._vis_i = max(0, self._vis_i - played)
                        self._visemes = (merged, t0, hop)
                        return
            self._visemes = (new, at, hop)
            self._vis_i = None
        except Exception:
            pass

    def set_audio_level(self, level: float) -> None:
        """Thread-safe entry point for the audio threads. Stores the louder of
        the incoming level and the current value so brief gaps between chunks
        don't make the waveform stutter; _step() decays it back down."""
        try:
            lv = float(level)
        except (TypeError, ValueError):
            return
        if lv < 0.0:
            lv = 0.0
        elif lv > 1.0:
            lv = 1.0
        if lv > self._live_amp:
            self._live_amp = lv

    def _make_grid(self, W: int, H: int) -> QPixmap:
        """Pre-render the static grid-dot background into a transparent pixmap so
        paintEvent can blit it once per frame instead of running a nested
        drawPoint() loop across the whole widget every 16 ms."""
        pm = QPixmap(max(1, W), max(1, H))
        pm.fill(Qt.GlobalColor.transparent)
        gp = QPainter(pm)
        gp.setPen(QPen(qcol(C.PRI_GHO), 1))
        for x in range(0, W, 48):
            for y in range(0, H, 48):
                gp.drawPoint(x, y)
        gp.end()
        return pm

    def _step(self):
        self._tick += 1
        now = time.time()

        # ── Live audio reactivity ────────────────────────────────────────────
        # A viseme schedule, if one is playing, gives both the level and the
        # mouth shape for this exact instant; otherwise fall back to the peak
        # level the audio threads pushed in.
        v_open = v_wide = v_level = None
        v_seq = None
        sched = self._visemes
        if sched is not None:
            frames, t0, hop = sched
            i = int((now - t0) / hop)
            if 0 <= i < len(frames):
                # Hand over *every* frame since the last tick, not just the one
                # under the cursor. This timer runs at 60 Hz but the paint is
                # throttled and the machine may be busy, so a tick can span two
                # or three 20 ms frames — and a consonant closure is only two
                # frames long. Sampling one and discarding the rest is how the
                # closures between words went missing.
                j = self._vis_i if self._vis_i is not None else i
                v_seq = frames[max(0, j):i + 1]
                self._vis_i = max(j, i + 1)
                v_level, v_open, v_wide = frames[i]
                if v_seq:
                    peak = max(f[0] for f in v_seq)
                    if peak > self._live_amp:
                        self._live_amp = peak
            elif i >= len(frames):
                self._visemes = None        # schedule spent
                self._vis_i = None

        # Audio threads push peaks into _live_amp; decay it toward silence so
        # gaps between chunks fade out instead of freezing, then smooth it.
        self._live_amp *= 0.86
        self._amp_disp += (self._live_amp - self._amp_disp) * 0.45
        amp = self._amp_disp

        # The avatar animates off the very same smoothed level the waveform
        # uses — one audio source, so the mouth can never drift out of sync.
        dt = now - self._step_t
        self._step_t = now
        # Integrated, not derived from absolute time: multiplying wall-clock by
        # a rate that changes with state jumps the rings the instant JARVIS
        # starts talking. Same lesson the head's sway taught.
        self._core_phase += min(0.10, max(0.0, dt))

        if self._avatar is not None and self.hud_style == "face":
            self._avatar.step(dt, amp, speaking=self.speaking,
                              muted=self.muted, state=self.state,
                              v_open=v_open, v_wide=v_wide or 0.0,
                              v_level=v_level, v_seq=v_seq,
                              v_hop=(sched[2] if sched is not None else 0.02))
        else:
            # Fallback core: slow "breathing" base target, lifted by the level.
            if now - self._last_t > (0.12 if self.speaking else 0.5):
                if self.speaking:
                    self._base_scale = 1.03
                    self._base_halo  = 122.0
                elif self.muted:
                    self._base_scale = random.uniform(0.998, 1.002)
                    self._base_halo  = random.uniform(15, 28)
                else:
                    self._base_scale = random.uniform(1.001, 1.008)
                    self._base_halo  = random.uniform(48, 68)
                self._last_t = now

            if self.muted:
                self._tgt_scale, self._tgt_halo = self._base_scale, self._base_halo
            elif self.speaking:
                self._tgt_scale = self._base_scale + amp * 0.13
                self._tgt_halo  = self._base_halo  + amp * 95.0
            else:
                self._tgt_scale = self._base_scale + amp * 0.06
                self._tgt_halo  = self._base_halo  + amp * 75.0

            sp = 0.38 if self.speaking else (0.30 if amp > 0.02 else 0.15)
            self._scale += (self._tgt_scale - self._scale) * sp
            self._halo  += (self._tgt_halo  - self._halo)  * sp

        self._blink_tick += 1
        if self._blink_tick >= 38:
            self._blink = not self._blink
            self._blink_tick = 0
            _blinked = True
        else:
            _blinked = False

        # Repaint throttling — advancing the animation state above is cheap at
        # 60 Hz, but the paint is heavy. Active (speaking, audio, thinking) runs
        # at ~30 Hz, which is the frame rate animation has used for talking
        # characters forever and is indistinguishable here; idle drops to ~20 Hz
        # so a sleeping HUD stops pinning a CPU core. The visuals stay smooth
        # either way because the animation state keeps stepping at 60 Hz.
        self._paint_tick = (self._paint_tick + 1) % 6
        active = (self.speaking or amp > 0.02
                  or self.state in ("THINKING", "PROCESSING"))
        if _blinked or (self._paint_tick % 2 == 0 if active
                        else self._paint_tick % 3 == 0):
            # Nothing is on screen when the window is hidden or minimised, so
            # rendering the avatar into it is pure waste — and this app is meant
            # to sit running all day. The animation state above keeps stepping,
            # so it picks up mid-motion instead of snapping when you come back.
            if self._on_screen():
                self.update()

    def _on_screen(self) -> bool:
        """True only when this canvas can actually be seen by the user."""
        try:
            if not self.isVisible():
                return False
            win = self.window()
            return not (win.isMinimized() or win.isHidden())
        except Exception:
            return True      # never let a visibility check stop the HUD drawing

    # ── reactor core ─────────────────────────────────────────────────────────
    # The centrepiece for anyone who did not want a face looking back at them.
    # Built from the same budget as the head — software QPainter, no GPU — and
    # from the same principle: everything on it means something. The rings turn
    # at a rate the state sets, the spectrum ring is the real audio level, and
    # the core brightens with the voice. Nothing here is decoration that moves
    # for its own sake, which is what made the old glowing orb feel dead.

    def _core_colours(self):
        if self.muted:
            return qcol(C.MUTED_C), qcol(C.MUTED_C)
        if self.speaking:
            return qcol(C.PRI), qcol(C.ACC)
        if self.state in ("THINKING", "PROCESSING"):
            return qcol(C.PRI), qcol(C.ACC2)
        if self.state == "LISTENING":
            return qcol(C.PRI), qcol(C.GREEN)
        return qcol(C.PRI), qcol(C.PRI_DIM)

    def _paint_core(self, p: QPainter, cx: float, cy: float, r: float,
                    W: float = 0.0, H: float = 0.0):
        """Draw the reactor at (cx, cy) with outer radius r, using the whole
        canvas (W x H) for the marks that frame it."""
        main, acc = self._core_colours()
        bg = qcol(C.BG)
        amp = self._amp_disp
        t = self._core_phase
        live = (self.speaking or amp > 0.04) and not self.muted

        def blend(col: QColor, a: float) -> QColor:
            """Pre-mix onto the background instead of asking Qt to composite.
            The raster engine's opaque path is several times faster than its
            translucent one, and everything here is a line or an arc."""
            k = max(0.0, min(1.0, a))
            return QColor(int(bg.red()   + (col.red()   - bg.red())   * k),
                          int(bg.green() + (col.green() - bg.green()) * k),
                          int(bg.blue()  + (col.blue()  - bg.blue())  * k))

        p.setBrush(Qt.BrushStyle.NoBrush)

        # 1. The atmosphere. One radial gradient doing what a stack of discs did
        #    badly: a wide, soft body of light that gives the thing presence
        #    before any detail is read. This single element decides whether the
        #    HUD looks vast or looks small, so it is drawn first and drawn big.
        # Concentrated rather than spread: a gradient reaching the outer rim
        # washes the whole disc a flat dim blue and reads as fog. Ending it at
        # two thirds leaves it a body of light with somewhere to fall off to,
        # which is what makes it look lit rather than tinted.
        lift = 1.0 + 0.55 * amp + (0.18 if self.speaking else 0.0)
        p.setPen(Qt.PenStyle.NoPen)
        for gr, a0, a1 in ((r * 0.70, 0.30, 0.0), (r * 0.34, 0.34, 0.0)):
            g = QRadialGradient(cx, cy, gr)
            g.setColorAt(0.00, blend(main, min(0.95, a0 * lift)))
            g.setColorAt(0.45, blend(main, min(0.95, a0 * lift * 0.52)))
            g.setColorAt(0.78, blend(main, min(0.95, a0 * lift * 0.18)))
            g.setColorAt(1.00, blend(main, a1))
            p.setBrush(QBrush(g))
            p.drawEllipse(QRectF(cx - gr, cy - gr, gr * 2, gr * 2))
        p.setBrush(Qt.BrushStyle.NoBrush)

        # 2. Frame marks at the corners of the whole canvas, not of the circle.
        #    They are what set the scale: the eye reads the reactor as filling
        #    the room rather than sitting in the middle of it.
        if W > 40 and H > 40:
            m, arm = min(W, H) * 0.035, min(W, H) * 0.055
            p.setPen(QPen(blend(main, 0.45), 1.4))
            for sx, sy in ((1, 1), (-1, 1), (1, -1), (-1, -1)):
                x = cx + sx * (W / 2 - m)
                y = cy + sy * (H / 2 - m)
                p.drawLine(QLineF(x, y, x - sx * arm, y))
                p.drawLine(QLineF(x, y, x, y - sy * arm))

        # 3. Crosshair across the full canvas, broken around the core so it
        #    frames the reactor rather than crossing it.
        p.setPen(QPen(blend(main, 0.16), 1))
        gap = r * 0.62
        if W > 40:
            p.drawLine(QLineF(cx - W / 2, cy, cx - gap, cy))
            p.drawLine(QLineF(cx + gap, cy, cx + W / 2, cy))
        if H > 40:
            p.drawLine(QLineF(cx, cy - H / 2, cx, cy - gap))
            p.drawLine(QLineF(cx, cy + gap, cx, cy + H / 2))

        # 4. Two thin outer circles. Sparse on purpose — a dense ring reads as a
        #    grey band at this size, and restraint is what made the original
        #    look expensive.
        for rr, a in ((1.00, 0.34), (0.93, 0.16)):
            rad = r * rr
            p.setPen(QPen(blend(main, a), 1))
            p.drawEllipse(QRectF(cx - rad, cy - rad, rad * 2, rad * 2))

        # 5. Long, sparse graduations: 24 majors reaching well in from the rim,
        #    with shorter minors between them.
        major, minor = [], []
        for i in range(72):
            a = math.radians(i * 5.0)
            ca, sa = math.cos(a), math.sin(a)
            if i % 3 == 0:
                major.append(QLineF(cx + ca * r * 0.885, cy + sa * r * 0.885,
                                    cx + ca * r * 0.985, cy + sa * r * 0.985))
            else:
                minor.append(QLineF(cx + ca * r * 0.945, cy + sa * r * 0.945,
                                    cx + ca * r * 0.985, cy + sa * r * 0.985))
        p.setPen(QPen(blend(main, 0.42), 1.3))
        p.drawLines(major)
        p.setPen(QPen(blend(main, 0.18), 1))
        p.drawLines(minor)

        # 6. Sweeping arcs. Long spans, not dashes — the original's grandeur
        #    came from a few big strokes. Speed is the state: idle drifts,
        #    thinking hurries, speaking runs.
        rate = 1.0 + (1.9 if self.state in ("THINKING", "PROCESSING") else 0.0) \
                   + (1.2 if self.speaking else 0.0)
        for k, (rr, span, count, dirn, col, a, wid) in enumerate((
                (0.955, 118, 2, +1, acc,  0.75, 2.0),
                (0.845, 82,  3, -1, main, 0.38, 1.3),
                (0.760, 150, 1, +1, acc,  0.45, 1.6),
                (0.660, 64,  4, -1, main, 0.26, 1.1),
                (0.545, 128, 2, +1, main, 0.30, 1.2))):
            rad = r * rr
            p.setPen(QPen(blend(col, a), wid))
            box = QRectF(cx - rad, cy - rad, rad * 2, rad * 2)
            base = (t * rate * (9 + k * 6) * dirn) % 360.0
            for sgm in range(count):
                p.drawArc(box, int((base + sgm * (360.0 / count)) * 16),
                          int(span * 16))

        # 7. The voice, as a ring of graduations that grow with it. Kept out at
        #    a wide radius so it never crowds the middle.
        n = 60
        ring = r * 0.415
        spikes = []
        for i in range(n):
            a = math.radians(i * (360.0 / n))
            ca, sa = math.cos(a), math.sin(a)
            wob = 0.5 + 0.5 * math.sin(t * 2.3 + i * 0.42)
            idle = 0.018 + 0.012 * math.sin(t * 1.2 + i * 0.7)
            h = r * (idle + (amp * 0.20 * wob if live else 0.0))
            spikes.append(QLineF(cx + ca * ring, cy + sa * ring,
                                 cx + ca * (ring + h), cy + sa * (ring + h)))
        p.setPen(QPen(blend(acc if live else main, 0.25 + 0.5 * amp), 1.6))
        p.drawLines(spikes)

        # 8. The inner ring the name sits in.
        inner = r * 0.355
        p.setPen(QPen(blend(acc, 0.30 + 0.45 * amp), 1.5))
        p.drawEllipse(QRectF(cx - inner, cy - inner, inner * 2, inner * 2))

        # 9. The name, sized from the string rather than from the radius alone:
        #    "J.A.R.V.I.S" and a name someone renamed to "MAX" are very
        #    different widths, and a fixed fraction of r spills one of them past
        #    the ring it is supposed to sit inside.
        name = self._assistant_name or ""
        if name:
            space = max(1.0, r * 0.018)
            fsz = max(8, int(min(r * 0.105,
                                 (inner * 1.75) / max(1, len(name)) * 1.6 - space)))
            f = QFont(FONT_UI, fsz, QFont.Weight.Bold)
            f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, space)
            p.setFont(f)
            p.setPen(QPen(blend(qcol(C.WHITE), 0.6 + 0.4 * min(1.0, amp * 2)), 1))
            p.drawText(QRectF(cx - r, cy - fsz, r * 2, fsz * 2),
                       Qt.AlignmentFlag.AlignCenter, name)

    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():      # device not ready (e.g. 0-size during layout) — skip cleanly
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), qcol(C.BG))

        W, H = self.width(), self.height()
        cx, cy = W / 2, H / 2
        fw = min(W, H)

        # grid dots — blitted from a cached layer; rebuilt only when the size
        # or the theme's ghost colour changes (so live re-theming still works).
        _gkey = (W, H, C.PRI_GHO)
        if self._grid_cache is None or self._grid_key != _gkey:
            self._grid_cache = self._make_grid(W, H)
            self._grid_key   = _gkey
        p.drawPixmap(0, 0, self._grid_cache)

        # ── holographic head ────────────────────────────────────────────────
        # Sized to the band between the top of the canvas and the status line,
        # capped by width, so it fills the HUD at any window size — including
        # fullscreen — without ever colliding with the status text below.
        _sy_status = cy + fw * 0.40
        if self._avatar is not None and self.hud_style == "face":
            _band_t = 10.0
            _band_h = max(60.0, _sy_status - 12.0 - _band_t)
            _r_head = min(fw * 0.42, _band_h / (self._avatar.SPAN + 0.08))
            _head_cy = _band_t + (_band_h - self._avatar.SPAN * _r_head) / 2.0 + _r_head
            # Ambient ring behind the head - one soft gradient arc that reads
            # as presence. Brightens with the voice; no spinning decoration.
            _ring_r = _r_head * 1.62
            _ring_a = 26 + int(52 * min(1.0, self._amp_disp * 2.2))
            _g = QRadialGradient(cx, _head_cy, _ring_r)
            _g.setColorAt(0.72, qcol(C.PRI, 0))
            _g.setColorAt(0.88, qcol(C.PRI, _ring_a))
            _g.setColorAt(1.00, qcol(C.PRI, 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(_g))
            p.drawEllipse(QRectF(cx - _ring_r, _head_cy - _ring_r,
                                 _ring_r * 2, _ring_r * 2))

            if self.muted:
                _main = _acc = qcol(C.MUTED_C)
            else:
                _main = qcol(C.PRI)
                if self.speaking:
                    _acc = qcol(C.ACC)
                else:
                    # Accent stays inside the face's blue/cyan family in every
                    # awake state — the iris and throat tint read the accent,
                    # and green/purple irises broke the identity. State colour
                    # still shows in the status pill and waveform.
                    _acc = qcol(C.ACC2 if
                                self.state in ("THINKING", "PROCESSING")
                                else C.PRI)
            self._avatar.paint(p, cx, _head_cy, _r_head, _main, _acc, qcol(C.BG))

        # reactor core — the other centrepiece, and the fallback if the head
        # could not be built. There is no third path: the old face.png branch
        # was unreachable (no such file ships) and the bare orb it fell through
        # to is what this replaces.
        else:
            _band_t = 12.0
            _band_h = max(60.0, _sy_status - 12.0 - _band_t)
            _r = min(W * 0.46, _band_h / 2.0)
            self._paint_core(p, cx, _band_t + _band_h / 2.0, _r, W, _band_h)

        # status text
        sy = _sy_status
        if self.muted:
            txt, col = "⊘  MUTED",     qcol(C.MUTED_C)
        elif self.speaking:
            txt, col = "●  SPEAKING",  qcol(C.ACC)
        elif self.state in ("THINKING", "PROCESSING"):
            txt, col = f"◈  {self.state}", qcol(C.ACC2)
        elif self.state == "EXECUTING":
            txt, col = "▸  EXECUTING", qcol(C.ACC)
        elif self.state == "ERROR":
            txt, col = "⚠  ERROR", qcol(C.MUTED_C)
        elif self.state == "LISTENING":
            sym = "●" if self._blink else "○"
            txt, col = f"{sym}  LISTENING",  qcol(C.GREEN)
        else:
            sym = "●" if self._blink else "○"
            txt, col = f"{sym}  {self.state}", qcol(C.PRI)

        p.setPen(QPen(col, 1))
        p.setFont(QFont(FONT_UI, 11, QFont.Weight.DemiBold))
        p.drawText(QRectF(0, sy, W, 26), Qt.AlignmentFlag.AlignCenter, txt)

        # waveform — reacts to the real audio level (mic while listening,
        # JARVIS's own voice while speaking). Falls back to a gentle idle
        # ripple when there's no sound. _amp_disp is the smoothed 0–1 level.
        wy = sy + 30
        N, bw = 36, 6
        wx0 = (W - N * bw) / 2
        amp = self._amp_disp
        mid = (N - 1) / 2.0
        for i in range(N):
            if self.muted:
                hgt, cl = 2, qcol(C.MUTED_C)
            else:
                env     = (1.0 - abs(i - mid) / mid) ** 0.7      # center-weighted hump
                shimmer = 0.55 + 0.45 * math.sin(self._tick * 0.18 + i * 0.7)
                idle    = 3.0 + 2.0 * math.sin(self._tick * 0.09 + i * 0.6)
                hgt     = int(max(2, min(22, idle + amp * 20.0 * env * shimmer)))
                if amp > 0.05:
                    cl = qcol(C.PRI) if hgt > 11 else qcol(C.PRI_DIM)
                else:
                    cl = qcol(C.BORDER_B)
            p.fillRect(QRectF(wx0 + i * bw, wy + 20 - hgt, bw - 1, hgt), cl)

        p.end()   # end deterministically so the backing store never flushes an active painter

class MetricBar(QWidget):

    def __init__(self, label: str, color: str = C.PRI, parent=None):
        super().__init__(parent)
        self._label = label
        self._color = color
        self._value = 0.0       # 0–100
        self._text  = "--"
        self.setFixedHeight(38)
        self.setMinimumWidth(80)

    def set_value(self, pct: float, text: str):
        v = max(0.0, min(100.0, pct))
        if v == self._value and text == self._text:
            return          # unchanged — skip the repaint
        self._value = v
        self._text  = text
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()

        p.setBrush(QBrush(qcol(C.PANEL2)))
        p.setPen(QPen(qcol(C.BORDER_A), 1))
        p.drawRoundedRect(QRectF(1, 1, W - 2, H - 2), 4, 4)

        bar_h   = 4
        bar_y   = H - bar_h - 5
        bar_w   = W - 12
        bar_x   = 6
        fill_w  = int(bar_w * self._value / 100)

        p.setBrush(QBrush(qcol(C.BAR_BG)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(QRectF(bar_x, bar_y, bar_w, bar_h), 2, 2)

        if self._value > 85:
            bar_col = qcol(C.RED)
        elif self._value > 65:
            bar_col = qcol(C.ACC)
        else:
            bar_col = qcol(self._color)

        if fill_w > 0:
            p.setBrush(QBrush(bar_col))
            p.drawRoundedRect(QRectF(bar_x, bar_y, fill_w, bar_h), 2, 2)

        p.setFont(QFont(FONT_UI, 7, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(8, 5, 50, 14), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self._label)

        p.setFont(QFont(FONT_UI, 9, QFont.Weight.Bold))
        p.setPen(QPen(bar_col if self._text != "--" else qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(0, 4, W - 6, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, self._text)

        p.end()

class LogWidget(QTextEdit):
    _sig = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        # Cap scrollback so an hours-long session can't grow the document
        # without bound — keeps memory flat and every insert cheap. Oldest
        # lines drop off the top automatically.
        self.document().setMaximumBlockCount(600)
        self.setFont(QFont(FONT_UI, 9))
        self.setStyleSheet(f"""
            QTextEdit {{
                background: {C.PANEL};
                color: {C.TEXT};
                border: 1px solid {C.BORDER};
                border-radius: 14px;
                padding: 6px;
                selection-background-color: {C.PRI_GHO};
            }}
            QScrollBar:vertical {{
                background: {C.BG};
                width: 8px;
                border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {C.BORDER_B};
                border-radius: 14px;
                min-height: 20px;
            }}
        """)
        self._queue: list[str] = []
        self._typing  = False
        self._text    = ""
        self._pos     = 0
        self._tag     = "sys"
        self._ai_name_lc = "jarvis"   # updated when assistant name changes
        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._step)
        self._sig.connect(self._enqueue)

    def append_log(self, text: str):
        self._sig.emit(text)

    def _enqueue(self, text: str):
        self._queue.append(text)
        if not self._typing:
            self._next()

    def _next(self):
        if not self._queue:
            self._typing = False
            return
        self._typing = True
        self._text   = self._queue.pop(0)
        self._pos    = 0
        tl = self._text.lower()
        _ai_pfx = f"{self._ai_name_lc}:"
        if   tl.startswith("you:"):                              self._tag = "you"
        elif tl.startswith(_ai_pfx) or tl.startswith("jarvis:"): self._tag = "ai"
        elif tl.startswith("file:"):                             self._tag = "file"
        elif "err" in tl:                                        self._tag = "err"
        else:                                                    self._tag = "sys"
        self._tmr.start(6)

    def _step(self):
        if self._pos < len(self._text):
            ch  = self._text[self._pos]
            cur = self.textCursor()
            fmt = cur.charFormat()
            col = {
                "you":  qcol(C.WHITE),
                "ai":   qcol(C.PRI),
                "err":  qcol(C.RED),
                "file": qcol(C.GREEN),
                # SYS lines are the bulk of the log. Amber fought the cyan HUD
                # and, being a fixed status colour rather than a hue-linked one,
                # stayed amber even after the accent picker retinted everything
                # else. TEXT_MED follows the theme and drops the contrast to a
                # level you can read past.
                "sys":  qcol(C.TEXT_MED),
            }.get(self._tag, qcol(C.TEXT))
            fmt.setForeground(QBrush(col))
            cur.movePosition(cur.MoveOperation.End)
            cur.insertText(ch, fmt)
            self.setTextCursor(cur)
            self.ensureCursorVisible()
            self._pos += 1
        else:
            self._tmr.stop()
            cur = self.textCursor()
            cur.movePosition(cur.MoveOperation.End)
            cur.insertText("\n")
            self.setTextCursor(cur)
            self.ensureCursorVisible()
            QTimer.singleShot(20, self._next)

_FILE_ICONS = {
    "image":   ("🖼", "#00d4ff"), "video":   ("🎬", "#ff6b00"),
    "audio":   ("🎵", "#cc44ff"), "pdf":     ("📄", "#ff4444"),
    "word":    ("📝", "#4488ff"), "excel":   ("📊", "#44bb44"),
    "code":    ("💻", "#ffcc00"), "archive": ("📦", "#ff8844"),
    "pptx":    ("📊", "#ff6622"), "text":    ("📃", "#aaaaaa"),
    "data":    ("🔧", "#88ddff"), "unknown": ("📎", "#888888"),
}
_EXT_TO_CAT = {
    **dict.fromkeys(["jpg","jpeg","png","gif","webp","bmp","tiff","svg","ico"], "image"),
    **dict.fromkeys(["mp4","avi","mov","mkv","wmv","flv","webm","m4v"],         "video"),
    **dict.fromkeys(["mp3","wav","ogg","m4a","aac","flac","wma","opus"],        "audio"),
    **dict.fromkeys(["pdf"],                                                     "pdf"),
    **dict.fromkeys(["doc","docx"],                                              "word"),
    **dict.fromkeys(["xls","xlsx","ods"],                                        "excel"),
    **dict.fromkeys(["ppt","pptx"],                                              "pptx"),
    **dict.fromkeys(["py","js","ts","jsx","tsx","html","css","java","c","cpp",
                     "cs","go","rs","rb","php","swift","kt","sh","sql","lua"],   "code"),
    **dict.fromkeys(["zip","rar","tar","gz","7z","bz2","xz"],                   "archive"),
    **dict.fromkeys(["txt","md","rst","log"],                                    "text"),
    **dict.fromkeys(["csv","tsv","json","xml"],                                  "data"),
}

def _file_category(path: Path) -> str:
    return _EXT_TO_CAT.get(path.suffix.lower().lstrip("."), "unknown")

def _fmt_size(size: int) -> str:
    if   size < 1024:    return f"{size} B"
    elif size < 1024**2: return f"{size/1024:.1f} KB"
    elif size < 1024**3: return f"{size/1024**2:.1f} MB"
    else:                return f"{size/1024**3:.1f} GB"


class FileDropZone(QWidget):
    file_selected = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(100)
        self._current_file: str | None = None
        self._hovering  = False
        self._drag_over = False
        self._dash_offset = 0.0
        self._anim_tmr = QTimer(self)
        self._anim_tmr.timeout.connect(self._animate)
        self._anim_tmr.start(40)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._canvas = _DropCanvas(self)
        layout.addWidget(self._canvas)

    def _animate(self):
        # The marching-ants dashed border is only meaningful while the user is
        # hovering or dragging a file over the zone. When idle, skip the repaint
        # entirely instead of redrawing the whole zone 25×/s forever — that idle
        # repaint held the GIL and stole time from the audio/response threads.
        if not (self._hovering or self._drag_over):
            return
        self._dash_offset = (self._dash_offset + 0.8) % 20
        self._canvas.update()

    def dragEnterEvent(self, e: QDragEnterEvent):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self._drag_over = True; self._canvas.update()

    def dragLeaveEvent(self, e):
        self._drag_over = False; self._canvas.update()

    def dropEvent(self, e: QDropEvent):
        self._drag_over = False
        urls = e.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if Path(path).is_file():
                self._set_file(path)
        self._canvas.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._browse()

    def enterEvent(self, e):
        self._hovering = True; self._canvas.update()

    def leaveEvent(self, e):
        self._hovering = False; self._canvas.update()

    def current_file(self) -> str | None:
        return self._current_file

    def clear_file(self):
        self._current_file = None; self._canvas.update()

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select a file for JARVIS", str(Path.home()),
            "All Files (*.*);;"
            "Images (*.jpg *.jpeg *.png *.gif *.webp *.bmp *.svg);;"
            "Documents (*.pdf *.docx *.txt *.md *.pptx);;"
            "Data (*.csv *.xlsx *.json *.xml);;"
            "Code (*.py *.js *.ts *.html *.css *.java *.cpp *.go);;"
            "Audio (*.mp3 *.wav *.ogg *.m4a *.aac *.flac);;"
            "Video (*.mp4 *.avi *.mov *.mkv *.wmv *.webm);;"
            "Archives (*.zip *.rar *.tar *.gz *.7z)",
        )
        if path:
            self._set_file(path)

    def _set_file(self, path: str):
        self._current_file = path
        self._canvas.update()
        self.file_selected.emit(path)


class _DropCanvas(QWidget):
    def __init__(self, zone: FileDropZone):
        super().__init__(zone)
        self._z = zone

    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        z    = self._z
        W, H = self.width(), self.height()
        pad  = 6
        rect = QRectF(pad, pad, W - pad * 2, H - pad * 2)

        bg_col = qcol("#001a24" if z._drag_over else ("#001218" if z._hovering else C.PANEL))
        p.setBrush(QBrush(bg_col)); p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(rect, 6, 6)

        if z._current_file:   border_col = qcol(C.GREEN, 200)
        elif z._drag_over:    border_col = qcol(C.PRI, 230)
        elif z._hovering:     border_col = qcol(C.BORDER_B, 200)
        else:                 border_col = qcol(C.BORDER, 160)

        pen = QPen(border_col, 1.5, Qt.PenStyle.DashLine)
        pen.setDashOffset(z._dash_offset)
        p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(rect, 6, 6)

        if z._current_file:   self._paint_file(p, W, H)
        elif z._drag_over:    self._paint_drag_over(p, W, H)
        else:                 self._paint_idle(p, W, H, z._hovering)

        p.end()

    def _paint_idle(self, p, W, H, hover):
        cx, cy = W / 2, H / 2
        col = qcol(C.PRI_DIM if not hover else C.PRI)
        p.setPen(QPen(col, 2)); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(QPointF(cx, cy - 14), QPointF(cx, cy + 4))
        p.drawLine(QPointF(cx - 8, cy - 6), QPointF(cx, cy - 14))
        p.drawLine(QPointF(cx + 8, cy - 6), QPointF(cx, cy - 14))
        p.drawLine(QPointF(cx - 14, cy + 4), QPointF(cx + 14, cy + 4))
        p.setFont(QFont(FONT_UI, 8))
        p.setPen(QPen(qcol(C.PRI_DIM if not hover else C.TEXT), 1))
        p.drawText(QRectF(0, cy + 8, W, 16), Qt.AlignmentFlag.AlignCenter,
                   "Drop file here  or  Click to Browse")
        p.setFont(QFont(FONT_UI, 7))
        p.setPen(QPen(qcol("#1a4a5a"), 1))
        p.drawText(QRectF(0, cy + 24, W, 14), Qt.AlignmentFlag.AlignCenter,
                   "Images · Video · Audio · PDF · Docs · Code · Data")

    def _paint_drag_over(self, p, W, H):
        cx, cy = W / 2, H / 2
        p.setFont(QFont(FONT_UI, 20))
        p.setPen(QPen(qcol(C.PRI), 1))
        p.drawText(QRectF(0, cy - 24, W, 32), Qt.AlignmentFlag.AlignCenter, "⬇")
        p.setFont(QFont(FONT_UI, 8, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.PRI), 1))
        p.drawText(QRectF(0, cy + 12, W, 16), Qt.AlignmentFlag.AlignCenter, "Release to load")

    def _paint_file(self, p, W, H):
        path = Path(self._z._current_file)
        cat  = _file_category(path)
        icon, icon_col = _FILE_ICONS.get(cat, _FILE_ICONS["unknown"])
        size_str = _fmt_size(path.stat().st_size)
        ext_str  = path.suffix.upper().lstrip(".") or "FILE"

        block_x, block_w = 10, 60
        p.setFont(QFont("Segoe UI Emoji", 22) if _OS == "Windows" else QFont("Arial", 22))
        p.setPen(QPen(qcol(icon_col), 1))
        p.drawText(QRectF(block_x, 0, block_w, H), Qt.AlignmentFlag.AlignCenter, icon)

        tx = block_x + block_w + 6
        tw = W - tx - 38

        p.setFont(QFont(FONT_UI, 8, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.WHITE), 1))
        name = path.name if len(path.name) <= 34 else path.name[:31] + "..."
        p.drawText(QRectF(tx, H * 0.18, tw, 16),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name)

        p.setFont(QFont(FONT_UI, 7))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(tx, H * 0.18 + 18, tw, 14),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f"{ext_str}  ·  {size_str}")

        p.setFont(QFont(FONT_UI, 6))
        p.setPen(QPen(qcol("#1e5c6a"), 1))
        par = str(path.parent)
        if len(par) > 42: par = "…" + par[-41:]
        p.drawText(QRectF(tx, H * 0.18 + 34, tw, 12),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, par)

        p.setFont(QFont(FONT_UI, 9, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.RED, 180), 1))
        p.drawText(QRectF(W - 34, 0, 28, H), Qt.AlignmentFlag.AlignCenter, "✕")

    def mousePressEvent(self, e):
        z = self._z
        if z._current_file and e.pos().x() > self.width() - 34:
            z.clear_file()
        else:
            z.mousePressEvent(e)


class _CameraPreview(QWidget):
    """Floating overlay that briefly shows what the camera captured."""

    _W, _H = 244, 188

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            _CameraPreview {{
                background: rgba(0, 6, 10, 242);
                border: 1px solid {C.PRI};
                border-radius: 16px;
            }}
        """)
        self.setFixedWidth(self._W)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 5, 6, 6)
        lay.setSpacing(4)

        hdr = QHBoxLayout()
        title = QLabel("◈  VISUAL INPUT")
        title.setFont(QFont(FONT_UI, 7, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        hdr.addWidget(title)
        hdr.addStretch()
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(16, 16)
        close_btn.setFont(QFont(FONT_UI, 8))
        close_btn.setStyleSheet(
            f"color: {C.TEXT_DIM}; background: transparent; border: none;"
        )
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.hide)
        hdr.addWidget(close_btn)
        lay.addLayout(hdr)

        self._img_lbl = QLabel()
        self._img_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._img_lbl.setStyleSheet("background: transparent;")
        lay.addWidget(self._img_lbl)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

        self.hide()

    def show_frame(self, img_bytes: bytes) -> None:
        px = QPixmap()
        px.loadFromData(img_bytes)
        if not px.isNull():
            max_w = self._W - 12
            scaled = px.scaled(
                max_w, 160,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._img_lbl.setPixmap(scaled)
            self._img_lbl.setFixedSize(scaled.width(), scaled.height())
            self.adjustSize()
        self.show()
        self.raise_()
        self._timer.start(6_000)   # auto-dismiss after 6 s


class SetupOverlay(QWidget):
    done = pyqtSignal(str, str, list)   # (gemini_key, os_name, free_providers)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            SetupOverlay {{
                background: rgba(0, 6, 10, 245);
                border: 1px solid {C.BORDER_B};
                border-radius: 16px;
            }}
        """)

        detected = {"darwin": "mac", "windows": "windows"}.get(
            _OS.lower(), "linux"
        )
        self._sel_os = detected

        # Pre-fill any free-provider keys already saved, so a re-prompt (e.g.
        # after an auth error) never asks the user to re-type them.
        existing = {}
        try:
            if API_FILE.exists():
                existing = json.loads(API_FILE.read_text(encoding="utf-8"))
        except Exception:
            existing = {}
        by_name = {str(p.get("name")).lower(): p for p in
                   (existing.get("free_providers") or []) if isinstance(p, dict)}

        self._PROVIDERS = [
            ("groq",       "GROQ   (free)", "https://api.groq.com/openai/v1",
             "llama-3.3-70b-versatile", "gsk_…"),
            ("cerebras",   "CEREBRAS (free)", "https://api.cerebras.ai/v1",
             "llama-3.3-70b", "sk-…"),
            ("openrouter", "OPENROUTER (free)", "https://openrouter.ai/api/v1",
             "meta-llama/llama-3.3-70b-instruct:free", "sk-or-…"),
            ("huggingface", "HUGGING FACE (free)", "https://router.huggingface.co/v1",
             "meta-llama/Llama-3.1-8B-Instruct", "hf_…"),
        ]
        self._pv_inputs = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 22, 30, 22)
        layout.setSpacing(8)

        def _lbl(txt, font_size=9, bold=False, color=C.PRI,
                 align=Qt.AlignmentFlag.AlignCenter):
            w = QLabel(txt)
            w.setAlignment(align)
            w.setFont(QFont(FONT_UI, font_size,
                            QFont.Weight.Bold if bold else QFont.Weight.Normal))
            w.setStyleSheet(f"color: {color}; background: transparent;")
            return w

        layout.addWidget(_lbl("◈  INITIALISATION REQUIRED", 13, True))
        layout.addWidget(_lbl("Configure J.A.R.V.I.S. before first boot.", 9, color=C.PRI_DIM))
        layout.addSpacing(6)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER};"); layout.addWidget(sep)
        layout.addSpacing(4)

        layout.addWidget(_lbl("GEMINI API KEY", 8, color=C.TEXT_DIM,
                               align=Qt.AlignmentFlag.AlignLeft))
        self._key_input = QLineEdit()
        self._key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._key_input.setPlaceholderText("AIza…")
        self._key_input.setFont(QFont(FONT_UI, 10))
        self._key_input.setFixedHeight(32)
        self._key_input.setStyleSheet(f"""
            QLineEdit {{
                background: #000d12; color: {C.TEXT};
                border: 1px solid {C.BORDER}; border-radius: 10px; padding: 4px 8px;
            }}
            QLineEdit:focus {{ border: 1px solid {C.PRI}; }}
        """)
        layout.addWidget(self._key_input)
        layout.addSpacing(12)

        layout.addWidget(_lbl("OPTIONAL — FREE FALLBACK KEYS (one-time, can skip)",
                              8, color=C.TEXT_DIM, align=Qt.AlignmentFlag.AlignLeft))
        layout.addWidget(_lbl("Used when Gemini quota runs out. Get keys free at "
                              "console.groq.com / cloud.cerebras.ai / openrouter.ai / "
                              "huggingface.co/settings/tokens",
                              7, color=C.PRI_DIM, align=Qt.AlignmentFlag.AlignLeft))
        for name, label, base, model, hint in self._PROVIDERS:
            row = QHBoxLayout(); row.setSpacing(6)
            lbl = QLabel(label)
            lbl.setFont(QFont(FONT_UI, 8, QFont.Weight.Bold))
            lbl.setFixedWidth(160)
            lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            row.addWidget(lbl)
            inp = QLineEdit()
            inp.setEchoMode(QLineEdit.EchoMode.Password)
            inp.setPlaceholderText(hint)
            inp.setFont(QFont(FONT_UI, 9))
            inp.setFixedHeight(28)
            inp.setStyleSheet(f"""
                QLineEdit {{
                    background: #000d12; color: {C.TEXT};
                    border: 1px solid {C.BORDER}; border-radius: 10px; padding: 2px 6px;
                }}
                QLineEdit:focus {{ border: 1px solid {C.PRI}; }}
            """)
            saved = by_name.get(name) or {}
            if saved.get("api_key"):
                inp.setText(str(saved["api_key"]))
            row.addWidget(inp, 1)
            self._pv_inputs[name] = inp
            layout.addLayout(row)
        layout.addSpacing(4)

        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"color: {C.BORDER};"); layout.addWidget(sep2)
        layout.addSpacing(4)

        layout.addWidget(_lbl("OPERATING SYSTEM", 8, color=C.TEXT_DIM,
                               align=Qt.AlignmentFlag.AlignLeft))
        det_name = {"windows": "Windows", "mac": "macOS", "linux": "Linux"}[detected]
        layout.addWidget(_lbl(f"Auto-detected: {det_name}", 8, color=C.ACC2,
                               align=Qt.AlignmentFlag.AlignLeft))

        os_row = QHBoxLayout(); os_row.setSpacing(6)
        self._os_btns: dict[str, QPushButton] = {}
        for key, label in [("windows","⊞  Windows"),("mac","  macOS"),("linux","🐧  Linux")]:
            btn = QPushButton(label)
            btn.setFont(QFont(FONT_UI, 9, QFont.Weight.Bold))
            btn.setFixedHeight(32)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _, k=key: self._sel(k))
            os_row.addWidget(btn)
            self._os_btns[key] = btn
        layout.addLayout(os_row)
        self._sel(detected)
        layout.addSpacing(12)

        init_btn = QPushButton("▸  INITIALISE SYSTEMS")
        init_btn.setFont(QFont(FONT_UI, 10, QFont.Weight.Bold))
        init_btn.setFixedHeight(36)
        init_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        init_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 10px;
            }}
            QPushButton:hover {{
                background: {C.PRI_GHO}; border: 1px solid {C.PRI};
            }}
        """)
        init_btn.clicked.connect(self._submit)
        layout.addWidget(init_btn)

    def _sel(self, key: str):
        self._sel_os = key
        pal = {"windows":(C.PRI,"#001a22"),"mac":(C.ACC2,"#1a1400"),"linux":(C.GREEN,"#07201c")}
        for k, btn in self._os_btns.items():
            if k == key:
                fg, bg = pal[k]
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: {fg}; color: {bg};
                        border: none; border-radius: 10px; font-weight: bold;
                    }}
                """)
            else:
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: #000d12; color: {C.TEXT_DIM};
                        border: 1px solid {C.BORDER}; border-radius: 10px;
                    }}
                    QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
                """)

    def _submit(self):
        key = self._key_input.text().strip()
        if not key:
            self._key_input.setStyleSheet(
                self._key_input.styleSheet() +
                f" QLineEdit {{ border: 1px solid {C.RED}; }}"
            )
            return
        providers = []
        for name, label, base, model, hint in self._PROVIDERS:
            providers.append({
                "name": name,
                "base_url": base,
                "api_key": self._pv_inputs[name].text().strip(),
                "model": model,
            })
        self.done.emit(key, self._sel_os, providers)


class HueWheel(QWidget):
    """
    Circular colour picker. The user drags the handle (small white circle)
    around the wheel to choose from ALL hues. The filled circle in the centre
    is a live preview of the selected colour.
    """

    hue_picked    = pyqtSignal(str)   # while dragging (live)
    hue_committed = pyqtSignal(str)   # when the handle is released

    _RING = 16   # ring thickness (px)

    def __init__(self, initial_hex: str = DEFAULT_UI_COLOR, parent=None):
        super().__init__(parent)
        self.setFixedSize(148, 148)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hue  = 0.53
        self._drag = False
        self.set_color(initial_hex)

    # ── API ──────────────────────────────────────────────────────────────────
    def color(self) -> str:
        return QColor.fromHsvF(self._hue, 1.0, 1.0).name()

    def set_color(self, hex_str: str):
        c = QColor((hex_str or "").strip())
        if c.isValid() and c.hsvHueF() >= 0:
            self._hue = c.hsvHueF()
            self.update()

    # ── geometry helpers ─────────────────────────────────────────────────────
    def _ring_rect(self) -> QRectF:
        m = self._RING / 2 + 3
        return QRectF(self.rect()).adjusted(m, m, -m, -m)

    def _hue_from_pos(self, pos: QPointF) -> float:
        c  = QRectF(self.rect()).center()
        dx = pos.x() - c.x()
        dy = c.y() - pos.y()          # screen y goes down — flip to math axis
        ang = math.atan2(dy, dx)      # [-π, π], counter-clockwise
        return (ang / (2 * math.pi)) % 1.0

    # ── drawing ──────────────────────────────────────────────────────────────
    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect   = self._ring_rect()
        center = rect.center()

        grad = QConicalGradient(center, 0)
        for i in range(0, 361, 20):
            grad.setColorAt(i / 360.0, QColor.fromHsvF((i % 360) / 360.0, 1.0, 1.0))
        p.setPen(QPen(QBrush(grad), self._RING))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(rect)

        # centre preview circle
        preview = QColor.fromHsvF(self._hue, 1.0, 1.0)
        inner   = rect.adjusted(30, 30, -30, -30)
        p.setPen(QPen(qcol(C.BORDER_B), 1))
        p.setBrush(QBrush(preview))
        p.drawEllipse(inner)

        # draggable handle
        r   = rect.width() / 2
        ang = self._hue * 2 * math.pi
        hx  = center.x() + r * math.cos(ang)
        hy  = center.y() - r * math.sin(ang)
        p.setPen(QPen(QColor("#00060a"), 2))
        p.setBrush(QBrush(QColor("#ffffff")))
        p.drawEllipse(QPointF(hx, hy), 7.5, 7.5)
        p.end()

    # ── fare ─────────────────────────────────────────────────────────────────
    def mousePressEvent(self, e):
        self._drag = True
        self._hue  = self._hue_from_pos(e.position())
        self.update()
        self.hue_picked.emit(self.color())

    def mouseMoveEvent(self, e):
        if self._drag:
            self._hue = self._hue_from_pos(e.position())
            self.update()
            self.hue_picked.emit(self.color())

    def mouseReleaseEvent(self, e):
        if self._drag:
            self._drag = False
            self.hue_committed.emit(self.color())


class CustomizeOverlay(QWidget):
    """Floating overlay — identity, voice, colour, eyes (screen share) and how
    JARVIS reacts. The context & personality controls below the fold apply the
    moment they are touched; APPLY commits name, user name, colour and voice."""

    saved = pyqtSignal(str, str, str, str)   # assistant_name, user_name, ui_color, voice
    _OW, _OH = 440, 780

    _QUALITY = (("light", "LIGHT"), ("medium", "MEDIUM"), ("high", "HIGH"))
    _CADENCE = (("standard", "STANDARD"), ("warm", "WARM"),
                ("live", "LIVE"), ("chatty", "CHATTY"))
    _HUMOUR  = (("off", "OFF"), ("subtle", "SUBTLE"),
                ("playful", "PLAYFUL"), ("chaotic", "CHAOTIC"))
    _EMOTION = (("off", "OFF"), ("light", "LIGHT"), ("full", "FULL"))

    def __init__(self, assistant_name="JARVIS", user_name="",
                 ui_color=DEFAULT_UI_COLOR, voice="", parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            CustomizeOverlay {{
                background: rgba(0, 6, 10, 245);
                border: 1px solid {C.BORDER_B};
                border-radius: 16px;
            }}
        """)

        def _lbl(txt, fs=9, bold=False, color=C.PRI, align=Qt.AlignmentFlag.AlignCenter):
            w = QLabel(txt); w.setAlignment(align)
            w.setFont(QFont(FONT_UI, fs,
                            QFont.Weight.Bold if bold else QFont.Weight.Normal))
            w.setStyleSheet(f"color: {color}; background: transparent;")
            return w

        def _sep():
            s = QFrame(); s.setFrameShape(QFrame.Shape.HLine)
            s.setStyleSheet(f"color: {C.BORDER}; margin: 4px 0;")
            return s

        _fs = (f"QLineEdit {{ background: #000d12; color: {C.TEXT}; "
               f"border: 1px solid {C.BORDER}; border-radius: 10px; padding: 4px 8px; }}"
               f"QLineEdit:focus {{ border: 1px solid {C.PRI}; }}")

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 18, 24, 12)
        root.setSpacing(8)

        root.addWidget(_lbl("⚙  CUSTOMISE ASSISTANT", 12, True))
        root.addWidget(_sep())

        content = QWidget()
        content.setStyleSheet("QWidget { background: transparent; }")
        lay = QVBoxLayout(content)
        lay.setContentsMargins(0, 4, 0, 4)
        lay.setSpacing(6)

        # ── Identity ──────────────────────────────────────────────────────────
        lay.addWidget(_lbl("ASSISTANT NAME", 8, color=C.TEXT_DIM,
                            align=Qt.AlignmentFlag.AlignLeft))
        self._name_input = QLineEdit(str(assistant_name))
        self._name_input.setFont(QFont(FONT_UI, 10))
        self._name_input.setFixedHeight(32)
        self._name_input.setStyleSheet(_fs)
        lay.addWidget(self._name_input)

        lay.addWidget(_lbl("YOUR NAME  (leave blank for default sir / efendim)", 8,
                            color=C.TEXT_DIM, align=Qt.AlignmentFlag.AlignLeft))
        self._user_input = QLineEdit(str(user_name))
        self._user_input.setPlaceholderText("e.g.  Tony   (leave blank for auto)")
        self._user_input.setFont(QFont(FONT_UI, 10))
        self._user_input.setFixedHeight(32)
        self._user_input.setStyleSheet(_fs)
        lay.addWidget(self._user_input)

        # ── Assistant voice — Gemini prebuilt voices ─────────────────────────
        from memory.config_manager import AVAILABLE_VOICES, DEFAULT_VOICE
        lay.addWidget(_lbl("ASSISTANT VOICE", 8, color=C.TEXT_DIM,
                            align=Qt.AlignmentFlag.AlignLeft))
        self._sel_voice   = str(voice or DEFAULT_VOICE)
        if self._sel_voice not in AVAILABLE_VOICES:
            self._sel_voice = DEFAULT_VOICE
        self._voice_btns: dict[str, QPushButton] = {}
        self._add_pills(lay, [(v, v) for v in AVAILABLE_VOICES],
                        self._sel_voice, self._on_voice_pick, self._voice_btns)

        # ── UI colour — colour wheel ─────────────────────────────────────────
        clr_hdr = QHBoxLayout()
        clr_hdr.addWidget(_lbl("UI COLOUR  —  drag the handle", 8,
                               color=C.TEXT_DIM, align=Qt.AlignmentFlag.AlignLeft))
        clr_hdr.addStretch()
        df_btn = QPushButton("DEFAULT")
        df_btn.setFixedSize(64, 20)
        df_btn.setFont(QFont(FONT_UI, 7, QFont.Weight.Bold))
        df_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        df_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 10px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        df_btn.clicked.connect(lambda: self._set_color(DEFAULT_UI_COLOR))
        clr_hdr.addWidget(df_btn)
        lay.addLayout(clr_hdr)

        self._initial_color = str(ui_color or DEFAULT_UI_COLOR).strip().lower()
        self._sel_color     = self._initial_color
        self.on_preview     = None   # callable(hex) — live preview; MainWindow wires it

        self._wheel = HueWheel(self._sel_color)
        wheel_row = QHBoxLayout()
        wheel_row.addStretch(); wheel_row.addWidget(self._wheel); wheel_row.addStretch()
        lay.addLayout(wheel_row)
        self._wheel.hue_picked.connect(self._on_wheel_pick)
        self._wheel.hue_committed.connect(self._on_wheel_commit)

        self._hex_input = QLineEdit(self._sel_color)
        self._hex_input.setPlaceholderText("#00d4ff   (custom hex colour)")
        self._hex_input.setFont(QFont(FONT_UI, 10))
        self._hex_input.setFixedHeight(28)
        self._hex_input.setStyleSheet(_fs)
        self._hex_input.textEdited.connect(self._on_hex_edited)
        lay.addWidget(self._hex_input)

        # ── Eyes & screen share (apply instantly) ────────────────────────────
        lay.addSpacing(4)
        lay.addWidget(_lbl("👁  EYES & SCREEN SHARE", 9, True))
        lay.addWidget(_sep())
        from memory.config_manager import (
            get_screen_awareness, save_screen_awareness,
            get_screen_glance, save_screen_glance,
            get_screen_share_quality, save_screen_share_quality,
            get_observe_interval, save_observe_interval,
            get_track_activity, save_track_activity,
            get_proactive_enabled, save_proactive_enabled,
            get_memory_enabled, save_memory_enabled,
            get_talk_cadence, save_talk_cadence,
            get_humor_level, save_humor_level,
            get_emotion_depth, save_emotion_depth,
        )
        self._share_btn = self._add_toggle(
            lay, "SCREEN SHARE: OFF", "SCREEN SHARE: ON",
            get_screen_awareness, save_screen_awareness,
            "Let JARVIS watch the active window and screen for context. Off = "
            "no foreground-window observer and all screen captures are refused.")

        lay.addWidget(_lbl("PICTURE  —  freshness vs. drain", 7, color=C.TEXT_DIM,
                            align=Qt.AlignmentFlag.AlignLeft))
        self._quality_btns: dict[str, QPushButton] = {}
        self._add_pills(lay, self._QUALITY, get_screen_share_quality(),
                        self._on_quality_pick, self._quality_btns)

        self._glance_btn = self._add_toggle(
            lay, "AUTO GLANCE: OFF", "AUTO GLANCE: ON",
            get_screen_glance, save_screen_glance,
            "Let check-ins & screen answers take a one-line vision look at the "
            "screen so they talk about what you are really doing. Needs screen "
            "share on.")

        obs_row = QHBoxLayout(); obs_row.setSpacing(6)
        obs_row.addWidget(_lbl("CHECK ACTIVITY EVERY", 7, color=C.TEXT_DIM,
                               align=Qt.AlignmentFlag.AlignLeft))
        from PyQt6.QtWidgets import QSpinBox
        self._observe_spin = QSpinBox()
        self._observe_spin.setRange(2, 60)
        self._observe_spin.setValue(get_observe_interval())
        self._observe_spin.setSuffix(" s")
        self._observe_spin.setFixedHeight(24)
        self._observe_spin.setFont(QFont(FONT_UI, 8))
        self._observe_spin.setStyleSheet(
            "QSpinBox { background: #000d12; color: " + C.TEXT +
            "; border: 1px solid " + C.BORDER + "; border-radius: 10px; }")
        self._observe_spin.valueChanged.connect(
            lambda v: save_observe_interval(int(v)))
        obs_row.addWidget(self._observe_spin)
        obs_row.addStretch()
        lay.addLayout(obs_row)

        self._timeline_btn = self._add_toggle(
            lay, "ACTIVITY TIMELINE: OFF", "ACTIVITY TIMELINE: ON",
            get_track_activity, save_track_activity,
            "Keep a short local timeline of what you have been doing, so JARVIS "
            "can follow up on unfinished things. Nothing leaves the machine.")

        # ── Reactions (apply instantly) ──────────────────────────────────────
        lay.addSpacing(4)
        lay.addWidget(_lbl("💬  REACTIONS & PRIVACY", 9, True))
        lay.addWidget(_sep())

        lay.addWidget(_lbl("TALK CADENCE", 7, color=C.TEXT_DIM,
                            align=Qt.AlignmentFlag.AlignLeft))
        self._cadence_btns: dict[str, QPushButton] = {}
        self._add_pills(lay, self._CADENCE, get_talk_cadence(),
                        self._on_cadence_pick, self._cadence_btns)

        lay.addWidget(_lbl("HUMOUR", 7, color=C.TEXT_DIM,
                            align=Qt.AlignmentFlag.AlignLeft))
        self._humour_btns: dict[str, QPushButton] = {}
        self._add_pills(lay, self._HUMOUR, get_humor_level(),
                        self._on_humour_pick, self._humour_btns)

        lay.addWidget(_lbl("EMOTION DEPTH", 7, color=C.TEXT_DIM,
                            align=Qt.AlignmentFlag.AlignLeft))
        self._emotion_btns: dict[str, QPushButton] = {}
        self._add_pills(lay, self._EMOTION, get_emotion_depth(),
                        self._on_emotion_pick, self._emotion_btns)

        self._proactive_btn = self._add_toggle(
            lay, "PROACTIVE CHECK-INS: OFF", "PROACTIVE CHECK-INS: ON",
            get_proactive_enabled, save_proactive_enabled,
            "Let JARVIS speak up on its own when you have been quiet a while.")

        self._memory_btn = self._add_toggle(
            lay, "PERSISTENT MEMORY: OFF", "PERSISTENT MEMORY: ON",
            get_memory_enabled, save_memory_enabled,
            "Whether JARVIS may write to long-term memory (session summaries, "
            "facts you tell it, lessons it learns).")

        lay.addWidget(_lbl("— Eyes & reaction options apply the moment you "
                           "touch them —", 7, color=C.BORDER_A,
                           align=Qt.AlignmentFlag.AlignCenter))

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setStyleSheet("""
            QScrollArea { background: transparent; border: none; }
            QScrollArea > QWidget > QWidget { background: transparent; }
        """)
        self._scroll.setWidget(content)
        root.addWidget(self._scroll, 1)

        btn_row = QHBoxLayout(); btn_row.setSpacing(8)
        save_btn = QPushButton("▸  APPLY CHANGES")
        save_btn.setFixedHeight(34)
        save_btn.setFont(QFont(FONT_UI, 9, QFont.Weight.Bold))
        save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 10px;
            }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border: 1px solid {C.PRI}; }}
        """)
        save_btn.clicked.connect(self._save)
        btn_row.addWidget(save_btn)

        cancel_btn = QPushButton("CANCEL")
        cancel_btn.setFixedHeight(34)
        cancel_btn.setFont(QFont(FONT_UI, 9))
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 10px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        cancel_btn.clicked.connect(self._cancel)
        btn_row.addWidget(cancel_btn)
        root.addLayout(btn_row)

    # ── pill groups (voice, quality, cadence, humour, emotion) ────────────────
    def _add_pills(self, lay, pairs, current, on_pick, store: dict):
        row = QHBoxLayout(); row.setSpacing(4)
        for key, label in pairs:
            b = QPushButton(label)
            b.setCheckable(True)
            b.setFixedHeight(28)
            b.setFont(QFont(FONT_UI, 8, QFont.Weight.Bold))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: on_pick(k))
            store[key] = b
            row.addWidget(b)
        lay.addLayout(row)
        self._refresh_pills(store, current)

    def _refresh_pills(self, store: dict, current: str):
        for name, b in store.items():
            on = (name == current)
            b.setChecked(on)
            if on:
                b.setStyleSheet(f"""
                    QPushButton {{ background: {C.PRI_GHO}; color: {C.PRI};
                        border: 1px solid {C.PRI}; border-radius: 10px; }}
                """)
            else:
                b.setStyleSheet(f"""
                    QPushButton {{ background: transparent; color: {C.TEXT_MED};
                        border: 1px solid {C.BORDER}; border-radius: 10px; }}
                    QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
                """)

    # ── instant-apply toggles ─────────────────────────────────────────────────
    def _add_toggle(self, lay, off_text, on_text, getter, setter, tip) -> QPushButton:
        b = QPushButton()
        b.setFixedHeight(28)
        b.setFont(QFont(FONT_UI, 8))
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setToolTip(tip)
        b.clicked.connect(lambda _=False: self._flip_toggle(getter, setter,
                                                            off_text, on_text))
        lay.addWidget(b)
        self._refresh_toggle(b, bool(getter()), off_text, on_text)
        return b

    def _flip_toggle(self, getter, setter, off_text, on_text):
        want = not bool(getter())
        setter(want)
        self._refresh_toggle(self.sender(), want, off_text, on_text)

    def _refresh_toggle(self, b: QPushButton, val: bool, off_text, on_text):
        b.setText(on_text if val else off_text)
        if val:
            b.setStyleSheet(f"""
                QPushButton {{ background: #07201c; color: {C.GREEN};
                    border: 1px solid {C.GREEN_D}; border-radius: 10px;
                    text-align: left; padding: 0 8px; }}
                QPushButton:hover {{ background: #0a2a24; }}
            """)
        else:
            b.setStyleSheet(f"""
                QPushButton {{ background: transparent; color: {C.TEXT_DIM};
                    border: 1px solid {C.BORDER}; border-radius: 10px;
                    text-align: left; padding: 0 8px; }}
                QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
            """)

    def _on_quality_pick(self, key: str):
        from memory.config_manager import save_screen_share_quality
        save_screen_share_quality(key)
        self._refresh_pills(self._quality_btns, key)

    def _on_cadence_pick(self, key: str):
        from memory.config_manager import save_talk_cadence
        save_talk_cadence(key)
        self._refresh_pills(self._cadence_btns, key)

    def _on_humour_pick(self, key: str):
        from memory.config_manager import save_humor_level
        save_humor_level(key)
        self._refresh_pills(self._humour_btns, key)

    def _on_emotion_pick(self, key: str):
        from memory.config_manager import save_emotion_depth
        save_emotion_depth(key)
        self._refresh_pills(self._emotion_btns, key)

    # ── voice selection ──────────────────────────────────────────────────────
    def _on_voice_pick(self, name: str):
        self._sel_voice = name
        self._refresh_pills(self._voice_btns, name)

    # ── colour flow ──────────────────────────────────────────────────────────
    def _set_color(self, hx: str, update_wheel: bool = True, preview: bool = True):
        """Updates the selected colour; hex box + wheel stay in sync, theme is live-previewed."""
        self._sel_color = hx.strip().lower()
        self._hex_input.blockSignals(True)
        self._hex_input.setText(self._sel_color)
        self._hex_input.blockSignals(False)
        if update_wheel:
            self._wheel.set_color(self._sel_color)
        if preview and self.on_preview:
            self.on_preview(self._sel_color)

    def _on_wheel_pick(self, hx: str):
        # While dragging: update the hex box, don't apply the theme yet
        self._sel_color = hx
        self._hex_input.blockSignals(True)
        self._hex_input.setText(hx)
        self._hex_input.blockSignals(False)

    def _on_wheel_commit(self, hx: str):
        # Handle released → live-preview the whole interface
        self._set_color(hx, update_wheel=False)

    def _on_hex_edited(self, text: str):
        t = text.strip().lower()
        if t.startswith("#") and len(t) == 7:
            try:
                int(t[1:], 16)
            except ValueError:
                return
            self._set_color(t, update_wheel=True, preview=True)

    def _cancel(self):
        # If a preview was applied, revert to the colour from launch
        if self.on_preview and self._sel_color != self._initial_color:
            self.on_preview(self._initial_color)
        self.hide()

    def _save(self):
        name = self._name_input.text().strip() or "JARVIS"
        user = self._user_input.text().strip()
        self.saved.emit(name, user, self._sel_color or DEFAULT_UI_COLOR, self._sel_voice)
        self.hide()


class ApiKeysOverlay(QWidget):
    """Floating overlay — view/edit the Gemini key and every free fallback
    provider key, any time, from the CONTROLS drawer. One-time onboarding is
    not the only way to set these anymore."""

    saved = pyqtSignal(str, list)                # gemini_key, free_providers
    test_done = pyqtSignal(list)                 # [(name, ok, message), ...]
    _OW, _OH = 500, 560

    _PROVIDER_ROWS = [
        ("groq",       "GROQ   (free)", "https://api.groq.com/openai/v1",
         "llama-3.3-70b-versatile", "gsk_…", "console.groq.com"),
        ("cerebras",   "CEREBRAS (free)", "https://api.cerebras.ai/v1",
         "llama-3.3-70b", "sk-…", "cloud.cerebras.ai"),
        ("openrouter", "OPENROUTER (free)", "https://openrouter.ai/api/v1",
         "meta-llama/llama-3.3-70b-instruct:free", "sk-or-…", "openrouter.ai"),
        ("huggingface", "HUGGING FACE (free)", "https://router.huggingface.co/v1",
         "meta-llama/Llama-3.1-8B-Instruct", "hf_…", "huggingface.co/settings/tokens"),
    ]

    def __init__(self, gemini_key="", providers=(), parent=None):
        super().__init__(parent)
        gemini_key = gemini_key if isinstance(gemini_key, str) else ""
        providers = [p for p in providers if isinstance(p, dict)]
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            ApiKeysOverlay {{
                background: rgba(0, 6, 10, 245);
                border: 1px solid {C.BORDER_B};
                border-radius: 16px;
            }}
        """)
        by_name = {str(p.get("name")).lower(): p
                   for p in providers if isinstance(p, dict)}

        ly = QVBoxLayout(self)
        ly.setContentsMargins(24, 18, 24, 18)
        ly.setSpacing(8)

        def _lbl(txt, fs=9, bold=False, color=C.PRI,
                 align=Qt.AlignmentFlag.AlignCenter):
            w = QLabel(txt); w.setAlignment(align)
            w.setFont(QFont(FONT_UI, fs,
                            QFont.Weight.Bold if bold else QFont.Weight.Normal))
            w.setStyleSheet(f"color: {color}; background: transparent;")
            return w

        self._fs = (f"QLineEdit {{ background: #000d12; color: {C.TEXT}; "
                    f"border: 1px solid {C.BORDER}; border-radius: 10px; padding: 4px 8px; }}"
                    f"QLineEdit:focus {{ border: 1px solid {C.PRI}; }}")

        ly.addWidget(_lbl("🔑  API KEYS", 12, True))
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        ly.addWidget(sep)

        ly.addWidget(_lbl("GEMINI API KEY  (required)", 8, color=C.TEXT_DIM,
                          align=Qt.AlignmentFlag.AlignLeft))
        self._gemini_input = QLineEdit(gemini_key)
        self._gemini_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._gemini_input.setPlaceholderText("AIza…")
        self._gemini_input.setFont(QFont(FONT_UI, 10))
        self._gemini_input.setFixedHeight(32)
        self._gemini_input.setStyleSheet(self._fs)
        ly.addWidget(self._gemini_input)
        ly.addSpacing(6)

        ly.addWidget(_lbl("FREE FALLBACK KEYS  (optional — Gemini quota "
                          "safety net)", 8, color=C.TEXT_DIM,
                          align=Qt.AlignmentFlag.AlignLeft))
        self._pv_inputs: dict[str, QLineEdit] = {}
        for name, label, base, model, hint, where in self._PROVIDER_ROWS:
            row = QHBoxLayout(); row.setSpacing(6)
            lbl = QLabel(label)
            lbl.setFont(QFont(FONT_UI, 8, QFont.Weight.Bold))
            lbl.setFixedWidth(158)
            lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            row.addWidget(lbl)
            inp = QLineEdit()
            inp.setEchoMode(QLineEdit.EchoMode.Password)
            inp.setPlaceholderText(hint)
            inp.setFont(QFont(FONT_UI, 9))
            inp.setFixedHeight(28)
            inp.setStyleSheet(self._fs)
            saved = by_name.get(name) or {}
            if saved.get("api_key"):
                inp.setText(str(saved["api_key"]))
            row.addWidget(inp, 1)
            self._pv_inputs[name] = inp
            ly.addLayout(row)
            ly.addWidget(_lbl(where, 7, color=C.PRI_DIM,
                              align=Qt.AlignmentFlag.AlignLeft))
        ly.addSpacing(4)

        show_btn = QPushButton("◉  SHOW KEYS")
        show_btn.setFixedHeight(26)
        show_btn.setFont(QFont(FONT_UI, 7))
        show_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        show_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 10px;
                text-align: left; padding: 0 8px;
            }}
            QPushButton:hover {{ color: {C.PRI}; border-color: {C.BORDER_B}; }}
        """)

        def _toggle_show():
            for w in list(self._pv_inputs.values()) + [self._gemini_input]:
                w.setEchoMode(QLineEdit.EchoMode.Normal
                              if w.echoMode() == QLineEdit.EchoMode.Password
                              else QLineEdit.EchoMode.Password)
        show_btn.clicked.connect(_toggle_show)
        ly.addWidget(show_btn)

        self._test_btn = QPushButton("⚡  Test keys")
        self._test_btn.setFixedHeight(32)
        self._test_btn.setFont(QFont(FONT_UI, 9, QFont.Weight.DemiBold))
        self._test_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._test_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 10px;
                text-align: left; padding: 0 8px;
            }}
            QPushButton:hover {{ color: {C.GREEN}; border-color: {C.BORDER_B}; }}
        """)
        self._test_btn.clicked.connect(self._start_test)

        hint = QLabel("Keys are stored only on this machine (config/api_keys.json). "
                      "Testing runs in the background — the UI never freezes.")
        hint.setWordWrap(True)
        hint.setFont(QFont(FONT_UI, 8))
        hint.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        self.test_done.connect(self._apply_test_results)
        ly.addWidget(self._test_btn)
        ly.addWidget(hint)

        save_btn = QPushButton("▸  SAVE & CLOSE")
        save_btn.setFixedHeight(34)
        save_btn.setFont(QFont(FONT_UI, 9, QFont.Weight.Bold))
        save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 10px;
            }}
            QPushButton:hover {{
                background: {C.PRI_GHO}; border: 1px solid {C.PRI};
            }}
        """)
        save_btn.clicked.connect(self._save)
        ly.addWidget(save_btn)

        self._status = _lbl("", 8, color=C.GREEN, align=Qt.AlignmentFlag.AlignLeft)
        ly.addWidget(self._status)
        self._bad_gemini = False

    def _save(self):
        gemini_key = self._gemini_input.text().strip()
        if gemini_key and len(gemini_key) > 15:
            self._bad_gemini = False
        elif gemini_key:                          # looks too short to be real
            self._warn("Gemini key looks too short — please check it.")
            return
        # Empty Gemini key is allowed: providers alone may be enough, and the
        # rest of the app can re-prompt on first use.
        providers = []
        for name, label, base, model, hint, where in self._PROVIDER_ROWS:
            providers.append({
                "name": name, "base_url": base,
                "api_key": self._pv_inputs[name].text().strip(), "model": model,
            })
        self.saved.emit(gemini_key, providers)
        self.hide()

    def _warn(self, txt: str):
        self._status.setText(txt)
        self._status.setStyleSheet(f"color: {C.RED}; background: transparent;")

    def _start_test(self):
        """Probe each key on a daemon thread so the UI never freezes; a queued
        signal carries results back to the main thread."""
        rows = []
        gemini_key = self._gemini_input.text().strip()
        for name, label, base, model, hint, where in self._PROVIDER_ROWS:
            rows.append({"name": name, "base_url": base,
                         "api_key": self._pv_inputs[name].text().strip(),
                         "model": model})
        self._status.setText("Testing keys… (up to ~8s each, in the background)")
        self._status.setStyleSheet(
            f"color: {C.TEXT_DIM}; background: transparent;")
        self._test_btn.setEnabled(False)
        self._test_btn.setText("Testing…")

        def _work():
            try:
                from core import api_verify
                results = api_verify.verify_all(gemini_key, rows)
            except Exception as e:
                results = [("gemini", False, f"verify crashed: {e}")]
            self.test_done.emit(results)

        self._test_thread = threading.Thread(target=_work, daemon=True)
        self._test_thread.start()

    def _apply_test_results(self, results: list):
        self._test_btn.setEnabled(True)
        self._test_btn.setText("⚡  Test keys")
        names = {"gemini": self._gemini_input}
        for n, inp in self._pv_inputs.items():
            names[n] = inp
        ok_count = 0
        for name, ok, msg in results:
            if ok:
                ok_count += 1
            inp = names.get(name)
            if inp is not None:
                edge = C.GREEN if ok else C.RED
                inp.setStyleSheet(self._fs + f"\nQLineEdit {{ border: 1px solid {edge}; }}")
        lines = [f"{name}: {'✓' if ok else '✗'} {msg}" for name, ok, msg in results]
        self._status.setText("  ".join(lines))
        self._status.setStyleSheet(
            f"color: {C.GREEN if ok_count else C.RED}; background: transparent;")


class PluginManagerOverlay(QWidget):
    """Floating overlay — lists discovered plugins with per-plugin ON/OFF toggles."""

    _OW = 420

    def __init__(self, plugins: list[dict], parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            PluginManagerOverlay {{
                background: rgba(0, 6, 10, 245);
                border: 1px solid {C.BORDER_B};
                border-radius: 16px;
            }}
        """)
        self.setFixedWidth(self._OW)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(6)

        hdr = QLabel("🧩  PLUGIN MANAGER")
        hdr.setFont(QFont(FONT_UI, 12, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        lay.addWidget(hdr)
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        lay.addWidget(sep)

        if not plugins:
            empty = QLabel("No plugins found in /plugins.")
            empty.setFont(QFont(FONT_UI, 8))
            empty.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            lay.addWidget(empty)

        for p in plugins:
            lay.addLayout(self._build_row(p))

        lay.addSpacing(4)
        close_btn = QPushButton("CLOSE")
        close_btn.setFixedHeight(30)
        close_btn.setFont(QFont(FONT_UI, 9))
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 10px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        close_btn.clicked.connect(self.hide)
        lay.addWidget(close_btn)
        self.adjustSize()

    def _build_row(self, p: dict) -> QHBoxLayout:
        row = QHBoxLayout(); row.setSpacing(6)

        label_text = p["name"] if p["valid"] else f"{p['name']}  (⚠ {p['file']})"
        lbl = QLabel(label_text)
        lbl.setFont(QFont(FONT_UI, 8))
        lbl.setStyleSheet(f"color: {C.TEXT if p['valid'] else C.TEXT_DIM}; background: transparent;")
        lbl.setToolTip(p["description"] if p["valid"] else p["error"])
        lbl.setWordWrap(False)
        row.addWidget(lbl, stretch=1)

        btn = QPushButton()
        btn.setFixedSize(72, 24)
        btn.setFont(QFont(FONT_UI, 7, QFont.Weight.Bold))
        if not p["valid"]:
            btn.setText("BROKEN")
            btn.setEnabled(False)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {C.TEXT_DIM};
                    border: 1px solid {C.BORDER}; border-radius: 10px;
                }}
            """)
        else:
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self._style_toggle(btn, p["enabled"])
            btn.clicked.connect(lambda _, name=p["name"], b=btn: self._toggle(name, b))
        row.addWidget(btn)
        return row

    def _style_toggle(self, btn: QPushButton, enabled: bool):
        if enabled:
            btn.setText("ON")
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: #07201c; color: {C.GREEN};
                    border: 1px solid {C.GREEN_D}; border-radius: 10px;
                }}
                QPushButton:hover {{ background: #0a2a24; }}
            """)
        else:
            btn.setText("OFF")
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {C.TEXT_DIM};
                    border: 1px solid {C.BORDER}; border-radius: 10px;
                }}
                QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
            """)

    def _toggle(self, name: str, btn: QPushButton):
        from memory.config_manager import get_plugin_enabled, save_plugin_enabled
        new_val = not get_plugin_enabled(name)
        save_plugin_enabled(name, new_val)
        self._style_toggle(btn, new_val)


class _HudOverlay(QWidget):
    """Base for the floating panels placed by hand over the HUD.

    They are children of the central widget but sit in no layout, so Qt never
    invalidates the region they occupy when they hide or shrink: the HUD keeps
    painting around them and their last frame stays on screen as a ghost. Any
    overlay positioned with _centre_overlay needs this."""

    def hideEvent(self, e):
        p = self.parentWidget()
        if p is not None:
            # Repaint exactly what we were covering, before we stop covering it.
            p.update(self.geometry())
        super().hideEvent(e)

    def closeEvent(self, e):
        p = self.parentWidget()
        if p is not None:
            p.update(self.geometry())
        super().closeEvent(e)


class ConfirmBanner(_HudOverlay):
    """The gate in front of an action that cannot be taken back.

    The old confirmation was a tool parameter the model filled in itself, which
    means it confirmed its own shutdown requests. This is the interface asking,
    and the answer travels from a human finger to core/confirm.py without the
    model in the loop. Nothing blocks while it is up: the assistant keeps
    talking, so this costs no latency — unlike the old gate, which spent two
    tool round trips on every power command."""

    answered = pyqtSignal(bool)
    _OW = 430

    def __init__(self, title: str, detail: str, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            ConfirmBanner {{
                background: rgba(14, 3, 0, 250);
                border: 1px solid {C.ACC};
                border-radius: 16px;
            }}
        """)
        self.setFixedWidth(self._OW)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(8)

        hdr = QLabel("⚠  CONFIRM")
        hdr.setFont(QFont(FONT_UI, 11, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.ACC}; background: transparent;")
        lay.addWidget(hdr)

        ttl = QLabel(title)
        ttl.setWordWrap(True)
        ttl.setFont(QFont(FONT_UI, 10, QFont.Weight.Bold))
        ttl.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
        lay.addWidget(ttl)

        if detail:
            dtl = QLabel(detail)
            dtl.setWordWrap(True)
            dtl.setFont(QFont(FONT_UI, 8))
            dtl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
            lay.addWidget(dtl)

        row = QHBoxLayout(); row.setSpacing(8)

        yes = QPushButton("▸  CONFIRM")
        yes.setFixedHeight(32)
        yes.setFont(QFont(FONT_UI, 9, QFont.Weight.Bold))
        yes.setCursor(Qt.CursorShape.PointingHandCursor)
        yes.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.ACC};
                border: 1px solid {C.ACC}; border-radius: 10px; }}
            QPushButton:hover {{ background: rgba(255,107,0,40); }}
        """)
        yes.clicked.connect(lambda: self.answered.emit(True))
        row.addWidget(yes)

        no = QPushButton("CANCEL")
        no.setFixedHeight(32)
        no.setFont(QFont(FONT_UI, 9))
        no.setCursor(Qt.CursorShape.PointingHandCursor)
        no.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 10px; }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        no.clicked.connect(lambda: self.answered.emit(False))
        row.addWidget(no)
        lay.addLayout(row)

        # Default focus on CANCEL: if someone hits Enter without reading, the
        # safe answer wins.
        no.setDefault(True)
        no.setFocus()


class AudioDeviceOverlay(_HudOverlay):
    """Choose which microphone JARVIS listens to and which speakers it uses.

    Both audio streams used to open with no `device=` at all, so they always
    took the OS default — which on Windows moves by itself the moment a headset
    is plugged in. 'JARVIS can't hear me' is usually 'JARVIS is listening to the
    webcam'."""

    picked = pyqtSignal()      # emitted after Apply, when something changed
    _OW = 460

    def __init__(self, parent=None):
        super().__init__(parent)
        from core.audio_devices import list_devices, DEFAULT_LABEL
        from memory.config_manager import get_input_device, get_output_device

        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            AudioDeviceOverlay {{
                background: rgba(0, 6, 10, 245);
                border: 1px solid {C.BORDER_B};
                border-radius: 16px;
            }}
        """)
        self.setFixedWidth(self._OW)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(6)

        hdr = QLabel("🎧  AUDIO DEVICES")
        hdr.setFont(QFont(FONT_UI, 12, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        lay.addWidget(hdr)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        lay.addWidget(sep)

        _combo_css = (
            f"QComboBox {{ background: #000d12; color: {C.TEXT}; "
            f"border: 1px solid {C.BORDER}; border-radius: 10px; padding: 4px 8px; }}"
            f"QComboBox:hover {{ border-color: {C.BORDER_B}; }}"
            f"QComboBox QAbstractItemView {{ background: #000d12; color: {C.TEXT}; "
            f"selection-background-color: {C.PRI_GHO}; border: 1px solid {C.BORDER}; }}"
        )

        def _row(label: str, kind: str, current: str) -> QComboBox:
            cap = QLabel(label)
            cap.setFont(QFont(FONT_UI, 8))
            cap.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            lay.addWidget(cap)

            box = QComboBox()
            box.setFont(QFont(FONT_UI, 9))
            box.setFixedHeight(30)
            box.setStyleSheet(_combo_css)
            # The list is served from a cache warmed on a background thread at
            # startup, so opening this panel never blocks the Qt thread on the
            # host audio API.
            box.addItem(DEFAULT_LABEL, "")
            for name in list_devices(kind):
                box.addItem(name, name)
            idx = box.findData(current) if current else 0
            box.setCurrentIndex(idx if idx >= 0 else 0)
            if current and idx < 0:
                # Saved device is not plugged in right now. Show it rather than
                # silently resetting the user's choice to default.
                box.addItem(f"{current}  (not connected)", current)
                box.setCurrentIndex(box.count() - 1)
            lay.addWidget(box)
            return box

        self._in_box  = _row("MICROPHONE — what JARVIS hears you with",
                             "input", get_input_device())
        lay.addSpacing(4)
        self._out_box = _row("SPEAKERS — what JARVIS talks through",
                             "output", get_output_device())

        note = QLabel("Applying reconnects the session. Your conversation is kept.")
        note.setWordWrap(True)
        note.setFont(QFont(FONT_UI, 7))
        note.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        lay.addSpacing(6)
        lay.addWidget(note)

        row = QHBoxLayout(); row.setSpacing(8)
        ok = QPushButton("▸  APPLY")
        ok.setFixedHeight(32)
        ok.setFont(QFont(FONT_UI, 9, QFont.Weight.Bold))
        ok.setCursor(Qt.CursorShape.PointingHandCursor)
        ok.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 10px; }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border-color: {C.PRI}; }}
        """)
        ok.clicked.connect(self._apply)
        row.addWidget(ok)

        cancel = QPushButton("CLOSE")
        cancel.setFixedHeight(32)
        cancel.setFont(QFont(FONT_UI, 9))
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 10px; }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        cancel.clicked.connect(self.hide)
        row.addWidget(cancel)
        lay.addLayout(row)

    def _apply(self):
        from memory.config_manager import (
            get_input_device, get_output_device,
            save_input_device, save_output_device,
        )
        new_in  = self._in_box.currentData()  or ""
        new_out = self._out_box.currentData() or ""
        changed = (new_in != get_input_device()) or (new_out != get_output_device())
        save_input_device(new_in)
        save_output_device(new_out)
        self.hide()
        # Only rebuild the session if something actually moved — a no-op Apply
        # should not cost a reconnect.
        if changed:
            self.picked.emit()


class MemoryOverlay(_HudOverlay):
    """Everything JARVIS has stored about you, and when it learned it.

    Memory used to be a 2200-character store that deleted its oldest entries
    when full and mentioned it only on stdout. The cap is gone; this panel is
    the other half of that change — a memory you cannot inspect is a memory you
    cannot trust, and 'delete' has to be something the person can do."""

    _OW = 520

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            MemoryOverlay {{
                background: rgba(0, 6, 10, 246);
                border: 1px solid {C.BORDER_B};
                border-radius: 16px;
            }}
        """)
        self.setFixedWidth(self._OW)

        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(20, 16, 20, 16)
        self._lay.setSpacing(5)
        self._rebuild()

    def _clear_layout(self):
        """Take every item out of the layout and detach it from the widget tree
        in this call.

        deleteLater() on its own is not enough: it queues destruction for the
        next event-loop pass, and until then the old rows are still children of
        this widget and still paint — which is what drew half of the previous
        panel over the new one. setParent(None) removes them from the tree now;
        deleteLater() then frees them safely."""
        while self._lay.count():
            item = self._lay.takeAt(0)
            w = item.widget()
            if w is not None:
                # hide() stops it painting in this frame; deleteLater() frees it
                # safely afterwards. setParent(None) would also stop the paint,
                # but it turns the widget into a top-level window for the moment
                # between the two calls, which is not something to leave lying
                # around inside a click handler.
                w.hide()
                w.deleteLater()
                continue
            sub = item.layout()
            if sub is not None:
                while sub.count():
                    si = sub.takeAt(0)
                    sw = si.widget()
                    if sw is not None:
                        sw.hide()
                        sw.deleteLater()
                sub.deleteLater()

    def _settle(self, before):
        """Size the panel to its content, re-centre it, and repaint what the old
        size covered.

        The re-size has to happen here rather than at the end of _rebuild
        because Qt has not polished the freshly-created children at that point,
        so the size hint it would read is the empty-layout one. Measured: a
        first adjustSize() returned 32 px for a panel whose content needed 155,
        and a second call — after the same widgets had been through the event
        loop — returned 155. So this runs twice: once now, once on the next
        turn, from _rebuild.

        The re-centre and the repaint are needed because the overlay is placed
        by hand and is in no layout: shrinking it leaves it off-centre and
        leaves its former pixels on screen, since nothing tells the parent that
        region changed. The repaint has to cover the union of the old and new
        rectangles."""
        self._lay.invalidate()
        self._lay.activate()
        self.updateGeometry()
        self.adjustSize()

        p = self.parentWidget()
        if p is None:
            self.update()
            return
        self.move(max(0, (p.width()  - self.width())  // 2),
                  max(0, (p.height() - self.height()) // 2))
        p.update(before.united(self.geometry()))
        self.update()

    def _rebuild(self):
        before = self.geometry()
        self._clear_layout()

        from memory.memory_manager import all_entries_for_ui

        hdr = QLabel("🧠  WHAT JARVIS REMEMBERS")
        hdr.setFont(QFont(FONT_UI, 12, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        self._lay.addWidget(hdr)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        self._lay.addWidget(sep)

        rows = all_entries_for_ui()

        cap = QLabel(f"{len(rows)} stored facts — newest first. "
                     f"Nothing here is sent anywhere; it lives in "
                     f"memory/long_term.json on this machine.")
        cap.setWordWrap(True)
        cap.setFont(QFont(FONT_UI, 7))
        cap.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        self._lay.addWidget(cap)

        if not rows:
            empty = QLabel("Nothing stored yet.")
            empty.setFont(QFont(FONT_UI, 9))
            empty.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
            self._lay.addWidget(empty)
        else:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFixedHeight(min(420, 34 * len(rows) + 10))
            scroll.setStyleSheet(
                f"QScrollArea {{ border: 1px solid {C.BORDER}; border-radius: 10px; "
                f"background: transparent; }}"
            )
            inner = QWidget()
            ilay  = QVBoxLayout(inner)
            ilay.setContentsMargins(6, 6, 6, 6)
            ilay.setSpacing(3)

            for r in rows:
                line = QHBoxLayout(); line.setSpacing(6)
                txt = QLabel(f"<b>{r['key'].replace('_', ' ')}</b> "
                             f"<span style='color:{C.TEXT_MED}'>— {r['value']}</span>")
                txt.setWordWrap(True)
                txt.setFont(QFont(FONT_UI, 8))
                txt.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
                line.addWidget(txt, 1)

                meta = QLabel(f"{r['category'][:4]} · {r['updated'] or '—'}")
                meta.setFont(QFont(FONT_UI, 7))
                meta.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
                line.addWidget(meta)

                rm = QPushButton("✕")
                rm.setFixedSize(20, 20)
                rm.setFont(QFont(FONT_UI, 8, QFont.Weight.Bold))
                rm.setCursor(Qt.CursorShape.PointingHandCursor)
                rm.setToolTip("Forget this")
                rm.setStyleSheet(f"""
                    QPushButton {{ background: transparent; color: {C.TEXT_DIM};
                        border: 1px solid {C.BORDER}; border-radius: 10px; }}
                    QPushButton:hover {{ color: {C.RED}; border-color: {C.RED}; }}
                """)
                rm.clicked.connect(
                    lambda _=False, c=r["category"], k=r["key"]: self._forget(c, k))
                line.addWidget(rm)

                holder = QWidget()
                holder.setLayout(line)
                ilay.addWidget(holder)

            ilay.addStretch()
            scroll.setWidget(inner)
            self._lay.addWidget(scroll)

        close = QPushButton("CLOSE")
        close.setFixedHeight(30)
        close.setFont(QFont(FONT_UI, 9))
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 10px; }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        close.clicked.connect(self.hide)
        self._lay.addWidget(close)

        self._settle(before)
        # …and again once Qt has polished the new children, because the size
        # hint is not final until then. Harmless when the first pass already
        # got it right: _settle is idempotent.
        QTimer.singleShot(0, lambda g=before: self._settle(g))

    def _forget(self, category: str, key: str):
        from memory.memory_manager import forget
        forget(key, category)
        # Rebuild on the NEXT event-loop turn, not inside this click handler.
        # The rebuild destroys the very ✕ button that emitted this signal, and
        # Qt is entitled to touch the sender after a slot returns; tearing it
        # down mid-emission is how a widget ends up half-alive on screen.
        QTimer.singleShot(0, self._rebuild)


class ClipboardPanel(QWidget):
    """Floating panel shown when text is copied — offers quick Jarvis actions."""

    action_requested = pyqtSignal(str)
    _W, _H = 326, 112

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            ClipboardPanel {{
                background: rgba(0, 8, 14, 248);
                border: 1px solid {C.BORDER_B};
                border-radius: 16px;
            }}
        """)
        self.setFixedWidth(self._W)
        self._clip_text = ""

        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 7)
        lay.setSpacing(4)

        hdr = QHBoxLayout(); hdr.setSpacing(4)
        icon_lbl = QLabel("◈  CLIPBOARD DETECTED")
        icon_lbl.setFont(QFont(FONT_UI, 7, QFont.Weight.Bold))
        icon_lbl.setStyleSheet(f"color: {C.ACC2}; background: transparent;")
        hdr.addWidget(icon_lbl); hdr.addStretch()
        x_btn = QPushButton("✕")
        x_btn.setFixedSize(16, 16)
        x_btn.setFont(QFont(FONT_UI, 8))
        x_btn.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent; border: none;")
        x_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        x_btn.clicked.connect(self.hide)
        hdr.addWidget(x_btn)
        lay.addLayout(hdr)

        self._preview = QLabel()
        self._preview.setFont(QFont(FONT_UI, 8))
        self._preview.setStyleSheet(f"""
            color: {C.TEXT}; background: {C.PANEL2};
            border: 1px solid {C.BORDER}; border-radius: 10px; padding: 4px 6px;
        """)
        self._preview.setWordWrap(False)
        self._preview.setFixedHeight(28)
        lay.addWidget(self._preview)

        btn_row = QHBoxLayout(); btn_row.setSpacing(4)
        _bs = (f"QPushButton {{ background: {C.PANEL2}; color: {C.TEXT_MED}; "
               f"border: 1px solid {C.BORDER}; border-radius: 10px; }}"
               f"QPushButton:hover {{ color: {C.PRI}; border-color: {C.BORDER_B}; }}")
        for label, cmd_fmt in [
            ("TRANSLATE", "Translate this text to English: {text}"),
            ("SUMMARISE", "Summarise this: {text}"),
            ("EXPLAIN",   "Explain this: {text}"),
            ("FIX",       "Fix grammar and spelling: {text}"),
        ]:
            b = QPushButton(label)
            b.setFixedHeight(22)
            b.setFont(QFont(FONT_UI, 7, QFont.Weight.Bold))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(_bs)
            b.clicked.connect(lambda _, c=cmd_fmt: self._trigger(c))
            btn_row.addWidget(b)
        lay.addLayout(btn_row)

        self._dismiss_timer = QTimer(self)
        self._dismiss_timer.setSingleShot(True)
        self._dismiss_timer.timeout.connect(self.hide)
        self.hide()

    def _trigger(self, cmd_fmt: str):
        if self._clip_text:
            self.action_requested.emit(cmd_fmt.format(text=self._clip_text[:800]))
        self.hide()

    def show_clipboard(self, text: str):
        self._clip_text = text
        preview = text[:58].replace('\n', ' ')
        if len(text) > 58:
            preview += "…"
        self._preview.setText(f'"{preview}"')
        self.show(); self.raise_()
        self._dismiss_timer.start(8000)


class PluginSettingsOverlay(QWidget):
    """Floating overlay — renders per-plugin settings forms.

    Fully generic: it iterates the settings schemas a plugin declared via its
    PLUGIN_SETTINGS constant (delivered by PluginRegistry.settings_schemas) and
    builds a form for each. It knows NOTHING about any specific plugin, so the
    core stays clean and plugins remain pure drop-in — install a plugin that
    declares fields (e.g. the 3D-printer suite) and its section appears here;
    install none and this panel simply says there's nothing to configure.
    """

    _test_done = pyqtSignal(str, bool, str)   # namespace, ok, message
    _OW = 460

    def __init__(self, sections: list[dict], parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            PluginSettingsOverlay {{
                background: rgba(0, 6, 10, 245);
                border: 1px solid {C.BORDER_B};
                border-radius: 16px;
            }}
        """)
        self._sections = sections or []
        self._widgets: dict[tuple, object] = {}    # (namespace, key) -> input widget
        self._types:   dict[tuple, str]    = {}     # (namespace, key) -> field type
        self._status_labels: dict[str, QLabel] = {} # namespace -> status QLabel
        self._test_done.connect(self._on_test_done)

        self._fs = (f"QLineEdit {{ background: #000d12; color: {C.TEXT}; "
                    f"border: 1px solid {C.BORDER}; border-radius: 10px; padding: 4px 8px; }}"
                    f"QLineEdit:focus {{ border: 1px solid {C.PRI}; }}")

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 16, 22, 16)
        root.setSpacing(8)

        root.addWidget(self._lbl("⚙  PLUGIN SETTINGS", 12, True))
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        root.addWidget(sep)

        if not self._sections:
            root.addWidget(self._lbl(
                "No configurable plugins are installed.\nDrop a plugin that needs "
                "settings (like the 3D-printer suite) into the plugins folder and "
                "it will show up here.", 9, color=C.TEXT_DIM))
        else:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setStyleSheet("QScrollArea { background: transparent; }")
            inner = QWidget()
            inner.setStyleSheet("background: transparent;")
            form = QVBoxLayout(inner)
            form.setContentsMargins(0, 0, 6, 0)
            form.setSpacing(6)
            for sec in self._sections:
                self._build_section(form, sec)
            form.addStretch(1)
            scroll.setWidget(inner)
            root.addWidget(scroll, 1)

        # ── bottom buttons ───────────────────────────────────────────────────
        btn_row = QHBoxLayout(); btn_row.setSpacing(8)
        if self._sections:
            save_btn = QPushButton("▸  SAVE")
            save_btn.setFixedHeight(34)
            save_btn.setFont(QFont(FONT_UI, 9, QFont.Weight.Bold))
            save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            save_btn.setStyleSheet(f"""
                QPushButton {{ background: transparent; color: {C.PRI};
                    border: 1px solid {C.PRI_DIM}; border-radius: 10px; }}
                QPushButton:hover {{ background: {C.PRI_GHO}; border: 1px solid {C.PRI}; }}
            """)
            save_btn.clicked.connect(self._save_all)
            btn_row.addWidget(save_btn)

        close_btn = QPushButton("CLOSE")
        close_btn.setFixedHeight(34)
        close_btn.setFont(QFont(FONT_UI, 9))
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 10px; }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        close_btn.clicked.connect(self.hide)
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)

    # ── helpers ───────────────────────────────────────────────────────────────
    def _lbl(self, txt, fs=9, bold=False, color=C.PRI,
             align=Qt.AlignmentFlag.AlignLeft):
        w = QLabel(txt); w.setAlignment(align); w.setWordWrap(True)
        w.setFont(QFont(FONT_UI, fs,
                        QFont.Weight.Bold if bold else QFont.Weight.Normal))
        w.setStyleSheet(f"color: {color}; background: transparent;")
        return w

    def _build_section(self, form: QVBoxLayout, sec: dict):
        ns     = sec.get("namespace") or sec.get("plugin") or "plugin"
        title  = sec.get("title") or ns
        fields = sec.get("fields") or []
        values = sec.get("values") or {}

        form.addSpacing(4)
        form.addWidget(self._lbl(title, 10, True, C.PRI))

        for field in fields:
            if not isinstance(field, dict) or not field.get("key"):
                continue
            key   = field["key"]
            ftype = (field.get("type") or "text").lower()
            label = field.get("label") or key
            default = field.get("default")
            stored  = values.get(key, default)

            form.addWidget(self._lbl(label.upper(), 8, color=C.TEXT_DIM))

            if ftype == "choice":
                w = QComboBox()
                w.addItems([str(o) for o in field.get("options", [])])
                w.setFont(QFont(FONT_UI, 9))
                w.setFixedHeight(30)
                w.setStyleSheet(
                    f"QComboBox {{ background: #000d12; color: {C.TEXT}; "
                    f"border: 1px solid {C.BORDER}; border-radius: 10px; padding: 2px 8px; }}"
                    f"QComboBox QAbstractItemView {{ background: #000d12; color: {C.TEXT}; "
                    f"selection-background-color: {C.PRI_GHO}; }}")
                if stored is not None:
                    w.setCurrentText(str(stored))
            elif ftype == "toggle":
                w = QPushButton()
                w.setCheckable(True)
                w.setChecked(bool(stored))
                w.setFixedHeight(28)
                w.setFont(QFont(FONT_UI, 8, QFont.Weight.Bold))
                w.setCursor(Qt.CursorShape.PointingHandCursor)
                self._style_toggle(w)
                w.toggled.connect(lambda _=False, b=w: self._style_toggle(b))
            else:  # text / password
                w = QLineEdit("" if stored is None else str(stored))
                w.setFont(QFont(FONT_UI, 10))
                w.setFixedHeight(30)
                w.setStyleSheet(self._fs)
                if field.get("placeholder"):
                    w.setPlaceholderText(str(field["placeholder"]))
                if ftype == "password":
                    w.setEchoMode(QLineEdit.EchoMode.Password)

            self._widgets[(ns, key)] = w
            self._types[(ns, key)]   = ftype
            form.addWidget(w)

        # optional test/connect action button + status line
        action = sec.get("action")
        if isinstance(action, dict) and callable(action.get("run")):
            form.addSpacing(2)
            ab = QPushButton(str(action.get("label") or "TEST"))
            ab.setFixedHeight(30)
            ab.setFont(QFont(FONT_UI, 8, QFont.Weight.Bold))
            ab.setCursor(Qt.CursorShape.PointingHandCursor)
            ab.setStyleSheet(f"""
                QPushButton {{ background: #00091a; color: {C.PRI};
                    border: 1px solid {C.PRI_DIM}; border-radius: 10px; }}
                QPushButton:hover {{ background: {C.PRI_GHO}; border-color: {C.PRI}; }}
            """)
            ab.clicked.connect(lambda _=False, n=ns: self._run_action(n))
            form.addWidget(ab)

        status = self._lbl("", 8, color=C.TEXT_DIM)
        self._status_labels[ns] = status
        form.addWidget(status)

        line = QFrame(); line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet(f"color: {C.BORDER}; margin: 4px 0;")
        form.addWidget(line)

    def _style_toggle(self, btn: QPushButton):
        on = btn.isChecked()
        btn.setText("ON" if on else "OFF")
        if on:
            btn.setStyleSheet(f"QPushButton {{ background: {C.PRI_GHO}; color: {C.PRI}; "
                              f"border: 1px solid {C.PRI}; border-radius: 10px; }}")
        else:
            btn.setStyleSheet(f"QPushButton {{ background: transparent; color: {C.TEXT_MED}; "
                              f"border: 1px solid {C.BORDER}; border-radius: 10px; }}")

    # ── data ──────────────────────────────────────────────────────────────────
    def _gather(self, ns: str) -> dict:
        out = {}
        for (n, key), w in self._widgets.items():
            if n != ns:
                continue
            t = self._types.get((n, key), "text")
            if t == "choice":
                out[key] = w.currentText()
            elif t == "toggle":
                out[key] = w.isChecked()
            else:
                out[key] = w.text().strip()
        return out

    def _save_ns(self, ns: str):
        from memory.config_manager import save_plugin_config
        save_plugin_config(ns, self._gather(ns))

    def _save_all(self):
        for sec in self._sections:
            ns = sec.get("namespace") or sec.get("plugin")
            if ns:
                self._save_ns(ns)
                lbl = self._status_labels.get(ns)
                if lbl:
                    lbl.setText("Saved ✓")
                    lbl.setStyleSheet(f"color: {C.PRI}; background: transparent;")

    def _run_action(self, ns: str):
        sec = next((s for s in self._sections
                    if (s.get("namespace") or s.get("plugin")) == ns), None)
        if not sec:
            return
        run_fn = (sec.get("action") or {}).get("run")
        if not callable(run_fn):
            return
        self._save_ns(ns)                 # persist what the user typed before testing
        values = self._gather(ns)
        lbl = self._status_labels.get(ns)
        if lbl:
            lbl.setText("Testing…")
            lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")

        def worker():
            try:
                res = run_fn(values)
                if isinstance(res, tuple) and len(res) == 2:
                    ok, msg = bool(res[0]), str(res[1])
                else:
                    ok, msg = bool(res), str(res)
            except Exception as e:
                ok, msg = False, str(e)
            self._test_done.emit(ns, ok, msg)

        threading.Thread(target=worker, daemon=True).start()

    def _on_test_done(self, ns: str, ok: bool, msg: str):
        lbl = self._status_labels.get(ns)
        if not lbl:
            return
        lbl.setText(msg)
        color = C.PRI if ok else "#ff6b6b"
        lbl.setStyleSheet(f"color: {color}; background: transparent;")


class RemoteKeyOverlay(QWidget):
    """Floating overlay — QR code for instant phone pairing + manual key fallback."""

    closed = pyqtSignal()

    _OW, _OH = 400, 465

    def __init__(self, url: str, key: str, auto_login_url: str = "",
                 manual_url: str = "", expiry_secs: int = 600, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            RemoteKeyOverlay {{
                background: rgba(0, 4, 12, 0.95);
                border: 1px solid {C.BORDER_B};
                border-radius: 14px;
            }}
        """)
        self._expiry          = time.time() + expiry_secs
        self._on_new_key      = None
        self._auto_login_url  = auto_login_url
        self._manual_url      = manual_url or url

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 16, 24, 16)
        lay.setSpacing(5)

        def _lbl(txt, fs=9, bold=False, color=C.PRI,
                 align=Qt.AlignmentFlag.AlignCenter):
            w = QLabel(txt)
            w.setAlignment(align)
            w.setFont(QFont(FONT_UI, fs,
                            QFont.Weight.Bold if bold else QFont.Weight.Normal))
            w.setStyleSheet(f"color: {color}; background: transparent;")
            w.setWordWrap(True)
            return w

        lay.addWidget(_lbl("◈  REMOTE ACCESS", 12, True))
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 1px 0;")
        lay.addWidget(sep)

        # ── QR code ───────────────────────────────────────────────────────────
        self._qr_label = QLabel()
        self._qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._qr_label.setFixedSize(176, 176)
        self._qr_label.setStyleSheet(
            "background: white; border-radius: 14px; padding: 4px;"
        )
        qr_row = QHBoxLayout()
        qr_row.addStretch()
        qr_row.addWidget(self._qr_label)
        qr_row.addStretch()
        lay.addLayout(qr_row)

        self._update_qr(auto_login_url)

        lay.addWidget(_lbl("Scan with phone camera to connect instantly", 8, color=C.TEXT_DIM))

        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"color: {C.BORDER}; margin: 1px 0;")
        lay.addWidget(sep2)

        lay.addWidget(_lbl("Or enter manually:", 7, color=C.TEXT_DIM,
                           align=Qt.AlignmentFlag.AlignLeft))

        self._url_lbl = QLabel(self._manual_url)
        self._url_lbl.setFont(QFont(FONT_UI, 8))
        self._url_lbl.setStyleSheet(f"color: {C.PRI_DIM}; background: transparent;")
        self._url_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._url_lbl.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self._url_lbl)

        self._key_lbl = QLabel(key)
        self._key_lbl.setFont(QFont(FONT_UI, 28, QFont.Weight.Bold))
        self._key_lbl.setStyleSheet(f"""
            color: {C.ACC};
            background: {C.PANEL2};
            border: 1px solid {C.BORDER_B};
            border-radius: 10px;
            padding: 6px 4px;
            letter-spacing: 10px;
        """)
        self._key_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._key_lbl)

        self._timer_lbl = QLabel()
        self._timer_lbl.setFont(QFont(FONT_UI, 8))
        self._timer_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        self._timer_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._timer_lbl)

        btn_row = QHBoxLayout(); btn_row.setSpacing(8)
        new_btn = QPushButton("NEW KEY")
        new_btn.setFixedHeight(32)
        new_btn.setFont(QFont(FONT_UI, 8, QFont.Weight.Bold))
        new_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        new_btn.setStyleSheet(f"""
            QPushButton {{
                background: {C.PANEL}; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 14px;
            }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border: 1px solid {C.PRI}; }}
        """)
        new_btn.clicked.connect(self._refresh_key)
        btn_row.addWidget(new_btn)

        close_btn = QPushButton("DISMISS")
        close_btn.setFixedHeight(32)
        close_btn.setFont(QFont(FONT_UI, 8, QFont.Weight.Bold))
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 14px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
        """)
        close_btn.clicked.connect(self._do_close)
        btn_row.addWidget(close_btn)
        lay.addLayout(btn_row)

        self._ctimer = QTimer(self)
        self._ctimer.timeout.connect(self._tick)
        self._ctimer.start(1000)
        self._tick()

    def set_new_key_callback(self, fn) -> None:
        self._on_new_key = fn

    def _update_qr(self, url: str) -> None:
        if not url:
            self._qr_label.setText("—")
            return
        try:
            import qrcode as _qrmod
            from io import BytesIO
            qr = _qrmod.QRCode(
                box_size=5, border=2,
                error_correction=_qrmod.constants.ERROR_CORRECT_M,
            )
            qr.add_data(url)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            buf = BytesIO()
            img.save(buf, format="PNG")
            px = QPixmap()
            px.loadFromData(buf.getvalue())
            self._qr_label.setPixmap(
                px.scaled(170, 170,
                          Qt.AspectRatioMode.KeepAspectRatio,
                          Qt.TransformationMode.SmoothTransformation)
            )
        except ImportError:
            self._qr_label.setText("pip install\nqrcode[pil]")
            self._qr_label.setFont(QFont(FONT_UI, 8))
            self._qr_label.setStyleSheet(
                "color: #888; background: white; border-radius: 14px; padding: 4px;"
            )
        except Exception:
            self._qr_label.setText(url[:28])
            self._qr_label.setFont(QFont(FONT_UI, 7))
            self._qr_label.setStyleSheet(
                f"color: {C.PRI}; background: white; border-radius: 14px; padding: 4px;"
            )

    def _tick(self):
        remaining = max(0, int(self._expiry - time.time()))
        m, s = divmod(remaining, 60)
        self._timer_lbl.setText(f"Key expires in  {m:02d}:{s:02d}")
        if remaining == 0:
            self._do_close()

    def mark_connected(self) -> None:
        """Call from any thread when a phone successfully connects."""
        self._ctimer.stop()
        self._key_lbl.setText("CONNECTED")
        self._key_lbl.setStyleSheet(f"""
            color: {C.GREEN};
            background: rgba(34,197,94,0.08);
            border: 2px solid rgba(34,197,94,0.4);
            border-radius: 10px;
            padding: 6px 4px;
            letter-spacing: 4px;
        """)
        self._qr_label.setText("✓")
        self._qr_label.setFont(QFont(FONT_UI, 54, QFont.Weight.Bold))
        self._qr_label.setStyleSheet(
            "color: #2fd6b0; background: #07201c; border-radius: 14px;"
        )
        self._timer_lbl.setText("Phone connected — JARVIS ready")
        self._timer_lbl.setStyleSheet(f"color: {C.GREEN}; background: transparent;")

    def _refresh_key(self):
        if self._on_new_key:
            result = self._on_new_key()
            if result:
                url    = result[0]
                key    = result[1]
                auto   = result[2] if len(result) >= 3 else ""
                manual = result[3] if len(result) >= 4 else url
                self._manual_url     = manual or url
                self._url_lbl.setText(self._manual_url)
                self._key_lbl.setText(key)
                self._auto_login_url = auto
                self._update_qr(auto or url)
                self._expiry = time.time() + 600
                self._key_lbl.setStyleSheet(f"""
                    color: {C.ACC};
                    background: {C.PANEL2};
                    border: 1px solid {C.BORDER_B};
                    border-radius: 10px;
                    padding: 6px 4px;
                    letter-spacing: 10px;
                """)
                self._timer_lbl.setStyleSheet(
                    f"color: {C.TEXT_MED}; background: transparent;"
                )
                self._ctimer.start(1000)
                self._tick()

    def _do_close(self):
        self._ctimer.stop()
        self.hide()
        self.closed.emit()


class ConversationView(QWidget):
    """Lightweight chat renderer for the Home screen.

    JARVIS's engine already streams every meaningful line to the UI log
    (SYS:/ERR:/You:/JARVIS: prefixed), so this is a *view* on that same stream
    rather than a parallel transcript that could drift out of sync. It classifies
    each line, renders it as a compact chat bubble, and keeps a bounded buffer so
    a long session cannot grow memory without end.
    """

    _MAX_BLOCKS = 600

    def __init__(self, assistant_name: str = "JARVIS", parent=None):
        super().__init__(parent)
        self._ai = (assistant_name or "JARVIS").upper()
        self._blocks: "collections.deque[str]" = collections.deque(maxlen=self._MAX_BLOCKS)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self._view = QTextEdit()
        self._view.setReadOnly(True)
        self._view.setFrameShape(QFrame.Shape.NoFrame)
        self._view.setAcceptRichText(True)
        self._view.setStyleSheet(f"""
            QTextEdit {{
                background: {C.BG}; border: none;
                color: {C.TEXT}; font-family: "Segoe UI", Arial; font-size: 9pt;
                padding: 8px 12px;
            }}
        """)
        lay.addWidget(self._view)

    @staticmethod
    def _esc(s) -> str:
        return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace("\n", "<br>"))

    def _classify(self, line: str):
        """(role, text) where role ∈ user | ai | sys | err."""
        s = line.strip()
        low = s.lower()
        if low.startswith(("you:", "user:")):
            return "user", s.split(":", 1)[1].strip()
        if low.startswith("err:"):
            return "err", s.split(":", 1)[1].strip()
        if low.startswith("sys:"):
            return "sys", s.split(":", 1)[1].strip()
        head, _, rest = s.partition(":")
        if rest and head.strip().upper() == self._ai:
            return "ai", rest.strip()
        return "sys", s

    def set_assistant_name(self, name: str) -> None:
        self._ai = (name or "JARVIS").upper()

    def append_log(self, line: str) -> None:
        if not line or not line.strip():
            return
        role, text = self._classify(line)
        if role == "user":
            html = (f'<div style="margin:5px 0; text-align:right;">'
                    f'<span style="background:{C.PRI_GHO}; color:{C.WHITE};'
                    f' border:1px solid {C.PRI_DIM}; border-radius:14px;'
                    f' padding:7px 12px; display:inline-block; max-width:76%;">'
                    f'{self._esc(text)}</span></div>')
        elif role == "ai":
            html = (f'<div style="margin:5px 0;">'
                    f'<span style="background:{C.PANEL2}; color:{C.TEXT};'
                    f' border:1px solid {C.BORDER}; border-radius:14px;'
                    f' padding:7px 12px; display:inline-block; max-width:76%;">'
                    f'{self._esc(text)}</span></div>')
        elif role == "err":
            html = (f'<div style="margin:4px 0; color:#ff8899; font-size:8pt;">'
                    f'<span>\u26A0 {self._esc(text)}</span></div>')
        else:
            html = (f'<div style="margin:3px 0; color:{C.TEXT_DIM}; font-size:8pt;">'
                    f'<span>\u00B7 {self._esc(text)}</span></div>')
        self._blocks.append(html)
        if len(self._blocks) == self._MAX_BLOCKS:
            # Full: drop the oldest half in one go instead of leaking forever.
            keep = list(self._blocks)[self._MAX_BLOCKS // 2:]
            self._blocks.clear()
            self._blocks.extend(keep)
            self._view.setText("")
        doc = self._view.document()
        cur = self._view.textCursor()
        cur.movePosition(cur.MoveOperation.End)
        cur.insertHtml(html)
        bar = self._view.verticalScrollBar()
        bar.setValue(bar.maximum())

    def clear(self) -> None:
        self._blocks.clear()
        self._view.clear()


class _SummonHotkey:
    """Global Win+Shift+J hotkey via ctypes — summons/activates JARVIS even when
    the window is hidden. Pure ctypes, no extra dependency; safely a no-op on
    non-Windows platforms or if registration fails."""

    WM_HOTKEY = 0x0312
    _ID = 0x4A52   # "JR"

    def __init__(self):
        self._native = None
        self._ok = False
        if sys.platform != "win32":
            return
        try:
            from ctypes import windll, wintypes
            self._native = windll.user32
            MOD_WIN = 0x0008
            MOD_SHIFT = 0x0004
            vk_j = 0x4A
            self._ok = bool(self._native.RegisterHotKey(None, self._ID,
                                                        MOD_WIN | MOD_SHIFT, vk_j))
        except Exception:
            self._ok = False

    def attach(self, app, on_trigger) -> bool:
        if not self._ok:
            return False
        self._on_trigger = on_trigger
        import ctypes
        self._filter = _NativeHotkeyFilter(self, on_trigger)
        app.installNativeEventFilter(self._filter)
        return True

    def release(self):
        if self._native:
            self._native.UnregisterHotKey(None, self._ID)
            self._native = None
        if getattr(self, "_filter", None) is not None:
            try:
                QApplication.instance().removeNativeEventFilter(self._filter)
            except Exception:
                pass
            self._filter = None
        self._ok = False


class _NativeHotkeyFilter(QAbstractNativeEventFilter):
    def __init__(self, owner, on_trigger):
        super().__init__()
        self._owner = owner
        self._on = on_trigger

    def nativeEventFilter(self, etype, message):  # noqa: N802 (Qt signature)
        try:
            import ctypes
            if etype.typeName == b"windowsdispatchermsg" and message:
                msg = ctypes.wintypes.MSG.from_address(int(message))
                if msg.message == _SummonHotkey.WM_HOTKEY:
                    if int(msg.wParam) == _SummonHotkey._ID and self._on:
                        self._on()
                    return True, 0
        except Exception:
            pass
        return False, 0


class MainWindow(QMainWindow):
    _log_sig        = pyqtSignal(str)
    _state_sig      = pyqtSignal(str)
    _content_sig    = pyqtSignal(str, str)   # (title, text) — thread-safe content display
    _reconfig_sig   = pyqtSignal()           # trigger setup overlay from any thread
    _camera_sig     = pyqtSignal(bytes)      # show camera frame preview (small overlay)
    _cam_stream_sig = pyqtSignal(bool)       # True=start live stream, False=stop
    _cam_frame_sig  = pyqtSignal(bytes)      # live camera frame → HUD area
    _clipboard_sig  = pyqtSignal(str)        # clipboard text changed (thread-safe)
    _confirm_sig    = pyqtSignal(str, str)   # (title, detail) — irreversible-action gate
    _confirm_hide_sig = pyqtSignal()
    _wake_dl_sig    = pyqtSignal(bool, str)  # wake-word install finished (ok, message)
    _quiz_sig       = pyqtSignal(str, object, object)  # (topic, questions, grader)
    _quiz_hide_sig  = pyqtSignal()
    _review_sig     = pyqtSignal(str, str, object, object)  # document review payload
    _screen_status_sig = pyqtSignal(bool, str)  # Screen Awareness chip (active, caption)

    def __init__(self, face_path: str):
        super().__init__()
        self._face_path = face_path

        # App icon (taskbar, title bar, Alt-Tab) — same asset the desktop
        # shortcut uses; a missing icon just keeps the Qt default.
        try:
            from pathlib import Path as _Path
            _ico = _Path(__file__).resolve().parent / "assets" / "jarvis.ico"
            if _ico.exists():
                self.setWindowIcon(QIcon(str(_ico)))
        except Exception:
            pass

        # Load customization from config
        _cfg = _read_full_config()
        self._assistant_name: str = (_cfg.get("assistant_name") or "JARVIS").strip()
        _display = self._assistant_name.upper()

        # Apply the saved UI colour BEFORE panels/stylesheets are built.
        # The ICE redesign is built on the blue/cyan default; a stale green/
        # teal accent saved by an older Mark would hue-shift the whole theme
        # back toward the old look, so only near-blue/cyan/violet accents are
        # honoured — anything else falls back to the default identity colour.
        _ui_color = (_cfg.get("ui_color") or "").strip()
        if _ui_color and _ui_color.lower() != DEFAULT_UI_COLOR:
            if not _accent_in_family(_ui_color):
                _ui_color = ""
            else:
                apply_ui_accent(_ui_color)

        self.setWindowTitle(f"{_display} — {APP_VERSION}")
        self.setMinimumSize(_MIN_W, _MIN_H)
        self.resize(_DEFAULT_W, _DEFAULT_H)

        screen = QApplication.primaryScreen().availableGeometry()
        self.move(
            (screen.width()  - _DEFAULT_W) // 2,
            (screen.height() - _DEFAULT_H) // 2,
        )

        self.on_text_command   = None
        self.on_remote_clicked = None   # callable: () -> (url, key) | None
        self.on_interrupt      = None   # callable: () -> None — stop JARVIS mid-speech
        self.on_voice_change   = None   # callable: () -> None — rebuild session with new voice
        self.on_audio_device_change = None  # callable: () -> None — reopen audio streams
        self._confirm_overlay  = None   # live ConfirmBanner, if one is on screen
        self.get_plugins       = None   # callable: () -> list[dict], set by JarvisLive
        self.get_plugin_settings = None # callable: () -> list[dict] settings schemas, set by JarvisLive
        self.on_wake_toggle    = None   # callable: (enable: bool) -> str, set by JarvisLive
        self.on_wake_manual    = None   # callable: () -> None — manual sleep/wake
        self.on_push_to_talk   = None   # callable: (enable: bool) -> str scope
        self.ptt_hold          = None   # callable: (held: bool) -> None — windowed chord
        self.wake_get_state    = None   # callable: () -> dict {enabled, awake, ready}
        self._muted            = False
        self._current_file: str | None = None
        self._remote_overlay: RemoteKeyOverlay | None = None
        self._customize_overlay: CustomizeOverlay | None = None
        self._api_keys_overlay: ApiKeysOverlay | None = None
        self._api_keys_thread: threading.Thread | None = None

        central = QWidget()
        central.setStyleSheet(f"background: {C.BG};")
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_header())

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        # ── Core face + content surfaces (kept for the whole session) ────────
        self.hud = HudCanvas(face_path, _display)
        self.hud.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.hud.setMinimumSize(120, 120)
        self._content_panel = self._build_content_panel()
        self._quiz_panel = self._build_quiz_panel()

        # Live camera container — replaces HUD when camera stream is active
        _cam_cont = QWidget()
        _cam_cont.setStyleSheet("background: #000308;")
        _cam_v = QVBoxLayout(_cam_cont)
        _cam_v.setContentsMargins(0, 0, 0, 0)
        _cam_v.setSpacing(0)
        _cam_hdr = QHBoxLayout()
        _cam_hdr.setContentsMargins(8, 5, 8, 5)
        _cam_title = QLabel("◈  CAMERA FEED")
        _cam_title.setFont(QFont(FONT_UI, 8, QFont.Weight.Bold))
        _cam_title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        _cam_hdr.addWidget(_cam_title)
        _cam_hdr.addStretch()
        _cam_x = QPushButton("✕  CLOSE")
        _cam_x.setFont(QFont(FONT_UI, 8, QFont.Weight.Bold))
        _cam_x.setCursor(Qt.CursorShape.PointingHandCursor)
        _cam_x.setStyleSheet(f"""
            QPushButton {{
                color: {C.TEXT_DIM}; background: transparent;
                border: none; padding: 2px 6px;
            }}
            QPushButton:hover {{ color: {C.PRI}; }}
        """)
        _cam_x.clicked.connect(self.stop_camera_stream)
        _cam_hdr.addWidget(_cam_x)
        _cam_v.addLayout(_cam_hdr)
        self._cam_live_lbl = QLabel()
        self._cam_live_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._cam_live_lbl.setStyleSheet("background: transparent;")
        self._cam_live_lbl.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        _cam_v.addWidget(self._cam_live_lbl, stretch=1)

        # Stack: 0 = animated HUD, 1 = live camera
        self._hud_cam_stack = QStackedWidget()
        self._hud_cam_stack.addWidget(self.hud)
        self._hud_cam_stack.addWidget(_cam_cont)

        # Clean two-view workspace:  Home  ⇄  Settings.
        self._app_stack = QStackedWidget()
        self._app_stack.addWidget(self._build_home_page())      # 0 = Home
        self._app_stack.addWidget(self._build_settings_page())  # 1 = Settings
        self._app_stack.setCurrentIndex(0)
        root.addWidget(self._app_stack, stretch=1)

        root.addWidget(self._build_footer())

        # Drawer-less settings pages still own the talk/wake/hud buttons.
        self._refresh_wake_btns()
        self._refresh_talk_btns()
        self._refresh_hud_btn()
        self._update_autostart_btn(self._check_autostart())
        from memory.config_manager import get_brief_enabled as _gbe
        self._update_brief_btn(_gbe())

        self._clock_tmr = QTimer(self)
        self._clock_tmr.timeout.connect(self._tick_clock)
        self._clock_tmr.start(1000)
        self._tick_clock()

        # Metric update timer
        self._metric_tmr = QTimer(self)
        self._metric_tmr.timeout.connect(self._update_metrics)
        self._metric_tmr.start(2000)
        self._update_metrics()

        self._log_sig.connect(self._log.append_log)
        self._state_sig.connect(self._apply_state)
        self._content_sig.connect(self._show_content)
        self._reconfig_sig.connect(self._show_setup)
        self._camera_sig.connect(self._show_camera_frame)
        self._confirm_sig.connect(self._show_confirm_banner)
        self._confirm_hide_sig.connect(self._hide_confirm_banner)
        self._cam_stream_sig.connect(self._on_cam_stream)
        self._cam_frame_sig.connect(self._on_cam_frame)
        self._clipboard_sig.connect(self._show_clipboard_panel)
        self._wake_dl_sig.connect(self._on_wake_install_done)
        self._quiz_sig.connect(self._show_quiz)
        self._quiz_hide_sig.connect(self._hide_quiz)
        self._review_sig.connect(self._show_review)
        self._screen_status_sig.connect(self.set_screen_status)
        self._cam_stop = threading.Event()

        # Camera preview overlay (child of central widget, positioned in resizeEvent)
        self._cam_preview = _CameraPreview(self.centralWidget())

        # Clipboard panel (child of central widget, bottom-center)
        self._clipboard_panel = ClipboardPanel(self.centralWidget())
        self._clipboard_panel.action_requested.connect(self._on_clipboard_action)
        QApplication.clipboard().dataChanged.connect(self._on_clipboard_changed)

        self._overlay: SetupOverlay | None = None
        self._ready = self._check_config()
        if not self._ready:
            self._show_setup()

        sc_mute = QShortcut(QKeySequence("F4"), self)
        sc_mute.activated.connect(self._toggle_mute)
        sc_full = QShortcut(QKeySequence("F11"), self)
        sc_full.activated.connect(self._toggle_fullscreen)
        sc_intr = QShortcut(QKeySequence("Escape"), self)
        sc_intr.activated.connect(self._do_interrupt)

        # System tray (spec §64-65): close → hide to tray, tray menu to open /
        # mute / quit. ESC emergency-stop above already stops mid-speech.
        self._tray_quit = False
        self._tray_notified = False
        self._init_tray()

        # Global summon hotkey (Win+Shift+J) — low-end friendly: never polls,
        # pure ctypes, silently off if registration fails.
        self._summon_hotkey = _SummonHotkey()
        self._summon_hotkey.attach(QApplication.instance(), self._summon)

    def _init_tray(self) -> None:
        """Create the system-tray icon + menu (no-op when a tray is unavailable).

        Window close hides to tray instead of quitting; the tray menu provides
        Open / Mute / Quit. Deliberately builds nothing fancy — a Qt tray does
        not belong on a machine without a system tray.
        """
        self._tray: QSystemTrayIcon | None = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        try:
            ico_path = Path(__file__).resolve().parent / "config" / "jarvis.ico"
            if not ico_path.exists():
                self._build_jarvis_icon(ico_path)
            icon = QIcon(str(ico_path)) if ico_path.exists() else QIcon()
            if icon.isNull():
                return

            menu = QMenu()
            act_open = menu.addAction("Open J.A.R.V.I.S")
            act_open.triggered.connect(self._show_from_tray)
            act_mute = menu.addAction("Mute microphone")
            act_mute.triggered.connect(lambda: (self._toggle_mute(),
                                                self._update_tray_menu(menu)))
            act_bg = menu.addAction("Background mode  (Win+Shift+J summons)")
            act_bg.triggered.connect(self._enter_background)
            menu.addSeparator()
            act_quit = menu.addAction("Quit")
            act_quit.triggered.connect(self._on_tray_quit)

            tray = QSystemTrayIcon(icon, self)
            tray.setToolTip(f"{self._assistant_name.upper()} — {APP_VERSION}")
            tray.setContextMenu(menu)
            tray.activated.connect(self._on_tray_activated)
            tray.show()
            self._tray = tray
        except Exception as e:
            print(f"[Tray] Disabled: {e}")
            self._tray = None

    def _update_tray_menu(self, menu: QMenu) -> None:
        """Keep the tray's mute label in sync after toggling."""
        for act in menu.actions():
            if act.text().startswith(("Mute", "Unmute")):
                act.setText("Unmute microphone" if self._muted else "Mute microphone")

    def _on_tray_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                      QSystemTrayIcon.ActivationReason.DoubleClick):
            self._show_from_tray()

    def _show_from_tray(self) -> None:
        self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()

    def _summon(self) -> None:
        """Global hotkey handler — summon the window to the front, even from
        background mode, and start where the user left off."""
        self._show_from_tray()
        self._set_hud_paused(False)

    def _on_tray_quit(self) -> None:
        self._tray_quit = True
        if self._tray:
            self._tray.hide()
        try:
            self._summon_hotkey.release()
        except Exception:
            pass
        QApplication.quit()

    def closeEvent(self, e):
        """Close hides to the system tray instead of quitting (if a tray exists).
        Quit requests (tray menu, [shutdown_jarvis] hard-exit) still quit."""
        if self._tray is None or self._tray_quit:
            e.accept()
            QApplication.quit()
            return
        e.ignore()
        self.hide()
        if not self._tray_notified:
            self._tray_notified = True
            self._tray.showMessage(
                self._assistant_name.upper(),
                "Still running — I'll be here in the system tray. "
                "Use the tray icon to reopen me, or F4 to mute.",
                QSystemTrayIcon.MessageIcon.Information, 4000,
            )

    def changeEvent(self, e):
        """Pause the paint engine + UI timers whenever the window drops below
        full visibility (hidden to tray, or iconified) — the single biggest
        background-CPU saving available on a low-end machine."""
        super().changeEvent(e)
        if e.type() != e.Type.WindowStateChange:
            return
        try:
            if self.isMinimized():
                self._set_hud_paused(True)
            elif not self.isHidden():
                self._set_hud_paused(False)
        except Exception:
            pass

    def hideEvent(self, e):
        super().hideEvent(e)
        self._set_hud_paused(True)

    def showEvent(self, e):
        super().showEvent(e)
        try:
            self._set_hud_paused(False)
        except Exception:
            pass

    def _show_camera_frame(self, img_bytes: bytes):
        """Slot — display camera preview overlay (main thread)."""
        self._cam_preview.show_frame(img_bytes)
        cw = self.centralWidget()
        pw = _CameraPreview._W
        ph = self._cam_preview.height()
        self._cam_preview.setGeometry(
            cw.width() - pw - 12,
            cw.height() - ph - 28,
            pw, ph,
        )

    # --- Live camera stream in HUD area ------------------------------------
    def _on_cam_stream(self, start: bool) -> None:
        if start:
            self._hud_cam_stack.setCurrentIndex(1)
        else:
            self._hud_cam_stack.setCurrentIndex(0)
            self._cam_live_lbl.clear()

    def _on_cam_frame(self, data: bytes) -> None:
        px = QPixmap()
        px.loadFromData(data)
        if not px.isNull():
            w, h = self._cam_live_lbl.width(), self._cam_live_lbl.height()
            if w > 1 and h > 1:
                self._cam_live_lbl.setPixmap(
                    px.scaled(w, h,
                              Qt.AspectRatioMode.KeepAspectRatio,
                              Qt.TransformationMode.SmoothTransformation)
                )

    def start_camera_stream(self) -> None:
        self._cam_stop.clear()
        self._cam_stream_sig.emit(True)
        t = threading.Thread(target=self._cam_loop, daemon=True, name="cam-stream")
        t.start()

    def _cam_loop(self) -> None:
        try:
            import cv2
            # Reuse camera index detected by screen_processor (cached in api_keys.json)
            cam_idx = 0
            try:
                import json as _j
                cfg = _j.loads((CONFIG_DIR / "api_keys.json").read_text())
                cam_idx = int(cfg.get("camera_index", 0))
            except Exception:
                pass
            try:
                backend = cv2.CAP_DSHOW if _OS == "Windows" else cv2.CAP_ANY
            except AttributeError:
                backend = 0
            cap = cv2.VideoCapture(cam_idx, backend)
            if not cap.isOpened():
                cap = cv2.VideoCapture(0)
            if not cap.isOpened():
                return
            # warm-up frames
            for _ in range(5):
                cap.read()
            while not self._cam_stop.wait(0.033) and cap.isOpened():
                ret, frame = cap.read()
                if ret and frame is not None:
                    _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 65])
                    self._cam_frame_sig.emit(buf.tobytes())
            cap.release()
        except Exception as e:
            print(f"[Camera] Stream error: {e}")
        finally:
            self._cam_stream_sig.emit(False)

    def stop_camera_stream(self) -> None:
        self._cam_stop.set()

    # ------------------------------------------------------------------
    # Icon generation — arc-reactor style, rendered with Pillow
    # ------------------------------------------------------------------
    @staticmethod
    def _build_jarvis_icon(out_path: Path) -> bool:
        """
        Render a JARVIS arc-reactor icon at 4× resolution and downsample
        for crisp results at all sizes. Saves a multi-res .ico to out_path.
        Returns True on success.
        """
        try:
            import math
            import PIL.Image
            import PIL.ImageDraw
            import PIL.ImageFilter
        except ImportError:
            return False

        CYAN   = (0, 212, 255)
        DIM    = (0, 100, 140)
        DARK   = (0, 6, 10)
        GLOW   = (0, 160, 200)
        WHITE  = (220, 240, 255)

        def _render(sz: int) -> PIL.Image.Image:
            S  = sz * 4                     # draw at 4× then downscale
            img = PIL.Image.new("RGBA", (S, S), (0, 0, 0, 0))
            d   = PIL.ImageDraw.Draw(img)
            cx = cy = S // 2

            # ── filled background circle ──────────────────────────────────
            R = S // 2 - 2
            d.ellipse([cx-R, cy-R, cx+R, cy+R], fill=(*DARK, 255))

            # ── outer border ring ─────────────────────────────────────────
            lw = max(2, S // 40)
            d.ellipse([cx-R, cy-R, cx+R, cy+R],
                      outline=(*CYAN, 220), width=lw)

            # ── mid decorative ring ───────────────────────────────────────
            R2 = int(R * 0.72)
            d.ellipse([cx-R2, cy-R2, cx+R2, cy+R2],
                      outline=(*DIM, 180), width=max(1, lw // 2))

            # ── 6 radial spokes (hex bolt) ────────────────────────────────
            R_inner = int(R * 0.30)
            R_outer = int(R * 0.62)
            spoke_w = max(1, S // 80)
            for i in range(6):
                angle = math.radians(i * 60 - 30)
                x1 = cx + int(R_inner * math.cos(angle))
                y1 = cy + int(R_inner * math.sin(angle))
                x2 = cx + int(R_outer * math.cos(angle))
                y2 = cy + int(R_outer * math.sin(angle))
                d.line([x1, y1, x2, y2], fill=(*GLOW, 200), width=spoke_w)

            # ── 6 tick marks on outer ring ────────────────────────────────
            for i in range(6):
                angle = math.radians(i * 60)
                for dr in range(lw * 2):
                    rx = (R - lw - dr)
                    d.point(
                        [cx + int(rx * math.cos(angle)),
                         cy + int(rx * math.sin(angle))],
                        fill=(*WHITE, 220),
                    )

            # ── inner glowing ring ────────────────────────────────────────
            Ri = int(R * 0.26)
            d.ellipse([cx-Ri, cy-Ri, cx+Ri, cy+Ri],
                      outline=(*CYAN, 255), width=max(2, lw))

            # ── bright glow soft blur applied before core ─────────────────
            # (draw a slightly larger cyan circle on a separate layer)
            glow_layer = PIL.Image.new("RGBA", (S, S), (0, 0, 0, 0))
            gd = PIL.ImageDraw.Draw(glow_layer)
            Rc = int(R * 0.13)
            gd.ellipse([cx-Rc*2, cy-Rc*2, cx+Rc*2, cy+Rc*2],
                       fill=(*CYAN, 110))
            glow_layer = glow_layer.filter(PIL.ImageFilter.GaussianBlur(S // 14))
            img = PIL.Image.alpha_composite(img, glow_layer)
            d   = PIL.ImageDraw.Draw(img)

            # ── core dot ──────────────────────────────────────────────────
            d.ellipse([cx-Rc, cy-Rc, cx+Rc, cy+Rc], fill=(*WHITE, 255))

            # ── downscale to target size ──────────────────────────────────
            return img.resize((sz, sz), PIL.Image.LANCZOS)

        try:
            sizes  = [256, 128, 64, 48, 32, 16]
            frames = [_render(s) for s in sizes]
            frames[0].save(
                out_path,
                format="ICO",
                append_images=frames[1:],
                sizes=[(s, s) for s in sizes],
            )
            return True
        except Exception as e:
            print(f"[Shortcut] ⚠️  Icon generation failed: {e}")
            return False

    @staticmethod
    def _create_lnk_windows(lnk: str, target: str, args: str,
                             work_dir: str, icon_loc: str) -> None:
        """
        Create a Windows .lnk shortcut WITHOUT launching PowerShell or cmd.
        Tries win32com (pywin32) first; falls back to wscript.exe + VBScript.
        wscript.exe is a GUI-mode host — it never opens a console window.
        """
        # ── Option 1: pywin32 (pure Python COM, zero subprocess) ──────────
        try:
            from win32com.client import Dispatch   # type: ignore
            sh = Dispatch("WScript.Shell")
            sc = sh.CreateShortCut(lnk)
            sc.TargetPath       = target
            sc.Arguments        = f'"{args}"'
            sc.WorkingDirectory = work_dir
            sc.Description      = "J.A.R.V.I.S AI Assistant"
            sc.IconLocation     = icon_loc
            sc.save()
            return
        except ImportError:
            pass

        # ── Option 2: wscript.exe + VBScript (always available on Windows,
        #    GUI-mode executable — never opens a console window) ────────────
        vbs = "\n".join([
            'Set ws = CreateObject("WScript.Shell")',
            f'Set sc = ws.CreateShortcut("{lnk}")',
            f'sc.TargetPath = "{target}"',
            f'sc.Arguments = Chr(34) & "{args}" & Chr(34)',
            f'sc.WorkingDirectory = "{work_dir}"',
            'sc.Description = "J.A.R.V.I.S AI Assistant"',
            f'sc.IconLocation = "{icon_loc}"',
            'sc.Save',
        ])
        import tempfile
        fd, tmp = tempfile.mkstemp(suffix=".vbs")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(vbs)
            proc = subprocess.Popen(
                ["wscript.exe", "/nologo", tmp],
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW,
            )
            proc.wait(timeout=10)
        finally:
            try:
                os.unlink(tmp)
            except Exception:
                pass

    @staticmethod
    def _get_desktop_dir() -> Path:
        """
        Resolve the user's REAL desktop directory instead of assuming
        ~/Desktop, which breaks when:
          • OneDrive "Known Folder Move" relocates the desktop
            (C:/Users/x/OneDrive/Desktop) — very common on Win 10/11;
          • the XDG desktop is localized on Linux (~/Masaüstü,
            ~/Schreibtisch, ~/Bureau, …).
        Falls back to ~/Desktop only as a last resort.
        """
        home = Path.home()
        _os = platform.system()

        if _os == "Windows":
            # ── 1) SHGetKnownFolderPath(FOLDERID_Desktop) — the canonical
            #       answer; follows OneDrive redirection. No dependencies. ──
            try:
                import ctypes
                from ctypes import wintypes

                class _GUID(ctypes.Structure):
                    _fields_ = [("Data1", wintypes.DWORD),
                                ("Data2", wintypes.WORD),
                                ("Data3", wintypes.WORD),
                                ("Data4", ctypes.c_ubyte * 8)]

                # FOLDERID_Desktop {B4BFCC3A-DB2C-424C-B029-7FE99A87C641}
                fid = _GUID(0xB4BFCC3A, 0xDB2C, 0x424C,
                            (ctypes.c_ubyte * 8)(0xB0, 0x29, 0x7F, 0xE9,
                                                 0x9A, 0x87, 0xC6, 0x41))
                buf = ctypes.c_wchar_p()
                if ctypes.windll.shell32.SHGetKnownFolderPath(
                        ctypes.byref(fid), 0, None, ctypes.byref(buf)) == 0:
                    p = Path(buf.value)
                    ctypes.windll.ole32.CoTaskMemFree(buf)
                    if p.is_dir():
                        return p
            except Exception:
                pass

            # ── 2) Registry: User Shell Folders (may contain %VARS%) ──────
            try:
                import winreg
                with winreg.OpenKey(
                        winreg.HKEY_CURRENT_USER,
                        r"Software\Microsoft\Windows\CurrentVersion"
                        r"\Explorer\User Shell Folders") as key:
                    val, _t = winreg.QueryValueEx(key, "Desktop")
                p = Path(os.path.expandvars(val))
                if p.is_dir():
                    return p
            except Exception:
                pass

        elif _os == "Linux":
            # ── xdg-user-dir honours localized names (~/Masaüstü, …) ──────
            try:
                out = subprocess.run(["xdg-user-dir", "DESKTOP"],
                                     capture_output=True, text=True, timeout=5)
                p = Path(out.stdout.strip())
                if out.stdout.strip() and p != home and p.is_dir():
                    return p
            except Exception:
                pass
            try:
                cfg = home / ".config" / "user-dirs.dirs"
                for line in cfg.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line.startswith("XDG_DESKTOP_DIR"):
                        val = line.split("=", 1)[1].strip().strip('"')
                        p = Path(val.replace("$HOME", str(home)))
                        if p != home and p.is_dir():
                            return p
            except Exception:
                pass

        # macOS: ~/Desktop is always the real path (localization is
        # display-only). Everything else lands here as a last resort.
        return home / "Desktop"

    def _create_desktop_shortcut(self):
        """
        Create a desktop shortcut on Windows / macOS / Linux.
        Never opens a terminal, console, or PowerShell window on any platform.
        """
        import stat as _stat
        script  = Path(__file__).resolve().parent / "main.py"
        python  = Path(sys.executable)
        desktop = self._get_desktop_dir()

        # Arc-reactor icon (.ico — also exported as .png for Linux/macOS)
        ico_path = Path(__file__).resolve().parent / "config" / "jarvis.ico"
        if not ico_path.exists():
            self._build_jarvis_icon(ico_path)

        try:
            _os = platform.system()

            # ── Windows ───────────────────────────────────────────────────────
            if _os == "Windows":
                pythonw  = python.parent / "pythonw.exe"
                target   = str(pythonw if pythonw.exists() else python)
                lnk      = str(desktop / "J.A.R.V.I.S.lnk")
                icon_loc = str(ico_path) if ico_path.exists() else f"{target},0"
                self._create_lnk_windows(lnk, target, str(script),
                                         str(script.parent), icon_loc)

            # ── macOS — proper .app bundle (no Terminal window) ───────────────
            elif _os == "Darwin":
                app     = desktop / "J.A.R.V.I.S.app"
                mac_dir = app / "Contents" / "MacOS"
                res_dir = app / "Contents" / "Resources"
                mac_dir.mkdir(parents=True, exist_ok=True)
                res_dir.mkdir(exist_ok=True)

                # Launcher executable (bash — runs as background process,
                # macOS does NOT open Terminal for executables inside .app bundles)
                launcher = mac_dir / "JARVIS"
                launcher.write_text(
                    "#!/usr/bin/env bash\n"
                    f'cd "{script.parent}"\n'
                    f'exec "{python}" "{script}"\n'
                )
                launcher.chmod(launcher.stat().st_mode
                               | _stat.S_IEXEC | _stat.S_IXGRP | _stat.S_IXOTH)

                # Minimal Info.plist (required for .app recognition)
                (app / "Contents" / "Info.plist").write_text(
                    '<?xml version="1.0" encoding="UTF-8"?>\n'
                    '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
                    '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                    '<plist version="1.0"><dict>\n'
                    '  <key>CFBundleExecutable</key><string>JARVIS</string>\n'
                    '  <key>CFBundleIdentifier</key>'
                    '<string>com.jarvis.assistant</string>\n'
                    '  <key>CFBundleName</key><string>J.A.R.V.I.S</string>\n'
                    '  <key>CFBundlePackageType</key><string>APPL</string>\n'
                    '  <key>CFBundleVersion</key><string>1.0</string>\n'
                    '</dict></plist>\n'
                )

                # Optional: copy icon as .icns (skip silently if Pillow is missing)
                try:
                    import PIL.Image
                    icns = res_dir / "AppIcon.icns"
                    PIL.Image.open(ico_path).save(icns, format="ICNS")
                    # Inject icon reference into plist
                    plist = app / "Contents" / "Info.plist"
                    txt = plist.read_text()
                    plist.write_text(
                        txt.replace(
                            '</dict></plist>',
                            '  <key>CFBundleIconFile</key>'
                            '<string>AppIcon</string>\n</dict></plist>\n',
                        )
                    )
                except Exception:
                    pass  # icon is optional

            # ── Linux — .desktop file (Terminal=false, no console) ────────────
            else:
                # Export .ico → .png for better desktop integration
                png_path = ico_path.with_suffix(".png")
                if not png_path.exists() and ico_path.exists():
                    try:
                        import PIL.Image
                        PIL.Image.open(ico_path).resize(
                            (256, 256), PIL.Image.LANCZOS
                        ).save(png_path, format="PNG")
                    except Exception:
                        png_path = ico_path  # fallback to .ico

                icon_line = f"Icon={png_path}\n" if png_path.exists() else ""
                desk = desktop / "J.A.R.V.I.S.desktop"
                desk.write_text(
                    "[Desktop Entry]\n"
                    "Name=J.A.R.V.I.S\n"
                    f"Exec={python} {script}\n"
                    f"Path={script.parent}\n"
                    "Type=Application\n"
                    "Terminal=false\n"
                    "Categories=Utility;\n"
                    + icon_line
                )
                desk.chmod(desk.stat().st_mode | 0o755)

            self._log.append_log("SYS: Desktop shortcut created.")
        except Exception as e:
            self._log.append_log(f"ERR: Shortcut failed — {e}")

    def _toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        cw = self.centralWidget()
        if self._overlay and self._overlay.isVisible():
            ow, oh = 460, 390
            self._overlay.setGeometry(
                (cw.width()  - ow) // 2,
                (cw.height() - oh) // 2,
                ow, oh,
            )
        if self._remote_overlay and self._remote_overlay.isVisible():
            ow, oh = RemoteKeyOverlay._OW, RemoteKeyOverlay._OH
            self._remote_overlay.setGeometry(
                (cw.width()  - ow) // 2,
                (cw.height() - oh) // 2,
                ow, oh,
            )
        if self._customize_overlay and self._customize_overlay.isVisible():
            ow, oh = CustomizeOverlay._OW, CustomizeOverlay._OH
            oh = min(oh, cw.height() - 16)
            self._customize_overlay.setGeometry(
                (cw.width()  - ow) // 2,
                (cw.height() - oh) // 2,
                ow, oh,
            )
        # Camera preview — bottom-right corner of the center/HUD area
        pw = _CameraPreview._W
        ph = self._cam_preview.height() or _CameraPreview._H
        self._cam_preview.setGeometry(
            cw.width() - _RIGHT_W - pw - 12,
            cw.height() - ph - 28,
            pw, ph,
        )
        # Clipboard panel — bottom-center
        if hasattr(self, '_clipboard_panel') and self._clipboard_panel.isVisible():
            self._position_clipboard_panel()
        # Quick drawer — reposition if open
        if hasattr(self, '_quick_drawer') and self._quick_drawer.isVisible():
            self._position_quick_drawer()

    def _update_metrics(self):
        # Home reads only CPU + MEM, so this stays cheap and leaves the heavy
        # NET/GPU/TMP probes (_SysMetrics) out of the hot path.
        try:
            snap = _metrics.snapshot()
            cpu = float(snap.get("cpu", -1) if snap.get("cpu") is not None else -1)
            mem = float(snap.get("mem", -1) if snap.get("mem") is not None else -1)
            if cpu >= 0:
                self._chip_cpu.setText(f"CPU {cpu:.0f}%")
                self._chip_cpu.setStyleSheet(
                    f"color: {'#ffbb55' if cpu > 80 else C.PRI}; "
                    f"background: {C.PANEL2}; border: 1px solid {C.BORDER_A}; "
                    f"border-radius: 14px; padding: 3px 7px;")
            if mem >= 0:
                self._chip_mem.setText(f"MEM {mem:.0f}%")
                self._chip_mem.setStyleSheet(
                    f"color: {'#ff6688' if mem > 85 else C.ACC2}; "
                    f"background: {C.PANEL2}; border: 1px solid {C.BORDER_A}; "
                    f"border-radius: 14px; padding: 3px 7px;")
        except Exception:
            pass

        if hasattr(self, '_uptime_lbl'):
            try:
                boot_t  = psutil.boot_time()
                elapsed = time.time() - boot_t
                h = int(elapsed // 3600)
                m = int((elapsed % 3600) // 60)
                self._uptime_lbl.setText(f"UP  {h:02d}:{m:02d}")
            except Exception:
                self._uptime_lbl.setText("UP  --:--")

        if hasattr(self, '_proc_lbl'):
            try:
                self._proc_lbl.setText(f"PROC  {len(psutil.pids())}")
            except Exception:
                self._proc_lbl.setText("PROC  --")

    def set_screen_status(self, active: bool, caption: str = "") -> None:
        """The Screen Awareness status chip on the Home rail. Thread-safe via
        a signal-free pattern: called from the Qt side only (main.py routes
        through set_state), so no cross-thread widget access. `caption` may
        carry the current activity word ("coding", "browsing")."""
        if not hasattr(self, '_screen_lbl'):
            return
        if active:
            word = (caption or "ON").strip().upper()[:12]
            self._screen_lbl.setText(f"EYES ·  {word}")
            self._screen_lbl.setStyleSheet(
                f"color: {C.GREEN}; background: {C.PANEL};"
                f"border: 1px solid {C.GREEN_D}; border-radius: 12px; padding: 4px 9px;")
        else:
            self._screen_lbl.setText("EYES  OFF")
            self._screen_lbl.setStyleSheet(
                f"color: {C.TEXT_DIM}; background: {C.PANEL};"
                f"border: 1px solid {C.BORDER}; border-radius: 12px; padding: 4px 9px;")


    def _build_header(self) -> QWidget:
        """Top chrome: identity → centred status → system readouts.
        Quiet on purpose — the face is the hero, the header only frames it."""
        w = QWidget()
        w.setFixedHeight(62)
        w.setStyleSheet(f"background: {C.DARK}; border-bottom: 1px solid {C.BORDER};")
        lay = QHBoxLayout(w)
        lay.setContentsMargins(18, 0, 18, 0)
        lay.setSpacing(12)

        def _btn(txt, tip, cb, fixed=(26, 26), fs=11):
            b = QPushButton(txt)
            b.setFixedSize(*fixed)
            b.setFont(QFont(FONT_UI, fs))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setToolTip(tip)
            b.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {C.TEXT_DIM};
                    border: 1px solid {C.BORDER}; border-radius: 14px;
                }}
                QPushButton:hover {{ color: {C.PRI}; border-color: {C.PRI_DIM}; }}
                QPushButton:checked {{
                    color: {C.PRI}; border-color: {C.PRI}; background: {C.PRI_GHO};
                }}
            """)
            b.clicked.connect(cb)
            return b

        def _chip(txt, color=C.PRI):
            l = QLabel(txt)
            l.setFont(QFont(FONT_UI, 8, QFont.Weight.Bold))
            l.setStyleSheet(f"color: {color}; background: {C.PANEL2};"
                            f"border: 1px solid {C.BORDER_A}; border-radius: 14px;"
                            f"padding: 3px 7px;")
            return l

        self._drawer_btn = _btn("⚙", "Settings & Controls",
                                self._toggle_drawer)
        self._drawer_btn.setCheckable(True)
        lay.addWidget(self._drawer_btn)

        # ── identity ────────────────────────────────────────────────────────
        mark = QLabel("◈")
        mark.setFont(QFont(FONT_UI, 21))
        mark.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        lay.addWidget(mark)

        id_col = QVBoxLayout(); id_col.setSpacing(0)
        _disp = self._assistant_name.upper()
        self._title_lbl = QLabel(_disp)
        self._title_lbl.setFont(QFont(FONT_UI, 14, QFont.Weight.DemiBold))
        self._title_lbl.setStyleSheet(
            f"color: {C.WHITE}; background: transparent; letter-spacing: 3px;")
        id_col.addWidget(self._title_lbl)
        _sub_text = ("AI Assistant"
                     if _disp in ("JARVIS", "J.A.R.V.I.S")
                     else "Personal AI Assistant")
        self._sub_lbl = QLabel(_sub_text)
        self._sub_lbl.setFont(QFont(FONT_UI, 7))
        self._sub_lbl.setStyleSheet(
            f"color: {C.TEXT_DIM}; background: transparent; letter-spacing: 2px;")
        id_col.addWidget(self._sub_lbl)
        lay.addLayout(id_col)
        lay.addStretch()

        # ── status — the single centred word that says what JARVIS is doing ──
        self._state_pill = QLabel("BOOTING")
        self._state_pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._state_pill.setFont(QFont(FONT_UI, 9, QFont.Weight.DemiBold))
        self._state_pill.setStyleSheet(_pill_style(
            C.TEXT_MED, C.PANEL2, C.BORDER_A))
        lay.addWidget(self._state_pill)
        lay.addStretch()

        # ── system + clock ──────────────────────────────────────────────────
        right_col = QHBoxLayout(); right_col.setSpacing(8)
        self._chip_cpu = _chip("CPU --")
        self._chip_mem = _chip("MEM --", C.ACC2)
        right_col.addWidget(self._chip_cpu)
        right_col.addWidget(self._chip_mem)

        clock_col = QVBoxLayout(); clock_col.setSpacing(0)
        self._clock_lbl = QLabel("00:00")
        self._clock_lbl.setFont(QFont(FONT_UI, 13, QFont.Weight.DemiBold))
        self._clock_lbl.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
        self._clock_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        clock_col.addWidget(self._clock_lbl)
        self._date_lbl = QLabel("")
        self._date_lbl.setFont(QFont(FONT_UI, 7))
        self._date_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        self._date_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        clock_col.addWidget(self._date_lbl)
        right_col.addLayout(clock_col)

        self._bg_btn = _btn("⌄", "Background mode — hide to tray (JARVIS keeps running)",
                            self._enter_background, fixed=(28, 28), fs=11)
        self._bg_btn.setToolTip("Background mode — hide to tray, JARVIS keeps running")
        right_col.addWidget(self._bg_btn)

        lay.addLayout(right_col)
        return w

    def _tick_clock(self):
        self._clock_lbl.setText(time.strftime("%H:%M:%S"))
        self._date_lbl.setText(time.strftime("%a %d %b %Y"))

    def _build_home_page(self) -> QWidget:
        """Home: the face dominates the centre; status orbiting it; a single
        input deck at the bottom with mic / call / quick actions beside it."""
        home = QWidget()
        home.setStyleSheet("background: " + C.BG + ";")
        lay = QVBoxLayout(home)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # -- face hero -------------------------------------------------------
        strip = QWidget()
        strip.setFixedHeight(_FACE_H)
        strip.setStyleSheet("background: " + C.BG + ";")
        self._home_face_strip = strip
        slay = QHBoxLayout(strip)
        slay.setContentsMargins(18, 10, 18, 2)
        slay.setSpacing(12)

        rail_left = QVBoxLayout(); rail_left.setSpacing(6)
        st = QLabel("STATUS")
        st.setFont(QFont(FONT_UI, 7, QFont.Weight.DemiBold))
        st.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;"
                         f"letter-spacing: 2px;")
        rail_left.addWidget(st)
        self._face_status = QLabel("BOOTING")
        self._face_status.setFont(QFont(FONT_UI, 9, QFont.Weight.DemiBold))
        self._face_status.setStyleSheet(_pill_style(
            C.TEXT_MED, C.PANEL2, C.BORDER_A))
        self._face_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        rail_left.addWidget(self._face_status)
        rail_left.addSpacing(8)
        self._tab_chat = self._tab_button("\U0001F4AC  Chat", self._show_chat)
        rail_left.addWidget(self._tab_chat)
        self._tab_log = self._tab_button("\u2630  Log", self._show_log)
        rail_left.addWidget(self._tab_log)
        rail_left.addStretch()
        slay.addLayout(rail_left)

        slay.addWidget(self._hud_cam_stack, stretch=1)

        rail_right = QVBoxLayout(); rail_right.setSpacing(6)
        rr = QLabel("SYSTEM")
        rr.setFont(QFont(FONT_UI, 7, QFont.Weight.DemiBold))
        rr.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;"
                         f"letter-spacing: 2px;")
        rail_right.addWidget(rr)
        self._ok_lbl = QLabel("AI CORE  ACTIVE")
        self._ok_lbl.setFont(QFont(FONT_UI, 7, QFont.Weight.DemiBold))
        self._ok_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._ok_lbl.setStyleSheet(
            f"color: {C.GREEN}; background: {C.PANEL};"
            f"border: 1px solid {C.GREEN_D}; border-radius: 12px; padding: 4px 9px;")
        rail_right.addWidget(self._ok_lbl)
        self._mem_lbl = QLabel("MEMORY  READY")
        self._mem_lbl.setFont(QFont(FONT_UI, 7, QFont.Weight.DemiBold))
        self._mem_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._mem_lbl.setStyleSheet(
            f"color: {C.PRI}; background: {C.PANEL};"
            f"border: 1px solid {C.BORDER_A}; border-radius: 12px; padding: 4px 9px;")
        rail_right.addWidget(self._mem_lbl)
        # Screen Awareness status — the small always-visible indicator the
        # background monitoring contract asks for. Coloured by actual state.
        self._screen_lbl = QLabel("EYES  OFF")
        self._screen_lbl.setFont(QFont(FONT_UI, 7, QFont.Weight.DemiBold))
        self._screen_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._screen_lbl.setStyleSheet(
            f"color: {C.TEXT_DIM}; background: {C.PANEL};"
            f"border: 1px solid {C.BORDER}; border-radius: 12px; padding: 4px 9px;")
        rail_right.addWidget(self._screen_lbl)
        rail_right.addStretch()
        slay.addLayout(rail_right)

        lay.addWidget(strip)

        # -- conversation / content surfaces (one at a time) ------------------
        home_row = QWidget()
        hr_lay = QHBoxLayout(home_row)
        hr_lay.setContentsMargins(18, 0, 18, 6)
        hr_lay.setSpacing(0)

        self._conv = ConversationView(self._assistant_name)
        self._log = LogWidget()
        self._home_stack = QStackedWidget()
        self._home_stack.addWidget(self._conv)          # 0 = chat
        self._home_stack.addWidget(self._content_panel)  # 1 = news/review/briefing
        self._home_stack.addWidget(self._quiz_panel)     # 2 = quiz
        self._home_stack.addWidget(self._log)            # 3 = activity log
        hr_lay.addWidget(self._home_stack, stretch=1)
        self._show_chat()
        lay.addWidget(home_row, stretch=1)

        # -- bottom deck: input + mic + call, quick actions to the right ------
        under = QWidget()
        under.setStyleSheet("background: " + C.BG + ";")
        ulay = QHBoxLayout(under)
        ulay.setContentsMargins(18, 4, 18, 12)
        ulay.setSpacing(12)

        in_col = QVBoxLayout(); in_col.setSpacing(6)
        in_row = QHBoxLayout(); in_row.setSpacing(6)
        in_row.addLayout(self._build_input_row(), stretch=1)
        self._mic_btn = self._round_btn("\U0001F399")
        self._mic_btn.setToolTip("Microphone - toggle mute  [F4]")
        self._mic_btn.clicked.connect(self._toggle_mute)
        in_row.addWidget(self._mic_btn)
        self._call_btn = self._round_btn("\U0001F4DE")
        self._call_btn.setToolTip("Call - hold to talk (push-to-talk / wake)")
        self._call_btn.pressed.connect(self._call_pressed)
        self._call_btn.released.connect(self._call_released)
        in_row.addWidget(self._call_btn)
        in_col.addLayout(in_row)

        self._file_hint = QLabel("")
        self._file_hint.setWordWrap(True)
        self._file_hint.setFont(QFont(FONT_UI, 8))
        self._file_hint.setStyleSheet(
            f"color: {C.PRI}; background: {C.PRI_GHO}; border: 1px solid {C.PRI_DIM};"
            f"border-radius: 10px; padding: 5px 9px;")
        self._file_hint.hide()
        in_col.addWidget(self._file_hint)
        ulay.addLayout(in_col, stretch=1)

        self._interrupt_btn = QPushButton("\u23F9")
        self._interrupt_btn.setFixedSize(34, 34)
        self._interrupt_btn.setFont(QFont(FONT_UI, 10))
        self._interrupt_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._interrupt_btn.setToolTip("Stop JARVIS mid-speech  [ESC]")
        self._interrupt_btn.setStyleSheet(f"""
            QPushButton {{
                background: {C.PANEL}; color: {C.MUTED_C};
                border: 1px solid {C.BORDER}; border-radius: 14px;
            }}
            QPushButton:hover {{ background: #2a0d16; border: 1px solid {C.MUTED_C}; }}
        """)
        self._interrupt_btn.clicked.connect(self._do_interrupt)
        ulay.addWidget(self._interrupt_btn, 0, Qt.AlignmentFlag.AlignBottom)
        ulay.addLayout(self._build_quick_actions())
        lay.addWidget(under)
        return home

    def _tab_button(self, txt, cb) -> QPushButton:
        b = QPushButton(txt)
        b.setFixedHeight(30)
        b.setFont(QFont(FONT_UI, 9))
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 14px;
                text-align: left; padding: 0 10px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B};
                                background: {C.PANEL}; }}
            QPushButton:checked {{
                background: {C.PRI_GHO}; color: {C.PRI}; font-weight: 600;
                border: 1px solid {C.PRI_DIM};
            }}
        """)
        b.clicked.connect(lambda _=False: (self._refresh_tab_btns(), cb()))
        b.setCheckable(True)
        return b

    def _refresh_tab_btns(self):
        idx = self._home_stack.currentIndex()
        for b, i in ((self._tab_chat, 0), (self._tab_log, 3)):
            b.setChecked(idx == i)

    def _round_btn(self, txt) -> QPushButton:
        b = QPushButton(txt)
        b.setFixedSize(42, 42)
        b.setFont(QFont(FONT_UI_EMOJI, 14))
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setStyleSheet(f"""
            QPushButton {{
                background: {C.PANEL}; color: {C.PRI};
                border: 1px solid {C.BORDER_B}; border-radius: 21px;
            }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border: 1px solid {C.PRI}; }}
            QPushButton:pressed {{ background: {C.PRI_GHO}; }}
        """)
        return b

    def _build_quick_actions(self) -> QVBoxLayout:
        """One-touch commands beside the input deck. Model-bound ones are plain
        phrases routed through JARVIS's own tools; the rest are pure UI."""
        def row_of(pairs):
            r = QHBoxLayout(); r.setSpacing(6)
            for label, cb in pairs:
                b = QPushButton(label)
                b.setFixedHeight(30)
                b.setFont(QFont(FONT_UI, 8))
                b.setCursor(Qt.CursorShape.PointingHandCursor)
                b.setStyleSheet(f"""
                    QPushButton {{
                        background: {C.PANEL}; color: {C.TEXT_MED};
                        border: 1px solid {C.BORDER}; border-radius: 15px;
                        padding: 0 11px;
                    }}
                    QPushButton:hover {{ color: {C.WHITE}; border-color: {C.PRI_DIM};
                                        background: {C.PANEL2}; }}
                """)
                b.clicked.connect(lambda _=False, c=cb: c())
                r.addWidget(b)
            r.addStretch()
            return r

        col = QVBoxLayout(); col.setSpacing(6)
        col.addLayout(row_of((
            ("\U0001F5A5 Open Apps", lambda: self._quick_action(
                "List my installed apps and open one for me.")),
            ("\U0001F50E Search", lambda: self._quick_action(
                "Search the web for something interesting.")),
            ("\U0001F3B5 Music", lambda: self._quick_action("Play some music.")),
            ("\u25B6 YouTube", lambda: self._quick_action("Open YouTube.")),
            ("\U0001F3AE Steam", lambda: self._quick_action(
                "Open my Steam library.")),
        )))
        col.addLayout(row_of((
            ("\U0001F4F0 News", lambda: self._quick_action(
                "Give me today's top news, briefly.")),
            ("\U0001F552 Time", lambda: self._quick_action(
                "What time is it, and what is today's date?")),
            ("\U0001F9E0 Memory", self._open_memory_panel),
            ("\U0001F4CE Attach", self._attach_file),
        )))
        return col

    def _quick_action(self, phrase: str):
        if self.on_text_command:
            threading.Thread(target=self.on_text_command, args=(phrase,),
                             daemon=True).start()

    def _attach_file(self):
        from PyQt6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(
            self, "Attach a file for JARVIS", str(Path.home()))
        if path:
            self._on_file_selected(path)

    def _call_pressed(self):
        if self.on_push_to_talk:
            try:
                self.on_push_to_talk(True)
                return
            except Exception:
                pass
        if self.on_wake_manual:
            try:
                self.on_wake_manual()
            except Exception:
                pass

    def _call_released(self):
        if self.on_push_to_talk:
            try:
                self.on_push_to_talk(False)
            except Exception:
                pass

    def _show_chat(self):
        self._home_stack.setCurrentIndex(0)

    def _show_log(self):
        self._home_stack.setCurrentIndex(3)
        self._refresh_tab_btns()

    def _enter_background(self):
        """Background / headless mode: hide the UI, keep JARVIS alive in the
        tray. Wake word and all services continue untouched."""
        self.hide()
        try:
            if self._tray and not self._tray_notified:
                self._tray.showMessage(
                    self._assistant_name.upper(),
                    "Background mode — still running. Say your wake word or "
                    "open the tray to talk to me.",
                    QSystemTrayIcon.MessageIcon.Information, 3500,
                )
                self._tray_notified = True
        except Exception:
            pass

    def _build_settings_page(self) -> QWidget:
        w = QWidget()
        w.setStyleSheet(f"background: {C.BG};")
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        nav = QListWidget()
        nav.setObjectName("SettingsNav")
        nav.setFixedWidth(196)
        nav.setStyleSheet(f"""
            QListWidget#SettingsNav {{
                background: {C.DARK};
                border: none;
                border-right: 1px solid {C.BORDER};
                outline: 0;
                padding-top: 8px;
            }}
            QListWidget#SettingsNav::item {{
                color: {C.TEXT_MED};
                padding: 9px 12px;
                margin: 2px 8px;
                border: none;
                border-radius: 14px;
                font-family: {FONT_STACK};
                font-size: 9pt;
            }}
            QListWidget#SettingsNav::item:hover {{ color: {C.TEXT}; background: {C.PANEL}; }}
            QListWidget#SettingsNav::item:selected {{
                color: {C.PRI}; background: {C.PRI_GHO};
                border: 1px solid {C.PRI_DIM};
                border-radius: 10px;
            }}
        """)
        self._settings_nav = nav

        pages = QStackedWidget()
        self._settings_pages = pages

        self._settings_stacks = {}
        section_labels = []
        for key, _label in (
            ("general", "General"),
            ("ai", "AI & Models"),
            ("api", "API Keys"),
            ("voice", "Voice & Language"),
            ("memory", "Memory"),
            ("screen", "Screen Awareness"),
            ("pc", "PC Control"),
            ("integrations", "Integrations"),
            ("appearance", "Appearance"),
            ("startup", "Startup & Background"),
            ("privacy", "Privacy"),
            ("advanced", "Advanced"),
            ("about", "About"),
        ):
            section_labels.append(_label.upper())
            page = self._build_settings_section(key)
            self._settings_stacks[key] = page
            pages.addWidget(page)
        nav.addItems(section_labels)
        nav.currentRowChanged.connect(pages.setCurrentIndex)
        nav.setCurrentRow(0)

        lay.addWidget(nav)
        lay.addWidget(pages, stretch=1)

        self._settings_sections = section_labels
        return w

    def _settings_card(self) -> QWidget:
        c = QWidget()
        c.setStyleSheet(f"background: {C.PANEL}; border: 1px solid {C.BORDER_B}; "
                        f"border-radius: 14px;")
        return c

    def _set_row(self, lay, title: str, desc: str, btn_text: str, cb,
                 colour=C.PRI, btn_w: int = 96) -> None:
        card = self._settings_card()
        h = QHBoxLayout(card)
        h.setContentsMargins(14, 10, 14, 10)
        h.setSpacing(10)
        tcol = QVBoxLayout(); tcol.setSpacing(2)
        tl = QLabel(title)
        tl.setFont(QFont(FONT_UI, 9, QFont.Weight.DemiBold))
        tl.setStyleSheet(f"color: {C.WHITE}; background: transparent;")
        tcol.addWidget(tl)
        if desc:
            dl = QLabel(desc)
            dl.setWordWrap(True)
            dl.setFont(QFont("Segoe UI", 8))
            dl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
            tcol.addWidget(dl)
        h.addLayout(tcol, stretch=1)
        b = QPushButton(btn_text)
        b.setFixedSize(btn_w, 32)
        b.setFont(QFont(FONT_UI, 8, QFont.Weight.Bold))
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setStyleSheet(f"""
            QPushButton {{
                background: {C.PANEL2}; color: {colour};
                border: 1px solid {C.PRI_DIM if colour == C.PRI else C.BORDER};
                border-radius: 10px;
            }}
            QPushButton:hover {{
                background: {C.PRI_GHO if colour == C.PRI else C.PANEL};
                border: 1px solid {C.PRI if colour == C.PRI else C.PRI_DIM};
            }}
        """)
        b.clicked.connect(lambda _=False: cb())
        h.addWidget(b)
        lay.addWidget(card)

    def _set_owned_btn(self, lay, title: str, desc: str, btn) -> None:
        """Settings row that hosts a caller-owned button (its state text/style
        is managed from elsewhere, e.g. auto-start or the wake-word toggle)."""
        card = self._settings_card()
        h = QHBoxLayout(card)
        h.setContentsMargins(14, 10, 14, 10)
        h.setSpacing(10)
        tcol = QVBoxLayout(); tcol.setSpacing(2)
        tl = QLabel(title)
        tl.setFont(QFont(FONT_UI, 9, QFont.Weight.DemiBold))
        tl.setStyleSheet(f"color: {C.WHITE}; background: transparent;")
        tcol.addWidget(tl)
        if desc:
            dl = QLabel(desc)
            dl.setWordWrap(True)
            dl.setFont(QFont("Segoe UI", 8))
            dl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
            tcol.addWidget(dl)
        h.addLayout(tcol, stretch=1)
        btn.setFixedHeight(30)
        h.addWidget(btn)
        lay.addWidget(card)

    def _set_toggle(self, lay, title: str, desc: str,
                    getter, setter) -> QPushButton:
        card = self._settings_card()
        h = QHBoxLayout(card)
        h.setContentsMargins(14, 10, 14, 10)
        h.setSpacing(10)
        tcol = QVBoxLayout(); tcol.setSpacing(2)
        tl = QLabel(title)
        tl.setFont(QFont(FONT_UI, 9, QFont.Weight.DemiBold))
        tl.setStyleSheet(f"color: {C.WHITE}; background: transparent;")
        tcol.addWidget(tl)
        if desc:
            dl = QLabel(desc)
            dl.setWordWrap(True)
            dl.setFont(QFont("Segoe UI", 8))
            dl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
            tcol.addWidget(dl)
        h.addLayout(tcol, stretch=1)
        b = QPushButton()
        b.setFixedSize(96, 30)
        b.setFont(QFont(FONT_UI, 8, QFont.Weight.Bold))
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.clicked.connect(lambda _=False: self._flip_setting(getter, setter))
        h.addWidget(b)
        lay.addWidget(card)
        self._paint_toggle(b, bool(getter()))
        return b

    def _flip_setting(self, getter, setter):
        want = not bool(getter())
        setter(want)
        self._paint_toggle(self.sender(), want)

    def _paint_toggle(self, b: QPushButton, on: bool):
        """A real switch: rounded track, no text. State reads from colour."""
        b.setFixedSize(46, 26)
        b.setText("")
        if on:
            b.setStyleSheet(f"""
                QPushButton {{
                    background: {C.GREEN_D}; border: 1px solid {C.GREEN};
                    border-radius: 13px; padding: 0;
                }}
            """)
        else:
            b.setStyleSheet(f"""
                QPushButton {{
                    background: {C.PANEL2}; border: 1px solid {C.BORDER_B};
                    border-radius: 13px; padding: 0;
                }}
            """)

    def _set_pills(self, lay, title: str, options, current: str, on_pick) -> tuple:
        card = self._settings_card()
        h = QHBoxLayout(card)
        h.setContentsMargins(14, 10, 14, 10)
        h.setSpacing(12)
        tl = QLabel(title)
        tl.setFont(QFont(FONT_UI, 9, QFont.Weight.DemiBold))
        tl.setStyleSheet(f"color: {C.WHITE}; background: transparent;")
        h.addWidget(tl)
        h.addStretch()
        pills = {}
        for key, label in options:
            b = QPushButton(label)
            b.setFixedHeight(28)
            b.setFont(QFont(FONT_UI, 8))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: self._flip_pill(pills, k,
                                                                    on_pick))
            h.addWidget(b)
            pills[key] = b
        lay.addWidget(card)
        self._refresh_pills(pills, current)
        return pills

    def _flip_pill(self, pills: dict, key: str, on_pick) -> None:
        """Select one pill in a segmented row, restyle, and notify the caller."""
        on_pick(key)
        self._refresh_pills(pills, key)

    def _refresh_pills(self, pills: dict, current: str):
        for k, b in pills.items():
            on = (k == current)
            bg = C.PRI_GHO if on else "transparent"
            fg = C.PRI if on else C.TEXT_MED
            bd = C.PRI_DIM if on else C.BORDER
            fw = "600" if on else "normal"
            b.setStyleSheet(f"""
                QPushButton {{
                    background: {bg}; color: {fg};
                    border: 1px solid {bd};
                    border-radius: 14px; padding: 0 12px;
                    font-weight: {fw};
                }}
                QPushButton:hover {{ color: {C.WHITE}; border-color: {C.BORDER_B}; }}
            """)

    def _set_note(self, lay, text: str) -> None:
        l = QLabel(text)
        l.setWordWrap(True)
        l.setFont(QFont("Segoe UI", 8))
        l.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent; "
                        f"border-left: 2px solid {C.BORDER}; padding: 4px 8px;")
        lay.addWidget(l)

    def _build_settings_section(self, key: str) -> QWidget:
        w = QWidget()
        w.setObjectName("SettingsPage:" + key)
        w.setStyleSheet(f"QWidget#SettingsPage\\:{key} {{ background: {C.BG}; }}")
        rootl = QVBoxLayout(w)
        rootl.setContentsMargins(16, 16, 16, 16)
        rootl.setSpacing(8)

        scroll_w = QWidget()
        scroll_w.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(scroll_w)
        lay.setContentsMargins(0, 0, 8, 0)
        lay.setSpacing(8)
        # (closing stretch is appended after the rows are added, so cards
        #  stack from the top instead of sinking to the bottom of the page)

        from memory.config_manager import (
            get_memory_enabled, save_memory_enabled,
            get_track_activity, save_track_activity,
            get_proactive_enabled, save_proactive_enabled,
            get_ambient_mode, save_ambient_mode,
            get_screen_awareness, save_screen_awareness,
            get_screen_glance, save_screen_glance,
            get_screen_share_quality, save_screen_share_quality,
            get_observe_interval, save_observe_interval,
            get_narrate_actions, save_narrate_actions,
            get_verify_clicks, save_verify_clicks,
            get_thinking_enabled, save_thinking_enabled,
            get_brief_enabled, save_brief_enabled,
            get_talk_cadence, save_talk_cadence,
            get_humor_level, save_humor_level,
            get_emotion_depth, save_emotion_depth,
            get_free_providers, get_language_mode,
        )

        def _fit():
            scroll_w.adjustSize()

        if key == "general":
            self._set_row(lay, "Customise Assistant",
                          "Your name, the assistant's name, voice and UI colour.",
                          "OPEN", self._open_customize)
            self._set_row(lay, "Audio Devices",
                          "Pick the microphone JARVIS hears and the speakers it "
                          "talks through.", "OPEN", self._open_audio_devices)
            self._set_toggle(lay, "Proactive Check-ins",
                             "Let JARVIS speak up on its own after quiet time.",
                             get_proactive_enabled, save_proactive_enabled)
            self._set_toggle(lay, "Ambient Mode",
                             "Gentle, slow ambient prompts while you work.",
                             get_ambient_mode, save_ambient_mode)

        elif key == "ai":
            self._set_row(lay, "AI & Providers",
                          "Local / free provider → free API → optional paid "
                          "fallback. Nothing about the app lives on one pricey "
                          "cloud API.", "OPEN",
                          self._open_api_keys)
            self._set_toggle(lay, "Deep Thinking",
                             "Extra reasoning step for hard questions.",
                             get_thinking_enabled, save_thinking_enabled)
            self._set_toggle(lay, "Verify clicks",
                             "Confirm before JARVIS clicks anything on your PC.",
                             get_verify_clicks, save_verify_clicks)
            pro = get_free_providers()
            if pro:
                self._set_note(lay, "Configured providers: " +
                               ", ".join(str(p.get("name", "?")) for p in pro[:5]))

        elif key == "api":
            self._set_row(lay, "API Keys & Providers",
                          "Every provider has a key field, an enable switch, a "
                          "test-connection button, status and error message. All "
                          "checks run in the background — opening this never "
                          "freezes or crashes.", "OPEN KEYS",
                          self._open_api_keys, colour=C.GREEN, btn_w=110)
            self._set_note(lay, "Vision, speech-to-text, text-to-speech, search, "
                               "OCR and translation all share this one panel.")

        elif key == "voice":
            self._set_row(lay, "Voice & Devices",
                          "Choose assistant voice, mic and speakers.",
                          "OPEN", self._open_audio_devices)
            self._set_pills(lay, "TALK CADENCE",
                            (("standard", "STANDARD"), ("warm", "WARM"),
                             ("live", "LIVE"), ("chatty", "CHATTY")),
                            get_talk_cadence(),
                            lambda k: save_talk_cadence(k))
            self._set_pills(lay, "HUMOUR",
                            (("off", "OFF"), ("subtle", "SUBTLE"),
                             ("playful", "PLAYFUL"), ("chaotic", "CHAOTIC")),
                            get_humor_level(),
                            lambda k: save_humor_level(k))
            self._set_pills(lay, "EMOTION DEPTH",
                            (("off", "OFF"), ("light", "LIGHT"), ("full", "FULL")),
                            get_emotion_depth(),
                            lambda k: save_emotion_depth(k))
            self._set_note(lay, "Language: " + str(get_language_mode() or "auto"))

        elif key == "memory":
            self._set_row(lay, "Memory Browser",
                          "Everything JARVIS has stored about you, and when it "
                          "learned it.", "OPEN", self._open_memory_panel)
            self._set_toggle(lay, "Persistent Memory",
                             "Session summaries, facts and lessons written to "
                             "long-term memory.", get_memory_enabled, save_memory_enabled)
            self._set_toggle(lay, "Activity Timeline",
                             "Keep a short local timeline of what you have been "
                             "doing.", get_track_activity, save_track_activity)

        elif key == "screen":
            self._set_toggle(lay, "Screen Share",
                             "Let JARVIS watch the active window. Off = observer "
                             "off and captures refused.",
                             get_screen_awareness, save_screen_awareness)
            self._set_toggle(lay, "Auto Glance Captions",
                             "One-line vision look during check-ins.",
                             get_screen_glance, save_screen_glance)
            self._set_pills(lay, "PICTURE QUALITY",
                            (("light", "LIGHT"), ("medium", "MEDIUM"),
                             ("high", "HIGH")),
                            get_screen_share_quality(),
                            lambda k: save_screen_share_quality(k))
            self._set_note(lay, "LIGHT polls rarely and refreshes captions every "
                                "4 min; MEDIUM every 2; HIGH every 1 min. A "
                                "static screen does zero work at any level.")

        elif key == "pc":
            self._set_row(lay, "Remote Control",
                          "Control your phone from the PC window.",
                          "OPEN", self._open_remote)
            self._autostart_btn = QPushButton()
            self._autostart_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self._autostart_btn.clicked.connect(self._toggle_autostart)
            self._set_owned_btn(lay, "Start with Windows",
                                "Launch JARVIS when you log in.",
                                self._autostart_btn)
            self._set_row(lay, "Desktop Shortcut",
                          "Create a desktop shortcut for quick launch.",
                          "CREATE", self._create_desktop_shortcut)

        elif key == "integrations":
            self._set_row(lay, "Plugins",
                          "Browse, install and enable companions.",
                          "OPEN", self._open_plugin_manager)
            self._set_row(lay, "Plugin Settings",
                          "Per-plugin configuration screens.",
                          "OPEN", self._open_plugin_settings)
            self._set_row(lay, "Desktop Shortcut",
                          "Put a quick-launch icon on the desktop.",
                          "CREATE", self._create_desktop_shortcut)

        elif key == "appearance":
            self._set_row(lay, "Customise",
                          "Name, user name, UI colour and voice.",
                          "OPEN", self._open_customize)
            self._hud_btn = QPushButton()
            self._hud_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self._hud_btn.clicked.connect(self._toggle_hud_style)
            self._set_owned_btn(lay, "Assistant Face",
                                "Animated face or reactor core — both "
                                "lightweight 2D.", self._hud_btn)
            self._refresh_hud_btn()
            self._set_row(lay, "Fullscreen",
                          "Fill the screen  [F11].", "F11",
                          self._toggle_fullscreen)
            self._set_row(lay, "Compact Layout",
                          "Collapse the face strip for a tight, chat-only window.",
                          "TOGGLE", self._toggle_compact)

        elif key == "startup":
            self._wake_btn = QPushButton()
            self._wake_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self._wake_btn.clicked.connect(self._toggle_wake_word)
            self._set_owned_btn(lay, "Wake Word",
                                "'Hey JARVIS' — works even with the window hidden.",
                                self._wake_btn)
            self._wake_sleep_btn = QPushButton()
            self._wake_sleep_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self._wake_sleep_btn.clicked.connect(self._tap_wake_manual)
            self._wake_sleep_btn.hide()
            self._set_owned_btn(lay, "Sleep / Wake State",
                                "Force JARVIS asleep or back to listening.",
                                self._wake_sleep_btn)
            self._ptt_btn = QPushButton()
            self._ptt_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self._ptt_btn.clicked.connect(self._toggle_ptt)
            self._set_owned_btn(lay, "Push-to-Talk",
                                "Hold a key to talk instead of streaming the mic.",
                                self._ptt_btn)
            self._brief_btn = QPushButton()
            self._brief_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self._brief_btn.clicked.connect(self._toggle_brief)
            self._set_owned_btn(lay, "Daily Briefing",
                                "A morning news & schedule briefing.",
                                self._brief_btn)
            self._autostart_btn = QPushButton()
            self._autostart_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self._autostart_btn.clicked.connect(self._toggle_autostart)
            self._set_owned_btn(lay, "Start with Windows",
                                "Run in the tray from login.", self._autostart_btn)
            self._set_row(lay, "Background Mode",
                          "Hide the window; JARVIS keeps running in the tray.",
                          "⇣ HIDE", self._enter_background, colour=C.ACC2)
            self._set_note(lay, "Closing the window always hides to tray — "
                                "voice, wake word, memory and screen awareness "
                                "keep running.")
            self._refresh_wake_btns()
            self._refresh_talk_btns()
            self._update_autostart_btn(self._check_autostart())
            self._update_brief_btn(get_brief_enabled())

        elif key == "privacy":
            self._set_toggle(lay, "Persistent Memory",
                             "Master switch for writing to long-term memory.",
                             get_memory_enabled, save_memory_enabled)
            self._set_toggle(lay, "Activity Timeline",
                             "Track recent activity for follow-up.",
                             get_track_activity, save_track_activity)
            self._set_toggle(lay, "Screen Share",
                             "Everything visual stays on this machine.",
                             get_screen_awareness, save_screen_awareness)
            self._set_toggle(lay, "Narrate Actions",
                             "Explain out loud what JARVIS is doing.",
                             get_narrate_actions, save_narrate_actions)
            self._set_row(lay, "Clear Conversation",
                          "Wipe the on-screen conversation view.",
                          "CLEAR", self._conv.clear, colour=C.MUTED_C)

        elif key == "advanced":
            self._set_toggle(lay, "Briefings",
                             "Auto news & schedule briefings.",
                             get_brief_enabled, save_brief_enabled)
            self._set_note(lay, "Summon hotkey: Win + Shift + J brings the "
                                "window to the front from anywhere.")
            self._set_row(lay, "Activity Log",
                          "Raw log feed (chat view is next to it on Home).",
                          "VIEW", self._show_log)
            self._uptime_lbl = QLabel("UP  --:--")
            self._proc_lbl = QLabel("PROC  --")
            syscard = self._settings_card()
            sl = QVBoxLayout(syscard)
            sl.setContentsMargins(10, 8, 10, 8)
            for lbl, col in ((self._uptime_lbl, C.GREEN),
                             (self._proc_lbl, C.TEXT_MED)):
                lbl.setFont(QFont(FONT_UI, 8, QFont.Weight.Bold))
                lbl.setStyleSheet(f"color: {col}; background: transparent;")
                sl.addWidget(lbl)
            lay.addWidget(syscard)

        elif key == "about":
            title = QLabel("◈  ICE")
            title.setFont(QFont(FONT_UI, 15, QFont.Weight.Bold))
            title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
            lay.addWidget(title)
            self._set_note(lay,
                "A clean, low-end friendly personal AI assistant.\n\n"
                "Design goal: futuristic but simple. Flat 2D rendering, no 3D "
                "hologram, no particles, no GPU-heavy effects. The face, "
                "conversation and controls are the whole interface; settings "
                "live behind the ⚙ button.\n\n"
                "Runs headless: close the window and JARVIS keeps listening, "
                "remembering, watching the screen (when enabled) and running "
                "automation from the system tray. CPU work stops while the "
                "window is hidden.\n\n"
                "No data leaves this machine except the API calls you have "
                "configured.")
            self._set_note(lay, f"Version  {APP_VERSION}   ·   Protocol  "
                                f"{APP_PROTOCOL}   ·   Low-end mode  ON")

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("""
            QScrollArea { background: transparent; border: none; }
            QScrollArea > QWidget > QWidget { background: transparent; }
        """)
        scroll.setWidget(scroll_w)
        lay.addStretch()
        rootl.addWidget(scroll, stretch=1)
        return w

    def _toggle_compact(self):
        """Compact/focus mode — collapse the face strip to a thin band so the
        window becomes a clean, chat-first surface."""
        strip = getattr(self, "_home_face_strip", None)
        if strip is None:
            return
        compact = strip.maximumHeight() == _FACE_H
        strip.setFixedHeight(_FACE_SMALL if compact else _FACE_H)
        if compact:
            self.hud.pause()
        else:
            self.hud.resume()

    def _toggle_drawer(self, checked: bool):
        """⚙ in the header toggles the whole workspace: Home ⇄ Settings."""
        self._app_stack.setCurrentIndex(1 if checked else 0)
        if checked:
            self._refresh_wake_btns()   # resolve wake state lazily on open

    def _position_quick_drawer(self):
        pass

    def _build_input_row(self) -> QHBoxLayout:
        row = QHBoxLayout(); row.setSpacing(6)
        self._input = QLineEdit()
        self._input.setPlaceholderText("Type a command or ask something\u2026")
        self._input.setFont(QFont(FONT_UI, 10))
        self._input.setFixedHeight(42)
        self._input.setStyleSheet(f"""
            QLineEdit {{
                background: {C.PANEL2}; color: {C.WHITE};
                border: 1px solid {C.BORDER}; border-radius: 21px;
                padding: 4px 16px; selection-background-color: {C.PRI_GHO};
            }}
            QLineEdit:focus {{ border: 1px solid {C.PRI_DIM};
                              background: {C.PANEL}; }}
        """)
        self._input.returnPressed.connect(self._send)
        row.addWidget(self._input)

        send = QPushButton("\u27A4")
        send.setFixedSize(42, 42)
        send.setFont(QFont(FONT_UI, 13))
        send.setCursor(Qt.CursorShape.PointingHandCursor)
        send.setToolTip("Send")
        send.setStyleSheet(f"""
            QPushButton {{
                background: {C.PRI_GHO}; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 21px;
            }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border: 1px solid {C.PRI};
                                color: {C.WHITE}; }}
            QPushButton:pressed {{ background: {C.PRI_DIM}; }}
        """)
        send.clicked.connect(self._send)
        row.addWidget(send)
        return row

    def _build_content_panel(self) -> QWidget:
        """
        Collapsible panel below the HUD — shows search results, news, briefings.
        Hidden by default; appears when show_content() is called.
        """
        w = QWidget()
        w.setObjectName("ContentPanel")
        w.setStyleSheet(f"""
            QWidget#ContentPanel {{
                background: {C.PANEL};
                border-top: 1px solid {C.BORDER_B};
            }}
        """)
        w.hide()

        lay = QVBoxLayout(w)
        lay.setContentsMargins(12, 7, 12, 8)
        lay.setSpacing(5)

        # ── header row ───────────────────────────────────────────────────────
        hdr = QHBoxLayout(); hdr.setSpacing(6)

        dot = QLabel("◈")
        dot.setFont(QFont(FONT_UI, 9, QFont.Weight.Bold))
        dot.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        hdr.addWidget(dot)

        self._content_title_lbl = QLabel("BRIEFING")
        self._content_title_lbl.setFont(QFont(FONT_UI, 8, QFont.Weight.Bold))
        self._content_title_lbl.setStyleSheet(
            f"color: {C.PRI}; background: transparent; letter-spacing: 1px;"
        )
        hdr.addWidget(self._content_title_lbl)
        hdr.addStretch()

        self._content_ts_lbl = QLabel("")
        self._content_ts_lbl.setFont(QFont(FONT_UI, 7))
        self._content_ts_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        hdr.addWidget(self._content_ts_lbl)

        dismiss = QPushButton("DISMISS  ✕")
        dismiss.setFont(QFont(FONT_UI, 7))
        dismiss.setFixedHeight(18)
        dismiss.setCursor(Qt.CursorShape.PointingHandCursor)
        dismiss.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_DIM};
                border: 1px solid {C.BORDER}; border-radius: 10px; padding: 0 5px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        dismiss.clicked.connect(w.hide)
        hdr.addWidget(dismiss)
        lay.addLayout(hdr)

        # ── separator ─────────────────────────────────────────────────────────
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER};"); lay.addWidget(sep)

        # ── text display ──────────────────────────────────────────────────────
        self._content_display = QTextEdit()
        self._content_display.setReadOnly(True)
        self._content_display.setFont(QFont(FONT_UI, 8))
        self._content_display.setMinimumHeight(60)
        self._content_display.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._content_display.setStyleSheet(f"""
            QTextEdit {{
                background: {C.DARK};
                color: {C.TEXT};
                border: 1px solid {C.BORDER};
                border-radius: 10px;
                padding: 6px 8px;
                selection-background-color: {C.PRI_GHO};
            }}
            QScrollBar:vertical {{
                background: {C.BG}; width: 6px; border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {C.BORDER_B}; border-radius: 10px; min-height: 16px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0; border: none;
            }}
        """)
        lay.addWidget(self._content_display)

        return w

    def _show_content(self, title: str, text: str):
        """Slot — runs on Qt main thread. Updates and shows the content panel."""
        import time as _time
        # The panel opens below the head, so the head looks down at it. It is a
        # tiny thing that answers "did that land?" before you read a word.
        self.hud.glance(0.0, -0.85, hold=1.3)
        self._content_title_lbl.setText(title.upper()[:48])
        self._content_ts_lbl.setText(_time.strftime("%H:%M:%S"))
        self._content_display.setPlainText(text)
        self._content_display.moveCursor(
            self._content_display.textCursor().MoveOperation.Start
        )
        self._home_stack.setCurrentIndex(1)
        self._refresh_tab_btns()

    # ── document review ──────────────────────────────────────────────────────
    # Rendered as rich text into the content panel that already exists, rather
    # than into a panel of its own. A review is read, not clicked, so QTextEdit
    # gives scrolling, selection and copy for nothing, and the HUD gains no
    # widget it has to lay out. Severity decides colour and order here because
    # that is presentation; the plugin supplies no styling and knows no palette,
    # which is also what lets a re-theme repaint a review correctly.

    # Severity is marked by a symbol and a colour, not by a word. The findings
    # themselves are in the user's language, and "[SERIOUS]" sitting inside a
    # Turkish sentence is the kind of seam this project tries not to have —
    # while translating the tag would mean a table per language, which is worse.
    # A shape carries it in every language, and shape plus colour still reads
    # for someone who cannot separate red from amber. What the marks mean
    # arrives the way everything else does: JARVIS says it out loud.
    _REVIEW_MARKS = {"serious": ("RED", "▲"), "caution": ("ACC2", "●"), "note": ("PRI_DIM", "·")}

    @staticmethod
    def _esc(s) -> str:
        return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace("\n", "<br>"))

    def _show_review(self, title: str, summary: str, findings, unclear):
        """Slot — Qt main thread. Lays a document review into the content panel."""
        e = self._esc
        parts = [f'<div style="color:{C.TEXT}; font-family:{FONT_MONO};">']

        if summary:
            parts.append(
                f'<div style="color:{C.WHITE}; border-left:2px solid {C.PRI};'
                f' padding-left:8px; margin-bottom:10px;">{e(summary)}</div>')

        for f in (findings or []):
            key, mark = self._REVIEW_MARKS.get(f.get("severity"), ("PRI_DIM", "·"))
            colour = getattr(C, key)
            parts.append(f'<div style="margin-bottom:11px;">')
            parts.append(
                f'<span style="color:{colour}; font-weight:bold;">{mark}</span> '
                f'<span style="color:{C.WHITE}; font-weight:bold;">'
                f'{e(f.get("heading"))}</span>')
            if f.get("detail"):
                parts.append(f'<div style="margin-left:12px;">{e(f["detail"])}</div>')
            if f.get("quote"):
                # The document's own wording, visually separated from the
                # explanation so the two are never mistaken for each other.
                parts.append(
                    f'<div style="margin-left:12px; color:{C.TEXT_DIM};'
                    f' border-left:1px solid {C.BORDER}; padding-left:7px;">'
                    f'&ldquo;{e(f["quote"])}&rdquo;</div>')
            if f.get("suggestion"):
                parts.append(
                    f'<div style="margin-left:12px; color:{C.PRI};">'
                    f'&rarr; {e(f["suggestion"])}</div>')
            parts.append('</div>')

        if unclear:
            parts.append(
                f'<div style="margin-top:6px; border-top:1px solid {C.BORDER};'
                f' padding-top:7px; color:{C.TEXT_MED};">'
                'The document does not settle:</div>')
            for u in unclear:
                parts.append(
                    f'<div style="margin-left:12px; color:{C.TEXT_MED};">'
                    f'&middot; {e(u)}</div>')
        parts.append('</div>')

        import time as _time
        self.hud.glance(0.0, -0.85, hold=1.3)
        # Left as written, not upper-cased. The other content-panel titles are
        # the app's own English labels, but this one is the document's name in
        # the user's language, and str.upper() applies English casing rules to
        # it: Turkish "Sözleşmesi" comes back "SÖZLEŞMESI", having lost the
        # dotted capital İ. Python has no locale-aware upper to reach for, and
        # imposing one language's rules on all of them is the bug, not the fix.
        self._content_title_lbl.setText((title or "Document")[:48])
        self._content_ts_lbl.setText(_time.strftime("%H:%M:%S"))
        self._content_display.setHtml("".join(parts))
        self._content_display.moveCursor(
            self._content_display.textCursor().MoveOperation.Start)
        self._home_stack.setCurrentIndex(1)
        self._refresh_tab_btns()

    # ── quiz panel ───────────────────────────────────────────────────────────
    # An interactive twin of the content panel. The plugin only ever hands over
    # questions; everything about asking, marking and reporting happens here,
    # and the finished result is pushed back into the conversation the same way
    # a dropped file is — as a message JARVIS reads and responds to. That keeps
    # the tool call short (it returns the moment the board is up) and leaves the
    # talking to the assistant, in the user's own language.

    def _quiz_btn(self, text: str, primary: bool = False) -> QPushButton:
        b = QPushButton(text)
        b.setFont(QFont(FONT_UI, 8))
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setMinimumHeight(24)
        edge = C.BORDER_B if primary else C.BORDER
        col = C.PRI if primary else C.TEXT_MED
        b.setStyleSheet(f"""
            QPushButton {{
                background: {C.PANEL2}; color: {col};
                border: 1px solid {edge}; border-radius: 10px;
                padding: 3px 9px; text-align: left;
            }}
            QPushButton:hover {{ color: {C.WHITE}; border-color: {C.PRI_DIM}; }}
            QPushButton:disabled {{ color: {C.TEXT_DIM}; border-color: {C.BORDER}; }}
        """)
        return b

    def _build_quiz_panel(self) -> QWidget:
        w = QWidget()
        w.setObjectName("QuizPanel")
        w.setStyleSheet(f"""
            QWidget#QuizPanel {{
                background: {C.PANEL};
                border-top: 1px solid {C.BORDER_B};
            }}
        """)
        w.hide()

        lay = QVBoxLayout(w)
        lay.setContentsMargins(12, 7, 12, 8)
        lay.setSpacing(6)

        hdr = QHBoxLayout(); hdr.setSpacing(6)
        dot = QLabel("◈")
        dot.setFont(QFont(FONT_UI, 9, QFont.Weight.Bold))
        dot.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        hdr.addWidget(dot)

        self._quiz_title_lbl = QLabel("QUIZ")
        self._quiz_title_lbl.setFont(QFont(FONT_UI, 8, QFont.Weight.Bold))
        self._quiz_title_lbl.setStyleSheet(
            f"color: {C.PRI}; background: transparent; letter-spacing: 1px;")
        hdr.addWidget(self._quiz_title_lbl)
        hdr.addStretch()

        self._quiz_count_lbl = QLabel("")
        self._quiz_count_lbl.setFont(QFont(FONT_UI, 7))
        self._quiz_count_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        hdr.addWidget(self._quiz_count_lbl)

        quit_btn = QPushButton("DISMISS  ✕")
        quit_btn.setFont(QFont(FONT_UI, 7))
        quit_btn.setFixedHeight(18)
        quit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        quit_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_DIM};
                border: 1px solid {C.BORDER}; border-radius: 10px; padding: 0 5px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        quit_btn.clicked.connect(self._hide_quiz)
        hdr.addWidget(quit_btn)
        lay.addLayout(hdr)

        rule = QFrame(); rule.setFixedHeight(1)
        rule.setStyleSheet(f"background: {C.BORDER};")
        lay.addWidget(rule)

        self._quiz_q_lbl = QLabel("")
        self._quiz_q_lbl.setWordWrap(True)
        self._quiz_q_lbl.setFont(QFont(FONT_UI, 9))
        self._quiz_q_lbl.setStyleSheet(f"color: {C.WHITE}; background: transparent;")
        lay.addWidget(self._quiz_q_lbl)

        self._quiz_answers = QWidget()
        self._quiz_answers.setStyleSheet("background: transparent;")
        self._quiz_answers_lay = QVBoxLayout(self._quiz_answers)
        self._quiz_answers_lay.setContentsMargins(0, 2, 0, 0)
        self._quiz_answers_lay.setSpacing(4)
        lay.addWidget(self._quiz_answers)

        self._quiz_note_lbl = QLabel("")
        self._quiz_note_lbl.setWordWrap(True)
        self._quiz_note_lbl.setFont(QFont(FONT_UI, 8))
        self._quiz_note_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        self._quiz_note_lbl.hide()
        lay.addWidget(self._quiz_note_lbl)

        foot = QHBoxLayout()
        foot.addStretch()
        self._quiz_next_btn = self._quiz_btn("NEXT  →", primary=True)
        self._quiz_next_btn.setFixedWidth(110)
        self._quiz_next_btn.clicked.connect(self._quiz_next)
        self._quiz_next_btn.hide()
        foot.addWidget(self._quiz_next_btn)
        lay.addLayout(foot)

        self._quiz = None
        return w

    def _show_quiz(self, topic: str, questions, grader=None):
        """Slot — Qt main thread. Puts a fresh quiz on the board."""
        if not questions:
            return
        self._quiz = {
            "topic": topic or "",
            "questions": list(questions),
            "grader": grader,
            "i": 0,
            "results": [],
            "answered": False,
        }
        self._quiz_title_lbl.setText((topic or "quiz").upper()[:48])
        self.hud.glance(0.0, -0.85, hold=1.3)
        self._home_stack.setCurrentIndex(2)
        self._quiz_render()

    def _hide_quiz(self):
        self._quiz = None
        self._home_stack.setCurrentIndex(0)
        self._refresh_tab_btns()

    def _quiz_clear_answers(self):
        while self._quiz_answers_lay.count():
            item = self._quiz_answers_lay.takeAt(0)
            child = item.widget()
            if child is not None:
                child.setParent(None)
                child.deleteLater()

    def _quiz_render(self):
        q = self._quiz["questions"][self._quiz["i"]]
        n, total = self._quiz["i"] + 1, len(self._quiz["questions"])
        self._quiz_count_lbl.setText(f"{n} / {total}")
        self._quiz_q_lbl.setText(q.get("question", ""))
        self._quiz_note_lbl.hide()
        self._quiz_next_btn.hide()
        self._quiz["answered"] = False
        self._quiz_clear_answers()

        opts = q.get("options") or []
        if opts:
            for text in opts:
                b = self._quiz_btn("   " + text)
                b.clicked.connect(lambda _=False, t=text: self._quiz_submit(t))
                self._quiz_answers_lay.addWidget(b)
        else:
            row = QWidget(); row.setStyleSheet("background: transparent;")
            h = QHBoxLayout(row); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(6)
            field = QLineEdit()
            field.setFont(QFont(FONT_UI, 9))
            field.setPlaceholderText("your answer")
            field.setStyleSheet(f"""
                QLineEdit {{
                    background: {C.PANEL2}; color: {C.WHITE};
                    border: 1px solid {C.BORDER}; border-radius: 10px; padding: 4px 7px;
                }}
                QLineEdit:focus {{ border-color: {C.PRI_DIM}; }}
            """)
            send = self._quiz_btn("ANSWER", primary=True)
            send.setFixedWidth(90)
            field.returnPressed.connect(lambda: self._quiz_submit(field.text()))
            send.clicked.connect(lambda: self._quiz_submit(field.text()))
            h.addWidget(field, stretch=1)
            h.addWidget(send)
            self._quiz_answers_lay.addWidget(row)
            field.setFocus()

    def _quiz_submit(self, given: str):
        if self._quiz is None or self._quiz["answered"]:
            return
        self._quiz["answered"] = True
        q = self._quiz["questions"][self._quiz["i"]]
        grader = self._quiz.get("grader")
        verdict = None
        if callable(grader):
            try:
                verdict = grader(q, given)
            except Exception:
                verdict = None
        self._quiz["results"].append({
            "question": q.get("question", ""),
            "type": q.get("type", ""),
            "given": str(given or "").strip(),
            "answer": q.get("answer", ""),
            "correct": verdict,
        })

        for i in range(self._quiz_answers_lay.count()):
            wdg = self._quiz_answers_lay.itemAt(i).widget()
            if wdg is not None:
                wdg.setEnabled(False)

        if verdict is True:
            mark, colour = "✓  correct", C.GREEN
        elif verdict is False:
            mark, colour = "✕  " + str(q.get("answer", "")), C.RED
        else:
            # Open answers and near-miss gap-fills are JARVIS's to judge. Saying
            # so is honest; marking it wrong here would be a guess.
            mark, colour = "…  noted — I'll go over this one with you", C.ACC2
        note = q.get("note") or ""
        self._quiz_note_lbl.setText(mark + (("\n" + note) if note else ""))
        self._quiz_note_lbl.setStyleSheet(f"color: {colour}; background: transparent;")
        self._quiz_note_lbl.show()

        last = self._quiz["i"] >= len(self._quiz["questions"]) - 1
        self._quiz_next_btn.setText("FINISH  →" if last else "NEXT  →")
        self._quiz_next_btn.show()
        self._quiz_next_btn.setFocus()

    def _quiz_next(self):
        if self._quiz is None:
            return
        if self._quiz["i"] >= len(self._quiz["questions"]) - 1:
            self._quiz_finish()
        else:
            self._quiz["i"] += 1
            self._quiz_render()

    def _quiz_finish(self):
        if self._quiz is None:
            return
        topic = self._quiz["topic"]
        results = self._quiz["results"]
        right = sum(1 for r in results if r["correct"] is True)
        unsure = sum(1 for r in results if r["correct"] is None)
        total = len(results)
        self._quiz_panel.hide()
        self._quiz = None

        self._log.append_log(f"QUIZ: {topic or 'quiz'} — {right}/{total} correct")

        # Hand it back to JARVIS as a message, not as a tool return: the tool
        # call ended minutes ago. This is the same channel a dropped file uses.
        lines = [f"[QUIZ_DONE] topic={topic or 'general'} | "
                 f"auto-marked {right}/{total} correct"
                 + (f", {unsure} still need your marking" if unsure else "")]
        for i, r in enumerate(results, 1):
            state = ("correct" if r["correct"] is True
                     else "wrong" if r["correct"] is False else "NEEDS MARKING")
            lines.append(
                f"{i}. [{r['type']}] {r['question']} | they answered: "
                f"{r['given'] or '(blank)'} | expected: {r['answer']} | {state}")
        lines.append(
            "Mark every question flagged NEEDS MARKING yourself — accept an answer "
            "that means the same thing. Then tell them how they did in their own "
            "language: the score, what they got wrong and why, in a couple of "
            "sentences. Offer another round only if it fits. "
            "Remember something only if it would still matter next week — that they "
            "are working through a subject, or keep missing the same thing. A score "
            "from one session is not worth a memory, and a memory per quiz would "
            "bury the things that are.")
        msg = "\n".join(lines)
        if self.on_text_command:
            threading.Thread(target=self.on_text_command, args=(msg,), daemon=True).start()

    def _build_footer(self) -> QWidget:
        w = QWidget()
        w.setFixedHeight(24)
        w.setStyleSheet(f"background: {C.DARK}; border-top: 1px solid {C.BORDER};")
        lay = QHBoxLayout(w); lay.setContentsMargins(18, 0, 18, 0)

        def _fl(txt, color=C.TEXT_DIM):
            l = QLabel(txt); l.setFont(QFont(FONT_UI, 7))
            l.setStyleSheet(f"color: {color}; background: transparent;")
            return l

        lay.addWidget(_fl("F4 Mute   \u00B7   F11 Fullscreen   \u00B7   Win+Shift+J Summon"))
        lay.addStretch()
        lay.addWidget(_fl(f"{self._assistant_name}  \u00B7  {APP_VERSION}", C.TEXT_MED))
        return w

    def _on_file_selected(self, path: str):
        self._current_file = path
        p    = Path(path)
        cat  = _file_category(p)
        icon, _ = _FILE_ICONS.get(cat, _FILE_ICONS["unknown"])
        size = _fmt_size(p.stat().st_size)
        if hasattr(self, "_file_hint"):
            self._file_hint.setText(
                f"{icon}  {p.name}  ·  {size}  ·  Tell {self._assistant_name} what to do with it")
            self._file_hint.show()
        self._log.append_log(f"FILE: {p.name} ({size}) loaded")
        if self.on_text_command:
            msg = (
                f"[FILE_UPLOADED] path={path} | name={p.name} | "
                f"type={p.suffix.lstrip('.')} | size={size} | "
                f"Briefly tell the user you can see the file '{p.name}' "
                f"({size}) has been uploaded and ask what they'd like to do with it."
            )
            threading.Thread(target=self.on_text_command, args=(msg,), daemon=True).start()

    def notify_phone_connected(self) -> None:
        if self._remote_overlay and self._remote_overlay.isVisible():
            self._remote_overlay.mark_connected()

    def _open_remote(self):
        if not self.on_remote_clicked:
            self._log.append_log("SYS: Dashboard not running — remote unavailable.")
            return
        result = self.on_remote_clicked()
        if not result:
            self._log.append_log("SYS: Could not generate remote key.")
            return
        url    = result[0]
        key    = result[1]
        auto   = result[2] if len(result) >= 3 else ""
        manual = result[3] if len(result) >= 4 else url
        if self._remote_overlay:
            self._remote_overlay._do_close()
        cw  = self.centralWidget()
        ow, oh = RemoteKeyOverlay._OW, RemoteKeyOverlay._OH
        ov  = RemoteKeyOverlay(url, key, auto_login_url=auto, manual_url=manual,
                               expiry_secs=600, parent=cw)
        ov.set_new_key_callback(self.on_remote_clicked)
        ov.setGeometry(
            (cw.width()  - ow) // 2,
            (cw.height() - oh) // 2,
            ow, oh,
        )
        ov.closed.connect(lambda: setattr(self, '_remote_overlay', None))
        ov.show()
        self._remote_overlay = ov
        self._log.append_log(f"SYS: Remote key generated — manual: {manual or url}")

    # ── Auto-start ──────────────────────────────────────────────────────────────

    def _check_autostart(self) -> bool:
        """Returns True if auto-start is currently registered on this OS."""
        try:
            if _OS == "Windows":
                import winreg
                key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_READ)
                try:
                    winreg.QueryValueEx(key, "JARVIS_AI")
                    return True
                except FileNotFoundError:
                    return False
                finally:
                    winreg.CloseKey(key)
            elif _OS == "Darwin":
                return (Path.home() / "Library" / "LaunchAgents"
                        / "com.jarvis.assistant.plist").exists()
            else:
                return (Path.home() / ".config" / "autostart" / "jarvis.desktop").exists()
        except Exception:
            return False

    def _toggle_autostart(self):
        currently_on = self._check_autostart()
        try:
            script = str(Path(__file__).resolve().parent / "main.py")
            if _OS == "Windows":
                import winreg
                reg = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_ALL_ACCESS)
                if currently_on:
                    winreg.DeleteValue(reg, "JARVIS_AI")
                else:
                    pythonw = Path(sys.executable).parent / "pythonw.exe"
                    exe = str(pythonw if pythonw.exists() else sys.executable)
                    winreg.SetValueEx(reg, "JARVIS_AI", 0, winreg.REG_SZ,
                                      f'"{exe}" "{script}"')
                winreg.CloseKey(reg)
            elif _OS == "Darwin":
                plist_dir = Path.home() / "Library" / "LaunchAgents"
                plist_dir.mkdir(parents=True, exist_ok=True)
                plist = plist_dir / "com.jarvis.assistant.plist"
                if currently_on:
                    plist.unlink(missing_ok=True)
                else:
                    plist.write_text(
                        '<?xml version="1.0" encoding="UTF-8"?>\n'
                        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
                        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                        '<plist version="1.0"><dict>\n'
                        '  <key>Label</key><string>com.jarvis.assistant</string>\n'
                        '  <key>ProgramArguments</key><array>\n'
                        f'    <string>{sys.executable}</string>\n'
                        f'    <string>{script}</string>\n'
                        '  </array>\n'
                        '  <key>RunAtLoad</key><true/>\n'
                        '</dict></plist>\n'
                    )
            else:
                desk_dir = Path.home() / ".config" / "autostart"
                desk_dir.mkdir(parents=True, exist_ok=True)
                desk = desk_dir / "jarvis.desktop"
                if currently_on:
                    desk.unlink(missing_ok=True)
                else:
                    desk.write_text(
                        "[Desktop Entry]\n"
                        f"Name={self._assistant_name}\n"
                        f"Exec={sys.executable} {script}\n"
                        "Type=Application\nTerminal=false\n"
                        "X-GNOME-Autostart-enabled=true\n"
                    )
            enabled = not currently_on
            self._update_autostart_btn(enabled)
            self._log.append_log(
                f"SYS: Auto-start {'enabled' if enabled else 'disabled'}.")
        except Exception as e:
            self._log.append_log(f"ERR: Auto-start failed — {e}")

    def _update_autostart_btn(self, enabled: bool):
        if not hasattr(self, '_autostart_btn'):
            return
        if enabled:
            self._autostart_btn.setText("◉  AUTO-START: ON")
            self._autostart_btn.setStyleSheet(f"""
                QPushButton {{
                    background: #07201c; color: {C.GREEN};
                    border: 1px solid {C.GREEN_D}; border-radius: 10px;
                }}
                QPushButton:hover {{ background: #0a2a24; }}
            """)
        else:
            self._autostart_btn.setText("◉  AUTO-START: OFF")
            self._autostart_btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {C.TEXT_DIM};
                    border: 1px solid {C.BORDER}; border-radius: 10px;
                }}
                QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
            """)

    def _toggle_brief(self):
        from memory.config_manager import get_brief_enabled, save_brief_enabled
        new_val = not get_brief_enabled()
        save_brief_enabled(new_val)
        self._update_brief_btn(new_val)

    # ── Wake word settings ───────────────────────────────────────────────────

    def _wake_state(self) -> dict:
        """Combined state for the two wake-word buttons. Readiness is a cheap,
        deterministic on-disk check now (see core.wake_word.is_ready), so there
        is nothing to cache — the button never flickers to a stale value."""
        if self.wake_get_state:
            try:
                s = self.wake_get_state()
                return {"ready": bool(s.get("ready")),
                        "enabled": bool(s.get("enabled")),
                        "awake": bool(s.get("awake"))}
            except Exception:
                pass
        # Before JarvisLive has wired its callback (drawer built at startup).
        ready, enabled = False, False
        try:
            from core.wake_word import is_ready
            from memory.config_manager import get_wake_word_enabled
            ready, enabled = is_ready(), get_wake_word_enabled()
        except Exception:
            pass
        return {"ready": ready, "enabled": enabled, "awake": True}

    def _refresh_wake_btns(self):
        if not hasattr(self, '_wake_btn'):
            return
        st = self._wake_state()
        _on = f"""
            QPushButton {{ background: #07201c; color: {C.GREEN};
                border: 1px solid {C.GREEN_D}; border-radius: 10px;
                text-align: left; padding: 0 8px; }}
            QPushButton:hover {{ background: #0a2a24; }}"""
        _off = f"""
            QPushButton {{ background: transparent; color: {C.TEXT_DIM};
                border: 1px solid {C.BORDER}; border-radius: 10px;
                text-align: left; padding: 0 8px; }}
            QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}"""
        self._wake_btn.setEnabled(True)
        if not st["ready"]:
            self._wake_btn.setText("⬇  WAKE WORD: DOWNLOAD")
            self._wake_btn.setStyleSheet(_off)
            self._wake_sleep_btn.hide()
        elif st["enabled"]:
            self._wake_btn.setText("🎙  WAKE WORD: ON")
            self._wake_btn.setStyleSheet(_on)
            self._wake_sleep_btn.show()
            self._wake_sleep_btn.setText("😴  SLEEP NOW" if st["awake"] else "👂  WAKE NOW")
            self._wake_sleep_btn.setStyleSheet(_off)
        else:
            self._wake_btn.setText("🎙  WAKE WORD: OFF")
            self._wake_btn.setStyleSheet(_off)
            self._wake_sleep_btn.hide()

    def _refresh_talk_btns(self):
        """Repaint the push-to-talk row from the saved setting."""
        if not hasattr(self, "_ptt_btn"):
            return
        from core.hotkey import chord_label
        from memory.config_manager import get_push_to_talk_enabled
        _on = f"""
            QPushButton {{ background: #07201c; color: {C.GREEN};
                border: 1px solid {C.GREEN_D}; border-radius: 10px;
                text-align: left; padding: 0 8px; }}
            QPushButton:hover {{ background: #0a2a24; }}"""
        _off = f"""
            QPushButton {{ background: transparent; color: {C.TEXT_DIM};
                border: 1px solid {C.BORDER}; border-radius: 10px;
                text-align: left; padding: 0 8px; }}
            QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}"""

        ptt = get_push_to_talk_enabled()
        self._ptt_btn.setText(f"🎚  PUSH-TO-TALK: {chord_label()}" if ptt
                              else "🎚  PUSH-TO-TALK: OFF")
        self._ptt_btn.setStyleSheet(_on if ptt else _off)
        self._ptt_btn.setToolTip(
            "Microphone stays closed until you hold the key — nothing is sent "
            "while you are not holding it." if ptt
            else "Hold a key to talk instead of streaming the mic continuously.")


    def _refresh_hud_btn(self):
        from memory.config_manager import get_hud_style
        face = get_hud_style() == "face"
        # Neither state is "off", so both read as active — this is a choice
        # between two things, not a switch with a disabled side.
        style = f"""
            QPushButton {{ background: {C.PANEL2}; color: {C.PRI};
                border: 1px solid {C.BORDER_A}; border-radius: 10px;
                text-align: left; padding: 0 8px; }}
            QPushButton:hover {{ color: {C.WHITE}; border: 1px solid {C.BORDER_B}; }}"""
        self._hud_btn.setText("🧑  HUD: ANIMATED FACE" if face
                              else "◉  HUD: REACTOR CORE")
        self._hud_btn.setStyleSheet(style)
        self._hud_btn.setToolTip(
            "An animated head that speaks your words and shows what JARVIS is "
            "doing. Tap to switch to the reactor core."
            if face else
            "A reactor core that turns with the state and moves with your voice. "
            "Tap to switch to the animated head.")

    def _toggle_hud_style(self):
        """Swap the centrepiece. Both objects stay in memory, so the change is
        instant and switching back costs nothing."""
        from memory.config_manager import get_hud_style, save_hud_style
        want = "core" if get_hud_style() == "face" else "face"
        save_hud_style(want)
        try:
            self.hud.hud_style = want
            self.hud.update()
        except Exception:
            pass
        self._refresh_hud_btn()
        self._log.append_log(
            "SYS: HUD switched to the animated face." if want == "face"
            else "SYS: HUD switched to the reactor core.")

    def _toggle_ptt(self):
        from memory.config_manager import (get_push_to_talk_enabled,
                                           save_push_to_talk_enabled)
        want = not get_push_to_talk_enabled()
        save_push_to_talk_enabled(want)
        scope = None
        if self.on_push_to_talk:
            try:
                scope = self.on_push_to_talk(want)
            except Exception as e:
                self._log.append_log(f"ERR: Push-to-talk failed — {e}")
                save_push_to_talk_enabled(False)
                want = False
        self._apply_ptt_shortcut(want and scope != "global")
        self._refresh_talk_btns()

    def _apply_ptt_shortcut(self, needed: bool):
        """Bind the chord inside the window when no global hook is available.

        On macOS and Linux there is no dependency-free way to read global key
        state, so the chord is at least live whenever this window has focus.
        Qt gives no key-release for a QShortcut, so a press latches the mic open
        and a short timer closes it; held down, auto-repeat keeps pushing that
        timer out, which behaves like holding a key.
        """
        from PyQt6.QtGui import QKeySequence, QShortcut
        from core.hotkey import qt_sequence

        if not needed:
            sc = getattr(self, "_ptt_sc", None)
            if sc is not None:
                sc.setEnabled(False)
                self._ptt_sc = None
            self._ptt_hold(False)
            return
        if getattr(self, "_ptt_sc", None) is not None:
            return

        self._ptt_release = QTimer(self)
        self._ptt_release.setSingleShot(True)
        self._ptt_release.setInterval(420)
        self._ptt_release.timeout.connect(lambda: self._ptt_hold(False))

        def _press():
            self._ptt_hold(True)
            self._ptt_release.start()

        self._ptt_sc = QShortcut(QKeySequence(qt_sequence()), self)
        self._ptt_sc.setAutoRepeat(True)
        self._ptt_sc.activated.connect(_press)

    def _ptt_hold(self, held: bool):
        """Report a windowed press/release to whoever owns the microphone."""
        cb = getattr(self, "ptt_hold", None)
        if cb:
            try:
                cb(bool(held))
            except Exception:
                pass

    def _toggle_wake_word(self):
        st = self._wake_state()
        if not st["ready"]:
            # First time: download openwakeword + model in a worker thread.
            self._wake_btn.setText("⬇  DOWNLOADING… (one-time)")
            self._wake_btn.setEnabled(False)
            def _work():
                try:
                    from core.wake_word import install_and_download
                    ok, msg = install_and_download(
                        logger=lambda m: self._log_sig.emit(f"SYS: {m}"))
                except Exception as e:
                    ok, msg = False, str(e)
                if ok and self.on_wake_toggle:
                    try:
                        self.on_wake_toggle(True)   # auto-enable after a successful download
                    except Exception:
                        pass
                self._wake_dl_sig.emit(ok, msg)
            threading.Thread(target=_work, daemon=True).start()
            return
        # Already downloaded → just flip enabled/disabled through JarvisLive.
        if self.on_wake_toggle:
            try:
                self.on_wake_toggle(not st["enabled"])
            except Exception:
                pass
        self._refresh_wake_btns()

    def _on_wake_install_done(self, ok: bool, msg: str):
        self._log_sig.emit(f"SYS: {'Wake word ready.' if ok else 'Wake word setup failed: ' + msg}")
        self._refresh_wake_btns()

    def _tap_wake_manual(self):
        if self.on_wake_manual:
            try:
                self.on_wake_manual()
            except Exception:
                pass
        self._refresh_wake_btns()

    def _update_brief_btn(self, enabled: bool):
        if not hasattr(self, '_brief_btn'):
            return
        if enabled:
            self._brief_btn.setText("☀  MORNING BRIEF: ON")
            self._brief_btn.setStyleSheet(f"""
                QPushButton {{
                    background: #07201c; color: {C.GREEN};
                    border: 1px solid {C.GREEN_D}; border-radius: 10px;
                    text-align: left; padding: 0 8px;
                }}
                QPushButton:hover {{ background: #0a2a24; }}
            """)
        else:
            self._brief_btn.setText("☀  MORNING BRIEF: OFF")
            self._brief_btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {C.TEXT_DIM};
                    border: 1px solid {C.BORDER}; border-radius: 10px;
                    text-align: left; padding: 0 8px;
                }}
                QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
            """)

    # ── Customization ────────────────────────────────────────────────────────────

    def _open_customize(self):
        cfg = _read_full_config()
        if self._customize_overlay:
            self._customize_overlay.hide()
        cw = self.centralWidget()
        ov = CustomizeOverlay(
            cfg.get("assistant_name", "JARVIS") or "JARVIS",
            cfg.get("user_name", ""),
            cfg.get("ui_color", "") or DEFAULT_UI_COLOR,
            cfg.get("voice_name", ""),
            parent=cw,
        )
        ow, oh = CustomizeOverlay._OW, CustomizeOverlay._OH
        oh = min(oh, cw.height() - 16)
        ov.setGeometry(
            (cw.width()  - ow) // 2,
            (cw.height() - oh) // 2,
            ow, oh,
        )
        ov.on_preview = self._preview_ui_color
        ov.saved.connect(self._apply_name_update)
        ov.show()
        self._customize_overlay = ov

    def _open_api_keys(self):
        try:
            cfg = _read_api_cfg()
            gkey = _coerce_key(cfg.get("gemini_api_key"))
            rows = _sanitize_provider_rows(cfg.get("free_providers"))
        except Exception as e:
            print(f"[API-KEYS] config load failed: {e}")
            gkey, rows = "", []
        try:
            if self._api_keys_overlay:
                self._api_keys_overlay.deleteLater()
            cw = self.centralWidget()
            ov = ApiKeysOverlay(gemini_key=gkey, providers=rows, parent=cw)
            ow, oh = ApiKeysOverlay._OW, ApiKeysOverlay._OH
            oh = min(oh, cw.height() - 16)
            ov.setGeometry(
                (cw.width()  - ow) // 2,
                (cw.height() - oh) // 2,
                ow, oh,
            )
            ov.saved.connect(self._save_api_keys)
            ov.show()
            ov.raise_()
            self._api_keys_overlay = ov
        except Exception as e:
            print(f"[API-KEYS] overlay failed: {e}")
            self._log_sig.emit(f"ERR: API keys panel failed to open — {e}")

    def _save_api_keys(self, gemini_key: str, providers: list):
        """Merge-save keys on a daemon thread so the UI never blocks; a queued
        signal reports the finish on the main thread."""
        try:
            gkey = gemini_key if isinstance(gemini_key, str) else ""
            rows = _sanitize_provider_rows(providers)
        except Exception as e:
            self._log_sig.emit(f"ERR: API keys save rejected — {e}")
            return

        def _work():
            try:
                n = _write_api_keys_impl(gkey, rows)
                try:
                    from core import free_providers
                    free_providers.reset_cooldowns()
                except Exception:
                    pass
                try:
                    from core import gemini
                    gemini.api_key(refresh=True)
                except Exception:
                    pass
                self._log_sig.emit(
                    f"SYS: API keys saved — Gemini "
                    f"{'set' if gkey else 'unchanged'}, {n} free fallback key(s).")
            except Exception as e:
                self._log_sig.emit(f"ERR: API keys save failed — {e}")

        self._api_keys_thread = threading.Thread(target=_work, daemon=True)
        self._api_keys_thread.start()

    def _preview_ui_color(self, hex_color: str):
        """Live preview — paints the whole interface the new colour (does NOT write to config)."""
        old = current_palette()
        if apply_ui_accent(hex_color):
            retheme_all_widgets(old, current_palette())

    def _apply_name_update(self, name: str, user_name: str, ui_color: str = "",
                           voice: str = ""):
        """Update all name/theme-dependent UI elements and persist to config."""
        self._assistant_name = name.strip() or "JARVIS"
        display = self._assistant_name.upper()
        self.setWindowTitle(f"{display} — {APP_VERSION}")
        self._title_lbl.setText(display)
        if display in ("JARVIS", "J.A.R.V.I.S"):
            self._sub_lbl.setText("Just A Rather Very Intelligent System")
        else:
            self._sub_lbl.setText("Personal AI Assistant")
        self._log._ai_name_lc = self._assistant_name.lower()
        self.hud._assistant_name = display

        color_changed = False
        if ui_color:
            old = current_palette()
            if apply_ui_accent(ui_color):
                # Live-paint the whole interface (panels, buttons, borders, HUD)
                retheme_all_widgets(old, current_palette())
                color_changed = old["PRI"] != C.PRI

        # Voice change → persist and, if it actually changed, rebuild the Live
        # session so the new voice takes effect (it's fixed at connect time).
        voice_changed = False
        if voice:
            from memory.config_manager import get_voice, save_voice
            if voice != get_voice():
                save_voice(voice)
                voice_changed = True

        try:
            data = _read_full_config()
            data["assistant_name"] = self._assistant_name
            data["user_name"] = user_name.strip()
            if ui_color:
                data["ui_color"] = ui_color.strip().lower()
            API_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")
            self._log.append_log(f"SYS: Identity updated — {display}")
            if color_changed:
                self._log.append_log(f"SYS: UI colour applied — {ui_color}")
            if voice_changed:
                self._log.append_log(f"SYS: Voice set — {voice}")
        except Exception as e:
            self._log.append_log(f"ERR: Config save failed — {e}")

        if voice_changed and self.on_voice_change:
            self.on_voice_change()

    def _centre_overlay(self, ov) -> None:
        """Place a floating overlay in the middle of the HUD and show it."""
        cw = self.centralWidget()
        ov.adjustSize()
        ov.setGeometry(
            max(0, (cw.width()  - ov.width())  // 2),
            max(0, (cw.height() - ov.height()) // 2),
            ov.width(), ov.height(),
        )
        ov.show()
        ov.raise_()

    # ── Audio devices ────────────────────────────────────────────────────────

    def _open_audio_devices(self):
        ov = AudioDeviceOverlay(parent=self.centralWidget())
        ov.picked.connect(self._on_audio_devices_applied)
        self._centre_overlay(ov)
        self._audio_overlay = ov            # keep a reference so it isn't GC'd

    def _on_audio_devices_applied(self):
        self._log.append_log("SYS: Audio devices updated.")
        if self.on_audio_device_change:
            self.on_audio_device_change()

    # ── Memory panel ─────────────────────────────────────────────────────────

    def _open_memory_panel(self):
        ov = MemoryOverlay(parent=self.centralWidget())
        self._centre_overlay(ov)
        self._memory_overlay = ov

    # ── Irreversible-action confirmation ─────────────────────────────────────

    def _show_confirm_banner(self, title: str, detail: str):
        self._hide_confirm_banner()
        ov = ConfirmBanner(title, detail, parent=self.centralWidget())
        ov.answered.connect(self._on_confirm_answered)
        self._centre_overlay(ov)
        self._confirm_overlay = ov

    def _hide_confirm_banner(self):
        ov = getattr(self, "_confirm_overlay", None)
        if ov is not None:
            ov.hide()
            ov.deleteLater()
            self._confirm_overlay = None

    def _on_confirm_answered(self, accepted: bool):
        # Tear the banner down first: core.confirm.resolve() may be about to
        # shut the machine down, and a live widget mid-callback is not where you
        # want to be when that happens.
        self._hide_confirm_banner()
        try:
            from core.confirm import resolve
            resolve(bool(accepted))
        except Exception as e:
            self._log.append_log(f"ERR: Confirmation failed — {e}")

    def _open_plugin_manager(self):
        plugins = self.get_plugins() if self.get_plugins else []
        cw = self.centralWidget()
        ov = PluginManagerOverlay(plugins, parent=cw)
        ov.adjustSize()
        ov.setGeometry(
            (cw.width()  - ov.width())  // 2,
            (cw.height() - ov.height()) // 2,
            ov.width(), ov.height(),
        )
        ov.show()
        ov.raise_()
        self._plugin_manager_overlay = ov   # keep a reference so it isn't GC'd

    def _open_plugin_settings(self):
        sections = self.get_plugin_settings() if self.get_plugin_settings else []
        cw = self.centralWidget()
        ov = PluginSettingsOverlay(sections, parent=cw)
        ow = PluginSettingsOverlay._OW
        oh = min(560, cw.height() - 16)
        ov.setGeometry(
            (cw.width()  - ow) // 2,
            (cw.height() - oh) // 2,
            ow, oh,
        )
        ov.show()
        ov.raise_()
        self._plugin_settings_overlay = ov   # keep a reference so it isn't GC'd

    # ── Clipboard intelligence ───────────────────────────────────────────────────

    def _on_clipboard_changed(self):
        try:
            text = QApplication.clipboard().text().strip()
            if len(text) >= 10:
                self._clipboard_sig.emit(text)
        except Exception:
            pass

    def _show_clipboard_panel(self, text: str):
        self._clipboard_panel.show_clipboard(text)
        self._position_clipboard_panel()

    def _position_clipboard_panel(self):
        cw = self.centralWidget()
        pw = ClipboardPanel._W
        ph = self._clipboard_panel.sizeHint().height() or ClipboardPanel._H
        x = (cw.width() - pw) // 2
        y = cw.height() - ph - 6
        self._clipboard_panel.setGeometry(x, y, pw, ph)
        self._clipboard_panel.raise_()

    def _on_clipboard_action(self, cmd: str):
        if self.on_text_command:
            threading.Thread(target=self.on_text_command, args=(cmd,), daemon=True).start()

    # ────────────────────────────────────────────────────────────────────────────

    def _do_interrupt(self):
        if self.on_interrupt:
            self.on_interrupt()

    def _toggle_mute(self):
        self._muted = not self._muted
        self.hud.muted = self._muted
        if not hasattr(self, '_mic_btn'):
            return
        self._style_mute_btn()
        if self._muted:
            self._apply_state("MUTED")
            self._log.append_log("SYS: Microphone muted.")
        else:
            self._apply_state("LISTENING")
            self._log.append_log("SYS: Microphone active.")

    def _style_mute_btn(self):
        if self._muted:
            self._mic_btn.setText("\U0001F507")
            self._mic_btn.setToolTip("Microphone muted \u2014 tap to unmute  [F4]")
            self._mic_btn.setStyleSheet(f"""
                QPushButton {{
                    background: #2a0d16; color: {C.MUTED_C};
                    border: 1px solid {C.MUTED_C}; border-radius: 21px;
                }}
                QPushButton:hover {{ background: #330f1c; }}
            """)
        else:
            self._mic_btn.setText("\U0001F399")
            self._mic_btn.setToolTip("Microphone \u2014 toggle mute  [F4]")
            self._mic_btn.setStyleSheet(f"""
                QPushButton {{
                    background: {C.PANEL}; color: {C.GREEN};
                    border: 1px solid {C.GREEN_D}; border-radius: 21px;
                }}
                QPushButton:hover {{ background: {C.PRI_GHO};
                                    border: 1px solid {C.GREEN}; }}
            """)

    def _send(self):
        txt = self._input.text().strip()
        if not txt: return
        self._input.clear()
        if getattr(self, "_current_file", None):
            self._current_file = None
            if hasattr(self, "_file_hint"):
                self._file_hint.hide()
        self._log.append_log(f"You: {txt}")
        if self.on_text_command:
            threading.Thread(target=self.on_text_command, args=(txt,), daemon=True).start()

    def _apply_state(self, state: str):
        self.hud.state    = state
        self.hud.speaking = (state == "SPEAKING")
        if hasattr(self, '_face_status'):
            self._face_status.setText(state)
            self._face_status.setStyleSheet(_pill_style(
                _state_colour(state), C.PANEL2, C.BORDER_A))
        if hasattr(self, '_state_pill'):
            self._state_pill.setText(f"◉  {state}")
            self._state_pill.setStyleSheet(_pill_style(
                _state_colour(state), C.PANEL2, C.BORDER_A))
        try:
            if getattr(self, "_hud_paused", False):
                self._set_hud_paused(False)
        except Exception:
            pass

    def _set_hud_paused(self, paused: bool) -> None:
        """Globally freeze/unfreeze the face engine + UI timers. Called while
        the window is hidden (background mode) so a low-end machine burns ~0%
        CPU until you call the app back."""
        self._hud_paused = paused
        try:
            if paused:
                self.hud.pause()
                self._clock_tmr.stop()
                self._metric_tmr.stop()
            else:
                self.hud.resume()
                if getattr(self, "_clock_tmr", None):
                    self._clock_tmr.start(1000)
                if getattr(self, "_metric_tmr", None):
                    self._metric_tmr.start(2000)
        except Exception:
            pass

    def _check_config(self) -> bool:
        if not API_FILE.exists(): return False
        try:
            d = json.loads(API_FILE.read_text(encoding="utf-8"))
            return bool(d.get("gemini_api_key")) and bool(d.get("os_system"))
        except Exception:
            return False

    def _show_setup(self):
        ov = SetupOverlay(self.centralWidget())
        cw = self.centralWidget()
        ow, oh = 520, 560
        ov.setGeometry(
            (cw.width()  - ow) // 2,
            (cw.height() - oh) // 2,
            ow, oh,
        )
        ov.done.connect(self._on_setup_done)
        ov.show()
        self._overlay = ov

    def _on_setup_done(self, key: str, os_name: str, providers: list):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        # Merge — never overwrite — so calling this again after an auth error
        # keeps every other setting the user has made.
        data: dict = {}
        if API_FILE.exists():
            try:
                data = json.loads(API_FILE.read_text(encoding="utf-8"))
            except Exception:
                data = {}
        data["gemini_api_key"] = key
        data["os_system"] = os_name
        data["free_providers"] = providers
        API_FILE.write_text(
            json.dumps(data, indent=4, ensure_ascii=False),
            encoding="utf-8",
        )
        self._ready = True
        if self._overlay:
            self._overlay.hide()
            self._overlay = None
        self._apply_state("LISTENING")
        self._assistant_name = _read_full_config().get("assistant_name", "JARVIS") or "JARVIS"
        self._log.append_log(f"SYS: Initialised. OS={os_name.upper()}. {self._assistant_name} online.")


class _RootShim:
    def __init__(self, app: QApplication):
        self._app = app
    def mainloop(self):
        self._app.exec()
    def protocol(self, *_):
        pass


class JarvisUI:
    def __init__(self, face_path: str, size=None):
        self._app = QApplication.instance() or QApplication(sys.argv)
        self._app.setStyle("Fusion")
        self._win = MainWindow(face_path)
        self.root = _RootShim(self._app)
        self._win.show()

    @property
    def muted(self) -> bool:
        return self._win._muted

    @muted.setter
    def muted(self, v: bool):
        if v != self._win._muted:
            self._win._toggle_mute()

    @property
    def current_file(self) -> str | None:
        return self._win._current_file

    @property
    def on_text_command(self):
        return self._win.on_text_command

    @on_text_command.setter
    def on_text_command(self, cb):
        self._win.on_text_command = cb

    @property
    def on_remote_clicked(self):
        return self._win.on_remote_clicked

    @on_remote_clicked.setter
    def on_remote_clicked(self, cb):
        self._win.on_remote_clicked = cb

    @property
    def on_interrupt(self):
        return self._win.on_interrupt

    @on_interrupt.setter
    def on_interrupt(self, cb):
        self._win.on_interrupt = cb

    @property
    def on_voice_change(self):
        return self._win.on_voice_change

    @on_voice_change.setter
    def on_voice_change(self, cb):
        self._win.on_voice_change = cb

    @property
    def on_audio_device_change(self):
        return self._win.on_audio_device_change

    @on_audio_device_change.setter
    def on_audio_device_change(self, cb):
        self._win.on_audio_device_change = cb

    def show_confirm(self, title: str, detail: str) -> None:
        """Thread-safe: raise the irreversible-action gate. Called from action
        handlers running in executor threads, so it goes through a signal."""
        self._win._confirm_sig.emit(str(title)[:120], str(detail)[:300])

    def hide_confirm(self) -> None:
        """Thread-safe: take the gate down."""
        self._win._confirm_hide_sig.emit()

    @property
    def get_plugins(self):
        return self._win.get_plugins

    @get_plugins.setter
    def get_plugins(self, cb):
        self._win.get_plugins = cb

    @property
    def get_plugin_settings(self):
        return self._win.get_plugin_settings

    @get_plugin_settings.setter
    def get_plugin_settings(self, cb):
        self._win.get_plugin_settings = cb

    @property
    def on_wake_toggle(self):
        return self._win.on_wake_toggle

    @on_wake_toggle.setter
    def on_wake_toggle(self, cb):
        self._win.on_wake_toggle = cb

    @property
    def on_wake_manual(self):
        return self._win.on_wake_manual

    @on_wake_manual.setter
    def on_wake_manual(self, cb):
        self._win.on_wake_manual = cb

    @property
    def wake_get_state(self):
        return self._win.wake_get_state

    @wake_get_state.setter
    def wake_get_state(self, cb):
        self._win.wake_get_state = cb

    def set_audio_level(self, level: float) -> None:
        """Thread-safe: feed a 0.0–1.0 live audio level to the HUD waveform.
        Called from the audio threads; a plain float store is atomic under the
        GIL, so no signal/lock is needed for this cosmetic value."""
        try:
            self._win.hud.set_audio_level(level)
        except Exception:
            pass

    def glance(self, dx: float, dy: float, hold: float = 1.1) -> None:
        """Ask the avatar to look somewhere for a moment (see HoloAvatar.glance)."""
        try:
            if self._avatar is not None:
                self._avatar.glance(dx, dy, hold)
        except Exception:
            pass

    @property
    def ptt_hold(self):
        return self._win.ptt_hold

    @ptt_hold.setter
    def ptt_hold(self, cb):
        self._win.ptt_hold = cb

    @property
    def on_push_to_talk(self):
        return self._win.on_push_to_talk

    @on_push_to_talk.setter
    def on_push_to_talk(self, cb):
        self._win.on_push_to_talk = cb

    def push_visemes(self, frames, hop: float, at: float) -> None:
        """Thread-safe: post a schedule of (level, openness, width) mouth frames
        for JARVIS's own speech. `at` is the wall-clock time the batch begins to
        sound, not the time of the call. See HudCanvas.push_visemes()."""
        try:
            self._win.hud.push_visemes(frames, hop, at)
        except Exception:
            pass

    def notify_phone_connected(self) -> None:
        self._win.notify_phone_connected()

    def set_state(self, state: str):
        self._win._state_sig.emit(state)

    def write_log(self, text: str):
        self._win._log_sig.emit(text)

    def wait_for_api_key(self):
        while not self._win._ready:
            time.sleep(0.1)

    def show_content(self, title: str, text: str):
        """Thread-safe: display content in the panel below the HUD."""
        self._win._content_sig.emit(title[:48], text[:4000])

    def show_quiz(self, topic: str, questions, grade=None) -> None:
        """Thread-safe: put an interactive quiz on the board.

        `grade(question, given)` decides each answer — the plugin supplies it so
        the marking rules live with the questions rather than being duplicated
        here. Returning None from it means "JARVIS should judge this one", which
        is how open answers and near-miss gap-fills are handled.

        Returns immediately: the user answers at their own pace and the finished
        result is delivered back through on_text_command.
        """
        self._win._quiz_sig.emit(str(topic or ""), list(questions or []), grade)

    def hide_quiz(self) -> None:
        """Thread-safe: clear any quiz currently on the board."""
        self._win._quiz_hide_sig.emit()

    def show_review(self, title: str, summary: str, findings, unclear=None) -> None:
        """Thread-safe: lay a document review into the panel below the HUD.

        `findings` is a list of {heading, detail, severity, quote, suggestion};
        severity is one of 'serious' / 'caution' / 'note' and decides colour and
        order here, so the caller supplies no styling of its own.
        """
        self._win._review_sig.emit(str(title or ""), str(summary or ""),
                                   list(findings or []), list(unclear or []))

    def prompt_reconfig(self):
        """Thread-safe: show the API key setup overlay (e.g. after an auth error)."""
        self._win._ready = False
        self._win._reconfig_sig.emit()

    def show_camera_frame(self, img_bytes: bytes):
        """Thread-safe: show a webcam frame in the small overlay (screen captures)."""
        self._win._camera_sig.emit(img_bytes)

    def start_camera_stream(self) -> None:
        """Thread-safe: start live camera feed in the full HUD area."""
        self._win.start_camera_stream()

    def stop_camera_stream(self) -> None:
        """Thread-safe: stop the live camera feed."""
        self._win.stop_camera_stream()

    @property
    def assistant_name(self) -> str:
        return self._win._assistant_name

    def start_speaking(self):
        self.set_state("SPEAKING")

    def stop_speaking(self):
        if not self.muted:
            self.set_state("LISTENING")