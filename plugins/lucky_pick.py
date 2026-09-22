"""JARVIS companion plugin: flip a coin / roll dice / pick a random option.

Playful zero-risk randomness for the moments when they can't decide or just
want a bit of luck. Fully local, no network.
"""

import random

PLUGIN = {
    "name": "lucky_pick",
    "description": (
        "Picks something at random: flips a coin, rolls dice, or chooses one "
        "item from a small list the user provides. Trigger phrases: 'flip a "
        "coin', 'toss it', 'roll a dice', 'heads or tails', 'pick one for me', "
        "'mein kya karein?'. If they want a curated suggestion about a real "
        "choice (what game, what movie), prefer 'web_search' or his judgment — "
        "NOT this plugin."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "kind":  {"type": "STRING", "description": "'coin' | 'dice' | 'pick'. Defaults to 'coin'."},
            "sides": {"type": "NUMBER", "description": "Sides on the die (dice only, default 6)."},
            "list":  {"type": "ARRAY", "items": {"type": "STRING"},
                       "description": "Items to choose from (pick only)."},
        },
        "required": [],
    },
}


def run(parameters: dict, player=None, session_memory=None) -> str:
    try:
        kind = str(parameters.get("kind", "coin")).strip().lower()
        if kind == "coin":
            spoken = "Heads" if random.random() < 0.5 else "Tails"
            spoken += " — sir."
        elif kind == "dice":
            sides = int(parameters.get("sides", 6))
            sides = 2 if sides < 2 else min(sides, 100)
            spoken = f"Rolled a d{sides}: {random.randint(1, sides)}."
        elif kind == "pick":
            items = parameters.get("list") or []
            if not items:
                return "Sir, give me the options to pick from."
            spoken = f"Go with {random.choice(list(items))} — that's my call."
        else:
            return f"Sir, I don't know a '{kind}' lucky pick."
        if player:
            player.write_log(f"JARVIS: {spoken}")
        return spoken
    except Exception as e:
        return f"Sir, luck failed me: {e}"
