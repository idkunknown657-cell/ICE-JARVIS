"""
core/error_recovery.py — what to do about a step that just failed.

WHY THIS EXISTS
    A multi-step task that hits an error has four sensible responses and one bad
    one. The bad one is what a naive loop does: retry the same call. Everything
    else in this project that runs steps — goal_agent, pc_agent, autonomy — makes
    that decision with local heuristics ("try one different approach, then ask"),
    which is right most of the time and blind to the difference between a network
    blip and a fundamentally impossible request.

    This module makes the decision from the actual error text, and the
    distinction it buys is worth a model call:

      retry   transient — a timeout, a lock, a page that had not finished. The
              same step, tried again, is the correct answer.
      skip    this step was not essential. The task can still succeed without it,
              and continuing is better than stopping.
      replan  the approach was wrong, not the attempt. A different tool or method
              is needed, and the caller should regenerate the step.
      abort   the request cannot be satisfied, or satisfying it would be unsafe.
              Stopping and saying so is the only honest option.

    A retry that should have been a replan costs the user the same wait twice. A
    replan that should have been an abort is how an assistant keeps hammering at
    something the user has already lost patience with. Hence this exists.

HOW IT IS CALLED
    `decide(step, error, attempt, max_attempts)` returns the four-way decision
    plus a short sentence to say out loud. Callers keep their own loop; this only
    tells them which way to turn. With no model available it falls back to a
    deterministic reading of the error text, so the behaviour is never worse than
    the heuristic it replaces.

NOTHING HERE RAISES, and nothing here acts — it classifies.
"""
from __future__ import annotations

import re

_TAG = "[Recovery]"

RETRY = "retry"
SKIP = "skip"
REPLAN = "replan"
ABORT = "abort"
DECISIONS = (RETRY, SKIP, REPLAN, ABORT)

#: Errors that are almost always worth one more attempt, matched on the message
#: the tool actually produced. Kept as plain substrings so a new error string is
#: a one-line addition rather than a regex to get wrong.
_TRANSIENT_HINTS = (
    "timeout", "timed out", "temporarily", "try again", "connection", "reset by peer",
    "connection aborted", "connection refused", "no route to host", "rate limit",
    "too many requests", "429", "503", "502", "504", "quota", "busy",
    "resource temporarily unavailable", "being used by another process",
    "permission denied (publickey)", "ssl", "handshake", "temporarily unavailable",
    "network is unreachable", "dns", "name resolution",
)

#: Errors that mean the request itself is the problem. Retrying these is pure
#: lost time, and on the unsafe group it is worse than that.
_FATAL_HINTS = (
    "not found", "no such file", "does not exist", "invalid api key", "unauthorized",
    "401", "403", "forbidden", "not supported", "unsupported", "cannot be done",
    "invalid argument", "missing required", "no such element", "element not found",
    "is not defined", "modulenotfounderror", "importerror", "syntaxerror",
    "permission denied", "access is denied", "read-only file system",
    "no results", "empty result", "returned nothing",
)

#: The subset where continuing would be actively wrong. These abort regardless of
#: whether the step was marked critical.
#:
#: Every entry has to be a *phrase that cannot appear by accident*. The first
#: draft of this list contained the bare words "order" and "format", and both fire
#: on ordinary failure prose — "failed in order to parse the response", "invalid
#: format string" — each of which would have aborted a task that was merely
#: unlucky. A false abort ends work the user still wanted, so the list trades
#: breadth for precision and leaves nuance to the model, which reads the step.
_UNSAFE_HINTS = (
    "destructive", "irreversible", "would delete", "would overwrite",
    "would format", "format c:", "format d:", "format the drive",
    "repartition", "uninstall",
    "payment", "purchase", "checkout", "place an order", "placing an order",
    "password", "credential", "api key",
    "shutdown",
)

_UNSAFE_RE = re.compile(
    r"\b(" + "|".join(re.escape(h) for h in _UNSAFE_HINTS) + r")\b", re.IGNORECASE)


def _looks_unsafe(text: str) -> bool:
    return bool(_UNSAFE_RE.search(text or ""))

#: Tools whose failure after an attempt is nearly always about the world, not the
#: code: re-trying a web fetch or a page interaction is cheap and often works.
_TRANSIENT_TOOLS = ("web_search", "browser_control", "goal_agent", "pc_agent",
                    "email_agent", "earth_intel", "market_data")


def _classify_locally(step: dict, error: str, attempt: int, max_attempts: int) -> str:
    """The no-model decision. Deliberately conservative: when the error says
    nothing recognisable, replan rather than retry, because the caller has usually
    already tried the same call once."""
    low = f"{error}".lower()
    if _looks_unsafe(low):
        return ABORT
    if any(hint in low for hint in _FATAL_HINTS):
        return REPLAN if step.get("critical") else SKIP
    if any(hint in low for hint in _TRANSIENT_HINTS):
        return REPLAN if attempt >= max_attempts else RETRY
    tool = str(step.get("tool") or "")
    if attempt < max_attempts and tool in _TRANSIENT_TOOLS:
        return RETRY
    return REPLAN


_SYSTEM = """\
You are JARVIS's error-recovery analyst. One step of a multi-step task has just
failed. Decide what to do next, and nothing about how to do it.

Choose exactly one decision:
  retry   The error is transient — a timeout, a lock, a slow page. The SAME step,
          attempted again, will probably work.
  skip    This step was not essential. The task can still succeed without it.
  replan  The approach was wrong rather than the attempt. A different tool or a
          different method is needed.
  abort   The request cannot be met, or meeting it would be unsafe. Stop.

Then give:
  reason          why it failed, in one short sentence
  fix_suggestion  for replan only: what to try instead
  say             one short sentence for JARVIS to say out loud, in the user's
                  language. Do not describe the mechanism; say what happens next.

Rules: prefer replan over retry when the same call has already failed twice.
Never choose skip for a step marked critical — choose replan instead. Choose
abort whenever the action touches money, accounts, credentials or anything
described as irreversible.

Return ONLY JSON:
{"decision": "retry|skip|replan|abort", "reason": "...", "fix_suggestion": "",
 "say": "..."}
"""


def decide(step: dict, error: str, attempt: int = 1, max_attempts: int = 2,
           timeout_ms: int = 20_000) -> dict:
    """Classify a failed step. Never raises; always returns a usable decision."""
    step = step if isinstance(step, dict) else {}
    error = str(error or "")
    attempt = max(1, int(attempt or 1))
    max_attempts = max(1, int(max_attempts or 1))

    local = _classify_locally(step, error, attempt, max_attempts)

    # Past the attempt budget a retry is not on the table, whatever the error says.
    if attempt >= max_attempts and local == RETRY:
        local = REPLAN

    result = {
        "decision": local,
        "reason": f"{step.get('tool', 'the step')} failed: {error[:200]}"
                  if error else "the step failed",
        "fix_suggestion": "",
        "say": "",
        "source": "local",
    }

    try:
        from core import gemini
        prompt = (
            f"{_SYSTEM}\n\n"
            f"Goal: {step.get('goal') or step.get('description') or '(not given)'}\n"
            f"Step: {step.get('step', '?')} — tool '{step.get('tool', '?')}'\n"
            f"Description: {step.get('description', '')}\n"
            f"Critical to the task: {'yes' if step.get('critical') else 'no'}\n"
            f"Attempt: {attempt} of {max_attempts}\n\n"
            f"Error:\n{error[:800]}\n"
        )
        reply = gemini.as_json(prompt, tier=gemini.FAST, timeout_ms=timeout_ms,
                               default=None)
    except Exception as e:
        print(f"{_TAG} analysis unavailable ({e}) — using the local reading")
        reply = None

    if not isinstance(reply, dict):
        return result

    chosen = str(reply.get("decision") or "").strip().lower()
    if chosen not in DECISIONS:
        print(f"{_TAG} unrecognised decision '{chosen}' — keeping the local reading")
        return result

    # The model does not get to overrule the two invariants: nothing critical is
    # skipped, and nothing is retried past the attempt budget.
    if chosen == SKIP and step.get("critical"):
        chosen = REPLAN
    if chosen == RETRY and attempt >= max_attempts:
        chosen = REPLAN
    # An unsafe-sounding action aborts even if the model wanted to continue.
    if _looks_unsafe(error) and chosen != ABORT:
        chosen = ABORT

    result.update({
        "decision": chosen,
        "reason": str(reply.get("reason") or result["reason"])[:300],
        "fix_suggestion": str(reply.get("fix_suggestion") or "")[:300],
        "say": str(reply.get("say") or "")[:200],
        "source": "model",
    })
    print(f"{_TAG} decision: {chosen} — {result['reason'][:100]}")
    return result


def default_line(decision: str) -> str:
    """A spoken fallback when the model gave no line of its own. Short, and about
    what happens next rather than what went wrong."""
    return {
        RETRY: "Let me try that again.",
        SKIP: "I will carry on without that part.",
        REPLAN: "That approach did not work — trying a different one.",
        ABORT: "I have stopped: that one cannot be done safely.",
    }.get(decision, "Something went wrong there.")


__all__ = ["decide", "default_line", "RETRY", "SKIP", "REPLAN", "ABORT",
           "DECISIONS"]
