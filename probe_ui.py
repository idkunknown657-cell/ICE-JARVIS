"""Probe the loaded page: did the scripts run, what's missing, any errors?"""
import sys, threading, time, json
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import webui
from webui import JarvisUI

out = {}
ui = JarvisUI("face.png")

def driver():
    time.sleep(4.0)
    w = ui._win.win
    def js(e):
        try:
            return w.evaluate_js(e)
        except Exception as ex:
            return "JSERR:" + repr(ex)
    out["title"] = js("document.title")
    out["pywebview_type"] = js("typeof window.pywebview")
    out["pywebview_api"] = js("typeof window.pywebview !== 'undefined' ? typeof window.pywebview.api : 'no-pywebview'")
    out["jarvisEvent"] = js("typeof window.__jarvisEvent")
    out["JarvisAvatar"] = js("typeof window.JarvisAvatar")
    out["mdRender"] = js("typeof window.mdRender")
    out["mock_flag"] = js("String(window.__MOCK__)")
    out["script_errs"] = js("window.__errs ? JSON.stringify(window.__errs) : 'none-captured'")
    out["dom_ready"] = js("String(!!document.getElementById('chatList'))")
    out["nav_count"] = js("String(document.querySelectorAll('.nav-btn').length)")
    # try calling the API directly from JS
    out["direct_api_call"] = js(
        "(window.pywebview && window.pywebview.api) ? 'has-api' : 'no-api'")
    try:
        w.destroy()
    except Exception:
        pass

def _watchdog():
    time.sleep(30)
    print("WATCHDOG:", json.dumps(out, indent=2, default=str))
    import os as _os; _os._exit(2)

threading.Thread(target=_watchdog, daemon=True).start()
threading.Thread(target=driver, daemon=True).start()
ui.root.mainloop()
print("PROBE:", json.dumps(out, indent=2, default=str))
