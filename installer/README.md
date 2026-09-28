# Windows installer

Turns this checkout into `ICE-Setup.exe` — the file people download, double-click,
and end up with JARVIS in their Start Menu. No Python on their machine, no
commands, no administrator rights.

## Build it

```powershell
python -m pip install pyinstaller            # once
winget install JRSoftware.InnoSetup          # once (the compiler for Setup.exe)

python installer\build_installer.py          # -> installer\Output\ICE-Setup.exe
```

Useful switches:

| Switch | What it does |
|---|---|
| `--version 1.1.0` | Override the `VERSION` file (CI passes the tag) |
| `--skip-freeze` | Reuse `dist\JARVIS` and only recompile Setup.exe (~3 min instead of ~10) |
| `--payload-only` | Build the frozen app folder and stop — pair with `JARVIS-Debug.exe --selftest` |
| `--no-webview2` | Do not bundle Microsoft's WebView2 bootstrapper |
| `--iscc PATH` | Point at `ISCC.exe` when it is not in a standard location |

Output lands in `installer\Output\` (git-ignored):

```
ICE-Setup.exe                 122 MB, what users download
ICE-JARVIS-Windows-x64.zip    the same payload for the in-app updater
update.json                   the manifest core/updater.py polls
SHA256SUMS.txt                checksums to verify a download
```

## How it is put together

```
installer/entry.py         frozen entry point: --version, --selftest,
                           --install-browser-deps, and crash reporting
installer/jarvis.spec      PyInstaller: JARVIS.exe (windowed) + JARVIS-Debug.exe
installer/hooks/           packaging hooks (Playwright's node driver is data)
installer/ICE-Setup.iss    Inno Setup: shortcuts, uninstall, upgrades
installer/build_installer.py  runs the three, strips secrets, writes the manifest
core/selftest.py           the completeness check both the build and the installed
                           app run — see below
core/browser_deps.py       the optional Playwright browser download
```

Four decisions are worth knowing before changing any of it.

**The install is per-user** (`%LOCALAPPDATA%\Programs\JARVIS`) and needs no admin
rights. JARVIS writes `config/`, `memory/` and `logs/` next to its own exe, so it
has to live somewhere the user can write.

**The payload contains no user data at all.** `config/api_keys.json`,
`config/certs/`, `logs/` and `memory` state are stripped, and the build fails
outright if a secret is still findable in what it is about to ship. That is what
makes an upgrade safe: the installer may overwrite every file it owns without
touching anything personal. Verified by installing 1.0.1, planting a key and
memory, then installing 1.1.0 over it — both survived.

**The frozen build verifies itself.** `build_installer.py` imports every app
module in the build environment to write `JARVIS-build.json`, and
`JARVIS.exe --selftest` re-runs that list *inside* the packaged app, along with 17
key libraries and the data files the app reads off disk. It is what caught three
things that would otherwise have shipped broken:

  * 54 modules missing, including every one of the `actions/*` tools JARVIS
    reaches for by name — PyInstaller's static analysis cannot see them;
  * `actions/` shipped as compiled modules, which left the installed app with
    **42 abilities missing and no error** — `core/action_loader.py` discovers
    tools by scanning that folder for `.py` files, so they ship as files;
  * the phone dashboard looking for its pages and its config inside `_internal/`.

Run it after installing, or on any packaged build:

```powershell
& "$env:LOCALAPPDATA\Programs\JARVIS\JARVIS-Debug.exe" --selftest
```

**Playwright's browsers are optional.** The driver ships (the app cannot
automate a browser without it), the 450 MB of Chromium and Firefox do not: the
installer offers them as a tick-box that runs `JARVIS.exe --install-browser-deps`.
JARVIS drives the Chrome or Edge already on the PC first, so this is the fallback
engine for a machine with neither.

## Releasing

Push a tag; CI does the rest.

```powershell
git tag v1.1.0
git push origin main v1.1.0
```

`.github/workflows/release.yml` builds `ICE-Setup.exe` on a clean Windows runner,
**fails the release if `--selftest` finds a problem**, then publishes
`ICE-Setup.exe`, the updater zip, `update.json` and the checksums on the Release.
That `update.json` is what updates everyone already running JARVIS — publishing a
release without it is what makes the in-app updater report "manifest
unreachable".

## Testing an installer before you ship it

```powershell
$setup = "installer\Output\ICE-Setup.exe"
& $setup /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LOG="$env:TEMP\ice-install.log"
& "$env:LOCALAPPDATA\Programs\JARVIS\JARVIS-Debug.exe" --selftest
Get-Content "$env:LOCALAPPDATA\Programs\JARVIS\logs\jarvis.log" -Tail 30
& "$env:LOCALAPPDATA\Programs\JARVIS\unins000.exe" /VERYSILENT   # keeps your data
```

Expect `Action discovery complete: 42 active`, four plugins, and the dashboard
coming up on `https://<your-lan-ip>:8000`. A console build is used for all of this
because a windowed exe has nowhere to print.

> Under Git Bash the flags need `MSYS_NO_PATHCONV=1`, or `/VERYSILENT` arrives as
> `C:/Program Files/Git/VERYSILENT` and the wizard opens instead.
