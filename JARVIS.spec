# -*- mode: python ; coding: utf-8 -*-
"""
JARVIS.spec — PyInstaller build recipe (run via build_exe.py or build_exe.bat).

Output: dist/JARVIS/JARVIS.exe  (+ _internal/ with the frozen Python runtime)

WHY ONEDIR (not onefile)
    One-file builds extract the whole runtime to a temp folder on every start,
    which adds seconds to launch and slows the first speak(). The app is a
    real-time voice assistant — a folder distribution starts instantly and is
    exactly what main.py's frozen-path logic (get_base_dir) is written for:
    runtime data (ui_web/, config/, core/prompt.txt, actions/) lives NEXT TO
    the exe. build_exe.py copies those there after the build.
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
    # prompt.txt is loaded by main.py at runtime.
    datas=[
        ("core/face_model.obj", "core"),
        ("core/prompt.txt", "core"),
        ("assets/jarvis.ico", "assets"),
    ],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # The legacy Qt UI (ui.py) is not part of the frozen app; drop the whole
    # toolkit plus the optional heavy TTS/wake-word stacks (installed on demand
    # from inside the app instead).
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
    name="JARVIS",
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
    name="JARVIS",
)
