"""JARVIS companion plugin: quick unit conversions (length, mass, temperature).

Fully local — no network. Covers the everyday cases a personal assistant
actually gets asked: km↔mi, kg↔lb, °C↔°F and a few friends.
"""

PLUGIN = {
    "name": "unit_converter",
    "description": (
        "Converts a value between common units (length, mass, temperature). "
        "Trigger phrases: 'how many km is 10 miles', 'what's 200°F in C', "
        "'convert 5 kg to pounds'. If the user wants currency exchange, use "
        "'web_search' instead — NOT this plugin."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "value":   {"type": "NUMBER", "description": "The numeric amount to convert."},
            "from":    {"type": "STRING", "description": "Starting unit: km, mi, m, ft, kg, lb, g, oz, C, F, K."},
            "to":      {"type": "STRING", "description": "Target unit: km, mi, m, ft, kg, lb, g, oz, C, F, K."},
        },
        "required": ["value", "from", "to"],
    },
}

# factor-to-base lookup; base units: meter, gram, kelvin
_FACTORS = {
    "km": 1000.0, "m": 1.0, "cm": 0.01, "mi": 1609.344, "ft": 0.3048,
    "kg": 1000.0, "g": 1.0, "mg": 0.001, "lb": 453.59237, "oz": 28.349523,
    "min": 60.0, "hr": 3600.0, "s": 1.0,
}


def run(parameters: dict, player=None, session_memory=None) -> str:
    try:
        value = float(parameters.get("value", 0))
        src = str(parameters.get("from", "")).strip().lower()
        dst = str(parameters.get("to", "")).strip().lower()
        if not src or not dst:
            return "Sir, tell me both the starting and the target unit."
        if src in ("c", "f", "k") or dst in ("c", "f", "k"):
            result = _convert_temp(value, src, dst)
            if result is None:
                return f"Sir, I don't know how to go from '{src}' to '{dst}'."
        else:
            if src not in _FACTORS or dst not in _FACTORS:
                return f"Sir, I don't handle '{src}' or '{dst}' yet."
            result = value * (_FACTORS[src] / _FACTORS[dst])
            result = round(result, 2)
        spoken = f"{_trim(value)} {src} is about {_trim(result)} {dst}."
        if player:
            player.write_log(f"JARVIS: {spoken}")
        return spoken
    except Exception as e:
        return f"Sir, the conversion failed: {e}"


def _convert_temp(value: float, src: str, dst: str) -> float | None:
    t = None
    if src == "c":
        t = value + 273.15
    elif src == "f":
        t = (value - 32) / 1.8 + 273.15
    elif src == "k":
        t = value
    else:
        return None
    if dst == "c":
        return round(t - 273.15, 2)
    if dst == "f":
        return round((t - 273.15) * 1.8 + 32, 2)
    if dst == "k":
        return round(t, 2)
    return None


def _trim(x: float) -> str:
    return str(x) if x == int(x) else f"{x:g}"
