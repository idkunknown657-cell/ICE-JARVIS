"""
core/skill_registry.py — the book of tools JARVIS has taught itself.

WHY THIS EXISTS
    plugins/*.py are tools a *human* wrote; the loader finds them, and the model
    is told about them at connect time. A skill JARVIS wrote for itself needs
    more than that:

      * a record of where it came from and what let it through the crucible, so
        "why can you do this?" has an answer months later;
      * a way to switch one off or delete it without editing code;
      * a persistent vault (skills/) for skill *packages* — a folder with a
        manifest, the code and its test cases — which is the layout a richer
        skill needs once it outgrows one file;
      * and a router that can find the right skill from a spoken sentence
        WITHOUT a model call, for the cases where the user names a capability
        directly ("use the speed test skill") and paying for a round trip just to
        pick a tool would be silly.

WHAT IS IN HERE
    Manifests   config/skills.json — one entry per forged skill: name,
                description, trigger phrases, aliases, the path that earned it,
                invocation count and last error. Plain JSON, safe to read and
                safe to delete (a missing file means "no skills yet", not a
                crash).
    Vault       skills/<name>/{manifest.json, skill.py, test_cases.json}. Loaded
                by path with importlib, never by package import, so a broken or
                half-written skill cannot take the app down with it.
    Matcher     find_matching_skill(text) → (name, args). Deterministic token
                scoring over names, descriptions, triggers and aliases, plus
                argument extraction driven by the skill's own parameter schema —
                no hardcoded per-tool knowledge anywhere.

SAFETY POSTURE
    Every function here returns data and never raises: a corrupt JSON file, a
    missing skill.py or an import that explodes all degrade to "that skill is not
    there". Nothing in this module executes a skill except execute(), and
    execute() only ever reaches code that core/skill_crucible.py already passed.
"""
from __future__ import annotations

import importlib.util
import inspect
import json
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

_LOCK = threading.RLock()

#: Where the registry keeps its own state. Deliberately next to the other config
#: files rather than in the plugin folder, so deleting a skill's code never
#: leaves its manifest behind and never deletes a *human*-written plugin.
DEFAULT_PARAMS = {"type": "OBJECT", "properties": {}}
_PACKAGE_MANIFEST = "manifest.json"
_PACKAGE_CODE = "skill.py"
_PACKAGE_CASES = "test_cases.json"

#: Words that trail a spoken request without being part of it. Without this,
#: "weather in Kolkata please" hands the skill "Kolkata please" — which is the
#: kind of near-miss that looks like a broken skill rather than a broken parser.
_TRAILING_FILLER = (
    "right now", "for me", "please", "thanks", "thank you", "now", "today",
    "quickly", "real quick", "asap", "sir", "bro", "yaar", "na", "ok", "okay",
)

#: A skill's own words that mean nothing on their own and must not count as a
#: match. Short and general: domain words are never listed here, because the
#: whole point of the matcher is that it knows no domains.
_STOPWORDS = frozenset("""
a an the and or to for when that will you i me my on in at by from of with about
is are was were be been this it can could please do does did what how give run
use using start execute check get fetch try call launch skill skills feature
features tool tools test tests testing made make built build forged forge just
recently newly new latest now already one have has had there here then than so
if but not no yes okay ok sure
""".split())


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def config_path() -> Path:
    return _base_dir() / "config" / "skills.json"


def vault_dir() -> Path:
    return _base_dir() / "skills"


# ── manifests ────────────────────────────────────────────────────────────────

def _load_raw() -> dict:
    try:
        data = json.loads(config_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_raw(data: dict) -> None:
    """Write the book atomically — a crash mid-save must not lose every skill."""
    path = config_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False),
                       encoding="utf-8")
        tmp.replace(path)
    except Exception as e:
        print(f"[Skills] could not save the registry: {e}")


def _entries() -> dict:
    raw = _load_raw()
    book = raw.get("skills")
    return book if isinstance(book, dict) else {}


def record(name: str, description: str = "", parameters: dict | None = None,
           path: str = "", source: str = "forged", triggers: list | None = None,
           aliases: list | None = None, version: str = "1.0.0",
           active: bool = True, verdict: dict | None = None) -> dict:
    """Add or update one skill's manifest. Never raises."""
    name = str(name or "").strip()
    if not name:
        return {}
    with _LOCK:
        data = _load_raw()
        book = data.get("skills")
        if not isinstance(book, dict):
            book = {}
        existing = book.get(name) if isinstance(book.get(name), dict) else {}
        entry = {
            "name": name,
            "description": str(description or existing.get("description", ""))[:600],
            "parameters": parameters if isinstance(parameters, dict)
                          else existing.get("parameters", dict(DEFAULT_PARAMS)),
            "triggers": [str(t)[:120] for t in (triggers or existing.get("triggers") or [])][:12],
            "aliases": [str(a)[:60] for a in (aliases or existing.get("aliases") or [])][:8],
            "path": str(path or existing.get("path", "")),
            "source": str(source or existing.get("source", "forged")),
            "version": str(version or existing.get("version", "1.0.0")),
            "active": bool(active) if active is not None else bool(existing.get("active", True)),
            "created_at": existing.get("created_at") or time.time(),
            "updated_at": time.time(),
            "invocations": int(existing.get("invocations", 0) or 0),
            "last_error": existing.get("last_error", ""),
            "verdict": verdict if isinstance(verdict, dict) else existing.get("verdict", {}),
        }
        book[name] = entry
        data["skills"] = book
        _save_raw(data)
        return entry


def get(name: str) -> dict:
    with _LOCK:
        entry = _entries().get(str(name or "").strip())
    return entry if isinstance(entry, dict) else {}


def is_known(name: str) -> bool:
    return bool(get(name))


def list_skills() -> list[dict]:
    """Newest first — the order a person expects when asking what it has learned."""
    with _LOCK:
        items = [v for v in _entries().values() if isinstance(v, dict)]
    items.sort(key=lambda e: e.get("created_at", 0), reverse=True)
    return items


def names() -> list[str]:
    with _LOCK:
        return sorted(_entries().keys())


def toggle(name: str, active: bool | None = None) -> dict:
    """Flip (or set) a skill's active flag. Returns the updated manifest."""
    with _LOCK:
        data = _load_raw()
        book = data.get("skills")
        if not isinstance(book, dict) or name not in book:
            return {}
        entry = book[name]
        entry["active"] = (not bool(entry.get("active", True))) if active is None \
            else bool(active)
        entry["updated_at"] = time.time()
        _save_raw(data)
        return entry


def forget(name: str, remove_file: bool = True) -> bool:
    """Delete a forged skill: its manifest, and — only for files this registry
    itself put there — its code. A hand-written plugin is never deleted by name
    alone; `source` has to say it was forged."""
    name = str(name or "").strip()
    with _LOCK:
        data = _load_raw()
        book = data.get("skills")
        if not isinstance(book, dict) or name not in book:
            return False
        entry = book.pop(name)
        _save_raw(data)

    if not remove_file:
        return True
    try:
        path = Path(str(entry.get("path") or ""))
        if path and path.exists() and str(entry.get("source")) == "forged":
            base = _base_dir().resolve()
            # Only ever delete inside this installation.
            if base in path.resolve().parents:
                path.unlink()
    except Exception as e:
        print(f"[Skills] could not remove {name}'s file: {e}")
    return True


def record_invocation(name: str) -> None:
    with _LOCK:
        data = _load_raw()
        book = data.get("skills")
        if isinstance(book, dict) and name in book:
            book[name]["invocations"] = int(book[name].get("invocations", 0)) + 1
            book[name]["last_used"] = time.time()
            _save_raw(data)


def record_error(name: str, error: str) -> None:
    with _LOCK:
        data = _load_raw()
        book = data.get("skills")
        if isinstance(book, dict) and name in book:
            book[name]["last_error"] = str(error)[:400]
            book[name]["last_error_at"] = time.time()
            _save_raw(data)


# ── the vault: skill packages ────────────────────────────────────────────────

@dataclass
class VaultSkill:
    """One package from skills/: its manifest, its code path and its tests."""
    name: str
    manifest: dict = field(default_factory=dict)
    path: Path = field(default_factory=Path)
    error: str = ""
    _module: object = None

    @property
    def description(self) -> str:
        return str(self.manifest.get("description") or "")

    @property
    def parameters(self) -> dict:
        p = self.manifest.get("parameters")
        return p if isinstance(p, dict) else dict(DEFAULT_PARAMS)

    @property
    def triggers(self) -> list:
        t = self.manifest.get("triggers")
        return t if isinstance(t, list) else []

    @property
    def aliases(self) -> list:
        a = self.manifest.get("aliases")
        return a if isinstance(a, list) else []

    @property
    def active(self) -> bool:
        return bool(self.manifest.get("active", True))

    def load(self):
        """Import the package's skill.py by path.

        By path, not by name: the vault is user data, so two installs can hold
        different skills under the same folder name without colliding in
        sys.modules.
        """
        if self._module is not None:
            return self._module
        code = self.path / _PACKAGE_CODE
        if not code.exists():
            raise ImportError(f"{self.name} has no {_PACKAGE_CODE}")
        module_name = f"jarvis_skill_{self.name}_{abs(hash(str(code.resolve())))}"
        spec = importlib.util.spec_from_file_location(module_name, code)
        if spec is None or spec.loader is None:
            raise ImportError(f"could not load {code}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            sys.modules.pop(module_name, None)
            raise
        self._module = module
        return module

    def entry_point(self):
        module = self.load()
        fn = getattr(module, "execute", None) or getattr(module, "run", None)
        if not callable(fn):
            raise AttributeError(f"{self.name} has no execute/run function")
        return fn

    def call(self, args: dict):
        """Call the skill with the arguments it declares. Runs **in this
        process** — only reach this for code the crucible has already passed."""
        fn = self.entry_point()
        import asyncio
        sig = inspect.signature(fn)
        kinds = {p.kind for p in sig.parameters.values()}
        if inspect.Parameter.VAR_KEYWORD in kinds:
            out = fn(**args)
        elif "parameters" in sig.parameters:
            out = fn(args)
        elif not sig.parameters:
            out = fn()
        else:
            out = fn(**{k: v for k, v in args.items() if k in sig.parameters})
        if inspect.iscoroutine(out):
            out = asyncio.run(out)
        return out

    def to_tool_declaration(self) -> dict:
        return {"name": self.name, "description": self.description,
                "parameters": self.parameters}


_vault_cache: dict[str, VaultSkill] = {}


def load_vault(refresh: bool = False) -> dict[str, VaultSkill]:
    """Every readable package under skills/. Unreadable ones are recorded with
    their error rather than skipped silently — "why is my skill not there?" is
    the question this has to be able to answer."""
    global _vault_cache
    with _LOCK:
        if _vault_cache and not refresh:
            return dict(_vault_cache)
        found: dict[str, VaultSkill] = {}
        root = vault_dir()
        try:
            folders = sorted(p for p in root.iterdir() if p.is_dir())
        except Exception:
            folders = []
        for folder in folders:
            if folder.name.startswith((".", "_")):
                continue
            manifest_file = folder / _PACKAGE_MANIFEST
            skill = VaultSkill(name=folder.name, path=folder)
            if not manifest_file.exists():
                skill.error = f"no {_PACKAGE_MANIFEST}"
            else:
                try:
                    data = json.loads(manifest_file.read_text(encoding="utf-8"))
                    if isinstance(data, dict):
                        skill.manifest = data
                        skill.name = str(data.get("name") or folder.name)
                    else:
                        skill.error = f"{_PACKAGE_MANIFEST} is not an object"
                except Exception as e:
                    skill.error = f"{_PACKAGE_MANIFEST} unreadable: {e}"
            if not skill.error and not (folder / _PACKAGE_CODE).exists():
                skill.error = f"no {_PACKAGE_CODE}"
            found[folder.name] = skill
        _vault_cache = found
    _sync_vault_book(found)
    return dict(found)


def _sync_vault_book(vault: dict) -> None:
    """Make the manifests reflect the vault.

    A package dropped in by hand has no manifest entry, and without one a failure
    while running it would be recorded against nothing — the error would vanish,
    which is the one outcome an error log must never produce. Writes only when an
    entry is missing, so this costs one dict lookup per package per scan and one
    write per genuinely new package.
    """
    try:
        for skill in vault.values():
            if skill.error or is_known(skill.name):
                continue
            record(skill.name, description=skill.description,
                   parameters=skill.parameters, path=str(skill.path),
                   source="vault", triggers=skill.triggers,
                   aliases=skill.aliases,
                   version=str(skill.manifest.get("version") or "1.0.0"))
    except Exception as e:
        print(f"[Skills] could not record the vault: {e}")


def active_vault_skills() -> list[VaultSkill]:
    """Vault skills that are readable and switched on. `active` is stored on the
    manifest when present, otherwise in the registry book (so the UI can toggle a
    human-written package without rewriting its manifest)."""
    out = []
    for folder_name, skill in load_vault().items():
        if skill.error:
            continue
        book = get(skill.name) or get(folder_name)
        if "active" in skill.manifest:
            on = skill.active
        else:
            on = bool(book.get("active", True)) if book else True
        if on:
            out.append(skill)
    return out


def vault_declarations() -> list[dict]:
    """Tool declarations for vault skills. main.py merges these with the plugin
    and action registries, so a package in skills/ is callable by voice without
    any code change."""
    decls = []
    for skill in active_vault_skills():
        try:
            decls.append(skill.to_tool_declaration())
        except Exception as e:
            print(f"[Skills] could not describe {skill.name}: {e}")
    return decls


def run_vault_skill(name: str, args: dict) -> str:
    """Dispatch a call to a vault package. Never raises — a failing skill returns
    its error as the spoken result, which is what every other tool does."""
    skill = load_vault().get(name) or next(
        (s for s in load_vault().values() if s.name == name), None)
    if skill is None:
        return f"There is no skill called '{name}'."
    if skill.error:
        return f"The '{name}' skill could not be loaded: {skill.error}"
    try:
        out = skill.call(dict(args or {}))
    except Exception as e:
        record_error(skill.name, str(e))
        return f"The '{skill.name}' skill failed: {e}"
    record_invocation(skill.name)
    if out is None:
        return "Done."
    if isinstance(out, dict):
        for key in ("summary", "output", "text", "result", "message"):
            if out.get(key):
                return str(out[key])
    return str(out)


# ── routed dispatch (no model call) ──────────────────────────────────────────

def _stem(word: str) -> str:
    low = word.lower()
    for suffix in ("ing", "ies", "es", "s"):
        if low.endswith(suffix) and len(low) > len(suffix) + 2:
            return low[: -len(suffix)]
    return low


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def _meaningful(words: list[str]) -> list[str]:
    return [w for w in words if len(w) > 2 and w not in _STOPWORDS]


def _candidates() -> list[dict]:
    """Everything addressable: forged flat plugins (via the live plugin registry),
    vault packages, and their manifests. One flat list so the scorer has a single
    shape to reason about."""
    seen: set[str] = set()
    out: list[dict] = []

    def add(name, description, parameters, triggers, aliases, kind, path=""):
        if not name or name in seen:
            return
        seen.add(name)
        out.append({"name": name, "description": description or "",
                    "parameters": parameters or dict(DEFAULT_PARAMS),
                    "triggers": list(triggers or []), "aliases": list(aliases or []),
                    "kind": kind, "path": str(path or "")})

    live = False
    try:
        from core import plugin_loader
        registry = plugin_loader.active_registry()
        if registry is not None:
            live = True
            for name, rec in dict(registry.plugins()).items():
                book = get(name)
                if book and not book.get("active", True):
                    continue
                add(name, rec.description, rec.parameters,
                    book.get("triggers"), book.get("aliases"),
                    "plugin", rec.file)
    except Exception as e:
        print(f"[Skills] plugin list unavailable to the matcher: {e}")

    if not live:
        # No registry in this process (a test, the headless bridge, a very early
        # call). Read the declarations straight off disk instead of returning an
        # empty candidate list, so routing does not silently stop working in the
        # one situation where nobody would notice.
        for cand in _plugin_files():
            add(cand["name"], cand["description"], cand["parameters"],
                cand["triggers"], cand["aliases"], "plugin", cand["path"])

    for skill in active_vault_skills():
        add(skill.name, skill.description, skill.parameters,
            skill.triggers, skill.aliases, "vault", skill.path)

    return out


def _plugin_files() -> list[dict]:
    """Plugin declarations read directly from plugins/*.py.

    Uses ast.literal_eval, never import: the matcher must be able to describe a
    plugin without executing it, because it runs on the request path where an
    import error would be visible to the user.
    """
    import ast as _ast
    base = _base_dir()
    out: list[dict] = []
    try:
        files = sorted((base / "plugins").glob("*.py"))
    except Exception:
        return out
    for path in files:
        if path.name.startswith("_"):
            continue
        try:
            tree = _ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        for node in tree.body:
            if not isinstance(node, _ast.Assign) or len(node.targets) != 1:
                continue
            target = node.targets[0]
            if not isinstance(target, _ast.Name) or target.id != "PLUGIN":
                continue
            try:
                meta = _ast.literal_eval(node.value)
            except Exception:
                break
            if not isinstance(meta, dict) or not meta.get("name"):
                break
            out.append({
                "name": str(meta.get("name")),
                "description": str(meta.get("description") or ""),
                "parameters": meta.get("parameters") if isinstance(
                    meta.get("parameters"), dict) else dict(DEFAULT_PARAMS),
                "triggers": meta.get("triggers") if isinstance(
                    meta.get("triggers"), list) else [],
                "aliases": meta.get("aliases") if isinstance(
                    meta.get("aliases"), list) else [],
                "path": str(path),
            })
            break
    return out


def _trim_filler(value: str) -> str:
    """Drop the politeness that follows a spoken argument, repeatedly, so
    "Kolkata please thanks" becomes "Kolkata"."""
    out = str(value or "").strip(" .,!?;:")
    changed = True
    while changed and out:
        changed = False
        low = out.lower()
        for filler in _TRAILING_FILLER:
            if low.endswith(filler):
                trimmed = out[: len(out) - len(filler)].strip(" .,!?;:")
                if trimmed:
                    out, changed = trimmed, True
                    break
    return out


def _extract_args(text: str, q_lower: str, words: list[str], parameters: dict) -> dict:
    """Pull arguments out of the sentence using the skill's OWN parameter
    descriptions. No tool-specific rules: a parameter described as a city gets a
    city, a quoted option in the description gets matched, a duration gets its
    number. A schema with nothing recognisable simply yields {}."""
    args: dict = {}
    props = parameters.get("properties") if isinstance(parameters, dict) else None
    if not isinstance(props, dict):
        return args

    for p_name, spec in props.items():
        if not isinstance(spec, dict):
            continue
        desc = str(spec.get("description") or "")
        p_type = str(spec.get("type") or "STRING").upper()

        # A place: take whatever follows the preposition the user used.
        if any(w in desc.lower() for w in ("city", "region", "area", "location",
                                           "place", "country")):
            for prep in ("around ", "in ", "near ", "at ", "for "):
                idx = q_lower.find(prep)
                if idx != -1:
                    tail = text[idx + len(prep):].strip()
                    tail = re.split(r"[,.?!;]|(?: and )|(?: with )", tail)[0].strip()
                    tail = _trim_filler(tail)
                    if tail:
                        args[p_name] = tail
                    break

        # A choice the description lists in quotes, e.g. "unit: km, mi, kg".
        for option in re.findall(r"'([a-zA-Z0-9_.\-]+)'", desc):
            if option.lower() in words:
                args[p_name] = option
                break

        # "AAPL for Apple" style label→symbol hints.
        for code, label in re.findall(
                r"'([A-Za-z0-9_.\-]+)'\s+for\s+([A-Za-z0-9]+)", desc):
            if label.lower() in words:
                args[p_name] = code
                break

        if p_type in ("INTEGER", "NUMBER"):
            m = re.search(r"\b(\d+(?:\.\d+)?)\s*"
                          r"(?:seconds?|secs?|minutes?|mins?|hours?|hrs?|days?|"
                          r"times?|points?|bars?|items?|results?)\b", q_lower)
            if m:
                value = float(m.group(1))
                args[p_name] = int(value) if p_type == "INTEGER" else value
    return args


def find_matching_skill(text: str) -> tuple[str, dict] | None:
    """Best skill for a spoken sentence, or None when nothing fits convincingly.

    Deterministic and model-free. The threshold matters more than the scoring: a
    weak match routed here would run the wrong tool without the model ever
    getting a chance to notice, so the bar is deliberately set where a sentence
    has to actually resemble a skill rather than merely share a word with one.
    """
    if not isinstance(text, str) or not text.strip():
        return None
    q = text.strip()
    q_lower = q.lower()
    words = _tokens(q)
    if not words:
        return None

    q_stems = {_stem(w) for w in _meaningful(words)}

    # "use the latest skill" and friends: the user means the most recent one and
    # has told us nothing about which. Only honour it when *no* domain word is
    # present, so "run the skill to check crypto" keeps its domain.
    generic = {"use the skill", "run the skill", "test the skill", "use that skill",
               "that skill", "use it", "the new skill", "the latest skill"}
    squeezed = " ".join(words)
    if squeezed in generic or (not q_stems and any(
            w in words for w in ("skill", "skills", "feature", "tool", "plugin"))):
        recent = list_skills()
        if recent:
            return str(recent[0]["name"]), {}

    best: dict | None = None
    best_score = 0
    for cand in _candidates():
        score = 0
        name_words = _tokens(cand["name"])
        name_stems = {_stem(w) for w in name_words if len(w) > 2
                      and w not in _STOPWORDS}
        desc_stems = {_stem(w) for w in _tokens(cand["description"])
                      if len(w) > 2 and w not in _STOPWORDS}

        for trigger in cand["triggers"]:
            if str(trigger).lower() in q_lower:
                score += 200
                break
        for alias in cand["aliases"]:
            a = str(alias).lower()
            if a in q_lower or a.replace("_", " ") in q_lower:
                score += 180
                break
        if cand["name"].lower() in q_lower or \
                cand["name"].replace("_", " ").lower() in q_lower:
            score += 150

        end = cand["name"].replace("_", " ").lower()
        for span in (3, 2):
            for i in range(len(words) - span + 1):
                phrase = " ".join(words[i:i + span])
                if all(w in _STOPWORDS for w in words[i:i + span]):
                    continue
                if phrase in end:
                    score += 60
                elif phrase in cand["description"].lower():
                    score += 25

        name_hits = name_stems & q_stems
        score += len(name_hits) * 35
        if name_stems and name_hits == name_stems:
            score += 50
        score += len(desc_stems & q_stems) * 6

        if score > best_score:
            best_score, best = score, cand

    if best is None or best_score < 90:
        return None
    return best["name"], _extract_args(q, q_lower, words, best["parameters"])


def stats() -> dict:
    """A one-glance summary for the UI and for `skill_forge` status."""
    book = list_skills()
    vault = load_vault(refresh=True)
    return {
        "forged": len(book),
        "active": sum(1 for e in book if e.get("active", True)),
        "vault_packages": len([s for s in vault.values() if not s.error]),
        "vault_broken": len([s for s in vault.values() if s.error]),
        "invocations": sum(int(e.get("invocations", 0) or 0) for e in book),
        "last": book[0] if book else {},
    }


def reset_cache() -> None:
    """Test seam: forget the vault scan and the plugin snapshot."""
    global _vault_cache
    with _LOCK:
        _vault_cache = {}


__all__ = ["record", "get", "is_known", "list_skills", "names", "toggle",
           "forget", "record_invocation", "record_error", "load_vault",
           "active_vault_skills", "vault_declarations", "run_vault_skill",
           "find_matching_skill", "stats", "reset_cache", "config_path",
           "vault_dir", "VaultSkill"]
