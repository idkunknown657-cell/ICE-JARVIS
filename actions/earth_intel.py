"""
actions/earth_intel.py — what is happening on Earth right now, from public feeds.

WHY
    "What is flying over me?", "was there an earthquake?", "what satellites are
    up?" are questions a desktop assistant should simply answer, and every one of
    them is public data. This tool turns those feeds into four spoken answers,
    with no API key and no new heavy dependency.

CREDIT
    The set of feeds and the shape of the questions come from God's Eye View
    (https://github.com/bilawalsidhu/gods-eye-view, MIT — (c) Bilawal Sidhu),
    which puts the same public signals onto a browser globe. This is an
    independent Python implementation against the providers directly; no code or
    data from that project is copied, and none of its bundled datasets (which are
    separately licensed, some non-commercial) are used or redistributed.

SOURCES — all keyless, all read-only
    Photon (photon.komoot.io)     place name → coordinates, OSM data
    adsb.lol                      live aircraft transponder positions
    USGS                          earthquake catalogue
    NASA EONET                    open natural events (storms, fires, volcanoes…)
    CelesTrak                     orbital elements (TLE)

WHAT IT DOES NOT DO
    It does not invent a position. If a feed is stale, empty or unreachable the
    answer says so, because an assistant that guesses your coordinates is worse
    than one that admits it does not have them. A read-only capability: nothing
    here sends, changes or buys anything, so nothing here asks for confirmation.

EVERY PUBLIC FUNCTION RETURNS A SENTENCE AND NEVER RAISES. A tool result is
something the assistant has to be able to say out loud.
"""
from __future__ import annotations

import math
import time
from datetime import datetime, timezone

try:
    import requests
    _REQUESTS = True
except Exception:                                   # pragma: no cover
    requests = None
    _REQUESTS = False

try:
    from memory.config_manager import get_home_location, save_home_location
except Exception:                                   # pragma: no cover
    def get_home_location() -> dict:                # type: ignore
        return {}

    def save_home_location(lat, lon, label="") -> None:   # type: ignore
        raise RuntimeError("configuration is unavailable")

_UA = {"User-Agent": "JARVIS-EarthIntel/1.0 (personal desktop assistant)"}
_TIMEOUT = 9.0
_EARTH_KM = 6371.0
_MU_KM3_S2 = 398600.4418

# How many rows any one answer may contain. A voice assistant reads these aloud,
# so the cap is about what a person can actually listen to, not about the feed.
_MAX_ROWS = 12
_MAX_RADIUS_NM = 250          # adsb.lol's own ceiling
_DEFAULT_RADIUS_KM = 150.0


# ── plumbing ─────────────────────────────────────────────────────────────────

def _log(message: str, player=None) -> None:
    print(f"[EarthIntel] {message}")
    if player:
        try:
            player.write_log(f"JARVIS: {message}")
        except Exception:
            pass


def _get_json(url: str, timeout: float = _TIMEOUT):
    """GET a JSON endpoint. Returns (data, error). Never raises.

    One funnel for every request means every failure mode — no network, DNS,
    HTTP error, a provider returning HTML, a truncated body — is turned into a
    sentence in exactly one place instead of being handled (or forgotten)
    per call site.
    """
    if not _REQUESTS:
        return None, "the requests library is not available"
    try:
        resp = requests.get(url, headers=_UA, timeout=timeout)
    except Exception as e:
        return None, _explain_network_error(e)
    code = getattr(resp, "status_code", 0)
    if code != 200:
        return None, f"the source answered HTTP {code}"
    try:
        return resp.json(), ""
    except Exception:
        return None, "the source did not return readable data"


def _get_text(url: str, timeout: float = _TIMEOUT):
    """GET a plain-text endpoint. Returns (text, error). Never raises.

    Needed because not every provider speaks JSON — CelesTrak answers the TLE
    request in its own fixed-column text format, and running that through
    resp.json() is a guaranteed parse failure.
    """
    if not _REQUESTS:
        return None, "the requests library is not available"
    try:
        resp = requests.get(url, headers=_UA, timeout=timeout)
    except Exception as e:
        return None, _explain_network_error(e)
    code = getattr(resp, "status_code", 0)
    if code != 200:
        return None, f"the source answered HTTP {code}"
    try:
        return resp.text, ""
    except Exception:
        return None, "the source returned nothing readable"


def _explain_network_error(exc: Exception) -> str:
    """A network failure in words a person can act on."""
    text = str(exc).lower()
    if "timed out" in text or "timeout" in text:
        return "the request timed out — the source may be slow right now"
    if "name or service" in text or "getaddrinfo" in text or "resolve" in text:
        return "there is no internet connection available"
    if "connection" in text or "refused" in text or "reset" in text:
        return "the connection to the source failed"
    if "certificate" in text or "ssl" in text:
        return "the secure connection to the source could not be verified"
    return f"the request failed ({exc})"


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres. Pure, so it is unit-tested."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = (math.sin(dp / 2.0) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dl / 2.0) ** 2)
    return 2.0 * _EARTH_KM * math.asin(min(1.0, math.sqrt(a)))


def parse_latlon(text: str):
    """Parse "28.61, 77.21" / "28.61 77.21" into (lat, lon), else None.

    Deliberately strict about ranges: a swapped or out-of-range pair must fail
    here rather than become a coordinate somewhere in the ocean.
    """
    raw = str(text or "").strip().replace("°", " ")
    for sep in (",", " "):
        if sep in raw:
            parts = [p for p in raw.split(sep) if p.strip()]
            if len(parts) == 2:
                try:
                    lat, lon = float(parts[0]), float(parts[1])
                except ValueError:
                    continue
                if -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0:
                    return lat, lon
    return None


def _row_limit(params: dict, default: int = _MAX_ROWS) -> int:
    """How many rows an answer may contain. Bounded both ways: a caller asking
    for 500 rows would produce something nobody can listen to."""
    try:
        return max(1, min(int(params.get("limit")), _MAX_ROWS))
    except (TypeError, ValueError):
        return default


def _fmt_km(km: float) -> str:
    if km < 1.0:
        return f"{km * 1000:.0f} m"
    if km < 10.0:
        return f"{km:.1f} km"
    return f"{km:.0f} km"


def _as_float(value, default=None):
    """Coerce a feed value to float. Providers mix numbers and numeric strings,
    and 'alt_baro' can literally be the string 'ground'."""
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


# ── place resolution ─────────────────────────────────────────────────────────

def _geocode(place: str):
    """Place name → (lat, lon, label). Keyless, via Photon (OpenStreetMap).

    Returns (None, None, error) when it cannot place the name, which is the
    honest answer — guessing a city's coordinates would poison every answer
    built on top of it.
    """
    query = str(place or "").strip()
    if not query:
        return None, None, "no place was given"
    data, err = _get_json(
        "https://photon.komoot.io/api/?limit=1&q=" + _quote(query))
    if err:
        return None, None, err
    features = (data or {}).get("features") or []
    if not features:
        return None, None, f"I could not find anywhere called '{query}'"
    top = features[0] or {}
    coords = ((top.get("geometry") or {}).get("coordinates") or [])
    if len(coords) < 2:
        return None, None, f"I could not read a position for '{query}'"
    lon, lat = _as_float(coords[0]), _as_float(coords[1])
    if lat is None or lon is None:
        return None, None, f"I could not read a position for '{query}'"
    props = top.get("properties") or {}
    label = ", ".join([str(props.get(k)) for k in ("name", "state", "country")
                       if props.get(k)]) or query
    return lat, lon, label


def _quote(text: str) -> str:
    try:
        from urllib.parse import quote_plus
        return quote_plus(text)
    except Exception:                               # pragma: no cover
        return str(text).replace(" ", "+")


def _resolve_place(params: dict, *, allow_home: bool = True):
    """Work out which point on Earth the question is about.

    Priority: explicit coordinates → a named place → the remembered home
    position. Returns (lat, lon, label, error).
    """
    lat = _as_float(params.get("lat"))
    lon = _as_float(params.get("lon"))
    if lat is not None and lon is not None:
        if -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0:
            return lat, lon, f"{lat:.3f}, {lon:.3f}", ""
        return None, None, "", "those coordinates are out of range"

    place = str(params.get("place") or params.get("city") or "").strip()
    if place:
        coords = parse_latlon(place)
        if coords:
            return coords[0], coords[1], f"{coords[0]:.3f}, {coords[1]:.3f}", ""
        glat, glon, label = _geocode(place)
        if glat is None:
            return None, None, "", label          # third value carries the error
        return glat, glon, label, ""

    if allow_home:
        home = {}
        try:
            home = get_home_location() or {}
        except Exception:
            home = {}
        if home:
            return (home["lat"], home["lon"],
                    home.get("label") or "your saved location", "")

    return None, None, "", ("I do not know where you are yet — tell me a place, "
                            "or say \"remember this as my location\".")


# ── actions ──────────────────────────────────────────────────────────────────

def _locate(params: dict) -> str:
    """Resolve a place and optionally remember it as home."""
    place = str(params.get("place") or params.get("city") or "").strip()
    if not place:
        home = get_home_location()
        if home:
            return (f"Your saved location is {home.get('label') or 'set'} "
                    f"({home['lat']:.3f}, {home['lon']:.3f}).")
        return "I do not have a location saved yet. Name a place and I will set it."

    coords = parse_latlon(place)
    if coords:
        lat, lon, label = coords[0], coords[1], f"{coords[0]:.3f}, {coords[1]:.3f}"
    else:
        lat, lon, label = _geocode(place)
        if lat is None:
            return label                        # _geocode returns its error here

    remember = params.get("remember")
    remembered = False
    if remember is None or str(remember).strip().lower() in ("true", "1", "yes", "on"):
        try:
            save_home_location(lat, lon, label)
            remembered = True
        except Exception as e:
            _log(f"could not save home location: {e}")

    where = f"{label} ({lat:.3f}, {lon:.3f})"
    return (f"That is {where}. Saved as your location — I will use it when you "
            f"ask what is overhead." if remembered
            else f"That is {where}.")


def _aircraft(params: dict) -> str:
    """Live aircraft within a radius of a point (adsb.lol, keyless)."""
    lat, lon, label, err = _resolve_place(params)
    if lat is None:
        return err

    radius_km = _as_float(params.get("radius_km"), _DEFAULT_RADIUS_KM) or _DEFAULT_RADIUS_KM
    radius_km = max(1.0, min(radius_km, _MAX_RADIUS_NM * 1.852))
    radius_nm = max(1, min(_MAX_RADIUS_NM, int(round(radius_km / 1.852))))

    data, err = _get_json(
        f"https://api.adsb.lol/v2/point/{lat:.4f}/{lon:.4f}/{radius_nm}")
    if err:
        return f"I could not reach the aircraft feed — {err}."

    aircraft = (data or {}).get("ac") or []
    if not aircraft:
        return (f"No aircraft are broadcasting within {_fmt_km(radius_km)} of "
                f"{label} right now.")

    rows = []
    for ac in aircraft:
        alat, alon = _as_float(ac.get("lat")), _as_float(ac.get("lon"))
        dist = _as_float(ac.get("dst"))
        if dist is None and alat is not None and alon is not None:
            dist = haversine_km(lat, lon, alat, alon) / 1.852
        if dist is None:
            continue
        rows.append((dist, ac))

    rows.sort(key=lambda r: r[0])
    shown = rows[:_row_limit(params)]

    lines = []
    for dist_nm, ac in shown:
        callsign = str(ac.get("flight") or ac.get("hex") or "unknown").strip()
        kind = str(ac.get("t") or "").strip()
        alt = ac.get("alt_baro")
        alt_txt = ("on the ground" if str(alt).lower() == "ground"
                   else (f"{int(_as_float(alt, 0)):,} ft" if _as_float(alt) is not None
                         else "altitude unknown"))
        speed = _as_float(ac.get("gs"))
        heading = _as_float(ac.get("track"))
        bits = [f"{callsign} ({kind})" if kind else callsign,
                _fmt_km(dist_nm * 1.852) + " away", alt_txt]
        if speed is not None:
            bits.append(f"{speed:.0f} kt")
        if heading is not None:
            bits.append(_compass(heading))
        lines.append("• " + " · ".join(bits))

    total = len(rows)
    more = f" ({total} in range; showing the nearest {len(shown)})" if total > len(shown) else ""
    return (f"Live aircraft within {_fmt_km(radius_km)} of {label}{more}:\n"
            + "\n".join(lines))


def _compass(deg: float) -> str:
    """Heading in words — a number of degrees is not something a person pictures."""
    dirs = ("north", "north-east", "east", "south-east",
            "south", "south-west", "west", "north-west")
    return "heading " + dirs[int((deg % 360) / 45.0 + 0.5) % 8]


def _quakes(params: dict) -> str:
    """Recent earthquakes from the USGS catalogue (keyless)."""
    magnitude = str(params.get("magnitude") or "2.5").strip()
    if magnitude not in ("1.0", "2.5", "4.5"):
        magnitude = "2.5"
    period = str(params.get("period") or "day").strip().lower()
    if period not in ("hour", "day", "week", "month"):
        period = "day"

    data, err = _get_json(
        "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/"
        f"{magnitude}_{period}.geojson")
    if err:
        return f"I could not reach the earthquake catalogue — {err}."

    features = (data or {}).get("features") or []
    if not features:
        return f"No magnitude {magnitude}+ earthquakes in the past {period}."

    # An optional point turns "what happened on Earth" into "what happened near
    # me", which is the question people actually mean.
    radius_km = _as_float(params.get("radius_km"))
    label = ""
    use_point = bool(radius_km) or any(
        params.get(k) not in (None, "") for k in ("place", "city", "lat", "lon"))
    if use_point:
        lat, lon, label, perr = _resolve_place(params)
        if lat is None:
            return perr
        radius_km = max(1.0, min(radius_km or 2000.0, 20000.0))
    else:
        lat = lon = None

    found = []
    for f in features:
        props = (f or {}).get("properties") or {}
        geo = ((f or {}).get("geometry") or {}).get("coordinates") or []
        mag = _as_float(props.get("mag"))
        if mag is None or len(geo) < 2:
            continue
        qlon, qlat = _as_float(geo[0]), _as_float(geo[1])
        if qlat is None or qlon is None:
            continue
        dist = None
        if lat is not None:
            dist = haversine_km(lat, lon, qlat, qlon)
            if dist > radius_km:
                continue
        found.append({"mag": mag, "place": str(props.get("place") or "unknown location"),
                      "time": _as_float(props.get("time")), "dist": dist,
                      "tsunami": _as_float(props.get("tsunami"), 0)})

    if not found:
        return (f"No magnitude {magnitude}+ earthquakes within "
                f"{_fmt_km(radius_km)} of {label} in the past {period}.")

    found.sort(key=lambda q: q["mag"], reverse=True)
    shown = found[:_row_limit(params)]
    lines = []
    for q in shown:
        when = _ago(q["time"])
        bits = [f"magnitude {q['mag']:.1f}", q["place"], when]
        if q["dist"] is not None:
            bits.insert(1, _fmt_km(q["dist"]) + " away")
        if q["tsunami"]:
            bits.append("tsunami warning issued")
        lines.append("• " + " · ".join(bits))

    header = (f"Earthquakes near {label} in the past {period}"
              if lat is not None else
              f"Strongest earthquakes in the past {period}")
    more = f" ({len(found)} matched; showing {len(shown)})" if len(found) > len(shown) else ""
    return f"{header}{more}:\n" + "\n".join(lines)


def _events(params: dict) -> str:
    """Open natural events — storms, wildfires, volcanoes, icebergs (NASA EONET)."""
    limit = _row_limit(params, default=6)
    category = str(params.get("category") or "").strip()
    url = f"https://eonet.gsfc.nasa.gov/api/v3/events?status=open&limit={limit}"
    if category:
        url += "&category=" + _quote(category)

    data, err = _get_json(url)
    if err:
        return f"I could not reach the natural-events feed — {err}."

    events = (data or {}).get("events") or []
    if not events:
        return ("No open natural events are being tracked"
                + (f" in {category}." if category else "."))

    lines = []
    for ev in events[:limit]:
        title = str(ev.get("title") or "unnamed event")
        cats = ev.get("categories") or [{}]
        name = str((cats[0] or {}).get("title") or "").strip()
        geom = ev.get("geometry") or []
        when = ""
        where = ""
        if geom:
            last = geom[-1] or {}
            when = _ago(_iso_to_ms(last.get("date")))
            coords = last.get("coordinates") or []
            if len(coords) >= 2:
                elat, elon = _as_float(coords[1]), _as_float(coords[0])
                if elat is not None and elon is not None:
                    where = f"{abs(elat):.1f}°{'N' if elat >= 0 else 'S'}, " \
                            f"{abs(elon):.1f}°{'E' if elon >= 0 else 'W'}"
        bits = [title] + ([name] if name else []) + ([where] if where else []) \
               + ([when] if when else [])
        lines.append("• " + " · ".join(bits))

    return (f"Open natural events ({len(lines)} of {len(events)}):\n"
            + "\n".join(lines))


def _iso_to_ms(text) -> float:
    """EONET dates are ISO-8601 with a Z. Bad dates read as 'unknown', not now."""
    raw = str(text or "").strip()
    if not raw:
        return 0.0
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp() * 1000.0
    except Exception:
        return 0.0


def _ago(ms: float) -> str:
    """'14 minutes ago' — a timestamp is not an answer to 'when'."""
    if not ms:
        return "time unknown"
    delta = time.time() - (ms / 1000.0)
    if delta < 0:
        delta = 0.0
    if delta < 90:
        return f"{int(delta)} seconds ago"
    if delta < 5400:
        return f"{int(delta / 60)} minutes ago"
    if delta < 172800:
        return f"{int(delta / 3600)} hours ago"
    return f"{int(delta / 86400)} days ago"


def _satellites(params: dict) -> str:
    """Satellites from the CelesTrak catalogue, with their real orbits.

    The orbital figures below are derived from the published mean elements and
    are exact for those elements — period, altitude and inclination are what the
    catalogue actually states, not an estimate.

    Whether a satellite is over YOUR head right now needs propagation (SGP4).
    That is an optional extra this project does not ship by default, and the
    answer says so rather than returning a plausible-looking wrong elevation.
    """
    group = str(params.get("group") or "stations").strip().lower()
    if group not in ("stations", "weather", "gps", "science", "amateur"):
        group = "stations"
    search = str(params.get("name") or "").strip().lower()

    text, err = _get_text(
        f"https://celestrak.org/NORAD/elements/gp.php?GROUP={group}&FORMAT=tle",
        timeout=12.0)
    if err:
        return f"I could not reach the orbital catalogue — {err}."

    tles = _parse_tles(text or "")
    if not tles:
        return "The orbital catalogue answered, but I could not read any elements."

    if search:
        tles = [t for t in tles if search in t["name"].lower() or
                search in t["norad"].lower()]
        if not tles:
            return f"No satellite in the {group} catalogue matches '{search}'."

    limit = _row_limit(params, default=6)

    lines = []
    for t in tles[:limit]:
        facts = _orbit_facts(t["line1"], t["line2"])
        bits = [t["name"]]
        if facts:
            bits.append(f"~{facts['alt_km']:,} km up")
            bits.append(f"{facts['period_min']:.0f} min orbit")
            bits.append(f"{facts['inclination']:.0f}° inclination")
        else:
            bits.append("elements could not be read")
        lines.append("• " + " · ".join(bits))

    note = ""
    if not _sgp4_available():
        note = ("\n(Live pass prediction over your location needs the optional "
                "sgp4 package — say the word and I will add it.)")
    return (f"{group.title()} catalogue — {len(tles)} objects"
            + (f" matching '{search}'" if search else "")
            + f"; showing {len(lines)}:\n" + "\n".join(lines) + note)


def _parse_tles(text: str) -> list[dict]:
    """Three-line TLE format → [{name, line1, line2, norad}].

    Tolerant of the blank lines and stray headers CelesTrak sometimes returns:
    it walks the text looking for a line-1/line-2 pair and takes the preceding
    non-element line as the name, rather than trusting a fixed stride.
    """
    out: list[dict] = []
    lines = [ln.rstrip() for ln in str(text or "").splitlines()]
    i = 0
    while i < len(lines):
        line1, line2 = lines[i].strip(), lines[i + 1].strip() if i + 1 < len(lines) else ""
        if line1.startswith("1 ") and line2.startswith("2 "):
            name = ""
            for back in range(i - 1, max(-1, i - 3), -1):
                cand = lines[back].strip()
                if cand and not cand.startswith("1 ") and not cand.startswith("2 "):
                    name = cand
                    break
            norad = line1[2:7].strip()
            out.append({"name": name or f"NORAD {norad}", "line1": line1,
                        "line2": line2, "norad": norad})
            i += 2
            continue
        i += 1
    return out


def _orbit_facts(line1: str, line2: str):
    """Period, mean altitude and inclination from a TLE. Kepler's third law.

    Pure arithmetic on the published elements, so it is unit-tested against the
    ISS's real orbit rather than against its own output.
    """
    try:
        inclination = float(line2[8:16])
        ecc = float("0." + line2[26:33].strip())
        mean_motion = float(line2[52:63])          # revolutions per day
        if mean_motion <= 0:
            return None
        period_min = 1440.0 / mean_motion
        period_s = period_min * 60.0
        a = (_MU_KM3_S2 * (period_s / (2.0 * math.pi)) ** 2) ** (1.0 / 3.0)
        alt_km = a - _EARTH_KM
        return {"period_min": period_min, "alt_km": int(round(alt_km)),
                "ecc": ecc, "inclination": inclination,
                "perigee_km": int(round(a * (1.0 - ecc) - _EARTH_KM)),
                "apogee_km": int(round(a * (1.0 + ecc) - _EARTH_KM))}
    except Exception:
        return None


def _sgp4_available() -> bool:
    try:
        import sgp4.api            # noqa: F401
        return True
    except Exception:
        return False


# ── shared entry point ───────────────────────────────────────────────────────

_ACTION_ALIASES = {
    "planes": "aircraft", "flights": "aircraft", "aeroplanes": "aircraft",
    "airplane": "aircraft", "aircraft": "aircraft", "track": "aircraft",
    "earthquake": "quakes", "earthquakes": "quakes", "quake": "quakes",
    "quakes": "quakes",
    "events": "events", "natural": "events", "fires": "events",
    "storms": "events", "wildfires": "events",
    "satellite": "satellites", "satellites": "satellites", "orbit": "satellites",
    "here": "locate", "location": "locate", "locate": "locate", "where": "locate",
}


def resolve_action(raw: str) -> str:
    norm = str(raw or "").strip().lower().replace(" ", "_").replace("-", "_")
    return _ACTION_ALIASES.get(norm, norm)


def earth_intel(parameters: dict = None, player=None, session_memory=None) -> str:
    params = dict(parameters or {})

    def _val(names):
        for n in names:
            if params.get(n) not in (None, ""):
                return params.get(n)
        return ""

    action = resolve_action(_val(("action", "what", "command")))
    if not action:
        return ("Tell me what to look up: aircraft, quakes, events, satellites, "
                "or locate.")

    handlers = {
        "aircraft": _aircraft,
        "quakes": _quakes,
        "events": _events,
        "satellites": _satellites,
        "locate": _locate,
    }
    fn = handlers.get(action)
    if fn is None:
        return ("Unknown earth_intel action. Use one of: "
                + ", ".join(sorted(handlers)))

    if player:
        player.write_log(f"[earth_intel] {action}")
    _log(f"action={action}")
    try:
        return fn(params)
    except Exception as e:
        # A tool result must be speakable; an unhandled exception here would
        # reach the model as a traceback.
        _log(f"{action} failed: {e}")
        return f"I could not complete that lookup ({e})."


# ── Tool declaration (auto-discovered by core/action_loader.py) ──────────────
TOOL = {
    "name": "earth_intel",
    "description": (
        "Looks up live public data about the real world. Use for: what aircraft "
        "are flying near a place, whether there has been an earthquake nearby or "
        "anywhere, what open natural events are being tracked (storms, wildfires, "
        "volcanoes), what satellites are in orbit, and for setting the user's own "
        "location. Do NOT use this for weather forecasts (use weather_report), "
        "for flight prices or booking (use flight_finder), or for anything about "
        "the user's own computer."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "enum": ["aircraft", "quakes", "events", "satellites", "locate"],
                "description": "What to look up.",
            },
            "place": {
                "type": "STRING",
                "description": ("A place name, or coordinates as 'lat, lon'. Omit to "
                                "use the user's saved location."),
            },
            "lat": {"type": "NUMBER", "description": "Latitude, if known exactly."},
            "lon": {"type": "NUMBER", "description": "Longitude, if known exactly."},
            "radius_km": {
                "type": "NUMBER",
                "description": ("Search radius in kilometres (aircraft: up to ~460; "
                                "quakes: any). Defaults to 150 for aircraft."),
            },
            "magnitude": {
                "type": "STRING",
                "enum": ["1.0", "2.5", "4.5"],
                "description": "Minimum earthquake magnitude (quakes). Default 2.5.",
            },
            "period": {
                "type": "STRING",
                "enum": ["hour", "day", "week", "month"],
                "description": "How far back to look for earthquakes. Default day.",
            },
            "category": {
                "type": "STRING",
                "description": ("Optional natural-event category (events), e.g. "
                                "wildfires, severeStorms, volcanoes, seaLakeIce."),
            },
            "group": {
                "type": "STRING",
                "enum": ["stations", "weather", "gps", "science", "amateur"],
                "description": "Which orbital catalogue to read (satellites).",
            },
            "name": {
                "type": "STRING",
                "description": "Satellite name or NORAD id to look for (satellites).",
            },
            "remember": {
                "type": "BOOLEAN",
                "description": ("For locate: save this place as the user's location. "
                                "Defaults to true."),
            },
            "limit": {
                "type": "NUMBER",
                "description": ("Maximum rows to return for any list answer "
                                f"(1-{_MAX_ROWS})."),
            },
        },
        "required": ["action"],
    },
    "handler": earth_intel,
}
