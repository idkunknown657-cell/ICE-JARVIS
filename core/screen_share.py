"""Background screen-share tuning — the local, cheap half of the "eyes".

The always-on glance loop polls the screen and keeps the newest downscaled frame
plus a one-line caption in memory for proactive check-ins and screen questions.
Its cost is pure local CPU/quota: capture, JPEG encode, and a caption model call
per refresh. This module owns the two knobs that keep that cost at the level the
user picked (via the UI) instead of burning resources on an unchanged screen:

  - quality tables: poll cadence + caption TTL per light/medium/high;
  - frame_fingerprint: a tiny perceptual hash used to skip ALL work — caption,
    re-store, re-encode — while the screen is essentially static.

It is deliberately dependency-free (only the standard library) so main.py can
use it from its asyncio loop without pulling capture logic into the hot path.
"""

import hashlib


# Poll interval (seconds) between background screen samples, per quality level.
# light is the "never lag me" preset; high approaches a live view.
POLL_SECS = {"light": 20.0, "medium": 8.0, "high": 4.0}

# Min seconds between vision-caption model calls for a given quality. The
# caption is the only model call in the loop, so this is the quota dial.
CAPTION_TTL_SECS = {"light": 240.0, "medium": 120.0, "high": 60.0}

_DEFAULT_QUALITY = "medium"


def settings_for(quality: str) -> tuple[float, float]:
    """(poll_secs, caption_ttl_secs) for a quality level. Unknown values fall
    back to medium and never raise."""
    q = (quality or "").strip().lower()
    if q not in POLL_SECS:
        q = _DEFAULT_QUALITY
    return POLL_SECS[q], CAPTION_TTL_SECS[q]


def frame_fingerprint(data: bytes) -> str:
    """A tiny perceptual hash of an encoded frame. Identical screens produce the
    identical hash (so the loop can skip every heavy step); small pixel noise
    (video shimmer, encoder variance) is absorbed by a 4-bit drop, while a real
    change — new window, cursor movement, scrolling — flips the hash. Empty when
    the frame cannot be decoded."""
    if not data:
        return ""
    try:
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(data)).convert("L")
        img.thumbnail((16, 12), Image.BILINEAR)
        px = bytearray(192)
        # list() of the 16x12 thumbnail is exactly 192 values. getdata() is
        # deprecated (removed in Pillow 14); get_flattened_data replaces it.
        try:
            raw = list(img.get_flattened_data())
        except AttributeError:            # Pillow < 11.2
            raw = list(img.getdata())
        for i, p in enumerate(raw):
            if i < 192:
                px[i] = (p >> 3) & 63
        return hashlib.md5(bytes(px)).hexdigest()
    except Exception:
        return ""


def _grid_hashes(data: bytes, grid: int = 4) -> tuple:
    """Perceptual hash per grid cell of the frame (4x4 = 16 hashes).

    Used by core.visual_memory.change_regions to locate WHERE a frame changed:
    each cell is hashed from its own 4x3 sub-thumbnail, so a change inside one
    cell flips that cell's hash and leaves the other 15 alone. Returns () when
    the frame cannot be decoded."""
    if not data:
        return ()
    try:
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(data)).convert("L")
        W, H = img.size
        hashes = []
        for r in range(grid):
            for c in range(grid):
                box = (c * W // grid, r * H // grid,
                       (c + 1) * W // grid, (r + 1) * H // grid)
                cell = img.crop(box)
                cell = cell.resize((8, 6), Image.BILINEAR)
                try:
                    raw = cell.get_flattened_data()
                except AttributeError:    # Pillow < 11.2
                    raw = cell.getdata()
                px = bytes((p >> 3) & 31 for p in raw)
                hashes.append(hashlib.md5(px).hexdigest()[:4])
        return tuple(hashes)
    except Exception:
        return ()