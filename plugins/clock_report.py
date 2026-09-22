"""JARVIS companion plugin: speak the current date & time naturally.

Let JARVIS answer "what time is it / what's today / what day" without reaching
for a browser or a system tool. Zero arguments — Gemini just calls it.
"""

from datetime import datetime

PLUGIN = {
    "name": "clock_report",
    "description": (
        "Tells the current date, day and time out loud. Trigger phrases: "
        "'what time is it?', 'what's the date?', 'today's date', 'what day "
        "is it?', 'time kya hai'. If the user is asking for a stopwatch or "
        "timer, use 'reminder' instead — NOT this plugin."
    ),
    "parameters": {"type": "OBJECT", "properties": {}},
}

def run(parameters: dict, player=None, session_memory=None) -> str:
    now = datetime.now()
    try:
        spoken = (f"It's {now.strftime('%I:%M %p').lstrip('0')} on "
                  f"{now.strftime('%A, %B %d, %Y')}.")
    except Exception as e:
        return f"Sir, I couldn't read the clock: {e}"
    if player:
        try:
            player.write_log(f"JARVIS: {spoken}")
        except Exception:
            pass
    return spoken
