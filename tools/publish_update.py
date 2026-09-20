"""Publish a JARVIS release to GitHub Releases so every installed copy
auto-updates (the app polls this repo's newest release at startup).

Usage (from the repo root, after a build or to build + publish in one go):

    python tools/publish_update.py --repo OWNER/REPO --version 1.1.0 --notes "What's new"

Steps the script performs:
    1. Bumps APP_VERSION in core/version.py (when --version is given)
    2. Builds dist/JARVIS.exe with tools/build_exe.ps1 (unless --skip-build)
    3. Computes the exe's sha256 and writes dist/update.json
    4. Creates a GitHub release v<version> with the exe + update.json as
       assets, via the `gh` CLI when available (or prints the manual steps)

    --repo is required. A release needs a git tag; gh creates it for you,
    but the tag must point at something reachable in the repo you push from.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT      = Path(__file__).resolve().parent.parent
VERSION   = ROOT / "core" / "version.py"
DIST      = ROOT / "dist"
EXE       = DIST / "JARVIS.exe"
MANIFEST  = DIST / "update.json"


def _current_version() -> str:
    m = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', VERSION.read_text(encoding="utf-8"))
    return m.group(1) if m else "0.0.0"


def _bump_version(new: str) -> None:
    VERSION.write_text(VERSION.read_text(encoding="utf-8").replace(
        f'APP_VERSION = "{_current_version()}"',
        f'APP_VERSION = "{new}"',
    ), encoding="utf-8")
    print(f"[publish] APP_VERSION -> {new}")


def _next_patch(cur: str) -> str:
    m = list(map(int, re.findall(r"\d+", cur) or [0, 0, 0])) + [0, 0, 0]
    return f"{m[0]}.{m[1]}.{m[2] + 1}"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _run(cmd: list[str]) -> None:
    print("[publish] $ " + " ".join(cmd))
    r = subprocess.run(cmd, cwd=ROOT)
    if r.returncode != 0:
        sys.exit(f"[publish] command failed: {' '.join(cmd)}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", required=True, help="GitHub repo: OWNER/REPO")
    ap.add_argument("--version", help="Version to publish (default: next patch)")
    ap.add_argument("--notes", default="", help="Release notes shown to users")
    ap.add_argument("--channel", default="stable", help="update.json channel")
    ap.add_argument("--skip-build", action="store_true",
                    help="Use the existing dist/JARVIS.exe instead of rebuilding")
    args = ap.parse_args()

    version = args.version or _next_patch(_current_version())
    version = version.lstrip("v").strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        sys.exit(f"[publish] bad version {version!r} — use X.Y.Z")

    if not args.skip_build:
        _bump_version(version)
        _run(["powershell", "-ExecutionPolicy", "Bypass",
              "-File", str(ROOT / "tools" / "build_exe.ps1")])
    elif not EXE.exists():
        sys.exit(f"[publish] {EXE} not found — run without --skip-build first")

    sha = _sha256(EXE)
    MANIFEST.write_text(json.dumps({
        "version": version,
        "channel": args.channel,
        "sha256":  sha,
        "notes":   args.notes,
        "exe_url": "",   # resolved by the app from the release assets
    }, indent=2), encoding="utf-8")
    print(f"[publish] update.json written (sha256 {sha[:12]}…)")

    tag  = f"v{version}"
    repo = args.repo.rstrip("/")

    gh = subprocess.run(["gh", "--version"], capture_output=True)
    if gh.returncode == 0:
        _run(["gh", "release", "create", tag,
              "--repo", repo,
              "--title", f"JARVIS {version}",
              "--notes", args.notes or f"JARVIS v{version}",
              str(EXE), str(MANIFEST)])
        print(f"[publish] DONE — release published: https://github.com/{repo}/releases/tag/{tag}")
        print(f"[publish] Users' apps will pick this up automatically at next startup.")
    else:
        print("\n[publish] `gh` CLI not found — publish manually:")
        print(f"  1. Upload   {EXE}")
        print(f"  2. Upload   {MANIFEST}")
        print(f"  3. Create a GitHub release tagged {tag} in {repo} with both as assets")
        print(f"     (or:  gh release create {tag} {EXE} {MANIFEST} --repo {repo} --title 'JARVIS {version}')")


if __name__ == "__main__":
    main()