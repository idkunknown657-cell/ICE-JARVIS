"""
build_exe.py — build a shareable Windows build of JARVIS in one command.

    py -3.12 build_exe.py            (or double-click build_exe.bat)

What it does:
  1. Runs PyInstaller with JARVIS.spec  →  dist/JARVIS/JARVIS.exe (+ _internal/)
  2. Copies the runtime data the app expects NEXT TO the exe (main.py's frozen
     base-dir logic looks there):  ui_web/, actions/, plugins/, core/prompt.txt,
     core/face_model.obj and the three non-secret config stores.
  3. Writes READ ME FIRST.txt for the person you hand the folder to.

Never copies config/api_keys.json, config/certs/ or any other secret — the
recipient runs the app's own first-run setup screen with their own Gemini key.

Optional flags:
    --console    build with a visible console window (for debugging)
    --no-clean   keep the previous build folder instead of rebuilding fresh
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

# Console must survive legacy code pages (cp1252 etc.) — same reconfigure the
# main app does, or an emoji/arrow in a status line kills the script at the end.
for _stream in ("stdout", "stderr"):
    try:
        _s = getattr(sys, _stream, None)
        if _s is not None and hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

HERE       = Path(__file__).resolve().parent
DIST_APP   = HERE / "dist" / "JARVIS"
BUILD_DIR  = HERE / "build"

# NOTHING from the developer's config/ is shipped. api_keys.json is the
# obvious secret, but improvements.json / usage_counts.json / ui_strategies.json
# are personal too: they hold LESSONS LEARNED FROM YOUR CONVERSATIONS, your
# usage patterns and your app-interaction logs. All three are runtime-accumulated
# stores that the app recreates on the recipient's machine (they handle a missing
# file gracefully and mkdir on save), so the build ships an EMPTY config dir.
CONFIG_FILES_TO_COPY: tuple[str, ...] = ()

# Anything that must never be found in a distribution, even by accident.
CONFIG_FILES_FORBIDDEN = (
    "api_keys.json", "improvements.json", "ui_strategies.json",
    "usage_counts.json", "certs", "whatsapp_web", "long_term.json",
)

README = """\
JARVIS — real-time voice AI companion
=====================================

FIRST RUN
  1. Double-click JARVIS.exe  (Windows 10/11, microphone and speakers needed).
  2. The setup screen asks for a FREE Gemini API key — get one in 30 seconds at
       https://aistudio.google.com/apikey
     Paste it, pick your name, done.
  3. (Optional, recommended) Add one free fallback key under ⚙ → API Keys so the
     assistant keeps working when Gemini's free quota runs out:
       Groq        console.groq.com
       Cerebras    cloud.cerebras.ai
       OpenRouter  openrouter.ai
       Hugging Face  https://huggingface.co/settings/tokens
       (create a "fine-grained" token with "Make calls to Inference Providers"
        permission, model default: meta-llama/Llama-3.1-8B-Instruct)

WHAT TO KNOW
  • Internet required (the voice model runs in the cloud; memory stays local).
  • Everything personal — memory, settings, keys — lives in the config/ folder
    next to this exe, on this machine only.
  • Voice: Aoede is the default soft female voice; change it in ⚙ → Voice.
  • "Hey Jarvis" wake word and the tray can be enabled in ⚙ → Startup.
  • Keep the folder together: JARVIS.exe, _internal/, ui_web/, actions/,
    core/, config/ all belong to the app.

TROUBLESHOOTING
  • If Windows SmartScreen warns, click "More info" → "Run anyway" (unsigned
    build).
  • If speech is silent, check ⚙ → Voice & Language → microphone/speaker
    selection.
  • Debug build with a visible console: rebuild with  build_exe.py --console
"""


def _find_builder() -> tuple[str, ...]:
    """Command prefix that can run PyInstaller: this interpreter if it has it,
    otherwise the `py` launcher's 3.12 environment."""
    try:
        import PyInstaller  # noqa: F401
        return (sys.executable, "-m", "PyInstaller")
    except ImportError:
        pass
    if Path(sys.executable).name.lower() != "python.exe" or sys.version_info[:2] != (3, 12):
        try:
            subprocess.run(["py", "-3.12", "-c", "import PyInstaller"],
                           check=True, capture_output=True)
            return ("py", "-3.12", "-m", "PyInstaller")
        except Exception:
            pass
    raise SystemExit(
        "PyInstaller is not installed in this Python environment.\n"
        "Install it into Python 3.12 and retry:\n"
        "    py -3.12 -m pip install pyinstaller"
    )


def _copy_tree(src: Path, dst: Path) -> None:
    """Copy a folder, skipping caches and junk. Merge if dst exists."""
    if not src.exists():
        print(f"  ! missing (skipped): {src}")
        return
    shutil.copytree(
        src, dst, dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".git*"),
    )


def stage_runtime_files() -> None:
    """Copy the source data files the frozen app expects next to the exe."""
    print("Staging runtime files next to the exe…")
    _copy_tree(HERE / "ui_web",  DIST_APP / "ui_web")
    _copy_tree(HERE / "actions", DIST_APP / "actions")
    _copy_tree(HERE / "plugins", DIST_APP / "plugins")

    core_dst = DIST_APP / "core"
    core_dst.mkdir(parents=True, exist_ok=True)
    for name in ("prompt.txt", "face_model.obj"):
        src = HERE / "core" / name
        if src.exists():
            shutil.copy2(src, core_dst / name)
        else:
            print(f"  ! missing (skipped): {src}")

    # the updater compares against the shipped VERSION file
    ver = HERE / "VERSION"
    if ver.exists():
        shutil.copy2(ver, DIST_APP / "VERSION")
    else:
        (DIST_APP / "VERSION").write_text("0.0.0", encoding="utf-8")

    cfg_dst = DIST_APP / "config"
    cfg_dst.mkdir(parents=True, exist_ok=True)
    for name in CONFIG_FILES_TO_COPY:
        src = HERE / "config" / name
        if src.exists():
            shutil.copy2(src, cfg_dst / name)
    # Paranoia pass: strip anything personal or secret from a previous staging.
    for name in CONFIG_FILES_FORBIDDEN:
        target = cfg_dst / name
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
        elif target.exists():
            target.unlink()
    if not (cfg_dst / ".gitkeep").exists():
        (cfg_dst / ".gitkeep").write_text("", encoding="utf-8")

    (DIST_APP / "READ ME FIRST.txt").write_text(README, encoding="utf-8")
    print(f"  → {DIST_APP}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the JARVIS exe distribution")
    ap.add_argument("--console", action="store_true",
                    help="keep a console window (debugging)")
    ap.add_argument("--no-clean", action="store_true",
                    help="reuse the previous build tree")
    ap.add_argument("--stage-only", action="store_true",
                    help="skip PyInstaller; only (re)copy the runtime files")
    args = ap.parse_args()

    if args.stage_only:
        stage_runtime_files()
        print(f"\nOK. Distributable folder: {DIST_APP}")
        return

    if not args.no_clean:
        shutil.rmtree(BUILD_DIR, ignore_errors=True)
        shutil.rmtree(DIST_APP, ignore_errors=True)

    cmd = list(_find_builder()) + [
        "--noconfirm", "--clean",
        "--distpath", str(HERE / "dist"),
        "--workpath", str(HERE / "build"),
    ]
    spec_path = HERE / "JARVIS.spec"
    if args.console:
        # PyInstaller spec options are baked into the spec file; a debug build
        # gets its own temporary spec with console=True.
        spec_path = HERE / "build" / "JARVIS_console.spec"
        spec_path.parent.mkdir(exist_ok=True)
        spec_path.write_text(
            (HERE / "JARVIS.spec").read_text(encoding="utf-8")
                .replace("console=False,", "console=True,"),
            encoding="utf-8")
    cmd.append(str(spec_path))

    print("Building:", " ".join(cmd))
    subprocess.run(cmd, check=True, cwd=HERE)

    stage_runtime_files()

    print("\n✅ Build complete.")
    print(f"   Distributable folder: {DIST_APP}")
    print("   Hand the WHOLE folder to someone (zip it) — they start with JARVIS.exe.")


if __name__ == "__main__":
    main()
