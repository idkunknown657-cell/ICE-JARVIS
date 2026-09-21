"""
Screen & webcam capture for JARVIS vision.

Provides the two capture entry points main.py uses — `_capture_screen()` and
`_capture_camera()` — plus their helpers (compression, camera auto-detection,
config access). main.py grabs a frame here on demand, then injects it into the
main Gemini Live session; there is no separate vision session here.
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import numpy as np

try:
    import cv2
    _CV2 = True
except ImportError:
    _CV2 = False

try:
    import mss
    import mss.tools
    _MSS = True
except ImportError:
    _MSS = False

try:
    import PIL.Image
    _PIL = True
except ImportError:
    _PIL = False


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


_BASE        = _base_dir()
_CONFIG_PATH = _BASE / "config" / "api_keys.json"


def _load_config() -> dict:
    try:
        return json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_config_key(key: str, value) -> None:
    try:
        cfg = _load_config()
        cfg[key] = value
        _CONFIG_PATH.write_text(json.dumps(cfg, indent=4), encoding="utf-8")
    except Exception as e:
        print(f"[Vision] WARNING: Could not save config key '{key}': {e}")


def _get_os() -> str:
    return _load_config().get("os_system", "windows").lower()


_IMG_MAX_W = 1280
_IMG_MAX_H = 720
_JPEG_Q    = 82


def _compress(img_bytes: bytes, source_format: str = "PNG") -> tuple[bytes, str]:
    if not _PIL:
        return img_bytes, f"image/{source_format.lower()}"

    try:
        img = PIL.Image.open(io.BytesIO(img_bytes)).convert("RGB")
        img.thumbnail((_IMG_MAX_W, _IMG_MAX_H), PIL.Image.BILINEAR)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=_JPEG_Q, optimize=False)
        return buf.getvalue(), "image/jpeg"
    except Exception as e:
        print(f"[Vision] WARNING: Image compress failed: {e}")
        return img_bytes, f"image/{source_format.lower()}"


def _fg_window_center() -> tuple[int, int] | None:
    """Centre of the foreground window's bounding box, or None when that cannot
    be determined (non-Windows, no window, permission)."""
    if _get_os() != "windows":
        return None
    try:
        import ctypes
        import ctypes.wintypes
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        rect = ctypes.wintypes.RECT()
        if not hwnd or not ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
        return ((rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2)
    except Exception:
        return None


def _active_monitor_index(monitors: list) -> int:
    """Return the index (into `monitors[1:]`) of the monitor whose area holds
    the foreground window — so the assistant looks at the screen you are
    actually using, not the primary one. Falls back to monitor 1 (primary)."""
    if len(monitors) < 2:
        return 1
    center = _fg_window_center()
    if center is None:
        return 1
    cx, cy = center
    for i in range(1, len(monitors)):
        m = monitors[i]
        inside = (m["left"] <= cx <= m["left"] + m["width"] and
                  m["top"]  <= cy <= m["top"]  + m["height"])
        if inside:
            return i
    return 1


def _capture_screen(active: bool = True) -> tuple[bytes, str]:
    """Full-desktop capture, JPEG bytes + mime.

    `active=True` frames the monitor the foreground window lives on (Windows),
    so JARVIS sees what the user is actually looking at even on a multi-monitor
    desk. mss first with a per-monitor fallback chain, then Pillow ImageGrab."""

    if _MSS:
        try:
            with mss.mss() as sct:
                monitors = sct.monitors          # [0] = all combined, [1..n] = real screens
                indices  = list(range(1, len(monitors))) or [0]
                if active and len(monitors) > 1:
                    idx = _active_monitor_index(monitors)
                    indices = [idx] + [i for i in indices if i != idx]
                target = None
                for idx in indices:
                    try:
                        shot = sct.grab(monitors[idx])
                        target = monitors[idx]
                        break
                    except Exception:
                        continue
                if target is None:
                    target   = monitors[0]
                    shot     = sct.grab(target)
                png      = mss.tools.to_png(shot.rgb, shot.size)
            return _compress(png, "PNG")
        except Exception as e:
            print(f"[Vision] WARNING: mss capture failed ({e}); falling back to PIL")

    if _PIL:
        try:
            from PIL import ImageGrab          # pillow-core, ships with the app
            buf = io.BytesIO()
            ImageGrab.grab(all_screens=True).convert("RGB").save(
                buf, format="JPEG", quality=_JPEG_Q, optimize=False)
            return buf.getvalue(), "image/jpeg"
        except Exception as e:
            print(f"[Vision] WARNING: PIL ImageGrab capture failed: {e}")

    raise RuntimeError("no screen capture backend available (mss or Pillow)")


def _cv2_backend() -> int:
    """Return the best OpenCV camera backend for the current OS."""
    if not _CV2:
        return 0
    os_name = _get_os()
    if os_name == "windows":
        return cv2.CAP_DSHOW
    if os_name == "mac":
        return cv2.CAP_AVFOUNDATION
    return cv2.CAP_ANY


def _probe_camera(index: int, backend: int, warmup: int = 5) -> bool:

    if not _CV2:
        return False
    cap = cv2.VideoCapture(index, backend)
    if not cap.isOpened():
        cap.release()
        return False
    for _ in range(warmup):
        cap.read()
    ret, frame = cap.read()
    cap.release()
    if not ret or frame is None:
        return False
    return bool(np.mean(frame) > 8)


def _detect_camera_index() -> int:

    backend = _cv2_backend()
    print("[Vision] Auto-detecting camera...")
    for idx in range(6):
        if _probe_camera(idx, backend):
            print(f"[Vision] Camera found at index {idx}")
            _save_config_key("camera_index", idx)
            return idx
        print(f"[Vision] WARNING: Camera index {idx}: no usable frame")

    print("[Vision] WARNING: No camera found - defaulting to index 0")
    _save_config_key("camera_index", 0)
    return 0


def _get_camera_index() -> int:
    cfg = _load_config()
    if "camera_index" in cfg:
        return int(cfg["camera_index"])
    return _detect_camera_index()


def _capture_camera() -> tuple[bytes, str]:
    if not _CV2:
        raise RuntimeError("OpenCV (cv2) is not installed. Run: pip install opencv-python")

    index   = _get_camera_index()
    backend = _cv2_backend()
    cap     = cv2.VideoCapture(index, backend)

    if not cap.isOpened():
        raise RuntimeError(f"Camera index {index} could not be opened.")

    for _ in range(10):
        cap.read()

    ret, frame = cap.read()
    cap.release()

    if not ret or frame is None:
        raise RuntimeError("Camera returned no frame.")

    if _PIL:
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        img = PIL.Image.fromarray(rgb)
        img.thumbnail((_IMG_MAX_W, _IMG_MAX_H), PIL.Image.BILINEAR)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=_JPEG_Q)
        return buf.getvalue(), "image/jpeg"

    _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, _JPEG_Q])
    return buf.tobytes(), "image/jpeg"
