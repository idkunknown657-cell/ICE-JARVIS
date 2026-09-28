"""Single source of truth for the app version.

That source is the `VERSION` file at the project root — what core/updater.py
compares against the release manifest, what installer/build_installer.py passes
to PyInstaller and Inno Setup, and what the installed app prints for --version.
This module reads it rather than keeping a second copy of the number.
"""

import sys
from pathlib import Path

_FALLBACK = "1.1.0"


def _base_dir() -> Path:
    """The project (or install) folder — beside the exe once packaged."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _from_file() -> str:
    try:
        return (_base_dir() / "VERSION").read_text(encoding="utf-8").strip()
    except Exception:
        return ""


# Read, never duplicated: what updater.py compares and what the installer writes
# into the exe is the VERSION file, so a second copy here is a number that goes
# stale the moment a release is cut.
APP_VERSION = _from_file() or _FALLBACK
APP_NAME    = "ICE"