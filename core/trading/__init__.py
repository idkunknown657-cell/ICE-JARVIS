"""core.trading — JARVIS's trading / market-analysis capability.

A self-contained engine layered onto the existing architecture: actions/*.py
front-ends call into these modules; confirmation reuses core/confirm.py;
vision reuses the existing Gemini image plumbing; nothing here rebuilds the
assistant itself. Two rules govern everything inside:

  * never fabricate market information — failures say what the provider said;
  * never promise outcomes — reports are scenarios with invalidations, and
    live orders can only move behind the user's own confirmation.
"""
