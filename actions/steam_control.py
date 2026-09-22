"""
actions/steam_control.py — drive the installed Steam desktop app.

Steam the *client* and the web are different things; this controls the client.
Instead of fragile screen coords it talks to Steam the way Steam itself expects:

  * the store API (store.steampowered.com) for search / price / status — a real,
    stable HTTP endpoint, no UI involved;
  * the local Steam install layout (`libraryfolders.vdf` + the
    `appmanifest_*.acf` files) to answer "is it installed, where, on which
    drive, how big" without opening anything;
  * `localconfig.vdf` under userdata to answer "does the user own it";
  * `steam://` deep links routed through the OS handler to make the *focused*
    client do the work (open store page, start install, launch game).

Because the client is being told what to do via its own protocol rather than by
poking pixels, this survives resolution changes, scaling and re-layouts. The one
thing it will NOT do is buy a game for you: purchases and anything high-impact
go through the on-screen confirmation gate first, and the checkout itself still
needs your own Steam session on the machine.
"""
from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import time
from pathlib import Path

try:
    from core import confirm
    _CONFIRM = True
except Exception:
    _CONFIRM = False

try:
    import requests
    _REQUESTS = True
except ImportError:
    _REQUESTS = False

_SYSTEM = platform.system()

try:
    from memory.config_manager import CURRENCY_SYMBOLS as _CURRENCY_SYMBOLS
except Exception:
    _CURRENCY_SYMBOLS = {"US": "$", "IN": "₹", "GB": "£", "EU": "€"}

# ISO currency codes → symbol, for prices the API reports (currency codes are
# NOT the country keys the config uses).
_CURRENCY_CODE_SYMBOLS = {
    "USD": "$", "CAD": "$", "AUD": "$", "MXN": "$", "NZD": "$",
    "EUR": "€", "GBP": "£", "INR": "₹", "JPY": "¥", "CNY": "¥",
    "RUB": "₽", "BRL": "R$", "KRW": "₩", "TRY": "₺",
}
_ZERO_DECIMAL_CODES = ("INR", "JPY", "KRW", "CLP", "ISK")

_STORE_SEARCH = "https://store.steampowered.com/api/storesearch/"
_STORE_DETAIL = "https://store.steampowered.com/api/appdetails"

# Keep the user several pages from an accidental buy even inside the app.
_PURCHASE_ACTION = "purchase"


# ── Local Steam installation ─────────────────────────────────────────────────

def _steam_root() -> Path | None:
    """The Steam install directory, or None if it cannot be found."""
    if _SYSTEM == "Windows":
        import winreg
        for hive, key in (
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam"),
            (winreg.HKEY_CURRENT_USER,  r"SOFTWARE\Valve\Steam"),
        ):
            try:
                with winreg.OpenKey(hive, key) as k:
                    val, _ = winreg.QueryValueEx(k, "InstallPath")
                    if val:
                        p = Path(val)
                        if p.exists():
                            return p
            except OSError:
                continue
        for candidate in (
            Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "Steam",
            Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Steam",
        ):
            if (candidate / "steam.exe").exists():
                return candidate
        return None
    if _SYSTEM == "Linux":
        for p in (Path.home() / ".steam" / "steam",
                  Path.home() / ".local" / "share" / "Steam",
                  Path("/usr") / "lib" / "steam"):
            if p.exists():
                return p
    if _SYSTEM == "Darwin":
        p = Path.home() / "Library" / "Application Support" / "Steam"
        if p.exists():
            return p
    return None


def _steam_exe(root: Path) -> Path | None:
    if not root:
        return None
    if _SYSTEM == "Windows":
        exe = root / "steam.exe"
        return exe if exe.exists() else None
    if _SYSTEM == "Linux":
        exe = shutil.which("steam")
        if exe:
            return Path(exe)
        for p in (root / "steam.sh", root / "steam"):
            if p.exists():
                return p
    if _SYSTEM == "Darwin":
        exe = Path("/Applications/Steam.app/Contents/MacOS/steam_osx")
        return exe if exe.exists() else None
    return None


def _steam_steamapps(root: Path) -> Path:
    """The PC/Steamapps dir; on macOS Steam keeps manifest under SteamApps."""
    return root / ("SteamApps" if _SYSTEM == "Darwin" else "steamapps")


def _ensure_steam() -> str:
    """Make sure the Steam client is running. Returns a human sentence."""
    root = _steam_root()
    if root is None:
        return "Steam does not appear to be installed on this computer."
    exe = _steam_exe(root)
    if exe is None:
        return "Steam is installed but its launcher could not be found."
    if _SYSTEM == "Windows":
        try:
            import psutil
            if any(p.info["name"] and "steam" in p.info["name"].lower()
                   for p in psutil.process_iter(["name"])):
                return "running"
        except Exception:
            pass
    if _SYSTEM == "Linux":
        proc = subprocess.run(["pgrep", "-f", "steam"], capture_output=True, text=True)
        if proc.returncode == 0:
            return "running"
    try:
        subprocess.Popen([str(exe)], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
        return "launched"
    except Exception as e:
        return f"failed_to_launch:{e}"


def _ensure_running() -> str | None:
    """Make sure Steam is running; return an error message or None."""
    state = _ensure_steam()
    if state in ("running", "launched"):
        return None
    return f"Could not start Steam ({state})"


def _open_uri(uri: str) -> bool:
    """Route a steam:// URI through the OS handler to the focused client."""
    try:
        if _SYSTEM == "Windows":
            os.startfile(uri)          # type: ignore[attr-defined]
        elif _SYSTEM == "Darwin":
            subprocess.Popen(["open", uri], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        else:
            subprocess.Popen(["xdg-open", uri], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False


# ── Steam URI builders (pure, unit-testable) ─────────────────────────────────

def _smuri(section: str, payload: str = "") -> str:
    if section == "search":
        from urllib.parse import quote
        return f"steam://url/StoreSearchPage/{quote(payload or '')}"
    if section == "store":
        return f"steam://store/{int(payload)}"
    if section == "install":
        return f"steam://install/{int(payload)}"
    if section == "run":
        return f"steam://run/{int(payload)}"
    if section == "nav":
        return f"steam://nav/{payload.replace(' ', '_').lower()}"
    return f"steam://{section}/{payload}"


# ── Minimal VDF reader (only the shapes this tool needs) ─────────────────────

def _vdf_parse(text: str) -> dict:
    """A tiny VDF parser: nested dicts for blocks, strings for scalars. Handles
    the usual wrapper form `"name" { ... }` at the root. Duplicate keys keep the
    last value, which is fine for every structure this tool reads."""
    s = text
    n = len(s)
    i = 0

    def skip_space():
        nonlocal i
        while i < n and s[i].isspace():
            i += 1

    def parse_item():
        """(key, value) where value is str or a nested dict; or None at EOF."""
        nonlocal i
        skip_space()
        if i >= n or s[i] != '"':
            return None
        j = s.find('"', i + 1)
        if j < 0:
            return None
        key = s[i + 1:j]
        i = j + 1
        skip_space()
        if i >= n:
            return key, ""
        if s[i] == "{":
            return key, parse_block()
        if s[i] == '"':
            j = s.find('"', i + 1)
            val = s[i + 1:j] if j >= 0 else ""
            i = j + 1 if j >= 0 else n
            return key, val
        j = i
        while j < n and s[j] not in " \t\r\n":
            j += 1
        val = s[i:j]
        i = j
        return key, val

    def parse_block():                       # called with s[i] at '{'
        nonlocal i
        i += 1
        out = {}
        while True:
            skip_space()
            if i >= n:
                return out
            if s[i] == "}":
                i += 1
                return out
            item = parse_item()
            if item is None:
                i += 1
                continue
            out[item[0]] = item[1]

    root = {}
    while True:
        skip_space()
        if i >= n:
            break
        item = parse_item()
        if item is None:
            break
        root[item[0]] = item[1]
    return root


def _vdf_unwrap(data: dict) -> dict:
    """Steam VDF files wrap everything in one named root ("libraryfolders",
    "AppState", "UserLocalConfigStore", ...). Peel that single level when it is
    the only top-level key so callers can read the real body."""
    if isinstance(data, dict) and len(data) == 1:
        only = next(iter(data.values()))
        if isinstance(only, dict):
            return only
    return data


def _library_folders(root: Path | None) -> list[dict]:
    """Configured library folders: [{id, path, apps:[appid...]}]. Paths are
    resolved on the filesystem."""
    if root is None:
        return []
    vdf = _steam_steamapps(root) / "libraryfolders.vdf"
    if not vdf.exists():
        return []
    try:
        data = _vdf_unwrap(_vdf_parse(
            vdf.read_text(encoding="utf-8", errors="replace")))
    except Exception:
        return []
    folders = []
    for key in data:
        if not str(key).isdigit():
            continue
        block = data[key]
        if not isinstance(block, dict):
            continue
        path = block.get("path")
        if not path:
            continue
        folder = Path(str(path).replace("\\\\", "\\"))
        apps_val = block.get("apps")
        apps = [int(k) for k in (apps_val if isinstance(apps_val, dict) else {})
                if str(k).isdigit()]
        try:
            resolved = str(folder.resolve())
        except Exception:
            resolved = str(folder)
        folders.append({"id": int(key), "path": resolved, "apps": apps})
    return folders


def _app_manifests(root: Path | None) -> list[dict]:
    """Every installed app across all library folders:
    [{appid, name, installdir, size, library, drive}]."""
    out = []
    for folder in _library_folders(root):
        apps_dir = Path(folder["path"]) / "steamapps"
        if not apps_dir.exists():
            continue
        for mf in apps_dir.glob("appmanifest_*.acf"):
            try:
                data = _vdf_unwrap(_vdf_parse(
                    mf.read_text(encoding="utf-8", errors="replace")))
            except Exception:
                continue
            rec = {
                "appid": None, "name": None, "installdir": None, "size": 0,
            }
            for key in tuple(rec):
                vkey = "SizeOnDisk" if key == "size" else key
                if vkey not in data:
                    continue
                val = data[vkey]
                if isinstance(val, str):
                    rec[key] = int(val) if key in ("appid", "size") else val
            if rec["appid"]:
                rec["library"] = folder["path"]
                rec["drive"] = os.path.splitdrive(folder["path"])[0] or folder["path"]
                out.append(rec)
    return out


def _owned_appids(root: Path | None) -> set[int]:
    """App ids present in the logged-in user's localconfig.vdf (a decent proxy
    for ownership). Falls back to everything installed when none can be read."""
    if root is None:
        return set()
    userdata = root / "userdata"
    owned = set()
    if userdata.exists():
        for cfg in userdata.glob("*/config/localconfig.vdf"):
            try:
                data = _vdf_parse(cfg.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                continue
            software = data.get("UserLocalConfigStore") or data
            if isinstance(software, dict):
                steam = ((software.get("Software") or {}).get("Valve") or {}).get("Steam") or {}
                apps = steam.get("Apps") if isinstance(steam, dict) else None
                if isinstance(apps, dict):
                    owned |= {int(k) for k in apps if str(k).isdigit()}
    if not owned:
        owned = {a["appid"] for a in _app_manifests(root)}
    return owned


# ── Store API ────────────────────────────────────────────────────────────────

def _cc() -> str:
    """Country/region key for store prices, from config; 'US' fallback."""
    try:
        from memory.config_manager import get_currency_code
        return get_currency_code()
    except Exception:
        return "US"


def _fmt_local_price(final, currency: str = "") -> str:
    """Format a store amount (smallest unit, e.g. paise) in the user's money,
    e.g. ₹ 1,300 or $ 59.99."""
    try:
        amount = float(final) / 100.0
    except (TypeError, ValueError):
        return ""
    code = (currency or "").upper()
    symbol = _CURRENCY_CODE_SYMBOLS.get(code) or _CURRENCY_SYMBOLS.get(code)
    if not symbol:
        try:
            from memory.config_manager import currency_symbol
            symbol = currency_symbol(_cc())
        except Exception:
            symbol = "$"
    if code in _ZERO_DECIMAL_CODES:
        return f"{symbol} {amount:,.0f}"
    return f"{symbol} {amount:,.2f}"


def _store_search(term: str, n: int = 5, cc: str = "") -> list[dict]:
    """Search the Steam store for `term` in the user's currency. Returns list of
    {name, appid, price, is_free, tiny_image, currency} best match first. Empty
    on any failure so callers degrade to local knowledge."""
    if not _REQUESTS or not term.strip():
        return []
    for cc_try in [cc or _cc(), "US"]:
        try:
            r = requests.get(_STORE_SEARCH,
                             params={"term": term, "cc": cc_try, "l": "english"},
                             timeout=12)
            r.raise_for_status()
            items = (r.json() or {}).get("items") or []
        except Exception as e:
            print(f"[steam_control] store search failed: {e}")
            items = []
        if items:
            return _search_to_rows(items, n)
    return []


def _search_to_rows(items: list[dict], n: int = 5) -> list[dict]:
    out = []
    for it in items[:max(1, n)]:
        price = (it.get("price") or {}) if isinstance(it.get("price"), dict) else {}
        fin = price.get("final")
        cur = str(price.get("currency") or "")
        out.append({
            "name": it.get("name") or "",
            "appid": int(it.get("id") or 0),
            "price": _fmt_local_price(fin, cur) if isinstance(fin, (int, float)) else "",
            "is_free": bool(it.get("is_free")),
            "tiny_image": it.get("tiny_image") or "",
            "currency": cur,
        })
    return [o for o in out if o["appid"]]


def _app_details(appid: int) -> dict:
    """Details for one app, or {} on failure."""
    if not _REQUESTS:
        return {}
    try:
        r = requests.get(_STORE_DETAIL,
                         params={"appids": appid, "cc": _cc(), "l": "english"},
                         timeout=12)
        r.raise_for_status()
        data = r.json()
        body = data.get(str(appid)) or {}
        if not body.get("success"):
            return {}
        d = body.get("data") or {}
        price = (d.get("price_overview") or {}) if isinstance(d.get("price_overview"), dict) else {}
        return {
            "name": d.get("name") or "",
            "type": d.get("type") or "",
            "is_free": bool(d.get("is_free")),
            "price": price.get("final_formatted") or "",
            "short": (d.get("short_description") or "")[:220],
            "metacritic": (d.get("metacritic") or {}).get("score"),
            "genres": [g.get("description") for g in d.get("genres") or []],
            "release": (d.get("release_date") or {}).get("date"),
        }
    except Exception as e:
        print(f"[steam_control] appdetails failed: {e}")
        return {}


# ── Resolution helpers ───────────────────────────────────────────────────────

def _drive_of(path: str) -> str:
    return (os.path.splitdrive(path)[0] or path).rstrip("\\/").upper()


def _resolve_appid(parameters: dict, root: Path | None) -> int | None:
    """appid param wins; then exact name in installed/owned; then store search's
    best match. Returns None when genuinely unknown."""
    pid = parameters.get("appid")
    try:
        if pid not in (None, "", 0):
            return int(pid)
    except (TypeError, ValueError):
        pass
    query = str(parameters.get("name") or parameters.get("query") or "").strip()
    if not query:
        return None
    query_l = query.lower()
    for rec in _app_manifests(root):
        if (rec.get("name") or "").lower() == query_l:
            return rec["appid"]
    for it in _store_search(query, n=1):
        return it["appid"]
    return None


def _match_by_name(query: str, root: Path | None):
    """Local match: returns the app manifest record or None."""
    query_l = (query or "").strip().lower()
    if not query_l:
        return None
    for rec in _app_manifests(root):
        if (rec.get("name") or "").lower() == query_l:
            return rec
        if query_l in (rec.get("name") or "").lower():
            return rec
    return None


# ── Actions ──────────────────────────────────────────────────────────────────

def _fmt_gb(size: int) -> str:
    return f"{size / (1024 ** 3):.1f} GB" if size else "unknown size"


def _game_status_text(parameters: dict, root: Path | None) -> str:
    query = str(parameters.get("name") or parameters.get("query") or "").strip()
    pid = parameters.get("appid")
    appid = _resolve_appid(parameters, root)
    local = _match_by_name(query, root) if query else None
    lines = []
    details = _app_details(appid) if appid else {}
    name = (details.get("name") or (local.get("name") if local else None)
            or query or f"appid {pid}")
    lines.append(f"{name}" + (f"  (appid {appid})" if appid else " (appid unknown)"))
    if not appid:
        lines.append("Could not resolve this on the Steam store. Check the name "
                     "or give me the app id.")
        return "\n".join(lines)
    if details.get("price"):
        lines.append(details["price"])
    elif details.get("is_free"):
        lines.append("Free to play")
    else:
        lines.append("Price unavailable")
    if details.get("type"):
        lines.append(f"Type: {details['type']}")
    if details.get("release"):
        lines.append(f"Release: {details['release']}")
    if (details.get("metacritic")):
        lines.append(f"Metacritic: {details['metacritic']}")
    owned = appid in _owned_appids(root)
    lines.append("Owned" if owned else "Not owned in this Steam account")
    local = _match_by_name(name, root) or local
    if local:
        lines.append(f"Installed on {local['drive']} ({_fmt_gb(local['size'])}), "
                     f"library at {local['library']}")
    elif owned:
        lines.append("Owned but not installed")
    return "\n".join(lines)


def _search_text(parameters: dict) -> str:
    query = str(parameters.get("query") or parameters.get("name") or "").strip()
    if not query:
        return "Give me a game name to search for."
    finder = _store_search(query, n=5)
    if not finder:
        return (f"No results from the Steam store for '{query}'. Check that "
                f"you are online, or try a different name.")
    lines = [f"Top Steam results for '{query}':"]
    for i, it in enumerate(finder, 1):
        price = it["price"] or ("Free" if it["is_free"] else "Price unknown")
        lines.append(f"{i}. {it['name']} — {price}  (appid {it['appid']})")
    lines.append("Say the number or name to open, install or launch it.")
    return "\n".join(lines)


def _open_app(parameters: dict, viewer: bool) -> str:
    query = str(parameters.get("name") or parameters.get("query") or "").strip()
    appid = _resolve_appid(parameters, _steam_root())
    if not appid:
        return (f"Could not resolve '{query or parameters.get('appid')}' to a Steam "
                f"app id. Search first, then try again.")
    state = _ensure_running()
    if state:
        return state
    _open_uri(_smuri("store", str(appid)))
    return (f"Opening the Steam page for {query or 'this game'} in the Steam app.")


def _install_app(parameters: dict) -> str:
    root = _steam_root()
    if root is None:
        return "Steam does not appear to be installed on this computer."
    query = str(parameters.get("name") or parameters.get("query") or "").strip()
    appid = _resolve_appid(parameters, root)
    if not appid:
        return (f"Could not resolve '{query or parameters.get('appid')}' to a Steam "
                f"app id. Search first, then try again.")
    state = _ensure_running()
    if state:
        return state
    owned = appid in _owned_appids(root)
    _open_uri(_smuri("install", str(appid)))
    if not owned:
        return (f"'{query or appid}' is not owned yet, so Steam will ask you to "
                f"complete the free purchase/licence before it installs.")
    return (f"Steam is now installing {query or appid} in the default library. "
            f"I'll watch the prompt if it asks anything.")

def _install_to_app(parameters: dict) -> str:
    root = _steam_root()
    if root is None:
        return "Steam does not appear to be installed on this computer."
    drive = str(parameters.get("drive") or "").strip().upper()
    query = str(parameters.get("name") or parameters.get("query") or "").strip()
    appid = _resolve_appid(parameters, root)
    if not appid:
        return f"Could not resolve '{query or parameters.get('appid')}' to a Steam app id."
    if not drive:
        return "Tell me which drive, e.g. 'install it on D drive' — say just the letter."
    folders = _library_folders(root)
    matches = [f for f in folders if _drive_of(f["path"]) == drive or f["path"].upper().startswith(drive + ":")]
    if not matches:
        available = ", ".join(sorted({_drive_of(f["path"]) for f in folders})) or "none"
        msg = (f"{drive} has no Steam library folder yet, so I cannot aim the "
               f"install there. Configured libraries: {available}. I can still "
               f"start the install in the default library if you want.")
        return msg
    state = _ensure_running()
    if state:
        return state
    _open_uri(_smuri("install", str(appid)))
    target = matches[0]["path"]
    return (f"Starting install of {query or appid}. {drive} has a library at "
            f"{target}, so choose that folder in the Steam dialog (Steam lets "
            f"you pick the library at install time). Starting it now.")


def _launch_app(parameters: dict) -> str:
    root = _steam_root()
    if root is None:
        return "Steam does not appear to be installed on this computer."
    query = str(parameters.get("name") or parameters.get("query") or "").strip()
    appid = _resolve_appid(parameters, root)
    if not appid:
        return f"Could not resolve '{query or parameters.get('appid')}' to a Steam app id."
    state = _ensure_running()
    if state:
        return state
    _open_uri(_smuri("run", str(appid)))
    return f"Launching {query or appid} through Steam."


def _list_library(parameters: dict) -> str:
    root = _steam_root()
    if root is None:
        return "Steam does not appear to be installed on this computer."
    games = _app_manifests(root)
    if not games:
        return "No installed games found in any Steam library."
    games.sort(key=lambda g: (g.get("name") or "").lower())
    lines = [f"{len(games)} installed game(s):"]
    for g in games:
        lines.append(f"• {g.get('name')}  ({_fmt_gb(g.get('size') or 0)}, "
                     f"{g['drive']} — {g.get('library')})")
    return "\n".join(lines)


def _list_libraries(parameters: dict) -> str:
    root = _steam_root()
    if root is None:
        return "Steam does not appear to be installed on this computer."
    folders = _library_folders(root)
    if not folders:
        return "No Steam library folders could be read."
    lines = ["Steam library folders:"]
    for f in folders:
        try:
            free = shutil.disk_usage(Path(f["path"])).free
            free_s = f", {free / (1024 ** 3):.1f} GB free"
        except Exception:
            free_s = ""
        lines.append(f"• {f['path']}  ({len(f.get('apps') or [])} apps{free_s})")
    return "\n".join(lines)


def _purchase_app(parameters: dict) -> str:
    root = _steam_root()
    if root is None:
        return "Steam does not appear to be installed on this computer."
    query = str(parameters.get("name") or parameters.get("query") or "").strip()
    appid = _resolve_appid(parameters, root)
    if not appid:
        return f"Could not resolve '{query or parameters.get('appid')}' to a Steam app id."
    if not _CONFIRM or not hasattr(confirm, "request"):
        return ("Buying needs the on-screen confirmation, which is unavailable "
                "right now, so I have not done it.")
    details = _app_details(appid)
    price = details.get("price") or ("free" if details.get("is_free") else "unknown price")
    title = f"Buy {details.get('name') or query or appid} on Steam"
    detail = (f"{details.get('name') or query} — {price}. This opens the Steam "
              f"store page in the client; the purchase completes on your own "
              f"Steam account. Nothing is purchased by me.")
    return confirm.request("steam_control-purchase", title, detail,
                           lambda: _do_purchase(appid, details.get("name") or query))


def _do_purchase(appid: int, name: str) -> str:
    _ensure_steam()
    ok = _open_uri(_smuri("store", str(appid)))
    return f"Opened {name} on the Steam store in the client." if ok else "open failed"


# ── Entry point ──────────────────────────────────────────────────────────────

def steam_control(
    parameters: dict = None,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    params = parameters or {}

    def _take(*keys):
        for k in keys:
            if params.get(k) not in (None, ""):
                return params.get(k)
        return ""

    action = _take("action", "command", "what").strip().lower().replace(" ", "_")
    action = {
        "search": "search", "find_game": "search", "find": "search",
        "lookup": "search", "price": "search",
        "game": "game", "check": "game", "status": "game", "info": "game",
        "page": "open_page", "open": "open_page", "open_page": "open_page",
        "install": "install", "download": "install", "install_game": "install",
        "install_to": "install_to", "install_on": "install_to",
        "launch": "launch", "run": "launch", "play": "launch",
        "library": "library", "games": "library", "installed": "library",
        "libraries": "libraries", "library_folders": "libraries",
        "purchase": "purchase", "buy": "purchase",
    }.get(action, action)

    if player:
        player.write_log(f"[steam_control] {action}")
    print(f"[steam_control] action={action}")

    try:
        if action == "search":
            return _search_text(params)
        if action == "game":
            return _game_status_text(params, _steam_root())
        if action == "open_page":
            return _open_app(params, viewer=True)
        if action == "install":
            return _install_app(params)
        if action == "install_to":
            return _install_to_app(params)
        if action == "launch":
            return _launch_app(params)
        if action == "library":
            return _list_library(params)
        if action == "libraries":
            return _list_libraries(params)
        if action == "purchase":
            return _purchase_app(params)
        return ("steam_control can: search, game/status, open_page, install, "
                "install_to <drive>, launch, library, libraries, purchase.")
    except Exception as e:
        return f"steam_control '{action}' failed: {e}"


# ── Tool declaration (auto-discovered by core/action_loader.py) ──────────────
TOOL = {
    "name": "steam_control",
    "description": "Controls the installed Steam desktop app for games: search the Steam store, check a game's price/ownership/install status, open its page in the Steam client, install or launch it, install to a chosen drive, list installed games and library folders, and buy a game (buying always asks for on-screen confirmation first). Use this for any Steam game request. Uses the Steam store API and the client's own steam:// deep links — no blind clicks.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "enum": ["search", "game", "open_page", "install", "install_to",
                         "launch", "library", "libraries", "purchase"],
                "description": "What to do on Steam.",
            },
            "query": {
                "type": "STRING",
                "description": "Game name to search/resolve (search / game / open_page / install / install_to / launch / purchase).",
            },
            "name": {
                "type": "STRING",
                "description": "Exact game name (same purpose as query; whichever you prefer).",
            },
            "appid": {
                "type": "NUMBER",
                "description": "Steam app id when you already know it (game / open_page / install / launch / purchase).",
            },
            "drive": {
                "type": "STRING",
                "description": "Drive letter for install_to, e.g. 'D' (install_to).",
            },
        },
        "required": ["action"],
    },
    "handler": steam_control,
}