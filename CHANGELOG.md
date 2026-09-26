# Changelog — ICE JARVIS

All notable changes to **ICE JARVIS**, the real-time voice AI assistant.

## Unreleased — Self-training that checks its own homework

The self-training loop could drill and remember all day, but it never once
asked whether the rules it wrote **worked** — so the playbook could only grow,
and a rule that changed nothing was indistinguishable from one that fixed the
problem. It now closes the loop: OBSERVE → DIAGNOSE → DRILL → REMEMBER →
**VERIFY**.

- **Every rule is graded against real evidence.** Each self-written rule stores
  the win rate it was written at plus how many outcomes had been recorded; the
  next rounds score it against the outcomes that arrived *after* it was written
  and tag it **helped / no change / worse**. A rule with no new evidence behind
  it reads *measuring…* — the loop will not grade a rule on evidence it does not
  have, because a verdict is a claim about the world.
- **A capability that is falling is practised before one that is merely weak.**
  "Weakest" used to mean lowest win rate, which quietly ignored the trend it
  already computed: something at 80% that just fell from 95% is a regression the
  next round can still stop, while something parked at 70% is a standing
  limitation. The regression penalty is half the drop, so this re-orders
  near-ties rather than chasing a noisy window.
- **Practice that stalls escalates instead of repeating itself.** Rounds whose
  rules did not move the record — or that produced no new rule at all — are
  counted against the capability; the next round on it runs on the *smarter*
  model and is told plainly, in the prompt, that the previous habits are not
  working and that rewording them is worthless.
- **The playbook is visible as an asset, not a pile.** Competency rows show
  which capability is falling right now, the Home card reports how many rules
  paid off and how many made things worse, and Settings lists every rule with
  its verdict, its measured effect and a one-click forget.
- **The conversation digest prefers proven rules.** What JARVIS carries into a
  real conversation is now sorted so instructions that measurably helped come
  first, unproven ones next, and rules that failed to move the record are
  labelled "do not rely on these" — leaning on an instruction that did not help
  would be worse than having no digest at all.

## Unreleased — A core that moves like something alive

Talking to a drawing that never moved felt like talking to a *program*, not to
something present. The centre of the HUD — canvas face, glow halo and the 3D
body — now drifts, breathes and pulses every frame, and it moves with the voice
rather than on a fixed loop.

- **Springs, not keyframes.** Five channels (left/right, up/down,
  forward/back, tilt, pulse) are critically-damped springs integrating a target
  that itself travels along layered incommensurate sines. The target is smooth
  but never repeats, so the core wanders unpredictably without a single jump;
  the damping lets it glide, lean and settle — never oscillate, never snap.
- **Speech drives it.** Voice level and viseme openness scale every channel:
  quiet speech keeps the core almost still, normal speech gives it a natural
  sway, excited speech widens the drift and quickens the pulse. Tilt and the
  vertical bob ride the live waveform, so the movement lands on the syllables
  instead of on a timer.
- **Each state has its own body language.** Listening: near-still, slightly
  leaned-in attention. Thinking: slow, small, deliberate wander — ambient
  rather than idle. Idle: a soft breathing/orbit cycle. Speaking: rides the real
  audio.
- **Hard-bounded and self-healing.** Every channel is clamped (≤9% of the core
  radius sideways, ≤7% vertically, ≤6% forward/back, ≤3.4° tilt, −2%…+9%
  scale), so the core can never travel far from the centre. Non-finite values
  are caught and reset to rest before they are integrated, so a poisoned
  channel recovers by itself instead of throwing every frame.
- **Three layers move as one body.** The canvas face, the HUD halo and the 3D
  body all read the same spring state; the halo rides the shipped CSS vars
  (`--mx`, `--my`, `--mz`, `--mrot`, `--mswell`) on individual
  `translate`/`rotate`/`scale` properties, so the drift composes with its own
  centring and breathing animation instead of fighting them.

## Unreleased — Microphone that actually works on desktops

The laptop path was fine; on desktop PCs an external, headset or USB
microphone could open successfully and still deliver **no audio**, so JARVIS
appeared deaf while every settings dropdown looked correct. Root cause and fix:

- **Fixed-rate devices were rejected, not broken.** Capture opened every
  microphone at 16 kHz; WASAPI in shared mode resamples, but USB interfaces,
  webcams and Bluetooth HFP endpoints are fixed-rate and fail outright. The
  capture layer now probes the device's own rate (16 k → 48 k → 44.1 k → 96 k →
  …, cached per device) and opens there — PortAudio resamples into exactly the
  16 kHz int16 the pipeline expects, so the desktop path is fixed without
  touching the laptop path, which still opens at 16 kHz first.
- **A device that opens is not a device that works.** New
  `core/mic_diagnostics.py` measures a real capture (peak RMS, noise floor,
  speech headroom) and answers four separate questions — device present,
  Windows permission, capture working, *signal detected* — instead of
  mistaking a successful open for a working microphone. Thresholds sit in the
  gaps between room-silence, hiss and speech; a hot-but-working mic is credited
  for its floor rather than failed by it.
- **Windows privacy gates are read, not guessed** (`ConsentStore\microphone`,
  master + NonPackaged): a denied master switch or "desktop apps" toggle is
  named exactly, with a one-tap button that opens `ms-settings:privacy-microphone`.
  Keys absent on older builds report *unknown*, never *denied*.
- **Error text a person can act on**: PortAudio failures map to plain causes —
  device unavailable, held exclusively by another app, exclusive-mode format
  rejection, access denied.
- **Settings → Voice & Language rebuilt around the microphone**: one picker
  listing every recording endpoint (each host API shown separately, because
  "works on WASAPI, dead on DirectSound" is exactly the desktop failure),
  marked with the Windows default and default *communication* device; status
  line naming which device the capture loop **actually** opened; a live input
  meter fed by the same callback the recognizer uses; **Test** that measures
  ~0.9 s of audio and verdicts plainly ("Microphone detected — signal is
  live." / "No microphone input detected."), and on any failure the full chain
  diagnosis with fixes.
- **Device changes recover without losing the conversation**: replug / default
  switches clear the per-device rate cache and re-resolve; the existing
  keep-context reconnect path re-opens the streams. `main.py` now remembers
  which microphone the capture loop actually opened so Settings can show the
  truth when the saved device was unavailable and the default took over.
- New bridge endpoints (`mic_devices`, `mic_test`, `mic_diag`,
  `mic_open_windows_settings`, `mic_refresh`) — all off-thread; 20 new unit
  tests cover the permission gates, the measured verdicts and the rate
  fallback without ever opening a real stream.

## Unreleased — Self-training: JARVIS practises while you are quiet

Everything else learns *from the user*. This learns from what JARVIS actually
did, which is the difference between an assistant that waits to be corrected and
one that stops repeating a mistake on its own.

### Practice, not just reflection (`actions/pc_drills.py`)
- New `training_run` tool the assistant can call **on itself**: one passive,
  read-only drill on the live screen — FIND a described element, POINT the
  mouse at it (never a click), READ the screen back. Its pass or fail feeds the
  same competency ledger, so the reflection cycle now has fresh, deliberate
  evidence instead of only whatever real usage happened to leave behind
- Gated like every other action: the PC-control master switch, the autonomy
  policy table (passive drills are free — pinned by a test so the tool and the
  HUD can never drift), and the same quiet-time + hourly throttle as idle
  rounds. Power (did the drill run?) and aim (did the aim land?) are scored
  separately, so an off switch is never recorded as a failed aim
- Honest refusals: a drill that is not due says the schedule refused, a drill
  with no target says what it needs, and a miss is reported as a miss — a fake
  success would poison the very evidence the loop runs on
- The system prompt gains a `{training_rules}` block generated from the same
  config the tool enforces, telling the model when it may ask for a drill and
  that the throttle's refusal is the schedule working, not an error to retry

### The loop (`core/self_training.py`)
- **Observe** — a competency ledger (pc control, screen reading, typing, tool
  use, planning, error recovery, memory, conversation) fed by the control log:
  every click, move, drag, find and type that any module performs reports its own
  verdict, and verdicts we cannot read are ignored rather than guessed at
- **Diagnose** — the weakest capability *with real evidence* (two attempts
  minimum) and its most recent verbatim failures; a single hiccup is not enough
  to earn a drill round
- **Drill** — one model call writes at most three "next time this happens, do
  exactly this" rules for those failures. A rule that restates something already
  believed is dropped mechanically (the same near-duplicate check the memory
  extractor uses), so an empty round is possible and is reported as empty
- **Remember** — surviving rules are stored as ordinary memory (`training`, one
  entry per drill) and logged in the reversible improvements log; the same text
  rides into the live session prompt as "what you are currently practising"
- Bounded and honest: rounds at most 2/hour by default, only after the user has
  been quiet, never during speech or a task; memory off stops it completely; the
  drills are model-written and labelled ``self-scored`` everywhere they appear
- Reversible: forget one rule, forget all, or clear the ledger — the audit log
  stays because it records what happened, not what is set

### Idle driver (`main.py`)
- The learning loop now also asks `cycle_due()` whether a quiet round is owed,
  and pushes `running` → `done` to the HUD so a round is visible while it runs
- Self-training is off in exactly one situation: the user is talking

### HUD (web UI)
- **Self-training card on Home**: is it practising right now, what it is
  practising on, and real win-rate bars per capability (weakest first, with the
  trend so a stuck bar can be told apart from a climbing one), the newest rule it
  wrote for itself, and rounds/rules/hour-budget counters
- **Settings → Self-training**: the switch, how hard it practises (gentle /
  balanced / focused), what it has done so far, its playbook with one-click
  forget per rule, "practise now", and one honest paragraph on what it cannot
  touch
- While a round runs the ambience leans green through the same state hook the
  rest of the interface already uses

### Bugs found and fixed on the way
- **The narrow window lost half the HUD.** Below 980px the whole home side
  column was `display:none` — status tiles, control feed, activity list and the
  new card with it. It now stacks under the face and the home view scrolls
- **A switch could save the opposite of what you meant.** Settings pages were
  rendered from a snapshot that was only fetched when Settings was first opened,
  so a page re-rendered a moment later (a pushed event, or leaving and coming
  back) redrew the *old* value; clicking that stale switch wrote the opposite
  setting. The local snapshot now follows every save, and the open page repaints
  from the value that was just written
- The training snapshot shipped `drills` as both a list and a count, so the
  count silently overwrote the list on the way to the tab — caught by a test that
  pins both names

## Unreleased — Avatar modes no longer blind the face

Changing the avatar mode to ORBIT or HELIX looked like it destroyed the whole
UI: the stage went blank and nothing reacted to the voice any more.

- **Root cause** — the avatar canvas sized itself from its own `width`/`height`
  *attributes* (`max-width`/`max-height` around an intrinsically sized canvas).
  The renderer rewrites those attributes to the current CSS box, so the moment
  the stage had no box the renderer wrote `0x0` into them. The element then
  collapsed, which kept the next measurement at zero: the face went permanently
  blank and dead. The home view is `display:none` whenever you are in Settings
  — exactly where the mode pills live — so simply opening Settings did it, and
  the mode switch just made it obvious.
- The canvas box is now pinned to the stage (`position:absolute; inset:0`), so
  it can never be sized by the very attributes it is writing
- The render loop refuses to write a zero-size backing store, keeps the last
  frame when there is no box, and self-heals the store on the next frame
- Mode crossfades are failure-safe: a thrown swap, a queued click mid-fade or a
  frozen rAF can no longer leave the stage faded out at opacity 0

### Fresher HUD
- Bottom-corner readouts: the live avatar mode and screen-awareness state
- A seven-bar voice VU meter under the avatar; one `--voice` variable written
  once per frame drives every bar, the halo and the viseme lobes — no per-bar JS
- The avatar now drops to a calm 15 fps while idle (8 fps under reduced motion)
  and only runs full rate while it is thinking, speaking or hearing you

## Unreleased — Computer-use engine, autonomy and control HUD

The controller was the weak link, so it was repaired before anything was built
on top of it.

### One control engine, one idea of where things are (`core/pc_engine.py`)
- Grounding is a cascade — last-found context ("there"), then the Windows
  accessibility tree (exact rects, no model call, 1.2 s cache), then vision in
  two stages (coarse grid-labelled capture → ×3 crop of a ±70 px window), and
  finally nothing at all: a target that cannot be found is never clicked
- DPI and multi-monitor correctness: the process is declared per-monitor DPI
  aware, every capture's scale is measured rather than assumed, absolute moves
  normalise over the virtual desktop, and the image→screen origin defaults to
  the virtual desktop's own top-left (a monitor left of or above the primary
  used to be offset); captures use Pillow directly because pyautogui's
  screenshot crops a primary-screen grab and so mis-reads second monitors
- Typing that proves itself: find and click the field → clear → type (clipboard
  paste for long or non-Latin text, keystrokes otherwise) → read the field back
  (UIA value, else select-all/copy with the user's clipboard restored) → on a
  mismatch clear and retry once by the other method → otherwise report exactly
  what the field actually contains
- Verification is local first: a 48×27 grayscale fingerprint decides "the screen
  changed" in milliseconds, so the model is only consulted when nothing moved
- Error-state precision: an empty field, an unreadable field, a click that
  physically happened but changed nothing, and a target that does not exist are
  four different answers — not one "done"

### One tool, one call (`actions/pc_agent.py`)
- A deterministic intent router turns ordinary phrasing into concrete primitives
  with no model call at all — "move the arrow to Settings", "click that
  button", "type hello into the search box", "open YouTube", "scroll down a
  bit", "press ctrl+s", "focus the Discord window", "search youtube for lofi"
- `steps` runs a whole ordered sequence in one call, and a compound instruction
  ("open YouTube and play music") is split into its actions locally, so a
  multi-step request no longer costs a round trip per step
- Recovery instead of repetition: a failed step re-looks with a fresh tree, then
  tries the other route (keyboard for mouse, unnamed field for a named one), and
  never repeats an identical dead click
- "there" / "it" / "that one" resolve from the last thing actually found, and the
  memory is dropped the moment the foreground window changes
- Smart method selection: a bare brand name ("open YouTube") opens the site when
  the shell and the app launcher both decline; a short non-brand name is looked
  for as a real control in the current window before anything is launched

### Autonomy with real boundaries (`core/autonomy.py`)
- A policy table answers classify(intent, asked_by_user) → free / ask-if-unasked
  / always-ask. Browsing, searching, watching, reading, clicking, typing,
  scrolling, opening apps and harmless organisation are free — no question, ever.
  Money, permanent deletion, power, security and installs always ask; messages
  and posts are free when the user asked and never when autonomy invented them
- Six levers (PC control, autonomous, sight, proactive, Discord, voice) read live,
  switchable by voice ("take over my PC", "give me back control") or on the HUD;
  switching control off cancels the running step immediately rather than after it
- Autonomous idle uses the existing proactive loop plus an `IdleGovernor` — a
  grace period, a cooldown and a priority ladder that ends in "do nothing at
  all", so being handed the PC does not mean a moving mouse on an empty screen
- The prompt's boundary list is generated from the same table the tools enforce,
  so the words and the behaviour cannot drift apart

### Discord, properly (`actions/discord_control.py`)
- FOCUS → QUICK SWITCHER → MESSAGE BOX → TYPE → READ BACK → SEND → VERIFY: the
  conversation is selected through Discord's own quick switcher, the text is
  confirmed present before Enter, and the box being empty afterwards is the
  proof it sent. The recipient is never guessed, and autonomous mode cannot
  message anyone unless the user has explicitly allowed it

### Control HUD (web UI)
- An autonomy rail on Home: six live switches plus a TAKE OVER / STOP button,
  mirrored by an AUTOPILOT chip in the titlebar
- A live control feed showing every step as it happens, with the located
  coordinates and how each one was verified, and a footer naming the foreground
  window, monitor count and whether the accessibility tree is answering
- PC Control settings rewritten: the real levers, honest copy (the old "Verify
  clicks" label claimed it asked for confirmation before every click — it never
  did), a what-always-waits-for-you list, and one-tap tests

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