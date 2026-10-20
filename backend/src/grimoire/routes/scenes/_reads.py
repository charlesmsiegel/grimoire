"""Scene reads project actors and rewrites over the raw transcript."""

from __future__ import annotations

from fastapi import HTTPException

from ... import store
from .. import character_turns
from ..common import _page_of, _page_window, _require_scene
from ._router import router as router


@router.get("/campaigns/{cid}/scenes")
def get_scenes(cid: str, limit: int | None = None, offset: int | None = None):
    """Every scene in the campaign, most-recently-updated first.

    With `limit`/`offset` (either, or both) the body is a slice of that same
    listing instead — `offset` skips that many from the front, `limit` caps what
    follows. Sending neither returns the whole listing, which is what every
    caller of this route reads today.

    Paging does not make the read cheaper: the sort is over every scene, so the
    listing is built in full and then sliced. What it bounds is the response —
    one row per scene, growing for as long as the campaign is played (#216).

    `offset` indexes a listing whose ORDER MOVES WITH PLAY: a turn rewrites its
    scene's `updated`, which sends that scene to the front. So a reader walking
    a live campaign page by page can see one scene twice and miss another, the
    way any offset pager over a re-sorting list can. That is a real limit of the
    parameter, not of this implementation — a client that cannot tolerate it
    wants an anchor that does not drift, the way `GET .../scenes/{sid}` takes a
    message INDEX (`before`) rather than an offset. #216 chose `offset` here
    knowingly; adding a cursor is a different feature and not what it asked for.
    """
    limit, offset = _page_window(limit, offset)
    try:
        return _page_of(store.scenes.list_scenes(cid), limit, offset)
    except store.campaigns.CampaignNotFound as exc:
        raise HTTPException(status_code=404, detail="campaign not found") from exc


@router.get("/campaigns/{cid}/scenes/{sid}")
def get_scene(cid: str, sid: str, limit: int | None = None, before: int | None = None):
    """The scene, whole by default. With `limit` the body is instead the last
    `limit` messages ending before index `before` (default: the tail), plus
    `offset`/`total`/`has_older` so the reader can page backwards — see
    `scenes.read_scene_window`. Omitting `limit` keeps the unwindowed shape
    every other caller of this route already reads.
    """
    if limit is not None and limit < 1:
        raise HTTPException(status_code=400, detail="limit must be at least 1")
    if before is not None and before < 0:
        raise HTTPException(status_code=400, detail="before must not be negative")
    try:
        if character_turns.enabled():
            # Lock-free unless a post still needs its response assigned: the
            # full `migrate` parses the campaign's whole ledger and waits out a
            # turn's lock, on every open -- see `migrate_if_needed`.
            scene = store.responses.migrate_if_needed(cid, sid)
            if limit is None:
                return _with_actor_refs(cid, sid, scene)
        if limit is None:
            return _with_actor_refs(cid, sid, store.scenes.read_scene(cid, sid))
        return _with_actor_refs(cid, sid, store.scenes.read_scene_window(cid, sid, limit, before))
    except (store.scenes.SceneNotFound, store.campaigns.CampaignNotFound) as exc:
        # a scene path is built from campaign_root, so an unusable campaign id
        # surfaces here as CampaignNotFound -- still a 404, not a 500
        raise HTTPException(status_code=404, detail="scene not found") from exc


def _with_actor_refs(cid: str, sid: str, scene: dict) -> dict:
    """The scene as the reader gets it: each message tagged with its actor, the
    ones a display rule changed carrying `shown` beside the raw `content`, and
    the ones a stored rewrite changed flagged `rewritten`, with the key of the
    record that says so (`rewrite_key`) -- the inspector reads that record, and
    which of a message's keys holds it is this module's to work out
    (`_record_for`). A windowed read says where it sits (`offset`, `total`) so
    depth counts over the whole scene."""
    refs = store.responses.actor_refs(cid, sid)
    scene["messages"] = store.regex.view.annotate_shown(
        scene["messages"], cid=cid, offset=scene.get("offset", 0), total=scene.get("total"))
    rewritten = store.regex.rewrites.read_all(cid, sid)
    active = _active_variants(cid, sid, rewritten)
    firsts = _first_parts(scene["messages"]) if rewritten else {}
    for message in scene["messages"]:
        ref = refs.get(message.get("response_id", ""))
        if ref:
            message["actor_ref"] = ref
        found = (_record_for(message, rewritten, active,
                             firsts.get(message.get("response_id") or ""))
                 if rewritten else None)
        if found is not None:
            message["rewritten"] = True
            message["rewrite_key"] = found[0]
    # Which absorbed sibling makes this branch read-only, resolved here so the
    # play view's banner can name and link it. Only when set.
    closed = store.scenes.read.closed_by(cid, sid)
    if closed:
        scene["meta"]["closed_by"] = closed
    return scene


def _first_parts(messages: list[dict]) -> dict[str, dict]:
    """Each response's first part among `messages`, by `response_id` -- what
    `_record_for` hands a later part so the first one wins a legacy record
    both of them match. A window may not hold it, and then nothing settles the
    tie there; the edit routes read the whole transcript (`_first_part_of`)."""
    out: dict[str, dict] = {}
    for m in messages:
        rid = m.get("response_id")
        if rid and not m.get("response_part") and rid not in out:
            out[rid] = m
    return out


def _first_part_of(cid: str, sid: str, target: dict) -> dict | None:
    """The first part of `target`'s response when `target` is a later part --
    read only then, since only then is there a tie to settle."""
    if not (target.get("response_id") and target.get("response_part")):
        return None
    return _first_parts(store.scenes.read_scene(cid, sid)["messages"]).get(
        target["response_id"])


def _record_for(message: dict, records: dict[str, dict],
                active: dict[str, str | None],
                first: dict | None = None) -> tuple[str, dict] | None:
    """The store phase's record that still describes this message, and the key
    it is under, or None. Every key the message may be recorded under is asked
    (`rewrites.candidates`): its part's, the bare `response_id` -- where a later
    part's record sat before parts had keys of their own -- and the `post_id` a
    legacy reply was recorded under before the response migration gave it a
    `response_id` too. A key can hold a record that is not this message's (the
    first part's, a variant since swiped away), so the first that describes it
    (`_describes`) wins. `active` is `_active_variants`.

    The bare `response_id` is the first part's key, so a later part takes a
    record there only when it is one from before records named their part
    (`rewrites.claims`) and `first` -- the first part of the same response,
    when the caller has it -- does not match it as well: two parts can say
    the same thing, and the key's owner keeps the record then."""
    for key in store.regex.rewrites.candidates(message):
        rec = records.get(key)
        if (rec is None or not store.regex.rewrites.claims(message, key, rec)
                or not _describes(message, rec, active)):
            continue
        if (first is not None and first is not message
                and key == message.get("response_id") and message.get("response_part")
                and _describes(first, rec, active)):
            continue
        return key, rec
    return None


def _active_variants(cid: str, sid: str, records: dict[str, dict]) -> dict[str, str | None]:
    """Each response's active variant, for `_describes` -- read only when some
    record names a variant, so a scene without one reads no ledger."""
    if not any(rec.get("variant") for rec in records.values()):
        return {}
    return {rid: vid for rid, (vid, _) in store.responses.variants_by_response(cid, sid).items()}


def _describes(message: dict, rec: dict, active: dict[str, str | None]) -> bool:
    """Whether `rec` still describes this message. A record is kept per key,
    and a reroll or a swipe puts other text under the same `response_id` -- so
    the flag, and the Restore it offers, holds only while the message says what
    the rewrite stored, and, for a record that names its variant, while that
    variant is the one shown (two variants can store the same text). Restoring
    a record the text has moved past would overwrite the newer text with an
    original that was never its own."""
    if rec.get("variant") and active.get(message.get("response_id") or "") != rec["variant"]:
        return False
    stored = rec.get("stored")
    content = message.get("content")
    return (isinstance(stored, str) and isinstance(content, str)
            and stored.strip() == content.strip())


@router.get("/campaigns/{cid}/scenes/{sid}/rewrites")
def get_scene_rewrites(cid: str, sid: str):
    """What the store phase rewrote in this scene, `{key: {original, rules,
    at}}` by `rewrites.key_for` -- the originals Restore writes back. A scene
    read names the key each rewritten message's record is under
    (`rewrite_key`)."""
    _require_scene(cid, sid)
    return {key: {"original": rec["original"], "rules": rec.get("rules", []),
                  "at": rec.get("at", "")}
            for key, rec in store.regex.rewrites.read_all(cid, sid).items()}
