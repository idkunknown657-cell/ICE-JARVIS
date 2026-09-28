"""
ICE — "is this install actually complete?" checks, shared by the frozen app and
the packaging script.

Two callers, one implementation:

  * `JARVIS.exe --selftest` runs inside the packaged build. Its job is to turn a
    missing hidden import or an asset that never made it into the payload into a
    *named* failure, instead of a crash the first time someone asks JARVIS to
    browse the web.
  * `installer/build_installer.py` runs it in the build environment first, so the
    manifest it writes lists the modules that genuinely import there — a module
    that needs an optional engine (`faster_whisper`, `edge_tts`, `kokoro`) is
    recorded as skipped rather than asserted, and cannot fail a release for a
    dependency the user was never going to have.

Nothing here raises: a checker that throws is worse than no checker.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Packages whose modules are compiled into the payload.
APP_PACKAGES: tuple[str, ...] = ("actions", "core", "memory", "dashboard",
                                 "config", "plugins")

# Import name → what breaks without it. Third-party, because the app's own
# modules are covered by the manifest check below.
CRITICAL_IMPORTS: tuple[tuple[str, str], ...] = (
    ("webview",        "the window itself"),
    ("clr",            "the WebView2 backend (pythonnet)"),
    ("numpy",          "the avatar and audio maths"),
    ("sounddevice",    "microphone and speaker capture"),
    ("requests",       "every network call"),
    ("cv2",            "screen and camera vision"),
    ("mss",            "screen capture"),
    ("PIL",            "image handling"),
    ("google.genai",   "the Gemini live session"),
    ("psutil",         "system stats and PC control"),
    ("comtypes",       "Windows audio control"),
    ("pycaw",          "per-app volume"),
    ("win32com",       "shortcuts and COM automation"),
    ("pystray",        "the tray icon"),
    ("playwright",     "browser automation"),
    ("websockets",     "the live audio socket"),
    ("cryptography",   "the phone dashboard's TLS"),
)

# Data the app reads off the disk rather than importing, relative to the install
# folder. These are the files a packaging mistake forgets.
REQUIRED_FILES: tuple[str, ...] = (
    "ui_web/index.html",
    "ui_web/css/style.css",
    "ui_web/js/app.js",
    "ui_web/js/avatar.js",
    "assets/jarvis.ico",
    "VERSION",
)

MANIFEST_NAME = "JARVIS-build.json"

# Modules that exist in the source tree but are deliberately not packaged, each
# with the reason. Asserting them would fail every build for a file no shipped
# code can reach; deleting them from the list without saying why is how a real
# omission gets mistaken for a legacy one later.
NOT_SHIPPED: dict[str, str] = {
    "core.avatar":
        "the hardware-3D avatar for the legacy Qt interface (ui.py, which is itself "
        "not shipped) — packaging it would drag in all of PyQt6",
}


def shipped_modules(names: list[str]) -> list[str]:
    """*names* minus the modules that are intentionally left out of the payload."""
    return [n for n in names if n not in NOT_SHIPPED]


# ── what should be there ─────────────────────────────────────────────────────

def app_modules(root: Path) -> list[str]:
    """Dotted names of every module the app ships.

    Walked from the source tree rather than hand-listed: a new action file is
    picked up by the next build instead of being forgotten until it crashes.
    """
    out: list[str] = ["main", "webui"]
    for package in APP_PACKAGES:
        base = root / package
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            parts = path.relative_to(root).with_suffix("").parts
            if any(p in ("__pycache__", "tests") or p.startswith(".") for p in parts):
                continue
            name = ".".join(parts)
            if name.endswith(".__init__"):
                name = name[: -len(".__init__")]
            if name not in out:
                out.append(name)
    return out


def check_imports(names: list[str]) -> dict[str, str]:
    """Import each name; return `{name: error}` for the ones that failed."""
    failures: dict[str, str] = {}
    for name in names:
        try:
            __import__(name)
        except BaseException as e:                    # noqa: BLE001
            failures[name] = f"{type(e).__name__}: {e}"
    return failures


def critical_failures() -> dict[str, str]:
    """Third-party libraries the app cannot live without, and what died."""
    failures: dict[str, str] = {}
    for name, why in CRITICAL_IMPORTS:
        try:
            __import__(name)
        except BaseException as e:                    # noqa: BLE001
            failures[name] = f"{type(e).__name__}: {e} — needed for {why}"
    return failures


def missing_files(root: Path, rels: tuple[str, ...] = REQUIRED_FILES) -> list[str]:
    """Which required data files are not in *root*."""
    out: list[str] = []
    for rel in rels:
        try:
            if not (root / rel).exists():
                out.append(rel)
        except Exception:
            out.append(rel)
    return out


# ── the packaged app's own entry point ───────────────────────────────────────

def packaged_modules(root: Path) -> list[str]:
    """The module list the build recorded, or [] when there is no manifest."""
    import json
    try:
        manifest = json.loads((root / MANIFEST_NAME).read_text(encoding="utf-8"))
        return [str(m) for m in manifest.get("modules", []) if str(m).strip()]
    except Exception:
        return []


def _say(line: str = "") -> None:
    """Print when there is anywhere to print to.

    A windowed build has no stdout at all (sys.stdout is None), and a checker
    that dies reporting good news would be a fine way to ruin a release.
    """
    if sys.stdout is None:
        return
    try:
        print(line, flush=True)
    except Exception:
        pass


def run(root: Path, verbose: bool = True) -> int:
    """Verify a packaged install. 0 = complete, 1 = something is missing.

    Modules are checked *before* the third-party list so that importing the app
    is what surfaces a missing library: the error then names the module that
    wanted it, which is the fact someone can act on.
    """
    problems: list[str] = []

    for rel in missing_files(root):
        problems.append(f"missing data file: {rel}")

    modules = shipped_modules(packaged_modules(root))
    if not modules:
        problems.append(
            f"no {MANIFEST_NAME} — this build did not come from build_installer.py, "
            "so nothing can be verified")
    else:
        # Plugins are loaded by path at runtime, so make the install folder
        # importable exactly the way the app does.
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        for name, err in check_imports(modules).items():
            problems.append(f"module {name} failed to import: {err}")

    for name, err in critical_failures().items():
        problems.append(f"library {name}: {err}")

    if verbose:
        _say(f"  modules checked : {len(modules)}")
        _say(f"  libraries       : {len(CRITICAL_IMPORTS)}")
        _say(f"  data files      : {len(REQUIRED_FILES)}")
        for name, why in NOT_SHIPPED.items():
            _say(f"  not packaged    : {name} ({why})")
        if problems:
            _say(f"\n{len(problems)} problem(s):")
            for p in problems:
                _say(f"  ✗ {p}")
        else:
            _say("\nOK — everything this build claims to contain is here.")
    return 1 if problems else 0
