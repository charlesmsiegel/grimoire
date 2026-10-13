"""Hand edits to the continuity ledger.

`GET /campaigns/{cid}/ledger` (in `campaigns.py`) has always been able to *show*
what a campaign owes; until this module nothing could change any of it by hand.
The only writer of plot.json, commitments.json, facts.json and relationships.json
was `absorb/apply.py`, reached through `PUT /chronicle` — so a thread the model
never noticed had closed stayed open forever, a commitment it invented could not
be removed, and a mistyped fact stood as a dated truth.

Three rules hold across every route here.

**One lock, the absorb pass's own.** Every write takes
`locks.campaign_lock(cid)`, which is the lock `PUT /chronicle` holds across its
whole record-then-apply sequence. So a hand edit cannot interleave with a save
landing and read a ledger that is half updated. The lock is reentrant, so the
store mutators that take it themselves are safe to call inside.

**Every write is journalled as `manual`, and journalled writes are
reversible.** `store/undo.py`'s `journalled()` was written for exactly this —
"a hand edit made outside the absorb pipeline" — and snapshots the record
before the write and seals it after. So a hand edit is accountable rather than
being the one kind of change a campaign cannot explain: it lands in
`journal.json` and shows up in the play view's Changes panel under History,
which already labels a `manual` row "edited by hand" and offers the same Undo
an absorb's edits get.

Best-effort, and deliberately so — `journalled` swallows its own failure and
that policy is older than this module: the write has already happened by the
time the journal is appended, and losing the history of an edit is a smaller
harm than 500-ing an edit that landed. So the guarantee is "a write that was
journalled can be reversed", not "a write cannot land unjournalled". The fact
routes below hold to the same rule rather than inventing a stricter one they
could not keep.

Not in the ledger's own Recent-changes section, which is a different log:
`store/changes.py` is the ROLLING view — one entry per record, replaced by the
next write-back, answering "what does this record's latest absorb say". A hand
edit deliberately does not write it, because that log is about what the pass
extracted and a correction is not an extraction.

**Only records, never logs.** The relationship history and the change log record
what happened; there is nothing here that edits either, because editing a log
falsifies history rather than correcting state. Undo is how something in them is
reversed, and it already exists.

The fact routes carry the rule `store/facts.py` argues and
`test_absorb_writer_guard.py` enforces: grimoire never edits a fact, the user
may. `PUT .../facts/{fid}` is that edit, and it is the only caller of
`facts.set_text` in the app.
"""

from __future__ import annotations

import json
import logging
from typing import NamedTuple

from fastapi import APIRouter, HTTPException

from .. import store
from ..store.continuity import doc as continuity_doc
from ..store.continuity import effective as continuity_effective
from ..store.continuity import review as continuity_review
from ..store.regex import view as regex_view
from ..store.scenes import identity as scenes_identity
from .models import (
    ChronicleLineSave,
    CommitmentSave,
    FactRecord,
    FactRetire,
    FactSave,
    KnowledgeEdit,
    KnowledgeFingerprint,
    KnowledgePost,
    KnowledgeSave,
    RelationshipSave,
    ThreadSave,
)

router = APIRouter()
log = logging.getLogger("grimoire.routes.ledger")


def _campaign_or_404(cid: str) -> None:
    if not store.campaigns.campaign_exists(cid):
        raise HTTPException(status_code=404, detail="campaign not found")


def _label(title: str, what: str) -> str:
    """The journal row's human name, in the shape the panel already renders:
    `<record> — <kind>`, like `put_group_state`'s."""
    return f"{title} — {what}"


def _live_target(cid: str, prefix: str, rid: str, physical: bool) -> str:
    """The record a hand edit addressed to `rid` should land on.

    A thread or commitment merged into another (a continuity alias) is hidden
    behind its canonical, and its own status no longer counts -- so an edit
    addressed to it lands on the canonical rather than vanishing into a record
    nothing reads (capstone spec §7.2). Only a LIVE alias redirects:
    `live_canon` follows a merge only to a record that is really there, so a
    merge into something since deleted, or a hand-edited cross-type one, leaves
    the edit on the record it named instead of conjuring the missing target.
    ``physical`` asks for the record itself regardless.

    A continuity.json whose aliases cannot be read is a refusal rather than "no
    merges": guessing would write to a record that may be hidden, where the
    edit would silently count for nothing (spec §3.9).
    """
    if physical:
        return rid
    if set(continuity_doc.malformed(cid)) & {"file", "aliases"}:
        raise HTTPException(status_code=409, detail={
            "kind": "malformed",
            "detail": "continuity.json cannot be read, so it is unclear which record "
                      "this edit belongs to; repair it, or edit the record itself"})
    live = continuity_effective.live_canon(cid).get(f"{prefix}:{rid}")
    return live.partition(":")[2] if live else rid


def _refuse_unsafe_delete(cid: str, ref: str, force: bool) -> None:
    """Refuse, before anything is written, a delete whose continuity cascade
    could not be carried out or would orphan merged records.

    A continuity.json whose aliases or links cannot be read gives no honest
    answer to "is anything merged into this?", and a cascade that failed after
    the delete had landed would report a completed write as an error -- so a
    malformed file refuses the delete up front. A record others are merged into
    is refused unless forced: deleting it un-merges them, which is the reader's
    call to make knowingly (spec §5.7).
    """
    if set(continuity_doc.malformed(cid)) & {"file", "aliases", "links"}:
        raise HTTPException(status_code=409, detail={
            "kind": "malformed",
            "detail": "continuity.json cannot be read; repair it before deleting records"})
    merged = continuity_review.merged_sources(cid, ref)
    if merged and not force:
        raise HTTPException(status_code=409, detail={
            "kind": "has_merged_records",
            "detail": "other records are merged into this one; unmerge them first, "
                      "or delete anyway",
            "refs": merged})


#: What a partial delete tells the reader, by the noun that landed. A thread or
#: commitment delete is journalled, so Undo restores it; an event delete writes
#: no journal row and an event is never merged, so its leftovers are links, and
#: Reviewed links / merges lists each one as broken with Remove link.
_PARTIAL_DELETE = {
    "thread": "the thread was deleted, but its merges and links could not all be "
              "removed ({error}); the delete can be undone",
    "commitment": "the commitment was deleted, but its merges and links could not all "
                  "be removed ({error}); the delete can be undone",
    "event": "the event was deleted, but its links could not all be removed ({error}); "
             "remove the rest from Reviewed links / merges",
}


def forget_or_partial(cid: str, ref: str, *, name: str, noun: str) -> None:
    """Drop the merges and links naming a record whose delete has just landed,
    or answer a 500 ``partial_delete`` that says the delete landed.

    `_refuse_unsafe_delete` already refused a file it could not read, so what is
    left is a disk that fails mid-write, or a file garbled under the hold. By
    then the record is gone, and a bare server error would report that
    completed write as a failure (spec §5.7). So the body names what landed and
    how to finish.

    The bump is explicit, and it is load-bearing. The activity middleware
    stamps only a response below 300, and bumps on an unhandled raise; an
    `HTTPException` becomes a response inside the middleware and reaches
    neither. Without the bump a write that landed would leave the campaign's
    token where it was, and a `POST /advance` expectation taken before the
    delete would still pass (CLAUDE.md, "A campaign carries a write token").

    The error row is explicit for the same reason: an unhandled raise reached
    the terminal and the error store, and an `HTTPException` reaches neither,
    so without it nothing would record that continuity.json was left half
    cleaned. It names ids only -- the campaign, the ref and the exception's
    class, with its frames -- never ``name``, which is the record's title.
    """
    try:
        continuity_review.forget_ref(cid, ref, name=name)
    except (OSError, continuity_doc.ContinuityError) as e:
        store.errors.record_exception(
            e, "ledger", campaign=cid,
            detail=f"delete of {ref} landed, but its merges and links could not all "
                   f"be removed ({type(e).__name__})")
        store.revision.bump(cid)
        raise HTTPException(status_code=500, detail={
            "kind": "partial_delete", "landed": [noun],
            "detail": _PARTIAL_DELETE[noun].format(error=type(e).__name__)}) from e


# ------------------------------------------------------------- moving a record


def _stored_scene(record) -> str:
    """A record's stored ``last_scene``, as the PUT routes have always passed it
    back to `set_movement` when the payload names none."""
    return (record or {}).get("last_scene", "") if isinstance(record, dict) else ""


def _closure_scene(stored: str, scene: str | None, keep_later: bool) -> str | None:
    """The ``last_scene`` a move writes: None (the beat's own scene) unless a
    closure asked to keep the later of the evidence scene and the stored one."""
    if keep_later and scene is not None:
        return continuity_effective.later_scene(stored, scene)
    return None


def move_thread(cid: str, pid: str, *, title: str = "", status: str = "", beat: str = "",
                scene: str | None = None, keep_later_scene: bool = False,
                label: str) -> None:
    """Move one thread -- the physical record `pid`, no redirect -- in one
    journalled write. The caller holds `campaign_lock(cid)`.

    The ledger's PUT and the continuity review's closure both come through
    here, so a hand edit to a thread still has exactly one home (capstone
    §12.4). ``title=""`` and an unknown ``status`` keep what is stored, as
    `plot.set_movement` reads them. ``scene=None`` keeps the stored
    ``last_scene``. ``keep_later_scene`` is the closure's rule: the beat is
    filed under the evidence scene the reader chose, but ``last_scene`` stays
    at the later of that scene and the stored one by play order. Both land in
    the ONE write `set_movement` makes, so a failure leaves nothing behind --
    a second write to put ``last_scene`` forward could fail after the first
    had moved it backwards, outside any journal row.
    """
    stored = _stored_scene(store.plot.get(cid, pid))
    with store.undo.journalled(cid, {"w": "plot", "id": pid},
                               kind="plot", ref={"kind": "plot", "id": pid},
                               field="thread", label=label):
        store.plot.set_movement(cid, pid, title, status, beat,
                                scene if scene is not None else stored,
                                last_scene=_closure_scene(stored, scene, keep_later_scene))


def move_commitment(cid: str, mid: str, *, title: str = "", kind: str = "",
                    status: str = "", due: str | None = None, beat: str = "",
                    scene: str | None = None, keep_later_scene: bool = False,
                    label: str) -> None:
    """`move_thread` for a commitment. ``kind=""`` keeps the stored kind, and
    `due` is three-valued as `commitments.set_movement` documents: None keeps
    the stored deadline, ``""`` clears it, text sets it -- which is how the
    review's explicit due copy lands as its own journalled row (§5.1)."""
    stored = _stored_scene(store.commitments.get(cid, mid))
    with store.undo.journalled(cid, {"w": "commitment", "id": mid},
                               kind="commitment", ref={"kind": "commitment", "id": mid},
                               field="commitment", label=label):
        store.commitments.set_movement(cid, mid, title, kind, status, due, beat,
                                       scene if scene is not None else stored,
                                       last_scene=_closure_scene(stored, scene,
                                                                 keep_later_scene))


# --------------------------------------------------------------------- threads


@router.post("/campaigns/{cid}/ledger/threads")
def post_thread(cid: str, body: ThreadSave):
    """Open a thread by hand. The id is slugified from the title, like every
    other id the absorb pass allocates for a new thread."""
    _campaign_or_404(cid)
    title = body.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="a thread needs a title")
    with store.locks.campaign_lock(cid):
        existing = store.plot.read(cid)
        pid = store.paths.uniquify(store.paths.slugify(title), lambda p: p in existing)
        with store.undo.journalled(cid, {"w": "plot", "id": pid},
                                   kind="plot", ref={"kind": "plot", "id": pid},
                                   field="thread", label=_label(title, "thread")):
            store.plot.set_movement(cid, pid, title, body.status or "open",
                                    body.beat or "", body.scene or "")
    return {"id": pid}


@router.put("/campaigns/{cid}/ledger/threads/{pid}")
def put_thread(cid: str, pid: str, body: ThreadSave, physical: bool = False):
    """Edit one thread: its title, its status, the scene it was last moved in,
    and optionally a beat to append.

    A beat is APPENDED rather than replaced, because that is what a beat is —
    `plot.set_movement` has always added one and there is no such thing as
    editing the list in place. Sending none leaves the beats alone, which is the
    ordinary case for closing a thread.

    Addressed to a thread merged into another, the edit lands on the canonical
    (`_live_target`); the response's ``id`` says which record was written.
    """
    _campaign_or_404(cid)
    with store.locks.campaign_lock(cid):
        if store.plot.get(cid, pid) is None:
            raise HTTPException(status_code=404, detail="thread not found")
        target = _live_target(cid, "thread", pid, physical)
        title = body.title.strip()
        label = (_label(title or pid, "thread") if target == pid else
                 f"{continuity_review.reader_name(cid, 'thread:' + target, start=True)}"
                 f" — thread (via merged "
                 f"{continuity_review.reader_name(cid, 'thread:' + pid)})")
        # `set_movement` reads a blank title or an unknown status as "keep what
        # is stored", which is the behaviour this route wants too: a payload
        # that only closes a thread must not blank its title.
        move_thread(cid, target, title=title, status=body.status or "",
                    beat=body.beat or "", scene=body.scene, label=label)
    return {"ok": True, "id": target}


@router.delete("/campaigns/{cid}/ledger/threads/{pid}")
def delete_thread(cid: str, pid: str, force: bool = False):
    """Remove a thread outright — for one the extraction invented, which is a
    different act from closing it. A closed thread stays on the ledger saying it
    happened; that is the wrong thing to say about a thread that never did."""
    _campaign_or_404(cid)
    with store.locks.campaign_lock(cid):
        thread = store.plot.get(cid, pid)
        if thread is None:
            raise HTTPException(status_code=404, detail="thread not found")
        _refuse_unsafe_delete(cid, f"thread:{pid}", force)
        title = str(thread.get("title") or pid)
        with store.undo.journalled(cid, {"w": "plot", "id": pid},
                                   kind="plot", ref={"kind": "plot", "id": pid},
                                   field="thread", label=_label(title, "thread deleted")):
            store.plot.restore(cid, pid, None)
        # Under the same hold: the id is free the moment this returns, and a
        # thread recreated under it must not inherit this one's merges or links.
        forget_or_partial(cid, f"thread:{pid}", name=title, noun="thread")
    return {"ok": True}


# ----------------------------------------------------------------- commitments


@router.post("/campaigns/{cid}/ledger/commitments")
def post_commitment(cid: str, body: CommitmentSave):
    _campaign_or_404(cid)
    title = body.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="a commitment needs a title")
    with store.locks.campaign_lock(cid):
        existing = store.commitments.read(cid)
        mid = store.paths.uniquify(store.paths.slugify(title), lambda m: m in existing)
        with store.undo.journalled(cid, {"w": "commitment", "id": mid},
                                   kind="commitment", ref={"kind": "commitment", "id": mid},
                                   field="commitment", label=_label(title, "commitment")):
            store.commitments.set_movement(cid, mid, title, body.kind or "promise",
                                           body.status or "open", body.due,
                                           body.beat or "", body.scene or "")
    return {"id": mid}


@router.put("/campaigns/{cid}/ledger/commitments/{mid}")
def put_commitment(cid: str, mid: str, body: CommitmentSave, physical: bool = False):
    """Edit one commitment. `due` is three-valued the whole way down, as
    `commitments.set_movement` documents: absent keeps the stored deadline, `""`
    clears it, text sets it. Addressed to a merged commitment, the edit lands on
    its canonical, as `put_thread`'s does."""
    _campaign_or_404(cid)
    with store.locks.campaign_lock(cid):
        if store.commitments.get(cid, mid) is None:
            raise HTTPException(status_code=404, detail="commitment not found")
        target = _live_target(cid, "commitment", mid, physical)
        title = body.title.strip()
        label = (_label(title or mid, "commitment") if target == mid else
                 f"{continuity_review.reader_name(cid, 'commitment:' + target, start=True)}"
                 f" — commitment (via merged "
                 f"{continuity_review.reader_name(cid, 'commitment:' + mid)})")
        move_commitment(cid, target, title=title, kind=body.kind or "",
                        status=body.status or "", due=body.due, beat=body.beat or "",
                        scene=body.scene, label=label)
    return {"ok": True, "id": target}


@router.delete("/campaigns/{cid}/ledger/commitments/{mid}")
def delete_commitment(cid: str, mid: str, force: bool = False):
    _campaign_or_404(cid)
    with store.locks.campaign_lock(cid):
        record = store.commitments.get(cid, mid)
        if record is None:
            raise HTTPException(status_code=404, detail="commitment not found")
        _refuse_unsafe_delete(cid, f"commitment:{mid}", force)
        title = str(record.get("title") or mid)
        with store.undo.journalled(cid, {"w": "commitment", "id": mid},
                                   kind="commitment", ref={"kind": "commitment", "id": mid},
                                   field="commitment",
                                   label=_label(title, "commitment deleted")):
            store.commitments.restore(cid, mid, None)
        forget_or_partial(cid, f"commitment:{mid}", name=title, noun="commitment")
    return {"ok": True}


# ------------------------------------------------------------------- the facts


@router.post("/campaigns/{cid}/ledger/facts")
def post_fact(cid: str, body: FactRecord):
    """Record a standing fact by hand, optionally superseding one.

    Through `facts.record` rather than anything new: supersession retires the
    predecessor and files the replacement in a single write, and a hand-recorded
    fact deserves that atomicity as much as an extracted one.
    """
    _campaign_or_404(cid)
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="a fact needs text")
    scene = body.scene or ""
    supersedes = body.supersedes or ""
    with store.locks.campaign_lock(cid):
        # What the write is about to touch, read before it happens. Journalling
        # a create as "restore nothing" is only correct when a create is what
        # actually occurs, and `facts.record` has two other outcomes:
        #
        #   - It DEDUPES. Re-recording the same text for the same scene returns
        #     the id already holding it, so a create-shaped reversal would
        #     delete a fact that was there before this request.
        #   - It SUPERSEDES in the same write, retiring the predecessor and
        #     pointing the two at each other. A reversal that removes only the
        #     new fact leaves the old one retired by an id that no longer
        #     exists.
        existing = store.facts.find(store.facts.read(cid), scene, text)
        prior = store.facts.get(cid, supersedes) if supersedes else None
        fid = store.facts.record(cid, text, body.date or "", scene, supersedes=supersedes)
        rows = []
        if not existing:
            rows.append({
                "scene": "", "source": "manual", "kind": "fact",
                "ref": {"kind": "fact", "id": fid}, "field": "text",
                "label": _label(text, "fact"), "before": "", "after": text,
                "undo": {"target": {"w": "fact", "id": fid}, "restore": None,
                         "expect": store.facts.get(cid, fid)},
                "why": ""})
        # A second row for the predecessor, when one was actually retired. Two
        # rows rather than one composite because the journal reverses one entry
        # at a time and there is no composite shape -- so undoing a supersession
        # is two steps, newest first, each of which is a coherent state.
        after = store.facts.get(cid, supersedes) if supersedes else None
        if prior is not None and after is not None and prior != after:
            rows.append({
                "scene": "", "source": "manual", "kind": "fact",
                "ref": {"kind": "fact", "id": supersedes}, "field": "status",
                "label": _label(str(prior.get("text") or supersedes), "fact superseded"),
                "before": str(prior.get("status", "")), "after": str(after.get("status", "")),
                "undo": {"target": {"w": "fact", "id": supersedes}, "restore": prior,
                         "expect": after},
                "why": ""})
        if rows:
            try:
                store.journal.append(cid, rows)
            except Exception:   # the fact landed; only its history did not
                log.warning("could not journal a hand-recorded fact in %s", cid, exc_info=True)
    return {"id": fid}


@router.put("/campaigns/{cid}/ledger/facts/{fid}")
def put_fact(cid: str, fid: str, body: FactSave):
    """Correct a fact's wording, date or scene.

    The one place in the app that calls `facts.set_text`, and the reason that
    function exists: grimoire never edits a fact, the user may. The lifecycle
    fields are untouched here — retiring is its own route below, because it
    means something different.
    """
    _campaign_or_404(cid)
    with store.locks.campaign_lock(cid):
        if store.facts.get(cid, fid) is None:
            raise HTTPException(status_code=404, detail="fact not found")
        with store.undo.journalled(cid, {"w": "fact", "id": fid},
                                   kind="fact", ref={"kind": "fact", "id": fid},
                                   field="text",
                                   label=_label(body.text or fid, "fact")):
            try:
                store.facts.set_text(cid, fid, body.text, body.date, body.scene)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


@router.post("/campaigns/{cid}/ledger/facts/{fid}/retire")
def post_fact_retire(cid: str, fid: str, body: FactRetire):
    """Retire a fact that stopped being true, with nothing replacing it.

    Refused for a fact that is already retired, and for one recorded after the
    scene doing the retiring — `facts.retire`'s own invariants, reported as a
    409 rather than swallowed as a no-op, because a button that reports success
    and changes nothing is worse than one that says why.
    """
    _campaign_or_404(cid)
    with store.locks.campaign_lock(cid):
        record = store.facts.get(cid, fid)
        if record is None:
            raise HTTPException(status_code=404, detail="fact not found")
        with store.undo.journalled(cid, {"w": "fact", "id": fid},
                                   kind="fact", ref={"kind": "fact", "id": fid},
                                   field="status",
                                   label=_label(str(record.get("text") or fid), "fact retired")):
            if not store.facts.retire(cid, fid, body.scene or ""):
                raise HTTPException(
                    status_code=409,
                    detail="that fact is already retired, or was recorded after this scene")
    return {"ok": True}


@router.delete("/campaigns/{cid}/ledger/facts/{fid}")
def delete_fact(cid: str, fid: str):
    """Remove a fact outright — for one that should never have been recorded.
    Retiring says it was true once, which is the wrong thing to say about a
    sentence the extraction invented."""
    _campaign_or_404(cid)
    with store.locks.campaign_lock(cid):
        record = store.facts.get(cid, fid)
        if record is None:
            raise HTTPException(status_code=404, detail="fact not found")
        with store.undo.journalled(cid, {"w": "fact", "id": fid},
                                   kind="fact", ref={"kind": "fact", "id": fid},
                                   field="text",
                                   label=_label(str(record.get("text") or fid), "fact deleted")):
            store.facts.forget(cid, fid)
    return {"ok": True}


# ----------------------------------------------------------- the relationships


@router.put("/campaigns/{cid}/ledger/relationships")
def put_relationship(cid: str, body: RelationshipSave):
    """Set where two people stand, by hand.

    Feelings are directional and bonds are not, which is why `a`/`b` mean
    different things per shape and why the two go through different writers —
    the same split `absorb.apply` makes.
    """
    _campaign_or_404(cid)
    a, b = body.a.strip(), body.b.strip()
    if not a or not b:
        raise HTTPException(status_code=400, detail="a standing needs both people")
    with store.locks.campaign_lock(cid):
        if body.bond is None:
            # `set_feeling` writes the whole record, so every field the payload
            # left out has to come from the stored one -- otherwise moving the
            # note alone resets a 4/2/1 standing to zeroes. Read inside the
            # lock that covers the write, so nothing lands between the two.
            stored = store.relationships.get_feeling(cid, a, b) or {}

            def _meter(given, key):
                if given is not None:
                    return max(0, min(5, int(given)))
                held = stored.get(key)
                return held if isinstance(held, int) else 0

        if body.bond is not None:
            with store.undo.journalled(cid, {"w": "bond", "a": a, "b": b},
                                       kind="bond", ref={"kind": "bond", "id": f"{a}:{b}"},
                                       field="type", label=_label(f"{a} & {b}", "bond")):
                store.relationships.set_bond(cid, a, b, body.bond, body.scene or "")
        else:
            with store.undo.journalled(cid, {"w": "feeling", "from": a, "to": b},
                                       kind="relationship",
                                       ref={"kind": "relationship", "id": f"{a}:{b}"},
                                       field="feeling", label=_label(f"{a} → {b}", "feeling")):
                store.relationships.set_feeling(
                    cid, a, b, _meter(body.trust, "trust"),
                    _meter(body.affection, "affection"), _meter(body.tension, "tension"),
                    body.note if body.note is not None else str(stored.get("note") or ""))
    return {"ok": True}


@router.delete("/campaigns/{cid}/ledger/relationships")
def delete_relationship(cid: str, a: str, b: str, bond: bool = False):
    """Remove one standing. `bond=true` addresses the undirected record; without
    it this removes the feeling `a` holds toward `b` and leaves the other
    direction alone, because the two are separate readings."""
    _campaign_or_404(cid)
    with store.locks.campaign_lock(cid):
        target = {"w": "bond", "a": a, "b": b} if bond else {"w": "feeling", "from": a, "to": b}
        label = _label(f"{a} & {b}" if bond else f"{a} → {b}",
                       "bond deleted" if bond else "feeling deleted")
        with store.undo.journalled(cid, target,
                                   kind="bond" if bond else "relationship",
                                   ref={"kind": "bond" if bond else "relationship",
                                        "id": f"{a}:{b}"},
                                   field="type" if bond else "feeling", label=label):
            if bond:
                store.relationships.restore_bond(cid, a, b, None)
            else:
                store.relationships.restore_feeling(cid, a, b, None)
    return {"ok": True}


# --------------------------------------------------------------- the chronicle


@router.put("/campaigns/{cid}/ledger/chronicle/{sid}")
def put_chronicle_line(cid: str, sid: str, body: ChronicleLineSave):
    """Correct a scene's one-line recap, or its in-fiction date.

    The line only. Re-absorbing the scene is how the reading of a transcript
    changes, and it replaces the whole record — this is for the case where the
    reading was right and the sentence was not.
    """
    _campaign_or_404(cid)
    with store.locks.campaign_lock(cid):
        if store.chronicle.get_record(cid, sid) is None:
            raise HTTPException(status_code=404, detail="that scene has not been absorbed")
        with store.undo.journalled(cid, {"w": "chronicle", "id": sid},
                                   kind="chronicle", ref={"kind": "chronicle", "id": sid},
                                   field="one_line",
                                   label=_label(sid, "chronicle line")):
            store.chronicle.set_line(cid, sid, body.one_line, body.date)
    return {"ok": True}


# --------------------------------------------------------------- the knowledge
#
# Roadmap 11's overrides (`store/knowledge.py`): the user's word on who knows,
# suspects or never heard what happened in a scene, which beats every derived
# classification. So the routes hold to one rule above the others: a GRANT is
# only ever stored about text the user was shown. The server computes every
# fingerprint it stores, and a grant carries what the client displayed
# (`expect`) as a precondition -- a swipe or an edit landing between the read
# and the save is 409 `moved`, never a grant of text nobody saw. An `unaware`
# needs no such care: withholding more is the safe direction.


def _busy() -> HTTPException:
    return HTTPException(status_code=409, detail={
        "kind": "busy",
        "detail": "the campaign's scenes could not all be read just now; try again"})


def _malformed() -> HTTPException:
    return HTTPException(status_code=409, detail={
        "kind": "malformed",
        "detail": "knowledge.json cannot be read; repair it before editing statements"})


def _bad(kind: str, detail: str, status: int = 400) -> HTTPException:
    return HTTPException(status_code=status, detail={"kind": kind, "detail": detail})


def _knowledge_audience(cid: str, audience: str, shown: str | None = None) -> str:
    """The audience's current display name, or 400 for one that is not a
    character or group of this campaign. Read through the door the matcher
    reads (`knowledge_scene.audiences_for`), so the name stored is the name a
    grant is later checked against."""
    if audience == store.knowledge.EVERYONE:
        return ""
    if not store.knowledge.valid_audience(audience):
        raise _bad("bad_audience", "an audience is a character, a group, or everyone")
    name = _audience_now(store.knowledge_scene.audiences_for(cid), audience)
    if not name:
        raise _bad("bad_audience", "that character or group is not in this campaign")
    if shown is not None and shown != name:
        # The form named someone else: renamed, or replaced under the same id.
        raise _bad("moved", f"that audience is now called {name}; look again", 409)
    return name


def _audience_now(who, audience: str) -> str | None:
    if audience == store.knowledge.EVERYONE:
        return "Everyone"
    if audience.startswith("characters:"):
        return who.names.get(audience) or None
    return (who.groups.get(audience) or (None,))[0] or None


class _ScenePosts(NamedTuple):
    """One read of a scene: its raw messages, response variants, the
    prompt-view text (what an actor's prompt would carry, so what a grant is
    agreeing to), the digest a whole-scene statement is fingerprinted with,
    and its title and chronicle record."""
    messages: list[dict]
    variants: dict
    shown: list[dict]
    digest: str
    title: str
    record: dict


def _scene_posts(cid: str, sid: str, variants: dict | None = None) -> _ScenePosts:
    """Read a scene for a statement about it; every read failure is a 409 the
    reader can act on, never a 500. `variants` is the scene's slice of a
    ledger the caller already read (`responses.variants_by_scene`)."""
    try:
        scene = store.scenes.read_scene(cid, sid)
        token = scenes_identity.scene_identity(cid, sid)
    except store.scenes.SceneNotFound as exc:
        raise HTTPException(status_code=404, detail="scene not found") from exc
    except UnicodeDecodeError as exc:
        raise _bad("malformed", "the scene file cannot be read; repair it and try again",
                   409) from exc
    except (scenes_identity.UnreadableError, OSError) as exc:
        raise _busy() from exc
    messages = scene["messages"]
    if variants is None:
        try:
            variants = (store.responses.variants_by_response(cid, sid, token=token)
                        if token else {})
        except ValueError as exc:   # responses.json is not a response ledger
            raise _bad("malformed", "responses.json cannot be read; repair it and try again",
                       409) from exc
        except OSError as exc:
            raise _busy() from exc
    title, record = store.knowledge_scene.whole_scene(cid, sid, scene["meta"])
    return _ScenePosts(messages, variants, regex_view.view(messages, cid=cid, phase="prompt"),
                       store.knowledge_scene.scene_digest(messages, title, record),
                       title or sid, record if isinstance(record, dict) else {})


def _visible(read: _ScenePosts) -> list[tuple[int, dict, dict]]:
    """The posts a whole-scene grant would expose: every one in context."""
    return [(i, m, v) for i, (m, v) in enumerate(zip(read.messages, read.shown, strict=True))
            if not store.scenes.serialize.is_excluded(m)
            and not store.scenes.serialize.is_director_note(m)]


def _whole_shown(read: _ScenePosts) -> str:
    """The digest of everything a whole-scene grant exposes, as shown: the
    title, the chronicle summary and line, and every post in context in its
    prompt view. A whole-scene grant carries it back as `expect_shown`."""
    return store.knowledge.digest(json.dumps(
        [read.title, str(read.record.get("summary") or ""), str(read.record.get("one_line") or ""),
         [[str(m.get("speaker") or ""), str(v.get("content") or "")]
          for _i, m, v in _visible(read)]], ensure_ascii=False))


def _subject_form(posts: list[KnowledgePost] | None, whole_scene: bool,
                  grant: bool) -> list[KnowledgePost] | None:
    """The posts a request names, None for the whole scene, or a refusal.

    A post with no key cannot be named by itself: a grant about it is 400
    `unkeyed` (as a whole-scene grant it would also cover the summary, which
    the user did not judge), and an `unaware` is stored about the whole
    scene, which only withholds more (spec 11 §7.4).
    """
    if posts is not None and whole_scene:
        raise _bad("bad_subject", "name posts or the whole scene, not both")
    if posts is not None and any(not p.key for p in posts):
        if grant:
            raise _bad("unkeyed", "a post with no key can only be marked unaware; knowing "
                                  "it would have to grant the whole scene, summary included")
        return None
    if posts is None and not whole_scene:
        raise _bad("bad_subject", "name the posts, or say it is the whole scene")
    if posts is not None and not posts:
        raise _bad("bad_subject", "name at least one post, or the whole scene")
    if posts is not None and len({(p.key, p.part) for p in posts}) != len(posts):
        raise _bad("bad_subject", "name each post once")
    return posts


def _fingerprinted(read: _ScenePosts, posts: list[KnowledgePost], grant: bool,
                   expect: list[KnowledgeFingerprint] | None, gone_is: int) -> list[dict]:
    """Each named post's stored fingerprint. A grant must be about a visible
    post that still reads as the client was shown it -- its raw text and the
    prompt-view text the form displayed."""
    shown = {(e.key, e.part): (e.variant, e.digest, e.shown) for e in expect or []}
    out = []
    for p in posts:
        found = [(m, v) for m, v in zip(read.messages, read.shown, strict=True)
                 if store.knowledge.post_key(m) == p.key and store.knowledge.post_part(m) == p.part]
        if len(found) != 1:
            raise (_bad("gone", "that post is no longer in the scene", 409) if gone_is == 409
                   else _bad("unknown_post", "the scene holds no single post with that key"))
        message, view = found[0]
        fp = store.knowledge.fingerprint(message, read.variants)
        if grant and store.scenes.serialize.is_excluded(message):
            raise _bad("hidden", "a hidden post reaches no one, so it cannot be known")
        now = (fp["variant"], fp["digest"], store.knowledge.digest(str(view.get("content") or "")))
        if grant and shown.get((p.key, p.part)) != now:
            raise _bad("moved", "that post changed since it was shown; look again", 409)
        out.append(fp)
    return out


def _knowledge_subject(cid: str, sid: str, posts: list[KnowledgePost] | None,
                       whole_scene: bool, status: str,
                       expect: list[KnowledgeFingerprint] | None, expect_digest: str,
                       expect_shown: str = "", *, gone_is: int = 400) -> dict:
    """The subject a statement about `sid` stores, fingerprinted from the scene
    as it reads under the caller's hold. Everything is validated before the
    scene is given an identity, so a refused request writes nothing."""
    grant = status in store.knowledge.GRANTS
    named = _subject_form(posts, whole_scene, grant)
    read = _scene_posts(cid, sid)
    if named is None:
        if grant and (expect_digest != read.digest or expect_shown != _whole_shown(read)):
            raise _bad("moved", "the scene changed since it was shown; look again", 409)
        subject: dict = {"kind": "scene", "digest": read.digest}
    else:
        subject = {"kind": "posts",
                   "posts": _fingerprinted(read, named, grant, expect, gone_is)}
    try:
        subject["scene"] = scenes_identity.ensure_identity(cid, sid)
    except scenes_identity.UnreadableError as exc:
        raise _busy() from exc
    return subject


def _knowledge_write(cid: str, kid: str, entry: dict, write) -> None:
    who = entry.get("audience_name") or "Everyone"
    with store.undo.journalled(cid, {"w": "knowledge", "id": kid}, kind="knowledge",
                               ref={"kind": "knowledge", "id": kid}, field="status",
                               label=_label(who, "knowledge")):
        try:
            write()
        except store.knowledge.KnowledgeError as exc:
            raise _malformed() from exc


@router.get("/campaigns/{cid}/ledger/knowledge/scenes/{sid}")
def get_knowledge_scene(cid: str, sid: str):
    """What a statement about `sid` could name, as an actor's prompt would
    carry it: each keyed post with the fingerprint a grant sends back as
    `expect` (`shown` is the digest of the text listed here), every post a
    whole-scene grant would also expose -- keyless ones included -- with the
    title and chronicle summary and line, and the whole scene's `digest` and
    `shown`. Writes nothing."""
    _campaign_or_404(cid)
    read = _scene_posts(cid, sid)
    posts, keyless = [], []
    for i, (m, view) in enumerate(zip(read.messages, read.shown, strict=True)):
        if store.scenes.serialize.is_director_note(m):
            continue
        text, hidden = str(view.get("content") or ""), store.scenes.serialize.is_excluded(m)
        fp = store.knowledge.fingerprint(m, read.variants)
        row = {"index": i, "speaker": str(m.get("speaker") or ""), "text": text}
        if fp["key"]:
            posts.append({**fp, **row, "shown": store.knowledge.digest(text), "hidden": hidden})
        elif not hidden:
            keyless.append(row)
    return {"sid": sid, "digest": read.digest, "shown": _whole_shown(read),
            "title": read.title, "summary": str(read.record.get("summary") or ""),
            "one_line": str(read.record.get("one_line") or ""),
            "keyless": len(keyless), "keyless_posts": keyless, "posts": posts}


class _SceneView(NamedTuple):
    """One read of a scene for the list: its id, title, prompt-view messages,
    current fingerprints and what a whole-scene grant would expose."""
    sid: str
    title: str
    shown: list[dict]
    facts: store.knowledge.SceneFacts
    whole_shown: str


def _scene_view(cid: str, identity: str, ledger) -> _SceneView | str | None:
    """The scene a statement is about, None when it was deleted, or
    ``"unverifiable"`` when it could not be read just now (a synced folder
    mid-write, a damaged response ledger) -- which marks that one entry rather
    than failing the list. `ledger` is `responses.variants_by_scene`, read
    once per request, or an exception when that read failed."""
    try:
        sid = scenes_identity.find_by_identity(cid, identity)
    except scenes_identity.UnreadableError:
        return "unverifiable"
    if sid is None:
        return None
    if isinstance(ledger, Exception):
        return "unverifiable"
    try:
        read = _scene_posts(cid, sid, variants=ledger.get(identity, {}))
    except HTTPException:
        return "unverifiable"
    # The posts from `scene_facts`, the whole-scene digest from the same read
    # a subject is fingerprinted with (title and chronicle included).
    posts = store.knowledge_scene.scene_facts(read.messages, read.variants).posts
    return _SceneView(sid, read.title, read.shown,
                      store.knowledge.SceneFacts(posts=posts, digest=read.digest),
                      _whole_shown(read))


def _listed_posts(entry: dict, scene: _SceneView | None) -> list[dict]:
    """Each named post as the scene shows it now, with the fingerprint of that
    text -- what a re-stamp sends back as `expect`."""
    if entry["subject"]["kind"] != "posts":
        return []
    rows = []
    for p in entry["subject"]["posts"]:
        row = {"key": p["key"], "part": p["part"], "index": None,
               "speaker": "", "text": "", "missing": True}
        for i, m in enumerate(scene.shown if scene else ()):
            if (store.knowledge.post_key(m) == p["key"]
                    and store.knowledge.post_part(m) == p["part"]):
                variant, digest, _hidden = scene.facts.posts.get(  # type: ignore[union-attr]
                    (p["key"], p["part"]), ("", "", False))
                text = str(m.get("content") or "")
                row.update(index=i, speaker=str(m.get("speaker") or ""), text=text,
                           missing=False, variant=variant, digest=digest,
                           shown=store.knowledge.digest(text))
                break
        rows.append(row)
    return rows


def _stale_reason(entry: dict, held, posts: list[dict], now: str | None) -> str | None:
    """Why a statement no longer applies, or None. Structural loss applies to
    every statement; a fingerprint or a renamed audience only stops a grant,
    since an `unaware` never consults either."""
    if held == "unverifiable":
        # Not "gone": the scene could not be read just now, and the statement
        # may well be fine.
        return "unverifiable"
    if not isinstance(held, _SceneView):
        return "gone"
    # A grant needs every post it names; an `unaware` withholds whichever of
    # its posts remain, so it is gone only when none does.
    lost = (all(p["missing"] for p in posts) if entry["status"] == store.knowledge.UNAWARE
            else any(p["missing"] for p in posts))
    if posts and lost:
        return "gone"
    if entry["status"] not in store.knowledge.GRANTS:
        return None
    if entry["audience"] != store.knowledge.EVERYONE and now != entry["audience_name"]:
        return "audience"
    if store.knowledge.holds(entry, held.facts):
        return None
    hidden = entry["subject"]["kind"] == "posts" and any(
        (held.facts.posts.get((p["key"], p["part"])) or ("", "", False))[2]
        for p in entry["subject"]["posts"])
    return "hidden" if hidden else "changed"


@router.get("/campaigns/{cid}/ledger/knowledge")
def get_knowledge(cid: str):
    """Every statement, with what the ledger needs to show it: the scene it is
    about (null when deleted; `unverifiable` when the scan could not finish),
    the current text of each named post (prompt view), and why a grant no
    longer grants -- `stale` with `stale_reason` `changed`, `hidden`, `gone`
    (its scene or post was deleted), `unverifiable` (its scene could not be
    read just now) or `audience` (the audience was renamed or no longer
    resolves, §7.3/§7.5)."""
    _campaign_or_404(cid)
    entries, intact = store.knowledge.read_checked(cid)
    who = store.knowledge_scene.audiences_for(cid)
    try:   # one parse of the campaign-wide ledger for every scene below
        ledger: dict | Exception = store.responses.variants_by_scene(cid)
    except (ValueError, OSError) as exc:
        ledger = exc
    scenes: dict[str, _SceneView | str | None] = {}
    out = []
    for kid, entry in sorted(entries.items(), key=lambda kv: kv[1].get("created", ""),
                             reverse=True):
        identity = entry["subject"]["scene"]
        if identity not in scenes:
            scenes[identity] = _scene_view(cid, identity, ledger)
        held = scenes[identity]
        scene = held if isinstance(held, _SceneView) else None
        posts = _listed_posts(entry, scene)
        now = _audience_now(who, entry["audience"])
        reason = _stale_reason(entry, held, posts, now)
        out.append({
            "id": kid, **entry,
            "scene": ({"sid": scene.sid, "title": scene.title, "digest": scene.facts.digest,
                       "shown": scene.whole_shown} if scene else None),
            "unverifiable": held == "unverifiable",
            "posts": posts,
            "stale": reason is not None, "stale_reason": reason,
            "audience_now": None if entry["audience"] == store.knowledge.EVERYONE else now,
        })
    return {"entries": out, "intact": intact}


@router.post("/campaigns/{cid}/ledger/knowledge")
def post_knowledge(cid: str, body: KnowledgeSave):
    """Write a statement by hand, or accept one from a capture
    (`source: "accepted"`). Both are authoritative (spec 11 §7.1)."""
    _campaign_or_404(cid)
    if body.status not in store.knowledge.STATUSES:
        raise _bad("bad_status", "a status is knows, suspects or unaware")
    if body.source not in store.knowledge.SOURCES:
        raise _bad("bad_source", "a source is manual or accepted")
    with store.locks.campaign_lock(cid):
        try:
            # First: a ledger that cannot be written must refuse before the
            # subject below gives a legacy scene its identity.
            kid = store.knowledge.mint_id(cid)
        except store.knowledge.KnowledgeError as exc:
            raise _malformed() from exc
        name = _knowledge_audience(cid, body.audience, body.audience_name or None)
        subject = _knowledge_subject(cid, body.scene, body.posts, body.whole_scene, body.status,
                                     body.expect, body.expect_digest, body.expect_shown)
        entry = {"audience": body.audience, "audience_name": name, "status": body.status,
                 "subject": subject, "note": body.note, "source": body.source}
        _knowledge_write(cid, kid, entry, lambda: store.knowledge.add(cid, entry, kid=kid))
    return {"id": kid}


def _edited(cid: str, stored: dict, body: KnowledgeEdit) -> tuple[dict, bool]:
    """`stored` with the body's status, audience and note applied, and whether
    a new audience makes it a new statement for recency."""
    entry = dict(stored)
    if body.status is not None:
        if body.status not in store.knowledge.STATUSES:
            raise _bad("bad_status", "a status is knows, suspects or unaware")
        entry["status"] = body.status
    renew = False
    if body.audience is not None:
        entry["audience"] = body.audience
        entry["audience_name"] = _knowledge_audience(cid, body.audience, body.audience_name)
        renew = body.audience != stored["audience"]
    if body.note is not None:
        entry["note"] = body.note
    return entry, renew


def _restamped(cid: str, subject: dict, status: str, body: KnowledgeEdit) -> dict:
    """The stored subject's posts fingerprinted again, against the text the
    client was shown (`expect`): the ledger's "confirm against current text"."""
    try:
        sid = scenes_identity.find_by_identity(cid, subject["scene"])
    except scenes_identity.UnreadableError as exc:
        raise _busy() from exc
    if sid is None:
        raise _bad("gone", "the scene this is about was deleted", 409)
    posts = (None if subject["kind"] == "scene" else
             [KnowledgePost(key=p["key"], part=p["part"]) for p in subject["posts"]])
    return _knowledge_subject(cid, sid, posts, posts is None, status, body.expect,
                              body.expect_digest, body.expect_shown, gone_is=409)


@router.put("/campaigns/{cid}/ledger/knowledge/{kid}")
def put_knowledge(cid: str, kid: str, body: KnowledgeEdit):
    """Correct a statement. Only what the body names moves: the audience (its
    current name is recorded again), the status, the note, a new subject, or
    `restamp`. Without a new subject or a restamp the stored fingerprint is
    kept, so a note edit never confirms a stale grant -- and an `unaware`
    cannot become a grant that way at all, since a keyless part's `unaware`
    is stored about the whole scene and flipping it would be the whole-scene
    grant `unkeyed` refuses. A new audience or subject is a new statement for
    recency (§7.2), so it is dated now."""
    _campaign_or_404(cid)
    with store.locks.campaign_lock(cid):
        try:
            stored = store.knowledge.get_strict(cid, kid)
        except store.knowledge.KnowledgeError as exc:
            raise _malformed() from exc
        if stored is None:
            raise HTTPException(status_code=404, detail="statement not found")
        entry, renew = _edited(cid, stored, body)
        grant = entry["status"] in store.knowledge.GRANTS
        if body.scene is not None:
            entry["subject"] = _knowledge_subject(cid, body.scene, body.posts, body.whole_scene,
                                                  entry["status"], body.expect, body.expect_digest,
                                                  body.expect_shown)
            renew = True
        elif grant and stored["status"] == store.knowledge.UNAWARE:
            # Neither a plain edit nor a restamp chooses text: an `unaware`
            # stored whole-scene for a keyless part would come out as the
            # whole-scene grant `unkeyed` refuses.
            raise _bad("bad_subject", "name the posts again to turn this into knowing or "
                                      "suspecting it")
        elif body.restamp:
            entry["subject"] = _restamped(cid, stored["subject"], entry["status"], body)
        if entry == stored and not renew:
            return {"ok": True}   # nothing moved: no write, no journal row, no stamp
        _knowledge_write(cid, kid, entry,
                         lambda: store.knowledge.update(cid, kid, entry, renew=renew))
    return {"ok": True}


@router.delete("/campaigns/{cid}/ledger/knowledge/{kid}")
def delete_knowledge(cid: str, kid: str):
    _campaign_or_404(cid)
    with store.locks.campaign_lock(cid):
        try:
            stored = store.knowledge.get_strict(cid, kid)
        except store.knowledge.KnowledgeError as exc:
            raise _malformed() from exc
        if stored is None:
            raise HTTPException(status_code=404, detail="statement not found")
        _knowledge_write(cid, kid, stored, lambda: store.knowledge.remove(cid, kid))
    return {"ok": True}
