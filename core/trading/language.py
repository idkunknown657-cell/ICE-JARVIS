"""
core/trading/language.py — the no-false-certainty layer (spec §16/§8).

Every generated trading report passes through here. Two jobs:

  1. BLOCK certainty claims ("guaranteed profit", "definitely buy", …) —
     templates are written to never contain them, and violations() exists so
     tests (and any future free-text path) can prove it stays that way.
  2. Carry the fixed disclaimers each report type must end with, so no
     template can forget one.

Never raises: a language check must not be able to kill a report.
"""
from __future__ import annotations

# Phrase lists are lowercased; violations() matches case-insensitively.
FORBIDDEN = (
    "guaranteed profit", "guaranteed profits", "guaranteed win", "guaranteed gain",
    "100% win", "100% wins", "100% profit", "100% profitable", "100% accurate",
    "cannot lose", "can't lose", "cannot go wrong", "can't go wrong",
    "become rich", "get rich quick", "rich quick",
    "definitely buy", "definitely sell", "definitely going up",
    "will definitely go up", "will definitely go down", "definitely go up",
    "risk-free", "risk free profit", "sure profit", "sure to profit",
    "no risk", "always wins", "always profit", "always profits",
    "this cannot lose", "you will become rich", "market will definitely",
)

# What each report type must close with (checked by tests, appended by report
# builders so no template can silently drop them).
DISCLAIMER_ANALYSIS = (
    "This is scenario-based analysis from the data shown above — not a "
    "prediction, not financial advice. Markets can invalidate any setup at any time."
)
DISCLAIMER_BACKTEST = (
    "Backtested/historical performance does not guarantee future results."
)
DISCLAIMER_JOURNAL = (
    "Past results do not guarantee future performance."
)
DISCLAIMER_DELAY = "Market data may be delayed."

# The evidence-based phrasing the model is steered toward (mirrored in tool
# descriptions): scenarios, invalidations, conditions — never certainties.
SAFE_STEERING = (
    "Current data supports this scenario. One possible setup is shown with its "
    "invalidation level; the setup becomes invalid if price closes beyond it."
)


def violations(text: str) -> list[str]:
    """Forbidden phrases found in `text` (lowercased). Never raises."""
    try:
        low = str(text or "").lower()
        return [p for p in FORBIDDEN if p in low]
    except Exception:
        return []


def ensure_safe(text: str) -> str:
    """Return `text`, with an explicit correction appended if it contains
    certainty language (defence-in-depth for any path that skips tests)."""
    try:
        bad = violations(text)
        if not bad:
            return str(text)
        return (str(text).rstrip()
                + "\n\n[CORRECTION] The phrase(s) "
                + "; ".join(f"'{b}'" for b in bad)
                + " were auto-rejected: no outcome in markets is guaranteed. "
                  "Read the scenarios and their invalidations above instead.")
    except Exception:
        return str(text)
