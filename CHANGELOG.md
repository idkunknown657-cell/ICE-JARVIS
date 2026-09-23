# Changelog — ICE JARVIS

All notable changes to **ICE JARVIS**, the real-time voice AI assistant.

## Unreleased — Trading / market analysis module

### Market data with provenance (§1/§7/§8)
- New `market_data` tool: quote (price, change, OHLC, volume, timeframe,
  timestamp, provider), candles across 1m–1M timeframes (4H aggregated from
  1H bars), news headlines with source + publish time, and earnings/calendar
  answers that honestly say the provider does not support them — delayed data
  always carries "Market data may be delayed." and is never called real-time;
  failures surface the provider's own words instead of invented numbers
- Pluggable provider registry (`core/trading/data.py`, default Yahoo — no API
  key), TTL cache + rate-limit-friendly background evaluation (§21)

### Analysis as scenarios, never certainties (§2–§4, §6, §16)
- New `market_analysis` tool: quick per-timeframe readouts (SMA/EMA/VWAP/RSI/
  MACD/Bollinger/ATR/ADX/stochastic/volume/support-resistance/previous
  highs-lows/breakouts/trend structure/volatility/MA crosses) and a full
  multi-timeframe report (higher TF trend → middle TF structure → lower TF
  entry) with CURRENT MARKET STATE / BULLISH SCENARIO / BEARISH SCENARIO /
  INVALIDATION / RISK / TRADE PLAN sections — entry zone, invalidation,
  targets, risk/reward, confirmation checklist, worked position sizing, and a
  FACT-vs-INTERPRETATION news block; `core/trading/language.py` enforces the
  no-false-certainty phrase guard and fixed disclaimers

### Risk, order workflow, paper trading, journal, backtest (§5, §12–§15, §18, §19)
- `risk_calculator`: position size = maximum dollar risk / distance to stop,
  shown long-hand every time; `check_trade` guardrails (stop required, budget,
  leverage cap, daily loss/trade caps, existing/correlated exposure) STOP
  live preparation with arithmetic — limits are only ever written by the
  user-facing settings tool (§17), never raised silently; impulsive requests
  ("all in", "double my position", "make it back") get calm numbers and forced
  confirmation, no shaming
- `trade_order`: ANALYZE → PREPARE → SHOW → CONFIRM → SUBMIT → VERIFY →
  JOURNAL. A live execution is structurally unreachable except through the
  on-screen CONFIRM button (`core/confirm.py` — the model cannot forge it),
  and unconfigured platform automation refuses honestly instead of
  blind-clicking a real-money order ticket; fills are verified on screen or
  reported as unverified
- `paper_trading` (default mode on a fresh install; virtual balance/positions/
  P/L), `trading_journal` (paper/live never blended; win rate, drawdown, avg
  risk/reward with the past≠future disclaimer), `backtest_strategy`
  (next-bar-open fills, fees + slippage, profit factor, per-trade Sharpe,
  winning/losing streaks, zero-trade results reported as results)

### Alerts, position watching, chart reading (§9–§11)
- `trade_alerts` background worker (20 s cadence, fires once by default) —
  price/percent/breakout/RSI/MA-cross/volume/support-resistance/stop/target/
  news types; `economic_event` honestly refused until a provider ships a
  calendar. Fired alerts are delivered by a new main-loop drain alongside the
  existing news monitor
- `trade_monitor` watches open trades (entry/current/stop/targets, unrealized
  P/L $ and %, time in trade, distances) and auto-registers its stop/target
  price alerts; `chart_analysis` reads chart images strictly from what is
  printed (unreadable → "Please zoom in or provide the timeframe.", never
  estimates)
- New `trading_settings` tool (mode/risk/platform/prefs) is the sole writer
  of trading config; `config/trading_*.json` + `paper_account.json` are
  gitignored (personal financial data)

## Unreleased — PC Control engine hardening

### Automation stop (voice/UI interrupt now halts work, not just speech)
- `core/cancel.py` stop token: a stale stop pressed while idle can never kill
  the next run (`begin()` always re-arms)
- New `stop_task` tool — "stop / cancel / wait / don't do that" halts a running
  multi-step goal between steps instead of letting it continue
- `goal_agent` checks the token before every step and reports exactly what it
  got done; `JARVIS.interrupt()` raises it, so the mic/interrupt button works
  as an emergency stop for queued automation

### Precise, verified pointer control
- Multi-monitor mouse: `pc_input` absolute moves/drags now normalise over the
  **virtual desktop** (`MOUSEEVENTF_VIRTUALDESK`) — coordinates on a second
  monitor (including negatives left of the primary) land correctly instead of
  being clamped onto monitor 1; `computer_control._clamp_to_screen` clamps to
  the whole virtual screen and still avoids pyautogui failsafe corners
- `screen_move` now verifies the pointer actually reached the target and warns
  on a mismatch instead of assuming success
- New `screen_drag` — OBSERVE source → OBSERVE destination → drag → report
  honestly ("verify the drop")
- `click` accepts `modifier` ("hold Shift and click") — the key is always
  released, even if the click fails

### System control (§8), with real state verification
- `wifi_on` / `wifi_off` — explicit state, adapter route first, non-admin Radio
  API fallback, verified read-back; refusal reports the *actual* current state
  instead of a fake "Done."
- `toggle_bluetooth` / `bluetooth_on` / `bluetooth_off` — Windows Radio API,
  `bluetoothctl` on Linux, honest "do it in System Settings" on macOS
- `sleep_pc` (real suspend, unlike `sleep_display`) and `sign_out`
  (confirmation-gated — apps close immediately)
- `press_key` now accepts combinations (`win+shift+s` used to crash)
- "decrease volume by 20%" is a **delta** (now −20%), no longer misread as
  "set volume to 20"; delta results report the resulting level
- Dispatcher returns each action's own verified result — the blanket
  "Done: {action}" only appears when the action says nothing itself

### Files
- `file_controller` gains `open` — open any file with its app or a folder in
  the file manager ("Open my Downloads.")

### Diagnostics (§30)
- `core/pc_log.py` → `logs/pc_control.log`: one structured line per control
  decision (app, target, method UIA/vision, bounding box, action, verification,
  result); typed text is logged as a length only — never contents; rotates at
  512 KB and can never raise into a control action

### Dependencies
- `pywinauto` added to `requirements.txt` (Windows) — the UI Automation layer
  of `screen_ai` now installs for fresh setups instead of silently degrading
  to vision-only

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