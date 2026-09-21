"""Focused check: API settings page renders provider cards (async, real config)."""
import sys, threading, time, json
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import webui
from webui import JarvisUI

out = {}
ui = JarvisUI("face.png")

def driver():
    for _ in range(120):
        if ui._win._ready:
            break
        time.sleep(0.25)
    w = ui._win.win
    def js(e):
        try:
            return w.evaluate_js(e)
        except Exception as ex:
            return "JSERR:" + repr(ex)
    js("document.querySelector('.nav-btn[data-view=\"settings\"]').click()")
    time.sleep(0.2)
    js("document.querySelector('#settingsNav [data-page=\"api\"]').click()")
    time.sleep(1.5)
    out["provider_cards"] = js("document.querySelectorAll('#settingsBody .provider-card').length")
    out["gemini_card_text"] = js("(document.querySelector('#settingsBody .provider-card .provider-name')||{}).textContent || 'none'")
    out["key_field_len"] = js("String((document.querySelector('#settingsBody .provider-card input')||{value:''}).value.length)")
    out["has_test_btn"] = js("String(!!Array.from(document.querySelectorAll('#settingsBody button')).find(b => b.textContent === 'Test'))")
    # voice page devices dropdowns populated
    js("document.querySelector('#settingsNav [data-page=\"voice\"]').click()")
    time.sleep(1.2)
    out["mic_options"] = js("document.querySelectorAll('#settingsBody select')[0] ? document.querySelectorAll('#settingsBody select')[0].options.length : -1")
    try:
        w.destroy()
    except Exception:
        pass

def _watchdog():
    time.sleep(40)
    print("WATCHDOG:", json.dumps(out, indent=2, default=str))
    import os as _os; _os._exit(2)

threading.Thread(target=_watchdog, daemon=True).start()
threading.Thread(target=driver, daemon=True).start()
ui.root.mainloop()
print("API-PAGE:", json.dumps(out, indent=2, default=str))
