"""Transcript edits keep response-part rewrite ownership and tracker invalidation beside the mutation."""

from __future__ import annotations

import contextlib
import logging

from fastapi import HTTPException, Request

from ... import store
from .. import runs, streaming
from .. import tracker as tracker_routes
from ..common import _require_scene
from ..models import EditMessage, ExcludeMessage
from . import _reads, _review_snapshot
from ._router import router as router

log = logging.getLogger('grimoire.routes.scenes')


@router.put("/campaigns/{cid}/scenes/{sid}/messages/{index}")
def put_scene_message(cid: str, sid: str, index: int, body: EditMessage,
                      request: Request):
    _require_scene(cid, sid)
    # Macros resolved once at persist time (#137), same as a fresh send. Not for
    # a restore: the original it writes back went through them when it landed.
    content = body.content if body.restore else store.context.expand_macros(
        body.content, store.context.scene_substitutions(cid, sid), cid, sid)
    try:
        # Same pairing as `_persist_reply`: every write to the transcript is
        # followed by reconciling the set, under one lock. An edit of the live
        # reply parks the pre-edit text as a variant — but that variant exists
        # only in the transcript until this runs, so a *second* edit would
        # overwrite the sole copy of the first and drop it from the set.
        #
        # The scene-free check is INSIDE that hold rather than ahead of it, and
        # review caught that it was not: checking and then locking leaves room
        # for a send to reserve and detach in between, so the edit lands under a
        # turn that started after the check said there was none.
        with runs.scene_held_open(request.app, cid, sid):
            messages = store.scenes.read_scene(cid,sid)["messages"]
            if 0 <= index < len(messages) and messages[index].get("response_id"):
                try:
                    store.responses.editable(cid,sid,messages[index]["response_id"])
                except store.responses.ResponseConflict as exc:
                    raise HTTPException(409,detail={"kind":exc.kind,"detail":exc.detail}) from exc
            target = messages[index] if 0 <= index < len(messages) else {}
            key, original, content, fired = _edit_store_phase(
                cid, sid, target, content, restore=body.restore)
            store.scenes.edit_message(cid, sid, index, content)
            # The edit is on disk; the sidecar is not a reason to fail it.
            with contextlib.suppress(OSError):
                store.alternates.reconcile(cid, sid)
            if key:
                _settle_rewrite(cid, sid, target, key, original, fired, content)
            # The post's tracker record describes text that is no longer there,
            # and every later record was built on it. Flagged, not re-run (the
            # spec's "nothing re-runs automatically"), and in this hold so no
            # reader sees the new text beside unflagged records. Fail-soft.
            tracker_routes.after_text_edit(cid, sid, index)
    except IndexError as err:
        raise HTTPException(status_code=400, detail="message index out of range") from err
    except store.scenes.RollMessageImmutable as err:
        raise HTTPException(status_code=400, detail="a dice roll's transcript line can't be edited") from err
    return {"ok": True}


def _edit_store_phase(cid: str, sid: str, target: dict, content: str, *,
                      restore: bool) -> tuple[str, str, str, list[str]]:
    """What an edit of `target` stores: `(key, original, content, fired)`,
    where `key` is the message's record key (`rewrites.key_for`, "" for none)
    and `content` the text to write. Shared by the plain edit and the retcon,
    which are the same write with different consequences, and called inside
    the hold that writes it; `_settle_rewrite` follows the write when `key`.

    The store phase runs on what the player typed, with the edited message's
    role and the connection that produced it -- but only where a record can be
    kept. A message with neither id (one written before ids existed) has
    nothing to key one by, and rewriting it would change text that Restore
    could never give back; a synthetic line has no role. A restore runs no
    phase at all: it would rewrite the original straight back, and it is
    refused unless it undoes exactly the rewrite on record
    (`_require_restorable`)."""
    key = store.regex.rewrites.key_for(target)
    if restore:
        _require_restorable(cid, sid, target, content)
    role = store.regex.apply.role_of(target)
    fired: list[str] = []
    stored = content
    if key and role and not restore:
        stored, fired = store.regex.view.store_phase(
            content, cid=cid, role=role, connection=store.regex.view.connection_of(target))
    return key, content, stored, fired


def _require_restorable(cid: str, sid: str, target: dict, content: str) -> None:
    """Refuse a restore that does not undo exactly the rewrite on record.

    A restore writes raw text past the macros and the store phase, so it is
    allowed only as the inverse of a recorded rewrite: the message at `index`
    has a record that still describes it (`_record_for` -- not a reroll or swipe
    that moved past it, nor a different message a cut in another tab
    renumbered into this slot), and `content` is that record's original.
    Anything else is a stale view of the scene, answered 409 for the client to
    re-read rather than written over whatever is there now."""
    records = store.regex.rewrites.read_all(cid, sid)
    found = _reads._record_for(target, records, _reads._active_variants(cid, sid, records),
                        _reads._first_part_of(cid, sid, target))
    if found is None or content != found[1].get("original"):
        raise HTTPException(409, detail={
            "kind": "rewrite_stale",
            "detail": "This message no longer matches its recorded rewrite; reload the scene."})


def _settle_rewrite(cid: str, sid: str, target: dict, key: str, original: str,
                    fired: list[str], content: str) -> None:
    """Bring the message's rewrite record in line with an edit that wrote
    `content` over `target` (the message as it was before the edit). `key` is
    the message's own key (`rewrites.key_for`).

    Only the latest rewrite is kept: one that fired replaces the record, and an
    edit that replaces the text the record stored -- a restore included --
    leaves the player's own text, with nothing to restore. Any other edit leaves
    the record alone: saving the rewritten text unchanged (an idempotent rule
    has nothing to fire on), or editing another part of the same response,
    which has a key of its own. Forgetting there would delete the only copy of
    an original. "The same" is compared stripped, as `_describes` compares, so
    the two agree on it.

    Every record that is this message's (`_own_record_keys`) is settled, not
    only `key`'s. A rewrite that fired supersedes the others, and an edit that
    clears the rewrite retires them all."""
    records = store.regex.rewrites.read_all(cid, sid)
    found = _reads._record_for(target, records, _reads._active_variants(cid, sid, records),
                        _reads._first_part_of(cid, sid, target))
    rid = target.get("response_id") or ""
    own = _own_record_keys(target, records, key, found)
    if fired:
        variant = streaming._active_variant(cid, sid, rid) if rid else ""
        streaming._record_rewrite(cid, sid, key, original, fired, content, variant)
        for other in own:
            if other != key:
                _forget_rewrite(cid, sid, other)
        return
    if found is not None and content.strip() != str(found[1]["stored"]).strip():
        for k in own:
            _forget_rewrite(cid, sid, k)


def _own_record_keys(target: dict, records: dict[str, dict], key: str,
                     found: tuple[str, dict] | None) -> list[str]:
    """The stored-rewrite records that are `target`'s, by key. A migrated
    legacy reply is recorded under its `post_id`, and a later part's record
    from before parts had keys sits under the bare `response_id`
    (`_record_for`, whose answer is `found`). Its part key (`key`) and its
    `post_id` are the message's alone; the bare `response_id` is too for the
    first part, and for a later part only when its record describes this
    message -- otherwise it is the first part's."""
    rid = target.get("response_id") or ""
    return [k for k in store.regex.rewrites.candidates(target)
            if k in records and (k != rid or k == key or (found and found[0] == k))]


def _forget_rewrite(cid: str, sid: str, key: str) -> None:
    """Drop a message's stored-rewrite record, fail-soft: the edit is on disk,
    and a record left behind no longer matches its text, so nothing flags it."""
    try:
        store.regex.rewrites.forget(cid, sid, key)
    except OSError:
        log.warning("could not forget the stored rewrite of %s in %s/%s",
                    key, cid, sid, exc_info=True)


@router.put("/campaigns/{cid}/scenes/{sid}/messages/{index}/excluded")
def put_scene_message_excluded(cid: str, sid: str, index: int, body: ExcludeMessage,
                               request: Request):
    """Hide a post from context, or return it.

    It stays in the transcript, the play view and every export; it reaches no
    prompt. Refused on an absorbed scene (no future prompt to protect, and what
    absorb took from it is corrected by retcon or re-absorb) and while a round
    is open (the toggle moves `transcript_hash`, stranding that round's Retry or
    roll resolution). Every check sits inside the same hold as the write, for
    the reason `put_scene_message` gives.
    """
    _require_scene(cid, sid)
    try:
        with runs.scene_held_open(request.app, cid, sid):
            if _review_snapshot._already_absorbed(store.scenes.read_scene(cid, sid)):
                raise HTTPException(409, detail={
                    "kind": "scene_absorbed",
                    "detail": "this scene has been absorbed; its posts can no longer be hidden"})
            if store.responses.unfinished(cid, sid) is not None:
                raise HTTPException(409, detail={
                    "kind": "round_open",
                    "detail": "finish or discard the open round before hiding a post"})
            if store.scenes.set_excluded(cid, sid, index, body.excluded):
                # What later tracker records were built on has changed, as after
                # an edit: flagged, not re-run, and in this hold. Fail-soft.
                tracker_routes.after_text_edit(cid, sid, index)
    except IndexError as exc:
        raise HTTPException(status_code=400, detail="message index out of range") from exc
    except store.scenes.NotExcludable as exc:
        raise HTTPException(status_code=400, detail={
            "kind": "not_excludable",
            "detail": "a roll, scene-transition or director-note line can't be hidden from context"}) from exc
    except (store.scenes.SceneNotFound, store.campaigns.CampaignNotFound) as exc:
        raise HTTPException(status_code=404, detail="scene not found") from exc
    return {"ok": True}


@router.delete("/campaigns/{cid}/scenes/{sid}/messages/{index}")
def delete_scene_messages_from(cid: str, sid: str, index: int, request: Request):
    """Delete this post and everything after it, undoing what the scene wrote (#75).

    The reply is a report of what the cascade actually did, not an `{"ok": true}`:
    a cascade delete reverses records the player cannot see from the transcript,
    and one whose compare-and-swap was refused is exactly what they need told.
    `store.cascade` carries the reasoning for every step and for each store it
    leaves alone.

    A cut that would remove nothing (`index` past the last post, or negative) is a
    400 rather than a silent success — the client addresses a post it is looking
    at, so an out-of-range index means the two disagree about the transcript.

    `SceneNotFound` is caught even though `_require_scene` has just run: that
    check is outside the campaign lock the cascade takes, so a delete of this
    scene landing in between would otherwise surface as a 500 for a scene that is
    simply not there — the same 404 the guard above exists to give.

    **The index is trusted, and that boundary is deliberate.** A caller addresses
    a post by position, so a transcript that moved between the client rendering
    it and this request arriving would cut from somewhere else — and here that
    means taking everything after it, not overwriting one post. Requiring the
    caller to also send what it believes is AT that index (the shape
    `scenes.remove_trailing_user_post` uses, and for a less destructive removal)
    was considered and not done: the client hides these controls entirely while a
    turn is in flight, `rolling` latches them across every other transcript
    write, and the remaining writer is another device on a store shared through a
    synced folder — which `store/locks.py` already documents as outside what any
    lock here can promise. An optional check would be no guarantee at all, and a
    required one would break a plain API caller for a race this route cannot be
    the place to close. Editing a post (`PUT`) accepts exactly the same exposure.
    """
    _require_scene(cid, sid)
    try:
        # Held across the check and the cascade, not merely ahead of it: the
        # campaign lock is reentrant, so the cascade's own acquisitions are
        # free, and a send cannot reserve a turn in the gap.
        with runs.scene_held_open(request.app, cid, sid):
            report = store.cascade.delete_from(cid, sid, index)
            # The cut posts' tracker records describe posts that no longer
            # exist. Nothing is left after them to flag: a cut takes the tail.
            tracker_routes.after_cut(cid, sid)
            streaming._prune_rewrites(cid, sid)
            return report
    except IndexError as exc:
        raise HTTPException(status_code=400, detail="message index out of range") from exc
    except (store.SceneNotFound, store.CampaignNotFound) as exc:
        raise HTTPException(status_code=404, detail="scene not found") from exc


@router.post("/campaigns/{cid}/scenes/{sid}/messages/{index}/retcon")
def post_scene_retcon(cid: str, sid: str, index: int, body: EditMessage,
                      request: Request):
    """Rewrite a past post and let the scene be extracted again (#78).

    The difference from `PUT .../messages/{index}` is everything that happens
    after the text lands: a retcon un-does what this scene's absorb wrote and
    clears `done`, because an edited post makes the recorded summary a
    description of a transcript that is no longer there. The plain PUT stays
    what it is — an in-place text fix, which must not silently un-absorb a
    finished scene — so the caller says which of the two it means.

    The reply is a report, like the cascade delete's and for the same reason:
    the reversal touches records the player cannot see from the transcript, and
    a compare-and-swap it had to refuse is exactly what they need told. `later`
    names the scenes played after this one — the ones a re-extraction can
    contradict, and the reason to re-absorb this scene and read the badges.
    """
    _require_scene(cid, sid)
    # Macros resolved once at persist time, the same as a fresh send and the
    # same as the plain edit (#137): a retconned `{{roll:1d20}}` must not
    # re-roll on every later context build. Not for a restore, as there.
    content = body.content if body.restore else store.context.expand_macros(
        body.content, store.context.scene_substitutions(cid, sid), cid, sid)
    try:
        # One hold over check and rewrite, as for the cascade delete above.
        with runs.scene_held_open(request.app, cid, sid):
            # The stored-rewrite handling the plain edit gets, in this same
            # hold: the store phase on the new text, then the post's record
            # settled -- replaced when a rule fired, retired when the retcon
            # overwrote the text it stored. Without it a retcon over a
            # rewritten post left an original nothing could restore.
            messages = store.scenes.read_scene(cid, sid)["messages"]
            target = messages[index] if 0 <= index < len(messages) else {}
            key, original, content, fired = _edit_store_phase(
                cid, sid, target, content, restore=body.restore)
            report = store.retcon.retcon(cid, sid, index, content)
            if key:
                _settle_rewrite(cid, sid, target, key, original, fired, content)
            # A retcon is a text edit for the tracker: the rewritten post's
            # record is stale and every later one was built on it.
            tracker_routes.after_text_edit(cid, sid, index)
            return report
    except IndexError as exc:
        raise HTTPException(status_code=400, detail="message index out of range") from exc
    except store.scenes.RollMessageImmutable as exc:
        raise HTTPException(status_code=400,
                            detail="a dice roll's transcript line can't be edited") from exc
    except (store.SceneNotFound, store.CampaignNotFound) as exc:
        raise HTTPException(status_code=404, detail="scene not found") from exc
