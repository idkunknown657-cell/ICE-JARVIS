"""
actions/chart_analysis.py — chart/screen inspection tool (spec §11).
Reads a chart image (or the current screen) through the existing vision
stack with a strictly factual prompt; unreadable values are reported as
unreadable, never estimated.
"""
from core.trading import chart


def chart_analysis(parameters: dict, player=None, speak=None) -> str:
    p = parameters or {}
    try:
        r = chart.analyze_chart(
            image_path=str(p.get("image_path") or "").strip(),
            timeframe_hint=str(p.get("timeframe_hint") or "").strip())
        if not r.get("ok"):
            return f"Chart analysis unavailable: {r['error']}"
        return r["text"]
    except Exception as e:
        return f"chart_analysis failed: {e}"


TOOL = {
    "name": "chart_analysis",
    "description": (
        "Inspect a trading chart image (spec §11) — candlesticks, trend, "
        "support/resistance, visible indicators, volume, timeframe, labeled "
        "price levels — reading only what is printed on the chart via vision "
        "OCR. Give image_path for a file, or omit it to read the current "
        "screen. Nothing visible is invented: unreadable values come back "
        "explicitly marked, and the reply then asks the user to zoom in or "
        "provide the timeframe ('Some chart values aren't readable. Please "
        "zoom in or provide the timeframe.'). Never claim to see information "
        "the image does not show."),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "image_path": {"type": "STRING",
                           "description": "Path to a chart screenshot (PNG); omit to capture the screen."},
            "timeframe_hint": {"type": "STRING",
                               "description": "Known timeframe (e.g. '4H') to cross-check against the image."},
        },
        "required": [],
    },
    "handler": chart_analysis,
}
