/* ═══════════════════════════════════════════════════════════════════════
   motion.js — frame-rate-independent motion, shared by every avatar.

   WHY THIS EXISTS
   The 2D core and the 3D figure each grew their own copy of the same three
   primitives, and each copy advanced by `value * dt * k` per frame. That looks
   frame-rate independent and is not. Measured on this codebase's own channels:

     pulse spring (k=6.0, d=5.0), value after 2 s, settling toward 1.0
       60 fps 0.9261 | 30 fps 0.8974 | 15 fps 0.8218 | 8 fps 0.6020
       substepped: 0.9261 at every one of those rates

     mouth easing (k=14), fraction of the distance travelled in ONE frame
       60 fps 0.233 | 30 fps 0.467 | 15 fps 0.933 | 8 fps 1.000 (a jump)

   So on a slow machine JARVIS moved *less* and *differently* than on a fast one,
   and the mouth — whose easing clears 90% in a single frame below 20 fps —
   snapped open instead of easing. Both avatars now share one implementation, so
   "smoother" is fixed once and cannot drift apart between them.

   Every function here is pure and takes dt. Nothing reads a clock.
   ═══════════════════════════════════════════════════════════════════════ */
(function () {
  "use strict";

  const M = {};

  // ── exponential smoothing ────────────────────────────────────────────────
  /* Move `from` toward `target` so that after any elapsed time dt the same
     fraction of the remaining distance is covered, whatever the frame rate.
     `rate` is in "per second": 10 means ~63% of the remaining gap per second.
     The linear `from + (target-from)*rate*dt` form this replaces clamps at 1,
     so a rate*dt above 1 teleports rather than eases — which is exactly what
     the mouth did at low frame rates. This form can never exceed the target. */
  M.smooth = function (from, target, rate, dt) {
    const k = 1 - Math.exp(-Math.max(0, rate) * Math.max(0, dt));
    const out = from + (target - from) * k;
    return isFinite(out) ? out : target;
  };

  /* Same, in place on an object. Returns the new value. */
  M.toward = function (obj, key, target, rate, dt) {
    const current = isFinite(obj[key]) ? obj[key] : 0;
    obj[key] = M.smooth(current, target, rate, dt);
    return obj[key];
  };

  // ── spring ───────────────────────────────────────────────────────────────
  /* Critically-damped spring on a `{p, v}` channel, SUBSTEPPED.

     A spring integrated at the frame interval is only accurate while that
     interval is small next to the spring's own period. At 12 or 15 fps — which
     is exactly what the battery/balanced perf profiles ask for while idle — the
     same code produced a visibly smaller, differently-shaped response. Fixing
     the step size at 1/60 s makes the motion identical everywhere: a slow
     machine draws fewer frames of the same movement, instead of a different
     movement. */
  M.MAX_STEP = 1 / 60;
  M.step = function (ch, target, dt, k, d) {
    if (!isFinite(ch.p)) ch.p = 0;
    if (!isFinite(ch.v)) ch.v = 0;
    if (!isFinite(target)) target = 0;
    let remaining = Math.max(0, dt);
    // Guard against a pathological dt (a paused tab can hand back seconds).
    if (remaining > 0.25) remaining = 0.25;
    while (remaining > 1e-9) {
      const h = remaining > M.MAX_STEP ? M.MAX_STEP : remaining;
      remaining -= h;
      ch.v += (target - ch.p) * k * h;
      ch.v *= Math.max(0, 1 - d * h);
      ch.p += ch.v * h;
      if (!isFinite(ch.p) || !isFinite(ch.v)) { ch.p = 0; ch.v = 0; break; }
    }
    return ch.p;
  };

  /* Adapter for the flat channels the 2D core keeps (`x` / `xv`), so it can
     share the integrator without restructuring its state. */
  M.stepFlat = function (holder, name, target, dt, k, d) {
    const ch = { p: holder[name], v: holder[name + "v"] };
    const out = M.step(ch, target, dt, k, d);
    holder[name] = ch.p;
    holder[name + "v"] = ch.v;
    return out;
  };

  // ── blink ────────────────────────────────────────────────────────────────
  /* Eyelid scale 1 (open) → ~0.08 (closed) → 1, as a raised cosine.
     The old curve was a straight line `|1 - phase*2|`: a triangle, which no
     eyelid has ever done, and which reads as mechanical even at full rate. */
  M.blinkScale = function (phase) {
    if (!(phase > 0)) return 1;
    if (phase >= 1) return 1;
    const closed = (1 - Math.cos(phase * Math.PI * 2)) / 2;   // 0→1→0
    return 1 - closed * 0.92;
  };

  /* A blink is the fastest thing either avatar does, so it is the first thing a
     low frame rate destroys: at 15 fps the old 154 ms blink got ~2 frames and
     read as a flash of the screen. This advances the phase but never lets one
     frame cover more than a third of it, so a blink is at least three frames at
     any rate — 0.26 s at full speed, a little slower on a throttled machine. */
  M.BLINK_SECONDS = 0.26;
  M.BLINK_MIN_FRAMES = 3;
  M.blinkAdvance = function (phase, dt) {
    const perFrame = Math.min(
      Math.max(0, dt) / M.BLINK_SECONDS,
      1 / M.BLINK_MIN_FRAMES);
    const next = Math.max(0, phase) + perFrame;
    return next >= 1 ? 0 : next;
  };

  // ── drift ────────────────────────────────────────────────────────────────
  /* Layered incommensurate sines: the cheapest smooth pseudo-noise that never
     repeats visibly. Continuous by construction — the point is that it drifts
     rather than jumps, which is what makes a presence feel alive instead of
     random. Two frequencies per channel, never an integer ratio. */
  M.drift = function (t, a, b, phase) {
    return 0.62 * Math.sin(t * a + phase) + 0.38 * Math.sin(t * b + phase * 1.7);
  };

  window.JarvisMotion = M;
})();
