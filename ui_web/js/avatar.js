/* ═══════════════════════════════════════════════════════════════════════
   JARVIS avatar — a lightweight 2D "arc core" face.
   Canvas 2D, capped at 30 fps, pauses when the page is hidden.
   States: idle · listening · thinking · speaking · sleeping · muted
           happy · confused · error (transient overlays over the base state)
   ═══════════════════════════════════════════════════════════════════════ */
(function () {
  "use strict";

  const canvas = document.getElementById("face");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");

  const AV = {
    state: "IDLE",          // IDLE | LISTENING | THINKING | SPEAKING | SLEEPING | MUTED
    level: 0,               // live mic level 0..1 (listening)
    viseme: { level: 0, openness: 0, width: 0 },   // current mouth frame (speaking)
    muted: false,
    expression: null,       // 'happy' | 'confused' | 'error' | null
    exprUntil: 0,
    style: "face",          // 'face' | 'core'
  };

  window.JarvisAvatar = AV;

  // ── animation state ────────────────────────────────────────────────────
  let t = 0;                 // seconds since start
  let last = performance.now();
  let acc = 0;
  let blinkT = 0, nextBlink = 2 + Math.random() * 3;
  let blinkPhase = 0;        // 0..1..0 while blinking
  let glanceX = 0, glanceY = 0, tgtGX = 0, tgtGY = 0, nextGlance = 4;
  let dispLevel = 0, dispOpen = 0, dispWide = 0;
  let running = true, pageVisible = true;

  const TAU = Math.PI * 2;
  const ease = (a, b, k) => a + (b - a) * Math.min(1, Math.max(0, k));

  // State → colour
  function palette() {
    switch (AV.state) {
      case "LISTENING": return { main: "#38bdf8", glow: "rgba(56,189,248," };
      case "THINKING":  return { main: "#8b6df5", glow: "rgba(139,109,245," };
      case "SPEAKING":  return { main: "#7dd7fc", glow: "rgba(125,215,252," };
      case "SLEEPING":  return { main: "#2c3a55", glow: "rgba(84,110,160," };
      case "MUTED":     return { main: "#8b5a68", glow: "rgba(255,92,122," };
      default:          return { main: "#38bdf8", glow: "rgba(56,189,248," };
    }
  }
  function exprPalette(p) {
    if (AV.expression === "happy")    return { main: "#6fe6c2", glow: "rgba(111,230,194," };
    if (AV.expression === "confused") return { main: "#f5b45c", glow: "rgba(245,180,92," };
    if (AV.expression === "error")    return { main: "#ff5c7a", glow: "rgba(255,92,122," };
    return p;
  }

  // ── viseme scheduling (mirrors backend push_visemes semantics) ────────
  let vFrames = null, vHop = 0, vStart = 0;
  AV.pushVisemes = function (frames, hop, at) {
    vFrames = frames; vHop = hop || 0.02; vStart = at;
  };
  function tickVisemes(nowSec) {
    if (!vFrames || !vFrames.length || AV.state !== "SPEAKING") {
      AV.viseme.level = ease(AV.viseme.level, 0, 0.25);
      AV.viseme.openness = ease(AV.viseme.openness, 0, 0.25);
      AV.viseme.width = ease(AV.viseme.width, 0, 0.25);
      return;
    }
    let idx = Math.floor((nowSec - vStart) / vHop);
    if (idx < 0) idx = 0;
    if (idx >= vFrames.length) idx = vFrames.length - 1;
    const f = vFrames[idx];
    if (Array.isArray(f)) {
      AV.viseme.level    = f[0] || 0;
      AV.viseme.openness = f[1] || 0;
      AV.viseme.width    = f[2] || 0;
    }
  }

  // ── drawing helpers ───────────────────────────────────────────────────
  function ring(cx, cy, r, a0, a1, width, color, blur) {
    ctx.beginPath();
    ctx.arc(cx, cy, r, a0, a1);
    ctx.lineWidth = width;
    ctx.strokeStyle = color;
    ctx.shadowColor = color;
    ctx.shadowBlur = blur || 0;
    ctx.stroke();
    ctx.shadowBlur = 0;
  }

  function drawWaveRing(cx, cy, rBase, amp, color, phase, n) {
    // circle of radial bars — the "waveform halo"
    n = n || 64;
    ctx.save();
    for (let i = 0; i < n; i++) {
      const a = (i / n) * TAU - Math.PI / 2;
      const wobble =
        0.30 + 0.70 * Math.abs(
          Math.sin(i * 1.7 + phase * 2.1) * 0.5 +
          Math.sin(i * 3.3 - phase * 3.7) * 0.35 +
          Math.sin(i * 0.7 + phase * 1.3) * 0.3
        );
      const len = 3 + amp * 30 * wobble;
      const x0 = cx + Math.cos(a) * rBase;
      const y0 = cy + Math.sin(a) * rBase;
      const x1 = cx + Math.cos(a) * (rBase + len);
      const y1 = cy + Math.sin(a) * (rBase + len);
      ctx.beginPath();
      ctx.moveTo(x0, y0); ctx.lineTo(x1, y1);
      ctx.lineWidth = 2;
      ctx.lineCap = "round";
      ctx.strokeStyle = color;
      ctx.globalAlpha = 0.25 + 0.55 * (len / 33);
      ctx.stroke();
    }
    ctx.restore();
    ctx.globalAlpha = 1;
  }

  // ── main render ───────────────────────────────────────────────────────
  function render() {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const cssW = canvas.clientWidth, cssH = canvas.clientHeight;
    if (canvas.width !== Math.round(cssW * dpr) || canvas.height !== Math.round(cssH * dpr)) {
      canvas.width = Math.round(cssW * dpr);
      canvas.height = Math.round(cssH * dpr);
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cssW, cssH);

    const W = cssW, H = cssH;
    const cx = W / 2 + glanceX * W * 0.015;
    const cy = H / 2 + glanceY * H * 0.015;
    const R = Math.min(W, H) * 0.36;

    // ── MINIMAL mode: one calm core + one soft ring, nothing else.
    //    The cheapest possible living avatar (~5 draw calls, 30 fps cap).
    if (AV.style === "minimal") {
      const p = palette();
      const breathe = 0.02 * Math.sin(t * 1.3);
      const amp = AV.state === "LISTENING" ? dispLevel :
                  AV.state === "SPEAKING"  ? Math.max(dispLevel, AV.viseme.level) : 0;
      const r = R * (0.72 + breathe + amp * 0.05);
      const aura = ctx.createRadialGradient(cx, cy, r * 0.15, cx, cy, r * 1.5);
      aura.addColorStop(0, p.glow + "0.07)");
      aura.addColorStop(1, p.glow + "0)");
      ctx.fillStyle = aura;
      ctx.beginPath(); ctx.arc(cx, cy, r * 1.5, 0, TAU); ctx.fill();
      const cg = ctx.createRadialGradient(cx, cy, r * 0.1, cx, cy, r);
      cg.addColorStop(0, p.glow + "0.8)");
      cg.addColorStop(1, p.glow + "0.05)");
      ctx.fillStyle = cg;
      ctx.beginPath(); ctx.arc(cx, cy, r, 0, TAU); ctx.fill();
      ring(cx, cy, r * 1.18, 0, TAU, 1.6, p.glow + "0.35)", 0);
      return;
    }

    let p = exprPalette(palette());

    // breathing / activity scale
    const breathe = 0.018 * Math.sin(t * 1.4);
    const amp =
      AV.state === "LISTENING" ? dispLevel :
      AV.state === "SPEAKING"  ? Math.max(dispLevel, AV.viseme.level) : 0;
    const scale = 1 + breathe + amp * 0.03;
    const r = R * scale;

    // special expressions tint
    if (AV.expression === "error") {
      const pulse = 0.5 + 0.5 * Math.sin(t * 6);
      p = { main: p.main, glow: p.glow };
      ctx.globalAlpha = 0.5 + 0.2 * pulse;
    }

    // outer soft aura — very subtle, never neon
    const aura = ctx.createRadialGradient(cx, cy, r * 0.2, cx, cy, r * 1.45);
    aura.addColorStop(0, p.glow + "0.055)");
    aura.addColorStop(1, p.glow + "0)");
    ctx.fillStyle = aura;
    ctx.beginPath(); ctx.arc(cx, cy, r * 1.45, 0, TAU); ctx.fill();

    // segmented outer ring (slow rotation; fast while thinking)
    const segN = 48;
    const rot = AV.state === "THINKING" ? t * 1.6 : t * 0.12;
    for (let i = 0; i < segN; i++) {
      const a0 = (i / segN) * TAU + rot;
      const a1 = a0 + (TAU / segN) * 0.55;
      const alpha = 0.10 + 0.4 * amp * ((i % 4) / 3);
      ring(cx, cy, r * 1.06, a0, a1, 2, p.glow + alpha + ")", 0);
    }

    // orbit arcs
    const orbAmp = 0.5 + amp * 0.9;
    ring(cx, cy, r * 0.88, t * 0.9, t * 0.9 + 1.15 * orbAmp, 2.6, p.glow + "0.6)", 5);
    ring(cx, cy, r * 0.88, t * 0.9 + Math.PI, t * 0.9 + Math.PI + 1.15 * orbAmp, 2.6, p.glow + "0.6)", 5);
    ring(cx, cy, r * 0.72, -t * 1.3, -t * 1.3 + 0.8 * orbAmp, 1.8, p.glow + "0.35)", 3);
    ring(cx, cy, r * 0.72, -t * 1.3 + Math.PI * 1.2, -t * 1.3 + Math.PI * 1.2 + 0.8 * orbAmp, 1.8, p.glow + "0.35)", 3);

    // core — a calm luminous disc, no harsh hotspot
    const coreR = r * 0.52 * (AV.state === "SLEEPING" ? 0.8 : 1);
    const cg = ctx.createRadialGradient(cx, cy, coreR * 0.08, cx, cy, coreR);
    const bright = 0.62 + amp * 0.35 + (AV.expression === "happy" ? 0.12 : 0);
    cg.addColorStop(0, p.glow + (0.85 * bright) + ")");
    cg.addColorStop(0.55, p.glow + (0.30 * bright) + ")");
    cg.addColorStop(1, p.glow + "0.04)");
    ctx.fillStyle = cg;
    ctx.beginPath(); ctx.arc(cx, cy, coreR, 0, TAU); ctx.fill();

    // inner highlight ring instead of a hot white centre
    ring(cx, cy, coreR * 0.66, 0, TAU, 1.2, p.glow + "0.5)", 4);

    // core rim
    ring(cx, cy, coreR, 0, TAU, 1.6, p.glow + "0.7)", 6);

    // face features (only in 'face' style)
    if (AV.style !== "core") {
      const eyeY = cy - coreR * 0.28;
      const eyeDX = coreR * 0.42;
      const eyeR = coreR * 0.11;

      // blink
      let eyeSquish = 1;
      if (AV.state === "SLEEPING") eyeSquish = 0.08;
      else if (blinkPhase > 0) eyeSquish = Math.max(0.08, Math.abs(1 - blinkPhase * 2));

      // eyes: small glowing dots
      for (const s of [-1, 1]) {
        const ex = cx + s * eyeDX + glanceX * coreR * 0.10;
        const ey = eyeY + glanceY * coreR * 0.08;
        ctx.save();
        ctx.translate(ex, ey);
        ctx.scale(1, eyeSquish);
        const eg = ctx.createRadialGradient(0, 0, 0, 0, 0, eyeR);
        eg.addColorStop(0, "#ffffff");
        eg.addColorStop(0.5, p.glow + "0.95)");
        eg.addColorStop(1, p.glow + "0.1)");
        ctx.fillStyle = eg;
        ctx.beginPath(); ctx.arc(0, 0, eyeR, 0, TAU); ctx.fill();
        ctx.restore();
      }

      // brows — ride the expression
      const browY = eyeY - coreR * (AV.expression === "confused" ? 0.34 : 0.30);
      ctx.lineWidth = 2.4; ctx.lineCap = "round";
      ctx.strokeStyle = p.glow + "0.7)";
      for (const s of [-1, 1]) {
        ctx.beginPath();
        const tilt = AV.expression === "confused" ? s * 0.16 : 0;
        ctx.moveTo(cx + s * eyeDX - coreR * 0.16, browY + tilt * coreR);
        ctx.lineTo(cx + s * eyeDX + coreR * 0.16, browY - tilt * coreR);
        ctx.stroke();
      }

      // mouth — a rounded arc, invisible at rest (no blob when closed)
      const open = AV.state === "SPEAKING" ? dispOpen : 0;
      const wide = AV.state === "SPEAKING" ? dispWide : 0;
      const mY = cy + coreR * 0.42;
      const mW = coreR * (0.34 + wide * 0.24);
      const mH = coreR * (0.04 + open * 0.5);
      ctx.beginPath();
      if (mH < 2.2) {
        // closed mouth: a gentle smile-line (middle sits just below the ends)
        ctx.moveTo(cx - mW, mY);
        ctx.quadraticCurveTo(cx, mY + coreR * 0.08, cx + mW, mY);
        ctx.lineWidth = 1.8;
        ctx.lineCap = "round";
        ctx.strokeStyle = p.glow + "0.55)";
        ctx.stroke();
      } else {
        ctx.ellipse(cx, mY, mW, mH, 0, 0, TAU);
        ctx.fillStyle = p.glow + (0.22 + open * 0.45) + ")";
        ctx.fill();
        ctx.lineWidth = 1.6;
        ctx.strokeStyle = p.glow + "0.7)";
        ctx.stroke();
      }
    } else {
      // 'core' style — inner reactor filaments instead of a face
      ctx.save();
      ctx.translate(cx, cy);
      ctx.rotate(-t * 0.6);
      for (let i = 0; i < 8; i++) {
        ctx.rotate(TAU / 8);
        ctx.beginPath();
        ctx.moveTo(coreR * 0.18, 0);
        ctx.lineTo(coreR * 0.95, 0);
        ctx.lineWidth = 2;
        ctx.strokeStyle = p.glow + (0.22 + amp * 0.5) + ")";
        ctx.stroke();
      }
      ctx.restore();
    }

    // muted indicator
    if (AV.muted) {
      ring(cx, cy, r * 1.14, Math.PI * 0.62, Math.PI * 0.88, 3, "rgba(255,92,122,0.85)", 6);
    }

    // waveform halo while listening/speaking
    if (AV.state === "LISTENING" || AV.state === "SPEAKING") {
      drawWaveRing(cx, cy, r * 1.13, dispLevel * (AV.state === "SPEAKING" ? Math.max(AV.viseme.level, 0.15) : 1),
                   p.glow + "0.55)", t, 56);
    }

    ctx.globalAlpha = 1;
  }

  // ── loop (30 fps cap, pauses when hidden) ─────────────────────────────
  function frame(now) {
    requestAnimationFrame(frame);
    if (!running || !pageVisible) return;
    const dt = Math.min(0.1, (now - last) / 1000);
    last = now; t += dt;

    // expressions expire
    if (AV.expression && t > AV.exprUntil) AV.expression = null;

    // blink timer
    blinkT += dt;
    if (blinkT > nextBlink) { blinkT = 0; nextBlink = 2.2 + Math.random() * 3.4; blinkPhase = 0.001; }
    if (blinkPhase > 0) {
      blinkPhase += dt * 6.5;
      if (blinkPhase >= 1) blinkPhase = 0;
    }

    // idle glances
    if (t > nextGlance) {
      nextGlance = t + 3 + Math.random() * 5;
      tgtGX = (Math.random() - 0.5) * 1.6;
      tgtGY = (Math.random() - 0.5) * 1.0;
    }
    glanceX = ease(glanceX, tgtGX, dt * 2.4);
    glanceY = ease(glanceY, tgtGY, dt * 2.4);

    // smoothed levels
    const target = AV.state === "LISTENING" ? AV.level : AV.viseme.level;
    dispLevel = ease(dispLevel, target, dt * 10);
    dispOpen  = ease(dispOpen, AV.viseme.openness, dt * 14);
    dispWide  = ease(dispWide, AV.viseme.width, dt * 14);

    acc += dt;
    if (acc >= 1 / 30) { acc = 0; tickVisemes(t); render(); }
  }
  requestAnimationFrame(frame);

  // pause rendering when the page is hidden or window minimised
  document.addEventListener("visibilitychange", () => {
    pageVisible = !document.hidden;
    last = performance.now();
  });
  window.addEventListener("blur",  () => { /* keep last frame; still cheap */ });

  AV.setPaused = function (p) { running = !p; last = performance.now(); };
})();
