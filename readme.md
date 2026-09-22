# ICE JARVIS

ICE JARVIS is a Windows voice companion with a warm, expressive personality. It can hold natural English, Hindi, and Hinglish conversations; remember useful context locally; speak responses; understand screen context when enabled; and help with everyday computer tasks.

## Download for Windows

1. Open the repository's **Releases** page and download the latest `ICE-JARVIS-Windows-x64.zip`.
2. Extract the ZIP to a normal folder. Keep all extracted files together.
3. Run `JARVIS.exe`.
4. On first launch, add your own Gemini API key in the setup screen. Optional fallback providers can be added later in Settings.
5. Start chatting or speaking to JARVIS.

Windows may display a SmartScreen warning because the application is not code-signed. Use **More info** then **Run anyway** only when you downloaded the build from this repository's official release.

## Highlights

- Natural, context-aware conversation in English, Hindi, and Hinglish.
- Subtle emotion-aware delivery: warm when things go well, gentle when they do not, and quiet when nothing needs saying.
- Local conversation memory with controls to review or remove saved information.
- Soft female voice, expression-aware avatar, screen sharing/awareness, and optional proactive observations.
- Voice input, typed chat, desktop assistance, browser and PC controls, reminders, files, and Steam helpers.
- Free fallback AI providers alongside Gemini: Groq, Cerebras, OpenRouter, and Hugging Face (add keys in Settings → API Keys).
- One-click self-updates: JARVIS checks the release channel, downloads, and swaps itself in on restart (Settings → Advanced → Updates).

## API keys and privacy

You provide your own Gemini API key. It is stored locally in `config/api_keys.json` next to the app and is not included in releases. Screen awareness and proactive behavior are configurable; disable them whenever you prefer.

## Development

Use Python 3.11–3.13 where possible, then run:

```powershell
python setup.py
python main.py
```

To create a distributable Windows build:

```powershell
python build_exe.py
```

The resulting `dist/JARVIS` folder is the complete portable application.

## License

See [LICENSE](LICENSE).
