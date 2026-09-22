# Changelog — ICE JARVIS

All notable changes to **ICE JARVIS**, the real-time voice AI assistant.

## v1.0.0 — 2026-09-20

Initial public release of **ICE JARVIS**.

### Core
- Gemini Live real-time voice conversation (native audio streaming, no subscriptions)
- Holographic animated face with viseme lip-sync, facial acting, blinking and status glances
- Hindi/Hinglish by default; any language spoken back at her choice

### AGI behaviour
- **Proactive brain** — talks first after silence, opens conversations from memory
- **Screen observation** — watches your screen on its own and reacts
- Happiness/respect gating — a quiet word (`shut up` / `be quiet`) puts her on hold
- Persistent memory across sessions (long-term recall + memory panel)

### PC control (works inside the .exe — zero extra dependencies)
- Mouse, keyboard (incl. Unicode/Hindi typing), clipboard, windows, processes
- Volume / brightness / media / sleep / lock / show-desktop / run dialog
- System info, monitors, screenshots; AI can click on screen content
- Process kill and shutdown behind on-screen confirmation; undo for control changes

### UI & customisation
- ⚙ Customise overlay: assistant name, user name, 10 voices, UI colour wheel + 5 presets
- HUD style: animated face or reactor core
- Animation level: FULL (particles + voice pulse) / LIGHT / OFF
- Overlays fade in; reactive waveform; live theming without restart
- Wake word, push-to-talk, echo guard, plugins, morning brief, weather, reminders, web search, YouTube, messaging and more

### Updates & packaging
- Single-file `ICE.exe` build (PyInstaller spec + `tools/build_exe.ps1`)
- Self-updating from GitHub Releases (`core/updater.py` + `tools/publish_update.py`)
- sha256-verified `update.json` manifests; staged swap + relaunch via detached batch