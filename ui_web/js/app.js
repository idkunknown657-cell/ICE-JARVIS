/* ═══════════════════════════════════════════════════════════════════════
   app.js — JARVIS frontend application.
   Talks to the Python bridge (webui.py) over window.pywebview.api and
   window events. No frameworks, no build step, no polling of the backend.
   ═══════════════════════════════════════════════════════════════════════ */
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);

  // ── bridge ──────────────────────────────────────────────────────────────
  const Bus = {
    listeners: {},
    on(name, fn) { (this.listeners[name] = this.listeners[name] || []).push(fn); },
    emit(name, payload) { (this.listeners[name] || []).forEach(fn => { try { fn(payload); } catch (e) { console.error(e); } }); },
  };
  window.__jarvisEvent = (name, payload) => Bus.emit(name, payload);

  const api = new Proxy({}, {
    get(_t, prop) {
      return (...args) => {
        if (window.pywebview && window.pywebview.api) {
          return window.pywebview.api[prop](...args);
        }
        return Promise.reject(new Error("backend not ready"));
      };
    },
  });

  // ── tiny DOM helper ─────────────────────────────────────────────────────
  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  // ── app state ───────────────────────────────────────────────────────────
  const S = {
    view: "home",
    state: "BOOTING",
    muted: false,
    assistantName: "JARVIS",
    userName: "",
    accent: "",
    hudStyle: "face",
    compact: false,
    configured: true,
    wake: { enabled: false, awake: true, ready: false },
    screen: { active: false, caption: "" },
    memoryEnabled: true,
    attachedFile: null,
    lastUserText: "",
    generating: false,
    typingEl: null,
    perf: null,
    settingsLoaded: null,
    settingsPage: "general",
    avatar: { mode: "classic", size: 1, x: 0, y: 0, anim: 1, expr: 1,
              light: "#38bdf8", perf: "balanced", visible: true, orbit: true },
    avatar3dLoading: false,
  };

  // ════════════════════════════════════════════════════════════════════════
  //  AVATAR MODES — classic 2D face · minimal core · optional 3D anime girl
  // ════════════════════════════════════════════════════════════════════════
  function feedAvatar3D(name, payload) {
    const a3 = window.Jarvis3DAvatar && window.Jarvis3DAvatar.instance();
    if (!a3) return;
    if (name === "state") a3.setState(payload);
    else if (name === "audio_level") a3.setLevel(payload.level);
    else if (name === "viseme_now") a3.setViseme(payload);
  }

  function load3DScript(cb) {
    if (window.Jarvis3DAvatar) { cb(true); return; }
    if (S.avatar3dLoading) return;
    S.avatar3dLoading = true;
    const s = document.createElement("script");
    s.src = "vendor/three.min.js";
    s.onload = () => {
      const s2 = document.createElement("script");
      s2.src = "js/avatar3d.js";
      s2.onload = () => { S.avatar3dLoading = false; cb(true); };
      s2.onerror = () => { S.avatar3dLoading = false; cb(false); };
      document.head.appendChild(s2);
    };
    s.onerror = () => { S.avatar3dLoading = false; cb(false); };
    document.head.appendChild(s);
  }

  const AVATAR_LABEL = { classic: "CLASSIC", minimal: "MINIMAL", orbit: "ORBIT",
                         helix: "HELIX", anime3d: "3D ANIME" };

  // Bottom-corner HUD readouts: which avatar is live and whether JARVIS can
  // currently see the screen. Cheap, and the answer to "did that click do
  // anything?" without hunting through Settings.
  function paintHud() {
    const m = $("hudMode");
    if (m) m.innerHTML = "MODE · <b>" + esc(AVATAR_LABEL[S.avatar.mode] || "CLASSIC") + "</b>";
    const g = $("hudSight");
    if (g) g.innerHTML = "SIGHT · <b>" + (S.screen.active ? "WATCHING" : "OFF") + "</b>";
    document.body.dataset.sight = S.screen.active ? "1" : "0";
  }

  function applyAvatarMode(cfg) {
    if (cfg) Object.assign(S.avatar, cfg);
    const mode = S.avatar.mode;
    paintHud();
    const face = $("face");
    const mount = $("avatar3dMount");
    const av2d = window.JarvisAvatar;

    if (mode === "anime3d") {
      load3DScript((ok) => {
        if (!ok) { toast("3D avatar unavailable — staying classic", "err");
                   S.avatar.mode = "classic"; applyAvatarMode(); return; }
        if (S.avatar.mode !== "anime3d") return;    // user switched away meanwhile
        if (av2d) av2d.setPaused(true);
        face.style.display = "none";
        mount.hidden = false;
        const a3 = window.Jarvis3DAvatar.create(mount, S.avatar);
        if (a3) a3.setState(S.state);
      });
    } else {
      window.Jarvis3DAvatar && window.Jarvis3DAvatar.destroy();
      mount.hidden = true; mount.textContent = "";
      face.style.display = "";
      face.style.opacity = "";
      face.style.transition = "";
      if (av2d) {
        av2d.setPaused(false);
        av2d.style = mode === "minimal" ? "minimal"
          : mode === "orbit" ? "orbit"
          : mode === "helix" ? "helix"
          : (S.hudStyle === "core" ? "core" : "face");
        av2d.setVisible(S.avatar.visible !== false);
      }
    }
  }

  // ════════════════════════════════════════════════════════════════════════
  //  VIEW ROUTER
  // ════════════════════════════════════════════════════════════════════════
  function showView(name) {
    S.view = name;
    document.querySelectorAll(".view").forEach(v => v.classList.remove("active"));
    $("view-" + name).classList.add("active");
    document.querySelectorAll(".nav-btn").forEach(b =>
      b.classList.toggle("active", b.dataset.view === name));
    if (name === "settings" && !S.settingsLoaded) loadSettings();
    if (name === "chat") {
      const sc = $("chatScroll");
      requestAnimationFrame(() => { sc.scrollTop = sc.scrollHeight; });
    }
  }

  document.querySelectorAll(".nav-btn").forEach(b =>
    b.addEventListener("click", () => showView(b.dataset.view)));

  // ════════════════════════════════════════════════════════════════════════
  //  STATE / FACE / TOPBAR
  // ════════════════════════════════════════════════════════════════════════
  const STATE_CAPTIONS = {
    BOOTING: "Initialising…", LISTENING: "Listening", THINKING: "Thinking…",
    EXECUTING: "Working…", SPEAKING: "Speaking", SLEEPING: "Sleeping — say “Hey Jarvis”",
    MUTED: "Microphone muted", ERROR: "Something went wrong",
  };

  function applyState(state) {
    if (state === S.state && state !== "LISTENING") { /* still refresh UI below */ }
    const wasExecuting = S.state === "EXECUTING";
    S.state = state;

    const pill = $("statePill");
    pill.dataset.state = state;
    pill.textContent = state;
    document.body.dataset.state = state;   // state-reactive ambience hooks
    const av = window.JarvisAvatar;
    if (av) {
      av.state = state === "MUTED" ? "MUTED" : state;
      av.muted = S.muted;
    }
    $("faceCaption").textContent = STATE_CAPTIONS[state] || state;

    // HUD readout: the face caption's little sibling, top-left of the stage
    const hud = $("hudState");
    if (hud) hud.innerHTML = "SYS · <b>" + esc(String(state)) + "</b>";

    // stop button visible while speaking
    $("btnStop").hidden = state !== "SPEAKING" && state !== "EXECUTING";

    // typing indicator follows THINKING
    if (state === "THINKING") showTyping();
    else hideTyping();

    // task chip lifecycle
    if (state === "EXECUTING") {
      setTask(null);                       // start fresh
    } else if (wasExecuting && taskActive()) {
      finishTask();
    }
  }

  // ── task / automation chip ─────────────────────────────────────────────
  let taskTimer = null;
  function taskActive() { return !$("taskChip").hidden && !$("taskChip").classList.contains("done"); }
  function setTask(label) {
    const chip = $("taskChip");
    chip.hidden = false;
    chip.classList.remove("done");
    $("taskDone").hidden = true;
    $("taskLabel").textContent = label || "Working…";
  }
  function finishTask() {
    const chip = $("taskChip");
    chip.classList.add("done");
    $("taskDone").hidden = false;
    $("taskLabel").textContent = "Task complete";
    clearTimeout(taskTimer);
    taskTimer = setTimeout(() => { chip.hidden = true; }, 2600);
  }

  // ════════════════════════════════════════════════════════════════════════
  //  CHAT
  // ════════════════════════════════════════════════════════════════════════
  const chatList = $("chatList");
  const chatScroll = $("chatScroll");
  let chatHasContent = false;

  function nearBottom() {
    return chatScroll.scrollHeight - chatScroll.scrollTop - chatScroll.clientHeight < 90;
  }
  function scrollToEnd(force) {
    if (force || nearBottom()) chatScroll.scrollTop = chatScroll.scrollHeight;
  }
  chatScroll.addEventListener("scroll", () => {
    $("scrollLatest").hidden = nearBottom();
  });
  $("scrollLatest").addEventListener("click", () => scrollToEnd(true));

  function showTyping() {
    if (S.typingEl) return;
    const m = el("div", "msg msg-ai");
    const b = el("div", "bubble");
    const d = el("span", "typing-dots");
    d.appendChild(el("i")); d.appendChild(el("i")); d.appendChild(el("i"));
    b.appendChild(d);
    m.appendChild(b);
    chatList.appendChild(m);
    S.typingEl = m;
    scrollToEnd();
  }
  function hideTyping() {
    if (S.typingEl) { S.typingEl.remove(); S.typingEl = null; }
  }

  // progressive reveal — feels like streaming without backend changes
  function revealText(bubble, text, done) {
    let i = 0;
    const step = Math.max(2, Math.round(text.length / 60));
    const iv = setInterval(() => {
      i += step;
      if (i >= text.length) {
        clearInterval(iv);
        window.mdRender(text, bubble);
        if (done) done();
        return;
      }
      // render partial markdown each tick (cheap for short partials)
      window.mdRender(text.slice(0, i), bubble);
      scrollToEnd();
    }, 30);
  }

  function addMessage(role, text) {
    if (!text || !text.trim()) return;
    hideTyping();
    if (!chatHasContent) {
      const empty = $("chatEmpty");
      if (empty) empty.remove();
      chatHasContent = true;
    }
    const stick = nearBottom();

    const m = el("div", "msg msg-" + role);
    const bubble = el("div", "bubble");

    if (role === "ai") {
      revealText(bubble, text, () => attachMeta(m, role, text));
      // expression hints from emoji — drive whichever avatar is active
      let expr = null;
      if (/😄|🙂|😀|😊|🎉|✨/.test(text)) expr = "happy";
      else if (/😂|🤣|😜/.test(text)) expr = "playful";
      else if (/😮|😲|❗|❕/.test(text)) expr = "surprised";
      else if (/😢|🥺|💔|😢/.test(text)) expr = "sad";
      else if (/🤔|🧐|❓|hmm/i.test(text)) expr = "confused";
      if (expr) {
        const av = window.JarvisAvatar;
        if (av && S.avatar.mode !== "anime3d") av.setExpression(expr, 3);
        const a3 = window.Jarvis3DAvatar && window.Jarvis3DAvatar.instance();
        if (a3) a3.setExpression(expr, 3);
      }
    } else if (role === "sys" || role === "err") {
      bubble.textContent = text;
    } else {
      bubble.textContent = text;
    }
    m.appendChild(bubble);
    chatList.appendChild(m);
    if (stick || role === "user") scrollToEnd(true);
  }

  function attachMeta(m, role, text) {
    if (role !== "ai") return;
    const meta = el("div", "msg-meta");
    const copy = el("button", null, "Copy");
    copy.addEventListener("click", () => {
      navigator.clipboard.writeText(text).then(() => { copy.textContent = "Copied"; setTimeout(() => copy.textContent = "Copy", 1200); });
    });
    const re = el("button", null, "Regenerate");
    re.addEventListener("click", () => { if (S.lastUserText) sendText(S.lastUserText, true); });
    meta.appendChild(copy);
    meta.appendChild(re);
    m.appendChild(meta);
  }

  function classifyLine(line) {
    const s = String(line || "").trim();
    const low = s.toLowerCase();
    if (low.startsWith("you:") || low.startsWith("user:")) return ["user", s.split(":").slice(1).join(":").trim()];
    if (low.startsWith("err:")) return ["err", s.split(":").slice(1).join(":").trim()];
    if (low.startsWith("sys:") || low.startsWith("net:")) return ["sys", s.split(":").slice(1).join(":").trim()];
    const head = s.split(":")[0];
    if (head && head.trim().toUpperCase() === S.assistantName.toUpperCase()) {
      return ["ai", s.split(":").slice(1).join(":").trim()];
    }
    return ["sys", s];
  }

  function tNow() { return performance.now() / 1000; }

  function prefersReducedMotion() {
    return !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  }

  // Smooth mode crossfade: the stage dips out, the style swaps mid-fade, and
  // the new look fades back in. Never blocks — old-mode rendering continues
  // while the fade runs, so there is no blank-frame flash.
  // Hardened: a thrown swap or a throttled rAF can no longer strand the stage
  // at opacity 0, and clicks that land mid-fade are queued instead of dropped.
  let _fadeBusy = false, _fadePending = null;
  function crossfadeAvatar(applyFn) {
    const stage = $("faceStage");
    if (_fadeBusy) { _fadePending = applyFn; return; }   // run the newest swap once this one ends
    // No stage, a background window (rAF is paused there) or reduced motion:
    // swap immediately rather than risk a fade that never completes.
    if (!stage || document.hidden || prefersReducedMotion()) { applyFn(); return; }
    _fadeBusy = true;
    stage.classList.add("av-fade");
    let done = false;
    const finish = () => {
      if (done) return;
      done = true;
      stage.classList.remove("av-fade");
      _fadeBusy = false;
      const next = _fadePending;
      _fadePending = null;
      if (next) crossfadeAvatar(next);
    };
    setTimeout(() => {
      try { applyFn(); }
      catch (err) { console.warn("avatar mode swap failed", err); }
      requestAnimationFrame(() => requestAnimationFrame(finish));
      setTimeout(finish, 420);      // rAF is frozen in a hidden window — never stay faded out
    }, 170);
  }

  function onLog(payload) {
    const line = payload.line || "";
    if (!line.trim()) return;
    const [role, text] = classifyLine(line);
    addMessage(role, text);

    // activity feed on home
    const act = $("activityList");
    const d = el("div", role === "user" ? "you" : role);
    d.textContent = (role === "user" ? "You: " : role === "ai" ? S.assistantName + ": " : "") + text;
    act.prepend(d);
    while (act.children.length > 30) act.lastChild.remove();

    // task narration while EXECUTING
    if (S.state === "EXECUTING" && (role === "ai" || role === "sys") && taskActive()) {
      $("taskLabel").textContent = text.replace(/…$/, "");
    }
  }

  // ════════════════════════════════════════════════════════════════════════
  //  INPUT DOCK
  // ════════════════════════════════════════════════════════════════════════
  const input = $("input");

  function autosize() {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 120) + "px";
  }
  input.addEventListener("input", autosize);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendClicked(); }
  });

  function sendText(text, isRegen) {
    text = (text || "").trim();
    if (!text) return;
    if (!isRegen) {
      S.lastUserText = text;
      input.value = ""; autosize();
    }
    addMessage("user", text);
    api.send_text(text).catch(err => toast("Backend not reachable", "err"));
  }
  function sendClicked() { sendText(input.value); }
  $("btnSend").addEventListener("click", sendClicked);

  $("btnStop").addEventListener("click", () => api.interrupt().catch(() => {}));

  // mute (F4)
  function toggleMute() {
    api.toggle_mute().then(r => {
      if (r && typeof r.muted === "boolean") applyMuted(r.muted);
    }).catch(() => {});
  }
  function applyMuted(m) {
    S.muted = m;
    $("btnMic").textContent = m ? "🔇" : "🎙";
    $("btnMic").style.color = m ? "var(--red)" : "";
    const av = window.JarvisAvatar;
    if (av) av.muted = m;
    if (m && S.state !== "SLEEPING") { $("statePill").dataset.state = "MUTED"; $("statePill").textContent = "MUTED"; }
    else { $("statePill").dataset.state = S.state; $("statePill").textContent = S.state; }
    $("valMic").textContent = m ? "Muted" : "Live";
    $("tileMic").classList.toggle("off", m);
  }
  $("btnMic").addEventListener("click", toggleMute);

  // call button — hold to talk, tap wakes/sleeps
  (function callBtn() {
    const b = $("btnCall");
    let held = false, moved = false;
    b.addEventListener("pointerdown", () => {
      held = true; moved = false;
      b.classList.add("held");
      api.set_ptt(true).then(r => {
        if (r && r.fallback) { moved = true; api.wake_manual(); }
      }).catch(() => {});
    });
    const release = () => {
      if (!held) return;
      held = false;
      b.classList.remove("held");
      api.set_ptt(false).catch(() => {});
      if (!moved) { /* tap → wake/sleep already handled by backend ptt(true) */ }
    };
    b.addEventListener("pointerup", release);
    b.addEventListener("pointerleave", release);
  })();

  // attach
  $("btnAttach").addEventListener("click", () => {
    api.open_file_dialog().then(r => {
      if (r && r.path) {
        S.attachedFile = r;
        $("attachName").textContent = r.name;
        $("attachChip").hidden = false;
        addMessage("sys", "Attached " + r.name);
        input.focus();
      }
    }).catch(() => {});
  });
  $("attachClear").addEventListener("click", () => {
    S.attachedFile = null;
    $("attachChip").hidden = true;
    api.clear_attach().catch(() => {});
  });

  // send also clears attachment (backend mirrors the old behaviour)
  const origSendText = sendText;
  $("btnSend").addEventListener("click", () => {
    if (S.attachedFile) { S.attachedFile = null; $("attachChip").hidden = true; }
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && S.attachedFile) { S.attachedFile = null; $("attachChip").hidden = true; }
  });

  // keyboard shortcuts
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !$("confirmVeil").hidden) { confirmAnswer(false); return; }
    if (e.key === "Escape") { api.interrupt().catch(() => {}); }
    if (e.key === "F4") { e.preventDefault(); toggleMute(); }
    if (e.key === "F11") { e.preventDefault(); api.window("fullscreen"); }
  });

  // ════════════════════════════════════════════════════════════════════════
  //  QUICK ACTIONS + COMMANDS
  // ════════════════════════════════════════════════════════════════════════
  const QUICK = [
    ["🖥 Apps", "List my installed apps and open one for me."],
    ["🔍 Search", "Search the web for something interesting."],
    ["▶ YouTube", "Open YouTube."],
    ["🎮 Steam", "Open my Steam library."],
    ["🐙 GitHub", "Open GitHub."],
    ["📁 Files", "Open File Explorer."],
    ["🖱 PC Control", "Open the PC control panel."],
    ["⋯ Commands", null],   // popover
  ];
  const CMD_POP = [
    ["📰 Today's news", "Give me today's top news, briefly."],
    ["🎵 Play music", "Play some music."],
    ["🕑 Time & date", "What time is it, and what is today's date?"],
    ["🌤 Weather", "What's the weather right now?"],
    ["🧠 Memory", "__memory__"],
    ["🖼 Wallpaper", "Change the wallpaper."],
    ["↩ Undo last action", "__undo__"],
    ["👁 Screen status", "What can you see on my screen right now?"],
  ];

  // Quick-launch pills were removed by design — everything they did is a
  // plain chat message JARVIS understands. Keeping buildQuick as a no-op so
  // legacy anchors don't break.

  function openCmdPopover(anchor) {
    const pop = $("cmdPopover");
    pop.textContent = "";
    CMD_POP.forEach(([label, cmd]) => {
      const b = el("button", null, label);
      b.addEventListener("click", () => {
        pop.hidden = true;
        if (cmd === "__memory__") showView("settings"), gotoSettingsPage("memory");
        else if (cmd === "__undo__") api.undo_last().catch(() => {});
        else api.quick_action(cmd).catch(() => {});
      });
      pop.appendChild(b);
    });
    const r = anchor.getBoundingClientRect();
    pop.style.left = Math.min(r.left, window.innerWidth - 230) + "px";
    pop.style.bottom = (window.innerHeight - r.top + 8) + "px";
    pop.style.top = "auto";
    pop.hidden = false;
    setTimeout(() => document.addEventListener("pointerdown", closePopOnce), 0);
  }
  function closePopOnce(e) {
    const pop = $("cmdPopover");
    if (!pop.contains(e.target)) pop.hidden = true;
    document.removeEventListener("pointerdown", closePopOnce);
  }

  // ════════════════════════════════════════════════════════════════════════
  //  HOME SIDE PANEL
  // ════════════════════════════════════════════════════════════════════════
  function refreshHomeTiles() {
    const w = S.wake;
    const wakeEl = $("valWake");
    wakeEl.textContent = w.ready ? (w.enabled ? (w.awake ? "Armed · awake" : "Armed · asleep") : "Ready") : "Not installed";
    $("tileWake").classList.toggle("on", !!(w.enabled && w.awake));
    $("tileWake").classList.toggle("off", !w.enabled);
    $("tileWake").onclick = () => { showView("settings"); gotoSettingsPage("startup"); };

    $("valMic").textContent = S.muted ? "Muted" : "Live";
    $("tileMic").classList.toggle("off", S.muted);
    $("tileMic").onclick = toggleMute;

    const eyes = $("valEyes");
    eyes.textContent = S.screen.active ? ("On · " + (S.screen.caption || "watching")) : "Off";
    $("tileEyes").classList.toggle("on", S.screen.active);
    $("tileEyes").classList.toggle("off", !S.screen.active);
    $("tileEyes").onclick = () => { showView("settings"); gotoSettingsPage("screen"); };

    $("valMemory").textContent = S.memoryEnabled ? "On" : "Off";
    $("tileMem").classList.toggle("on", S.memoryEnabled);
    $("tileMem").classList.toggle("off", !S.memoryEnabled);
    $("tileMem").onclick = () => { showView("settings"); gotoSettingsPage("memory"); };
  }

  $("contentClose").addEventListener("click", () => { $("contentCard").hidden = true; });
  $("reviewClose").addEventListener("click", () => { $("reviewCard").hidden = true; });
  $("activityClear").addEventListener("click", () => { $("activityList").textContent = ""; });

  function showContent(title, text) {
    $("contentTitle").textContent = title || "Content";
    const body = $("contentBody");
    body.textContent = "";
    if (/\n|[#*`|-]/.test(text)) window.mdRender(text, body);
    else body.textContent = text;
    $("contentCard").hidden = false;
    if (S.view !== "home") toast(title, "ok");
  }

  const SEV = { serious: "var(--red)", caution: "var(--amber)", note: "var(--text-med)" };
  function showReview(title, summary, findings, unclear) {
    $("reviewTitle").textContent = title || "Review";
    const body = $("reviewBody");
    body.textContent = "";
    if (summary) { const p = el("div", null, summary); p.style.marginBottom = "8px"; body.appendChild(p); }
    (findings || []).forEach(f => {
      const d = el("div");
      d.style.cssText = "border-left:2px solid " + (SEV[f.severity] || SEV.note) + ";padding:4px 10px;margin:6px 0;";
      const h = el("div"); h.style.fontWeight = "650"; h.textContent = f.heading || "";
      const t = el("div"); t.style.fontSize = "12px"; t.textContent = f.detail || "";
      d.appendChild(h); d.appendChild(t);
      if (f.suggestion) { const sg = el("div"); sg.className = "dim"; sg.style.fontSize = "11.5px"; sg.textContent = "→ " + f.suggestion; d.appendChild(sg); }
      body.appendChild(d);
    });
    (unclear || []).forEach(u => {
      const d = el("div", "dim"); d.style.fontSize = "11.5px"; d.textContent = "? " + u;
      body.appendChild(d);
    });
    $("reviewCard").hidden = false;
  }

  // ════════════════════════════════════════════════════════════════════════
  //  TOASTS
  // ════════════════════════════════════════════════════════════════════════
  function toast(text, kind) {
    const t = el("div", "toast" + (kind ? " t-" + kind : ""), text);
    $("toasts").appendChild(t);
    setTimeout(() => { t.classList.add("leaving"); setTimeout(() => t.remove(), 350); }, 3400);
  }

  // ════════════════════════════════════════════════════════════════════════
  //  CONFIRM BANNER
  // ════════════════════════════════════════════════════════════════════════
  function confirmAnswer(accepted) {
    $("confirmVeil").hidden = true;
    api.confirm_answer(accepted).catch(() => {});
  }
  $("confirmOk").addEventListener("click", () => confirmAnswer(true));
  $("confirmCancel").addEventListener("click", () => confirmAnswer(false));

  // ════════════════════════════════════════════════════════════════════════
  //  SETTINGS
  // ════════════════════════════════════════════════════════════════════════
  const PAGES = [
    ["general", "⚙", "General"], ["ai", "🧠", "AI & Models"], ["api", "🔑", "API Keys"],
    ["voice", "🎙", "Voice & Language"], ["persona", "🎭", "Personality"], ["memory", "🗂", "Memory"],
    ["training", "🎓", "Self-training"],
    ["screen", "👁", "Screen Awareness"], ["pc", "🖱", "PC Control"], ["integrations", "🧩", "Integrations"],
    ["steam", "🎮", "Steam"], ["appearance", "🎨", "Appearance"], ["startup", "🚀", "Startup & Background"],
    ["privacy", "🔒", "Privacy"], ["performance", "📈", "Performance"], ["advanced", "🛠", "Advanced"],
    ["about", "ℹ", "About"],
  ];

  (function buildNav() {
    const nav = $("settingsNav");
    PAGES.forEach(([key, ico, label]) => {
      const b = el("button");
      b.appendChild(el("span", "nav-ico", ico));
      const s = el("span", null, label);
      b.appendChild(s);
      b.addEventListener("click", () => gotoSettingsPage(key));
      b.dataset.page = key;
      nav.appendChild(b);
    });
  })();

  function gotoSettingsPage(key) {
    S.settingsPage = key;
    document.querySelectorAll("#settingsNav button").forEach(b =>
      b.classList.toggle("active", b.dataset.page === key));
    if (!S.settingsLoaded) return;
    renderSettingsPage(key);
  }

  async function loadSettings() {
    try {
      S.settingsLoaded = await api.get_settings();
    } catch (e) { S.settingsLoaded = {}; }
    renderSettingsPage(S.settingsPage);
  }

  function page(name, desc) {
    const wrap = el("div", "settings-page");
    wrap.appendChild(el("div", "settings-h", name));
    wrap.appendChild(el("div", "settings-desc", desc || ""));
    return wrap;
  }

  function card(name, desc, control) {
    const c = el("div", "set-card");
    const t = el("div", "set-text");
    t.appendChild(el("div", "set-name", name));
    if (desc) t.appendChild(el("div", "set-desc", desc));
    c.appendChild(t);
    if (control) c.appendChild(control);
    return c;
  }

  function cardCol(name, desc, inner) {
    const c = el("div", "set-card col");
    const t = el("div", "set-text");
    t.appendChild(el("div", "set-name", name));
    if (desc) t.appendChild(el("div", "set-desc", desc));
    c.appendChild(t);
    if (inner) { const w = el("div", "set-col"); w.style.marginTop = "10px"; w.appendChild(inner); c.appendChild(w); }
    return c;
  }

  // ── self-update card ────────────────────────────────────────────────────
  // Phase lives in S.updateState so the card survives settings re-renders;
  // the backend streams download progress through update_progress events.
  S.updateState = { phase: "idle", frac: 0, version: "" };
  let updProgressSink = null;   // wired to the live card while Advanced is open
  Bus.on("update_progress", ev => { if (updProgressSink) updProgressSink(ev); });

  function buildUpdateCard(d) {
    const us = S.updateState;
    if (us.phase === "idle" && d.update_pending) {
      us.phase = "ready";                       // resume a staged update
      us.version = d.update_pending_version || us.version || "";
    }

    const upd = el("div", "set-col");
    const urow = el("div", "set-row2 upd-head");
    const dot = el("span", "upd-dot");
    const ustat = el("span", "mono-val", "v" + (d.app_version || "?"));
    const ucheck = btnSm("Check for updates", runUpdateCheck);
    urow.appendChild(dot); urow.appendChild(ustat); urow.appendChild(ucheck);
    upd.appendChild(urow);

    // progress bar — fill width follows the real download, shimmer while active
    const bar = el("div", "upd-bar");
    const barFill = el("i");
    barFill.style.width = Math.round(us.frac * 100) + "%";
    bar.appendChild(barFill);
    bar.hidden = us.phase !== "downloading";
    upd.appendChild(bar);

    const usrc = el("input", "text-input");
    usrc.type = "url";
    usrc.placeholder = "https://your-server.example/jarvis/update.json";
    usrc.value = d.update_source || "";
    const srow = el("div", "set-row2");
    srow.appendChild(usrc);
    srow.appendChild(btnSm("Save source", () =>
      api.updater_set_source(usrc.value.trim()).then(r =>
        toast(r && r.ok ? "Update source saved" : (r && r.err) || "Failed",
              r && r.ok ? "ok" : "err"))));
    upd.appendChild(srow);

    let dloadBtn = null;
    const setDot = cls => { dot.className = "upd-dot" + (cls ? " " + cls : ""); };

    function showDownloadable(version, notes) {
      us.phase = "downloadable"; us.version = version || us.version || "";
      bar.hidden = true;
      setDot("update");
      ustat.textContent = "v" + us.version + " available" + (notes ? " — " + notes : "");
      if (!dloadBtn) { dloadBtn = el("button", "btn btn-sm", ""); upd.appendChild(dloadBtn); }
      dloadBtn.disabled = false;
      dloadBtn.className = "btn btn-sm";
      dloadBtn.textContent = "Download & install";
      dloadBtn.onclick = startDownload;
    }

    function showReady(version) {
      us.phase = "ready"; us.version = version || us.version || "";
      bar.hidden = true;
      setDot("ready");
      ustat.textContent = "v" + (us.version || "update") + " ready to install";
      if (!dloadBtn) { dloadBtn = el("button", "btn btn-sm", ""); upd.appendChild(dloadBtn); }
      dloadBtn.disabled = false;
      dloadBtn.className = "btn btn-sm upd-cta upd-pulse";
      dloadBtn.textContent = "Restart & install" + (us.version ? " v" + us.version : "");
      dloadBtn.onclick = installNow;
    }

    async function installNow() {
      dloadBtn.disabled = true;
      dloadBtn.classList.remove("upd-pulse");
      dloadBtn.textContent = "Installing…";
      toast("Installing update — JARVIS restarts automatically", "ok");
      await api.updater_apply().catch(() => {});
    }

    function startDownload() {
      us.phase = "downloading"; us.frac = 0;
      bar.hidden = false;
      barFill.style.width = "0%";
      setDot("downloading");
      dloadBtn.disabled = true;
      dloadBtn.textContent = "Downloading… 0%";
      api.updater_download().catch(e => {
        us.phase = "error";
        bar.hidden = true;
        setDot("err");
        toast(String(e && e.message || e), "err");
        if (dloadBtn) { dloadBtn.disabled = false; dloadBtn.textContent = "Download & install"; }
      });
    }

    async function runUpdateCheck() {
      if (us.phase === "downloading" || us.phase === "ready") return;
      ucheck.disabled = true;
      setDot("checking");
      if (us.phase === "idle") ustat.textContent = "checking…";
      try {
        const r = await api.updater_check().catch(e => ({ ok: false, err: String(e) }));
        if (!r || !r.ok) {
          if (us.phase === "idle") { setDot(""); ustat.textContent = (r && r.err) || "check failed"; }
          return;
        }
        if (r.update_available) showDownloadable(r.latest, r.notes);
        else {
          us.phase = "idle";
          setDot("");
          ustat.textContent = "v" + r.current + " — up to date";
          if (dloadBtn) { dloadBtn.remove(); dloadBtn = null; }
        }
      } finally { ucheck.disabled = false; }
    }

    // live progress from the backend's download thread
    updProgressSink = ev => {
      if (ev.stage === "download") {
        us.frac = ev.frac;
        barFill.style.width = Math.round(ev.frac * 100) + "%";
        if (dloadBtn) dloadBtn.textContent = "Downloading… " + Math.round(ev.frac * 100) + "%";
      } else if (ev.stage === "done") {
        if (ev.ok) {
          showReady(ev.version);
          toast("Update v" + (ev.version || "") + " downloaded — restart to install", "ok");
        } else {
          us.phase = "error";
          bar.hidden = true;
          setDot("err");
          toast(ev.err || "Download failed", "err");
          if (dloadBtn) {
            dloadBtn.disabled = false;
            dloadBtn.textContent = "Download & install";
            dloadBtn.onclick = startDownload;
          }
        }
      }
    };

    // repaint from the phase we arrived with (page re-entered mid-flow)
    if (us.phase === "downloadable") showDownloadable(us.version, "");
    else if (us.phase === "ready") showReady(us.version);
    else if (us.phase === "downloading") {
      setDot("downloading");
      dloadBtn = el("button", "btn btn-sm", "Downloading… " + Math.round(us.frac * 100) + "%");
      dloadBtn.disabled = true;
      upd.appendChild(dloadBtn);
    }

    // freshen staged state on every visit — the stored settings may predate
    // a download that finished while another page was open
    api.updater_status().then(st => {
      if (st && st.pending && (us.phase === "idle" || us.phase === "downloadable"))
        showReady(st.pending_version);
    }).catch(() => {});

    return upd;
  }

  // Quiet update checks: once a minute after boot, then every 5 min —
  // never blocks anything, toasts only once per new version.
  let _lastUpdateToast = "";
  async function silentUpdateCheck() {
    try {
      const r = await api.updater_check();
      if (!r || !r.ok || !r.update_available) return;
      if (_lastUpdateToast !== r.latest) {
        _lastUpdateToast = r.latest;
        toast("JARVIS update v" + (r.latest || "") + " available — ⚙ Advanced → Updates", "ok");
      }
      if (S.updateState.phase === "idle") {
        S.updateState.phase = "downloadable";
        S.updateState.version = r.latest || "";
        if (S.settingsLoaded && S.settingsPage === "advanced") renderSettingsPage("advanced");
      }
    } catch (e) { /* offline — the next cycle retries */ }
  }
  setTimeout(() => { silentUpdateCheck(); setInterval(silentUpdateCheck, 5 * 60 * 1000); }, 60000);

  // ════════════════════════════════════════════════════════════════════════
  //  AUTONOMY RAIL — the six switches that decide what JARVIS may do
  //
  //  The rail is the answer to "what is it allowed to touch right now?" — it
  //  is always on screen, it reflects the real backend state (pushed, not
  //  guessed), and turning a lever off is a brake rather than a setting: the
  //  backend cancels whatever step is running the moment it lands.
  // ════════════════════════════════════════════════════════════════════════
  const MODES = [
    ["pc_control",       "🖱", "PC Control",  "mouse + keyboard"],
    ["autonomous",       "✋", "Autonomous",  "acts unasked"],
    ["screen_awareness", "👁", "Sight",       "can see the screen"],
    ["proactive",        "💬", "Proactive",   "may speak first"],
    ["discord",          "🎧", "Discord",     "may use Discord"],
    ["voice",            "🎙", "Voice",       "mic is live"],
  ];

  S.pc = { modes: {}, feed: [], state: {} };

  function buildRail() {
    const wrap = $("autoSwitches");
    wrap.textContent = "";
    MODES.forEach(([key, ico, label]) => {
      const b = el("button", "auto-sw" + (key === "autonomous" ? " sw-hot" : ""));
      b.dataset.mode = key;
      b.dataset.on = "0";
      b.title = label;
      b.appendChild(el("span", "sw-dot"));
      b.appendChild(el("span", "sw-ico", ico));
      b.appendChild(el("span", "sw-name", label));
      b.appendChild(el("span", "sw-state", "—"));
      b.addEventListener("click", () => toggleMode(key));
      wrap.appendChild(b);
    });
  }

  function toggleMode(key) {
    const on = !!(S.pc.modes || {})[key];
    // Optimistic paint: the switch must feel instant even though the real
    // state round-trips through the backend.
    S.pc.modes[key] = !on;
    paintRail();
    save(key, !on, true).catch(() => {});
    if (key === "autonomous") {
      toast(!on ? "Autonomous mode ON — JARVIS may use this PC"
                : "Autonomous mode OFF — hands off", !on ? "ok" : undefined);
    }
  }

  function paintRail() {
    const modes = S.pc.modes || {};
    document.querySelectorAll(".auto-sw").forEach(b => {
      const key = b.dataset.mode;
      const on = !!modes[key];
      b.dataset.on = on ? "1" : "0";
      const st = b.querySelector(".sw-state");
      if (st) st.textContent = on ? "ON" : "OFF";
    });
    const auto = !!modes.autonomous;
    document.body.dataset.autopilot = auto ? "1" : "0";   // hooks the HUD readout CSS
    const hudAuto = $("hudAuto");
    if (hudAuto) hudAuto.innerHTML = "AUTO · <b>" + (auto ? "ENGAGED" : "OFF") + "</b>";
    const chip = $("chipAuto");
    chip.dataset.on = auto ? "1" : "0";
    chip.title = auto
      ? "Autonomous mode is ON — click to take control back"
      : "Autonomous mode — let JARVIS use this PC on its own";
    chip.lastChild.textContent = auto ? "AUTOPILOT ON" : "AUTOPILOT OFF";
    $("autoRail").classList.toggle("mode-auto", auto);
    const ho = $("btnHandover");
    ho.dataset.on = auto ? "1" : "0";
    $("hoLabel").textContent = auto ? "STOP" : "TAKE OVER";
    $("autoRailSub").textContent = !modes.pc_control
      ? "Hands off — JARVIS cannot use the mouse"
      : (auto ? "Using your PC — press STOP for hands off"
              : "Watching, not touching");
  }

  function askHandover() {
    const auto = !!(S.pc.modes || {}).autonomous;
    if (!S.pc.modes.pc_control) {
      toast("Turn PC Control on first", "err");
      return;
    }
    if (auto) return quick("Stop taking over my PC.");
    quick("Take over my PC. Use it for a while and do something useful.");
  }

  function quick(phrase) {
    return api.quick_action(phrase)
      .then(() => toast("Sent: " + phrase, "ok"))
      .catch(() => toast("Backend not reachable", "err"));
  }

  // ── control feed ──────────────────────────────────────────────────────
  const FEED_ICON = { ok: "✓", fail: "✗", mode: "◉", info: "·", act: "▸",
                      discord: "🎧" };

  function feedRow(entry) {
    const row = el("div", "pc-row k-" + (entry.kind || "info"));
    const t = entry.at ? new Date(entry.at * 1000) : new Date();
    row.appendChild(el("span", "pc-time",
      String(t.getHours()).padStart(2, "0") + ":" +
      String(t.getMinutes()).padStart(2, "0") + ":" +
      String(t.getSeconds()).padStart(2, "0")));
    row.appendChild(el("span", "pc-ico", FEED_ICON[entry.kind] || "·"));
    row.appendChild(el("span", "pc-text", entry.text || ""));
    return row;
  }

  function addFeed(entry) {
    const body = $("pcFeed");
    const empty = $("pcEmpty");
    if (empty) empty.remove();
    body.appendChild(feedRow(entry));
    while (body.children.length > 60) body.firstChild.remove();
    body.scrollTop = body.scrollHeight;
    // A working assistant should look like it is working.
    if (entry.kind && entry.kind !== "info") {
      const live = $("pcLive");
      live.classList.add("on");
      clearTimeout(addFeed._t);
      addFeed._t = setTimeout(() => live.classList.remove("on"), 2600);
    }
  }

  function setFeed(rows) {
    const body = $("pcFeed");
    body.textContent = "";
    if (!rows || !rows.length) {
      body.appendChild(el("div", "pc-empty", "Nothing yet."));
      return;
    }
    rows.forEach(r => body.appendChild(feedRow(r)));
    body.scrollTop = body.scrollHeight;
  }

  function paintPcFoot(state) {
    if (!state) return;
    const bits = [];
    if (state.foreground) bits.push("<b>" + esc(state.foreground) + "</b>");
    if (state.monitors) bits.push(state.monitors + (state.monitors > 1 ? " monitors" : " monitor"));
    if (state.desktop) bits.push(esc(state.desktop));
    bits.push(state.uia ? "accessibility tree" : "vision only");
    const foot = $("pcFoot");
    foot.innerHTML = bits.join(" · ");
    foot.hidden = false;
  }

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  async function loadPc() {
    try {
      const st = await api.pc_status();
      if (!st) return;
      S.pc.modes = st.modes || S.pc.modes;
      S.pc.state = st.state || {};
      paintRail();
      setFeed(st.feed || []);
      paintPcFoot(S.pc.state);
    } catch (e) { /* headless or old backend */ }
  }

  // ════════════════════════════════════════════════════════════════════════
  //  SELF-TRAINING — what it practises while nobody is talking
  //
  //  Three questions, in this order: is it working right now, what is it
  //  working on, and is that work paying off. Every number is real — the bars
  //  are win rates out of the competency ledger in core/self_training.py, fed
  //  by the control log itself, and the rules are written by JARVIS (the card
  //  says "self-written" out loud because that is what they are).
  // ════════════════════════════════════════════════════════════════════════
  S.train = {
    enabled: true, active: true, intensity: "balanced", memory_enabled: true,
    running: false, cycles: 0, learned: 0, drill_count: 0, focus_label: "",
    rounds_left: 0, rounds_per_hour: 2, last_ts: 0,
    competency: [], drills: [], self_scored: true,
  };
  let trainManual = false;

  function agoText(ts) {
    if (!ts) return "never";
    const s = Math.max(0, Date.now() / 1000 - ts);
    if (s < 90) return Math.round(s) + "s ago";
    if (s < 5400) return Math.round(s / 60) + "m ago";
    if (s < 172800) return Math.round(s / 3600) + "h ago";
    return Math.round(s / 86400) + "d ago";
  }

  function paintTrain() {
    const card = $("trainCard");
    if (!card) return;
    const t = S.train;
    card.dataset.busy = t.running ? "1" : "0";
    document.body.dataset.training = t.running ? "1" : "0";

    const live = $("trainLive");
    if (live) {
      if (t.running) { live.dataset.on = "2"; live.lastChild.textContent = "PRACTISING"; }
      else if (!t.active) { live.dataset.on = "3"; live.lastChild.textContent = "OFF"; }
      else if (t.cycles) { live.dataset.on = "1"; live.lastChild.textContent = "IDLE"; }
      else { live.dataset.on = "0"; live.lastChild.textContent = "READY"; }
    }

    const focus = $("trainFocus");
    if (focus) {
      if (t.running) {
        focus.innerHTML = "Practising now — one round on its weakest step, then it tells me what it learned.";
      } else if (!t.active) {
        focus.innerHTML = t.memory_enabled === false
          ? "Off — memory is switched off, so nothing may be learned on its own."
          : "Off. It only learns while you are talking to it.";
      } else if (t.focus_label) {
        focus.innerHTML = "Last round drilled <b>" + esc(t.focus_label) +
          "</b> — the step with the worst real record.";
      } else {
        focus.innerHTML = "Watching how its own clicks, keys and reads actually land, then drilling the weakest one while you are quiet.";
      }
    }

    // Competency bars: weakest first, with the trend so a bar that is stuck low
    // can be told apart from one that is climbing.
    const bars = $("trainBars");
    if (bars) {
      bars.textContent = "";
      const rows = (t.competency || []).slice(0, 4);
      if (!rows.length) {
        bars.appendChild(el("div", "train-focus dim",
          "No evidence yet — it starts keeping score from the first click it makes for you."));
      }
      rows.forEach(r => {
        const row = el("div", "train-bar" +
          (r.score < 0.6 ? " poor" : r.score < 0.8 ? " weak" : ""));
        const name = el("span", "train-bar-name", r.label || r.capability);
        const track = el("div", "train-bar-track");
        const fill = el("i", "train-bar-fill");
        fill.style.width = Math.round((r.score || 0) * 100) + "%";
        track.appendChild(fill);
        const val = el("span", "train-bar-val");
        let trend = "";
        if (r.trend > 0.02) trend = " <span class=\"up\">▲" + Math.round(r.trend * 100) + "</span>";
        else if (r.trend < -0.02) trend = " <span class=\"down\">▼" + Math.round(Math.abs(r.trend) * 100) + "</span>";
        val.innerHTML = Math.round((r.score || 0) * 100) + "% · " +
          (r.wins || 0) + "/" + (r.attempts || 0) + trend;
        row.appendChild(name); row.appendChild(track); row.appendChild(val);
        bars.appendChild(row);
      });
    }

    // Newest rule it wrote for itself.
    const box = $("trainNew");
    if (box) {
      const d = (t.drills || [])[0];
      if (d && d.rule) {
        box.textContent = d.rule + (d.situation ? "  (when: " + d.situation + ")" : "");
        box.hidden = false;
      } else {
        box.hidden = true;
      }
    }

    const foot = $("trainFoot");
    if (foot) {
      foot.innerHTML = "<b>" + (t.cycles || 0) + "</b> round" +
        ((t.cycles || 0) === 1 ? "" : "s") + " · <b>" + (t.learned || 0) +
        "</b> rules written · <b>" + (t.drill_count || 0) + "</b> in playbook · <b>" + (t.rounds_left || 0) + "/" +
        (t.rounds_per_hour || 2) + "</b> left this hour · last " + agoText(t.last_ts);
    }
  }

  async function loadTrain() {
    try {
      const st = await api.training_get();
      if (st && typeof st === "object") S.train = Object.assign(S.train, st);
    } catch (e) { /* older backend, or the bridge is not up yet */ }
    paintTrain();
  }

  function trainRun() {
    trainManual = true;
    S.train.running = true;
    paintTrain();
    api.training_run(true).then(r => {
      if (r && r.ok === false) {
        S.train.running = false; trainManual = false; paintTrain();
        toast(r.err || "A round is already running", "err");
      }
    }).catch(() => { S.train.running = false; paintTrain(); });
  }

  Bus.on("training", ev => {
    if (!ev || typeof ev !== "object") return;
    if (ev.phase === "running") {
      S.train.running = true;
      paintTrain();
      return;
    }
    if (ev.state && typeof ev.state === "object") {
      S.train = Object.assign(S.train, ev.state, { running: false });
    } else {
      S.train.running = false;
    }
    // The pushed snapshot is the truth for these two levers; keep the settings
    // page's copy of them in step so the switch and the card cannot disagree.
    const st = ev.state;
    if (st && S.settingsLoaded && typeof S.settingsLoaded === "object") {
      if (typeof st.enabled === "boolean") S.settingsLoaded.self_training = st.enabled;
      if (st.intensity) S.settingsLoaded.training_intensity = st.intensity;
    }
    paintTrain();
    if (S.settingsLoaded && S.settingsPage === "training") renderSettingsPage("training");

    const r = ev.report || {};
    if (ev.phase === "done") {
      if (r.learned) {
        toast("Learned " + r.learned + " new rule" + (r.learned > 1 ? "s" : "") +
              " while you were quiet — ⚙ Self-training", "ok");
      } else if (trainManual) {
        toast(r.ok === false
          ? "Training: " + (r.reason === "memory-off" ? "memory is off" : (r.err || r.reason || "nothing to do"))
          : "Training round done — nothing new to learn", "");
      }
      trainManual = false;
    }
  });

  function toggleEl(get, set) {
    const t = el("button", "toggle" + (get() ? " on" : ""));
    t.setAttribute("aria-pressed", get());
    t.addEventListener("click", async () => {
      t.classList.toggle("on");
      t.setAttribute("aria-pressed", t.classList.contains("on"));
      await set(t.classList.contains("on"));
    });
    return t;
  }

  function pillsEl(options, get, set) {
    const w = el("div", "pills");
    options.forEach(([val, label]) => {
      const p = el("button", "pill" + (get() === val ? " sel" : ""), label);
      p.addEventListener("click", async () => {
        w.querySelectorAll(".pill").forEach(x => x.classList.remove("sel"));
        p.classList.add("sel");
        await set(val);
      });
      w.appendChild(p);
    });
    return w;
  }

  function save(key, value, quiet) {
    return api.save_setting(key, value).then(r => {
      if (!quiet) toast("Saved", "ok");
      // Keep the local settings snapshot in step with what was just written.
      // It used to be refetched only when Settings was first opened, so a page
      // re-rendered a moment later (a pushed event, or leaving and coming back)
      // redrew the OLD value — a switch you had just turned off came back on,
      // and clicking that stale switch saved the opposite of what you meant.
      if (S.settingsLoaded && typeof S.settingsLoaded === "object") {
        S.settingsLoaded[key] = value;
        if (key.indexOf("avatar_") === 0) loadSettings();   // nested on purpose
        else if ($("view-settings").classList.contains("active")) {
          // Redraw the open page from the value we just wrote, so the control
          // on screen and the stored setting can never drift apart. A page that
          // was re-rendered from a stale snapshot used to show the opposite of
          // the truth, and the next click then saved the opposite too.
          renderSettingsPage(S.settingsPage);
        }
      }
      if (r && r.refresh) loadSettings();
      return r;
    }).catch(() => toast("Could not save", "err"));
  }

  function renderSettingsPage(key) {
    const body = $("settingsBody");
    body.textContent = "";
    const d = S.settingsLoaded || {};
    const P = renderers[key];
    body.appendChild(P ? P(d) : el("div", "settings-placeholder", "Nothing here yet"));
    body.scrollTop = 0;
  }

  const renderers = {

    general(d) {
      const p = page("General", "Identity and everyday behaviour.");
      const names = el("div");
      const asst = el("input", "text-input"); asst.value = d.assistant_name || "JARVIS";
      const user = el("input", "text-input"); user.placeholder = "Sir"; user.value = d.user_name || "";
      const row = el("div", "set-col");
      const f1 = el("div"); f1.appendChild(el("div", "field-label", "Assistant name")); f1.appendChild(asst);
      const f2 = el("div"); f2.appendChild(el("div", "field-label", "Your name")); f2.appendChild(user);
      row.appendChild(f1); row.appendChild(f2);
      const b = el("button", "btn", "Save names"); b.style.marginTop = "10px";
      b.addEventListener("click", () => save("assistant_name", asst.value.trim() || "JARVIS", true)
        .then(() => save("user_name", user.value.trim(), true)));
      row.appendChild(b);
      p.appendChild(cardCol("Identity", "How JARVIS addresses you, everywhere.", row));
      p.appendChild(card("Morning briefing", "A short news & schedule briefing on first connect.",
        toggleEl(() => d.brief, v => save("brief", v))));
      p.appendChild(card("Currency", "For prices, flights and shopping answers.", (() => {
        const s = el("select", "text-input");
        ["USD", "EUR", "GBP", "TRY", "INR", "JPY"].forEach(c => {
          const o = el("option", null, c); o.value = c; s.appendChild(o);
        });
        s.value = d.currency || "USD";
        s.addEventListener("change", () => save("currency", s.value));
        return s;
      })()));
      p.appendChild(card("Desktop shortcut", "Put a quick-launch icon on your desktop.",
        btnSm("Create", () => api.create_shortcut().then(() => toast("Shortcut created", "ok")))));
      return p;
    },

    ai(d) {
      const p = page("AI & Models", "Reasoning depth, autonomy and provider fallback.");
      p.appendChild(card("Deep thinking", "Extra reasoning step for hard questions.",
        toggleEl(() => d.thinking, v => save("thinking", v))));
      p.appendChild(card("Goal agent", "One sentence → JARVIS plans and completes multi-step tasks.",
        toggleEl(() => d.goal_agent, v => save("goal_agent", v))));
      p.appendChild(card("Verify clicks", "Ask before clicking anything on your PC.",
        toggleEl(() => d.verify_clicks, v => save("verify_clicks", v))));
      p.appendChild(card("Media resolution", "Vision detail sent to the model.",
        pillsEl([["low", "LOW"], ["medium", "MEDIUM"], ["high", "HIGH"]],
          () => d.media_resolution, v => save("media_resolution", v))));
      const n = el("div", "note");
      n.textContent = "Providers run local → free → paid automatically: if Gemini quota runs out, configured free providers (Groq, Cerebras, OpenRouter, Hugging Face…) take over. Configure them under API Keys.";
      p.appendChild(n);
      return p;
    },

    api(d) {
      const p = page("API Keys", "Every provider has a key field, a test button and live status. Tests run in the background — the interface never freezes.");
      const wrap = el("div");
      wrap.id = "apiWrap";
      p.appendChild(wrap);
      loadApiKeys(wrap);
      return p;
    },

    voice(d) {
      const p = page("Voice & Language", "Voice, devices and speech cadence.");
      const inner = el("div", "set-col");
      const sel = el("select", "text-input");
      ["Aoede", "Kore", "Leda", "Zephyr", "Puck", "Charon", "Fenrir"].forEach(v => {
        const o = el("option", null, v); o.value = v; sel.appendChild(o);
      });
      sel.value = d.voice || "Aoede";
      const row = el("div", "set-row2");
      row.appendChild(sel);
      row.appendChild(btnSm("Apply voice", () => save("voice", sel.value)));
      inner.appendChild(row);
      p.appendChild(cardCol("Assistant voice", "Changing voice rebuilds the session (context is kept per design).", inner));

      p.appendChild(card("Talk cadence", "How JARVIS phrases things.",
        pillsEl([["standard", "STANDARD"], ["warm", "WARM"], ["live", "LIVE"], ["chatty", "CHATTY"]],
          () => d.talk_cadence, v => save("talk_cadence", v))));

      const dev = el("div", "set-col");
      const inSel = el("select", "text-input"), outSel = el("select", "text-input");
      const inRow = el("div", "set-row2"), outRow = el("div", "set-row2");
      Promise.all([api.devices_get(), Promise.resolve(d)]).then(([dv]) => {
        fillDeviceSel(inSel, dv.input, d.input_device, "System default");
        fillDeviceSel(outSel, dv.output, d.output_device, "System default");
      });
      inRow.appendChild(inSel);
      inRow.appendChild(btnSm("Set", () => save("input_device", inSel.value)));
      outRow.appendChild(outSel);
      outRow.appendChild(btnSm("Set", () => save("output_device", outSel.value)));
      dev.appendChild(inRow); dev.appendChild(outRow);
      p.appendChild(cardCol("Microphone & speakers", "Picking by name survives replugs (device indices shift).", dev));

      const lang = el("div", "note");
      lang.textContent = "Language: " + (d.language_mode || "auto") + " — JARVIS detects your language on first use and adapts.";
      p.appendChild(lang);
      return p;
    },

    persona(d) {
      const p = page("Personality", "Warmth, humour and emotional range.");
      p.appendChild(card("Humour", "",
        pillsEl([["off", "OFF"], ["subtle", "SUBTLE"], ["playful", "PLAYFUL"], ["chaotic", "CHAOTIC"]],
          () => d.humor, v => save("humor", v))));
      p.appendChild(card("Emotion depth", "How much JARVIS reacts to how you sound.",
        pillsEl([["off", "OFF"], ["light", "LIGHT"], ["full", "FULL"]],
          () => d.emotion_depth, v => save("emotion_depth", v))));
      p.appendChild(card("Narrate actions", "Explain out loud what JARVIS is doing.",
        toggleEl(() => d.narrate_actions, v => save("narrate_actions", v))));
      return p;
    },

    memory(d) {
      const p = page("Memory", "What JARVIS keeps about you, and control over it.");
      p.appendChild(card("Persistent memory", "Session summaries, facts and lessons written to long-term memory.",
        toggleEl(() => d.memory_enabled, v => save("memory_enabled", v))));
      p.appendChild(card("Activity timeline", "Short local timeline of what you've been doing.",
        toggleEl(() => d.track_activity, v => save("track_activity", v))));
      const browse = el("div");
      p.appendChild(cardCol("Memory browser", "Everything stored — delete anything in one click.", browse));
      api.memory_get().then(mem => {
        if (!mem || !Object.keys(mem).length) {
          browse.appendChild(el("div", "dim", "Nothing stored yet."));
          return;
        }
        Object.keys(mem).forEach(cat => {
          const entries = mem[cat] || {};
          const keys = Object.keys(entries);
          if (!keys.length) return;
          browse.appendChild(el("div", "mem-cat-h", cat));
          keys.forEach(k => {
            const row = el("div", "mem-entry");
            const txt = typeof entries[k] === "string" ? entries[k] : JSON.stringify(entries[k]);
            row.appendChild(el("span", null, k + " — " + txt));
            const x = el("button", "xbtn", "✕");
            x.title = "Forget";
            x.addEventListener("click", () => api.memory_forget(cat, k).then(() => row.remove()));
            row.appendChild(x);
            browse.appendChild(row);
          });
        });
      }).catch(() => browse.appendChild(el("div", "dim", "Memory unavailable.")));
      return p;
    },

    training(d) {
      const p = page("Self-training",
        "JARVIS practises on its own while you are quiet, and writes itself rules from its own mistakes.");

      p.appendChild(card("Self-training",
        "Watches how its own clicks, keys and reads land, then spends one model call \u2014 while you "
        + "are not talking \u2014 on the step with the worst real record. Off means nothing is learned "
        + "while idle; it stops by itself when memory is switched off.",
        toggleEl(() => d.self_training !== false, v => save("self_training", v))));

      p.appendChild(card("How hard it practises",
        "Gentle: one round an hour \u00b7 Balanced: two \u00b7 Focused: five. Rounds only ever run "
        + "after you have been quiet, and never during a task.",
        pillsEl([["gentle", "GENTLE"], ["balanced", "BALANCED"], ["focused", "FOCUSED"]],
          () => d.training_intensity || "balanced", v => save("training_intensity", v))));

      // live numbers, refreshed from the same snapshot the Home card uses
      const stats = el("div", "train-stats");
      p.appendChild(cardCol("What it has done so far",
        "Rounds are its own practice sessions; rules are the playbook entries they produced.", stats));
      const rules = el("div");
      rules.style.marginTop = "8px";
      p.appendChild(cardCol("Its playbook",
        "Rules JARVIS wrote for itself. Nothing here was written by you, and any of them can go.", rules));

      const actions = el("div", "set-col");
      const row = el("div", "set-row2");
      row.appendChild(btnSm("Practise now", trainRun));
      row.appendChild(btnSm("Forget everything it taught itself", () => {
        api.training_reset().then(() => { loadTrain(); toast("Playbook cleared", "ok"); })
          .catch(() => toast("Could not clear", "err"));
      }));
      actions.appendChild(row);
      p.appendChild(cardCol("Take it back",
        "Reversible, and logged: every rule is stored as ordinary memory, so forgetting one is the "
        + "same as forgetting anything else.", actions));

      const note = el("div", "note");
      note.textContent = "What it can and cannot do: it can only change what JARVIS believes \u2014 its "
        + "own playbook. It cannot touch settings, keys, permissions or files, it never drives the "
        + "mouse or keyboard by itself, and the rules are written and graded by the model, which is "
        + "why they are labelled self-written. Reversible at any time on this page.";
      p.appendChild(note);

      const paint = st => {
        if (!st) return;
        S.train = Object.assign(S.train, st);
        paintTrain();
        stats.textContent = "";
        const tiles = [
          [st.cycles || 0, "rounds"],
          [st.learned || 0, "rules written"],
          [st.drill_count || 0, "in playbook"],
          [(st.rounds_left || 0) + "/" + (st.rounds_per_hour || 2), "left this hour"],
        ];
        tiles.forEach(([val, name]) => {
          const tile = el("div", "train-stat");
          tile.appendChild(el("div", "train-stat-val", String(val)));
          tile.appendChild(el("div", "train-stat-name", name));
          stats.appendChild(tile);
        });
        if (st.focus_label) {
          const f = el("div", "dim");
          f.style.cssText = "font-size:11.5px;margin-top:8px";
          f.textContent = "Last practised: " + st.focus_label + " \u00b7 " + agoText(st.last_ts);
          stats.appendChild(f);
        }

        rules.textContent = "";
        const list = st.drills || [];
        if (!list.length) {
          rules.appendChild(el("div", "dim",
            "Nothing yet \u2014 the first rules appear after a round finds something new to fix."));
        }
        list.forEach(dr => {
          const line = el("div", "train-rule");
          const body = el("div", "train-rule-body");
          body.appendChild(el("span", "train-rule-cap", dr.label || dr.capability || ""));
          body.appendChild(el("span", null, dr.rule || ""));
          if (dr.situation) body.appendChild(el("span", "train-rule-when", "when: " + dr.situation));
          line.appendChild(body);
          const x = el("button", "xbtn", "✕");
          x.title = "Forget this rule";
          x.addEventListener("click", () => api.training_forget(dr.id)
            .then(() => { line.remove(); loadTrain(); toast("Forgotten", "ok"); })
            .catch(() => toast("Could not forget", "err")));
          line.appendChild(x);
          rules.appendChild(line);
        });
      };
      paint(S.train);
      api.training_get().then(paint).catch(() => {});
      return p;
    },

    screen(d) {
      const p = page("Screen Awareness", "Let JARVIS see the active window. Everything stays on this machine.");
      p.appendChild(card("Screen share", "Off = observer off and captures refused.",
        toggleEl(() => d.screen_awareness, v => save("screen_awareness", v))));
      p.appendChild(card("Auto glance captions", "One-line vision look during check-ins.",
        toggleEl(() => d.screen_glance, v => save("screen_glance", v))));
      p.appendChild(card("Picture quality", "LIGHT polls rarely · HIGH every minute. A static screen costs nothing.",
        pillsEl([["light", "LIGHT"], ["medium", "MEDIUM"], ["high", "HIGH"]],
          () => d.screen_share_quality, v => save("screen_share_quality", v))));
      p.appendChild(card("Observe interval", "Seconds between activity samples.", (() => {
        const s = el("select", "text-input");
        [5, 8, 12, 20, 30].forEach(v => { const o = el("option", null, v + " s"); o.value = v; s.appendChild(o); });
        s.value = String(d.observe_interval || 12);
        s.style.maxWidth = "120px";
        s.addEventListener("change", () => save("observe_interval", parseInt(s.value, 10)));
        return s;
      })()));
      return p;
    },

    pc(d) {
      const p = page("PC Control", "What JARVIS may do with this machine, and how it proves it did it.");

      // The levers first, because they are the ones people actually flip.
      const modes = S.pc.modes || {};
      MODES.forEach(([key, ico, label, sub]) => {
        p.appendChild(card(label, sub.charAt(0).toUpperCase() + sub.slice(1) + ".",
          toggleEl(() => !!modes[key], v => {
            S.pc.modes[key] = v;
            paintRail();
            return save(key, v, true);
          })));
      });

      p.appendChild(cardCol("Try it",
        "Hand the machine over, or send one instruction and watch the control feed on Home.", (() => {
          const row = el("div", "set-col");
          const r1 = el("div", "set-row2");
          r1.appendChild(btnSm("Take over my PC", askHandover));
          r1.appendChild(btnSm("Where is the pointer?",
            () => quick("Move the mouse to the middle of the screen and tell me where it landed.")));
          r1.appendChild(btnSm("What can you see?",
            () => quick("Look at my screen and tell me what I am doing.")));
          row.appendChild(r1);
          const r2 = el("div", "set-row2");
          r2.appendChild(btnSm("Test Discord (no send)",
            () => quick("Open Discord and read me the conversation on screen.")));
          row.appendChild(r2);
          return row;
        })()));

      p.appendChild(card("Verify every action",
        "After each click or type, look at the screen again and check it actually took. "
        + "This is what stops JARVIS saying \u201cdone\u201d when nothing happened — it costs "
        + "one quick look per action, not a question.",
        toggleEl(() => d.verify_clicks, v => save("verify_clicks", v))));
      p.appendChild(card("Goal agent",
        "Plan and execute a longer goal end to end. Risky steps still wait for you.",
        toggleEl(() => d.goal_agent, v => save("goal_agent", v))));
      p.appendChild(card("Goal agent auto-run",
        "Let the agent finish without asking between steps.",
        toggleEl(() => d.goal_agent_auto, v => save("goal_agent_auto", v))));
      p.appendChild(card("Start with Windows", "Launch JARVIS when you log in.",
        toggleEl(() => d.autostart, v => save("autostart", v))));

      const note = el("div", "note");
      note.textContent = "What always waits for you, even in autonomous mode: money, "
        + "permanent deletion, shutdown or restart, passwords and security settings, "
        + "installing software, and any message JARVIS decided to send on its own. "
        + "Everything else it will just do. Press STOP on the Home rail for hands off — "
        + "it stops mid-step.";
      p.appendChild(note);
      return p;
    },

    integrations(d) {
      const p = page("Integrations", "Plugins and the remote dashboard.");
      const list = el("div");
      p.appendChild(cardCol("Plugins", "Drop a .py file into the plugins folder and restart.", (() => {
        const w = el("div", "set-col");
        w.appendChild(list);
        const row = el("div", "set-row2");
        row.appendChild(btnSm("Open plugins folder", () => api.open_plugins_folder()));
        w.appendChild(row);
        return w;
      })()));
      api.plugins_get().then(pls => {
        if (!pls.length) { list.appendChild(el("div", "dim", "No plugins found.")); return; }
        pls.forEach(pl => {
          const row = el("div", "plugin-row" + (pl.valid ? "" : " invalid"));
          const left = el("div");
          left.style.flex = "1";
          left.appendChild(el("div", "plugin-name", pl.name));
          left.appendChild(el("div", pl.valid ? "plugin-desc" : "plugin-err", pl.valid ? (pl.description || "") : (pl.error || "Invalid plugin")));
          row.appendChild(left);
          if (pl.valid) {
            row.appendChild(toggleEl(() => !!pl.enabled, v => api.plugin_set_enabled(pl.name, v)));
          }
          list.appendChild(row);
        });
      }).catch(() => list.appendChild(el("div", "dim", "Plugins unavailable.")));

      p.appendChild(card("Remote dashboard", "Control JARVIS from your phone — QR pairing.",
        btnSm("Pair phone", pairRemote)));
      return p;
    },

    steam(d) {
      const p = page("Steam", "JARVIS drives the Steam client itself — not the web.");
      const n = el("div", "note");
      n.textContent = "Say things like: “Search Hades on Steam”, “Is Elden Ring cheaper anywhere?”, “Install Celeste”, “Open my Steam library”. JARVIS opens the Steam app, searches, and waits for your confirmation before downloads or purchases.";
      p.appendChild(n);
      const row = el("div", "set-col");
      const b = el("button", "btn", "Open Steam library");
      b.style.alignSelf = "flex-start";
      b.addEventListener("click", () => api.quick_action("Open my Steam library."));
      row.appendChild(b);
      p.appendChild(cardCol("Try it", "", row));
      p.appendChild(card("Confirm purchases",
        "Installs and buys always wait for your on-screen confirmation.",
        toggleEl(() => d.goal_agent_auto, v => save("goal_agent_auto", v))));
      return p;
    },

    appearance(d) {
      const p = page("Appearance", "Colour, layout and the assistant's face.");
      // accent picker
      const row = el("div", "set-col");
      const colors = ["#38bdf8", "#31d9ae", "#8b6df5", "#f5b45c", "#ff5c7a", "#94a3b8"];
      const pw = el("div", "pills");
      colors.forEach(c => {
        const s = el("button", "pill");
        s.style.cssText = "width:26px;height:26px;border-radius:50%;background:" + c + ";border-color:" + (d.accent === c ? c : "var(--border)") + ";";
        s.title = c;
        s.addEventListener("click", () => {
          applyAccent(c);
          save("accent", c, true);
          pw.querySelectorAll(".pill").forEach(x => x.style.borderColor = "var(--border)");
          s.style.borderColor = c;
        });
        pw.appendChild(s);
      });
      row.appendChild(pw);
      p.appendChild(cardCol("Accent colour", "Recolours the entire interface.", row));

      // ── AI Avatar Mode ────────────────────────────────────────────────
      const AVATAR_HINT = {
        classic: "The classic JARVIS face — eyes, brows and a mouth that moves with the voice.",
        minimal: "One calm core and one soft ring. The lightest thing that still feels alive.",
        orbit: "Satellites circling a pulsing sun — read system activity at a glance, no face.",
        helix: "A rotating double helix that tightens while JARVIS speaks.",
        anime3d: "The optional 3D anime companion. Loads on demand and unloads completely when switched off.",
      };
      p.appendChild(card("AI Avatar Mode",
        "Classic face · Minimal core · Orbit · Helix · optional 3D Anime AI Girl. " +
        (AVATAR_HINT[S.avatar.mode] || ""),
        pillsEl(
          [["classic", "CLASSIC"], ["minimal", "MINIMAL"], ["orbit", "ORBIT"], ["helix", "HELIX"], ["anime3d", "3D ANIME GIRL"]],
          () => (S.avatar.mode || "classic"),
          v => { S.avatar.mode = v; crossfadeAvatar(() => applyAvatarMode({ mode: v }));
                 const r = save("avatar_mode", v, true);
                 renderSettingsPage("appearance");   // reveal/hide the options panel
                 return r; })));

      // customization panel for non-classic modes
      if (S.avatar.mode && S.avatar.mode !== "classic") {
        const grid = el("div", "set-col");
        const mkSlider = (label, key, min, max, step, fmt) => {
          const row = el("div", "set-row2");
          const lbl = el("span", null, label);
          lbl.style.cssText = "flex:0 0 150px;font-size:12px;color:var(--text-med);";
          const inp = el("input");
          inp.type = "range"; inp.min = min; inp.max = max; inp.step = step;
          inp.value = S.avatar[key];
          inp.style.flex = "1";
          const val = el("span", "mono-val", fmt(S.avatar[key]));
          val.style.cssText = "flex:0 0 52px;text-align:right;";
          inp.addEventListener("input", () => { val.textContent = fmt(parseFloat(inp.value)); });
          inp.addEventListener("change", () => {
            S.avatar[key] = parseFloat(inp.value);
            applyAvatarMode({ [key]: S.avatar[key] });
            save("avatar_" + (key === "x" ? "x" : key), S.avatar[key], true);
          });
          row.appendChild(lbl); row.appendChild(inp); row.appendChild(val);
          return row;
        };
        if (S.avatar.mode === "anime3d") {
          grid.appendChild(mkSlider("Size", "size", 0.6, 1.6, 0.05, v => Math.round(v * 100) + "%"));
          grid.appendChild(mkSlider("Position X", "x", -1, 1, 0.05, v => v.toFixed(2)));
          grid.appendChild(mkSlider("Position Y", "y", -0.6, 0.6, 0.05, v => v.toFixed(2)));
          grid.appendChild(mkSlider("Animation intensity", "anim", 0, 1.5, 0.05, v => Math.round(v * 100) + "%"));
          grid.appendChild(mkSlider("Expression intensity", "expr", 0, 1.5, 0.05, v => Math.round(v * 100) + "%"));
          grid.appendChild(card("Orbital companion", "", toggleEl(
            () => S.avatar.orbit !== false,
            v => { S.avatar.orbit = v; applyAvatarMode({ orbit: v }); return save("avatar_orbit", v, true); })));
          const lrow = el("div", "set-row2");
          const llbl = el("span", null, "Lighting");
          llbl.style.cssText = "flex:0 0 150px;font-size:12px;color:var(--text-med);";
          const lights = el("div", "pills");
          ["#38bdf8", "#8b6df5", "#31d9ae", "#f5b45c", "#ff8fa5"].forEach(c => {
            const b = el("button", "pill");
            b.style.cssText = "width:24px;height:24px;border-radius:50%;background:" + c + ";";
            b.title = c;
            b.addEventListener("click", () => {
              S.avatar.light = c; applyAvatarMode({ light: c }); save("avatar_light", c, true);
              lights.querySelectorAll(".pill").forEach(x => x.style.borderColor = "var(--border)");
              b.style.borderColor = "#fff";
            });
            if (S.avatar.light === c) b.style.borderColor = "#fff";
            lights.appendChild(b);
          });
          lrow.appendChild(llbl); lrow.appendChild(lights);
          grid.appendChild(lrow);
        }
        const perfRow = el("div", "set-row2");
        const plbl = el("span", null, "Performance");
        plbl.style.cssText = "flex:0 0 150px;font-size:12px;color:var(--text-med);";
        perfRow.appendChild(plbl);
        perfRow.appendChild(pillsEl(
          [["battery", "BATTERY"], ["balanced", "BALANCED"], ["quality", "QUALITY"]],
          () => S.avatar.perf,
          v => { S.avatar.perf = v; applyAvatarMode({ perf: v }); return save("avatar_perf", v, true); }));
        grid.appendChild(perfRow);
        p.appendChild(cardCol("Avatar options",
          S.avatar.mode === "anime3d" ?
            "Idle frames are cheap; quality rises automatically while she speaks. BATTERY caps FPS and resolution for low-end PCs."
            : "Every 2D style is drawn on one canvas and drops to a calm 15 fps while idle, so it stays cheap on low-end PCs.",
          grid));
        p.appendChild(card("Show avatar", "Hide the avatar without changing the mode.",
          toggleEl(() => S.avatar.visible !== false,
            v => { S.avatar.visible = v; applyAvatarMode({ visible: v }); return save("avatar_visible", v, true); })));
      }

      p.appendChild(card("Compact layout", "Collapse the side panel for a chat-focused window.",
        toggleEl(() => d.compact, v => { S.compact = v; document.body.classList.toggle("compact", v); return save("compact", v); })));
      p.appendChild(card("Fullscreen", "Fill the screen.", btnSm("Toggle  [F11]", () => api.window("fullscreen"))));
      return p;
    },

    startup(d) {
      const p = page("Startup & Background", "Wake word, push-to-talk and background running.");
      const wakeCtl = el("div", "set-col");
      const wakeRow = el("div", "set-row2");
      const wakeBtn = btnSm("Enable", () => {});
      const st = d.wake_word || { enabled: false, awake: true, ready: false };
      const refreshWake = () => {
        wakeBtn.textContent = !st.ready ? "Download (one-time)"
          : st.enabled ? "Disable wake word" : "Enable wake word";
      };
      refreshWake();
      wakeBtn.addEventListener("click", async () => {
        if (!st.ready) {
          wakeBtn.textContent = "Downloading…";
          wakeBtn.disabled = true;
          const r = await api.wake_install().catch(e => ({ ok: false, msg: String(e) }));
          wakeBtn.disabled = false;
          if (r && r.ok) { st.ready = true; toast("Wake word ready", "ok"); }
          else toast("Wake word setup failed: " + (r && r.msg || "?"), "err");
          refreshWake();
          return;
        }
        const r = await api.wake_toggle(!st.enabled).catch(() => null);
        if (r && r.token === "enabled") st.enabled = true;
        else if (r) st.enabled = false;
        refreshWake();
      });
      const wakeState = el("span", "mono-val");
      const tick = () => { wakeState.textContent = st.enabled ? (st.awake ? "· awake" : "· asleep") : "· off"; };
      tick();
      wakeRow.appendChild(wakeBtn); wakeRow.appendChild(wakeState);
      wakeCtl.appendChild(wakeRow);
      wakeCtl.appendChild(btnSm("Sleep / wake now", () => api.wake_manual().then(() => { st.awake = !st.awake; tick(); })));
      p.appendChild(cardCol("Wake word  (“Hey Jarvis”)", "Works even when the window is hidden.", wakeCtl));

      p.appendChild(card("Push-to-talk", "Hold the call button (or hotkey) instead of streaming the mic.",
        toggleEl(() => d.ptt, v => save("ptt", v))));
      p.appendChild(card("Start with Windows", "Run in the tray from login.",
        toggleEl(() => d.autostart, v => save("autostart", v))));
      p.appendChild(card("Background mode", "Hide the window; JARVIS keeps running in the tray.",
        btnSm("⇣  Hide now", () => api.enter_background())));
      const n = el("div", "note");
      n.textContent = "Closing the window always hides to tray — voice, wake word, memory and screen awareness keep running. Say “Hey Jarvis”, or Win+Shift+J to summon the window.";
      p.appendChild(n);
      return p;
    },

    privacy(d) {
      const p = page("Privacy", "What leaves the machine, and what stays.");
      p.appendChild(card("Persistent memory", "Master switch for writing to long-term memory.",
        toggleEl(() => d.memory_enabled, v => save("memory_enabled", v))));
      p.appendChild(card("Activity timeline", "Track recent activity for follow-up.",
        toggleEl(() => d.track_activity, v => save("track_activity", v))));
      p.appendChild(card("Screen share", "Everything visual stays on this machine.",
        toggleEl(() => d.screen_awareness, v => save("screen_awareness", v))));
      p.appendChild(card("Narrate actions", "Explain out loud what JARVIS is doing.",
        toggleEl(() => d.narrate_actions, v => save("narrate_actions", v))));
      p.appendChild(card("Wake word privacy", "While asleep, the microphone audio never leaves the machine.",
        toggleEl(() => d.wake_word && d.wake_word.enabled, v => api.wake_toggle(v))));
      return p;
    },

    performance(d) {
      const p = page("Performance", "Live resource use. Values refresh every 2 seconds — never per frame.");
      const grid = el("div", "perf-grid");
      const mk = (k, label) => {
        const t = el("div", "perf-tile");
        t.appendChild(el("div", "perf-k", label));
        const v = el("div", "perf-v", "--");
        t.appendChild(v);
        const bar = el("div", "perf-bar"); const i = el("i"); bar.appendChild(i);
        t.appendChild(bar);
        t.dataset.key = k;
        grid.appendChild(t);
        return t;
      };
      mk("cpu", "CPU"); mk("mem", "RAM"); mk("gpu", "GPU"); mk("tmp", "Temp");
      p.appendChild(grid);
      paintPerf(grid);
      const n = el("div", "note");
      n.textContent = "The interface itself is a native WebView2 view: no bundled browser, no heavy framework, animations pause when the window is hidden.";
      p.appendChild(n);
      return p;
    },

    advanced(d) {
      const p = page("Advanced", "Diagnostics and power-user controls.");
      const g = el("div", "perf-grid");
      const up = el("div", "perf-tile"); up.appendChild(el("div", "perf-k", "UPTIME")); const uv = el("div", "perf-v", "--"); up.appendChild(uv);
      const pr = el("div", "perf-tile"); pr.appendChild(el("div", "perf-k", "PROCESSES")); const pv = el("div", "perf-v", "--"); pr.appendChild(pv);
      g.appendChild(up); g.appendChild(pr);
      p.appendChild(g);
      paintAdvanced(g);

      // ── Self-update (shared animated card — see buildUpdateCard above) ──
      p.appendChild(cardCol("Updates", "Check your update channel and install new versions in one click.",
        buildUpdateCard(d)));

      p.appendChild(card("Undo", "Take back JARVIS's last file or setting action.",
        btnSm("Undo last", () => api.undo_last().then(r => toast(r && r.ok ? "Undone" : "Nothing to undo", "ok")))));
      p.appendChild(card("Summon hotkey", "Win + Shift + J brings the window to the front from anywhere.", null));
      p.appendChild(card("Activity log", "The full log lives in Chat — system lines appear there inline.",
        btnSm("Open chat", () => showView("chat"))));
      return p;
    },

    about(d) {
      const p = page("About", "");
      const c = el("div", "set-card col");
      c.appendChild(el("div", "set-name", "JARVIS — ICE" + (d.app_version ? "  ·  v" + d.app_version : "")));
      const b = el("div", "set-desc");
      b.innerHTML = "A real-time voice AI that hears, sees, speaks and controls the computer.<br>" +
        "Voice: Gemini Live · Wake word: openWakeWord (local) · Memory: local JSON · " +
        "Vision: screen & webcam · Interface: native WebView2.<br><br>" +
        "Everything personal stays on this machine.";
      c.appendChild(b);
      p.appendChild(c);
      // a staged update is one tap away from anywhere — including here
      if (d.update_pending) {
        const row = el("div", "set-card col upd-pending-card");
        row.appendChild(el("div", "set-name", "v" + (d.update_pending_version || "update") +
          " is ready to install"));
        row.appendChild(el("div", "set-desc",
          "It downloads safely in the background and swaps in on restart."));
        const ib = btnSm("Restart & install", () => {
          ib.disabled = true; ib.textContent = "Installing…";
          toast("Installing update — JARVIS restarts automatically", "ok");
          api.updater_apply().catch(() => {});
        });
        ib.className = "btn btn-sm upd-cta upd-pulse";
        row.appendChild(ib);
        p.appendChild(row);
      }
      return p;
    },
  };

  function btnSm(label, fn) {
    const b = el("button", "btn btn-sm", label);
    b.addEventListener("click", () => { try { fn(); } catch (e) { console.error(e); } });
    return b;
  }

  function fillDeviceSel(sel, list, current, defaultLabel) {
    sel.textContent = "";
    const o = el("option", null, defaultLabel); o.value = ""; sel.appendChild(o);
    (list || []).forEach(n => { const x = el("option", null, n); x.value = n; sel.appendChild(x); });
    sel.value = current || "";
  }

  function paintPerf(grid) {
    const paint = () => {
      if (S.view !== "settings" || S.settingsPage !== "performance") return;
      applyPerfTo(grid);
      setTimeout(paint, 2000);
    };
    applyPerfTo(grid);
    setTimeout(paint, 2000);
  }
  function applyPerfTo(grid) {
    const P = S.perf;
    if (!P || !grid) return;
    const map = { cpu: P.cpu, mem: P.mem, gpu: P.gpu, tmp: P.tmp };
    grid.querySelectorAll(".perf-tile").forEach(t => {
      const k = t.dataset.key;
      let v = map[k];
      const vEl = t.querySelector(".perf-v");
      const bar = t.querySelector(".perf-bar i");
      if (k === "tmp") {
        vEl.textContent = (v && v > 0) ? Math.round(v) + "°C" : "N/A";
        bar.style.width = v > 0 ? Math.min(100, v) + "%" : "0%";
      } else if (k === "gpu" && (v === undefined || v < 0)) {
        vEl.textContent = "N/A"; bar.style.width = "0%";
      } else {
        vEl.textContent = Math.round(v) + "%";
        bar.style.width = Math.max(2, Math.min(100, v)) + "%";
      }
    });
  }
  function paintAdvanced(grid) {
    const P = S.perf;
    if (!P) return;
    const tiles = grid.querySelectorAll(".perf-tile");
    tiles[0].querySelector(".perf-v").textContent = P.uptime || "--";
    tiles[1].querySelector(".perf-v").textContent = P.procs || "--";
  }

  // ── API keys page (async, never blocks) ────────────────────────────────
  async function loadApiKeys(wrap) {
    wrap.textContent = "";
    wrap.appendChild(el("div", "dim", "Loading…"));
    let data;
    try { data = await api.api_keys_get(); }
    catch (e) { wrap.textContent = ""; wrap.appendChild(el("div", "dim", "Could not load API settings.")); return; }
    wrap.textContent = "";
    const primary = data.primary || "gemini";

    // ── Gemini ──
    const gCard = el("div", "provider-card");
    const gHead = el("div", "provider-head");
    gHead.appendChild(el("div", "provider-name", "Gemini"));
    const gTag = el("span", "provider-tag" + (primary === "gemini" ? "" : " off"),
                    primary === "gemini" ? "PRIMARY" : "FALLBACK");
    gHead.appendChild(gTag);
    const gStatus = el("span", "provider-status",
      data.gemini.key ? "" : "API key required. Open Settings → API Keys.");
    gHead.appendChild(gStatus);
    gCard.appendChild(gHead);

    const gRow = el("div", "key-row");
    const gIn = el("input", "text-input"); gIn.type = "password"; gIn.value = data.gemini.key || ""; gIn.placeholder = "AIza… (optional — a free provider below also works)";
    gRow.appendChild(gIn);
    gRow.appendChild(btnSm("👁", () => { gIn.type = gIn.type === "password" ? "text" : "password"; }));
    gRow.appendChild(btnSm("Test", async () => {
      gStatus.textContent = "testing…"; gStatus.className = "provider-status busy";
      const r = await api.setup_test_key(gIn.value.trim()).catch(e => ({ ok: false, msg: String(e) }));
      gStatus.textContent = r.ok ? "Gemini API: Connected" : "✕ " + (r.msg || "invalid");
      gStatus.className = "provider-status " + (r.ok ? "ok" : "bad");
    }));
    gRow.appendChild(btnSm("Save", async () => {
      const r = await api.api_keys_save({ gemini_key: gIn.value.trim() }).catch(e => ({ ok: false, err: String(e) }));
      toast(r && r.ok ? (gIn.value.trim() ? "Gemini key saved" : "Gemini key cleared") : "Save failed", r && r.ok ? "ok" : "err");
    }));
    if (primary !== "gemini") {
      gRow.appendChild(btnSm("Make primary", async () => {
        await api.api_keys_save({ primary: "gemini" }).catch(() => null);
        loadApiKeys(wrap);
      }));
    }
    gCard.appendChild(gRow);
    wrap.appendChild(gCard);

    // ── Providers ──
    const head = el("div", "field-label", "Fallback providers (used when Gemini is unavailable)");
    head.style.marginTop = "18px";
    wrap.appendChild(head);

    (data.providers || []).forEach(pr => {
      const isPrimary = primary === pr.name;
      const on = pr.enabled !== false && !!pr.api_key;
      const cardEl = el("div", "provider-card");
      const head2 = el("div", "provider-head");
      head2.appendChild(el("div", "provider-name", pr.name));
      head2.appendChild(el("span", "provider-tag" + (isPrimary ? "" : " off"),
                           isPrimary ? "PRIMARY" : "FALLBACK"));
      const st = el("span", "provider-status", pr.enabled === false ? "disabled" : (pr.api_key ? "" : "no key"));
      head2.appendChild(st);
      cardEl.appendChild(head2);

      const row = el("div", "key-row");
      const inp = el("input", "text-input");
      inp.type = "password"; inp.value = pr.api_key || ""; inp.placeholder = "api key";
      row.appendChild(inp);
      row.appendChild(btnSm("👁", () => { inp.type = inp.type === "password" ? "text" : "password"; }));
      row.appendChild(btnSm("Test", async () => {
        st.textContent = "testing…"; st.className = "provider-status busy";
        const rows = [{ name: pr.name, base_url: pr.base_url, api_key: inp.value.trim(), model: pr.model }];
        const res = await api.api_keys_test({ gemini_key: "", providers: rows }).catch(() => null);
        const entry = res && res.find(r => r[0] === pr.name);
        st.textContent = entry ? (entry[1] ? pr.name + ": Connected" : "✕ " + entry[2]) : "✕ test failed";
        st.className = "provider-status " + (entry && entry[1] ? "ok" : "bad");
      }));
      row.appendChild(btnSm("Save", async () => {
        const r = await api.api_keys_save({
          provider: { name: pr.name, base_url: pr.base_url, api_key: inp.value.trim(), model: pr.model },
        }).catch(e => ({ ok: false, err: String(e) }));
        toast(r && r.ok ? pr.name + " saved" : "Save failed", r && r.ok ? "ok" : "err");
        if (r && r.ok) loadApiKeys(wrap);
      }));
      row.appendChild(btnSm(pr.enabled === false ? "Enable" : "Disable", async () => {
        const r = await api.api_keys_save(
          pr.enabled === false ? { enable: pr.name } : { disable: pr.name }
        ).catch(() => null);
        if (r && r.ok) loadApiKeys(wrap);
      }));
      if (!isPrimary) {
        row.appendChild(btnSm("Make primary", async () => {
          await api.api_keys_save({ primary: pr.name }).catch(() => null);
          loadApiKeys(wrap);
        }));
      }
      row.appendChild(btnSm("✕", async () => {
        if (!confirm("Delete " + pr.name + " and its key from disk?")) return;
        const r = await api.api_keys_save({ delete: pr.name }).catch(() => null);
        if (r && r.ok) { toast(pr.name + " deleted", "ok"); loadApiKeys(wrap); }
      }));
      cardEl.appendChild(row);
      wrap.appendChild(cardEl);
    });

    // ── Add a new provider (no reinstall, no source edit) ──
    const addHead = el("div", "field-label", "Add a provider");
    addHead.style.marginTop = "18px";
    wrap.appendChild(addHead);
    const addCard = el("div", "provider-card");
    const addRow = el("div", "key-row"); addRow.style.flexWrap = "wrap";
    const nIn = el("input", "text-input"); nIn.placeholder = "name (e.g. openai)"; nIn.style.flex = "1 1 120px";
    const uIn = el("input", "text-input"); uIn.placeholder = "base URL (https://…/v1)"; uIn.style.flex = "2 1 220px";
    const mIn = el("input", "text-input"); mIn.placeholder = "model"; mIn.style.flex = "1 1 140px";
    const kIn = el("input", "text-input"); kIn.type = "password"; kIn.placeholder = "api key"; kIn.style.flex = "1 1 140px";
    [nIn, uIn, mIn, kIn].forEach(x => addRow.appendChild(x));
    addRow.appendChild(btnSm("Add", async () => {
      const name = nIn.value.trim();
      if (!name || !uIn.value.trim()) { toast("Name and base URL required", "err"); return; }
      const r = await api.api_keys_save({
        provider: { name, base_url: uIn.value.trim(), model: mIn.value.trim(), api_key: kIn.value.trim() },
      }).catch(e => ({ ok: false, err: String(e) }));
      if (r && r.ok) { toast(name + " added", "ok"); loadApiKeys(wrap); }
      else toast((r && r.err) || "Add failed", "err");
    }));
    addCard.appendChild(addRow);
    wrap.appendChild(addCard);

    const n = el("div", "note");
    n.textContent = "Free keys: console.groq.com · cloud.cerebras.ai · openrouter.ai · huggingface.co/settings/tokens (fine-grained token with \"Make calls to Inference Providers\"). Disable keeps the key but stops use; ✕ deletes it. Changes apply after Save — no restart needed.";
    n.style.marginTop = "12px";
    wrap.appendChild(n);
  }

  // ── remote pairing ─────────────────────────────────────────────────────
  function pairRemote() {
    api.remote_pair().then(r => {
      const veil = $("remoteVeil");
      if (!r) {
        $("remoteQr").hidden = true;
        $("remoteText").textContent = "Dashboard unavailable — run: pip install fastapi \"uvicorn[standard]\" cryptography";
      } else {
        const img = $("remoteQr");
        if (r.qr_b64) { img.src = "data:image/png;base64," + r.qr_b64; img.hidden = false; }
        else img.hidden = true;
        $("remoteText").textContent = "Scan, or open: " + (r.autologin || r.url || "");
      }
      veil.hidden = false;
    }).catch(() => toast("Could not reach the dashboard", "err"));
  }
  $("remoteClose").addEventListener("click", () => { $("remoteVeil").hidden = true; });

  // ════════════════════════════════════════════════════════════════════════
  //  SETUP (first run / key re-entry)
  // ════════════════════════════════════════════════════════════════════════
  const PROVIDERS_SETUP = [
    ["groq", "GROQ (free)", "https://api.groq.com/openai/v1", "llama-3.3-70b-versatile", "gsk_…"],
    ["cerebras", "CEREBRAS (free)", "https://api.cerebras.ai/v1", "llama-3.3-70b", "sk-…"],
    ["openrouter", "OPENROUTER (free)", "https://openrouter.ai/api/v1", "meta-llama/llama-3.3-70b-instruct:free", "sk-or-…"],
    ["huggingface", "HUGGING FACE (free)", "https://router.huggingface.co/v1", "meta-llama/Llama-3.1-8B-Instruct", "hf_…"],
  ];

  function buildSetup() {
    const prow = $("setupProviders");
    prow.textContent = "";
    PROVIDERS_SETUP.forEach(([name, label, base, model, hint]) => {
      const row = el("div", "key-row"); row.style.marginBottom = "6px";
      const lbl = el("span", null, label);
      lbl.style.cssText = "flex:0 0 150px;text-align:left;font-size:11px;color:var(--text-dim);align-self:center;";
      row.appendChild(lbl);
      const inp = el("input", "text-input"); inp.type = "password"; inp.placeholder = hint; inp.dataset.pv = name;
      row.appendChild(inp);
      prow.appendChild(row);
    });
    const osRow = $("setupOsRow");
    osRow.textContent = "";
    const detected = navigator.platform.toLowerCase().includes("win") ? "windows"
      : navigator.platform.toLowerCase().includes("mac") ? "mac" : "linux";
    let sel = detected;
    [["windows", "⊞ Windows"], ["mac", "macOS"], ["linux", "🐧 Linux"]].forEach(([k, label]) => {
      const b = el("button", "os-btn" + (k === sel ? " sel" : ""), label);
      b.addEventListener("click", () => {
        sel = k;
        osRow.querySelectorAll(".os-btn").forEach(x => x.classList.remove("sel"));
        b.classList.add("sel");
      });
      b.dataset.os = k;
      osRow.appendChild(b);
    });
  }

  $("setupShow").addEventListener("click", () => {
    const k = $("setupGemini");
    k.type = k.type === "password" ? "text" : "password";
  });
  $("setupTest").addEventListener("click", async () => {
    const st = $("setupTestStatus");
    st.className = "test-status busy"; st.textContent = "Testing…";
    const r = await api.setup_test_key($("setupGemini").value.trim()).catch(e => ({ ok: false, msg: String(e) }));
    st.className = "test-status " + (r.ok ? "ok" : "bad");
    st.textContent = r.ok ? "✓ Key valid" : "✕ " + (r.msg || "Invalid key");
  });
  $("setupGo").addEventListener("click", async () => {
    const msg = $("setupMsg");
    const key = $("setupGemini").value.trim();
    const providers = PROVIDERS_SETUP.map(([name, label, base, model], i) => ({
      name, base_url: base, model,
      api_key: document.querySelector("[data-pv='" + name + "']").value.trim(),
    })).filter(p => p.api_key);
    // No key at all is allowed: JARVIS starts and shows
    // "API key required. Open Settings → API Keys." until one is added.
    msg.className = "setup-msg"; msg.textContent = "Initialising…";
    const r = await api.setup_save({
      gemini_key: key,
      providers,
      assistant_name: $("setupAsst").value.trim() || "JARVIS",
      user_name: $("setupUser").value.trim(),
    }).catch(e => ({ ok: false, msg: String(e) }));
    if (r && r.ok) {
      if (!key && !providers.length) {
        msg.className = "setup-msg good";
        msg.textContent = "Ready. You can add API keys later in Settings → API Keys.";
      } else {
        msg.className = "setup-msg good"; msg.textContent = "Ready. Starting JARVIS…";
      }
      setTimeout(() => { $("setupVeil").hidden = true; }, 700);
    } else {
      msg.className = "setup-msg bad"; msg.textContent = (r && r.msg) || "Could not save.";
    }
  });

  // ════════════════════════════════════════════════════════════════════════
  //  ACCENT / THEME
  // ════════════════════════════════════════════════════════════════════════
  function hexToHsl(hex) {
    const n = parseInt(hex.slice(1), 16);
    const r = ((n >> 16) & 255) / 255, g = ((n >> 8) & 255) / 255, b = (n & 255) / 255;
    const mx = Math.max(r, g, b), mn = Math.min(r, g, b);
    let h = 0, s = 0; const l = (mx + mn) / 2;
    if (mx !== mn) {
      const d = mx - mn;
      s = l > 0.5 ? d / (2 - mx - mn) : d / (mx + mn);
      if (mx === r) h = (g - b) / d + (g < b ? 6 : 0);
      else if (mx === g) h = (b - r) / d + 2;
      else h = (r - g) / d + 4;
      h /= 6;
    }
    return [h * 360, s * 100, l * 100];
  }

  function applyAccent(hex) {
    if (!hex || !/^#[0-9a-fA-F]{6}$/.test(hex)) return;
    S.accent = hex;
    const [h, s, l] = hexToHsl(hex);
    const r = document.documentElement.style;
    r.setProperty("--pri", hex);
    r.setProperty("--pri-dim", "hsl(" + h + " " + Math.max(30, s - 18) + "% " + Math.max(24, l - 22) + "%)");
    r.setProperty("--pri-ghost", "hsla(" + h + ", " + s + "%, " + l + "%, 0.10)");
    r.setProperty("--glow-pri", "0 0 18px hsla(" + h + ", " + s + "%, " + l + "%, 0.18)");
  }

  // ════════════════════════════════════════════════════════════════════════
  //  CLOCK + WINDOW CONTROLS
  // ════════════════════════════════════════════════════════════════════════
  function tickClock() {
    const d = new Date();
    $("clockTime").textContent = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    $("clockDate").textContent = d.toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" });
  }
  tickClock(); setInterval(tickClock, 1000);

  $("btnMin").addEventListener("click", () => api.window("minimize"));
  $("btnMax").addEventListener("click", () => api.window("maximize"));
  $("btnBg").addEventListener("click", () => api.enter_background());
  $("btnClose").addEventListener("click", () => api.enter_background());

  // ════════════════════════════════════════════════════════════════════════
  //  EVENT WIRING (backend → UI)
  // ════════════════════════════════════════════════════════════════════════
  Bus.on("state", s => { applyState(s); feedAvatar3D("state", s); });
  Bus.on("log", onLog);
  Bus.on("content", c => showContent(c.title, c.text));
  Bus.on("review", r => showReview(r.title, r.summary, r.findings, r.unclear));
  Bus.on("audio_level", a => {
    const av = window.JarvisAvatar; if (av) av.level = a.level;
    feedAvatar3D("audio_level", a);
  });
  Bus.on("viseme_now", v => {
    const av = window.JarvisAvatar;
    if (av) { av.viseme.level = v.level || 0; av.viseme.openness = v.openness || 0; av.viseme.width = v.width || 0; }
    feedAvatar3D("viseme_now", v);
  });
  Bus.on("camera_start", () => {
    $("cameraView").hidden = false;
    const a3 = window.Jarvis3DAvatar && window.Jarvis3DAvatar.instance();
    if (a3) a3.pause(true);
    if (window.JarvisAvatar) window.JarvisAvatar.setPaused(true);
  });
  Bus.on("camera_frame", f => { $("cameraView").hidden = false; $("camImg").src = "data:image/jpeg;base64," + f.b64; });
  Bus.on("camera_stop", () => {
    $("cameraView").hidden = true;
    const a3 = window.Jarvis3DAvatar && window.Jarvis3DAvatar.instance();
    if (a3) a3.pause(false);
    if (window.JarvisAvatar && S.avatar.mode !== "anime3d") window.JarvisAvatar.setPaused(false);
  });
  $("camClose").addEventListener("click", () => api.stop_camera().catch(() => {}));
  Bus.on("screen", s => { S.screen = s; refreshHomeTiles(); paintHud(); });
  Bus.on("wake", w => { S.wake = w; refreshHomeTiles(); if (S.settingsLoaded) { S.settingsLoaded.wake_word = w; if (S.settingsPage === "startup") renderSettingsPage("startup"); } });
  Bus.on("perf", p => { S.perf = p; paintTopPerf(p); });
  Bus.on("confirm", c => { $("confirmTitle").textContent = c.title; $("confirmDetail").textContent = c.detail; $("confirmVeil").hidden = false; });
  Bus.on("confirm_hide", () => { $("confirmVeil").hidden = true; });
  Bus.on("toast", t => toast(t.text, t.kind));
  // Computer-control traffic: one line per step, live, plus a full snapshot
  // whenever a lever changes so the rail can never drift out of sync.
  Bus.on("pc_feed", e => addFeed(e || {}));
  Bus.on("control", c => {
    S.pc.modes = c.modes || S.pc.modes;
    S.pc.state = c.state || S.pc.state;
    paintRail();
    setFeed(c.feed || []);
    paintPcFoot(S.pc.state);
    if (S.settingsLoaded && S.settingsPage === "pc") renderSettingsPage("pc");
  });
  Bus.on("phone", () => toast("Phone connected via Remote Dashboard", "ok"));
  Bus.on("muted", m => applyMuted(!!m.muted));
  Bus.on("config", c => {
    if (c.assistant_name) {
      S.assistantName = c.assistant_name;
      $("brandName").textContent = c.assistant_name.toUpperCase();
      document.title = c.assistant_name.toUpperCase() + " — JARVIS";
    }
    if (c.accent) applyAccent(c.accent);
    if (c.hud_style) { S.hudStyle = c.hud_style; const av = window.JarvisAvatar; if (av) av.style = c.hud_style; }
    if (c.avatar) applyAvatarMode(c.avatar);
    if (typeof c.compact === "boolean") { S.compact = c.compact; document.body.classList.toggle("compact", c.compact); }
    refreshHomeTiles();
  });
  Bus.on("setup", () => { $("setupVeil").hidden = false; });
  Bus.on("avatar", a => { applyAvatarMode(a); });
  Bus.on("app_hidden", () => {
    if (window.JarvisAvatar) window.JarvisAvatar.setPaused(true);
    const a3 = window.Jarvis3DAvatar && window.Jarvis3DAvatar.instance();
    if (a3) a3.pause(true);
  });
  Bus.on("app_shown", () => {
    if (window.JarvisAvatar && S.avatar.mode !== "anime3d") window.JarvisAvatar.setPaused(false);
    const a3 = window.Jarvis3DAvatar && window.Jarvis3DAvatar.instance();
    if (a3) a3.pause(false);
  });

  function paintTopPerf(p) {
    const cpu = $("chipCpu"), mem = $("chipMem");
    if (typeof p.cpu === "number") {
      cpu.textContent = "CPU " + Math.round(p.cpu) + "%";
      cpu.className = "chip" + (p.cpu > 85 ? " chip-bad" : " chip-load");
      cpu.style.setProperty("--load", Math.min(100, Math.max(0, p.cpu)) + "%");
    }
    if (typeof p.mem === "number") {
      mem.textContent = "MEM " + Math.round(p.mem) + "%";
      mem.className = "chip chip-alt" + (p.mem > 88 ? " chip-bad" : " chip-load");
      mem.style.setProperty("--load", Math.min(100, Math.max(0, p.mem)) + "%");
    }
  }

  // ════════════════════════════════════════════════════════════════════════
  //  PARTICLE FIELD — drifting constellation behind the app.  Dots link with
  //  faint lines when close; the whole field leans toward the pointer with a
  //  soft lag.  Pauses when the tab is hidden, skips itself under
  //  prefers-reduced-motion, and caps devicePixelRatio at 1.5 for perf.
  // ════════════════════════════════════════════════════════════════════════
  function startParticles() {
    const cv = $("fxCanvas");
    if (!cv || cv.dataset.running) return;
    if (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    cv.dataset.running = "1";
    const ctx = cv.getContext("2d");
    const N = 70, LINK = 130;
    let W = 0, H = 0, dpr = 1, parts = [];
    let mx = 0.5, my = 0.42;              // pointer in [0..1], smoothed
    let tx = 0.5, ty = 0.42;              // pointer target
    let running = true;

    function resize() {
      dpr = Math.min(1.5, window.devicePixelRatio || 1);
      W = cv.clientWidth; H = cv.clientHeight;
      cv.width = Math.max(1, W * dpr); cv.height = Math.max(1, H * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }
    function seed() {
      parts = [];
      for (let i = 0; i < N; i++) parts.push({
        x: Math.random() * W, y: Math.random() * H,
        vx: (Math.random() - .5) * .22, vy: (Math.random() - .5) * .22,
        r: Math.random() * 1.4 + .5,
        tw: Math.random() * Math.PI * 2,               // twinkle phase
        tws: .006 + Math.random() * .012,              // twinkle speed
      });
    }
    addEventListener("resize", () => { resize(); seed(); });
    addEventListener("pointermove", e => {
      tx = e.clientX / Math.max(1, innerWidth);
      ty = e.clientY / Math.max(1, innerHeight);
    }, { passive: true });
    document.addEventListener("visibilitychange", () => { running = !document.hidden; });

    resize(); seed();
    (function frame() {
      if (running) {
        // ease the parallax origin toward the pointer — no snapping
        mx += (tx - mx) * .04; my += (ty - my) * .04;
        const ox = (mx - .5) * 26, oy = (my - .5) * 18;  // max lean in px
        // the field mirrors JARVIS's state: agitated while working, nearly
        // still while asleep — read once per frame, cheap
        const st = document.body.dataset.state || "";
        const flow = st === "EXECUTING" ? 3.2 : st === "THINKING" ? 2.0
                   : st === "SLEEPING"  ? 0.35 : 1.0;
        ctx.clearRect(0, 0, W, H);
        for (const p of parts) {
          p.x += p.vx * flow; p.y += p.vy * flow; p.tw += p.tws * Math.min(2, flow);
          if (p.x < -10) p.x = W + 10; if (p.x > W + 10) p.x = -10;
          if (p.y < -10) p.y = H + 10; if (p.y > H + 10) p.y = -10;
        }
        // links first, dots on top
        ctx.lineWidth = 1;
        for (let i = 0; i < N; i++) {
          const a = parts[i];
          for (let j = i + 1; j < N; j++) {
            const b = parts[j];
            const dx = a.x - b.x, dy = a.y - b.y;
            const d2 = dx * dx + dy * dy;
            if (d2 < LINK * LINK) {
              const al = (1 - Math.sqrt(d2) / LINK) * .16;
              ctx.strokeStyle = "rgba(56,189,248," + al.toFixed(3) + ")";
              ctx.beginPath();
              ctx.moveTo(a.x + ox * (a.r / 2), a.y + oy * (a.r / 2));
              ctx.lineTo(b.x + ox * (b.r / 2), b.y + oy * (b.r / 2));
              ctx.stroke();
            }
          }
        }
        for (const p of parts) {
          const tw = .35 + .65 * (0.5 + 0.5 * Math.sin(p.tw));
          ctx.fillStyle = "rgba(125,211,252," + (tw * .5).toFixed(3) + ")";
          ctx.beginPath();
          ctx.arc(p.x + ox * (p.r / 2), p.y + oy * (p.r / 2), p.r, 0, 6.2832);
          ctx.fill();
        }
      }
      requestAnimationFrame(frame);
    })();
  }

  // ════════════════════════════════════════════════════════════════════════
  //  BOOT
  // ════════════════════════════════════════════════════════════════════════
  // pywebview injects its bridge after page scripts run, so wait for it here;
  // only fall back to demo mode (mock.js) when it never shows up.
  function whenBridge(timeoutMs) {
    return new Promise((resolve) => {
      const t0 = Date.now();
      (function check() {
        if (window.pywebview && window.pywebview.api && !window.__MOCK__) return resolve(true);
        if (Date.now() - t0 > timeoutMs) return resolve(false);
        setTimeout(check, 100);
      })();
    });
  }

  async function boot() {
    const real = await whenBridge(4000);
    if (!real && window.__installMock) window.__installMock();

    buildSetup();
    buildRail();
    paintRail();
    $("trainRun").addEventListener("click", trainRun);
    loadTrain();
    // Pushed events keep the card live; this slow poll keeps it honest after a
    // backend restart or a tab that was asleep.
    setInterval(loadTrain, 30000);
    $("btnHandover").addEventListener("click", askHandover);
    $("chipAuto").addEventListener("click", askHandover);
    $("pcClear").addEventListener("click", () => {
      api.pc_feed_clear().then(() => setFeed([])).catch(() => setFeed([]));
    });
    let init = null;
    for (let i = 0; i < 60; i++) {
      try { init = await api.get_initial(); break; }
      catch (e) { await new Promise(r => setTimeout(r, 250)); }
    }
    if (!init) init = {};
    S.assistantName = init.assistant_name || "JARVIS";
    S.configured = init.configured !== false;
    S.wake = init.wake || S.wake;
    S.screen = init.screen || S.screen;
    S.memoryEnabled = init.memory_enabled !== false;
    $("brandName").textContent = S.assistantName.toUpperCase();
    if (S.assistantName.toUpperCase() !== "JARVIS" && S.assistantName.toUpperCase() !== "J.A.R.V.I.S") {
      $("brandSub").textContent = "Personal AI Assistant";
    }
    if (init.accent) applyAccent(init.accent);
    if (init.hud_style) { S.hudStyle = init.hud_style; const av = window.JarvisAvatar; if (av) av.style = init.hud_style; }
    if (init.avatar) applyAvatarMode(init.avatar);
    if (init.compact) { S.compact = true; document.body.classList.add("compact"); }
    if (typeof init.muted === "boolean") applyMuted(init.muted);
    applyState("LISTENING");
    startParticles();
    refreshHomeTiles();
    paintHud();
    loadPc();
    // The feed is pushed, but a slow poll keeps the rail honest if an event is
    // missed (backend restart, tab asleep) without any real cost.
    setInterval(loadPc, 20000);
    api.window("announce_ready").catch(() => {});
    if (!S.configured) {
      $("setupVeil").hidden = false;
    }

    // Quiet update checks are scheduled in silentUpdateCheck() (60 s after
    // boot, then every 5 min) — see the Advanced-page section above.
  }

  boot();
})();
