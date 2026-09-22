"""
make_update.py — package the current dist/JARVIS build into a pushable update.

    python make_update.py --url https://your-host/jarvis/JARVIS_update.zip
    python make_update.py --version 2026.10.1 --notes "Softer voice, faster replies"

What it produces (next to dist/):
    JARVIS_update.zip     the whole app, zip-root = dist/JARVIS contents
    update.json           the manifest the app's updater fetches:
                              {"version": "…", "notes": "…",
                               "url": <the --url>, "sha256": "…"}

HOW YOU PUSH AN UPDATE ("from here")
  1. Build:      py -3.12 build_exe.py
  2. Bump:       edit the VERSION file  (or pass --version below)
  3. Pack:       python make_update.py --url https://<your-host>/.../JARVIS_update.zip
  4. Upload BOTH files to that host (GitHub Releases works great — upload the
     zip as a release asset and update.json to the same repo/release).
  5. Every shipped exe checks that URL (60 s after boot + the ⚙ → Advanced →
     Updates button), and installs with one click.

The recipient's config/ (their API keys, memory, learned lessons) is NEVER in
the zip unless you built it that way — make_update.py re-strips personal files
from the zip as a final guard, so a stale dist can't leak your data.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import zipfile
from pathlib import Path

HERE     = Path(__file__).resolve().parent
DIST_APP = HERE / "dist" / "JARVIS"

# same guard-rail list as build_exe.py — personal/secret stores never travel
FORBIDDEN = (
    "config/api_keys.json", "config/improvements.json",
    "config/ui_strategies.json", "config/usage_counts.json",
    "config/long_term.json", "config/certs", "config/whatsapp_web",
    "memory/long_term.json", "update_staging", "apply_update.bat",
)


def _sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser(description="Pack dist/JARVIS into an update")
    ap.add_argument("--url", required=True,
                    help="public HTTPS URL where the zip WILL be hosted")
    ap.add_argument("--version", default="",
                    help="version string (default: read the VERSION file)")
    ap.add_argument("--notes", default="",
                    help="short release notes shown in the app")
    ap.add_argument("--out", default="",
                    help="output dir (default: repo root)")
    args = ap.parse_args()

    if not args.url.startswith("https://"):
        sys.exit("✗ --url must be https:// (the app refuses insecure updates)")

    if not (DIST_APP / "JARVIS.exe").exists():
        sys.exit("✗ dist/JARVIS/JARVIS.exe not found — run build_exe.py first")

    version = (args.version
               or (HERE / "VERSION").read_text(encoding="utf-8").strip()
               or "0.0.0")

    out_dir = Path(args.out) if args.out else HERE
    zip_path = out_dir / "JARVIS_update.zip"
    json_path = out_dir / "update.json"

    print(f"Packing {DIST_APP}  →  {zip_path.name}   (v{version})")
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        skipped = []
        for path in sorted(DIST_APP.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(DIST_APP).as_posix()
            if rel in FORBIDDEN or rel.startswith(FORBIDDEN):
                skipped.append(rel)
                continue
            z.write(path, rel)
        if skipped:
            print("  ✓ stripped personal/secret files from the zip:")
            for s in skipped:
                print(f"     - {s}")

    digest = _sha256_of(zip_path)
    manifest = {
        "version": version,
        "notes": args.notes,
        "url": args.url,
        "sha256": digest,
    }
    json_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    size_mb = zip_path.stat().st_size / (1024 * 1024)
    print(f"\n✅ Update ready")
    print(f"   {zip_path.name}   {size_mb:.0f} MB   sha256={digest[:16]}…")
    print(f"   {json_path.name}")
    print("\nNEXT STEPS — push it:")
    print(f"   1. Upload {zip_path.name} so it is downloadable at:")
    print(f"        {args.url}")
    print(f"   2. Upload {json_path.name} NEXT TO it (or wherever your")
    print("      installed apps point their update source at).")
    print("   3. Done — running apps pick it up within a minute of boot,")
    print("      or instantly via ⚙ → Advanced → Updates.")


if __name__ == "__main__":
    main()
