"""Append-only per-campaign roll log at <campaign>/rolls.json (Phase 2, #824;
proposal tag + internal locking added #162, Phase 4).

Each entry stores the full engine result — notation and seed included — so any
roll can be replayed bit-for-bit: `replay` re-runs the engine with the stored
seed and reports whether it still matches. Pure JSON IO in the style of
changes.py; entries are never rewritten or deleted, so ids are positional.

Every writer (`append`, `find_by_proposal`, `find_or_append_by_proposal`, and
the existing `repoint_scenes`) takes the same module-local per-campaign lock
(the `_LOCKS`/`_LOCKS_GUARD` pattern from proposals.py). **That lock** is what
keeps concurrent writers from losing entries or racing each other's
read-modify-write; the crash-safe write via `store.atomic` is a separate
property and does not serialize anything on its own (#233 — this docstring used
to credit the temp file for the lock's job). `replay` is read-only and needs no
lock.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from . import atomic, campaigns, dice
from .paths import now_iso


class RollNotFound(Exception):
    pass


_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock(cid: str) -> threading.RLock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(cid, threading.RLock())


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


def _write(cid: str, entries: list[dict]) -> None:
    atomic.write_text(_path(cid), json.dumps(entries, indent=2) + "\n")


def append(cid: str, scene: str | None, label: str | None, result: dict,
           proposal: str | None = None, tier: str | None = None) -> dict:
    """`tier` (a check's outcome label, e.g. "success") is logged as a
    SIBLING key of `result`, never merged into it -- `replay` compares the
    stored `result` dict against a fresh engine draw byte-for-byte, so
    anything added to `result` itself would break that match."""
    with _lock(cid):
        entries = read(cid)
        entry = {"id": f"r{len(entries) + 1}", "ts": now_iso(),
                 "scene": scene, "label": label, "result": result,
                 **({"proposal": proposal} if proposal else {}),
                 **({"tier": tier} if tier else {})}
        entries.append(entry)
        _write(cid, entries)
        return entry


def get(cid: str, rid: str) -> dict:
    for entry in read(cid):
        if entry.get("id") == rid:
            return entry
    raise RollNotFound(rid)


def find_by_proposal(cid: str, pid: str) -> dict | None:
    with _lock(cid):
        for entry in read(cid):
            if entry.get("proposal") == pid:
                return entry
        return None


def find_or_append_by_proposal(cid: str, scene: str | None, label: str | None,
                                result: dict, proposal: str,
                                tier: str | None = None) -> dict:
    """Find-else-append as one locked operation -- the projection uses this
    (not a separate find + append) so a concurrent retry for the same
    proposal never produces two tagged entries. `tier` is a sibling key of
    `result`; see `append`'s docstring for why it can't be merged in."""
    with _lock(cid):
        entries = read(cid)
        for entry in entries:
            if entry.get("proposal") == proposal:
                return entry
        entry = {"id": f"r{len(entries) + 1}", "ts": now_iso(),
                 "scene": scene, "label": label, "result": result,
                 "proposal": proposal, **({"tier": tier} if tier else {})}
        entries.append(entry)
        _write(cid, entries)
        return entry


def repoint_scenes(cid: str, mapping: dict[str, str]) -> None:
    """Follow renamed scene ids in each logged roll's scene field."""
    with _lock(cid):
        entries = read(cid)
        hit = False
        for entry in entries:
            if entry.get("scene") in mapping:
                entry["scene"] = mapping[entry["scene"]]
                hit = True
        if hit:
            _write(cid, entries)


def replay(cid: str, rid: str) -> dict:
    """Re-run a logged roll from its stored seed; `match` is the replay guarantee."""
    entry = get(cid, rid)
    result = dice.roll(entry["result"]["notation"], entry["result"]["seed"])
    return {"entry": entry, "result": result, "match": result == entry["result"]}
