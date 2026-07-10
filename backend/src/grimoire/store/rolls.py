"""Append-only per-campaign roll log at <campaign>/rolls.json (Phase 2, #824).

Each entry stores the full engine result — notation and seed included — so any
roll can be replayed bit-for-bit: `replay` re-runs the engine with the stored
seed and reports whether it still matches. Pure JSON IO in the style of
changes.py; entries are never rewritten or deleted, so ids are positional.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import campaigns, dice
from .paths import now_iso


class RollNotFound(Exception):
    pass


def _path(cid: str) -> Path:
    return campaigns.campaign_root(cid) / "rolls.json"


def read(cid: str) -> list[dict]:
    p = _path(cid)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    return data if isinstance(data, list) else []


def append(cid: str, scene: str | None, label: str | None, result: dict) -> dict:
    entries = read(cid)
    entry = {"id": f"r{len(entries) + 1}", "ts": now_iso(),
             "scene": scene, "label": label, "result": result}
    entries.append(entry)
    _path(cid).write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8")
    return entry


def get(cid: str, rid: str) -> dict:
    for entry in read(cid):
        if entry.get("id") == rid:
            return entry
    raise RollNotFound(rid)


def replay(cid: str, rid: str) -> dict:
    """Re-run a logged roll from its stored seed; `match` is the replay guarantee."""
    entry = get(cid, rid)
    result = dice.roll(entry["result"]["notation"], entry["result"]["seed"])
    return {"entry": entry, "result": result, "match": result == entry["result"]}
