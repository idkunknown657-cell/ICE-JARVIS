# -*- mode: python ; coding: utf-8 -*-
"""
ICE.spec — PyInstaller build recipe for the ICE JARVIS Windows build
(onedir, WebView2 UI). Run via build_exe.py / tools/build_exe.ps1.

Output: dist/ICE/ICE.exe  (+ _internal/ with the frozen Python runtime)

WHY ONEDIR (not onefile)
    One-file builds extract the whole runtime to a temp folder on every start,
    which adds seconds to launch and slows the first speak(). The app is a
    real-time voice assistant — a folder distribution starts instantly and is
    exactly what main.py's frozen-path logic (get_base_dir) is written for:
    runtime data (ui_web/, config/, core/prompt.txt, actions/) lives NEXT TO
    the exe. build_exe.py copies those there after the build.

The UI is the web interface in ui_web/ (pywebview / WebView2). The legacy Qt
HUD (ui.py) is not part of the frozen app, so the whole Qt toolkit is dropped,
plus the optional heavy TTS/wake-word stacks (installed on demand from inside
the app instead).
"""
import os
from PyInstaller.utils.hooks import collect_submodules

# actions/ is a namespace package (no __init__.py), so collect_submodules can't
# walk it — enumerate the files instead. Underscore files are helpers pulled in
# by tracing the action modules themselves.
_action_modules = [
    f"actions.{f[:-3]}"
    for f in sorted(os.listdir("actions"))
    if f.endswith(".py") and not f.startswith("_")
]

hiddenimports = (
    _action_modules
    + collect_submodules("core")
    + collect_submodules("memory")
    + [
        # pywebview on Windows drives WebView2 through pythonnet/CLR
        "webview.platforms.winforms",
        "webview.platforms.edgechromium",
        "clr_loader",
        "pythonnet",
        # core audio + network
        "sounddevice",
        "psutil",
        "requests",
        "websockets",
        "google.genai",
        "google.genai.types",
        # tray + dashboard (optional features, imported lazily at runtime)
        "pystray",
        "PIL",
        "fastapi",
        "uvicorn",
        "cryptography",
    ]
)

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    # avatar_mesh.py reads face_model.obj from its own directory — inside the
    # frozen bundle that is _internal/core/.
    datas=[("core/face_model.obj", "core")],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "PyQt6", "PyQt5", "PySide6", "tkinter",
        "kokoro", "torch", "tensorflow", "IPython",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ICE",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # windowed — logs live in the app's Chat view
    icon="assets/jarvis.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="ICE",
)