"""
Regression tests for core/pc_input (dependency-free PC control engine) and the
extended actions/computer_control tool.

SAFETY: these tests never move the real mouse, press real keys, write the real
clipboard, or kill processes. They cover pure functions, read-only queries and
the confirm-gate behaviour of destructive actions only.
"""
import io
import sys
import platform
from pathlib import Path

for s in ("stdout", "stderr"):
    try:
        getattr(sys, s).reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import core.pc_input as pc
from actions.computer_control import computer_control

_WIN = platform.system() == "Windows"

FAILED = []


def check(name: str, cond: bool, detail: str = ""):
    if cond:
        print(f"ok   {name}")
    else:
        FAILED.append(name)
        print(f"FAIL {name}  {detail}")


# ── pure key-mapping table ────────────────────────────────────────────────────
check("vk enter", pc.vk_for_name("enter") == 0x0D)
check("vk volumeup", pc.vk_for_name("volumeup") == 0xAF)
check("vk win", pc.vk_for_name("win") == 0x5B)
check("vk ctrl", pc.vk_for_name("ctrl") == 0x11)
check("vk f12", pc.vk_for_name("f12") == 0x7B)
check("vk f24", pc.vk_for_name("f24") == 0x87)
check("vk esc alias", pc.vk_for_name("escape") == pc.vk_for_name("esc"))
check("vk unknown is None", pc.vk_for_name("flurble") is None)

# ── pyautogui-compatible shim surface ─────────────────────────────────────────
for _name in ("press", "hotkey", "scroll", "hscroll", "write", "typewrite",
              "click", "doubleClick", "moveTo", "dragTo", "position", "size",
              "screenshot"):
    check(f"shim has {_name}", callable(getattr(pc, _name, None)))

# ── read-only engine queries (safe everywhere they run) ──────────────────────
if _WIN:
    w, h = pc.screen_size()
    check("screen_size ints", isinstance(w, int) and isinstance(h, int) and w > 0)
    px, py = pc.mouse_position()
    check("mouse_position ints", isinstance(px, int) and isinstance(py, int))
    rows = pc.window_list(limit=10)
    check("window_list shape", isinstance(rows, list) and all(len(r) == 3 for r in rows))
    procs = pc.process_list()
    check("process_list shape", isinstance(procs, list) and all(len(r) == 3 for r in procs))
    mons = pc.screen_monitors()
    check("monitors has primary", any(m.get("width", 0) > 0 for m in mons))
    info = pc.system_info()
    check("system_info has system", bool(info.get("system")))
    c = pc.clipboard_get()
    check("clipboard_get str", isinstance(c, str))
else:
    print("(skipping Win32-only queries on this host)")

# ── computer_control dispatch (never fires physical input) ────────────────────
check("dispatch position", "," in computer_control({"action": "mouse_position"}))
check("dispatch screen", "Screen:" in computer_control({"action": "screen_size"}))
check("dispatch unknown", "Unknown action" in computer_control({"action": "nope_x"}))
check("dispatch no-action", "No action" in computer_control({}))
out = computer_control({"action": "process_kill", "name": "junk_test_proc.exe"})
check("kill is gated, not run", ("confirm" in out.lower()) or ("confirm" in out.lower() and "not" in out.lower()))
check("kill never says done", "Killed" not in out and "Terminated" not in out)

# ── TOOL declaration still validates ──────────────────────────────────────────
from core.action_loader import discover_actions
log: list[str] = []
reg = discover_actions(Path("actions"), logger=log.append)
check("computer_control discovered", reg.has("computer_control"))
check("computer_settings discovered", reg.has("computer_settings"))

if FAILED:
    print(f"\n{len(FAILED)} FAILED: {FAILED}")
    sys.exit(1)
print("\nPC INPUT TESTS PASSED")