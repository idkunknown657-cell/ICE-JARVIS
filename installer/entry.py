"""
ICE JARVIS — entry point for the packaged (frozen) build.

Why this exists instead of shipping `main.py` as the frozen script: a windowed exe
has no console, so anything raised before `main()` installs its logger vanishes
without a trace. "I double-clicked it and nothing happened" is the hardest report
to act on, and it is entirely avoidable.

So this file owns the few things a shipped app needs and a source checkout does
not:

  * `--version`               — what the installer put here, without booting the UI
  * `--selftest`              — verify the build is complete (see core/selftest.py)
  * `--install-browser-deps`  — the optional Playwright download, run by the
                                installer's tick-box
  * a startup crash that writes `logs/startup-crash.log` *and* shows a dialog
    saying where that file is, because a silent exit is not a diagnosis.
"""
from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

# The debug build's console is whatever code page Windows is set to (cp1252 here),
# and this file prints em dashes and ✓/✗ marks. Same reasoning as main.py: make the
# output encoding explicit rather than let a status line raise on a Turkish PC.
for _stream in ("stdout", "stderr"):
    try:
        _s = getattr(sys, _stream, None)
        if _s is not None and hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


# ── paths ────────────────────────────────────────────────────────────────────

def _say(line: str = "") -> None:
    """Print only when there is somewhere to print to.

    The windowed build has no stdout (sys.stdout is None), and `--selftest` has to
    be safe to run from a double-clicked exe as well as from a terminal.
    """
    if sys.stdout is None:
        return
    try:
        print(line, flush=True)
    except Exception:
        pass


def base_dir() -> Path:
    """The folder the app lives in — beside the exe once installed."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def version() -> str:
    try:
        return (base_dir() / "VERSION").read_text(encoding="utf-8").strip() or "0.0.0"
    except Exception:
        return "0.0.0"


# ── crash reporting ──────────────────────────────────────────────────────────

def _write_crash(text: str) -> Path:
    """Always return a path, even when the log folder cannot be written."""
    log_dir = base_dir() / "logs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    path = log_dir / "startup-crash.log"
    try:
        with path.open("a", encoding="utf-8", errors="replace") as f:
            f.write(text)
    except Exception:
        return log_dir
    return path


def _show_error(message: str) -> None:
    """A native message box — the one channel a windowed build is sure to have."""
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, "ICE JARVIS", 0x10)
    except Exception:
        try:
            print(message, file=sys.stderr)
        except Exception:
            pass


def _report_crash(exc: BaseException) -> int:
    detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    path = _write_crash(f"\n===== startup crash {version()} =====\n{detail}\n")
    _show_error(
        "ICE JARVIS could not start.\n\n"
        f"{type(exc).__name__}: {exc}\n\n"
        f"The full traceback was written to:\n{path}\n\n"
        "Open \"ICE JARVIS (debug console)\" from the Start Menu to watch it happen."
    )
    return 1


# ── modes ────────────────────────────────────────────────────────────────────

def _selftest() -> int:
    from core import selftest

    root = base_dir()
    _say(f"ICE JARVIS {version()} — self-test")
    _say(f"  install folder : {root}")
    _say(f"  frozen         : {bool(getattr(sys, 'frozen', False))}")
    return selftest.run(root)


def _install_browser_deps() -> int:
    from core import browser_deps

    result = browser_deps.install(log=_say)
    if result["ok"]:
        _say(f"browser components: {', '.join(result['installed']) or 'nothing to do'}")
        return 0
    _say(f"browser components: FAILED — {result['err']}")
    if result.get("detail"):
        _say(result["detail"])
    return 1


def _run_app() -> int:
    """Boot the real application. Never returns normally — it runs the mainloop."""
    # Every path in the app resolves from the install folder, so a shortcut with a
    # different working directory cannot change which config gets read.
    try:
        os.chdir(base_dir())
    except Exception:
        pass

    import main                       # noqa: F401  — boots the WebView2 interface
    main.main()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = [a for a in (argv if argv is not None else sys.argv[1:]) if a]

    if "--version" in args:
        _say(version())
        return 0

    if "--selftest" in args:
        try:
            return _selftest()
        except BaseException as e:                       # noqa: BLE001
            return _report_crash(e)

    if "--install-browser-deps" in args:
        try:
            return _install_browser_deps()
        except BaseException as e:                       # noqa: BLE001
            return _report_crash(e)

    try:
        return _run_app()
    except BaseException as e:                           # noqa: BLE001
        return _report_crash(e)


if __name__ == "__main__":
    sys.exit(main())
