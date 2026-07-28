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
import uuid
from contextlib import contextmanager

from . import atomic, campaigns, locks
from .paths import now_iso

NON_TERMINAL = ("pending", "resolving", "resolved", "declined")


@contextmanager
def locked(cid: str):
    """Reentrant per-campaign lock; the route's projection sequence runs
    inside this so concurrent resolved-retries serialize. Shared with every
    other campaign-scoped mutator (see ``locks.campaign_lock``) so a module
    edit holding the campaign excludes proposal creation/transition — a
    proposal derived from the old pack can never persist after a check
    rename/delete swapped it away."""
    with locks.campaign_lock(cid):
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
    atomic.write_text(_path(cid), json.dumps(data, indent=2) + "\n")


def new(cid: str, sid: str, payload: dict) -> dict:
    """Create a fresh pending proposal for the scene, superseding whatever
    non-terminal record (if any) was already there — a new send always
    retires the old one."""
    with locks.campaign_lock(cid):
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
    with locks.campaign_lock(cid):
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


def update_resolution(cid: str, sid: str, pid: str, resolution: dict) -> bool:
    """Persist projection metadata onto the scene's record WITHOUT changing
    status. Writes ``resolution`` only when the record carries exactly this id
    AND its status is ``resolved`` or ``superseded``; returns False (record
    replaced or moved to another state — caller stops) otherwise.

    This exists so projection metadata (roll_id, line_intent) persists on a
    same-id *superseded* record — whose roll stands in the transcript as
    history per spec — which a plain status CAS (``transition(...,
    ("resolved",), "resolved", res)``) would silently drop once superseded.
    It is NOT a state transition: status is left exactly as found either way.

    A still-``resolved`` record is written through that status-preserving CAS
    (``resolved -> resolved``), so the write lands only while the id and state
    still hold and refuses cleanly against any concurrent legal transition. A
    ``superseded`` record is terminal — no CAS can target it — so its metadata
    is written directly.
    """
    with locks.campaign_lock(cid):
        if transition(cid, sid, pid, ("resolved",), "resolved", resolution):
            return True
        data = _read(cid)
        rec = data.get(sid)
        if (not isinstance(rec, dict) or rec.get("id") != pid
                or rec.get("status") != "superseded"):
            return False
        rec["resolution"] = resolution
        _write(cid, data)
        return True


def supersede(cid: str, sid: str) -> None:
    """A new send or a newer fence retires any non-narrated proposal."""
    with locks.campaign_lock(cid):
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
    with locks.campaign_lock(cid):
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
            # No scene file yet ⇒ empty transcript ⇒ intent 0: a retry
            # trims nothing, which is right when nothing was ever written.
            rec["narration_intent"] = 0
        _write(cid, data)
        persist()
        rec["status"] = "narrated"
        _write(cid, data)
        return True
