"""Persistent usage counting — spots recurring workflows automatically.

The user brief asks JARVIS to notice "frequently used applications, common
workflows, commands I commonly use" WITHOUT being told to remember. This is the
local, private, zero-model half of that: every tool the assistant executes gets
a tick in a per-day counter (config/usage_counts.json). When the same action
recurs across several days, it becomes a candidate for a remembered workflow
preference.

Memory is built in memory_manager; this module only counts. Recording is a
cheap in-memory increment; it is flushed to disk on a schedule so the hot path
never does a file write per action.
"""

import json
import time
from collections import defaultdict
import sys
from pathlib import Path
from threading import Lock

_BASE = (Path(sys.executable).resolve().parent
         if getattr(sys, "frozen", False)
         else Path(__file__).resolve().parent.parent)
CONFIG_DIR = _BASE / "config"
USAGE_PATH = CONFIG_DIR / "usage_counts.json"

_LOCK = Lock()
_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
# _counts[day]["action::<name>"] = n   — keyed by day so a few active days
# produce meaningful "spread" instead of one giant total.


def _today() -> str:
    return time.strftime("%Y-%m-%d")


def record(*slots: str) -> None:
    """Tick one or more usage slots for today. Slots are arbitrary strings but
    conventionally "action::<tool_name>". Thread-safe; never raises."""
    try:
        day = _today()
        with _LOCK:
            for s in slots:
                s = (s or "").strip()[:60]
                if s:
                    _counts[day][s] = _counts[day].get(s, 0) + 1
    except Exception as e:
        print(f"[Usage] ⚠️ record failed: {e}")


def flush() -> int:
    """Persist the in-memory counters (merging with what is on disk, so a crash
    between flushes loses nothing that matters). Returns how many slots were
    written. Never raises."""
    with _LOCK:
        if not _counts:
            return 0
        try:
            data = {}
            if USAGE_PATH.exists():
                try:
                    data = json.loads(USAGE_PATH.read_text(encoding="utf-8"))
                    if not isinstance(data, dict):
                        data = {}
                except Exception:
                    data = {}
            for day, slots in _counts.items():
                day_map = data.get(day)
                if not isinstance(day_map, dict):
                    day_map = {}
                for s, n in slots.items():
                    day_map[s] = int(day_map.get(s, 0)) + n
                data[day] = day_map
            USAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
            USAGE_PATH.write_text(json.dumps(data, ensure_ascii=False),
                                  encoding="utf-8")
            n = len(_counts)
            _counts.clear()
            return n
        except Exception as e:
            print(f"[Usage] ⚠️ flush failed: {e}")
            return 0


def _load_disk() -> dict:
    try:
        data = json.loads(USAGE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def workflow_candidates(min_days: int = 2, min_count: int = 3,
                        label: str = "action::") -> list[dict]:
    """Actions done often AND on several different days — the signature of a
    recurring workflow. Returns sorted [{slot, count, days}] newest-spread-first.
    Cheap: a single small JSON read, no model call."""
    data = _load_disk()
    if not data:
        return []
    per_slot: dict[str, dict] = {}
    for day, slots in data.items():
        if not isinstance(slots, dict):
            continue
        for s, n in slots.items():
            if not s.startswith(label):
                continue
            m = per_slot.setdefault(s, {"count": 0, "days": set()})
            try:
                m["count"] += int(n)
                m["days"].add(day)
            except (TypeError, ValueError):
                continue
    out = []
    for s, m in per_slot.items():
        if m["count"] >= min_count and len(m["days"]) >= min_days:
            out.append({"slot": s, "count": m["count"],
                        "days": sorted(m["days"])})
    out.sort(key=lambda r: (-len(r["days"]), -r["count"]))
    return out


def prune_older_than(days: int = 30) -> int:
    """Drop usage rows older than `days` days. Returns rows removed."""
    data = _load_disk()
    if not data:
        return 0
    cutoff = time.strftime("%Y-%m-%d",
                           time.localtime(time.time() - days * 86400))
    gone = [d for d in data if d < cutoff]
    for d in gone:
        data.pop(d)
    if gone:
        try:
            USAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
            USAGE_PATH.write_text(json.dumps(data, ensure_ascii=False),
                                  encoding="utf-8")
        except Exception as e:
            print(f"[Usage] ⚠️ prune failed: {e}")
    return len(gone)