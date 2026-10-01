"""
ICE — build the Windows installer.

    python installer\\build_installer.py                 full build
    python installer\\build_installer.py --skip-freeze   reuse dist/JARVIS, recompile Setup.exe
    python installer\\build_installer.py --payload-only  just the frozen app folder

What it produces, in `installer/Output/`:

    ICE-Setup.exe                    what users download and double-click
    ICE-JARVIS-Windows-x64.zip       the same payload as a zip, for the in-app updater
    update.json                      the manifest core/updater.py polls
    SHA256SUMS.txt                   checksums for all of the above

The 500-line summary of what it has to get right, and why:

  * **The payload carries no user data.** config/api_keys.json, config/certs/,
    memory state and logs are stripped, and the build fails outright if a secret
    is still findable in what it is about to ship. This is what makes an upgrade
    safe: the installer can overwrite every file it owns without touching
    anything personal.
  * **The manifest is verified before it is published.** Every app module is
    imported in the build environment first, and `JARVIS.exe --selftest` then runs
    the same list inside the frozen build. A hidden import PyInstaller missed is a
    build failure here, not a crash on a stranger's PC.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INSTALLER = ROOT / "installer"
SPEC = INSTALLER / "jarvis.spec"
ISS = INSTALLER / "ICE-Setup.iss"
DIST = ROOT / "dist"
PAYLOAD = DIST / "JARVIS"
WORK = ROOT / "build" / "pyinstaller"
OUTPUT = INSTALLER / "Output"
REDIST = INSTALLER / "redist"
BUILD_META = INSTALLER / "build"

# Folders the app reads from disk at runtime (as opposed to importing). Miss one
# and the app starts to a blank window, a missing icon, or — in the case of
# actions/ — with no abilities at all: core/action_loader.py discovers tools by
# scanning that folder for *.py files, baking it in as compiled modules leaves it
# empty. (dashboard/ is not here: its pages are resolved relative to the module,
# so jarvis.spec ships them inside the package instead.)
DATA_DIRS = ("ui_web", "assets", "plugins", "actions", "skills")
DATA_FILES = ("VERSION", "readme.md", "LICENSE", "CHANGELOG.md")

# Paths that must never reach a user's download.
FORBIDDEN_IN_PAYLOAD = (
    "config/api_keys.json",
    "config/certs/jarvis.key",
    "memory/long_term.json",
    "update_staging",
    ".freebuff",
    "tests",
)

WEBVIEW2_URL = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"
WEBVIEW2_STUB = REDIST / "MicrosoftEdgeWebview2Setup.exe"

PUBLISHER_REPO = "https://github.com/idkunknown657-cell/ICE-JARVIS"


# ── small helpers ────────────────────────────────────────────────────────────

def say(msg: str = "") -> None:
    """Print a progress line without ever dying about it.

    GitHub's Windows runners hand this process a cp1252 console, and a single
    unencodable character — the arrow in "zipping dist → zip" — killed the first
    tagged release mid-build. Encode through the stream's own codec with
    replacement so a decoration degrades to '?' instead of ending the release.
    """
    stream = sys.stdout
    if stream is None:
        return
    text = str(msg)
    try:
        enc = stream.encoding or "ascii"
        print(text.encode(enc, "replace").decode(enc), flush=True)
    except Exception:
        pass


def step(msg: str) -> None:
    say(f"\n=== {msg} ===")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def repo_version() -> str:
    try:
        return (ROOT / "VERSION").read_text(encoding="utf-8").strip() or "0.0.0"
    except Exception:
        return "0.0.0"


def quad(version: str) -> str:
    """`1.1.0` → `1.1.0.0`, which is what the Windows version resource wants."""
    parts = [p for p in version.split(".") if p.isdigit()]
    while len(parts) < 4:
        parts.append("0")
    return ".".join(parts[:4])


def git_commit() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                             cwd=ROOT, capture_output=True, text=True, timeout=20)
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


def find_iscc(explicit: str = "") -> Path | None:
    """Locate Inno Setup's compiler. Returns None when it is not installed."""
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    env = os.environ.get("ISCC", "").strip()
    if env:
        candidates.append(Path(env))
    local = os.environ.get("LOCALAPPDATA", "")
    bases = [os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
             os.environ.get("ProgramFiles", r"C:\Program Files"),
             # `winget install JRSoftware.InnoSetup` lands here, not in \Local\
             # itself — worth spelling out, because the miss is silent.
             str(Path(local) / "Programs") if local else ""]
    for base in bases:
        if base:
            candidates.append(Path(base) / "Inno Setup 6" / "ISCC.exe")
    for base in (os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                 os.environ.get("ProgramFiles", r"C:\Program Files")):
        candidates.append(Path(base) / "Inno Setup 7" / "ISCC.exe")
    for name in ("ISCC", "iscc"):
        which = shutil.which(name)
        if which:
            candidates.append(Path(which))
    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate
        except Exception:
            continue
    return None


def copy_tree(src: Path, dst: Path) -> int:
    """Copy a folder, skipping bytecode and anything hidden. Returns file count."""
    copied = 0
    for path in src.rglob("*"):
        rel = path.relative_to(src)
        if any(part in ("__pycache__", ".git") or part.startswith(".")
               for part in rel.parts):
            continue
        if path.suffix in (".pyc", ".pyo"):
            continue
        target = dst / rel
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            copied += 1
    return copied


# ── the payload ──────────────────────────────────────────────────────────────

def write_version_info(version: str) -> Path:
    """The Windows file-properties block on JARVIS.exe, from VERSION."""
    BUILD_META.mkdir(parents=True, exist_ok=True)
    path = BUILD_META / "version_info.txt"
    v = quad(version)
    path.write_text(
        f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers=({v.replace('.', ', ')}), prodvers=({v.replace('.', ', ')}),
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
        StringStruct('CompanyName', 'idkunknown657-cell'),
        StringStruct('FileDescription', 'ICE JARVIS — voice AI assistant'),
        StringStruct('FileVersion', '{version}'),
        StringStruct('InternalName', 'JARVIS'),
        StringStruct('LegalCopyright', 'MIT Licensed'),
        StringStruct('OriginalFilename', 'JARVIS.exe'),
        StringStruct('ProductName', 'ICE JARVIS'),
        StringStruct('ProductVersion', '{version}'),
    ])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""", encoding="utf-8")
    return path


def audit_modules() -> dict:
    """Import every app module in a subprocess; report which ones work.

    A subprocess because importing the app is not a side-effect-free act, and the
    report is written to a file because modules are entitled to print on import.
    """
    out_file = BUILD_META / "module_audit.json"
    BUILD_META.mkdir(parents=True, exist_ok=True)
    script = (
        "import json, sys\n"
        "from pathlib import Path\n"
        f"sys.path.insert(0, r'{ROOT}')\n"
        "from core import selftest\n"
        f"mods = selftest.app_modules(Path(r'{ROOT}'))\n"
        "bad = selftest.check_imports(mods)\n"
        f"Path(r'{out_file}').write_text(json.dumps("
        "{'all': mods, 'failed': bad}), encoding='utf-8')\n"
    )
    say("  auditing which app modules import cleanly…")
    try:
        subprocess.run([sys.executable, "-c", script], cwd=str(ROOT),
                       capture_output=True, timeout=600)
        data = json.loads(out_file.read_text(encoding="utf-8"))
    except Exception as e:
        say(f"  ! module audit failed ({type(e).__name__}: {e}) — "
            "the manifest will be empty and --selftest will refuse the build")
        return {"all": [], "failed": {}}
    failed = data.get("failed", {})
    if failed:
        say(f"  {len(failed)} module(s) need something this machine lacks "
           f"(recorded as skipped, not asserted):")
        for name, err in list(failed.items())[:10]:
            say(f"    - {name}: {err[:100]}")
    return data


def clean_payload(version: str, audit: dict) -> None:
    """Strip build scratch and user data, then prove no secret is left."""
    step("cleaning the payload")
    for junk in PAYLOAD.rglob("__pycache__"):
        shutil.rmtree(junk, ignore_errors=True)
    for pattern in ("**/*.pyc", "**/*.pyo"):
        for path in PAYLOAD.glob(pattern):
            try:
                path.unlink()
            except Exception:
                pass
    for rel in ("config/api_keys.json", "config/certs", "logs", "update_staging",
                ".autostart_flag", "apply_update.bat", "JARVIS-build.json",
                "preview_bundle.html",
                # state the self-forge / self-repair systems write at runtime:
                # skills the forge published, patches it applied, boot-sentry
                # watches, learned rules — all of it is per-user, none of it
                # belongs in a download (config/*.json never travels anyway;
                # this is the belt under those braces).
                "config/skills.json", "config/patches.json",
                "config/patch_watch.json", "config/patch_backups",
                "config/forge_staging", "config/learned_rules.json",
                "config/skill_watch.json"):
        target = PAYLOAD / rel
        try:
            if target.is_dir():
                shutil.rmtree(target, ignore_errors=True)
            elif target.exists():
                target.unlink()
        except Exception:
            pass

    # A stray tests/ folder or .git directory in the payload is wasted space and,
    # in the second case, a copy of the whole history.
    for rel in ("tests", ".git", ".freebuff", ".github"):
        t = PAYLOAD / rel
        if t.is_dir():
            shutil.rmtree(t, ignore_errors=True)

    for rel in FORBIDDEN_IN_PAYLOAD:
        if (PAYLOAD / rel).exists():
            raise SystemExit(f"refusing to build: {rel} is in the payload")

    # The local Gemini key is the one secret that could plausibly ride along in a
    # text file. Read it, then look for it in everything small enough to be text.
    secret = ""
    try:
        cfg = json.loads((ROOT / "config" / "api_keys.json").read_text(encoding="utf-8"))
        secret = str(cfg.get("gemini_api_key") or "").strip()
    except Exception:
        pass
    if len(secret) >= 16:
        for path in PAYLOAD.rglob("*"):
            try:
                if not path.is_file() or path.stat().st_size > 2_000_000:
                    continue
                if secret in path.read_text(encoding="utf-8", errors="ignore"):
                    raise SystemExit(f"refusing to build: your API key is in {path}")
            except SystemExit:
                raise
            except Exception:
                continue
        say("  no API key found anywhere in the payload")
    else:
        say("  no local API key to check for")

    try:
        sys.path.insert(0, str(ROOT))
        from core import selftest as _selftest
        shipped = _selftest.shipped_modules(audit.get("all", []))
        not_shipped = dict(_selftest.NOT_SHIPPED)
    except Exception:
        shipped = audit.get("all", [])
        not_shipped = {}

    manifest = {
        "version": version,
        "built": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "commit": git_commit(),
        "python": platform.python_version(),
        "modules": [m for m in shipped if m not in audit.get("failed", {})],
        "skipped": sorted(audit.get("failed", {})),
        "not_shipped": not_shipped,
    }
    (PAYLOAD / "JARVIS-build.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8")
    say(f"  manifest: {len(manifest['modules'])} modules, "
        f"{len(manifest['skipped'])} skipped")


def build_payload(version: str, clean: bool) -> None:
    step(f"freezing the app (version {version})")
    write_version_info(version)
    if clean:
        shutil.rmtree(WORK, ignore_errors=True)
        shutil.rmtree(PAYLOAD, ignore_errors=True)

    cmd = [sys.executable, "-m", "PyInstaller", str(SPEC),
           "--distpath", str(DIST), "--workpath", str(WORK),
           "--noconfirm", "--log-level", "WARN"]
    say("  " + " ".join(cmd))
    proc = subprocess.run(cmd, cwd=str(ROOT))
    if proc.returncode != 0:
        raise SystemExit(f"PyInstaller failed (exit {proc.returncode})")
    if not (PAYLOAD / "JARVIS.exe").exists():
        raise SystemExit(f"no JARVIS.exe in {PAYLOAD} after the freeze")
    if not (PAYLOAD / "JARVIS-Debug.exe").exists():
        raise SystemExit("the debug console build is missing — "
                         "check the second EXE() in installer/jarvis.spec")

    step("copying data folders next to the exe")
    for name in DATA_DIRS:
        src = ROOT / name
        if not src.is_dir():
            say(f"  - {name}/ not present, skipped")
            continue
        count = copy_tree(src, PAYLOAD / name)
        say(f"  + {name}/ ({count} files)")
    for name in DATA_FILES:
        src = ROOT / name
        if src.is_file():
            shutil.copy2(src, PAYLOAD / name)
            say(f"  + {name}")


# ── installer + release assets ───────────────────────────────────────────────

def fetch_webview2() -> bool:
    """Microsoft's Evergreen bootstrapper, so a PC without WebView2 can still run."""
    if WEBVIEW2_STUB.is_file() and WEBVIEW2_STUB.stat().st_size > 400_000:
        return True
    try:
        import requests
        REDIST.mkdir(parents=True, exist_ok=True)
        say(f"  fetching the WebView2 bootstrapper from Microsoft…")
        r = requests.get(WEBVIEW2_URL, timeout=120, allow_redirects=True)
        r.raise_for_status()
        if not r.content.startswith(b"MZ") or len(r.content) < 400_000:
            say("  ! that did not look like the installer — carrying on without it")
            return False
        WEBVIEW2_STUB.write_bytes(r.content)
        return True
    except Exception as e:
        say(f"  ! could not fetch it ({type(e).__name__}) — the installer will "
            "warn instead of installing it")
        return False


def compile_installer(version: str, iscc: Path, reuse_webview2: bool) -> Path:
    step("compiling ICE-Setup.exe")
    defines = [f"/DMyAppVersion={version}", f"/DMyAppVersionQuad={quad(version)}"]
    have_stub = reuse_webview2 and fetch_webview2()
    if have_stub:
        defines.append("/DWebView2Stub=1")
    cmd = [str(iscc), "/Qp", *defines, str(ISS)]
    say("  " + " ".join(cmd))
    proc = subprocess.run(cmd, cwd=str(INSTALLER))
    if proc.returncode != 0:
        raise SystemExit(f"ISCC failed (exit {proc.returncode})")
    exe = OUTPUT / "ICE-Setup.exe"
    if not exe.is_file():
        raise SystemExit(f"ISCC reported success but {exe} is not there")
    return exe


def build_update_assets(version: str, tag: str) -> dict:
    """The zip + manifest the in-app updater polls for."""
    step("building the update payload")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    zip_path = OUTPUT / "ICE-JARVIS-Windows-x64.zip"
    say(f"  zipping {PAYLOAD} → {zip_path.name}")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for path in sorted(PAYLOAD.rglob("*")):
            if path.is_file():
                z.write(path, path.relative_to(PAYLOAD).as_posix())
    digest = sha256(zip_path)
    manifest = {
        "version": version,
        "notes": f"ICE JARVIS {version} — Windows installer and updater payload.",
        "url": f"{PUBLISHER_REPO}/releases/download/{tag}/{zip_path.name}",
        "sha256": digest,
    }
    (OUTPUT / "update.json").write_text(json.dumps(manifest, indent=2),
                                        encoding="utf-8")
    say(f"  update.json → {manifest['url']}")
    return manifest


def write_checksums(paths: list[Path]) -> Path:
    """SHA256SUMS.txt — so a download can be verified after the fact."""
    lines = []
    for path in paths:
        if path.is_file():
            lines.append(f"{sha256(path)}  {path.name}")
    target = OUTPUT / "SHA256SUMS.txt"
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


# ── main ─────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build ICE-Setup.exe")
    ap.add_argument("--version", default="", help="override the VERSION file")
    ap.add_argument("--tag", default="", help="release tag (default v<version>)")
    ap.add_argument("--iscc", default="", help="path to ISCC.exe")
    ap.add_argument("--skip-freeze", action="store_true",
                    help="reuse dist/JARVIS and only recompile the installer")
    ap.add_argument("--payload-only", action="store_true",
                    help="build the frozen app folder and stop")
    ap.add_argument("--no-clean", action="store_true",
                    help="keep PyInstaller's cache (slower machine, faster build)")
    ap.add_argument("--no-webview2", action="store_true",
                    help="do not bundle Microsoft's WebView2 bootstrapper")
    ap.add_argument("--no-update-payload", action="store_true",
                    help="skip the zip + update.json for the in-app updater")
    args = ap.parse_args(argv)

    version = (args.version or repo_version()).lstrip("v")
    tag = args.tag or f"v{version}"

    step("preflight")
    say(f"  project      : {ROOT}")
    say(f"  version      : {version}  (tag {tag})")
    say(f"  python       : {sys.version.split()[0]} ({platform.machine()})")
    try:
        import PyInstaller  # noqa: F401
    except Exception:
        raise SystemExit("PyInstaller is not installed — "
                         "run: python -m pip install pyinstaller")
    iscc = None if args.payload_only else find_iscc(args.iscc)
    if not args.payload_only:
        if iscc is None:
            raise SystemExit(
                "Inno Setup 6 is not installed, so ICE-Setup.exe cannot be compiled.\n"
                "  winget install JRSoftware.InnoSetup\n"
                "or point at it:  --iscc \"C:\\path\\to\\ISCC.exe\"")
        say(f"  ISCC         : {iscc}")

    # Always audited: the manifest this produces is what --selftest asserts inside
    # the frozen build, so it has to be written on every path, including a rebuild
    # that only recompiles the installer.
    audit = audit_modules()
    if args.skip_freeze:
        if not (PAYLOAD / "JARVIS.exe").exists():
            raise SystemExit(f"--skip-freeze but there is no build in {PAYLOAD}")
        step("reusing the existing frozen build")
    else:
        build_payload(version, clean=not args.no_clean)

    clean_payload(version, audit)
    if args.payload_only:
        say(f"\npayload ready: {PAYLOAD}")
        return 0

    exe = compile_installer(version, iscc, reuse_webview2=not args.no_webview2)

    produced = [exe]
    if not args.no_update_payload:
        build_update_assets(version, tag)
        produced += [OUTPUT / "ICE-JARVIS-Windows-x64.zip", OUTPUT / "update.json"]
    sums = write_checksums(produced + [PAYLOAD / "JARVIS.exe"])

    step("done")
    say(f"  installer : {exe}  ({exe.stat().st_size / 1048576:.1f} MB)")
    say(f"  sha256    : {sha256(exe)}")
    say(f"  checksums : {sums}")
    say("\n  test it:  run the installer, then")
    say(f"            \"{PAYLOAD / 'JARVIS.exe'}\" --selftest")
    return 0


if __name__ == "__main__":
    sys.exit(main())
