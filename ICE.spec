# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the ICE JARVIS Windows build.
#
# Build with:  powershell -ExecutionPolicy Bypass -File tools/build_exe.ps1
#
# What ships next to ICE.exe (copied by build_exe.ps1, NOT part of the exe):
#   core\prompt.txt, core\face_model.obj   — read from the exe's own folder at
#                                            runtime (see get_base_dir / _base_dir)
#   actions\, plugins\                     — scanned at startup for tools; keeping
#                                            them as real folders beside the exe
#                                            preserves the drop-in plugin model
#                                            (users can add a .py there).
#
# Heavy optional AI/media libraries (torch, playwright, cv2, fastapi, …) are
# deliberately EXCLUDED: the code imports them inside try/except, so a missing
# one disables exactly one feature and the exe stays a reasonable size.

from PyInstaller.utils.hooks import collect_all

datas, binaries, hiddenimports = [], [], []
for _pkg in ("google", "google.genai", "sounddevice", "numpy", "PIL",
             "psutil", "mss", "requests"):
    try:
        _d, _b, _h = collect_all(_pkg)
        datas    += _d
        binaries += _b
        hiddenimports += _h
    except Exception:
        pass  # a missing optional package must not fail the build

hiddenimports += [
    "ui", "memory.memory_manager", "memory.config_manager",
    "core.avatar", "core.avatar_mesh", "core.action_loader",
    "core.plugin_loader", "core.updater", "core.version", "core.echo",
    "core.viseme", "core.confirm", "core.undo", "core.audio_devices",
    "core.pc_input",
    "actions.proactive", "actions.screen_processor", "actions.system_monitor",
    "actions.background_monitor", "actions.web_search",
]

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # heavy / optional — imported lazily, degrade gracefully when absent
        "tkinter", "torch", "transformers", "pandas", "numpy.random._mt19937",
        "playwright", "cv2", "faster_whisper", "kokoro", "edge_tts", "vosk",
        "openwakeword", "fastapi", "uvicorn", "win10toast", "pdfplumber",
        "docx", "pptx", "openpyxl", "pyautogui", "pywinauto", "comtypes",
        "pycaw", "pynvml", "youtube_transcript_api", "cryptography", "qrcode",
        "tinytuya", "paho", "ddgs", "duckduckgo_search", "pydub",
        "send2trash", "pyperclip", "pygetwindow", "plyer", "miniaudio",
        "wmi", "pywin32",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="ICE",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)