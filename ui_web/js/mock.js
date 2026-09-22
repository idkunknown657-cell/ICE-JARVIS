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
    { name: "groq", base_url: "https://api.groq.com/openai/v1", api_key: "gsk_demo", model: "llama-3.3-70b-versatile", free: true },
    { name: "cerebras", base_url: "https://api.cerebras.ai/v1", api_key: "", model: "llama-3.3-70b", free: true },
  ];

  let perf = { cpu: 18, mem: 46, gpu: 31, tmp: 52, net: 0.4, uptime: "3h 12m", procs: 214 };

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
      return { gemini: { key: "AIzaSy…demo", valid: null, msg: "" }, providers };
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
    setTimeout(() => emit("state", "LISTENING"), 600);
    setTimeout(() => emit("log", { line: "SYS: JARVIS online. (demo mode — no backend attached)" }), 800);
    setTimeout(() => emit("log", { line: "JARVIS: Good to see you, Sir 😄 The new interface is live — lighter, calmer, faster." }), 1600);
    setTimeout(() => emit("state", "LISTENING"), 2600);
    setInterval(() => {
      perf.cpu = Math.max(4, Math.min(95, perf.cpu + (Math.random() - 0.5) * 12));
      perf.mem = Math.max(20, Math.min(90, perf.mem + (Math.random() - 0.5) * 4));
      emit("perf", perf);
    }, 2000);
    setInterval(() => {
      emit("audio_level", { level: Math.random() * 0.8 });
    }, 120);

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
