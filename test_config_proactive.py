"""Quick check of the new config knobs + proactive mute memory helpers."""
import os, sys, json, tempfile
from pathlib import Path

for _s in ("stdout", "stderr"):  # mirrors main.py's console fix
    try:
        getattr(sys, _s).reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Isolate the config store so tests never touch a real api_keys.json
import memory.config_manager as cm
_tmp = Path(tempfile.mkdtemp())
_old_dir, _old_file = cm.CONFIG_DIR, cm.CONFIG_FILE
cm.CONFIG_DIR  = _tmp
cm.CONFIG_FILE = _tmp / "api_keys.json"

# --- config knobs ---
from memory.config_manager import (
    get_proactive_config, save_proactive_config,
    get_updates_config, save_updates_config,
)

pc = get_proactive_config()
assert pc["enabled"] is True and pc["min_silence_s"] == 25 and pc["cooldown_s"] == 120
assert pc["observe_s"] == 45 and pc["vision"] is True
save_proactive_config({"min_silence_s": 15, "enabled": False, "bogus": 999})
pc2 = get_proactive_config()
assert pc2["min_silence_s"] == 15 and pc2["enabled"] is False and "bogus" not in pc2
assert pc2["cooldown_s"] == 120  # untouched key survives
print("proactive config OK:", pc2)

uc = get_updates_config()
assert uc["github_repo"] == "" and uc["check_on_start"] is True and uc["channel"] == "stable"
save_updates_config({"github_repo": "kakud/JARVIS", "channel": "STABLE "})
uc2 = get_updates_config()
assert uc2["github_repo"] == "kakud/JARVIS" and uc2["channel"] == "stable"
print("updates config OK:", uc2)

# --- mute memory helpers (isolated store) ---
import memory.memory_manager as mm
tmp = Path(tempfile.mkdtemp()) / "memory.json"
old = mm.MEMORY_PATH
mm.MEMORY_PATH = tmp
try:
    assert mm.is_proactive_muted() is False
    mm.set_proactive_muted(True)
    assert mm.is_proactive_muted() is True
    mm.set_proactive_muted(False)
    assert mm.is_proactive_muted() is False
    print("mute helpers OK")
finally:
    mm.MEMORY_PATH = old

# --- proactive engine defaults ---
from actions.proactive import ProactiveEngine
e = ProactiveEngine()
assert e.min_silence_secs == 25 and e.check_cooldown == 120
prompt = e.build_prompt(memory={"identity": {}}, monitors=["a"], recent_turns=["hi and then silence"])
assert "stay silent" in prompt and "interrupted" in prompt
print("proactive engine OK")

# --- updater import: version parse ---
from core.updater import parse_version
assert parse_version("v1.2.3") == (1, 2, 3) and parse_version("1") == (0, 0, 0)
print("parse_version OK")

cm.CONFIG_DIR, cm.CONFIG_FILE = _old_dir, _old_file  # restore real store
print("ALL CONFIG/MEMORY/ENGINE TESTS PASSED")