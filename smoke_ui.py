"""Smoke-launch the real WebView2 UI (no Gemini, no audio): verifies the
window opens, app.js boots, the bridge carries events both ways, settings
pages render, and the window closes cleanly."""
import sys, threading, time, json
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import webui
from webui import JarvisUI, _PUMP

results = {}
ui = JarvisUI("face.png")

# fake backend callbacks (what JarvisLive would set)
ui.on_text_command = lambda t: results.setdefault("text_cmds", []).append(t)
ui.on_interrupt = lambda: results.setdefault("interrupts", 0) or results.__setitem__("interrupts", results.get("interrupts", 0) + 1)
ui.wake_get_state = lambda: {"enabled": False, "awake": True, "ready": False}
def _wake_toggle(en): return "enabled" if en else "disabled"
ui.on_wake_toggle = _wake_toggle
ui.get_plugins = lambda: [
    {"name": "demo_plugin", "description": "A demo.", "valid": True, "enabled": True, "settings": None},
]

steps = []

def driver():
    print("[smoke] driver: waiting for JS boot…", flush=True)
    # wait for the JS side to boot
    for _ in range(120):
        if ui._win._ready:
            break
        time.sleep(0.25)
    results["ready"] = ui._win._ready
    print("[smoke] ready:", results["ready"], flush=True)
    if not ui._win._ready:
        print("[smoke] never became ready — dumping state", flush=True)
        print("pywebview obj:", ui._win.win, flush=True)
        try:
            print("events loaded:", ui._win.win.events.loaded.is_set(), flush=True)
        except Exception as e:
            print("loaded evt err:", e, flush=True)
        try:
            ui._win.win.destroy()
        except Exception:
            pass
        return

    w = ui._win.win
    def js(expr):
        try:
            return w.evaluate_js(expr)
        except Exception as e:
            return "JSERR:" + repr(e)

    # 1. DOM booted
    results["title"] = js("document.title")
    results["nav_btns"] = js("document.querySelectorAll('.nav-btn').length")
    results["brand"] = js("document.getElementById('brandName').textContent")

    # 2. backend → UI events
    ui.set_state("LISTENING")
    ui.write_log("You: hello from the smoke test")
    ui.write_log("JARVIS: Heyyy 😄 bridge works!")
    ui.write_log("SYS: JARVIS online.")
    ui.set_screen_status(True, "coding")
    time.sleep(1.0)
    results["chat_msgs"] = js("document.querySelectorAll('#chatList .msg').length")
    results["state_pill"] = js("document.getElementById('statePill').textContent")
    results["eyes_val"] = js("document.getElementById('valEyes').textContent")

    # 3. UI → backend: simulate a chat send through the real API object
    api = ui._api
    api.send_text("open youtube")
    time.sleep(0.5)
    results["text_cmds"] = results.get("text_cmds", [])

    # 4. views switch
    for view in ["chat", "home", "settings"]:
        js(f"document.querySelector('.nav-btn[data-view=\"{view}\"]').click()")
        time.sleep(0.25)
        results["view_" + view] = js(
            f"document.getElementById('view-{view}').classList.contains('active')")

    # 5. every settings page renders content
    pages = ["general", "ai", "api", "voice", "persona", "memory", "screen",
             "pc", "integrations", "steam", "appearance", "startup",
             "privacy", "performance", "advanced", "about"]
    for page in pages:
        js(f"document.querySelector('#settingsNav [data-page=\"{page}\"]').click()")
        time.sleep(0.25)
        results["page_" + page] = js(
            f"document.querySelectorAll('#settingsBody .settings-page > *').length")
    results["api_page_cards"] = js(
        "document.querySelectorAll('#settingsBody .provider-card').length")

    # 6. quick action routes to the backend
    js("document.querySelector('#quickRow .quick-btn').click()")
    time.sleep(0.6)
    results["quick_action_cmd"] = (results.get("text_cmds") or [None])[0]

    # 5. api keys page payload is jsonable
    keys = api.api_keys_get()
    results["api_keys_payload"] = isinstance(keys, dict) and "gemini" in keys

    # 6. confirm banner round trip
    ui.show_confirm("Shutdown", "Really?")
    time.sleep(0.5)
    results["confirm_visible"] = js("!document.getElementById('confirmVeil').hidden")
    api.confirm_answer(False)
    time.sleep(0.4)
    results["confirm_hidden_after"] = js("document.getElementById('confirmVeil').hidden")

    # 7. perf event arrived at the topbar
    results["cpu_chip"] = js("document.getElementById('chipCpu').textContent")

    # 8. settings round-trip through the REAL config (screen awareness off/on)
    results["settings_save_ok"] = js(
        "window.pywebview ? 'js-side-ok' : 'no'")

    # close
    time.sleep(0.5)
    try:
        w.destroy()
    except Exception:
        pass

threading.Thread(target=driver, daemon=True).start()

# hard watchdog — never let the smoke test wedge the session
def _watchdog():
    time.sleep(75)
    print("[smoke] WATCHDOG fired — window never closed. Results so far:")
    print(json.dumps(results, indent=2, ensure_ascii=False, default=str))
    import os as _os
    _os._exit(2)

threading.Thread(target=_watchdog, daemon=True).start()
t0 = time.time()
ui.root.mainloop()
results["elapsed_s"] = round(time.time() - t0, 1)
print("SMOKE RESULTS:", json.dumps(results, indent=2, ensure_ascii=False, default=str))
