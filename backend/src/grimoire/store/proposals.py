"""Durable roll proposals (#162, mechanics Phase 4).

One record per scene in ``<campaign>/proposals.json``. State machine:
pending -> resolving (claimed) -> resolved, or pending -> declined;
resolved/declined -> narrated; superseded is terminal. Every state change
is a compare-and-set under a per-campaign lock; ``commit_narration``
persists a continuation and marks narrated atomically so a supersede that
lands mid-stream drops the stale text. Ids are uuid-based (a rebuilt file
can never re-mint an old id); writes are atomic via temp-file + replace.
Reads never raise on malformed content.

This module also owns projection (``project``) — writing a resolved record's
roll and 🎲 line into the campaign's roll log and transcript — because the
two are inseparable: a record with a projectable resolution must never leave
the projectable states before its projection completes, or the roll stands in
rolls.json without its transcript line forever. All three exits — retire
(``supersede``), replace (``new``) and narrate (``commit_narration``, since
``narrated`` is outside what ``project`` accepts) — call ``heal`` themselves
(#242), so that is a guarantee of the state machine rather than a rule each
caller must remember. A guard test keeps this module the sole writer of
proposals.json, since a direct writer would bypass all three.
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
    retires the old one. Heals first: the record about to be overwritten is
    the only recovery handle for its own projection (see ``heal``)."""
    with locks.campaign_lock(cid):
        heal(cid, sid)
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
    """A new send or a newer fence retires any non-narrated proposal. Heals
    first: retirement must never outrun the record's own projection (see
    ``heal``)."""
    with locks.campaign_lock(cid):
        heal(cid, sid)
        data = _read(cid)
        rec = data.get(sid)
        if isinstance(rec, dict) and rec.get("status") in NON_TERMINAL:
            rec["status"] = "superseded"
            _write(cid, data)


def project(cid: str, sid: str, pid: str) -> dict | None:
    """Idempotent, crash-recoverable projection of a resolved proposal into the
    roll log and transcript (roll entry + 🎲 line + the ``roll_id`` /
    ``line_intent`` metadata that makes both re-findable). Runs entirely under
    the per-campaign lock (pure file I/O, no LLM), so concurrent retries
    serialize. The updated resolution is carried forward across each CAS —
    never rebuilt from a stale local — so the roll_id survives the line_intent
    write.

    Defensive re-validation: a caller may check status *before* acquiring this
    lock, so a supersede + brand-new record for the scene can land in that
    narrow window. If the scene's current record no longer carries this
    proposal id, or has no stored resolution yet, another actor won — return
    None and do nothing (no roll append, no line). Deliberately NOT a status
    check: a record that still carries this id but was superseded after
    resolving (status "superseded") must still project — its roll stands in
    the transcript as history per spec; only the automatic continuation is
    cancelled (by ``commit_narration``), not the roll projection itself. The
    roll_id/line_intent backfills persist via ``update_resolution``, which
    writes metadata without touching terminal status, so a same-id superseded
    record keeps them (a status CAS would silently lose and drop them)."""
    from . import checks, rolls, scenes  # function-level: avoid import cycles
    with locks.campaign_lock(cid):
        rec = get(cid, sid)
        if (rec is None or rec.get("id") != pid
                or rec.get("status") not in ("resolved", "superseded")
                or not isinstance(rec.get("resolution"), dict)):
            return None
        res = dict(rec["resolution"])
        entry = rolls.find_or_append_by_proposal(
            cid, sid, checks.roll_label(res), res["result"], proposal=pid,
            tier=res.get("tier"))
        res = {**res, "roll_id": entry["id"]}
        update_resolution(cid, sid, pid, res)
        if "line_intent" not in res:
            res = {**res, "line_intent": len(scenes.read_scene(cid, sid)["messages"])}
            update_resolution(cid, sid, pid, res)
        line = checks.format_check_roll(res)
        if not any(m.get("speaker") == scenes.ROLL_SPEAKER and m["content"] == line
                   for m in scenes.read_scene(cid, sid)["messages"][res["line_intent"]:]):
            scenes.append_message(cid, sid, "assistant", line,
                                  speaker=scenes.ROLL_SPEAKER)
        return res


def heal(cid: str, sid: str) -> None:
    """Complete the scene's current record's projection before that record is
    retired or replaced. Called by ``supersede`` and ``new`` themselves, so the
    guarantee holds for every caller — present and future — rather than
    depending on each one remembering (#242).

    The record is the only recovery handle for a projection crash (roll
    tagged, line missing): the stale-retry heal in the POST roll-proposal
    route matches on the record's id and only projects superseded records that
    still carry a resolution, so once ``new`` overwrites ``data[sid]`` (or the
    frontend stops offering the superseded record) the roll would stand in
    rolls.json without its transcript line forever. Projection is idempotent
    pure file I/O, so healing an already-complete record is a cheap no-op.
    Only records whose resolution carries a roll ``result`` can project —
    declined records never store a resolution (and would have no roll to
    project if they somehow did), and pending/resolving records have nothing
    resolved yet; those are retired/replaced as before.

    The heal is idempotent, and a crash during it leaves the record current
    (retirement never ran), so the next attempt re-heals. The only remaining
    loss windows are the phase-4 spec's two accepted fence-handoff ones."""
    rec = get(cid, sid)
    if (isinstance(rec, dict) and isinstance(rec.get("resolution"), dict)
            and "result" in rec["resolution"]):
        project(cid, sid, rec["id"])


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
        # Third retirement of the projectable state, after supersede/new:
        # `narrated` is outside project()'s accepted statuses, so a record
        # narrated before it projected could never project again. Heal after
        # validation (a lost CAS must leave the record untouched) and re-read,
        # because heal persists roll_id/line_intent through `update_resolution`
        # and the stale local `data` would write them straight back out.
        heal(cid, sid)
        data = _read(cid)
        rec = data[sid]
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
