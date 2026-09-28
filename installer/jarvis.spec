# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec — the frozen payload that ICE-Setup.exe installs.

Run it through `python installer/build_installer.py`, not by hand: the build
script is what copies the app's data folders (ui_web/, assets/, plugins/,
dashboard/, …) in beside the exe, because those are read from the filesystem at
runtime rather than imported, and it is what writes the build manifest that
`JARVIS.exe --selftest` verifies.

Two executables come out of one analysis:

  JARVIS.exe        windowed — what the Start Menu and Desktop shortcuts open
  JARVIS-Debug.exe  the same code with a console attached, for troubleshooting

They share one `_internal/` folder, so the debug build costs a second exe header
rather than a second copy of the runtime.
"""
import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_dynamic_libs, collect_submodules

# SPECPATH is the folder holding this spec (installer/); ROOT is the project.
ROOT = Path(SPECPATH).resolve().parent
ENTRY = str(ROOT / "installer" / "entry.py")
HOOKS = str(ROOT / "installer" / "hooks")

ICON = ROOT / "assets" / "jarvis.ico"
ICON = str(ICON) if ICON.exists() else None

# Written by build_installer.py from VERSION + the git commit, so the exe's
# properties can never drift from the release it came from.
_ver = Path(SPECPATH) / "build" / "version_info.txt"
VERSION_FILE = str(_ver) if _ver.exists() else None

# The app dispatches most of its work by module name — `actions.browser_control` is
# fetched to answer a browser request, not imported at startup — so PyInstaller's
# static analysis cannot see it, and a build without this list ships an app where
# large parts of it simply do not exist. The frozen --selftest is what proves the
# list is complete.
APP_PACKAGES = ("actions", "core", "memory", "dashboard", "config")

hiddenimports: list[str] = []
for _pkg in APP_PACKAGES:
    try:
        hiddenimports += collect_submodules(_pkg)
    except Exception:
        pass

# Optional engines: bundled when this machine has them, skipped without an error
# when it does not (the app degrades by design — see core/installer.py).
_optional = ["sgp4", "paho.mqtt.client", "pynvml", "mss.windows", "pywin32"]

def _installed(name: str) -> bool:
    import importlib.util
    try:
        return importlib.util.find_spec(name) is not None
    except Exception:
        return False

hiddenimports += [
    # pywebview picks its backend at runtime, so nothing imports these.
    "webview.platforms.edgechromium",
    "webview.platforms.winforms",
    "playwright",
    "playwright.async_api",
    "playwright.sync_api",
    "playwright._impl._driver",
    "clr",
    # Windows audio + automation, all resolved by name at runtime.
    "comtypes",
    "comtypes.client",
    "comtypes.gen",
    "pycaw",
    "win32com",
    "win32com.client",
    "win32timezone",
    "pythoncom",
    "pystray._win32",
    # The phone dashboard's server.
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
    "fastapi",
]

datas = []
binaries = []

# dashboard/server.py looks for its pages at `Path(__file__).parent / "static"`,
# which inside a frozen build is _internal/dashboard/static — so they have to
# travel with the package rather than sit beside the exe. Without this the phone
# dashboard quietly disables itself ("Disabled: ... login.html").
_dash_static = ROOT / "dashboard" / "static"
if _dash_static.is_dir():
    datas.append((str(_dash_static), "dashboard/static"))

try:
    hiddenimports += collect_submodules("pycaw")
except Exception:
    pass

hiddenimports += [name for name in _optional if _installed(name)]

# nvml ships as a DLL beside the package; PyInstaller only follows imports.
for _pkg in ("pynvml", "nvidia_ml_py"):
    try:
        binaries += collect_dynamic_libs(_pkg)
    except Exception:
        pass

excludes = [
    # ui.py is the legacy Qt interface: not reachable from entry.py, and PyQt6 is
    # ~120 MB of Qt that a WebView2 build never touches.
    "ui",
    "PyQt6",
    "PyQt6.QtCore",
    "PyQt6.QtGui",
    "PyQt6.QtWidgets",
    "PyQt6.QtNetwork",
    "tkinter",
    "test",
    # NOTE: do not exclude distutils — PyInstaller's own hook aliases the
    # vendored setuptools._distutils onto that name, and excluding it makes the
    # build die with "already imported as ExcludedModule".
]

a = Analysis(
    [ENTRY],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[HOOKS],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)

_exe_common = dict(
    exclude_binaries=True,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICON,
    version=VERSION_FILE,
)

jarvis = EXE(pyz, a.scripts, name="JARVIS", console=False, **_exe_common)
debug = EXE(pyz, a.scripts, name="JARVIS-Debug", console=True, **_exe_common)

COLLECT(
    jarvis,
    debug,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="JARVIS",
)
