"""
core/trading/chart.py — chart/screenshot analysis (§11).

Reuses the existing Gemini image plumbing (same call shape as screen_ai's
vision helpers) with a strictly factual prompt: read ONLY what is printed on
the chart, mark anything else UNREADABLE, never estimate. The post-check then
delivers the spec's honest fallback line when values are missing — the model
is never allowed to fill gaps with plausible-looking numbers.
"""
from __future__ import annotations

import time


def analyze_chart(image_path: str = "", timeframe_hint: str = "") -> dict:
    """Read a trading chart from an image path (or the current screen).
    Returns {ok, text} — text is observations only, with the readability
    caveat applied when anything material was unreadable."""
    try:
        from google.genai import types as gtypes
        from core import gemini
    except Exception as e:
        return {"ok": False, "error": f"Vision stack unavailable: {e}"}

    # Acquire the image: explicit path, else a fresh screen capture via the
    # existing computer_control capture (reused, not rebuilt).
    parts = []
    source = ""
    if image_path:
        try:
            with open(image_path, "rb") as fh:
                data = fh.read()
            parts.append(gtypes.Part.from_bytes(data=data, mime_type="image/png"))
            source = image_path
        except Exception as e:
            return {"ok": False, "error": f"Could not read image '{image_path}': {e}"}
    else:
        try:
            from actions.computer_control import _capture_with_mapping
            img, _, _ = _capture_with_mapping()
            import io
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            parts.append(gtypes.Part.from_bytes(data=buf.getvalue(),
                                                mime_type="image/png"))
            source = "current screen"
        except Exception as e:
            return {"ok": False,
                    "error": f"Screen capture unavailable: {e}. Give an "
                             f"image_path instead."}

    hint = f" The stated timeframe is {timeframe_hint}." if timeframe_hint else ""
    prompt = (
        "You are reading a trading chart image for a user. Report ONLY what is "
        "visibly printed or clearly rendered in the image, in this exact layout:\n"
        "SYMBOL: <as shown or UNREADABLE>\n"
        "TIMEFRAME: <as shown or UNREADABLE>\n"
        "LAST PRICE: <as shown or UNREADABLE>\n"
        "OHLC: <only values printed on the chart, else UNREADABLE>\n"
        "VOLUME: <if printed, else UNREADABLE>\n"
        "INDICATORS VISIBLE: <labels/values actually on the chart, else none>\n"
        "HORIZONTAL LEVELS: <price levels labelled on the chart, else none>\n"
        "VISIBLE PATTERN: <one sentence describing only what is drawn>\n"
        "TREND: <direction that can be seen from the drawn candles, else UNREADABLE>\n"
        "UNREADABLE ITEMS: <list every item above you could not read>\n"
        "Rules: do not estimate, infer, calculate, or invent ANY value. If a "
        "number is not printed and readable, it is UNREADABLE. "
        "Never guess a symbol, price, or timeframe." + hint
    )
    try:
        resp = gemini.call(parts + [prompt], tier=gemini.FAST, timeout_ms=30_000)
        text = (getattr(resp, "text", "") or "").strip()
    except Exception as e:
        return {"ok": False, "error": f"Chart read failed: {e}"}
    if not text:
        return {"ok": False,
                "error": "The vision model returned nothing for this chart."}

    low = text.lower()
    unreadable = low.count("unreadable")
    has_price = any(ch.isdigit() for ch in text.split("LAST PRICE:")[-1][:40]) \
        if "LAST PRICE:" in text else False

    out = [f"CHART READ — source: {source} at "
           f"{time.strftime('%Y-%m-%d %H:%M:%S')}",
           "OBSERVATIONS (read from the image only):", text]
    if unreadable >= 3 or not has_price:
        out.append("\nSome chart values aren't readable. Please zoom in or "
                   "provide the timeframe.")
    else:
        out.append("\nValues above are only as readable as the image — nothing "
                   "was inferred for items marked UNREADABLE.")
    return {"ok": True, "text": "\n".join(out), "unreadable_items": unreadable}
