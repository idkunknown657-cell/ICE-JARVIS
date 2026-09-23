"""
Free LLM-provider fallbacks (OpenAI-compatible chat endpoints).

Gemini carries the whole assistant by default. When every Gemini model on a
tier's ladder is down or drained of quota, these providers are the second
string so text calls — proactive context, screen glances, goal agents,
verification — answer with something instead of nothing.

Each provider is a simple config row in api_keys.json:

    "free_providers": [
        {"name": "groq",      "base_url": "https://api.groq.com/openai/v1",
         "api_key": "...",    "model": "llama-3.3-70b-versatile"},
        {"name": "cerebras",  "base_url": "https://api.cerebras.ai/v1",
         "api_key": "...",    "model": "llama-3.3-70b"},
        {"name": "openrouter","base_url": "https://openrouter.ai/api/v1",
         "api_key": "...",    "model": "meta-llama/llama-3.3-70b-instruct:free"}
    ]

More rows can be added any time; a row without an api_key is skipped. Providers
that fail (especially 429 / 5xx) are put on a cooldown so a dead one never
blocks the ones after it.
"""
import json
import time
from pathlib import Path

_BASE = Path(__file__).resolve().parent.parent
_CONFIG_PATH = _BASE / "config" / "api_keys.json"

_COOLDOWN_SECONDS = 120          # 429 / 5xx / network failure → skip for 2 min
_DEFAULT_MODEL   = "meta-llama/llama-3.3-70b-instruct:free"

_fail_until: dict[str, float] = {}   # provider name -> perf counter deadline


def _endpoint(base_url: str, path: str) -> str:
    """Join a provider base_url with an OpenAI-style path (e.g. "chat/completions"
    or "models"), tolerating bases stored with or without the trailing "/v1".

    Every shipped sample row carries "/v1" ("https://api.groq.com/openai/v1",
    "https://router.huggingface.co/v1"), and so does every URL the settings UI
    saves — but this module used to append a second "/v1/..." to it, producing
    "/v1/v1/chat/completions": a silent 404 that made ALL the fallback providers
    fail while looking configured. Normalising here fixes every row, past and
    future, regardless of how the user pasted the base URL."""
    base = (base_url or "").strip().rstrip("/")
    if base.endswith("/v1"):
        base = base[: -len("/v1")]
    return f"{base}/v1/{path.lstrip('/')}"


def _load() -> list[dict]:
    try:
        data = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []
    provs = data.get("free_providers") or []
    out = []
    for p in provs:
        if not isinstance(p, dict):
            continue
        name = str(p.get("name") or "").strip()
        base = str(p.get("base_url") or "").strip().rstrip("/")
        key  = str(p.get("api_key") or "").strip()
        model = str(p.get("model") or "").strip() or _DEFAULT_MODEL
        if p.get("enabled") is False:
            continue                      # disabled in Settings — key kept, not used
        if name and base and key and base.startswith("http"):
            out.append({"name": name, "base_url": base,
                        "api_key": key, "model": model})
    return out


def reset_cooldowns() -> None:
    """Clear every provider cooldown — call after saving new keys so the fresh
    credentials are tried immediately instead of waiting out a 2-min penalty."""
    _fail_until.clear()


def configured_count() -> int:
    return len(_load())


def enabled() -> bool:
    return configured_count() > 0


def health_summary() -> str:
    n = configured_count()
    if not n:
        return "no free providers configured"
    return f"{n} free provider(s) configured (keyed): 'groq', 'cerebras', 'openrouter', …"


def from_contents(contents) -> str:
    """Turn whatever Gemini callers pass (text, parts, lists of either) into a
    plain string a plain chat endpoint can use. SDK Part objects are reduced to
    their text fields; images are dropped (these endpoints are text-only)."""
    try:
        from google.genai import types as gtypes
        Part = gtypes.Part
    except Exception:
        Part = None

    def _scalar(c):
        if isinstance(c, str):
            return c
        if Part is not None and isinstance(c, Part):
            return getattr(c, "text", "") or ""
        return ""
    return "\n".join(filter(None, (_scalar(c) for c in contents))).strip() \
        if isinstance(contents, (list, tuple)) else _scalar(contents)


def text(prompt: str, system: str | None = None, timeout: int = 60) -> str | None:
    """Ask the first healthy configured provider for text. Returns None when
    none answered (callers keep their existing default behaviour)."""
    import requests
    for p in _load():
        name = p["name"]
        if _fail_until.get(name, 0.0) > time.perf_counter():
            continue
        endpoint = _endpoint(p["base_url"], "chat/completions")
        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.append({"role": "user", "content": prompt})
        try:
            resp = requests.post(
                endpoint,
                json={"model": p["model"], "messages": msgs,
                      "stream": False, "max_tokens": 300},
                headers={"Authorization": f"Bearer {p['api_key']}"},
                timeout=timeout,
            )
        except Exception as e:
            print(f"[FreeLLM] {name}: {type(e).__name__} — cooldown")
            _fail_until[name] = time.perf_counter() + _COOLDOWN_SECONDS
            continue
        if resp.status_code == 429 or resp.status_code >= 500:
            print(f"[FreeLLM] {name}: {resp.status_code} — cooldown")
            _fail_until[name] = time.perf_counter() + _COOLDOWN_SECONDS
            continue
        if resp.status_code != 200:
            print(f"[FreeLLM] {name}: {resp.status_code} {resp.text[:160]}")
            continue
        try:
            content = (resp.json()["choices"][0]["message"]["content"] or "").strip()
        except Exception as e:
            print(f"[FreeLLM] {name}: bad payload — {e}")
            continue
        if content:
            print(f"[FreeLLM] answered via {name} ({p['model']})")
            return content
    return None