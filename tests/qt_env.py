"""
tests/qt_env.py — the two facts that have to be true before the first widget.

Import this *before* ``ui``/``PyQt6`` in any test that builds real widgets; the
setup runs on import, so ``import qt_env`` is the whole interface.

  * **A platform that exists.**  The suite pins ``QT_QPA_PLATFORM=offscreen``
    unless the developer already picked one, so the same tests run on a
    desktop and on a runner with no session to draw in.

  * **A COM apartment on this thread.**  PyQt6 releases its Windows objects
    while the garbage collector runs, and a release on a thread whose COM
    apartment was never created raises ``0x800401F0`` (CO_E_NOTINITIALIZED):
    a Windows fatal exception with no Python frame to blame and no test to
    point at.  Qt's Windows platform plugin calls ``OleInitialize()`` when it
    loads, but the offscreen plugin never does and nothing else here does
    either, so a headless test process had no apartment at all.  Where that
    exception is not handled the interpreter dies on the spot — CI run
    36897686899 lost its last 156 tests to a bare "exit code 1" with no
    traceback and nothing failing, and an earlier run died the same way just
    after the final test.

The apartment is deliberately never torn down: ``CoUninitialize()`` while Qt
still holds those objects is the same crash one step later, and the process is
about to exit anyway.
"""
import os
import sys

__all__ = ["PLATFORM", "pin_platform", "prepare_headless_qt"]

#: What a headless test process runs on.  Qt's windows plugin wants a session;
#: offscreen draws into memory and wants nothing.
PLATFORM = "offscreen"

#: Return codes from CoInitializeEx(): S_OK (this call created the apartment),
#: S_FALSE (this thread already had one) and RPC_E_CHANGED_MODE (…of a
#: different kind, which still counts — the frames exist, which is all Qt
#: needs).  Anything else means COM is not usable from here.
_APARTMENT_THREADED = 0x2
_S_OK, _S_FALSE = 0, 1
_RPC_E_CHANGED_MODE = 0x80010106
_ACCEPTED_HRESULTS = (_S_OK, _S_FALSE, _RPC_E_CHANGED_MODE)

_prepared = False
_com_ready = False


def pin_platform() -> str:
    """``offscreen`` unless someone already chose a platform.  Idempotent."""
    os.environ.setdefault("QT_QPA_PLATFORM", PLATFORM)
    return os.environ["QT_QPA_PLATFORM"]


def _init_com(platform: str | None = None, coinitialize=None) -> bool:
    """Open a COM apartment for this thread; True when it is usable.

    Never raises: a machine that cannot start COM is a reason for the Windows
    release paths to be quiet, not for a test run to fail.  The platform and
    the entry point are parameters so the branches can be exercised from a
    test on a machine that is not Windows.
    """
    platform = sys.platform if platform is None else platform
    if platform != "win32":
        return False
    if coinitialize is None:
        try:
            import ctypes
            ole32 = ctypes.WinDLL("ole32")
            # HRESULT, not int: the changed-mode code is negative as an int.
            ole32.CoInitializeEx.restype = ctypes.c_ulong
            coinitialize = ole32.CoInitializeEx
        except Exception:
            return False
    try:
        hresult = int(coinitialize(None, _APARTMENT_THREADED))
    except Exception:
        return False
    return hresult in _ACCEPTED_HRESULTS


def prepare_headless_qt() -> bool:
    """Pin the platform and open the apartment; True once this thread has one.

    Idempotent: only the first call touches the environment or COM, which
    also keeps a second import of this module from re-asking.
    """
    global _prepared, _com_ready
    if _prepared:
        return _com_ready
    _prepared = True
    pin_platform()
    _com_ready = _init_com()
    return _com_ready


prepare_headless_qt()
