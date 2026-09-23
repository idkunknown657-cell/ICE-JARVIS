# Changelog — ICE JARVIS

All notable changes to **ICE JARVIS**, the real-time voice AI assistant.

## v1.0.1 — 2026-09-22

First release shipped as a downloadable Windows build
(`ICE-JARVIS-Windows-x64.zip`) with one-click self-updates.

### UI
- Animated self-update card: pulsing status dot, shimmering progress bar with
  live percentage, glowing install button — all `prefers-reduced-motion` aware
- Quick-launch pill row removed from the dock; chat is the launcher
- Settings → About shows a one-tap install card when an update is staged

### Conversation & personality
- Emotional delivery engine (`core/emotion.py`): emotion shapes words, tone,
  voice prosody and avatar expression; bounded palette, anti-overact rules
- Persona continuity (`core/persona.py`): openers and references follow the
  real relationship, lessons learned are applied as standing guidance
- Language detection (`core/language.py`): English / Hindi / Hinglish detected
  per message and mirrored naturally

### AI providers
- Free fallbacks alongside Gemini: **GROQ, Cerebras, OpenRouter, Hugging Face**
  (Settings → API Keys) with automatic failover and failure cooldowns
- `/v1` base-URL normalisation — provider rows saved with or without the
  trailing `/v1` both work (fixes a silent 404 that broke all fallbacks)

### Self-updates
- Update channel baked in: every exe checks the repo's `latest` release
  manifest at boot and every 5 minutes (silent, toasts once per version)
- Real download progress streamed to the UI; staged updates resume after a
  restart; sha256 verification before anything is unpacked

### Repository hygiene
- Scratch/dev-only files removed; root tests moved under `tests/`
- Release tooling aligned to the actual `JARVIS.exe` naming
- GitHub Actions CI runs the full unit suite on every push

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
- ~~Single-file `ICE.exe` build~~ — removed; run from source (no exe, no build)
- Self-updating from GitHub Releases (`core/updater.py`)
- sha256-verified `update.json` manifests; staged swap + relaunch via detached batch