# ICE JARVIS

<p align="center">
  <a href="https://github.com/idkunknown657-cell/ICE-JARVIS/releases/latest"><img alt="Release" src="https://img.shields.io/github/v/release/idkunknown657-cell/ICE-JARVIS?style=flat-square&color=38bdf8"></a>
  <a href="https://github.com/idkunknown657-cell/ICE-JARVIS/releases/latest"><img alt="Platform" src="https://img.shields.io/badge/platform-Windows%20x64-38bdf8?style=flat-square"></a>
  <a href="https://github.com/idkunknown657-cell/ICE-JARVIS/actions"><img alt="Tests" src="https://img.shields.io/badge/tests-403%20passing-31d9ae?style=flat-square"></a>
  <img alt="Python" src="https://img.shields.io/badge/python-3.11%20%7C%203.12-8b6df5?style=flat-square">
  <img alt="License" src="https://img.shields.io/github/license/idkunknown657-cell/ICE-JARVIS?style=flat-square">
</p>

A Windows voice companion with a warm, expressive personality. ICE JARVIS holds
natural **English, Hindi and Hinglish** conversations, understands emotional
context, remembers what matters locally, watches the screen when allowed, and
helps with everyday computer tasks — by voice or by chat.

Everything personal stays on your machine: your keys, your memory, your
conversations. The app self-updates, but your data never leaves your PC.

---

## 📥 Download for Windows (recommended)

1. Go to **[Releases → Latest](https://github.com/idkunknown657-cell/ICE-JARVIS/releases/latest)**.
2. Download **`ICE-JARVIS-Windows-x64.zip`**.
3. Extract the ZIP to a normal folder (keep all extracted files together).
4. Run **`JARVIS.exe`**.
5. On first launch, paste your **own free Gemini API key**
   ([get one at aistudio.google.com](https://aistudio.google.com/apikey)).
6. Start talking or typing. Done.

> **SmartScreen warning?** The app is not code-signed. Click
> *More info → Run anyway* — but only for builds downloaded from this
> repository's official Releases page.

**Updates are one click:** the app checks this repo at startup, shows an
animated update card under **⚙ Settings → Advanced → Updates**, and swaps
itself in after a restart. Your keys and memory survive every update.

---

## 🛠 Run from source (for developers)

```powershell
# 1. Get the code
git clone https://github.com/idkunknown657-cell/ICE-JARVIS.git
cd ICE-JARVIS

# 2. Install dependencies (Python 3.11–3.13, OS-specific extras auto-filtered)
python setup.py

# 3. Launch
python main.py
```

First launch shows the same setup screen — paste your Gemini key and go.

### Build your own exe

```powershell
python build_exe.py          # → dist\JARVIS\JARVIS.exe (whole folder is portable)
```

### Publish an update for other users

```powershell
python tools\publish_update.py --repo idkunknown657-cell/ICE-JARVIS
```

That bumps the version, builds, packs `update.json` + zip, creates the GitHub
release and uploads the assets — every installed copy updates itself from it.

### Run the tests

```powershell
python -m unittest discover -s tests    # 400+ unit tests
```

---

## 🔑 API keys

| Provider | Cost | Get a key | Where to put it |
|---|---|---|---|
| **Gemini** (primary) | Free tier | [aistudio.google.com](https://aistudio.google.com/apikey) | First-run setup screen |
| **GROQ** (fallback) | Free tier | [console.groq.com](https://console.groq.com/keys) | ⚙ Settings → API Keys |
| **Cerebras** (fallback) | Free tier | [cloud.cerebras.ai](https://cloud.cerebras.ai) | ⚙ Settings → API Keys |
| **OpenRouter** (fallback) | Free tier | [openrouter.ai](https://openrouter.ai/keys) | ⚙ Settings → API Keys |
| **Hugging Face** (fallback) | Free tier | [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens) (fine-grained, *"Make calls to Inference Providers"*) | ⚙ Settings → API Keys |

Gemini carries everything. If its quota runs out, the free fallbacks answer
automatically — add any subset you like. Keys are stored locally in
`config/api_keys.json` next to the app and are **never** included in releases.

---

## ✨ Highlights

- **Natural conversation** — English, Hindi, Hinglish; detects your language
  and replies in the same style. Jokes, teasing, sarcasm, support — not a
  command shell.
- **Emotional delivery** — emotion shapes words, tone, voice prosody and the
  avatar's expression. Subtle by design; silence when nothing needs saying.
- **Real memory** — sessions, projects, preferences and learned lessons live
  in local JSON; review or delete anything in the memory panel.
- **Screen awareness** — watches the screen when enabled and occasionally
  reacts to something actually worth reacting to.
- **PC control** — mouse, keyboard (incl. Unicode/Hindi), windows, volume,
  media, screenshots; destructive actions sit behind an on-screen confirm.
- **Self-updating** — sha256-verified updates from this repo's Releases,
  one click, staged safely and applied on restart.
- **Beautiful UI** — dark, glassy, animated; live theming; the holographic
  face is her real face, rendered locally.

---

## 📁 Project structure

```
ICE-JARVIS/
├── main.py            # entry point — boots the WebView2 interface
├── webui.py           # Python ⇄ UI bridge: API surface, event pump, updater wiring
├── ui_web/            # the interface itself (HTML/CSS/JS, three.js avatar)
├── core/              # brain: gemini, emotion, persona, language, free_providers,
│                      # updater, screen observer, pc_input, tts/stt, memory glue
├── actions/           # tool plugins JARVIS can call (PC control, steam, files…)
├── plugins/           # user-facing plugin examples
├── memory/            # local memory + config managers
├── tests/             # 400+ unit tests (python -m unittest discover -s tests)
├── tools/             # build_exe.ps1, publish_update.py (release automation)
├── JARVIS.spec        # PyInstaller recipe → dist/JARVIS/JARVIS.exe
└── build_exe.py       # one-command Windows build
```

---

## 🧯 Troubleshooting

| Problem | Fix |
|---|---|
| **"Python not found" (source run)** | Install Python 3.11–3.13 from [python.org](https://www.python.org/downloads/) and tick *Add to PATH*. |
| **Voice doesn't respond** | Check the mic tile on the Home screen (click to unmute); Settings → Voice & Language → pick the right input device. |
| **No speech output** | Settings → Voice & Language → choose another output voice; some voices need the app restarted once. |
| **Update check fails** | Settings → Advanced → Updates → *Save source* with a blank field restores the default GitHub channel. Corporate proxies can block github.com — try again on a normal network. |
| **SmartScreen blocks the exe** | *More info → Run anyway* (unsigned build). Verify you downloaded it from this repo's Releases. |
| **Antivirus flags the exe** | PyInstaller builds trip heuristics. The build scripts never touch your key stores; add an exclusion if you're comfortable. |
| **Want a clean slate** | Delete `config/api_keys.json` and `memory/long_term.json` next to the exe — the app re-runs first-time setup. |

---

## 🔒 Privacy

- API keys, memory, and conversation history stay in the app folder on your PC.
- Screen awareness, proactive comments and memory are each one toggle away
  from off (⚙ Settings).
- The only network calls are the AI providers you configure and the update
  manifest on GitHub — no telemetry, no analytics, nothing else.

## 📄 License

See [LICENSE](LICENSE).
