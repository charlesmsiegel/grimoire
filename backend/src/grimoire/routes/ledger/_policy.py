"""ledger: policy responsibilities."""

from __future__ import annotations

from fastapi import HTTPException

from ... import store
from ...store.continuity import doc as continuity_doc
from ...store.continuity import effective as continuity_effective
from ...store.continuity import review as continuity_review
from ._router import router as router


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
