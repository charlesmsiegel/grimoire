"""Durable roll proposals (#162, mechanics Phase 4).

One record per scene in ``<campaign>/proposals.json``. State machine:
pending -> resolving (claimed) -> resolved, or pending -> declined;
resolved/declined -> narrated; superseded is terminal. Every state change
is a compare-and-set under a per-campaign lock; ``commit_narration``
persists a continuation and marks narrated atomically so a supersede that
lands mid-stream drops the stale text. Ids are uuid-based (a rebuilt file
can never re-mint an old id); writes are atomic via temp-file + replace.
Reads never raise on malformed content.
Spec: docs/superpowers/specs/2026-07-12-mechanics-phase4-play-integration-design.md.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from contextlib import contextmanager

from . import campaigns
from .paths import now_iso

NON_TERMINAL = ("pending", "resolving", "resolved", "declined")

_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock(cid: str) -> threading.RLock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(cid, threading.RLock())


@contextmanager
def locked(cid: str):
    """Reentrant per-campaign lock; the route's projection sequence runs
    inside this so concurrent resolved-retries serialize."""
    with _lock(cid):
        yield


def _path(cid: str):
    return campaigns.campaign_root(cid) / "proposals.json"


def _read(cid: str) -> dict:
    p = _path(cid)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(cid: str, data: dict) -> None:
    p = _path(cid)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, p)


def new(cid: str, sid: str, payload: dict) -> dict:
    """Create a fresh pending proposal for the scene, superseding whatever
    non-terminal record (if any) was already there — a new send always
    retires the old one."""
    with _lock(cid):
        data = _read(cid)
        rec = {"id": f"pr-{uuid.uuid4().hex}", "status": "pending",
               "payload": payload, "created": now_iso(), "resolution": None}
        data[sid] = rec
        _write(cid, data)
        return rec


def get(cid: str, sid: str) -> dict | None:
    rec = _read(cid).get(sid)
    return rec if isinstance(rec, dict) else None


def transition(cid: str, sid: str, pid: str, from_states, to: str,
               resolution: dict | None = None) -> bool:
    """Atomic CAS: move the scene's proposal to ``to`` only if it carries
    exactly this id and its status is in ``from_states``. Every state
    change goes through here; a lost transition means another actor moved
    the record (e.g. a supersede mid-resolve) and the caller must stop."""
    with _lock(cid):
        data = _read(cid)
        rec = data.get(sid)
        if (not isinstance(rec, dict) or rec.get("id") != pid
                or rec.get("status") not in tuple(from_states)):
            return False
        rec["status"] = to
        if resolution is not None:
            rec["resolution"] = resolution
        _write(cid, data)
        return True


def claim(cid: str, sid: str, pid: str) -> bool:
    """CAS pending -> resolving; resolve_check may run only after a win."""
    return transition(cid, sid, pid, ("pending",), "resolving")


def supersede(cid: str, sid: str) -> None:
    """A new send or a newer fence retires any non-narrated proposal."""
    with _lock(cid):
        data = _read(cid)
        rec = data.get(sid)
        if isinstance(rec, dict) and rec.get("status") in NON_TERMINAL:
            rec["status"] = "superseded"
            _write(cid, data)


def commit_narration(cid: str, sid: str, pid: str, persist) -> bool:
    """Persist a streamed continuation and mark narrated, crash-recoverably.

    Holding the campaign lock: re-validate the record still carries this
    id in a committable state; if a previous attempt left a
    ``narration_intent``, trim the scene back to it (everything past the
    intent is our own partial continuation — the marker is written before
    any append) except manual roll lines, which survive; record a fresh
    intent; run ``persist()`` (the caller's write of the streamed text);
    write narrated. A supersede that landed while the continuation
    streamed wins here: validation fails, nothing persists — the intent
    marker guarantees everything past it is our own, so no foreign text
    is ever trimmed.
    """
    from . import scenes  # function-level: avoid import-order surprises
    with _lock(cid):
        data = _read(cid)
        rec = data.get(sid)
        if (not isinstance(rec, dict) or rec.get("id") != pid
                or rec.get("status") not in ("resolved", "declined")):
            return False
        intent = rec.get("narration_intent")
        if isinstance(intent, int):
            scenes.trim_continuation(cid, sid, intent)
        try:
            rec["narration_intent"] = len(scenes.read_scene(cid, sid)["messages"])
        except scenes.SceneNotFound:
            rec["narration_intent"] = 0  # scene not yet created: nothing to trim back to
        _write(cid, data)
        persist()
        rec["status"] = "narrated"
        _write(cid, data)
        return True
