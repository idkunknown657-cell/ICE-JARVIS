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
              light: "#38bdf8", perf: "balanced", visible: true },
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

  function applyAvatarMode(cfg) {
    if (cfg) Object.assign(S.avatar, cfg);
    const mode = S.avatar.mode;
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
      if (av2d) {
        av2d.setPaused(false);
        av2d.style = mode === "minimal" ? "minimal" : (S.hudStyle === "core" && mode !== "minimal" ? "core" : "face");
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
    const av = window.JarvisAvatar;
    if (av) {
      av.state = state === "MUTED" ? "MUTED" : state;
      av.muted = S.muted;
    }
    $("faceCaption").textContent = STATE_CAPTIONS[state] || state;

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
        if (av && S.avatar.mode !== "anime3d") { av.expression = expr; av.exprUntil = tNow() + 3; }
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

  (function buildQuick() {
    const row = $("quickRow");
    QUICK.forEach(([label, phrase]) => {
      const b = el("button", "quick-btn", label);
      if (phrase) {
        b.addEventListener("click", () => api.quick_action(phrase).catch(() => {}));
      } else {
        b.addEventListener("click", (ev) => openCmdPopover(b));
      }
      row.appendChild(b);
    });
  })();

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
      const p = page("PC Control", "Automation, verification and autonomy.");
      p.appendChild(card("Verify clicks", "Confirm before JARVIS clicks anything on your PC.",
        toggleEl(() => d.verify_clicks, v => save("verify_clicks", v))));
      p.appendChild(card("Goal agent", "Plan-and-execute complex goals with on-screen confirmation for risky steps.",
        toggleEl(() => d.goal_agent, v => save("goal_agent", v))));
      p.appendChild(card("Goal agent auto-run", "Let the agent finish without asking between steps.",
        toggleEl(() => d.goal_agent_auto, v => save("goal_agent_auto", v))));
      p.appendChild(card("Start with Windows", "Launch JARVIS when you log in.",
        toggleEl(() => d.autostart, v => save("autostart", v))));
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
      p.appendChild(card("Confirm purchases", "Installs and buys always wait for your on-screen confirmation.",
        toggleEl(() => d.verify_clicks, v => save("verify_clicks", v))));
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
      p.appendChild(card("AI Avatar Mode",
        "Classic JARVIS face · Minimal core · optional 3D Anime AI Girl. " +
        "The 3D model loads only when selected and unloads completely when switched off.",
        pillsEl(
          [["classic", "CLASSIC"], ["minimal", "MINIMAL"], ["anime3d", "3D ANIME GIRL"]],
          () => (S.avatar.mode || "classic"),
          v => { S.avatar.mode = v; applyAvatarMode({ mode: v });
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
            : "The minimal core draws a handful of shapes — the lightest possible avatar.",
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

    // Gemini
    const gCard = el("div", "provider-card");
    const gHead = el("div", "provider-head");
    gHead.appendChild(el("div", "provider-name", "Gemini"));
    gHead.appendChild(el("span", "provider-tag", "PRIMARY"));
    const gStatus = el("span", "provider-status", data.gemini.valid === true ? "✓ valid" : data.gemini.valid === false ? "✕ " + (data.gemini.msg || "invalid") : "");
    if (data.gemini.valid === true) gStatus.classList.add("ok");
    if (data.gemini.valid === false) gStatus.classList.add("bad");
    gHead.appendChild(gStatus);
    gCard.appendChild(gHead);

    const gRow = el("div", "key-row");
    const gIn = el("input", "text-input"); gIn.type = "password"; gIn.value = data.gemini.key || ""; gIn.placeholder = "AIza…";
    gRow.appendChild(gIn);
    gRow.appendChild(btnSm("👁", () => { gIn.type = gIn.type === "password" ? "text" : "password"; }));
    gRow.appendChild(btnSm("Test", async () => {
      gStatus.textContent = "testing…"; gStatus.className = "provider-status busy";
      const r = await api.setup_test_key(gIn.value.trim()).catch(e => ({ ok: false, msg: String(e) }));
      gStatus.textContent = r.ok ? "✓ valid" : "✕ " + (r.msg || "invalid");
      gStatus.className = "provider-status " + (r.ok ? "ok" : "bad");
    }));
    gRow.appendChild(btnSm("Save", async () => {
      await api.api_keys_save({ gemini_key: gIn.value.trim() });
      toast("Gemini key saved", "ok");
    }));
    gCard.appendChild(gRow);
    wrap.appendChild(gCard);

    // Providers
    const head = el("div", "field-label", "Free fallback providers");
    head.style.marginTop = "18px";
    wrap.appendChild(head);

    (data.providers || []).forEach(pr => {
      const cardEl = el("div", "provider-card");
      const head2 = el("div", "provider-head");
      head2.appendChild(el("div", "provider-name", pr.name));
      const tag = el("span", "provider-tag" + (pr.api_key ? "" : " off"), "FREE");
      head2.appendChild(tag);
      const st = el("span", "provider-status", "");
      head2.appendChild(st);
      cardEl.appendChild(head2);

      const row = el("div", "key-row");
      const inp = el("input", "text-input");
      inp.type = "password"; inp.value = pr.api_key || ""; inp.placeholder = "api key (free)";
      row.appendChild(inp);
      row.appendChild(btnSm("👁", () => { inp.type = inp.type === "password" ? "text" : "password"; }));
      row.appendChild(btnSm("Test", async () => {
        st.textContent = "testing…"; st.className = "provider-status busy";
        const rows = [{ name: pr.name, base_url: pr.base_url, api_key: inp.value.trim(), model: pr.model }];
        const res = await api.api_keys_test({ gemini_key: "", providers: rows }).catch(() => null);
        const entry = res && res.find(r => r[0] === pr.name);
        st.textContent = entry ? (entry[1] ? "✓ valid" : "✕ " + entry[2]) : "✕ test failed";
        st.className = "provider-status " + (entry && entry[1] ? "ok" : "bad");
      }));
      const enBtn = btnSm(pr.api_key ? "Disable" : "Enable", async () => {
        if (pr.api_key) {
          await api.api_keys_save({ disable: pr.name });
          pr.api_key = ""; enBtn.textContent = "Enable"; tag.classList.add("off");
          toast(pr.name + " disabled", "ok");
        } else {
          toast("Paste a key first", "err");
        }
      });
      row.appendChild(enBtn);
      row.appendChild(btnSm("Save", async () => {
        await api.api_keys_save({
          provider: { name: pr.name, base_url: pr.base_url, api_key: inp.value.trim(), model: pr.model },
        });
        pr.api_key = inp.value.trim();
        tag.classList.remove("off");
        toast(pr.name + " saved", "ok");
      }));
      cardEl.appendChild(row);
      wrap.appendChild(cardEl);
    });

    const n = el("div", "note");
    n.textContent = "Get free keys: console.groq.com · cloud.cerebras.ai · openrouter.ai · huggingface.co/settings/tokens (create a fine-grained token with \"Make calls to Inference Providers\" permission). Disabled providers keep nothing on disk — disabling removes the key.";
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
    if (!key) { msg.className = "setup-msg bad"; msg.textContent = "A Gemini key is required."; return; }
    const providers = PROVIDERS_SETUP.map(([name, label, base, model], i) => ({
      name, base_url: base, model,
      api_key: document.querySelector("[data-pv='" + name + "']").value.trim(),
    })).filter(p => p.api_key);
    msg.className = "setup-msg"; msg.textContent = "Initialising…";
    const r = await api.setup_save({
      gemini_key: key,
      providers,
      assistant_name: $("setupAsst").value.trim() || "JARVIS",
      user_name: $("setupUser").value.trim(),
    }).catch(e => ({ ok: false, msg: String(e) }));
    if (r && r.ok) {
      msg.className = "setup-msg good"; msg.textContent = "Ready. Starting JARVIS…";
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
  Bus.on("screen", s => { S.screen = s; refreshHomeTiles(); });
  Bus.on("wake", w => { S.wake = w; refreshHomeTiles(); if (S.settingsLoaded) { S.settingsLoaded.wake_word = w; if (S.settingsPage === "startup") renderSettingsPage("startup"); } });
  Bus.on("perf", p => { S.perf = p; paintTopPerf(p); });
  Bus.on("confirm", c => { $("confirmTitle").textContent = c.title; $("confirmDetail").textContent = c.detail; $("confirmVeil").hidden = false; });
  Bus.on("confirm_hide", () => { $("confirmVeil").hidden = true; });
  Bus.on("toast", t => toast(t.text, t.kind));
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
      cpu.className = "chip" + (p.cpu > 85 ? " chip-bad" : "");
    }
    if (typeof p.mem === "number") {
      mem.textContent = "MEM " + Math.round(p.mem) + "%";
      mem.className = "chip chip-alt" + (p.mem > 88 ? " chip-bad" : "");
    }
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
    refreshHomeTiles();
    api.window("announce_ready").catch(() => {});
    if (!S.configured) {
      $("setupVeil").hidden = false;
    }

    // Quiet update checks are scheduled in silentUpdateCheck() (60 s after
    // boot, then every 5 min) — see the Advanced-page section above.
  }

  boot();
})();
