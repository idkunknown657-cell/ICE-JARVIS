# ICE JARVIS

<p align="center">
  <a href="https://github.com/idkunknown657-cell/ICE-JARVIS"><img alt="Platform" src="https://img.shields.io/badge/platform-Windows%20Linux%20macOS-38bdf8?style=flat-square"></a>
  <a href="https://github.com/idkunknown657-cell/ICE-JARVIS/actions"><img alt="Tests" src="https://img.shields.io/badge/tests-880%20passing-31d9ae?style=flat-square"></a>
  <img alt="Python" src="https://img.shields.io/badge/python-3.13%20%7C%203.14-8b6df5?style=flat-square">
  <img alt="License" src="https://img.shields.io/github/license/idkunknown657-cell/ICE-JARVIS?style=flat-square">
</p>

ICE — a voice companion that runs on your own machine. It talks to you
naturally (**English / Hindi / Hinglish**), understands emotional context,
remembers things locally, watches the screen when you allow it, and controls
your PC by voice — mouse, keyboard, windows, typing, everything.

Everything personal stays on **your** PC: your API keys, your memory, your
conversations. No telemetry, no accounts, no cloud.

---

## 🚀 Quick start (run from source)

> The code **is** the app. Run it with Python — no build, no exe, no installers.

### 0. Prerequisites

| Requirement | How to check |
|---|---|
| **Python 3.13 or 3.14** (64-bit) | `python --version` — install from [python.org](https://www.python.org/downloads/) and tick **☑ Add to PATH** |
| **A microphone** | built-in or USB — used for voice input |
| **Speakers / headset** | voice output |
| **A free Gemini API key** | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) (free tier) |

Windows 10/11 is the primary target. Linux/macOS work but are tested less.

### 1. Get the code

```powershell
git clone https://github.com/idkunknown657-cell/ICE-JARVIS.git
cd ICE-JARVIS
```

### 2. Install dependencies — then start

**Windows — double-click `setup.bat`.** It finds Python 3.13+, installs
everything (requirements.txt carries per-platform markers, then Playwright
browsers), checks the config and launches JARVIS. If something fails the error
stays on screen and is written to `logs\setup.log` — it never vanishes.

Prefer a terminal? `python setup.py` does the same install step only.

### 3. Start JARVIS

| Launcher | Console? | Use it when |
|---|---|---|
| **`main.pyw`** (or the `ICE` desktop shortcut) | hidden | normal daily use — double-click it; errors go to `logs\jarvis.log` |
| **`start_debug.bat`** | visible | something isn't working — shows every print/traceback live *and* in `logs\jarvis.log` |

### 4. Add your API key(s)

The first-run **setup screen** opens on the first start. You can:

- paste a **Gemini** key, **or**
- paste one or more **free provider** keys (GROQ, Cerebras, OpenRouter, Hugging Face), **or**
- press **Initialise systems** with *nothing* — JARVIS starts anyway and shows
  *"API key required. Open Settings → API Keys."* until you add one later.

Keys can be added, edited, tested, disabled or deleted at any time in
**⚙ Settings → API Keys** — no reinstall and no source edits needed.

> 🔒 Your key is stored only in `config/api_keys.json`, which is git-ignored
> and never included in this repository or any release.

### 5. Talk

The window opens with the ICE avatar. Speak, or type in the chat box —
then just talk to it naturally, the way you'd talk to a person.

> 💬 No commands to memorize. Say what you mean in plain language:
> *"good morning"*, *"what can you do?"*, *"open the browser and play something"*.

> ✅ **Does the voice stay silent after the first reply?** That used to be a
> real bug and is fixed: after you stop speaking, ICE closes your turn and
> the reply comes back. If you still hear nothing, check the **mic tile** on
> the Home screen (click to unmute) and **Settings → Voice & Language** for
> input/output devices.

---

## ✨ Features

ICE is a full desktop AI assistant — conversational first, capable underneath.

| Capability | What it does |
|---|---|
| **Natural conversation** | Holds natural English, Hindi and Hinglish dialogue with emotional context |
| **Smart tools** | Live clock & info, web search, reminders, notes, weather, unit conversions and more |
| **App & media control** | Opens any app, plays YouTube or music, controls volume |
| **System control** | Adjusts brightness and settings on request |
| **Typing & keyboard** | Types text for you and presses real keyboard combinations |
| **Screen awareness** | Sees the screen and can point, click and navigate what you describe |
| **Mouse control** | Moves and clicks the pointer with pixel accuracy |
| **Screenshots** | Captures the screen whenever you ask |
| **Desktop shortcut** | Creates an **ICE** icon on your Desktop with one request — it launches in the background, no console window |
| **Self-training** | Keeps score of how its own clicks, keys and reads land, then practises the weakest one while you are quiet |
| **Memory** | Remembers what matters locally, across sessions |
| **Privacy** | Keys, memory and data stay on your machine — nothing leaves your PC |

ICE understands intent: instead of memorizing command words, describe
what you want in normal language and it figures out the right action —
whether that's opening an app, finding something online, or operating the
computer for you.

---

## 🎓 Self-training — it practises while you are quiet

Most assistants only learn when you correct them. ICE also learns from what it
*did*: every click, keystroke and screen reading reports whether it actually
worked, which builds a private score for each capability (PC control, screen
reading, typing, tool use, planning, error recovery). After you have been quiet
for a while it takes the worst one, reads its own recent failures and writes
itself a few concrete rules — *"when the target has no name, walk the window with
`Tab` and read the focused element back before clicking"* — then uses those rules
in the next conversation.

What it can and cannot do:

- it can only change **what JARVIS believes** — nothing else. No settings, no
  API keys, no permissions, no files, and it never drives the mouse or keyboard
  by itself;
- rounds are bounded (2 per hour by default, gentle/balanced/focused), run only
  after you have been quiet, and stop the moment you speak — or entirely when
  **memory is switched off**;
- besides reading its own history it can **practise live**: a passive, read-only
  drill that finds a described element on screen and moves the pointer onto it
  — never a click — with the pass or fail feeding the same score. The drill
  goes through the same autonomy gate and throttle as everything else;
- every rule is written by the model itself, so the interface labels them
  **self-written**, and each one can be forgotten in a click on
  **⚙ Settings → Self-training**;
- the ledger lives in `config/self_training.json` (git-ignored, never uploaded)
  and the audit trail in `config/improvements.json`, both local.

## 🔑 API keys

| Provider | Cost | Get a key | Where to put it |
|---|---|---|---|
| **Gemini** (primary) | Free tier | [aistudio.google.com](https://aistudio.google.com/apikey) | First-run setup screen |
| **GROQ** (fallback) | Free tier | [console.groq.com](https://console.groq.com/keys) | ⚙ Settings → API Keys |
| **Cerebras** (fallback) | Free tier | [cloud.cerebras.ai](https://cloud.cerebras.ai) | ⚙ Settings → API Keys |
| **OpenRouter** (fallback) | Free tier | [openrouter.ai](https://openrouter.ai/keys) | ⚙ Settings → API Keys |
| **Hugging Face** (fallback) | Free tier | [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens) | ⚙ Settings → API Keys |

Gemini is the primary — but it is **optional**: if it is missing or its quota
runs out, the free fallbacks answer automatically (add any subset you like).
Everything is managed in **⚙ Settings → API Keys**, where you can add/edit/
delete providers, enable/disable them, test each connection, and pick the
primary + fallback order. Nothing is hard-coded — a missing, invalid or
expired key never crashes JARVIS; the status line tells you what is happening
(e.g. *"Gemini API: Connected"* or *"API key required. Open Settings → API
Keys."*).

---

## 🧪 Running the tests

```powershell
python -m unittest discover -s tests
```

Heads-up: on Windows the full suite in one process can trip a Qt/COM access
violation (pre-existing, also happens on untouched upstream code). If it
crashes partway, run it in halves:

```powershell
python -m unittest discover -s tests -p "test_[a-u]*.py"
python -m unittest discover -s tests -p "test_[v-z]*.py"
```

Both halves pass — 880 tests total.

---

## 📁 Project structure

```
ICE-JARVIS/
├── main.py            # entry point — boots the interface + live-voice session
├── webui.py           # Python ⇄ UI bridge: API surface, event pump, updater wiring
├── setup.py           # one-command dependency installer (OS-aware)
├── setup.bat          # Windows first-time setup: Python check → deps → config → start
├── main.pyw           # Windows normal start (double-click, no console; logs → logs\jarvis.log)
├── start_debug.bat    # Windows debug start (visible console + logs)
├── ui_web/            # the interface itself (HTML/CSS/JS, three.js avatar)
├── core/              # brain: gemini, emotion, persona, language, free_providers,
│                      # updater, screen observer, pc_input, tts/stt, memory glue
├── actions/           # tool plugins ICE can call (PC control, steam, files, …)
├── plugins/           # user-facing plugin examples
├── memory/            # local memory + config managers
├── dashboard/         # phone/web remote dashboard (FastAPI + TLS + AES)
└── tests/             # 880 unit tests
```

---

## 🧯 Troubleshooting

| Problem | Fix |
|---|---|
| **`python` not found** | Install Python 3.13 or 3.14 from [python.org](https://www.python.org/downloads/) and tick *Add to PATH*. Reopen your terminal. |
| **Something failed at startup** | Normal mode hides the console on purpose — the full traceback is in `logs\jarvis.log` (setup errors: `logs\setup.log`). Run `start_debug.bat` to watch it live. |
| **`python setup.py` crashes with a Unicode error** | Fixed — setup now forces UTF-8 output. Just up-to-date: `git pull`. |
| **Voice doesn't respond / silent after one reply** | Mic tile on Home → unmute; Settings → Voice & Language → right input device. Voice replies depend on a working mic + speakers. |
| **No speech output** | Settings → Voice & Language → choose another output voice; some voices need a restart. |
| **Mouse click/arrow lands off-target** | Say *"move mouse to center"* then *"mouse position"* to recalibrate; scaled displays (125%/150%) are handled automatically. |
| **Desktop shortcut not created** | OneDrive accounts redirect the Desktop — ICE now asks Windows for the real Desktop path, so `ICE.lnk` may appear under `OneDrive\Desktop`. |
| **Want a clean slate** | Delete `config/api_keys.json` and `memory/long_term.json` — the app re-runs first-time setup. |

---

## 🔒 Privacy

- API keys, memory, and conversation history stay in the app folder on your PC.
- Screen awareness, proactive comments and memory are each one toggle away from
  off (⚙ Settings).
- The only network calls are the AI providers you configure and the update
  manifest on GitHub — no telemetry, no analytics.

## 📄 License

See [LICENSE](LICENSE).