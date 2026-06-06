# 🤖 MARK XL — Local AI Assistant

> **J.A.R.V.I.S** — Just A Rather Very Intelligent System  
> Cross-platform voice AI assistant running entirely on local hardware. No cloud APIs required.

---

## Overview

MARK XL is a fully local, real-time voice and visual AI agent. It combines offline speech recognition, a locally hosted LLM (via Ollama), and text-to-speech to deliver a privacy-first personal assistant with OS-level control capabilities.

Successor to the previous mark, which used the Google Gemini Live API. MARK XL removes all cloud LLM dependencies while adding streaming responses, a dynamic configuration UI, and multi-language support.

---

## Architecture

```
Microphone → STT (Whisper / Vosk)
                  ↓
           Ollama LLM (tool calling + streaming)
                  ↓
         Tool Execution (OS, Browser, Files …)
                  ↓
           TTS (EdgeTTS / Kokoro / ElevenLabs)
                  ↓
              Speaker
```

### Core Components

| Layer | Technology | Notes |
|-------|-----------|-------|
| **STT** | faster-whisper / Vosk | Fully offline. Auto language detection or forced locale. |
| **LLM** | Ollama (any model) | qwen2.5, llama3.2, mistral, etc. Streaming + tool calling. |
| **TTS** | EdgeTTS / Kokoro / ElevenLabs | EdgeTTS = free + internet. Kokoro = fully offline. |
| **UI** | PyQt6 | HUD overlay with system monitor, log panel, file drop zone. |
| **Agent** | Custom task queue | Multi-step planner + executor + error recovery. |

---

## Features

- **Streaming responses** — TTS starts speaking on the first sentence, not after the full response
- **Tool calling** — 18 built-in tools: browser control, file management, weather, YouTube, messaging, screen analysis, code helper, game updater, flight finder, and more
- **Long-term memory** — Silently saves personal facts; recalled in every conversation
- **Live configuration** — Change LLM model, STT engine, TTS voice without restarting (⚙ Configure button)
- **Ollama auto-start** — Automatically launches `ollama serve` if it's not running
- **Model warmup** — Pre-loads the LLM into memory during startup so the first message is as fast as subsequent ones
- **Multi-language STT** — Set `stt_language` to `auto` (Whisper detects) or a specific locale (`tr`, `de`, `fr`, …)
- **Cross-platform** — Windows, macOS, Linux (OS detected automatically at runtime)
- **File drop zone** — Drag and drop images, PDFs, Word docs, CSV, audio, video for AI processing

---

## Requirements

- Python 3.11 or 3.12
- [Ollama](https://ollama.com) installed and a model pulled (e.g. `ollama pull qwen2.5:7b`)
- A microphone

---

## Quick Start

```bash
# 1. Install Ollama → https://ollama.com
#    Then pull a model:
ollama pull qwen2.5:7b

# 2. Clone / download the project and launch
cd Mark-XL
python main.py
```

That's it. On first run MARK XL:
1. Auto-installs base packages (PyQt6, numpy …) and restarts once
2. Opens the **Initialisation** overlay — choose STT engine, LLM model, TTS engine
3. Click **INITIALISE SYSTEMS** — engine packages install in the background (progress shown in log)
4. JARVIS comes online

After setup, use the **⚙ CONFIGURE** button in the right panel to change any setting at any time without restarting.

---

## Configuration (`config/api_keys.json`)

```json
{
    "stt_engine":         "whisper",
    "stt_model":          "base",
    "stt_language":       "auto",
    "llm_url":            "http://localhost:11434",
    "llm_model":          "qwen2.5:7b",
    "tts_engine":         "edgetts",
    "tts_voice":          "en-US-GuyNeural",
    "elevenlabs_api_key": ""
}
```

| Key | Values | Default |
|-----|--------|---------|
| `stt_engine` | `whisper` / `vosk` | `whisper` |
| `stt_model` | `tiny` / `base` / `small` / `medium` / `large-v3` | `base` |
| `stt_language` | `auto` or ISO code (`tr`, `en`, `de` …) | `auto` |
| `llm_url` | Ollama API base URL | `http://localhost:11434` |
| `llm_model` | Any model pulled in Ollama | `qwen2.5:7b` |
| `tts_engine` | `edgetts` / `kokoro` / `elevenlabs` | `edgetts` |
| `tts_voice` | Voice name / ID depending on engine | `en-US-GuyNeural` |

---

## Built-in Tools

| Tool | Description |
|------|-------------|
| `open_app` | Opens any application or website |
| `web_search` | Web search and compare mode |
| `weather_report` | Current weather for any city |
| `send_message` | WhatsApp / Telegram messaging |
| `reminder` | Timed reminders via Task Scheduler |
| `youtube_video` | Play, summarize, trending videos |
| `screen_process` | Screen capture + vision model analysis |
| `computer_settings` | Volume, brightness, window management, shortcuts |
| `browser_control` | Full Playwright browser automation |
| `file_controller` | File/folder CRUD, search, disk usage |
| `desktop_control` | Wallpaper, organize, clean desktop |
| `code_helper` | Write, edit, explain, run code |
| `dev_agent` | Build complete multi-file projects |
| `agent_task` | Multi-step autonomous task execution |
| `computer_control` | Direct mouse/keyboard control |
| `game_updater` | Steam / Epic Games install & update |
| `flight_finder` | Google Flights search |
| `file_processor` | Process images, PDFs, CSV, audio, video |

---

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `F4` | Mute / unmute microphone |
| `F11` | Toggle fullscreen |

---

## TTS Engine Comparison

| Engine | Internet | Quality | Cost |
|--------|----------|---------|------|
| EdgeTTS | Required | Good | Free |
| Kokoro | No | Excellent | Free (local model ~100 MB) |
| ElevenLabs | Required | Best | Paid API |

---

## License

MIT — FatihMakes Industries
