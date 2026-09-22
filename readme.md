# ICE JARVIS

<p align="center">
  <a href="https://github.com/idkunknown657-cell/ICE-JARVIS"><img alt="Platform" src="https://img.shields.io/badge/platform-Windows%20Linux%20macOS-38bdf8?style=flat-square"></a>
  <a href="https://github.com/idkunknown657-cell/ICE-JARVIS/actions"><img alt="Tests" src="https://img.shields.io/badge/tests-430%20passing-31d9ae?style=flat-square"></a>
  <img alt="Python" src="https://img.shields.io/badge/python-3.11%20%7C%203.12-8b6df5?style=flat-square">
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
| **Python 3.11–3.13** (64-bit) | `python --version` — install from [python.org](https://www.python.org/downloads/) and tick **☑ Add to PATH** |
| **A microphone** | built-in or USB — used for voice input |
| **Speakers / headset** | voice output |
| **A free Gemini API key** | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) (free tier) |

Windows 10/11 is the primary target. Linux/macOS work but are tested less.

### 1. Get the code

```powershell
git clone https://github.com/idkunknown657-cell/ICE-JARVIS.git
cd ICE-JARVIS
```

### 2. Install dependencies

```powershell
python setup.py
```

That installs everything you need for **your** OS automatically
(requirements.txt carries per-platform markers, then Playwright browsers are
fetched). It takes a few minutes the first time.

### 3. Add your Gemini API key

Start the app once:

```powershell
python main.py
```

The first-run **setup screen** opens — paste your Gemini key there and press
**Save**. (Or open `config/api_keys.json` and put it under `"gemini_api_key"`.)

> 🔒 Your key is stored only in `config/api_keys.json`, which is git-ignored
> and never included in this repository or any release.

### 4. Talk

The window opens with the ICE avatar. Speak — or type in the chat box.
Say *"hello"*, *"what time is it"*, *"create a desktop shortcut"*.

> ✅ **Does the voice stay silent after the first reply?** That used to be a
> real bug and is fixed: after you stop speaking, ICE closes your turn and
> the reply comes back. If you still hear nothing, check the **mic tile** on
> the Home screen (click to unmute) and **Settings → Voice & Language** for
> input/output devices.

---

## 🎮 What you can say

| You say | ICE does |
|---|---|
| *"hello"* / *"how are you"* | chat naturally, in your language |
| *"what time is it"* | live clock/info via tools |
| *"open chrome"* / *"open steam"* | launches apps |
| *"move mouse right 100"* / *"move mouse to center"* | moves the pointer (exact pixels / % of screen) |
| *"click on the send button"* | **sees** the screen, finds the element, clicks it |
| *"press arrow down"* | presses the real keyboard arrow key |
| *"type hello world"* | types at the cursor (clipboard-backed for long text) |
| *"press ctrl+c"* / *"scroll down"* | hotkeys and scrolling |
| *"press volume up"* / *"brightness 50%"* | media & system control |
| *"take a screenshot"* | captures the screen |
| *"create a desktop shortcut"* | creates an **ICE.lnk** on your real Desktop (OneDrive-aware) — the icon launches ICE in the **background** via `pythonw.exe`, no cmd window |

Full mouse/keyboard/pc vocabulary lives in
[`actions/computer_control.py`](actions/computer_control.py) and is handed to
the model automatically — everything it can do is described in the prompt.

---

## 🔑 API keys

| Provider | Cost | Get a key | Where to put it |
|---|---|---|---|
| **Gemini** (primary) | Free tier | [aistudio.google.com](https://aistudio.google.com/apikey) | First-run setup screen |
| **GROQ** (fallback) | Free tier | [console.groq.com](https://console.groq.com/keys) | ⚙ Settings → API Keys |
| **Cerebras** (fallback) | Free tier | [cloud.cerebras.ai](https://cloud.cerebras.ai) | ⚙ Settings → API Keys |
| **OpenRouter** (fallback) | Free tier | [openrouter.ai](https://openrouter.ai/keys) | ⚙ Settings → API Keys |
| **Hugging Face** (fallback) | Free tier | [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens) | ⚙ Settings → API Keys |

Gemini carries everything. If its quota runs out, the free fallbacks answer
automatically — add any subset you like.

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

Both halves pass — 430 tests total.

---

## 📁 Project structure

```
ICE-JARVIS/
├── main.py            # entry point — boots the interface + live-voice session
├── webui.py           # Python ⇄ UI bridge: API surface, event pump, updater wiring
├── setup.py           # one-command dependency installer (OS-aware)
├── ui_web/            # the interface itself (HTML/CSS/JS, three.js avatar)
├── core/              # brain: gemini, emotion, persona, language, free_providers,
│                      # updater, screen observer, pc_input, tts/stt, memory glue
├── actions/           # tool plugins ICE can call (PC control, steam, files, …)
├── plugins/           # user-facing plugin examples
├── memory/            # local memory + config managers
├── dashboard/         # phone/web remote dashboard (FastAPI + TLS + AES)
├── tests/             # 430 unit tests
├── tools/             # release automation (publish_update.py, …)
└── JARVIS.spec        # PyInstaller recipe (optional exe builds)
```

---

## 🧯 Troubleshooting

| Problem | Fix |
|---|---|
| **`python` not found** | Install Python 3.11–3.13 from [python.org](https://www.python.org/downloads/) and tick *Add to PATH*. Reopen your terminal. |
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