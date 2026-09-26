/* ═══════════════════════════════════════════════════════════════════════
   avatar3d.js — optional "3D Anime AI Girl" avatar mode.

   An original, fully procedural anime-styled character (no downloaded
   model, no external textures — just tiny canvas sprites) inspired by the
   look JARVIS asked for: long black hair, dark turtleneck, large
   expressive eyes, cyan rim lighting that matches the UI.

   Lightweight on purpose:
   • ~10–15k triangles of spheres/capsules/planes — trivial for an iGPU
   • toon (cel) shading, no shadows, no post-processing, antialias off
   • an FPS governor: idle runs at 12–30 fps, speaking/expression moments
     run faster; the loop renders nothing while the page is hidden
   • the whole object is disposable: destroy() frees every geometry,
     material, texture and the WebGL context, and stops the loop

   Loaded ONLY when the user selects this avatar mode.
   ═══════════════════════════════════════════════════════════════════════ */
(function () {
  "use strict";

  let A3 = null;   // singleton instance

  const FPS = {
    battery:  { idle: 12, active: 24, pr: 0.75 },
    balanced: { idle: 20, active: 45, pr: 1.0 },
    quality:  { idle: 30, active: 60, pr: 2.0 },
  };

  // ── living-motion helpers (the same feel as the 2D core) ────────────
  // Layered incommensurate sines: smooth pseudo-noise that never repeats
  // visibly and never jumps — the cheapest organic drift there is.
  function drift3(t, a, b, phase) {
    return 0.62 * Math.sin(t * a + phase) + 0.38 * Math.sin(t * b + phase * 1.7);
  }
  // Critically-damped spring step on a { p, v } channel: glides toward the
  // target, never overshoots into oscillation, never snaps.
  function spring3(ch, target, dt, k, d) {
    ch.v += (target - ch.p) * k * dt;
    ch.v *= Math.max(0, 1 - d * dt);
    ch.p += ch.v * dt;
    return ch.p;
  }

  function Jarvis3D() {
    this.mount = null;
    this.renderer = null;
    this.scene = null;
    this.camera = null;
    this.root = null;
    this.head = null;
    this.body = null;
    this.hairBack = null;
    this.halo = null;
    this.rimLight = null;
    this.eyeL = null;
    this.eyeR = null;
    this.browL = null;
    this.browR = null;
    this.mouth = null;
    this.blush = null;
    this.dead = true;
    this.paused = false;

    this.state = "IDLE";
    this.expression = null;
    this.exprUntil = 0;
    this.level = 0;
    this.viseme = { level: 0, openness: 0, width: 0 };

    this.t = 0;
    this.lastRaf = 0;
    this.lastRender = 0;
    this.acc = 0;
    this.blinkAt = 2.5;
    this.blinking = 0;
    this._dblBlink = false;
    this.particles = null;          // hologram dust motes
    this.glow = null;               // accent ground glow
    this.gaze = { x: 0, y: 0, tx: 0, ty: 0, nextAt: 2.0 };

    // living presence: the same layered-sine + spring drift the 2D core
    // uses — she breathes, sways, leans and pulses with her voice. Every
    // channel starts at rest and is integrated in _tick(), so even the
    // first frame after init() can never teleport.
    this._motion = {
      x:     { p: 0, v: 0 },   // gentle left/right sway
      y:     { p: 0, v: 0 },   // slight vertical bob
      z:     { p: 0, v: 0 },   // subtle toward/away lean
      rot:   { p: 0, v: 0 },   // small body tilt (radians)
      pulse: { p: 0, v: 0 },   // voice-driven scale swell
    };
    this._energy = 0;
    this._energySlow = 0;

    this.cfg = {
      size: 1.0, x: 0, y: 0,
      anim: 1.0, expr: 1.0,
      light: "#38bdf8",
      perf: "balanced",
      visible: true,
    };

    this._texCache = {};
  }

  // ── toon helpers ────────────────────────────────────────────────────────
  Jarvis3D.prototype._gradientMap = function () {
    if (this._gradMap) return this._gradMap;
    const data = new Uint8Array([80, 160, 235, 255]);   // 4-step cel ramp
    const tex = new THREE.DataTexture(data, data.length, 1, THREE.LuminanceFormat);
    tex.minFilter = THREE.NearestFilter;
    tex.magFilter = THREE.NearestFilter;
    tex.generateMipmaps = false;
    this._gradMap = tex;
    return tex;
  };

  Jarvis3D.prototype._toon = function (colorHex, opts) {
    const m = new THREE.MeshToonMaterial({
      color: colorHex,
      gradientMap: this._gradientMap(),
    });
    if (opts) Object.assign(m, opts);
    return m;
  };

  // ── tiny canvas sprite textures (eyes, mouth, blush) ───────────────────
  Jarvis3D.prototype._tex = function (key, draw) {
    if (this._texCache[key]) return this._texCache[key];
    const c = document.createElement("canvas");
    c.width = 256; c.height = 256;
    const x = c.getContext("2d");
    draw(x, c.width, c.height);
    const t = new THREE.CanvasTexture(c);
    t.minFilter = THREE.LinearFilter;
    this._texCache[key] = t;
    return t;
  };

  function roundedEyeWhite(x, cx, cy, w, h) {
    x.beginPath();
    x.ellipse(cx, cy, w, h, 0, 0, Math.PI * 2);
    x.fillStyle = "#ffffff";
    x.fill();
  }

  Jarvis3D.prototype._drawEye = function (x, W, H, style, flip) {
    x.clearRect(0, 0, W, H);
    const cx = W / 2, cy = H / 2;
    const lash = "#141419";

    if (style === "closed" || style === "wink") {
      x.strokeStyle = lash; x.lineWidth = 14; x.lineCap = "round";
      x.beginPath();
      x.moveTo(30, cy + 6);
      x.quadraticCurveTo(cx, cy + (style === "wink" ? -34 : 34), W - 30, cy + 6);
      x.stroke();
      return;
    }
    if (style === "happy") {                       // ^ ^ arc
      x.strokeStyle = lash; x.lineWidth = 15; x.lineCap = "round";
      x.beginPath();
      x.moveTo(34, cy + 22);
      x.quadraticCurveTo(cx, cy - 36, W - 34, cy + 22);
      x.stroke();
      return;
    }

    // open eye
    const wide = style === "wide";
    const sad = style === "sad";
    const ew = wide ? 108 : 100, eh = wide ? 128 : 116;
    roundedEyeWhite(x, cx, cy, ew, eh);

    // iris — soft grey-violet, larger than realistic (anime)
    const ir = wide ? 62 : 56;
    const icy = cy + (sad ? 10 : 0);
    const g = x.createRadialGradient(cx, icy - ir * 0.2, ir * 0.1, cx, icy, ir);
    g.addColorStop(0, "#a9a4c4");
    g.addColorStop(0.55, "#5c5878");
    g.addColorStop(1, "#26242f");
    x.beginPath(); x.ellipse(cx, icy, ir, ir * 1.15, 0, 0, Math.PI * 2);
    x.fillStyle = g; x.fill();

    // pupil + highlights
    x.beginPath(); x.ellipse(cx, icy + 4, ir * 0.34, ir * 0.4, 0, 0, Math.PI * 2);
    x.fillStyle = "#16151c"; x.fill();
    x.beginPath(); x.ellipse(cx - ir * 0.34, icy - ir * 0.42, ir * 0.26, ir * 0.3, 0, 0, Math.PI * 2);
    x.fillStyle = "rgba(255,255,255,0.95)"; x.fill();
    x.beginPath(); x.ellipse(cx + ir * 0.3, icy + ir * 0.42, ir * 0.12, ir * 0.13, 0, 0, Math.PI * 2);
    x.fillStyle = "rgba(255,255,255,0.55)"; x.fill();

    // upper lash line — thick, dark, slightly winged
    x.strokeStyle = lash; x.lineWidth = 20; x.lineCap = "round";
    x.beginPath();
    x.moveTo(cx - ew, cy - eh * (sad ? 0.5 : 0.72));
    x.quadraticCurveTo(cx, cy - eh * 1.16, cx + ew, cy - eh * (sad ? 0.66 : 0.8));
    x.stroke();

    if (sad) {   // droop: skin-tone lid over the inner top
      x.fillStyle = "#ffe3d0";
      x.beginPath();
      x.ellipse(cx + (flip ? -ew * 0.55 : ew * 0.55), cy - eh * 0.62, ew * 0.75, eh * 0.4, 0, 0, Math.PI * 2);
      x.fill();
      x.strokeStyle = lash; x.lineWidth = 10; x.lineCap = "round";
      x.beginPath();
      x.moveTo(cx - ew * 0.4, cy - eh * 0.62);
      x.quadraticCurveTo(cx + (flip ? ew * 0.5 : -ew * 0.5), cy - eh * 0.3, cx + (flip ? ew * 0.9 : -ew * 0.9), cy - eh * 0.55);
      x.stroke();
    }
  };

  Jarvis3D.prototype._drawMouth = function (x, W, H, style) {
    x.clearRect(0, 0, W, H);
    const cx = W / 2, cy = H / 2;
    x.strokeStyle = "#8a4a44"; x.lineWidth = 12; x.lineCap = "round";
    if (style === "smile") {
      x.beginPath();
      x.moveTo(56, cy - 12);
      x.quadraticCurveTo(cx, cy + 34, W - 56, cy - 12);
      x.stroke();
    } else if (style === "neutral") {
      x.beginPath();
      x.moveTo(66, cy);
      x.quadraticCurveTo(cx, cy + 10, W - 66, cy);
      x.stroke();
    } else if (style === "sad") {
      x.beginPath();
      x.moveTo(60, cy + 16);
      x.quadraticCurveTo(cx, cy - 22, W - 60, cy + 16);
      x.stroke();
    } else if (style === "o") {
      x.beginPath(); x.ellipse(cx, cy, 30, 38, 0, 0, Math.PI * 2);
      x.fillStyle = "#5f3230"; x.fill();
    } else if (style === "cat") {
      x.beginPath();
      x.moveTo(52, cy - 6);
      x.quadraticCurveTo(cx - 26, cy + 30, cx, cy - 2);
      x.quadraticCurveTo(cx + 26, cy + 30, W - 52, cy - 6);
      x.stroke();
    } else if (style === "open") {
      const g = x.createRadialGradient(cx, cy + 10, 4, cx, cy, 74);
      g.addColorStop(0, "#7a3d3a");
      g.addColorStop(1, "#4a2422");
      x.beginPath(); x.ellipse(cx, cy, 52, 62, 0, 0, Math.PI * 2);
      x.fillStyle = g; x.fill();
      // teeth hint
      x.fillStyle = "rgba(255,255,255,0.85)";
      x.beginPath();
      x.ellipse(cx, cy - 40, 42, 14, 0, 0, Math.PI * 2);
      x.fill();
    }
  };

  Jarvis3D.prototype._eyeMat = function (style) {
    const t = this._tex("eye_" + style, (x, W, H) => this._drawEye(x, W, H, style, false));
    return new THREE.MeshBasicMaterial({ map: t, transparent: true, depthWrite: false });
  };
  Jarvis3D.prototype._mouthMat = function (style) {
    const t = this._tex("mouth_" + style, (x, W, H) => this._drawMouth(x, W, H, style));
    return new THREE.MeshBasicMaterial({ map: t, transparent: true, depthWrite: false });
  };

  // ── character build ─────────────────────────────────────────────────────
  Jarvis3D.prototype._build = function () {
    const scene = new THREE.Scene();

    // camera — portrait framing (head + shoulders), like the reference
    this.camera = new THREE.PerspectiveCamera(30, 1, 0.1, 50);
    this.camera.position.set(0, 2.05, 6.4);
    this.camera.lookAt(0, 2.02, 0);

    // lights — soft key, cyan rim (the JARVIS identity), dim fill
    scene.add(new THREE.HemisphereLight(0xcfe4ff, 0x14141e, 0.95));
    const key = new THREE.DirectionalLight(0xffffff, 0.75);
    key.position.set(2.2, 3.4, 4.2);
    scene.add(key);
    this.rimLight = new THREE.DirectionalLight(new THREE.Color(this.cfg.light), 1.15);
    this.rimLight.position.set(-2.4, 2.2, -3.4);
    scene.add(this.rimLight);

    const SKIN = 0xffe3d0, HAIR = 0x0c0c11, CLOTH = 0x17171d;

    // ── root / body / head groups
    this.root = new THREE.Group();
    this.body = new THREE.Group();
    this.head = new THREE.Group();
    scene.add(this.root);
    this.root.add(this.body);
    this.body.add(this.head);

    // torso — soft shoulders in a dark turtleneck
    const torso = new THREE.Mesh(new THREE.SphereGeometry(1, 28, 20), this._toon(CLOTH));
    torso.scale.set(0.78, 1.15, 0.6);
    torso.position.set(0, 0.62, 0);
    this.body.add(torso);
    const collar = new THREE.Mesh(new THREE.CylinderGeometry(0.34, 0.4, 0.24, 20), this._toon(0x1f1f27));
    collar.position.set(0, 1.42, 0);
    this.body.add(collar);
    const neck = new THREE.Mesh(new THREE.CylinderGeometry(0.22, 0.26, 0.36, 16), this._toon(SKIN));
    neck.position.set(0, 1.62, 0);
    this.body.add(neck);

    // head — slightly egg-shaped
    const headMesh = new THREE.Mesh(new THREE.SphereGeometry(1, 32, 24), this._toon(SKIN));
    headMesh.scale.set(0.92, 1.0, 0.9);
    this.head.add(headMesh);
    this.head.position.set(0, 2.62, 0);

    // ── face features (planes hugging the +Z of the head)
    const eyeGeo = new THREE.PlaneGeometry(0.5, 0.62);
    this.eyeL = new THREE.Mesh(eyeGeo, this._eyeMat("normal"));
    this.eyeL.position.set(-0.34, 0.05, 0.815);
    this.eyeL.rotation.y = 0.28;
    this.eyeR = new THREE.Mesh(eyeGeo, this._eyeMat("normal"));
    this.eyeR.position.set(0.34, 0.05, 0.815);
    this.eyeR.rotation.y = -0.28;
    this.head.add(this.eyeL, this.eyeR);

    const browGeo = new THREE.PlaneGeometry(0.3, 0.055);
    const browMat = new THREE.MeshBasicMaterial({ color: 0x141419, transparent: true });
    this.browL = new THREE.Mesh(browGeo, browMat);
    this.browL.position.set(-0.33, 0.4, 0.86); this.browL.rotation.y = 0.28;
    this.browR = new THREE.Mesh(browGeo, browMat);
    this.browR.position.set(0.33, 0.4, 0.86); this.browR.rotation.y = -0.28;
    this.head.add(this.browL, this.browR);

    this.mouth = new THREE.Mesh(new THREE.PlaneGeometry(0.3, 0.26), this._mouthMat("smile"));
    this.mouth.position.set(0, -0.4, 0.87);
    this.head.add(this.mouth);

    const nose = new THREE.Mesh(new THREE.ConeGeometry(0.035, 0.12, 10), this._toon(0xf7cbb2));
    nose.rotation.x = Math.PI / 2;
    nose.position.set(0, -0.14, 0.92);
    this.head.add(nose);

    // blush — soft dots, opacity driven by expression
    const blushTex = this._tex("blush", (x, W, H) => {
      const g = x.createRadialGradient(W / 2, H / 2, 4, W / 2, H / 2, W / 2);
      g.addColorStop(0, "rgba(255,150,150,0.75)");
      g.addColorStop(1, "rgba(255,150,150,0)");
      x.fillStyle = g; x.fillRect(0, 0, W, H);
    });
    const blushMat = new THREE.MeshBasicMaterial({ map: blushTex, transparent: true, opacity: 0.35, depthWrite: false });
    this.blush = new THREE.Mesh(new THREE.PlaneGeometry(0.34, 0.18), blushMat);
    const blush2 = this.blush.clone();
    this.blush.position.set(-0.5, -0.2, 0.74); this.blush.rotation.y = 0.5;
    blush2.position.set(0.5, -0.2, 0.74); blush2.rotation.y = -0.5;
    this.head.add(this.blush, blush2);
    this.blush2 = blush2;

    // ── hair — long black, with side locks and soft bangs
    const hairMat = this._toon(HAIR);
    this.hairBack = new THREE.Group();
    // main back volume: lathe profile sweeping from crown to below the chest
    const pts = [];
    for (let i = 0; i <= 14; i++) {
      const v = i / 14;
      const y = 1.0 - v * 3.1;                       // from crown to ~waist
      const r = 0.62 + 0.5 * Math.sin(Math.min(1, v * 1.6) * Math.PI * 0.62) + v * 0.34;
      pts.push(new THREE.Vector2(Math.max(0.1, r), y));
    }
    pts.push(new THREE.Vector2(0.02, -2.2));
    const backMesh = new THREE.Mesh(new THREE.LatheGeometry(pts, 24), hairMat);
    backMesh.scale.set(1.02, 1, 0.86);
    backMesh.position.z = -0.06;
    this.hairBack.add(backMesh);
    this.head.add(this.hairBack);

    // side locks framing the face
    for (const s of [-1, 1]) {
      const lock = new THREE.Mesh(new THREE.CapsuleGeometry(0.13, 1.7, 6, 12), hairMat);
      lock.position.set(s * 0.82, -0.62, 0.14);
      lock.rotation.z = s * -0.07;
      this.head.add(lock);
      const lockTip = new THREE.Mesh(new THREE.SphereGeometry(0.13, 12, 10), hairMat);
      lockTip.position.set(s * 0.86, -1.5, 0.1);
      this.head.add(lockTip);
    }

    // bangs — a row of soft wedges over the forehead
    for (let i = 0; i < 7; i++) {
      const t = i / 6, s = t * 2 - 1;
      const w = new THREE.Mesh(new THREE.ConeGeometry(0.16, 0.55 + 0.25 * Math.sin(i * 2.1), 8), hairMat);
      w.position.set(s * 0.55, 0.62 - 0.06 * Math.abs(s), 0.66);
      w.rotation.x = 0.5; w.rotation.z = -s * 0.22;
      this.head.add(w);
    }
    // crown cover so the scalp is never bald under the lights
    const crown = new THREE.Mesh(new THREE.SphereGeometry(0.99, 28, 18, 0, Math.PI * 2, 0, Math.PI * 0.55), hairMat);
    crown.scale.set(0.94, 1.02, 0.92);
    crown.position.y = 0.05;
    this.head.add(crown);

    // ── cyan halo ring behind her — the JARVIS signature
    this.halo = new THREE.Group();
    const haloMat = new THREE.MeshBasicMaterial({
      color: new THREE.Color(this.cfg.light), transparent: true, opacity: 0.32,
    });
    const haloMat2 = haloMat.clone(); haloMat2.opacity = 0.16;
    const ring1 = new THREE.Mesh(new THREE.TorusGeometry(1.62, 0.022, 10, 72), haloMat);
    const ring2 = new THREE.Mesh(new THREE.TorusGeometry(1.38, 0.012, 8, 64), haloMat2);
    this.halo.add(ring1, ring2);
    this.halo.position.set(0, 2.5, -0.7);
    scene.add(this.halo);

    // ── orbital companion — the ORBIT avatar, living behind her.
    //    Three tilted rings with glowing satellites; spins faster while
    //    thinking, brightens with her voice. Toggleable via cfg.orbit.
    this.orbit = new THREE.Group();
    const orbMat = new THREE.MeshBasicMaterial({
      color: new THREE.Color(this.cfg.light || "#38bdf8"),
      transparent: true, opacity: 0.30, depthWrite: false,
    });
    const ORBITS = [
      { r: 1.55, tiltX: 1.25, tiltZ: 0.20, sats: 2, op: 0.34 },
      { r: 1.95, tiltX: 1.45, tiltZ: -0.30, sats: 3, op: 0.24 },
      { r: 2.30, tiltX: 1.05, tiltZ: 0.45, sats: 1, op: 0.16 },
    ];
    this.orbitSats = [];
    for (const o of ORBITS) {
      const ring = new THREE.Mesh(new THREE.TorusGeometry(o.r, 0.012, 8, 80),
        orbMat.clone());
      ring.material.opacity = o.op;
      ring.rotation.x = o.tiltX;
      ring.rotation.z = o.tiltZ;
      this.orbit.add(ring);
      for (let s = 0; s < o.sats; s++) {
        const sat = new THREE.Mesh(
          new THREE.SphereGeometry(0.045, 10, 8),
          new THREE.MeshBasicMaterial({
            color: new THREE.Color(this.cfg.light || "#38bdf8"),
            transparent: true, opacity: 0.95, depthWrite: false,
          }));
        sat.userData = { ring, a: (s / o.sats) * Math.PI * 2, speed: 0.5 + o.r * 0.18 };
        this.orbit.add(sat);
        this.orbitSats.push(sat);
      }
    }
    this.orbit.position.set(0, 2.35, -1.7);
    this.orbit.visible = this.cfg.orbit !== false;
    scene.add(this.orbit);

    // ── hologram dust — slow-orbiting light motes around her ──────────────
    // Count scales with the perf preset; battery gets none. Additive blending
    // over the transparent canvas, one sprite texture, zero per-frame updates
    // to the geometry — the whole cloud just rotates and breathes.
    const moteCount = this.cfg.perf === "battery" ? 0
                    : this.cfg.perf === "quality" ? 90 : 60;
    if (moteCount > 0) {
      const moteTex = this._tex("mote", (x, W, H) => {
        const g = x.createRadialGradient(W / 2, H / 2, 2, W / 2, H / 2, W / 2);
        g.addColorStop(0, "rgba(255,255,255,0.9)");
        g.addColorStop(0.4, "rgba(255,255,255,0.35)");
        g.addColorStop(1, "rgba(255,255,255,0)");
        x.fillStyle = g; x.fillRect(0, 0, W, H);
      });
      const pos = new Float32Array(moteCount * 3);
      for (let i = 0; i < moteCount; i++) {
        const a = Math.random() * Math.PI * 2;
        const r = 1.2 + Math.random() * 1.6;
        pos[i * 3]     = Math.cos(a) * r;
        pos[i * 3 + 1] = Math.random() * 4.4 - 0.4;
        pos[i * 3 + 2] = Math.sin(a) * r - 0.3;
      }
      const pg = new THREE.BufferGeometry();
      pg.setAttribute("position", new THREE.BufferAttribute(pos, 3));
      const pm = new THREE.PointsMaterial({
        size: 0.06, map: moteTex, transparent: true, opacity: 0.5,
        blending: THREE.AdditiveBlending, depthWrite: false,
        sizeAttenuation: true,
        color: new THREE.Color(this.cfg.light || "#38bdf8"),
      });
      this.particles = new THREE.Points(pg, pm);
      this.particles.userData.baseOpacity = 0.5;
      scene.add(this.particles);
    }

    // ── accent ground glow — the UI's light colour pooling beneath her ────
    const glowTex = this._tex("groundglow", (x, W, H) => {
      const g = x.createRadialGradient(W / 2, H / 2, 4, W / 2, H / 2, W / 2);
      g.addColorStop(0, "rgba(255,255,255,0.55)");
      g.addColorStop(1, "rgba(255,255,255,0)");
      x.fillStyle = g; x.fillRect(0, 0, W, H);
    });
    this.glow = new THREE.Mesh(
      new THREE.PlaneGeometry(3.4, 1.5),
      new THREE.MeshBasicMaterial({
        map: glowTex, transparent: true, opacity: 0.28,
        blending: THREE.AdditiveBlending, depthWrite: false,
        color: new THREE.Color(this.cfg.light || "#38bdf8"),
      })
    );
    this.glow.rotation.x = -Math.PI / 2;
    this.glow.position.set(0, 0.01, 0.4);
    scene.add(this.glow);

    // soft fake ground shadow (a gradient sprite — no shadow maps)
    const shTex = this._tex("shadow", (x, W, H) => {
      const g = x.createRadialGradient(W / 2, H / 2, 4, W / 2, H / 2, W / 2);
      g.addColorStop(0, "rgba(0,0,0,0.45)");
      g.addColorStop(1, "rgba(0,0,0,0)");
      x.fillStyle = g; x.fillRect(0, 0, W, H);
    });
    const shadow = new THREE.Mesh(
      new THREE.PlaneGeometry(2.6, 1.0),
      new THREE.MeshBasicMaterial({ map: shTex, transparent: true, depthWrite: false })
    );
    shadow.rotation.x = -Math.PI / 2;
    shadow.position.set(0, -0.02, 0.4);
    scene.add(shadow);

    this.scene = scene;
  };

  // ── lifecycle ───────────────────────────────────────────────────────────
  Jarvis3D.prototype.init = function (mount, cfg) {
    if (!window.THREE) return false;
    this.mount = mount;
    if (cfg) Object.assign(this.cfg, cfg);
    this._applyConfig();

    this.renderer = new THREE.WebGLRenderer({ alpha: true, antialias: false });
    this.renderer.setClearColor(0x000000, 0);
    this._applyPixelRatio();
    mount.appendChild(this.renderer.domElement);

    this._build();
    this._resize();
    window.addEventListener("resize", this._onResize = () => this._resize());

    this.dead = false;
    this.lastRaf = performance.now();
    const loop = (now) => {
      if (this.dead) return;
      this._raf = requestAnimationFrame(loop);
      this._tick(now);
    };
    this._raf = requestAnimationFrame(loop);
    return true;
  };

  Jarvis3D.prototype._applyPixelRatio = function () {
    const p = FPS[this.cfg.perf] || FPS.balanced;
    const dpr = window.devicePixelRatio || 1;
    this.renderer.setPixelRatio(Math.min(dpr, p.pr === 0.75 ? 0.75 : p.pr === 1.0 ? 1 : 2));
  };

  Jarvis3D.prototype._resize = function () {
    if (!this.mount || !this.renderer) return;
    const w = this.mount.clientWidth || 300;
    const h = this.mount.clientHeight || 300;
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / Math.max(1, h);
    this.camera.updateProjectionMatrix();
  };

  Jarvis3D.prototype._applyConfig = function () {
    if (!this.root) return;
    this.root.scale.setScalar(this.cfg.size * 0.9);
    this.root.position.x = this.cfg.x;
    this.root.position.y = this.cfg.y;
    this.root.visible = this.cfg.visible !== false;
    const accent = new THREE.Color(this.cfg.light || "#38bdf8");
    if (this.rimLight) this.rimLight.color = accent;
    if (this.halo) {
      this.halo.children.forEach(r => { r.material.color = accent; });
    }
    if (this.orbit) {
      this.orbit.visible = this.cfg.orbit !== false;
      this.orbit.traverse(o => { if (o.material) o.material.color = accent; });
    }
    if (this.particles) this.particles.material.color = accent;
    if (this.glow) this.glow.material.color = accent;
    this._applyPixelRatio();
    this._resize();
  };

  Jarvis3D.prototype.configure = function (cfg) {
    Object.assign(this.cfg, cfg || {});
    this._applyConfig();
  };

  Jarvis3D.prototype.setState = function (s) { this.state = s; };
  Jarvis3D.prototype.setLevel = function (v) { this.level = v; };
  Jarvis3D.prototype.setViseme = function (v) { this.viseme = v; };

  Jarvis3D.prototype.setExpression = function (expr, seconds) {
    this.expression = expr;
    this.exprUntil = this.t + (seconds || 3);
  };

  Jarvis3D.prototype.pause = function (p) { this.paused = !!p; };

  Jarvis3D.prototype.destroy = function () {
    this.dead = true;
    if (this._raf) cancelAnimationFrame(this._raf);
    if (this._onResize) window.removeEventListener("resize", this._onResize);
    if (this.renderer) {
      this.renderer.dispose();
      if (this.renderer.domElement && this.renderer.domElement.parentNode) {
        this.renderer.domElement.parentNode.removeChild(this.renderer.domElement);
      }
      try { this.renderer.forceContextLoss(); } catch (e) { /* ignore */ }
    }
    if (this.scene) {
      this.scene.traverse((o) => {
        if (o.geometry) o.geometry.dispose();
        if (o.material) {
          (Array.isArray(o.material) ? o.material : [o.material]).forEach(m => {
            if (m.map) m.map.dispose();
            m.dispose();
          });
        }
      });
    }
    this._texCache = {};
    this.renderer = this.scene = this.camera = this.root = null;
    if (this.mount) this.mount.textContent = "";
  };

  // ── animation ───────────────────────────────────────────────────────────
  Jarvis3D.prototype._tick = function (now) {
    const dt = Math.min(0.1, (now - this.lastRaf) / 1000);
    this.lastRaf = now;
    this.t += dt;

    if (this.paused || document.hidden) return;

    // FPS governor — fewer frames while idle, more while speaking/expressing
    const p = FPS[this.cfg.perf] || FPS.balanced;
    const active = this.state === "SPEAKING" || this.state === "EXECUTING" ||
                   this.expression !== null || this.blinking > 0;
    const target = active ? p.active : p.idle;
    this.acc += dt;
    if (this.acc < 1 / target) return;
    const step = this.acc; this.acc = 0;

    const anim = this.cfg.anim;
    const t = this.t;

    // expressions expire
    if (this.expression && t > this.exprUntil) this._applyFace("normal");

    // ── living presence: drift, sway, lean and voice-pulse ───────────────
    // Layered sines give the target, springs give the glide — she can never
    // jerk or teleport, and every state change blends instead of cutting.
    // Speech intensity scales the amplitudes: quiet voice ≈ still, excited
    // speech visibly (but gently) moves more.
    const lvl3 = Math.max(this.level, this.viseme.level);
    this._energy += (Math.min(1, lvl3 * 1.15) - this._energy) * Math.min(1, step * 3.2);
    this._energySlow += (this._energy - this._energySlow) * Math.min(1, step * 1.4);
    const en = this._energy, enSlow = this._energySlow;
    let tx, ty, tz, trot, tpulse;
    if (this.state === "SPEAKING") {
      // speech drives everything, scaled by loudness; a slow talking rhythm
      // rides on top so she nods while she talks
      tx   = drift3(t, 0.9, 0.41, 0.0) * (0.018 + en * 0.040);
      ty   = drift3(t, 0.7, 1.3,  2.1) * (0.012 + en * 0.026)
           + Math.sin(t * 3.1) * en * 0.008;
      tz   = drift3(t, 0.5, 0.33, 4.2) * (0.012 + en * 0.034);
      trot = drift3(t, 0.6, 0.29, 1.3) * (0.008 + enSlow * 0.022);
      tpulse = 0.008 + en * 0.042;
    } else if (this.state === "THINKING") {
      // slow intelligent ambient: long, unhurried wanders
      tx   = drift3(t, 0.33, 0.13, 2.6) * 0.020;
      ty   = drift3(t, 0.27, 0.11, 4.9) * 0.012;
      tz   = drift3(t, 0.21, 0.09, 0.7) * 0.014;
      trot = drift3(t, 0.3, 0.12, 5.5) * 0.016;
      tpulse = 0.003;
    } else if (this.state === "LISTENING") {
      // attentive: nearly still, a slow lean toward "you"
      tx   = drift3(t, 0.5, 0.23, 5.0) * 0.010;
      ty   = drift3(t, 0.4, 0.19, 1.8) * 0.007 + 0.003;
      tz   = 0.005 + this.level * 0.010;
      trot = drift3(t, 0.4, 0.17, 3.3) * 0.007;
      tpulse = 0.004 + this.level * 0.014;
    } else {
      // idle / executing / sleeping: soft breathing and a lazy orbit
      tx   = drift3(t, 0.4, 0.17, 1.1) * 0.012;
      ty   = drift3(t, 0.31, 0.14, 3.8) * 0.009;
      tz   = drift3(t, 0.24, 0.10, 2.2) * 0.009;
      trot = drift3(t, 0.35, 0.15, 0.4) * 0.010;
      tpulse = 0.003 + 0.003 * Math.sin(t * 0.9);
    }
    const mx     = spring3(this._motion.x, tx, step, 2.2, 3.4);
    const my     = spring3(this._motion.y, ty, step, 2.4, 3.6);
    const mz     = spring3(this._motion.z, tz, step, 1.8, 3.0);
    const mrot   = Math.max(-0.05, Math.min(0.05, spring3(this._motion.rot, trot, step, 1.6, 2.6)));
    const mpulse = Math.max(-0.02, Math.min(0.07, spring3(this._motion.pulse, tpulse, step, 6.0, 5.0)));

    // breathing + weight shift (always alive), now carrying the voice swell
    // and the drift: x/z glide, the whole body breathes, rotation tilts her
    const breathe = Math.sin(t * 1.25) * 0.012 * anim;
    this.body.scale.set(1 + breathe * 0.4 + mpulse, 1 + breathe + mpulse, 1 + breathe * 0.4 + mpulse);
    this.body.rotation.z = Math.sin(t * 0.45) * 0.012 * anim + mrot;
    this.body.position.x = mx;
    this.body.position.z = mz * 0.8;

    // head pose per state
    const head = this.head;
    let hx = 0, hy = 0, hz = 0;
    if (this.state === "SLEEPING") { hx = 0.3; hy = 0.08; }
    else if (this.state === "THINKING") { hx = 0.06; hy = -0.14; hz = -0.1; }
    else if (this.state === "LISTENING") { hz = 0.06 * anim; }
    else if (this.state === "EXECUTING") { hx = 0.04; }
    if (this.state !== "SLEEPING") {
      hy += Math.sin(t * 0.5) * 0.05 * anim;              // idle sway
      hx += Math.sin(t * 0.8) * 0.015 * anim;
      if (this.state === "SPEAKING") {
        hx += Math.sin(t * 5.2) * 0.02 * (0.4 + this.viseme.level) * anim;  // talking nods
      }
    }
    head.rotation.x += (hx - head.rotation.x) * Math.min(1, step * 5);
    head.rotation.y += (hy - head.rotation.y) * Math.min(1, step * 5);
    head.rotation.z += (hz - head.rotation.z) * Math.min(1, step * 5);

    // hair follows the head with a lag — sells the whole effect
    if (this.hairBack) {
      this.hairBack.rotation.x = -head.rotation.x * 0.35 + Math.sin(t * 1.1) * 0.012 * anim;
      this.hairBack.rotation.z = -head.rotation.z * 0.3;
    }

    // halo: slow spin + pulse while speaking; fast shimmer while thinking
    if (this.halo) {
      this.halo.rotation.z += step * (this.state === "THINKING" ? 1.1 : 0.25);
      const pulse = this.state === "SPEAKING"
        ? 0.3 + 0.2 * Math.abs(Math.sin(t * 6)) : 0.28 + 0.06 * Math.sin(t * 1.6);
      this.halo.children[0].material.opacity = pulse;
    }

    // orbital companion: precess slowly, satellites ride their rings, the
    // whole system brightens with her voice and races while thinking
    if (this.orbit && this.orbit.visible) {
      const spin = this.state === "THINKING" ? 2.4 : this.state === "SPEAKING" ? 0.9 : 0.3;
      this.orbit.rotation.y += step * spin * 0.4;
      this.orbit.rotation.z = Math.sin(t * 0.21) * 0.08;      // slow precession
      for (const sat of this.orbitSats) {
        sat.userData.a += step * spin * sat.userData.speed * 0.55;
        const ring = sat.userData.ring;
        const ex = Math.cos(sat.userData.a) * ring.geometry.parameters.radius;
        const ey = Math.sin(sat.userData.a) * ring.geometry.parameters.radius;
        sat.position.set(
          ex * Math.cos(ring.rotation.z) - ey * Math.sin(ring.rotation.z) * 0.25,
          ey * Math.cos(ring.rotation.x),
          ey * Math.sin(ring.rotation.x));
      }
      const bright = this.state === "SPEAKING" ? 0.55 + this.level * 0.6
                   : this.state === "THINKING" ? 0.5
                   : 0.3 + 0.08 * Math.sin(t * 1.4);
      for (const ring of this.orbit.children) {
        if (ring.geometry && ring.geometry.type === "TorusGeometry") {
          ring.material.opacity +=
            (bright * 0.5 - ring.material.opacity) * Math.min(1, step * 3);
        }
      }
    }

    // hologram dust: slow orbit, brightening with the voice
    if (this.particles) {
      this.particles.rotation.y += step * (this.state === "THINKING" ? 0.35 : 0.08);
      const moteTarget = this.particles.userData.baseOpacity *
        (this.state === "SPEAKING" ? 0.75 + this.level * 0.9
                                   : 0.85 + 0.15 * Math.sin(t * 1.3));
      this.particles.material.opacity +=
        (moteTarget - this.particles.material.opacity) * Math.min(1, step * 4);
    }
    // ground glow + rim light rise with her voice — she "lights up"
    if (this.glow) {
      const gt = 0.22 + (this.state === "SPEAKING" ? this.level * 0.5
                                                   : 0.06 * Math.sin(t * 1.1));
      this.glow.material.opacity += (gt - this.glow.material.opacity) * Math.min(1, step * 4);
    }
    if (this.rimLight) {
      const ri = 1.15 + (this.state === "SPEAKING" ? this.level * 0.55 : 0);
      this.rimLight.intensity += (ri - this.rimLight.intensity) * Math.min(1, step * 5);
    }

    // gaze: micro drift between points of focus — thinking looks away and
    // up, listening settles on "you", speaking keeps gentle motion
    if (t > this.gaze.nextAt) {
      const thinking = this.state === "THINKING";
      this.gaze.tx = thinking ? -0.05 - Math.random() * 0.04
                              : (Math.random() * 2 - 1) * 0.035;
      this.gaze.ty = thinking ? 0.035 + Math.random() * 0.03
                              : (Math.random() * 2 - 1) * 0.02;
      this.gaze.nextAt = t + (thinking ? 1.2 : 1.8) + Math.random() * 2.2;
    }
    if (this.state === "LISTENING") { this.gaze.tx *= 0.4; this.gaze.ty *= 0.4; }
    this.gaze.x += (this.gaze.tx - this.gaze.x) * Math.min(1, step * 6);
    this.gaze.y += (this.gaze.ty - this.gaze.y) * Math.min(1, step * 6);
    if (this.eyeL) {
      this.eyeL.position.x = -0.34 + this.gaze.x;
      this.eyeR.position.x = 0.34 + this.gaze.x;
      this.eyeL.position.y = 0.05 + this.gaze.y;
      this.eyeR.position.y = 0.05 + this.gaze.y;
    }

    // blinking
    if (t > this.blinkAt && this.state !== "SLEEPING") {
      this.blinking = 0.16;
      this.blinkAt = t + 2.2 + Math.random() * 3.4;
    }
    if (this.blinking > 0) {
      this.blinking -= step;
      this._setEyes("closed");
      if (this.blinking <= 0) {
        // ~1 in 5 blinks is a quick double-blink — very alive, very anime
        if (!this._dblBlink && Math.random() < 0.18) {
          this._dblBlink = true;
          this.blinking = 0.12;
          this.blinkAt = t + 0.3;
        } else {
          this._dblBlink = false;
          this._applyFace(this.expression || "normal");
        }
      }
    }

    // speaking mouth — three viseme zones from the live audio analysis:
    // closed-ish smile → rounded "o" → full open, with width shaping
    if (this.state === "SPEAKING" && this.blinking <= 0) {
      const open = this.viseme.openness || 0;
      const wide = this.viseme.width || 0;
      let style = "smile";
      if (open > 0.45) style = "open";
      else if (open > 0.22) style = (wide > 0.15) ? "smile" : "o";
      this._setMouth(style);
      this.mouth.scale.y = 0.7 + open * 1.1;
      this.mouth.scale.x = 1 - wide * 0.18;
    } else {
      this.mouth.scale.y += (1 - this.mouth.scale.y) * Math.min(1, step * 8);
      this.mouth.scale.x += (1 - this.mouth.scale.x) * Math.min(1, step * 8);
    }

    // mic level → tiny body emphasis while listening (layered ON TOP of the
    // living drift, which owns x/z — the two never fight over an axis)
    const levelLift = this.state === "LISTENING" ? this.level * 0.03 * anim : 0;
    this.body.position.y = my * 0.5 + levelLift;

    this.renderer.render(this.scene, this.camera);
  };

  Jarvis3D.prototype._setEyes = function (style) {
    if (this.eyeL.material._style !== style) {
      const mL = this._eyeMat(style), mR = this._eyeMat(style);
      // mirror the sad lid so both eyes droop outward correctly
      this.eyeL.material.dispose(); this.eyeR.material.dispose();
      this.eyeL.material = mL; this.eyeR.material = mR;
      this.eyeL.material._style = style; this.eyeR.material._style = style;
    }
  };

  Jarvis3D.prototype._setMouth = function (style) {
    if (this.mouth.material._style !== style) {
      this.mouth.material.dispose();
      this.mouth.material = this._mouthMat(style);
      this.mouth.material._style = style;
    }
  };

  // expression → face texture mapping (scaled by expr intensity)
  Jarvis3D.prototype._applyFace = function (expr) {
    if (expr !== this.expression && expr !== "normal") this.expression = expr;
    if (expr === "normal") this.expression = null;
    const k = Math.max(0, Math.min(1.5, this.cfg.expr || 1));

    switch (expr) {
      case "happy":
        this._setEyes("happy"); this._setMouth("smile");
        this.blush.material.opacity = 0.35 + 0.3 * k; this.blush2.material.opacity = 0.35 + 0.3 * k;
        this.browL.rotation.z = 0.10 * k; this.browR.rotation.z = -0.10 * k;
        break;
      case "playful":
        this._setEyes("wink"); this._setMouth("cat");
        this.blush.material.opacity = 0.3 + 0.35 * k; this.blush2.material.opacity = 0.3;
        this.browL.rotation.z = 0.14 * k; this.browR.rotation.z = -0.04 * k;
        break;
      case "surprised":
        this._setEyes("wide"); this._setMouth("o");
        this.blush.material.opacity = 0.2; this.blush2.material.opacity = 0.2;
        this.browL.position.y = 0.4 + 0.07 * k; this.browR.position.y = 0.4 + 0.07 * k;
        this.browL.rotation.z = 0; this.browR.rotation.z = 0;
        break;
      case "sad":
      case "error":
        this._setEyes("sad"); this._setMouth("sad");
        this.blush.material.opacity = 0.15; this.blush2.material.opacity = 0.15;
        this.browL.rotation.z = -0.22 * k; this.browR.rotation.z = 0.22 * k;
        this.browL.position.y = 0.4; this.browR.position.y = 0.4;
        break;
      case "confused":
        this._setEyes("normal"); this._setMouth("neutral");
        this.browL.rotation.z = 0.16 * k; this.browR.rotation.z = -0.02 * k;
        this.browL.position.y = 0.4; this.browR.position.y = 0.4;
        break;
      default:
        this._setEyes("normal"); this._setMouth("smile");
        this.blush.material.opacity = 0.3; this.blush2.material.opacity = 0.3;
        this.browL.rotation.z = 0; this.browR.rotation.z = 0;
        this.browL.position.y = 0.4; this.browR.position.y = 0.4;
    }
  };

  // ── module surface ──────────────────────────────────────────────────────
  window.Jarvis3DAvatar = {
    available() { return !!window.THREE; },
    create(mount, cfg) {
      if (A3) this.destroy();
      A3 = new Jarvis3D();
      const ok = A3.init(mount, cfg);
      if (!ok) { A3 = null; return null; }
      return A3;
    },
    instance() { return A3; },
    destroy() {
      if (A3) { A3.destroy(); A3 = null; }
    },
  };
})();
