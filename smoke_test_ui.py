"""Offscreen smoke test for the JARVIS HUD UI plumbing."""
import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication
app = QApplication.instance() or QApplication(sys.argv)

import ui
from ui import HudCanvas, CustomizeOverlay, JarvisUI
from memory.config_manager import AVAILABLE_VOICES, DEFAULT_VOICE

# 1. HudCanvas
hud = HudCanvas("face.png", assistant_name="Test")
hud.set_audio_level(0.5)
hud.glance(10, 10, hold=0.2)
hud.push_visemes([(0.3, 0.4, 0.5)] * 10, hop=0.02, at=0.0)
hud.repaint()
for lvl in ("full", "light", "off"):
    hud.set_ui_animation(lvl)
    hud.update()
print("HudCanvas OK — animation levels applied")

# 2. CustomizeOverlay — voice row with the new soft-girl default
ov = CustomizeOverlay(assistant_name="Aanya", user_name="", voice="Sulafat")
assert len(ov._voice_btns) == len(AVAILABLE_VOICES) == 10, ov._voice_btns.keys()
assert DEFAULT_VOICE == "Sulafat"
assert "Sulafat" in ov._voice_desc.text() and "Soft" in ov._voice_desc.text() and "default soft voice" in ov._voice_desc.text(), ov._voice_desc.text()
ov._on_voice_pick("Kore")
assert "Kore" in ov._voice_desc.text() and "Firm" in ov._voice_desc.text(), ov._voice_desc.text()
buttons = [b.text() for b in ov._voice_btns.values()]
assert any(b.startswith("Sulafat") and "Soft" in b for b in buttons), buttons
print("CustomizeOverlay OK — pills:", buttons[:4])
print("desc for Kore:", ov._voice_desc.text())

# 2b. CustomizeOverlay — new HUD style / animation pills
assert set(ov._hud_btns) == {"face", "core"}, ov._hud_btns.keys()
assert set(ov._anim_btns) == {"full", "light", "off"}, ov._anim_btns.keys()
ov._on_hud_pick("core")
assert ov._sel_hud == "core" and ov._hud_btns["core"].isChecked()
ov._on_anim_pick("off")
assert ov._sel_anim == "off" and ov._anim_btns["off"].isChecked()
ov._on_hud_pick("face")
ov._on_anim_pick("full")
print("CustomizeOverlay OK — HUD style + animation pills")

# 2c. CustomizeOverlay — saved() carries all six fields
got = {}
ov.saved.connect(lambda *a: got.update(args=a))
ov._set_color("#ffaa00", preview=False)
ov.saved.emit(*("Aanya", "Tony", "#ffaa00", "Sulafat", "core", "light"))
assert got["args"] == ("Aanya", "Tony", "#ffaa00", "Sulafat", "core", "light"), got
print("CustomizeOverlay OK — saved signal shape")

# 3. JarvisUI shim — full window + glance fix (used to hit a missing self._avatar)
jui = JarvisUI("face.png")
jui.glance(0, 0, hold=0.05)          # must not raise
jui.set_audio_level(0.5)
print("JarvisUI glance/set_audio_level OK")

print("SMOKE TEST PASSED")