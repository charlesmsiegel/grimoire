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

from . import atomic, checks, locks, rolls
from .campaigns import paths as campaigns_paths
from .paths import now_iso
from .scenes import (
    paths as scenes_paths,
    read as scenes_read,
    serialize as scenes_serialize,
    write as scenes_write,
)

NON_TERMINAL = ("pending", "resolving", "resolved", "declined")

# The complete set of edges the generic CAS may walk (#242). Enumerating EDGES
# rather than target statuses is the point: an allowlist of targets still let
# ``resolved -> declined`` through, which keeps the resolution but puts the
# record outside what ``project`` accepts — so the next ``supersede`` would
# heal nothing and retire the roll's only recovery handle unprojected.
#
# Every edge that leaves a projectable state is therefore absent here and owned
# by a function that heals first: ``supersede`` (-> superseded) and
# ``commit_narration`` (-> narrated). ``resolved -> resolved`` is the
# status-preserving CAS ``update_resolution`` uses to persist projection
# metadata.
#
# Healing inside ``transition`` instead is NOT the alternative: that same
# ``resolved -> resolved`` CAS would recurse back through ``project`` without
# bound.
TRANSITION_EDGES = frozenset({
    ("pending", "resolving"),     # claim
    ("pending", "declined"),      # the player declines the roll
    ("resolving", "pending"),     # resolve_check failed; hand the chip back
    ("resolving", "resolved"),    # the roll landed
})

# ...and the subset of those that may CARRY a resolution. This enforces the
# data invariant the rest of the module leans on: a resolution containing a
# roll ``result`` only ever exists on a status ``project`` accepts (or on
# ``narrated``, which by construction projected on the way there). Without it a
# legal edge like ``pending -> declined`` could store a result, which ``heal``
# reads as projectable while ``project`` refuses the status — and the roll is
# then discarded by the next retirement, unlogged.
#
# One edge, because storing a resolution and amending one are different
# operations: this is the roll landing, and ``update_resolution`` owns the
# metadata amendments (and refuses to change a `result`). A generic
# ``resolved -> resolved`` edge could not tell the two apart.
RESOLUTION_EDGES = frozenset({("resolving", "resolved")})


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
    return campaigns_paths.campaign_root(cid) / "proposals.json"


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
    exactly this id and its status is in ``from_states``. A lost transition
    means another actor moved the record (e.g. a supersede mid-resolve) and
    the caller must stop.

    Every ``(from_state, to)`` pair the caller declares must be in
    ``TRANSITION_EDGES``, and the whole declaration is checked — not just the
    edge that happens to win the CAS — because passing a from-state is an
    assertion that it may legally reach ``to``. Edges that leave a projectable
    state are absent by design; they belong to ``supersede`` and
    ``commit_narration``, which heal first (#242). Misuse raises rather than
    returning False: an illegal edge is a programming error, not the lost race
    False is reserved for, and callers treat False as "someone else won, stop"."""
    illegal = sorted((f, to) for f in from_states if (f, to) not in TRANSITION_EDGES)
    if illegal:
        raise ValueError(
            f"proposals.transition: illegal edge(s) {illegal} — a record may only "
            "leave a projectable state through supersede() or commit_narration(), "
            "which heal first (#242)")
    if resolution is not None:
        unresolved = sorted((f, to) for f in from_states if (f, to) not in RESOLUTION_EDGES)
        if unresolved:
            raise ValueError(
                f"proposals.transition: edge(s) {unresolved} may not carry a "
                "resolution — a roll result stored on a status project() refuses "
                "is discarded by the next retirement (#242)")
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
    history per spec — which a plain status CAS would silently drop once
    superseded. It is NOT a state transition: status is left exactly as found.

    It owns this write outright rather than borrowing ``transition``'s
    ``resolved -> resolved`` edge, because that edge was indistinguishable from
    "replace the whole resolution": a direct caller could swap in a different
    ``result`` after projection, and the next ``project`` would find the
    ORIGINAL roll by proposal id (``find_or_append_by_proposal`` is idempotent
    by tag) while formatting its transcript line from the REPLACEMENT — a roll
    log and a transcript that contradict each other. The generic edge is gone;
    the ``result`` guard below is what makes this write metadata-only.
    """
    with locks.campaign_lock(cid):
        data = _read(cid)
        rec = data.get(sid)
        if (not isinstance(rec, dict) or rec.get("id") != pid
                or rec.get("status") not in ("resolved", "superseded")):
            return False
        current = rec.get("resolution")
        if (isinstance(current, dict) and "result" in current
                and resolution.get("result") != current["result"]):
            raise ValueError(
                "proposals.update_resolution persists projection metadata; it "
                "cannot change a resolved roll's `result` — the logged roll and "
                "its transcript line would disagree (#242)")
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
            res = {**res, "line_intent": len(scenes_read.read_scene(cid, sid)["messages"])}
            update_resolution(cid, sid, pid, res)
        line = checks.format_check_roll(res)
        if not any(m.get("speaker") == scenes_serialize.ROLL_SPEAKER and m["content"] == line
                   for m in scenes_read.read_scene(cid, sid)["messages"][res["line_intent"]:]):
            scenes_write.append_message(cid, sid, "assistant", line,
                                        speaker=scenes_serialize.ROLL_SPEAKER)
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
    loss windows are the phase-4 spec's two accepted fence-handoff ones.

    The closing check is a backstop, not flow control. This function's notion
    of "projectable" (a resolution carrying a ``result``) is deliberately
    broader than ``project``'s (that, AND a resolved/superseded status), and
    every gap found between the two has been a way to retire a roll unlogged.
    ``TRANSITION_EDGES`` / ``RESOLUTION_EDGES`` and ``update_resolution``'s own
    guard now make the gap unreachable — ``narrated`` is the sole status that
    can hold a projectable resolution ``project`` declines, and it projected on
    its way there. If some future edge reopens it, this raises instead of
    letting the caller retire the record: loud beats a silent orphan, which is
    the whole point of #242."""
    # Reentrant, so free under the retirement paths that already hold it; taken
    # explicitly so the read and the projection are one atomic step even for a
    # direct caller — otherwise `project` could lose a race and return None for
    # a reason the backstop below would misread as a broken invariant.
    with locks.campaign_lock(cid):
        rec = get(cid, sid)
        if (isinstance(rec, dict) and isinstance(rec.get("resolution"), dict)
                and "result" in rec["resolution"]):
            if project(cid, sid, rec["id"]) is None and rec.get("status") != "narrated":
                raise RuntimeError(
                    f"proposals.heal: record {rec['id']} carries a roll result on "
                    f"status {rec.get('status')!r}, which project() refuses — "
                    "retiring it would discard the roll (#242)")


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
            scenes_write.trim_continuation(cid, sid, intent)
        try:
            rec["narration_intent"] = len(scenes_read.read_scene(cid, sid)["messages"])
        except scenes_paths.SceneNotFound:
            # No scene file yet ⇒ empty transcript ⇒ intent 0: a retry
            # trims nothing, which is right when nothing was ever written.
            rec["narration_intent"] = 0
        _write(cid, data)
        persist()
        rec["status"] = "narrated"
        _write(cid, data)
        return True
