"""Non-raising API-key verification for the settings panel.

Every function here runs in whatever thread the caller chooses (the UI calls
them from a daemon worker) and NEVER raises: a bad key, dead server or timeout
becomes a (ok, message) tuple instead of an exception, so bad configuration
can never take the interface down.
"""
import urllib.parse

import requests

GEMINI_MODELS_URL = "https://generativelanguage.googleapis.com/v1beta/models"


def _verify(fn):
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as e:                      # noqa: BLE001 — never raise
            return False, f"unreachable ({type(e).__name__}: {str(e)[:80]})"
    return wrapper


@_verify
def verify_gemini(key: str, timeout: float = 8.0) -> tuple[bool, str]:
    """Is the Gemini key usable? Hits the (free) models-list endpoint — a 200
    means the key is valid, 401/403/400 means invalid/expired. No generation
    cost, no side effects."""
    key = (key or "").strip()
    if not key:
        return False, "no key provided"
    r = requests.get(GEMINI_MODELS_URL + "?key=" + urllib.parse.quote(key),
                     timeout=timeout)
    if r.status_code == 200:
        return True, "valid"
    if r.status_code in (400, 401, 403):
        return False, f"invalid/expired (HTTP {r.status_code})"
    return False, f"server error (HTTP {r.status_code})"


@_verify
def verify_provider(base_url: str, api_key: str,
                    timeout: float = 8.0) -> tuple[bool, str]:
    """Is an OpenAI-compatible fallback provider key usable? Lists the models
    endpoint with a Bearer token — 200 means valid.

    The stored base_url may or may not already end in "/v1" (every shipped
    sample row carries it); appending another "/v1" produced "/v1/v1/models",
    a 404 that reported every correctly configured provider as invalid."""
    base_url = (base_url or "").strip().rstrip("/")
    if base_url.endswith("/v1"):
        base_url = base_url[: -len("/v1")]
    api_key = (api_key or "").strip()
    if not api_key:
        return False, "no key provided"
    if not base_url.startswith("http"):
        return False, "bad base_url"
    r = requests.get(f"{base_url}/v1/models",
                     headers={"Authorization": f"Bearer {api_key}"},
                     timeout=timeout)
    if r.status_code == 200:
        return True, "valid"
    return False, f"invalid (HTTP {r.status_code})"


def verify_all(gemini_key: str, providers: list,
               timeout: float = 8.0) -> list[tuple]:
    """Ordered status list — ('gemini', ok, msg) then one entry per provider
    row. Never raises (garbage rows included) and never blocks long: ~timeout
    per provider worst case, designed to run on a worker thread."""
    def _row(p):
        return p if isinstance(p, dict) else {}

    out: list[tuple] = [("gemini", *verify_gemini(gemini_key, timeout))]
    for p in providers:
        r = _row(p)
        name = str(r.get("name") or "provider")
        ok, msg = verify_provider(r.get("base_url"), r.get("api_key"), timeout)
        out.append((name, ok, msg))
    return out