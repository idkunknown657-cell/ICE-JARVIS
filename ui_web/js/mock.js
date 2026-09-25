/* ═══════════════════════════════════════════════════════════════════════
   mock.js — demo backend for development, NOT auto-installed.

   pywebview injects its bridge AFTER page scripts run, so "window.pywebview
   missing at parse time" proves nothing. app.js waits briefly for the real
   bridge and calls __installMock() only if it never arrives (plain browser /
   preview development). Everything here is cosmetic; the real backend is
   webui.py.
   ═══════════════════════════════════════════════════════════════════════ */
(function () {
  "use strict";

  const listeners = {};
  function emit(name, payload) {
    // deliver through the SAME channel the real backend uses, so app.js
    // cannot tell demo mode from the real thing
    if (typeof window.__jarvisEvent === "function") {
      try { window.__jarvisEvent(name, payload); return; } catch (e) { console.error(e); }
    }
    (listeners[name] || []).forEach(fn => {
      try { fn(payload); } catch (e) { console.error(e); }
    });
  }
  window.__mockEmit = emit;

  const settings = {
    configured: true,
    assistant_name: "JARVIS",
    user_name: "Sir",
    voice: "Puck",
    talk_cadence: "live",
    humor: "playful",
    emotion_depth: "full",
    language_mode: "auto",
    thinking: true,
    verify_clicks: true,
    memory_enabled: true,
    track_activity: true,
    screen_awareness: true,
    screen_glance: true,
    screen_share_quality: "medium",
    observe_interval: 12,
    narrate_actions: true,
    wake_word: { enabled: false, awake: true, ready: false },
    ptt: false,
    brief: true,
    autostart: false,
    input_device: "",
    output_device: "",
    hud_style: "face",
    accent: "#38bdf8",
    compact: false,
    proactive: true,
    // computer-use levers — demo starts hands-off so the rail shows both states
    pc_control: true,
    autonomous: false,
    discord_control: true,
    voice_control: true,
    self_training: true,
    training_intensity: "balanced",
    media_resolution: "medium",
    goal_agent: true,
    currency: "USD",
    devices: { input: ["Microphone (Realtek Audio)", "HyperX Mic"], output: ["Speakers (Realtek Audio)", "HyperX Cloud II"] },
    app_version: "1.0.1",
    update_source: "",
    update_pending: false,
    update_pending_version: "",
  };

  const providers = [
    { name: "groq", base_url: "https://api.groq.com/openai/v1", api_key: "gsk_demo", model: "llama-3.3-70b-versatile", free: true, enabled: true },
    { name: "cerebras", base_url: "https://api.cerebras.ai/v1", api_key: "", model: "llama-3.3-70b", free: true, enabled: true },
  ];

  let perf = { cpu: 18, mem: 46, gpu: 31, tmp: 52, net: 0.4, uptime: "3h 12m", procs: 214 };

  // Self-training snapshot, the shape core/self_training.py produces: a
  // competency ledger fed by real control outcomes, plus the rules it wrote
  // for itself. Weakest capability first, exactly like the backend sorts it.
  const trainDrills = [
    { id: "d1", capability: "screen", label: "Screen reading",
      situation: "the target has no accessible name",
      rule: "when an element has no name, walk the window with Tab and read the focused element back before clicking anything" },
    { id: "d2", capability: "pc_control", label: "PC control",
      situation: "a dialog appeared after the click",
      rule: "after a click that opens a dialog, re-read the window instead of clicking the same coordinates again" },
  ];
  const train = {
    enabled: true, active: true, intensity: "balanced", memory_enabled: true,
    self_scored: true, cycles: 3, learned: 5, drill_count: trainDrills.length,
    focus: "screen", focus_label: "Screen reading", last_ts: Date.now() / 1000 - 420,
    last_reason: "idle", rounds_left: 1, rounds_per_hour: 2,
    competency: [
      { capability: "screen", label: "Screen reading", attempts: 24, wins: 15, score: 0.63, trend: 0.07 },
      { capability: "error_recovery", label: "Error recovery", attempts: 9, wins: 6, score: 0.67, trend: 0 },
      { capability: "pc_control", label: "PC control", attempts: 61, wins: 55, score: 0.9, trend: 0.03 },
      { capability: "keyboard", label: "Typing & keys", attempts: 18, wins: 17, score: 0.94, trend: 0 },
    ],
    drills: trainDrills,
    history: [{ ts: Date.now() / 1000 - 420, reason: "idle", capability: "screen", proposed: 2, learned: 2, ms: 3100 }],
  };

  // The control feed, exactly the shape core/autonomy.py pushes.
  // Seeded so the preview shows the terminal treatment: badges per kind,
  // green flash on ok, amber pulse on act, blue rail on mode.
  const feed = [];
  function feedPush(kind, text) {
    const e = { kind, text, at: Date.now() / 1000 };
    feed.push(e);
    if (feed.length > 120) feed.shift();
    emit("pc_feed", e);
  }
  // seed a believable session so the preview shows the terminal treatment
  feedPush("mode", "Autopilot engaged — I have the wheel, sir.");
  feedPush("act",  "observe → screen understood (Chrome — YouTube)");
  feedPush("act",  "move → cursor to search box [812, 540]");
  feedPush("act",  "click → search box");
  feedPush("ok",   "focus verified · search box active");
  feedPush("act",  "type → \"lofi hip hop playlist\"");
  feedPush("ok",   "text verified on screen");
  feedPush("act",  "key → Enter");
  feedPush("ok",   "verify → results loaded, first video visible");
  feedPush("act",  "click → first result");
  feedPush("ok",   "playback started — task complete");
  setInterval(() => feedPush("info", "watching the screen · " + (40 + Math.floor(Math.random() * 50)) + "% window idle"), 12000);
  function rails() {
    emit("control", { modes: null, feed: feed.slice(-30), state: null });
  }

  // Sync the demo snapshot with the switches, exactly as the backend does.
  function trainChanged() {
    train.enabled = settings.self_training !== false;
    train.active = train.enabled && settings.memory_enabled !== false;
    train.intensity = settings.training_intensity || "balanced";
    emit("training", { phase: "changed", state: JSON.parse(JSON.stringify(train)) });
  }

  // One believable round: it picks the weakest capability, writes one rule for
  // itself and reports honestly. The preview choreography calls this so the
  // Home card can be seen working without anyone clicking anything.
  function runRound(reason) {
    if (!train.active || !settings.self_training) return;
    emit("training", { phase: "running", reason: reason || "idle" });
    setTimeout(() => {
      train.cycles += 1;
      train.last_ts = Date.now() / 1000;
      train.focus = "screen";
      train.focus_label = "Screen reading";
      train.drills.unshift({
        id: "d" + Math.random().toString(36).slice(2, 8),
        capability: "screen", label: "Screen reading",
        situation: "the window changed while it was being read",
        rule: "if the window changed mid-read, throw the reading away and look once more instead of acting on a stale picture",
      });
      train.drills = train.drills.slice(0, 6);
      train.drill_count = train.drills.length;
      train.learned += 1;
      emit("training", {
        phase: "done",
        report: { ok: true, capability: "screen", capability_label: "Screen reading",
                  proposed: 2, learned: 1, self_scored: true },
        state: JSON.parse(JSON.stringify(train)),
      });
      feedPush("mode", "Self-training: wrote 1 new rule for screen reading");
    }, 2600);
  }

  const api = {
    async ready() { return true; },
    async get_initial() {
      return {
        configured: settings.configured,
        assistant_name: settings.assistant_name,
        user_name: settings.user_name,
        accent: settings.accent,
        hud_style: settings.hud_style,
        compact: settings.compact,
        muted: false,
        wake: settings.wake_word,
        screen: { active: settings.screen_awareness, caption: "browsing" },
        memory_enabled: settings.memory_enabled,
      };
    },
    async get_settings() { return JSON.parse(JSON.stringify(settings)); },
    async save_setting(key, value) {
      if (key === "assistant_name") settings.assistant_name = value;
      else if (key in settings) settings[key] = value;
      // The real bridge pushes self-training changes at the user, so the demo
      // has to as well — otherwise the Home card keeps claiming it is on after
      // the switch was turned off.
      if (key === "self_training" || key === "training_intensity") trainChanged();
      emit("toast", { text: "Saved: " + key, kind: "ok" });
      return { ok: true };
    },
    async send_text(text) {
      emit("log", { line: "You: " + text });
      setTimeout(() => emit("state", "THINKING"), 150);
      const replies = [
        "Heyyy 😄 What are you up to?",
        "On it, Sir — give me a moment.",
        "Here's what I found:\n\n- First point, short and useful\n- Second point with `code` inside\n- Third one, still tidy\n\nWant me to go deeper?",
        "All done ✓ I opened Steam, searched the game and parked it on the store page for you.",
      ];
      setTimeout(() => {
        emit("state", "SPEAKING");
        emit("log", { line: "JARVIS: " + replies[Math.floor(Math.random() * replies.length)] });
        setTimeout(() => emit("state", "LISTENING"), 2200);
      }, 1400);
      return { ok: true };
    },
    async interrupt() { emit("state", "LISTENING"); emit("log", { line: "SYS: Interrupted — listening..." }); },
    async toggle_mute() { return { muted: false }; },
    async set_ptt(held) { return {}; },
    async quick_action(phrase) { return api.send_text(phrase); },
    async open_file_dialog() { return null; },
    async clear_attach() { return {}; },
    async remote_pair() { return null; },
    async api_keys_get() {
      return { gemini: { key: "AIzaSy…demo", valid: null, msg: "" }, providers, primary: "gemini" };
    },
    async api_keys_test(data) {
      await new Promise(r => setTimeout(r, 900));
      return [["gemini", true, "valid"],
        ...providers.filter(p => p.api_key).map(p => [p.name, true, "valid"])];
    },
    async api_keys_save(data) { emit("toast", { text: "API keys saved", kind: "ok" }); return { ok: true }; },
    async memory_get() {
      return {
        identity: { name: "Sir", language: "English" },
        preferences: { humour: "dry", music: "synthwave" },
        projects: { jarvis: "UI rebuild" },
      };
    },
    async memory_forget(cat, key) { emit("toast", { text: "Forgot: " + key, kind: "ok" }); return { ok: true }; },
    async plugins_get() {
      return [
        { name: "whatsapp", description: "Send WhatsApp messages by voice.", valid: true, enabled: true, settings: null },
        { name: "home_assistant", description: "Tuya smart lights.", valid: true, enabled: false, settings: null },
      ];
    },
    async plugin_set_enabled(name, en) { emit("toast", { text: name + (en ? " enabled" : " disabled"), kind: "ok" }); return { ok: true }; },
    async open_plugins_folder() { return {}; },
    async wake_toggle(en) { settings.wake_word.enabled = en; emit("wake", settings.wake_word); return { token: "enabled" }; },
    async wake_install() { settings.wake_word.ready = true; emit("wake", settings.wake_word); return { ok: true, msg: "Wake word ready." }; },
    async wake_manual() { settings.wake_word.awake = !settings.wake_word.awake; emit("wake", settings.wake_word); return {}; },
    async devices_get() { return settings.devices; },
    async perf_get() { return perf; },

    // ── computer control (demo) ────────────────────────────────────────────
    async pc_status() {
      return {
        modes: {
          pc_control: settings.pc_control,
          autonomous: settings.autonomous,
          screen_awareness: settings.screen_awareness,
          proactive: settings.proactive,
          discord: settings.discord_control,
          voice: settings.voice_control,
        },
        feed: feed.slice(-30),
        state: {
          uia: true, monitors: 2, foreground: "Chrome — YouTube",
          pointer: [812, 540], desktop: "3840x1080 at 0,0",
          input: true, vision: true,
        },
      };
    },
    async pc_feed_clear() { feed.length = 0; emit("control", await api.pc_status()); return { ok: true }; },

    // ── self-training (demo) ──────────────────────────────────────────────
    async training_get() { return JSON.parse(JSON.stringify(train)); },
    async training_run() { runRound("manual"); return { ok: true, started: true }; },
    async training_forget(id) {
      const i = train.drills.findIndex(d => d.id === id);
      if (i >= 0) train.drills.splice(i, 1);
      train.drill_count = train.drills.length;
      emit("training", { phase: "changed", state: JSON.parse(JSON.stringify(train)) });
      return { ok: true };
    },
    async training_forget_all() {
      train.drills = []; train.drill_count = 0;
      emit("training", { phase: "changed", state: JSON.parse(JSON.stringify(train)) });
      return { ok: true, forgotten: 0 };
    },
    async training_reset() {
      train.drills = []; train.drill_count = 0; train.cycles = 0; train.learned = 0;
      emit("training", { phase: "changed", state: JSON.parse(JSON.stringify(train)) });
      return { ok: true };
    },
    async confirm_answer(accepted) { emit("confirm_hide", {}); return {}; },
    async undo_last() { emit("toast", { text: "Undone", kind: "ok" }); return { ok: true }; },
    async setup_save(data) { settings.configured = true; return { ok: true }; },
    async setup_test_key(key) { await new Promise(r => setTimeout(r, 800)); return { ok: !!key, msg: key ? "valid" : "no key" }; },
    async toggle_autostart() { settings.autostart = !settings.autostart; return settings.autostart; },
    async create_shortcut() { emit("toast", { text: "Shortcut created", kind: "ok" }); return {}; },
    async enter_background() { return {}; },
    async window(op) { return {}; },
    async open_path(p) { return {}; },
    async stop_camera() { return {}; },
    // ── self-update (demo: v1.0.2 "available", fake streamed download) ──
    async updater_status() { return { version: settings.app_version, pending: false, pending_version: "" }; },
    async updater_check() {
      await new Promise(r => setTimeout(r, 600));
      return { ok: true, update_available: true, current: settings.app_version,
               latest: "1.0.2", notes: "smoother UI, faster replies", url: "", sha256: "" };
    },
    async updater_set_source(url) { settings.update_source = url; return { ok: true }; },
    async updater_download() {
      let f = 0;
      const tick = setInterval(() => {
        f = Math.min(1, f + 0.06 + Math.random() * 0.08);
        emit("update_progress", { stage: "download", frac: f });
        if (f >= 1) {
          clearInterval(tick);
          setTimeout(() => emit("update_progress", { stage: "done", ok: true, version: "1.0.2" }), 400);
        }
      }, 220);
      return { ok: true };
    },
    async updater_apply() { emit("toast", { text: "Installing update — JARVIS restarts", kind: "ok" }); return { ok: true, relaunch: true }; },
  };

  window.__installMock = function () {
    if (window.__MOCK__) return;
    if (window.pywebview && window.pywebview.api) return;   // real bridge arrived
    window.__MOCK__ = true;
    window.pywebview = { api };

    // ── demo choreography ─────────────────────────────────────────────
    feedPush("info", "PC control ready · 2 monitors · accessibility tree");
    setTimeout(() => emit("state", "LISTENING"), 600);
    setTimeout(() => emit("log", { line: "SYS: JARVIS online. (demo mode — no backend attached)" }), 800);
    setTimeout(() => emit("log", { line: "JARVIS: Good to see you, Sir 😄 The new interface is live — lighter, calmer, faster." }), 1600);

    // A live control run: handover, locate, click, verify — the rail and the
    // feed are the point of the redesign, so the demo shows them working.
    setTimeout(() => { settings.autonomous = true; feedPush("mode", "Autonomous mode → ON"); rails(); }, 3400);
    [
      [4400, "act",  "open: Opened https://www.youtube.com; YouTube is up"],
      [5400, "ok",   "click: 'search box' located via accessibility tree at 428,132 · clicked · verified"],
      [6400, "ok",   "type: typed 10 characters into the 'search box' and read them back"],
      [7300, "act",  "click: 'lofi beats' — first result, vision-fine at 512,404"],
      [8300, "ok",   "click: Clicked 'Play' at 612,588 and the screen changed"],
      [9600, "info", "point: pointer on 'volume slider' at 1466,842 via uia"],
      [11200, "ok",  "discord: Discord → Sir: sent 24 characters"],
    ].forEach(([ms, kind, text]) => setTimeout(() => feedPush(kind, text), ms));
    setTimeout(() => emit("state", "LISTENING"), 2600);
    setInterval(() => {
      perf.cpu = Math.max(4, Math.min(95, perf.cpu + (Math.random() - 0.5) * 12));
      perf.mem = Math.max(20, Math.min(90, perf.mem + (Math.random() - 0.5) * 4));
      emit("perf", perf);
    }, 2000);
    setInterval(() => {
      emit("audio_level", { level: Math.random() * 0.8 });
    }, 120);

    // a quiet self-training round, so the Home card is seen working by itself
    setTimeout(() => runRound("idle"), 13000);

    // demo task: EXECUTING → done
    setTimeout(() => {
      emit("state", "EXECUTING");
      emit("log", { line: "JARVIS: Searching Steam for Minecraft…" });
      setTimeout(() => emit("log", { line: "JARVIS: Game found — opening the store page…" }), 1500);
      setTimeout(() => emit("state", "LISTENING"), 2600);
    }, 9000);

    // show what a confirmation looks like (once)
    setTimeout(() => emit("confirm", { title: "Shut down the PC", detail: "JARVIS will shut the machine down in 60 seconds." }), 20000);
  };
})();
