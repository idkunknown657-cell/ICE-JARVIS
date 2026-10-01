"""
core/skill_forge.py — teach JARVIS a capability it does not have yet.

WHY THIS EXISTS
    Every other improvement loop in this project reacts to what the user said.
    This one reacts to what they *asked for and could not get*: "learn how to
    check my internet speed", "make yourself able to look up a crypto price".
    Before this module the honest answer to those was "I can't" — and the person
    asking was the only one who could fix it, by finding a plugin and dropping it
    in. Now the assistant writes the tool, proves it works, and answers the
    original request in the same breath.

THE PIPELINE — ASK → WRITE → PROVE → PUBLISH → USE
    1. ASK      the goal and any context go to the reasoning model, which returns
                one ICE plugin: a PLUGIN declaration plus a run() function.
    2. WRITE    it lands in config/forge_staging/, never in plugins/. Staging is
                not tidiness — a half-written file in plugins/ is imported at the
                next boot, so nothing reaches that folder unverified.
    3. PROVE    core/skill_crucible.py runs the four gates: shape, safety,
                imports, behaviour (executed in a separate interpreter). A
                candidate that fails on shape or behaviour gets up to two repair
                rounds, each one told exactly what the last attempt did wrong.
    4. PUBLISH  only a candidate that passed every gate is moved into plugins/
                and the loader is asked to rescan, so the new tool is callable
                immediately — no restart, no second session.
    5. USE      the caller (`actions/skill_forge.py`) runs the new tool against
                the request that prompted it, so "learn to do X" ends with X done,
                not with an announcement that a tool now exists.

BOUNDARIES
    * It writes exactly one thing: a plugin file inside config/forge_staging/ and
      then plugins/. It never edits existing code, settings or memory structure.
    * It will not overwrite anything. A name that already exists — as a plugin, an
      action, a core tool, or another staged candidate — is refused and re-named,
      because silently replacing a working tool would be the worst possible
      outcome of asking for a new one.
    * It never installs packages. Missing imports fail gate 3 and the user is told
      which ones, so the decision stays theirs.
    * Every promotion is recorded twice: in the skill registry (with the crucible
      verdict that let it through) and in the improvements log, which is the same
      reversible store the rest of the learning system writes to.

NOTHING HERE RAISES. Every public entry point returns a result dict; a failed
forge is a result with a `message` the assistant can say out loud.
"""
from __future__ import annotations

import json
import re
import shutil
import sys
import time
from pathlib import Path

from core import skill_crucible as crucible
from core import skill_registry

#: Two repair rounds. Past that the model is guessing, and each round costs the
#: user a wait — a skill that needs a third fix is better left staged for review.
MAX_REPAIR_ROUNDS = 2

#: How long one behavioural run may take. Generous enough for a real network
#: lookup, short enough that a hung candidate does not own the session.
SMOKE_TIMEOUT_S = 30.0

_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,63}$")

_TAG = "[Forge]"


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def staging_dir() -> Path:
    return _base_dir() / "config" / "forge_staging"


def plugins_dir() -> Path:
    return _base_dir() / "plugins"


# ── the prompt ───────────────────────────────────────────────────────────────

_SYSTEM = """\
You write JARVIS plugins — single Python files that become new voice-callable
tools. You are given a capability the user asked for that does not exist yet.

Return ONE JSON object and nothing else:

{
  "name":        "snake_case_tool_name",
  "description": "Two or three sentences, written for the model that will decide
                  when to call this. Say what it does, name the phrases a user
                  would say to trigger it, and name any existing tool it must
                  NOT be confused with.",
  "parameters":  {"type": "OBJECT", "properties": {...}, "required": [...]},
  "code":        "the complete file, as a string",
  "test_cases":  [{"param_name": "value"}]
}

The file you write must follow this contract exactly:

    PLUGIN = {
        "name": "<the same name>",
        "description": "<the same description>",
        "parameters": <the same schema>,
    }

    def run(parameters: dict, player=None, session_memory=None) -> str:
        ...

Rules for run():
  * Read arguments with a safe default for every one of them:
    `query = parameters.get("query") or "headphones"`. It is called with an
    empty dict during verification, and it must survive that.
  * Return a SHORT natural-language sentence — it is spoken aloud. Never return a
    dict, never return None, never return raw JSON.
  * Never raise. Wrap the whole body in try/except and return a spoken error
    sentence instead. Catching broad exceptions is correct here; a traceback in
    the middle of a conversation is not.
  * Catch failures from the network and fall back to something useful rather than
    reporting the error. If a download fails, say what you could not fetch; do not
    invent data you did not receive.
  * Standard library is always available. Of third-party packages, only these are
    installed: requests, numpy, Pillow, psutil, pyperclip, mss, cv2, pdfplumber,
    PyPDF2, docx, bs4 (beautifulsoup4), ddgs, cryptography, fastapi. Importing
    anything else means the skill is refused, so prefer the standard library.
  * Use requests with a timeout (`timeout=8`) for anything over the network.
  * On Windows, prefer `requests` over `urllib` for HTTPS: Python's bundled
    certificate store frequently rejects pages a normal browser opens happily.
  * Keep it under 200 lines. One capability, done properly.
"""


def _repair_prompt(goal: str, name: str, code: str, problem: str,
                   detail: str = "") -> str:
    return f"""\
A JARVIS plugin you wrote failed verification and must be corrected.

The capability it was meant to provide: {goal}
Its name: {name} — the PLUGIN["name"] value must be exactly "{name}".

What was wrong:
    {problem}
{("    " + detail.strip()[:1200]) if detail else ""}

The code that failed:
```python
{code[:12000]}
```

Fix it. Keep the same name and the same PLUGIN/run contract. If it failed
because calling it with an empty parameters dict raised, give every argument a
default. If it failed because it returned an error, make it return a useful
sentence instead. Return ONLY JSON: {{"code": "the corrected file"}}
"""


def _parse_object(raw) -> dict:
    """Coerce a model reply into a dict. `gemini.as_json` already strips fences
    and prose, so anything here is a genuine miss and returns {}."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}
    return {}


def _ask_model(prompt: str, timeout_ms: int = 90_000) -> dict:
    """One reasoning-tier call. Returns {} when the model or the key is absent."""
    try:
        from core import gemini
    except Exception as e:                                  # pragma: no cover
        print(f"{_TAG} model client unavailable: {e}")
        return {}
    try:
        raw = gemini.as_json(prompt, tier=gemini.SMART, timeout_ms=timeout_ms,
                             default=None)
    except Exception as e:
        print(f"{_TAG} synthesis call failed: {e}")
        return {}
    return _parse_object(raw)


# ── naming ───────────────────────────────────────────────────────────────────

def _clean_name(raw: str) -> str:
    name = re.sub(r"[^a-zA-Z0-9_]", "_", str(raw or "").strip().lower())
    name = re.sub(r"_{2,}", "_", name).strip("_")
    if name and name[0].isdigit():
        name = "skill_" + name
    return name[:64]


def _taken_names() -> set[str]:
    """Every name already in use. Refusing a collision is not politeness — a
    forged plugin that shadows a working tool would replace behaviour the user
    never agreed to lose."""
    taken: set[str] = set()
    try:
        for path in plugins_dir().glob("*.py"):
            if not path.name.startswith("_"):
                taken.add(path.stem)
    except Exception:
        pass
    try:
        for path in staging_dir().glob("*.py"):
            taken.add(path.stem)
    except Exception:
        pass
    try:
        from core import plugin_loader
        registry = plugin_loader.active_registry()
        if registry is not None:
            taken |= set(registry.plugins().keys())
    except Exception:
        pass
    try:
        from core.action_loader import discover_actions
        taken |= {p.stem for p in (_base_dir() / "actions").glob("*.py")}
    except Exception:
        pass
    taken |= set(skill_registry.names())
    return {t for t in taken if t}


def _unique_name(preferred: str, goal: str) -> str:
    base = _clean_name(preferred) or _name_from_goal(goal) or "learned_skill"
    taken = _taken_names()
    if base not in taken:
        return base
    for n in range(2, 40):
        candidate = f"{base}_{n}"
        if candidate not in taken:
            return candidate
    return f"{base}_{int(time.time()) % 100000}"


def _name_from_goal(goal: str) -> str:
    """A name derived from the request, used when the model's reply had none.
    The filler words are dropped so "learn how to check my internet speed"
    becomes `check_internet_speed` rather than `learn_how_to`."""
    words = re.findall(r"[a-zA-Z0-9]+", (goal or "").lower())
    skip = {"a", "an", "the", "to", "for", "of", "and", "how", "that", "it",
            "you", "your", "my", "me", "i", "can", "will", "learn", "make",
            "create", "build", "add", "new", "skill", "tool", "feature",
            "please", "brahma", "jarvis", "with", "using", "use", "be", "able"}
    keep = [w for w in words if w not in skip and len(w) > 2][:4]
    return "_".join(keep)


# ── the code file ────────────────────────────────────────────────────────────

def _strip_fences(code: str) -> str:
    body = str(code or "").strip()
    body = re.sub(r"^```(?:python)?\s*\n?", "", body)
    return re.sub(r"\n?```\s*$", "", body).strip()


def _declaration_span(tree, source: str) -> tuple[int, int, dict, str] | None:
    """Locate the module-level PLUGIN / FEATURE_METADATA assignment.

    Returns (start_line, end_line, parsed_dict, target_name) or None.
    """
    import ast as _ast
    for node in tree.body:
        target = ""
        if isinstance(node, _ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], _ast.Name):
            target = node.targets[0].id
        elif isinstance(node, _ast.AnnAssign) and isinstance(node.target, _ast.Name):
            target = node.target.id
        if target not in ("PLUGIN", "FEATURE_METADATA"):
            continue
        try:
            value = _ast.literal_eval(node.value)
        except Exception:
            return None
        if not isinstance(value, dict):
            return None
        return node.lineno, node.end_lineno, value, target
    return None


def _build_source(code: str, name: str, description: str,
                  parameters: dict) -> str:
    """Guarantee the file declares the contract the loader will actually see.

    The declaration is *rewritten* from the values the registry is about to
    record, rather than trusted from the reply. That is not belt-and-braces: the
    forge may rename a candidate to avoid a collision, and the model's own
    `PLUGIN["name"]` still holds the old name, which the shape gate would then
    refuse — so every collision would fail the forge for no real reason. Any
    extra keys the model put in the declaration (triggers, aliases) are kept.
    """
    import ast as _ast

    body = _strip_fences(code)
    meta = {
        "name": name,
        "description": description,
        "parameters": parameters or dict(skill_registry.DEFAULT_PARAMS),
    }

    try:
        tree = _ast.parse(body)
    except SyntaxError:
        tree = None

    span = _declaration_span(tree, body) if tree is not None else None
    if span is not None:
        start, end, existing, target = span
        merged = dict(existing)
        merged.update(meta)
        lines = body.splitlines()
        replacement = f"{target} = " + json.dumps(merged, indent=4,
                                                  ensure_ascii=False)
        new_lines = lines[: start - 1] + replacement.splitlines() + lines[end:]
        return "\n".join(new_lines).rstrip() + "\n"

    header = (
        '"""\n'
        f"{description}\n\n"
        "Written by JARVIS for itself from a spoken request, and verified by\n"
        "core/skill_crucible.py before it was allowed to load.\n"
        '"""\n\n'
        "PLUGIN = " + json.dumps(meta, indent=4, ensure_ascii=False) + "\n\n"
    )
    return header + body + "\n"


def _sanitize_parameters(raw) -> dict:
    """Keep a usable schema and discard a broken one. A malformed schema would
    silence the whole session's tool list (see main.py's declaration guard), so
    the safe failure is an empty parameter set, not a broken one."""
    if not isinstance(raw, dict) or raw.get("type") != "OBJECT":
        return dict(skill_registry.DEFAULT_PARAMS)
    props = raw.get("properties")
    if not isinstance(props, dict):
        props = {}
    clean_props = {}
    for key, spec in props.items():
        if not isinstance(spec, dict):
            continue
        p_type = str(spec.get("type") or "STRING").upper()
        if p_type not in ("STRING", "INTEGER", "NUMBER", "BOOLEAN", "ARRAY"):
            p_type = "STRING"
        entry = {"type": p_type,
                 "description": str(spec.get("description") or "")[:300]}
        if p_type == "ARRAY":
            entry["items"] = {"type": "STRING"}
        clean_props[str(key)[:60]] = entry
    out = {"type": "OBJECT", "properties": clean_props}
    required = raw.get("required")
    if isinstance(required, list):
        keep = [str(r) for r in required if str(r) in clean_props]
        if keep:
            out["required"] = keep
    return out


def _test_cases(raw, parameters: dict) -> list:
    """At least one case: the empty call. That is the case that catches the most
    common failure of all — a handler that assumes its arguments exist."""
    cases: list = []
    if isinstance(raw, list):
        cases = [item for item in raw[:2] if isinstance(item, dict)]
    elif isinstance(raw, dict):
        cases = [raw]
    if {} not in cases:
        cases.append({})
    return cases or [{}]


# ── the pipeline ─────────────────────────────────────────────────────────────

def forge(goal: str, name: str = "", context: str = "",
          timeout_ms: int = 90_000) -> dict:
    """Synthesize, verify and publish one new tool. Never raises.

    Returns a dict with:
        ok        True when the skill is live and callable
        name      the name it was published under
        message   a spoken sentence describing what happened
        ...       plus path, description, parameters, verdict and the staged
                  path when it did not make it
    """
    goal = str(goal or "").strip()
    if not goal:
        return {"ok": False, "message": "Tell me what you would like me to learn."}

    try:
        from memory import config_manager as cfg
        if not cfg.get_self_forge_enabled():
            return {"ok": False, "already_had": True,
                    "message": "I am not allowed to write new skills at the moment — "
                               "turn that back on in settings and ask me again."}
    except Exception:
        pass   # config unavailable (tests, headless): proceed with defaults

    # ── 1. ASK ───────────────────────────────────────────────────────────────
    prompt = (f"{_SYSTEM}\n\nThe capability the user asked for:\n{goal}\n")
    if context:
        prompt += f"\nExtra context that may matter:\n{context[:1500]}\n"
    print(f"{_TAG} synthesizing: {goal[:70]}")
    reply = _ask_model(prompt, timeout_ms=timeout_ms)
    if not reply.get("code"):
        return {"ok": False, "goal": goal,
                "message": "I could not write that skill — the model did not return "
                           "usable code. Check the API key and try again."}

    description = str(reply.get("description") or "").strip() \
        or f"Learned capability: {goal}"
    parameters = _sanitize_parameters(reply.get("parameters"))
    chosen = _unique_name(str(reply.get("name") or ""), goal)
    if chosen != _clean_name(str(reply.get("name") or "")):
        print(f"{_TAG} name adjusted to '{chosen}' (preferred name was taken or invalid)")

    source = _build_source(reply.get("code"), chosen, description, parameters)
    cases = _test_cases(reply.get("test_cases"), parameters)

    staging = staging_dir()
    try:
        staging.mkdir(parents=True, exist_ok=True)
        candidate = staging / f"{chosen}.py"
        candidate.write_text(source, encoding="utf-8")
    except Exception as e:
        return {"ok": False, "goal": goal,
                "message": f"I could not stage the new skill: {e}"}

    # ── 2. PROVE (with repair rounds) ────────────────────────────────────────
    verdict = crucible.verify(source, chosen, cases=cases, timeout=SMOKE_TIMEOUT_S)
    rounds = 0
    while not verdict.ok and rounds < MAX_REPAIR_ROUNDS and verdict.stage != "safety":
        # Safety is deliberately outside the repair loop: a candidate that tried
        # to do one of the blocked things is not a mistake to iterate on.
        rounds += 1
        print(f"{_TAG} repair round {rounds}: {verdict.reason}")
        fix = _ask_model(_repair_prompt(goal, chosen, source,
                                        verdict.reason, verdict.detail),
                         timeout_ms=timeout_ms)
        fixed_code = fix.get("code") if isinstance(fix, dict) else None
        if not fixed_code:
            break
        source = _build_source(fixed_code, chosen, description, parameters)
        try:
            candidate.write_text(source, encoding="utf-8")
        except Exception:
            pass
        verdict = crucible.verify(source, chosen, cases=cases,
                                  timeout=SMOKE_TIMEOUT_S)

    if not verdict.ok:
        print(f"{_TAG} refused at the {verdict.stage} gate: {verdict.reason}")
        return {
            "ok": False, "goal": goal, "name": chosen,
            "staged_path": str(staging / f"{chosen}.py"),
            "stage": verdict.stage, "repair_rounds": rounds,
            "message": (f"I wrote a '{chosen}' skill but did not activate it: "
                        f"{verdict.reason}. It is parked in staging so nothing "
                        f"unverified can run."),
            "verdict": verdict.as_dict(),
        }

    # ── 3. PUBLISH ───────────────────────────────────────────────────────────
    try:
        from memory import config_manager as cfg
        auto = cfg.get_self_forge_auto()
    except Exception:
        auto = True

    if not auto:
        return {
            "ok": True, "activated": False, "name": chosen, "goal": goal,
            "description": description, "parameters": parameters,
            "staged_path": str(staging / f"{chosen}.py"),
            "message": (f"I wrote and verified a '{chosen}' skill: {description} "
                        f"It is waiting for your go-ahead before I switch it on."),
            "verdict": verdict.as_dict(),
        }

    live = plugins_dir() / f"{chosen}.py"
    try:
        live.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(staging / f"{chosen}.py"), str(live))
    except Exception as e:
        return {"ok": False, "goal": goal, "name": chosen,
                "message": f"The '{chosen}' skill passed verification but could not "
                           f"be installed: {e}"}

    reloaded = _reload_loader()
    if reloaded and not _loader_has(chosen):
        # The rescan ran and the plugin is still not there, which means the loader
        # rejected it — the collision that matters most is with an inline core
        # tool, whose name lives in main.py rather than in actions/, so it cannot
        # be seen by reading the filesystem. Publishing it anyway would leave a
        # file that never becomes a tool and a manifest claiming otherwise, so it
        # goes back to staging and the refusal is reported.
        try:
            shutil.move(str(live), str(staging / f"{chosen}.py"))
        except Exception:
            pass
        return {
            "ok": False, "goal": goal, "name": chosen,
            "staged_path": str(staging / f"{chosen}.py"),
            "stage": "collision",
            "message": (f"I wrote a '{chosen}' skill but something already owns that "
                        f"name, so I did not activate it. It is in staging — ask "
                        f"again and I will pick a different name."),
            "verdict": verdict.as_dict(),
        }
    if not reloaded:
        # No live registry (tests, headless): the file is legitimate and will load
        # at next start. Said plainly rather than claiming it is callable now.
        print(f"{_TAG} plugin registry not live; '{chosen}' will load on next start")

    triggers = [goal] + ([_clean_name(goal).replace("_", " ")] if goal else [])
    skill_registry.record(
        chosen, description=description, parameters=parameters, path=str(live),
        source="forged", triggers=triggers, aliases=[chosen.replace("_", "")],
        verdict=verdict.as_dict())

    _note_improvement(chosen, goal, description, verdict)

    # If this capability was learned because the watch list offered it, retire
    # the candidate so it is never offered again. Lazy import: discovery reads
    # the registry, the registry's records flow from here.
    try:
        from core import skill_discovery
        skill_discovery.mark_forged(goal, chosen)
    except Exception:
        pass

    print(f"{_TAG} live: {chosen} -> {live}")
    return {
        "ok": True, "activated": True, "name": chosen, "goal": goal,
        "description": description, "parameters": parameters,
        "path": str(live), "repair_rounds": rounds, "hot": bool(reloaded),
        "verdict": verdict.as_dict(),
        "message": (f"I learned '{chosen}': {description} "
                    f"(verified: {verdict.detail})"),
    }


def _loader_has(name: str) -> bool:
    """Is `name` a live tool now? False when there is no registry at all, which
    the caller treats as "cannot tell yet" rather than "no"."""
    try:
        from core import plugin_loader
        registry = plugin_loader.active_registry()
        return bool(registry is not None and registry.has(name))
    except Exception:
        return False


def _reload_loader() -> bool:
    """Ask the running plugin registry to pick the new file up. False when no
    registry exists (tests, headless) or the rescan failed — the caller reports
    that honestly instead of claiming the tool is usable."""
    try:
        from core import plugin_loader
        registry = plugin_loader.active_registry()
        if registry is None:
            return False
        before = len(registry.plugins())
        after = registry.reload()
        return after >= before
    except Exception as e:
        print(f"{_TAG} loader reload failed: {e}")
        return False


def _note_improvement(name: str, goal: str, description: str,
                      verdict: crucible.Verdict) -> None:
    """Record the promotion in the same reversible log every other learning
    feature writes to, so one place answers "what has it changed about itself"."""
    try:
        from core import learning
        learning.log_improvement(
            "self_forge",
            f"Wrote and activated a new tool '{name}' for: {goal[:120]}",
            old="(no such capability)",
            new=f"plugins/{name}.py — {description[:120]}",
            applied=True, module="core/skill_forge.py")
    except Exception as e:
        print(f"{_TAG} improvement log skipped: {e}")
    try:
        from core import self_training
        self_training.record_outcome("tool_use", True, f"forged skill {name}")
    except Exception:
        pass


# ── queries the tool exposes ─────────────────────────────────────────────────

def staged() -> list[dict]:
    """Candidates that were written but never made it live, newest first. These
    are the ones a user may want to read or delete by hand."""
    out = []
    try:
        for path in sorted(staging_dir().glob("*.py"),
                           key=lambda p: p.stat().st_mtime, reverse=True):
            out.append({"name": path.stem, "path": str(path),
                        "size": path.stat().st_size,
                        "written": time.strftime(
                            "%Y-%m-%d %H:%M", time.localtime(path.stat().st_mtime))})
    except Exception:
        pass
    return out


def forget_staged(name: str = "") -> int:
    """Delete staged candidates — one by name, or all of them. Returns how many."""
    removed = 0
    try:
        targets = [staging_dir() / f"{name}.py"] if name else list(staging_dir().glob("*.py"))
        for path in targets:
            try:
                if path.exists():
                    path.unlink()
                    removed += 1
            except Exception:
                pass
    except Exception:
        pass
    return removed


def describe_skill(name: str) -> str:
    """One spoken line about a skill, for the tool's `list` action."""
    entry = skill_registry.get(name)
    if entry:
        state = "active" if entry.get("active", True) else "switched off"
        used = int(entry.get("invocations", 0) or 0)
        return (f"{entry.get('name')}: {entry.get('description', '')} "
                f"({state}, used {used} time{'s' if used != 1 else ''})")
    for skill in skill_registry.load_vault().values():
        if skill.name == name or skill.path.name == name:
            if skill.error:
                return f"{name}: could not load — {skill.error}"
            return f"{name}: {skill.description} (vault package)"
    return f"{name}: not found in the registry."


def inventory() -> str:
    """Everything JARVIS can do that it taught itself, as a spoken list."""
    lines: list[str] = []
    forged = skill_registry.list_skills()
    if forged:
        lines.append("Skills I wrote for myself:")
        for entry in forged[:12]:
            state = "" if entry.get("active", True) else " (switched off)"
            lines.append(f"  - {entry.get('name')}: {entry.get('description', '')[:100]}{state}")
    vault = [s for s in skill_registry.load_vault().values() if not s.error]
    if vault:
        lines.append("Installed skill packages:")
        for skill in vault[:12]:
            lines.append(f"  - {skill.name}: {skill.description[:100]}")
    if not lines:
        return ("I have not taught myself anything yet. Ask me to learn something — "
                "\"learn how to check my internet speed\" — and I will write the tool.")
    return "\n".join(lines)
