"""Publish an ICE JARVIS release: build the web-UI exe, pack it into an update
bundle, and publish it to GitHub Releases so every installed copy auto-updates
(the app polls this repo's newest release at startup).

Usage (from the repo root):

    python tools/publish_update.py --repo idkunknown657-cell/ICE-JARVIS [--version 1.0.2]

What it does:
    1. Bumps core/version.py (and the VERSION file) to the requested version
       (default: next patch above the current one).
    2. Builds dist/JARVIS/JARVIS.exe via tools/build_exe.ps1 (unless --skip-build).
    3. Packs the build with make_update.py — JARVIS_update.zip + update.json
       (version, notes, url, sha256), then renames the zip to the friendly
       release-asset name ICE-JARVIS-Windows-x64.zip.
    4. Creates a GitHub release v<version> and uploads BOTH files as assets,
       using the GitHub API directly (token from your Git credential manager —
       no `gh` CLI needed).

The app's updater fetches update.json from the "latest" release at startup, so
just publishing creates the update path.

Requires:  requests, and a GitHub credential already stored in git
           (a Personal Access Token with `repo` scope is all that's needed).
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import requests

ROOT      = Path(__file__).resolve().parent.parent
VERSION   = ROOT / "core" / "version.py"
DIST      = ROOT / "dist"

API   = "https://api.github.com"
UPLOAD_ATTR_SUFFIX = "?name="


# ── helpers ───────────────────────────────────────────────────────────────────

def _token() -> str:
    """Pull the GitHub PAT out of the OS credential manager via `git credential`.
    Uses the same credential entry `git push` uses, so no token is stored in
    any repo file or printed anywhere."""
    p = subprocess.run(
        ["git", "credential", "fill"],
        input="protocol=https\nhost=github.com\n\n",
        capture_output=True, text=True, encoding="utf-8",
    )
    out = p.stdout
    tok = ""
    for line in (out or "").splitlines():
        if line.startswith("password="):
            tok = line[len("password="):].strip()
    if not tok:
        raise SystemExit(
            "[publish] No GitHub token found in the credential store.\n"
            "  Run once:  git credential approve  (and store a token with `repo` scope)\n"
            "  or set the GH_TOKEN environment variable and try again.")
    return tok


def _gh_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _current_version() -> str:
    m = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', VERSION.read_text(encoding="utf-8"))
    return m.group(1) if m else "0.0.0"


def _next_patch(cur: str) -> str:
    m = [int(x) for x in re.findall(r"\d+", cur)] + [0, 0, 0]
    return f"{m[0]}.{m[1]}.{m[2] + 1}"


def _bump_version(new: str) -> None:
    _ver = _current_version()
    VERSION.write_text(VERSION.read_text(encoding="utf-8").replace(
        f'APP_VERSION = "{_ver}"', f'APP_VERSION = "{new}"'), encoding="utf-8")
    vfile = ROOT / "VERSION"
    if vfile.exists():
        vfile.write_text(f"{new}\n", encoding="utf-8")
    print(f"[publish] version -> {new}")


def _run(cmd: list[str]) -> None:
    print("  $ " + " ".join(cmd))
    r = subprocess.run(cmd, cwd=ROOT)
    if r.returncode != 0:
        sys.exit(f"[publish] command failed: {' '.join(cmd)}")


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", required=True, help="GitHub repo: OWNER/REPO")
    ap.add_argument("--version", help="Version to publish (default: next patch)")
    ap.add_argument("--notes", default="", help="Release notes shown in the app")
    ap.add_argument("--skip-build", action="store_true",
                    help="Use the existing dist/ICE/ICE.exe instead of rebuilding")
    args = ap.parse_args()

    version = args.version or _next_patch(_current_version())
    version = version.lstrip("v").strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        sys.exit(f"[publish] bad version {version!r} — use X.Y.Z")

    if not args.skip_build:
        _bump_version(version)
        _run(["powershell", "-ExecutionPolicy", "Bypass",
              "-File", str(ROOT / "tools" / "build_exe.ps1")])
    elif not (DIST / "JARVIS" / "JARVIS.exe").exists():
        sys.exit(f"[publish] {DIST / 'JARVIS' / 'JARVIS.exe'} not found — run without --skip-build first")

    zip_path  = DIST / "ICE-JARVIS-Windows-x64.zip"
    manifest  = DIST / "update.json"
    asset_url = f"https://github.com/{args.repo}/releases/download/v{version}/ICE-JARVIS-Windows-x64.zip"
    _run(["python", str(ROOT / "make_update.py"),
          "--url", asset_url, "--version", version, "--notes", args.notes])

    # make_update.py writes JARVIS_update.zip next to dist/; ship it under the
    # friendly asset name users see on the Releases page.
    raw_zip = DIST.parent / "JARVIS_update.zip"
    if raw_zip.exists():
        shutil.move(str(raw_zip), zip_path)
    if not (zip_path.exists() and manifest.exists()):
        sys.exit("[publish] make_update.py did not produce the expected files")

    print("==> Publishing to GitHub Releases...")
    token = _token()
    gh  = _gh_headers(token)
    repo = args.repo.rstrip("/")
    tag  = f"v{version}"

    # tag + release on the current branch head
    _run(["git", "tag", tag])
    _run(["git", "push", "origin", tag])

    rel = requests.post(
        f"{API}/repos/{repo}/releases",
        headers=gh, data=json.dumps({
            "tag_name": tag, "name": f"ICE JARVIS {version}", "body": args.notes,
        }), timeout=60)
    if rel.status_code not in (201, 200):
        sys.exit(f"[publish] release create failed ({rel.status_code}): {rel.text}")
    release = rel.json()
    print(f"  release created: {release['html_url']}")

    upload_url = release["upload_url"].split("{")[0]
    for path, name in ((zip_path, "ICE-JARVIS-Windows-x64.zip"), (manifest, "update.json")):
        with path.open("rb") as f:
            up = requests.post(
                f"{upload_url}?name={name}",
                headers={**gh, "Content-Type": "application/octet-stream"},
                data=f, timeout=300)
        if up.status_code not in (201, 200):
            sys.exit(f"[publish] asset {name} upload failed ({up.status_code}): {up.text}")
        print(f"  uploaded {name} ({path.stat().st_size / 1e6:.1f} MB)")

    print(f"\n[publish] DONE — published: {release['html_url']}")
    print("  Every installed copy checks this repo's latest release at startup and updates itself.")


if __name__ == "__main__":
    main()
