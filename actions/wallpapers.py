"""actions/wallpapers.py — change, cycle and preview wallpapers from a folder.

The wallpaper collection lives in a local folder (config `wallpaper_folder`,
defaults to the WinCux wallpapers directory). "Change the wallpaper" simply
steps to the next image there; "preview the sunset one" opens it in the default
viewer before applying. All images are local — no URLs are touched.

Actions:
  list        - show the wallpapers available
  set         - apply a specific one by name (or index)
  next        - step forward through the folder
  previous    - step back
  random      - pick one at random
  preview     - open one in the default image viewer (name or index)
  open        - open the wallpaper folder in Explorer

Writes are reversible and cost nothing, so no confirmation gate.
"""
from __future__ import annotations

import os
import random
from pathlib import Path

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".gif"}


def _folder() -> Path:
    try:
        from memory.config_manager import get_wallpaper_folder
        return Path(get_wallpaper_folder()).expanduser()
    except Exception:
        return Path(r"C:\Users\kakud\AppData\Local\WinCux\data\wallpapers")


def _images() -> list[Path]:
    f = _folder()
    if not f.is_dir():
        return []
    try:
        return sorted(
            p for p in f.iterdir()
            if p.is_file() and p.suffix.lower() in _IMAGE_EXTS
        )
    except OSError:
        return []


def _label(idx: int, total: int) -> str:
    return f"({idx + 1} of {total})"


def _apply(path: Path) -> str:
    import actions.desktop as desktop
    result = desktop.set_wallpaper(str(path))
    return result


def _remember_index(idx: int) -> None:
    try:
        from memory.config_manager import save_wallpaper_index
        save_wallpaper_index(idx)
    except Exception:
        pass


def _current_index(images: list[Path]) -> int:
    """The remembered position to step from."""
    try:
        from memory.config_manager import get_wallpaper_index
        return min(get_wallpaper_index(), len(images) - 1)
    except Exception:
        return 0


def _pick_by_name(name: str, images: list[Path]) -> Path | None:
    low = (name or "").strip().lower()
    if not low:
        return None
    for i, p in enumerate(images):
        if p.name.lower() == low or p.stem.lower() == low or low in p.stem.lower():
            return p
    return None


def _describe(images: list[Path], start: int = 0, limit: int = 20) -> str:
    lines = [f"Wallpapers in {_folder()} ({len(images)}):"]
    shown = images[start:start + limit]
    for i, p in enumerate(shown, start=start):
        lines.append(f"{i + 1}. {p.name}")
    if len(images) > start + limit:
        lines.append(f"... and {len(images) - start - limit} more")
    return "\n".join(lines)


def wallpapers(
    parameters: dict = None,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    params = parameters or {}
    action = str(params.get("action") or "next").strip().lower()
    name   = str(params.get("name") or "").strip()
    index  = params.get("index")

    images = _images()
    if not images:
        return (f"No wallpapers found in {_folder()} — put some jpg/png/webp "
                f"images there and say 'change wallpaper' again.")

    if action in ("list", "show", "ls"):
        return _describe(images)

    if action in ("open", "folder"):
        try:
            if os.name == "nt":
                os.startfile(_folder())          # type: ignore[attr-defined]
            else:
                import subprocess
                subprocess.Popen(["xdg-open", str(_folder())])
            return f"Opened the wallpaper folder {_folder()}."
        except Exception as e:
            return f"Could not open the wallpaper folder: {e}"

    if action == "preview":
        target = _resolve_target(images, name, index)
        if target is None:
            return f"Wallpaper not found: {name or index or 'that one'}."
        try:
            if os.name == "nt":
                os.startfile(target)             # type: ignore[attr-defined]
            else:
                import subprocess
                subprocess.Popen(["xdg-open", str(target)])
            return f"Previewing {target.name} in the image viewer."
        except Exception as e:
            return f"Could not open the preview: {e}"

    if action in ("set", "apply"):
        target = _resolve_target(images, name, index)
        if target is None:
            return f"Wallpaper not found: {name or index or 'that one'}."
        return _apply_and_label(target, images)

    if action in ("previous", "prev"):
        idx = (_current_index(images) - 1) % len(images)
    elif action in ("random", "shuffle"):
        idx = random.randrange(len(images))
    else:                                        # "next" and default
        idx = (_current_index(images) + 1) % len(images)
    return _apply_and_label(images[idx], images)


def _apply_and_label(target: Path, images: list[Path]) -> str:
    result = _apply(target)
    saved_idx = images.index(target)
    _remember_index(saved_idx)
    tag = _label(saved_idx, len(images))
    if result.lower().startswith("wallpaper set"):
        return f"{result}  {tag}"
    return result


def _resolve_target(images: list[Path], name: str, index) -> Path | None:
    if index is not None:
        try:
            i = int(index)
        except (TypeError, ValueError):
            i = -1
        if 0 <= i < len(images):
            return images[i]
    if name:
        return _pick_by_name(name, images)
    return None


def change_wallpaper(direction: str = "next") -> str:
    """Convenience: 'next'/'previous'/'random' step on the folder."""
    images = _images()
    if not images:
        return (f"No wallpapers found in {_folder()} — put some jpg/png/webp "
                f"images there and say 'change wallpaper' again.")
    idx = _current_index(images)
    if direction == "previous":
        idx = (idx - 1) % len(images)
    elif direction == "random":
        idx = random.randrange(len(images))
    else:
        idx = (idx + 1) % len(images)
    result = _apply(images[idx])
    _remember_index(idx)
    tag = _label(idx, len(images))
    if result.lower().startswith("wallpaper set"):
        return f"{result}  {tag}"
    return result


# ── Tool declaration (auto-discovered by core/action_loader.py) ──────────────
TOOL = {
    "name": "wallpapers",
    "description": (
        "Changes the desktop wallpaper from a local folder full of images, or "
        "previews one first. Use for requests like 'change the wallpaper', "
        "'next wallpaper', 'show me a wallpaper', 'preview the sunset one' or "
        "'open the wallpaper folder'. It steps through an existing local "
        "collection — it never downloads or touches image URLs. "
        "actions: list | set | next | previous | random | preview | open."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "list (show names), set (apply by name or index), "
                               "next, previous, random, preview (open in viewer), "
                               "open (show the folder). Default: next.",
            },
            "name": {
                "type": "STRING",
                "description": "Image filename or part of it, e.g. 'sunset.jpg' "
                               "or 'sunset'.",
            },
            "index": {
                "type": "INTEGER",
                "description": "1-based position in the folder list.",
            },
        },
        "required": [],
    },
    "handler": wallpapers,
}