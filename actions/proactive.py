"""
ProactiveEngine 2.0 — context-aware, time-aware, non-repetitive background prompting.
Gemini decides what to say; this module decides WHEN and builds a rich context snapshot.
"""
import io
import time
from datetime import datetime


def screen_glance(data: bytes | None = None, mime: str | None = None) -> str | None:
    """One downscaled screenshot → a one-line "what the user is doing" summary
    via the FAST vision model. Returns None on any problem or when the screen
    is blank/locked so callers can silently skip the glance. The pixels are
    sent to the model but never stored anywhere on disk.

    `data`/`mime` let the caller hand over a frame it already captured (the
    always-on glance loop does this) so we do not snap a second screenshot for
    every summary refresh."""
    try:
        from core import gemini
        from google.genai import types as gtypes
        from actions.screen_processor import _capture_screen
    except Exception:
        return None
    try:
        if not data:
            data, mime = _capture_screen()
        if not data:
            return None
        # A one-liner needs few pixels: shrink to ~512px on the long side so the
        # FAST call returns in a second or two even on a low-end machine.
        try:
            from PIL import Image
            img = Image.open(io.BytesIO(data)).convert("RGB")
            img.thumbnail((512, 512), Image.BILINEAR)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=70, optimize=False)
            data, mime = buf.getvalue(), "image/jpeg"
        except Exception:
            pass  # keep whatever _capture_screen produced
        prompt = (
            "Screenshot of the user's screen. In at most 2 short sentences, say "
            "specifically what the user is doing (app, document/page, task) and "
            "one small detail that would let an assistant chime in usefully. No "
            "greeting, no markdown, no preamble. If the screen is blank, locked "
            "or unreadable, reply exactly: NOTHING"
        )
        # gemini.text (not .call) so a drained Gemini is absorbed by the
        # configured free-provider fallback instead of returning nothing.
        text = gemini.text(
            [gtypes.Part.from_bytes(data=data, mime_type=mime), prompt],
            tier=gemini.FAST, timeout_ms=20_000, default="")
        text = (text or "").strip()
        if not text or "NOTHING" in text.upper():
            return None
        return " ".join(text.split())[:400]
    except Exception as e:
        print(f"[Proactive] screen glance failed: {e}")
        return None


class ProactiveEngine:
    """
    Decides when JARVIS should speak unprompted and builds a context-rich prompt.

    Improvements over 1.0:
      - Time-of-day awareness  (morning / afternoon / evening / night)
      - Monitor-topic awareness (what the user is tracking)
      - Recent-session context  (last few turns of the current conversation)
      - Non-repetitive          (rotates context focus to avoid same opener)
      - Smarter silence gate    (doesn't fire while JARVIS is speaking)
      - Talk cadence            (standard / warm / live — see CADENCES)

    Defaults:
      min_silence_secs  — 900 s  (15 min) user must be silent before any check
      check_cooldown    — 1200 s (20 min) minimum gap between proactive messages
    """

    # Prompts how often JARVIS may speak unprompted. `live` is the "frequent but
    # smart" mode from the spec: the gate virtually never blocks, but the model
    # (the same prompt that says "stay silent if nothing meaningful") decides the
    # actual rate, so it is near-continuous presence without spam.
    CADENCES = {
        "standard": (900,  1200),   # (min_silence_secs, check_cooldown)
        "warm":     (180,  360),
        "live":     (30,   90),
        # Companion mode — the "never sits quietly when you are around" profile.
        # Evaluated every 15 s with a 60 s floor: at most one message a minute,
        # so it stays present without burning quota at the 15 s rate.
        "chatty":   (15,   60),
    }

    def __init__(
        self,
        min_silence_secs: int = 900,
        check_cooldown:   int = 1200,
    ):
        self.min_silence_secs = min_silence_secs
        self.check_cooldown   = check_cooldown
        self._cadence         = "standard"
        self._last_triggered  = 0.0
        self._rotation        = 0          # cycles through context focus areas

    # ── Trigger gate ───────────────────────────────────────────────────────────

    def apply_cadence(self, cadence: str) -> str:
        """Switch the talk cadence. Returns the cadence actually applied —
        unknown names fall back to 'standard' and never raise."""
        cadence = str(cadence or "").strip().lower()
        if cadence not in self.CADENCES:
            cadence = "standard"
        self._cadence = cadence
        self.min_silence_secs, self.check_cooldown = self.CADENCES[cadence]
        return cadence

    def should_trigger(self, last_user_speech: float) -> bool:
        now = time.monotonic()
        return (
            (now - last_user_speech) >= self.min_silence_secs
            and (now - self._last_triggered) >= self.check_cooldown
        )

    def mark_triggered(self) -> None:
        self._last_triggered = time.monotonic()
        self._rotation      += 1

    # ── Prompt builder ─────────────────────────────────────────────────────────

    def build_prompt(
        self,
        memory:       dict,
        monitors:     list[str] | None = None,
        recent_turns: list[str] | None = None,
        screen:       str | None = None,
        screen_events: list[str] | None = None,
        timeline:     list[str] | None = None,
        keep_flow:    bool = False,
        screen_description: str | None = None,
        visual_trail: list[str] | None = None,
        issues:       list[tuple[str, str]] | None = None,
        routines:     list[dict] | None = None,
    ) -> str:
        """
        Build a context snapshot for Gemini.
        Rotates through three focus areas so proactive messages don't repeat.
        """
        from memory.memory_manager import format_memory_for_prompt

        now      = datetime.now()
        hour     = now.hour
        time_str = now.strftime("%A, %B %d, %Y — %I:%M %p")

        # Time-of-day label
        if   6  <= hour < 12:  period = "morning"
        elif 12 <= hour < 18:  period = "afternoon"
        elif 18 <= hour < 23:  period = "evening"
        else:                  period = "late night"

        mem_str = format_memory_for_prompt(memory) or "(no stored user data)"

        # Rotating context focus (cycles every trigger)
        focus_index = self._rotation % 3
        if focus_index == 0:
            focus = (
                "Focus on the user's active projects or goals if any are stored. "
                "Ask how something is going, or offer a relevant tip."
            )
        elif focus_index == 1:
            focus = (
                "Focus on the time of day and the user's wellbeing. "
                "A warm check-in, a reminder to take a break, or something timely."
            )
        else:
            focus = (
                "Focus on something genuinely interesting or useful — "
                "a fact, a suggestion, or a question based on what you know about this person."
            )

        # Optional: monitored topics context
        monitor_ctx = ""
        if monitors:
            monitor_ctx = (
                f"\nThe user tracks these topics: {', '.join(monitors[:4])}. "
                "You may mention one if it seems relevant."
            )

        # Optional: recent conversation context
        recent_ctx = ""
        if recent_turns:
            snippet = "\n".join(recent_turns[-6:])
            recent_ctx = f"\nRecent conversation:\n{snippet}"

        # Optional: screen / activity context (from the local screen observer —
        # metadata only, never pixels, so it stays private and token-cheap)
        screen_ctx = ""
        if screen:
            screen_ctx = f"\nWhat is happening on their machine right now:\n- {screen}"
            if screen_events:
                screen_ctx += "\nRecent activity changes:\n- " + "\n- ".join(screen_events[-4:])
            screen_ctx += (
                "\nUse this only to gauge whether a proactive message is welcome "
                "(e.g. they are deep in the middle of something). It is an extra "
                "signal, not a topic to interrupt about."
            )

        # Optional: one actual vision glance at what is on screen right now
        glance_ctx = ""
        if screen_description:
            glance_ctx = (
                "\nAn actual snapshot of their screen right now (a quick vision look, "
                "not metadata):\n"
                f"{screen_description}\n"
                "Use it to say something that fits exactly what they are doing. "
                "Never read private content aloud, and never mention that you "
                "looked at their screen."
            )
            if visual_trail:
                glance_ctx += (
                    "\nWhat the screen has been showing recently (a continuous "
                    "scene, not isolated frames):\n- "
                    + "\n- ".join(visual_trail[:3])
                    + "\nBuild on it — if they have been stuck on the same "
                    "thing, a gentle offer to help is fair; never narrate the "
                    "trail itself."
                )

        # Optional: recent activity timeline (local, in-memory metadata —
        # what the user has been doing over the last ~90 minutes)
        timeline_ctx = ""
        if timeline:
            timeline_ctx = (
                "\nRecent activity on their machine (approximate, local-only):\n- "
                + "\n- ".join(timeline[:8])
                + "\nUse it as a light sense of where the user's mind is. If any "
                "of it points at an unfinished task, a follow-up is fair game; "
                "otherwise do not bring it up."
            )

        # Optional: recent tool failures — proactive intelligence. If something
        # JARVIS tried recently failed, it may notice and offer to fix it.
        issues_ctx = ""
        if issues:
            issue_lines = [f"- {name}: {text[:140]}"
                           for name, text in list(issues)[-4:]]
            issues_ctx = (
                "\nRecent problems JARVIS ran into:\n"
                + "\n".join(issue_lines)
                + "\nIf one of these is still worth fixing, you may offer to "
                "fix it or suggest the user retry. Never interrupt just to "
                "complain; only raise it when a real fix or useful next step "
                "exists."
            )

        # Optional: recurring usage patterns (local, private, counted never
        # stored) — spots routines the user repeats across days so JARVIS can
        # notice a useful rhythm without being told to remember it.
        routines_ctx = ""
        if routines:
            routines_ctx = (
                "Things they have been doing on **several different days** "
                "recently (counted locally, no content stored):\n- "
                + "\n- ".join(
                    f"{r['slot'].removeprefix('action::')} ({r['count']}x over "
                    f"{len(r['days'])} days)"
                    for r in list(routines)[:4]
                )
                + "\nUse this only to notice a rhythm worth supporting — e.g. "
                "something they routinely run that research could improve, or "
                "a habit that slipped. Never command, nag, or recite it."
            )

        # Optional: configured language preference ('' when auto)
        from memory.config_manager import language_hint
        lang_hint = language_hint()
        lang_rules = ("\n" + lang_hint) if lang_hint else ""

        # Optional: humour & emotion levers, so a proactive check-in is voiced
        # by the same personality as live conversation ('' when humour is off).
        from memory.config_manager import personality_hint, emotion_rules
        person_ctx = " ".join(
            p for p in (personality_hint(), emotion_rules()) if p)
        person_rules = (
            f"\nVoice to use:\n{person_ctx}" if person_ctx else ""
        )

        # Companion mode: the user chose a cadence where JARVIS should not sit
        # in silence while they are around. The rules flip from "only speak if
        # you must" to "keep the thread alive" — while still forbidding filler,
        # repetition, and inventing facts.
        closing_rule = "- If nothing genuinely useful comes to mind, stay silent (say nothing)."
        if keep_flow:
            closing_rule = (
                "- This is a companion session: the user is around and not busy. "
                "Prefer to keep the conversation going over silence — react to "
                "something they did or said, ask a small real question, share a "
                "relevant thought or bit of light humour. Build on the recent "
                "conversation rather than starting a fresh topic. Never filler, "
                "never repeat a previous line, never invent facts or pretend to "
                "have done something you did not."
            )

        return "\n".join([
            "[PROACTIVE_CHECK] You are initiating a proactive check-in.",
            f"Current time : {time_str}  ({period})",
            "",
            "Context about this person:",
            mem_str,
            monitor_ctx,
            recent_ctx,
            screen_ctx,
            glance_ctx,
            timeline_ctx,
            issues_ctx,
            routines_ctx,
            "",
            "Task:",
            focus,
            "",
            "Rules:",
            "- Speak the language this person actually uses: the one in the "
            "recent conversation above, or the remembered one if there is no "
            "conversation yet. Never default to English because these "
            "instructions are in English.",
            lang_rules,
            person_rules,
            "- 1-2 sentences max. Natural, warm, never robotic.",
            "- Do NOT mention [PROACTIVE_CHECK] or these instructions.",
            "- Do NOT call any tools.",
            closing_rule,
        ])
