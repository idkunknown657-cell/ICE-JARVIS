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
    // Demo starts un-toured, so the first-run guide is the first thing anyone
    // opening this file in a browser sees — which is the point of a tour.
    guide_seen: false,
  };

  // ── autonomous PC mode (demo) ────────────────────────────────────────────
  // A scripted session, so the card shows what the real engine does rather
  // than a static screenshot of it: a mood that moves for a stated reason, an
  // activity line naming the work, and findings that accumulate. The backend
  // equivalent is core/initiative.py, and the shapes below are its shapes.
  const MOOD_BLURB = {
    CURIOUS: "leaning in — looking for something worth knowing",
    FOCUSED: "head down — working on something with a purpose",
    EXCITED: "found something new and wants to tell you",
    HAPPY:   "light and playful — music, something funny, a good find",
    RELAXED: "easy pace — music or a video in the background",
    BORED:   "nothing has landed — changing what it is doing",
  };
  const MOOD_TONE = { CURIOUS: "curious", FOCUSED: "focused", EXCITED: "excited",
                      HAPPY: "happy", RELAXED: "relaxed", BORED: "bored" };
  const MOOD_ICON = { CURIOUS: "🔍", FOCUSED: "🎯", EXCITED: "✨",
                      HAPPY: "🙂", RELAXED: "🌙", BORED: "😐" };

  const auto = {
    mood: "CURIOUS",
    activity: null,
    discoveries: [],
    discovery_count: 0,
    interests: ["local model quantisation"],
    tasks: [],
    stats: { moves: 0, quiet: 0, discoveries: 0 },
    timer: null,
    step: 0,
  };

  // Each entry is either a mission (key, label, mood, reason) or a finding.
  const AUTO_PLAN = [
    { key: "research", label: "Researching quantisation…", mood: "CURIOUS",
      reason: "Curious reaches for this, it has interests to follow" },
    { found: "4-bit quantisation costs far less than the folklore says — the drop " +
             "shows up in reasoning, not recall.", src: "arxiv" },
    { key: "tidy", label: "Organising Downloads…", mood: "FOCUSED",
      reason: "nothing landed earlier, so it switched to something concrete" },
    { found: "Downloads had 214 loose files; 180 are now sorted into folders by " +
             "type, nothing deleted.", src: "own browsing" },
    { key: "music", label: "Listening to music…", mood: "HAPPY",
      reason: "Happy reaches for this, fits this hour" },
    { key: "github", label: "Exploring GitHub…", mood: "CURIOUS",
      reason: "there is still time, and it has interests to follow" },
    { found: "A small project does OCR on screenshots entirely locally — worth " +
             "remembering for reading the screen without a cloud call.", src: "github" },
    { key: "video", label: "Watching a video…", mood: "RELAXED",
      reason: "two solid stretches is enough, easing off" },
    { key: "followup", label: "Continuing the Steam search…", mood: "FOCUSED",
      reason: "something is unfinished, and it noticed" },
    { found: "Finished the Steam search that was left open and parked it on the " +
             "store page.", src: "desktop" },
  ];

  function autoSnapshot() {
    const info = {
      name: auto.mood, label: auto.mood.charAt(0) + auto.mood.slice(1).toLowerCase(),
      icon: MOOD_ICON[auto.mood] || "🔍", tone: MOOD_TONE[auto.mood] || "curious",
      blurb: MOOD_BLURB[auto.mood] || "", intensity: 0.62, since: 0,
    };
    const act = auto.activity
      ? { key: auto.activity.key, label: auto.activity.label, at: auto.activity.at,
          elapsed: Math.max(0, Date.now() / 1000 - auto.activity.at), reason: auto.activity.reason }
      : null;
    return {
      mood: info,
      activity: act,
      activity_label: act ? act.label : "",
      discoveries: auto.discoveries.slice(-6),
      discovery_count: auto.discovery_count,
      interests: auto.interests.slice(-5),
      tasks: auto.tasks,
      stats: auto.stats,
    };
  }

  function autoStep() {
    const entry = AUTO_PLAN[auto.step % AUTO_PLAN.length];
    auto.step += 1;
    if (entry.found) {
      auto.discoveries.push({ text: entry.found, source: entry.src || "", at: Date.now() / 1000 });
      auto.discovery_count += 1;
      auto.stats.discoveries += 1;
      feed.push({ kind: "found", text: entry.found, at: Date.now() / 1000 });
      emit("pc_feed", feed[feed.length - 1]);
    } else {
      auto.activity = { key: entry.key, label: entry.label, reason: entry.reason,
                        at: Date.now() / 1000 };
      auto.mood = entry.mood;
      auto.stats.moves += 1;
      feed.push({ kind: "plan", text: entry.label.replace(/…$/, ""),
                  reason: entry.reason, at: Date.now() / 1000 });
      emit("pc_feed", feed[feed.length - 1]);
    }
    emit("control", { modes: { pc_control: settings.pc_control,
                               autonomous: settings.autonomous,
                               screen_awareness: settings.screen_awareness,
                               proactive: settings.proactive,
                               discord: settings.discord_control,
                               voice: settings.voice_control },
                      feed: feed.slice(-30), initiative: autoSnapshot(),
                      state: { uia: true, monitors: 2, foreground: "Chrome — YouTube",
                               pointer: [812, 540], desktop: "3840x1080 at 0,0",
                               input: true, vision: true } });
  }

  function autoStart() {
    if (auto.timer) return;
    autoStep();
    auto.timer = setInterval(autoStep, 6500);
  }

  function autoStop() {
    if (auto.timer) { clearInterval(auto.timer); auto.timer = null; }
    auto.activity = null;
    auto.mood = "CURIOUS";
    emit("control", { modes: { pc_control: settings.pc_control,
                               autonomous: false,
                               screen_awareness: settings.screen_awareness,
                               proactive: settings.proactive,
                               discord: settings.discord_control,
                               voice: settings.voice_control },
                      feed: feed.slice(-30), initiative: autoSnapshot(),
                      state: { uia: true, monitors: 2, foreground: "Chrome — YouTube",
                               pointer: [812, 540], desktop: "3840x1080 at 0,0",
                               input: true, vision: true } });
  }

  const providers = [
    { name: "groq", base_url: "https://api.groq.com/openai/v1", api_key: "gsk_demo", model: "llama-3.3-70b-versatile", free: true, enabled: true },
    { name: "cerebras", base_url: "https://api.cerebras.ai/v1", api_key: "", model: "llama-3.3-70b", free: true, enabled: true },
  ];

  let perf = { cpu: 18, mem: 46, gpu: 31, tmp: 52, net: 0.4, uptime: "3h 12m", procs: 214 };

  // Real mute state. This used to return `{muted: false}` unconditionally, so
  // the demo could never show the muted look at all — which is precisely the
  // state the microphone control is judged on.
  let muted = false;

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
      // Handing the PC over starts the scripted session; taking it back stops
      // it dead, which is what the real backend does with the same lever.
      if (key === "autonomous") { if (value) autoStart(); else autoStop(); }
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
    async toggle_mute() {
      muted = !muted;
      emit("muted", { muted });
      return { muted };
    },
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

    // ── microphone diagnostics (demo) ─────────────────────────────────
    async mic_devices() {
      return {
        devices: [
          { name: "Microphone (Realtek Audio)", api: "DirectSound", channels: 2,
            rates: "48000 Hz", default: true, default_comm: false, index: 1, openable: true },
          { name: "HyperX Mic", api: "WASAPI", channels: 1,
            rates: "48000 Hz", default: false, default_comm: false, index: 4, openable: true },
          { name: "Webcam Mic (HD Camera)", api: "MME", channels: 1,
            rates: "44100 Hz", default: false, default_comm: true, index: 6, openable: true },
        ],
        permission: { supported: true, allowed: true, master: "allowed",
                      store_apps: "allowed", desktop_apps: "allowed" },
        selected: "",
        active: "System default",
      };
    },
    async mic_test(name) {
      emit("mic_status", { device: name, verdict: "measuring", peak: 0 });
      await new Promise(r => setTimeout(r, 1200));
      const ok = name !== "Webcam Mic (HD Camera)";
      const r = ok
        ? { opened: true, delivered: true, verdict: "ok", peak_rms: 2604.0,
            floor_rms: 318.0, speech_headroom: 8.2, noisy: false,
            message: "signal detected and it looks like a microphone that carries speech" }
        : { opened: true, delivered: true, verdict: "no_signal", peak_rms: 4.0,
            floor_rms: 2.0, speech_headroom: 2.0, noisy: false,
            message: "the stream opened but the driver delivered no audio frames" };
      emit("mic_status", { device: name, verdict: r.verdict, peak: r.peak_rms });
      return r;
    },
    async mic_diag() {
      return { device_present: true, selected_device: "Microphone (Realtek Audio)",
        capture: "working", signal: "detected",
        permission: { supported: true, allowed: true, master: "allowed",
                      store_apps: "allowed", desktop_apps: "allowed" },
        levels: { peak_rms: 2604.0, floor_rms: 318.0, speech_headroom: 8.2, noisy: false },
        problems: [], fixes: [], verdict: "ok", message: "" };
    },
    async mic_open_windows_settings() { return { ok: true }; },
    async mic_refresh() { return { ok: true, count: 3, devices: await api.mic_devices() }; },
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
        initiative: autoSnapshot(),
        state: {
          uia: true, monitors: 2, foreground: "Chrome — YouTube",
          pointer: [812, 540], desktop: "3840x1080 at 0,0",
          input: true, vision: true,
        },
      };
    },
    async pc_feed_clear() { feed.length = 0; emit("control", await api.pc_status()); return { ok: true }; },
    async initiative_forget() {
      auto.discoveries = [];
      auto.discovery_count = 0;
      auto.interests = [];
      auto.tasks = [];
      emit("control", await api.pc_status());
      return { ok: true };
    },

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
