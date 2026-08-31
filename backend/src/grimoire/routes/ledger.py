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

import logging

from fastapi import APIRouter, HTTPException

from .. import store
from .models import (
    ChronicleLineSave,
    CommitmentSave,
    FactRecord,
    FactRetire,
    FactSave,
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
def put_thread(cid: str, pid: str, body: ThreadSave):
    """Edit one thread: its title, its status, the scene it was last moved in,
    and optionally a beat to append.

    A beat is APPENDED rather than replaced, because that is what a beat is —
    `plot.set_movement` has always added one and there is no such thing as
    editing the list in place. Sending none leaves the beats alone, which is the
    ordinary case for closing a thread.
    """
    _campaign_or_404(cid)
    with store.locks.campaign_lock(cid):
        if store.plot.get(cid, pid) is None:
            raise HTTPException(status_code=404, detail="thread not found")
        title = body.title.strip()
        with store.undo.journalled(cid, {"w": "plot", "id": pid},
                                   kind="plot", ref={"kind": "plot", "id": pid},
                                   field="thread", label=_label(title or pid, "thread")):
            # `set_movement` reads a blank title or an unknown status as "keep
            # what is stored", which is the behaviour this route wants too: a
            # payload that only closes a thread must not blank its title.
            store.plot.set_movement(cid, pid, title, body.status or "",
                                    body.beat or "", body.scene if body.scene is not None
                                    else (store.plot.get(cid, pid) or {}).get("last_scene", ""))
    return {"ok": True}


@router.delete("/campaigns/{cid}/ledger/threads/{pid}")
def delete_thread(cid: str, pid: str):
    """Remove a thread outright — for one the extraction invented, which is a
    different act from closing it. A closed thread stays on the ledger saying it
    happened; that is the wrong thing to say about a thread that never did."""
    _campaign_or_404(cid)
    with store.locks.campaign_lock(cid):
        thread = store.plot.get(cid, pid)
        if thread is None:
            raise HTTPException(status_code=404, detail="thread not found")
        title = str(thread.get("title") or pid)
        with store.undo.journalled(cid, {"w": "plot", "id": pid},
                                   kind="plot", ref={"kind": "plot", "id": pid},
                                   field="thread", label=_label(title, "thread deleted")):
            store.plot.restore(cid, pid, None)
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
def put_commitment(cid: str, mid: str, body: CommitmentSave):
    """Edit one commitment. `due` is three-valued the whole way down, as
    `commitments.set_movement` documents: absent keeps the stored deadline, `""`
    clears it, text sets it."""
    _campaign_or_404(cid)
    with store.locks.campaign_lock(cid):
        record = store.commitments.get(cid, mid)
        if record is None:
            raise HTTPException(status_code=404, detail="commitment not found")
        title = body.title.strip()
        with store.undo.journalled(cid, {"w": "commitment", "id": mid},
                                   kind="commitment", ref={"kind": "commitment", "id": mid},
                                   field="commitment",
                                   label=_label(title or mid, "commitment")):
            store.commitments.set_movement(
                cid, mid, title, body.kind or "", body.status or "", body.due,
                body.beat or "",
                body.scene if body.scene is not None else record.get("last_scene", ""))
    return {"ok": True}


@router.delete("/campaigns/{cid}/ledger/commitments/{mid}")
def delete_commitment(cid: str, mid: str):
    _campaign_or_404(cid)
    with store.locks.campaign_lock(cid):
        record = store.commitments.get(cid, mid)
        if record is None:
            raise HTTPException(status_code=404, detail="commitment not found")
        title = str(record.get("title") or mid)
        with store.undo.journalled(cid, {"w": "commitment", "id": mid},
                                   kind="commitment", ref={"kind": "commitment", "id": mid},
                                   field="commitment",
                                   label=_label(title, "commitment deleted")):
            store.commitments.restore(cid, mid, None)
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
