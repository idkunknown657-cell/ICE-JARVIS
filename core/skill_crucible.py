"""
core/skill_crucible.py — nothing JARVIS writes for itself runs until it passes here.

WHY THIS EXISTS
    core/skill_forge.py lets the model write a brand-new tool in answer to "learn
    how to do X". That is the single most dangerous thing this codebase can do:
    every other module runs code a human already read. So a synthesised skill is
    treated as evidence, not as a plugin — it is verified in a child process,
    never imported into the live JARVIS process, and it only reaches plugins/ if
    it survives every gate below.

THE FOUR GATES, CHEAPEST FIRST
    1. SHAPE      the source parses, is a sane size, and actually declares one of
                  the two tool contracts this project understands — an ICE plugin
                  (`PLUGIN` + `run`) or a feature package (`FEATURE_METADATA` +
                  `execute`). A skill that is merely *shaped* wrong is the most
                  common failure and costs nothing to catch.
    2. SAFETY     a string-level screen for the handful of things a model-written
                  file must never be able to do: repartition the disk, wreck
                  System32, rewrite JARVIS's own source, exfiltrate the API key
                  store, or install itself to run at login. Deliberately a
                  blocklist of *sentences*, not an AST permission model: it is
                  auditable by the person reading it, and it fails loudly.
    3. IMPORTS    every top-level import must already exist in this interpreter.
                  No pip install. A shipped JARVIS runs from a frozen exe with no
                  package manager, so "install what it asked for" would either
                  fail silently or mutate the user's Python — instead the skill is
                  refused and told exactly what was missing.
    4. BEHAVIOUR  the only gate that can catch a lie. The skill is executed once,
                  in a fresh interpreter (`-I`: no user site-packages, no
                  PYTHONPATH, no cwd on sys.path), in a temp directory, with a
                  trimmed environment and a hard timeout, and must return
                  something that is not an error. Nothing it does here can touch
                  the running assistant.

WHAT IT IS NOT
    Not a container. Gates 1-3 are static and gate 4 is a timeout and an env
    scrub — a determined author of hostile code could still get through. The
    point is that *model output* is untrusted-but-bounded, and every promotion is
    logged with the verdict that let it through, so it can be read back and
    reversed (see core/skill_registry.py and the `skill_forge` tool's `delete`).

NOTHING HERE RAISES. Every entry point returns a verdict object; a broken
candidate is a result, not an exception.
"""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

# ── Gate 1: shape ────────────────────────────────────────────────────────────

#: Past this the candidate is not a tool, it is a project. Keeps the prompt
#: honest too — a skill that big never passed a single-pass synthesis.
MAX_SOURCE_BYTES = 40_000
MIN_SOURCE_BYTES = 60

_NAME_HINT = "letters, digits and underscore, starting with a letter or underscore"

# ── Gate 2: safety ───────────────────────────────────────────────────────────
#
# Each entry is (needle, why). Matched case-insensitively against every string
# literal and name in the source, so it catches both a deliberate call and a
# docstring that merely plans one. Kept short on purpose: a screen nobody can
# read is a screen nobody maintains.
BLOCKED = (
    # Repartitioning or destroying volumes — unrecoverable for the user.
    ("format c:", "would reformat the system drive"),
    ("format /fs", "would reformat a volume"),
    ("diskpart", "can repartition and wipe disks"),
    ("vssadmin", "would delete shadow copies / backups"),
    ("bcdedit", "would edit the boot configuration"),
    ("cipher /w", "wipes free space so deleted data cannot be recovered"),
    # System32 / SysWOW64 — the classic way to make Windows unbootable.
    ("system32", "touches the Windows system directory"),
    ("syswow64", "touches the Windows system directory"),
    # JARVIS itself. A skill may not edit the assistant that runs it.
    ("apply_update.bat", "rewrites JARVIS's own updater"),
    ("api_keys.json", "reads or writes JARVIS's stored API keys"),
    ("config/certs", "touches JARVIS's TLS material"),
    ("config\\\\certs", "touches JARVIS's TLS material"),
    # Other people's secrets, sitting in the same user profile.
    ("login data", "reads saved browser passwords"),
    ("wallet.dat", "reads a crypto wallet"),
    (".ssh", "reads SSH private keys"),
    # Installing itself behind the user's back.
    ("schtasks /create", "registers a scheduled task"),
    ("schtasks.exe", "registers a scheduled task"),
    ("currentversion\\\\run", "adds a startup (registry Run key) entry"),
    ("currentversion/run", "adds a startup (registry Run key) entry"),
    ("startup\\\\", "adds a startup-folder entry"),
    ("/startup", "adds a startup-folder entry"),
    # Turning off the protections that make any of this survivable.
    ("netsh advfirewall", "disables the firewall"),
    ("netsh firewall", "disables the firewall"),
    ("taskkill /f /im", "kills processes wholesale"),
    ("uninstall.json", "rewrites the uninstaller manifest"),
)

#: Standard library, so an import of one of these proves nothing and costs a
#: subprocess to check. Anything outside this set is a dependency question.
_STDLIB = set(getattr(sys, "stdlib_module_names", ()) or ())
if not _STDLIB:                                            # pragma: no cover
    _STDLIB = {
        "abc", "argparse", "ast", "asyncio", "base64", "bisect", "calendar",
        "collections", "concurrent", "contextlib", "copy", "csv", "ctypes",
        "dataclasses", "datetime", "decimal", "difflib", "email", "enum",
        "errno", "functools", "glob", "gzip", "hashlib", "heapq", "hmac",
        "html", "http", "importlib", "inspect", "io", "itertools", "json",
        "logging", "math", "mimetypes", "multiprocessing", "operator", "os",
        "pathlib", "pickle", "platform", "pprint", "queue", "random", "re",
        "secrets", "shlex", "shutil", "signal", "socket", "sqlite3", "ssl",
        "stat", "statistics", "string", "struct", "subprocess", "sys",
        "tempfile", "textwrap", "threading", "time", "traceback", "types",
        "typing", "unicodedata", "unittest", "urllib", "uuid", "venv",
        "warnings", "wave", "weakref", "webbrowser", "xml", "zipfile", "zlib",
    }

#: Import names that are this project's own modules rather than dependencies.
_INTERNAL_ROOTS = {"core", "actions", "plugins", "memory", "config", "dashboard"}


@dataclass
class Verdict:
    """The result of one gate. `ok` False always carries a spoken `reason`."""
    ok: bool = False
    stage: str = "shape"
    reason: str = ""
    detail: str = ""
    warnings: list = field(default_factory=list)
    telemetry: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"ok": self.ok, "stage": self.stage, "reason": self.reason,
                "detail": self.detail, "warnings": list(self.warnings),
                "telemetry": dict(self.telemetry)}


# ── Gate 1 ───────────────────────────────────────────────────────────────────

def parse(source: str) -> tuple[ast.Module | None, Verdict | None]:
    """Parse `source`, refusing the sizes and syntax errors that mean the model
    produced prose rather than a tool."""
    if not isinstance(source, str) or len(source.strip()) < MIN_SOURCE_BYTES:
        return None, Verdict(stage="shape", reason="the generated file was empty")
    if len(source.encode("utf-8", "replace")) > MAX_SOURCE_BYTES:
        return None, Verdict(
            stage="shape",
            reason=f"the generated file was over {MAX_SOURCE_BYTES // 1000} KB — "
                   f"too large to be one tool")
    try:
        return ast.parse(source), None
    except SyntaxError as e:
        return None, Verdict(stage="shape",
                             reason=f"it had a syntax error on line {e.lineno}",
                             detail=str(e.msg))


def _module_level_names(tree: ast.Module) -> dict:
    """Every module-level name a candidate defines, so a contract can be found
    without importing anything."""
    names: dict[str, ast.AST] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names[node.name] = node
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    names[t.id] = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names[node.target.id] = node.value or node
    return names


def check_shape(tree: ast.Module | None, expected_name: str = "") -> Verdict:
    """Confirm the candidate declares a tool contract this project can load.

    Two are accepted, and both already exist in the wild here:

      * an ICE plugin — ``PLUGIN`` dict plus ``run(parameters, ...)``
      * a feature package — ``FEATURE_METADATA`` dict plus ``execute(**kwargs)``

    The second is accepted because skills ported in from elsewhere are written
    that way; it costs one extra branch to keep them loadable.
    """
    if tree is None:
        return Verdict(stage="shape", reason="there was nothing to parse")

    names = _module_level_names(tree)

    fn = names.get("run") or names.get("execute")
    if fn is None or not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return Verdict(
            stage="shape",
            reason="it defines neither run(parameters, ...) nor execute(**kwargs), "
                   "so there is no way to call it")

    meta_name = "PLUGIN" if "PLUGIN" in names else (
        "FEATURE_METADATA" if "FEATURE_METADATA" in names else "")
    if not meta_name:
        return Verdict(
            stage="shape",
            reason="it has no PLUGIN (or FEATURE_METADATA) declaration, so "
                   "nothing can decide when to use it")
    try:
        meta = ast.literal_eval(names[meta_name])
    except Exception as e:
        return Verdict(stage="shape",
                       reason=f"its {meta_name} declaration is not a plain dict",
                       detail=str(e))
    if not isinstance(meta, dict):
        return Verdict(stage="shape",
                       reason=f"its {meta_name} declaration is not a dict")

    name = str(meta.get("name") or "").strip()
    if not name:
        return Verdict(stage="shape", reason="its declaration has no name")
    if expected_name and name != expected_name:
        return Verdict(stage="shape",
                       reason=f"it declares itself '{name}' but was staged as "
                              f"'{expected_name}'")

    description = str(meta.get("description") or "").strip()
    if not description:
        return Verdict(stage="shape",
                       reason="its declaration has no description, so the model "
                              "would never know when to call it")

    params = meta.get("parameters", {})
    if meta_name == "PLUGIN" and not (isinstance(params, dict)
                                      and params.get("type") == "OBJECT"):
        return Verdict(stage="shape",
                       reason='its parameters must be a dict with "type": "OBJECT"')

    return Verdict(ok=True, stage="shape")


# ── Gate 2 ───────────────────────────────────────────────────────────────────

def check_safety(tree: ast.Module | None) -> Verdict:
    """Screen every string literal and identifier for the irreversible things a
    generated tool must never do. See BLOCKED for what and why."""
    if tree is None:
        return Verdict(stage="safety", reason="there was nothing to screen")
    haystacks: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            haystacks.append(node.value.lower())
        elif isinstance(node, ast.Name):
            haystacks.append(node.id.lower())
        elif isinstance(node, ast.Attribute):
            haystacks.append(node.attr.lower())
    blob = "\n".join(haystacks)
    for needle, why in BLOCKED:
        if needle in blob:
            return Verdict(
                stage="safety",
                reason=f"it contains '{needle}', which {why} — refused",
                detail=needle)
    return Verdict(ok=True, stage="safety")


# ── Gate 3 ───────────────────────────────────────────────────────────────────

def extract_dependencies(tree: ast.Module) -> list[str]:
    """Top-level external modules the candidate imports."""
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root and root not in _STDLIB and root not in _INTERNAL_ROOTS:
                    found.add(root)
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:
                root = node.module.split(".")[0]
                if root and root not in _STDLIB and root not in _INTERNAL_ROOTS:
                    found.add(root)
    return sorted(found)


def check_dependencies(deps: list[str]) -> Verdict:
    """Every dependency must already be importable *here*.

    No installation is attempted, and that is a deliberate reversal of the
    obvious behaviour: JARVIS ships as a frozen exe with no package manager, so
    the only thing `pip install` could achieve is mutating the user's own Python
    installation behind their back. A skill that needs something absent is
    refused, with the missing names, so the user can decide.
    """
    if not deps:
        return Verdict(ok=True, stage="imports", detail="no external imports")
    missing = []
    for dep in deps:
        try:
            __import__(dep)
        except Exception:
            missing.append(dep)
    if missing:
        return Verdict(
            stage="imports",
            reason="it needs packages that are not installed: "
                   + ", ".join(missing)
                   + " — I will not install anything on my own, so it was not activated",
            detail=",".join(missing))
    return Verdict(ok=True, stage="imports", detail="all imports available: "
                                                   + ", ".join(deps))


# ── Gate 4 ───────────────────────────────────────────────────────────────────

# The child is given this and nothing else. A generated skill has no business
# reading the user's API keys out of the environment, and on Windows Python
# genuinely needs SYSTEMROOT and PATH to start at all.
_ENV_KEEP = ("SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "PATH", "TEMP", "TMP",
             "PATHEXT", "COMSPEC", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE",
             "APPDATA", "LOCALAPPDATA", "USERPROFILE", "HOMEDRIVE", "HOMEPATH",
             "LANG", "PYTHONIOENCODING", "PYTHONUTF8")

_HARNESS = r'''
import importlib.util, inspect, json, sys, traceback

path, cases_json = sys.argv[1], sys.argv[2]
sys.path.insert(0, sys.argv[3])
cases = json.loads(cases_json) or [{}]

_ERROR_PREFIXES = ("error:", "failed:", "could not", "i cannot")

def is_error(out):
    """A tool that reports failure in its return value has failed, even though
    it raised nothing. Same rule the plugin loader's callers rely on."""
    if isinstance(out, dict):
        if out.get("error"):
            return str(out["error"])
        if out.get("success") is False:
            return str(out.get("message") or "returned success=False")
        return ""
    if isinstance(out, str):
        low = out.strip().lower()
        if low.startswith(_ERROR_PREFIXES):
            return out[:200]
    return ""

def call(fn, params):
    """Pass the arguments the way this function actually declares them."""
    sig = inspect.signature(fn)
    kinds = {p.kind for p in sig.parameters.values()}
    if inspect.Parameter.VAR_KEYWORD in kinds:
        return fn(**params)
    if "parameters" in sig.parameters:
        return fn(params)
    if not sig.parameters:
        return fn()
    if inspect.Parameter.VAR_POSITIONAL in kinds:
        return fn(params)
    accepted = {k: v for k, v in params.items() if k in sig.parameters}
    return fn(**accepted)

try:
    spec = importlib.util.spec_from_file_location("candidate_skill", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["candidate_skill"] = mod
    spec.loader.exec_module(mod)
except BaseException:
    print(json.dumps({"ok": False, "stage": "import",
                      "error": traceback.format_exc(limit=6)}))
    raise SystemExit(0)

fn = getattr(mod, "run", None) or getattr(mod, "execute", None)
if fn is None:
    print(json.dumps({"ok": False, "stage": "import",
                      "error": "no run/execute after import"}))
    raise SystemExit(0)

results = []
for i, params in enumerate(cases):
    entry = {"index": i}
    try:
        if inspect.iscoroutinefunction(fn):
            import asyncio
            out = asyncio.run(call(fn, params))
        else:
            out = call(fn, params)
        entry["output"] = str(out)[:400]
        problem = is_error(out)
        entry["ok"] = not problem
        if problem:
            entry["error"] = problem
    except BaseException as e:
        entry["ok"] = False
        entry["error"] = f"{type(e).__name__}: {e}"
        entry["traceback"] = traceback.format_exc(limit=6)
    results.append(entry)

print(json.dumps({"ok": all(r.get("ok") for r in results), "results": results}))
'''


def run_smoke_test(source: str, name: str, cases: list | None = None,
                   timeout: float = 30.0) -> Verdict:
    """Execute the candidate once, in a separate interpreter, and report what
    came back. The plugin is never imported into this process."""
    cases = cases or [{}]
    if not isinstance(cases, list) or not cases:
        cases = [{}]
    # The declared parameters are what the model will one day fill in, so a
    # zero-argument call is the honest first test: it is exactly how the tool
    # gets probed. Cases beyond the first let the forge pass real arguments.
    trimmed = []
    for c in cases[:3]:
        trimmed.append(c if isinstance(c, dict) else {})

    tmp = Path(tempfile.mkdtemp(prefix="jarvis_crucible_"))
    try:
        candidate = tmp / f"{name}.py"
        candidate.write_text(source, encoding="utf-8")
        harness = tmp / "_harness.py"
        harness.write_text(_HARNESS, encoding="utf-8")

        env = {k: os.environ[k] for k in _ENV_KEEP if k in os.environ}
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        env["PYTHONDONTWRITEBYTECODE"] = "1"

        started = time.monotonic()
        try:
            proc = subprocess.run(
                # -I: ignore PYTHONPATH and user site-packages, and keep the
                # working directory off sys.path. The harness adds back only the
                # temp dir, so the candidate can import nothing of ours.
                [sys.executable, "-I", str(harness), str(candidate),
                 json.dumps(trimmed), str(tmp)],
                capture_output=True, text=True, timeout=timeout,
                cwd=str(tmp), env=env)
        except subprocess.TimeoutExpired:
            return Verdict(stage="behaviour",
                           reason=f"it did not finish within {int(timeout)} seconds "
                                  f"and was stopped",
                           telemetry={"elapsed_s": round(timeout, 2)})
        elapsed = round(time.monotonic() - started, 2)

        raw = (proc.stdout or "").strip()
        payload = None
        for line in reversed(raw.splitlines()):
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                try:
                    payload = json.loads(line)
                    break
                except Exception:
                    continue
        if payload is None:
            err = (proc.stderr or raw or "no output").strip()
            return Verdict(stage="behaviour",
                           reason="it crashed before it could be tested",
                           detail=err[:600],
                           telemetry={"elapsed_s": elapsed})

        if not payload.get("ok"):
            bad = next((r for r in payload.get("results", [])
                        if not r.get("ok")), None)
            if bad and bad.get("error"):
                reason = f"calling it raised {bad['error']}"
                detail = bad.get("traceback", "")
            elif bad:
                reason = ("calling it returned an error result: "
                          + str(bad.get("output", ""))[:200])
                detail = ""
            else:
                reason = payload.get("error", "it failed to load")
                detail = payload.get("stage", "")
            return Verdict(stage="behaviour", reason=reason, detail=detail[:600],
                           telemetry={"elapsed_s": elapsed})

        n = len(payload.get("results", []))
        return Verdict(ok=True, stage="behaviour",
                       detail=f"ran cleanly {n} time(s) in {elapsed}s",
                       telemetry={"elapsed_s": elapsed, "runs": n,
                                  "output": (payload["results"][0].get("output", "")
                                             if payload.get("results") else "")})
    except Exception as e:                                  # pragma: no cover
        return Verdict(stage="behaviour",
                       reason=f"the test harness itself failed ({e})")
    finally:
        try:
            for p in tmp.glob("*"):
                p.unlink()
            tmp.rmdir()
        except Exception:
            pass


# ── The whole gauntlet ───────────────────────────────────────────────────────

def verify(source: str, name: str, cases: list | None = None,
           timeout: float = 30.0, skip_behaviour: bool = False) -> Verdict:
    """Run every gate in order and return the first verdict, or an ok one.

    `skip_behaviour` exists for the forge's repair loop: a candidate that failed
    only on behaviour is repaired and re-checked statically first, and running
    the same slow child process twice for a syntax fix wastes the user's time.
    """
    tree, bad = parse(source)
    if bad:
        return bad

    # Safety before shape, deliberately: a candidate that tried to reformat a
    # disk must be refused *for that reason*. Reporting a missing PLUGIN block
    # instead would send the user off to fix the declaration on code that should
    # never be repaired in the first place.
    for gate in (lambda: check_safety(tree),
                 lambda: check_shape(tree, expected_name=name)):
        verdict = gate()
        if not verdict.ok:
            return verdict

    deps = extract_dependencies(tree)
    verdict = check_dependencies(deps)
    if not verdict.ok:
        return verdict

    if not skip_behaviour:
        verdict = run_smoke_test(source, name, cases=cases, timeout=timeout)
        if not verdict.ok:
            return verdict

    return Verdict(ok=True, stage="complete",
                   detail="passed shape, safety, imports and behaviour",
                   telemetry={"dependencies": deps})
