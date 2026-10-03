"""The scene state tracker's HTTP surface (store/tracker/), and its update run.

The on/off setting and the field-definition layers, and the run that keeps a
snapshot per post: `schedule` is called after every post lands (a player's, a
character's, an opener) and starts a `background` run that asks the model what
changed and stores the merged result under that post's key. The scene's
records are read, hand-edited and re-run through the routes at the bottom, and
the `after_*` hooks are how the transcript routes that edit, cut or swipe keep
those records consistent with what the transcript now says.

The callers are `routes/scenes.py`, `routes/character_turns.py` and
`routes/greetings.py`; this module imports none of them, which is what keeps
the route graph acyclic."""

from __future__ import annotations

import asyncio
import logging
import threading

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from .. import store
from ..llm import LLMClient, effective_model
from ..llm_errors import LLMError
from . import runs
from .common import (
    _dump,
    _llm_http_error,
    _require_connection,
    _require_scene,
    get_llm,
    run_error,
)
from .models import CampaignTracker, TrackerEdit, TrackerLayer

router = APIRouter()

log = logging.getLogger(__name__)


def _setting_body(cid: str) -> dict:
    try:
        return {"setting": store.tracker.settings.campaign_setting(cid),
                "enabled": store.tracker.settings.enabled(cid)}
    except store.campaigns.CampaignNotFound as exc:
        raise HTTPException(status_code=404, detail="campaign not found") from exc


@router.get("/campaigns/{cid}/tracker")
def get_campaign_tracker(cid: str):
    return _setting_body(cid)


@router.put("/campaigns/{cid}/tracker")
def put_campaign_tracker(cid: str, body: CampaignTracker):
    # The campaign first: a 400 about a value is an answer about a campaign, and
    # giving it for one that does not exist tells the caller the wrong thing.
    _setting_body(cid)
    try:
        store.campaigns.set_campaign_tracker(cid, body.setting)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except store.campaigns.CampaignNotFound as exc:
        # Deleted between the check and the write, in another tab.
        raise HTTPException(status_code=404, detail="campaign not found") from exc
    return _setting_body(cid)


# --- field definition layers -------------------------------------------------
#
# Each layer's GET answers `inherited` (what the layer sits on) next to its own
# `layer`, so an editor can show what a change would be changing without a
# second request, and `effective` (the two combined).

def _layer_body(inherited: list[dict], layer: dict) -> dict:
    fields = store.tracker.fields
    return {"layer": layer, "effective": fields.apply_layer(inherited, layer),
            "inherited": inherited}


def _bad_layer(exc: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


def _world_or_404(wid: str) -> None:
    if not store.worlds.world_exists(wid):
        raise HTTPException(status_code=404, detail="world not found")


def _inherited_for_world() -> list[dict]:
    return [dict(f) for f in store.tracker.fields.DEFAULT_FIELDS]


def _campaign_world(cid: str) -> str:
    try:
        return store.campaigns.read_campaign(cid)["meta"].get("world") or ""
    except store.campaigns.CampaignNotFound as exc:
        raise HTTPException(status_code=404, detail="campaign not found") from exc


@router.get("/worlds/{wid}/tracker-fields")
def get_world_tracker_fields(wid: str):
    _world_or_404(wid)
    return _layer_body(_inherited_for_world(), store.tracker.fields.world_layer(wid))


@router.put("/worlds/{wid}/tracker-fields")
def put_world_tracker_fields(wid: str, body: TrackerLayer):
    _world_or_404(wid)
    try:
        store.tracker.fields.write_world_layer(wid, _dump(body))
    except store.tracker.fields.FieldLayerError as exc:
        raise _bad_layer(exc) from exc
    except store.worlds.WorldNotFound as exc:
        raise HTTPException(status_code=404, detail="world not found") from exc
    return get_world_tracker_fields(wid)


@router.get("/campaigns/{cid}/tracker-fields")
def get_campaign_tracker_fields(cid: str):
    wid = _campaign_world(cid)
    return _layer_body(store.tracker.fields.world_fields(wid),
                       store.tracker.fields.campaign_layer(cid))


@router.put("/campaigns/{cid}/tracker-fields")
def put_campaign_tracker_fields(cid: str, body: TrackerLayer):
    _campaign_world(cid)
    try:
        store.tracker.fields.write_campaign_layer(cid, _dump(body))
    except store.tracker.fields.FieldLayerError as exc:
        raise _bad_layer(exc) from exc
    except store.campaigns.CampaignNotFound as exc:
        raise HTTPException(status_code=404, detail="campaign not found") from exc
    return get_campaign_tracker_fields(cid)


@router.get("/campaigns/{cid}/scenes/{sid}/tracker-fields")
def get_scene_tracker_fields(cid: str, sid: str):
    _require_scene(cid, sid)
    return _layer_body(store.tracker.fields.campaign_fields(cid),
                       store.tracker.fields.scene_layer(cid, sid))


@router.put("/campaigns/{cid}/scenes/{sid}/tracker-fields")
def put_scene_tracker_fields(cid: str, sid: str, body: TrackerLayer):
    _require_scene(cid, sid)
    try:
        store.tracker.fields.write_scene_layer(cid, sid, _dump(body))
    except store.tracker.fields.FieldLayerError as exc:
        raise _bad_layer(exc) from exc
    except (store.scenes.SceneNotFound, store.campaigns.CampaignNotFound) as exc:
        # Deleted between the check and the write, in another tab.
        raise HTTPException(status_code=404, detail="scene not found") from exc
    return get_scene_tracker_fields(cid, sid)


# --- the update run ----------------------------------------------------------
#
# One `background` run per tracked post (`runs.reserve_background`): no
# exclusion key, so an update can neither refuse a turn with `run_in_flight`
# nor hold the scene against an edit, a cut or a rename. Play never waits on
# it, and nothing it does can fail the turn that scheduled it -- every entry
# point below swallows its own failures, because by the time any of them is
# called the post is already on disk and the player is owed it.
#
# Everything here is keyed on the scene's IDENTITY, not its `sid`. A run
# outlives its request, and a `sid` moves on rename: the `sid` a run was handed
# is only a hint, re-resolved (`_current_sid`) every time the run touches the
# store.

def mark(cid: str, sid: str, key: str) -> str | None:
    """The first half of scheduling: mark `key` `pending`, and return it -- or
    `None` when the tracker is off or the mark could not be written.

    **Meant to be called inside the campaign-lock hold that wrote the post**
    (the append, `_save`, `_pause`, `_accept_reroll`). There the acquisition
    below is reentrant and costs nothing; anywhere on a turn's path OUTSIDE
    such a hold it would be a fresh wait of up to `LOCK_TIMEOUT` in front of
    the player's terminal frames, which is exactly the cost `reserve_background`
    was built not to charge. `pending` before the run exists is also
    `records.save`'s caller contract (a crash between a snapshot and its index
    entry leaves `pending`, never an `ok` about a file that did not land).

    Never raises: the post is written, and tracking it is extra."""
    try:
        if not store.tracker.settings.enabled(cid):
            return None
        ident = store.scenes.ensure_identity(cid, sid)
        store.tracker.records.mark_pending(cid, ident, key)
    except Exception as exc:  # noqa: BLE001 -- see the docstring
        log.warning("tracker: could not mark %s pending in %s/%s -- %s", key, cid, sid, exc)
        return None
    return key


def mark_response(cid: str, sid: str, rid: str) -> str | None:
    """`mark` for response `rid`'s ACTIVE variant -- the one the transcript now
    shows. `None` when it has none yet. Same calling rule as `mark`."""
    try:
        if not store.tracker.settings.enabled(cid):
            return None
        active, _ = store.responses.variants_by_response(cid, sid).get(rid, (None, []))
    except Exception as exc:  # noqa: BLE001 -- see `mark`
        log.warning("tracker: could not resolve response %s in %s/%s -- %s", rid, cid, sid, exc)
        return None
    return mark(cid, sid, store.tracker.paths.response_key(rid, active)) if active else None


def start(app, cid: str, sid: str, key: str, client: LLMClient,
          identity: str | None = None, *, flag_later: bool = False) -> None:
    """The second half: start the run for a key `mark` returned. Never raises.

    Takes no campaign lock on its success path (`reserve_background` reads the
    identity rather than ensuring it), so a turn calls it AFTER its terminal
    frames without anything left to wait on. Only a failure takes the lock, to
    mark the record `failed`: once `mark` has said `pending`, every way this
    can go wrong must say so rather than leave the record waiting on a run
    that does not exist.

    **Must be called from a worker thread, never the event loop:** reserving a
    run builds its handshake events through the lifespan's `BlockingPortal`,
    which raises from the loop thread.

    `identity` is the scene the key was marked on, when the caller knows it (a
    turn does: its fenced identity). A reservation made against a different
    scene -- the `sid` deleted and reissued in between -- is released, and the
    record is failed where it was marked rather than looked for in a stranger.

    `flag_later` is a Retry's: the record had no usable result, so every later
    record was built without this post's contribution and is stale once it
    lands (`_commit`)."""
    try:
        run = runs.reserve_background(app, cid, sid, "tracker-update")
    except Exception as exc:  # noqa: BLE001 -- fail-soft by contract
        log.warning("tracker: could not reserve %s for %s/%s -- %s", key, cid, sid, exc)
        run = None
    if run is None or not run.scene_identity:
        # A store mid-move or a scene that vanished: no run will ever settle
        # this record, so it must not sit at `pending` for good.
        _fail_by_sid(cid, sid, key, "the update could not be scheduled")
        return
    ident = run.scene_identity
    if identity is not None and ident != identity:
        runs.release_before_start(app, run, "failed",
                                  {"kind": "scene_replaced", "detail": "the scene was replaced"})
        _fail(cid, identity, sid, key, "the scene was replaced before the update started")
        return
    try:
        runs.start_computing(app, run, lambda: _update(app, cid, sid, key, client, ident,
                                                       flag_later))
    except Exception as exc:  # noqa: BLE001 -- `scenes._start_background`'s reason
        # `runner.start` raises with no lifespan running, or when shutdown
        # closed the portal between the reservation and the handoff. A run
        # reserved and never entered stays `running` for the life of the
        # process (and keeps `PUT /config/data-dir` refused), so it is released
        # here, and the record that would otherwise wait on it is failed.
        log.warning("tracker: could not start %s for %s/%s -- %s", key, cid, sid, exc)
        runs.release_before_start(app, run, "failed",
                                  {"kind": "run_failed", "detail": str(exc)})
        _fail(cid, ident, sid, key, str(exc) or "the update could not be started")


def schedule(app, cid: str, sid: str, key: str, client: LLMClient, *,
             flag_later: bool = False) -> None:
    """`mark` then `start`, for a caller with no write hold of its own to mark
    inside -- a user action (Retry, re-run from here) or an opening. Never
    raises. A turn uses the two halves instead (see `mark`)."""
    if mark(cid, sid, key):
        start(app, cid, sid, key, client, flag_later=flag_later)


def schedule_response(app, cid: str, sid: str, rid: str, client: LLMClient) -> None:
    """`schedule` for a response's active variant. Never raises."""
    key = mark_response(cid, sid, rid)
    if key:
        start(app, cid, sid, key, client)


def schedule_untracked(app, cid: str, sid: str, client: LLMClient) -> None:
    """`schedule` every tracked post that has no record yet, in transcript order.

    Only for a scene that was EMPTY before the call (an adopted opener, a
    greeting start), where every post on it is new. Anywhere else "no record"
    may be a record a cut or a prune removed on purpose, and recomputing a whole
    scene's worth of posts is not something to do as a side effect."""
    try:
        if not store.tracker.settings.enabled(cid):
            return
        ident = store.scenes.ensure_identity(cid, sid)
        have = store.tracker.records.read_index(cid, ident)
        keys = [k for _, k in store.tracker.walk.ordered_keys(cid, sid) if k not in have]
    except Exception as exc:  # noqa: BLE001 -- see `schedule`
        log.warning("tracker: could not list untracked posts in %s/%s -- %s", cid, sid, exc)
        return
    for key in keys:
        schedule(app, cid, sid, key, client)


_LOCKS_GUARD = threading.Lock()


def _scene_lock(app, identity: str) -> asyncio.Lock:
    """The one lock every update for this scene runs under.

    WHY: an update's input is the snapshot its predecessor wrote
    (`walk.state_before` reads the latest `ok` record above the post), so two
    updates for one scene running side by side would both start from the same
    prior and the later one would silently drop whatever the earlier one
    changed. The spec's rule is one at a time, in transcript order, and the
    order updates are SCHEDULED in is transcript order: a post's update is
    scheduled when the post lands. `asyncio.Lock` wakes its waiters first come,
    first served, so serialising on it keeps that order.

    An `asyncio.Lock`, not the campaign lock: the wait spans a provider call,
    and the campaign lock is held by every write in the campaign. Every
    `_update` runs on the lifespan loop, so one loop owns every lock here; they
    live on `app.state` (not module scope) for the reason the run registry does
    -- a `TestClient` builds an app per test. The `threading.Lock` only guards
    creating them: `schedule` runs on worker threads, but this is reached from
    `_update` on the loop, and a dict get-or-set is not atomic across both."""
    with _LOCKS_GUARD:
        locks = getattr(app.state, "tracker_locks", None)
        if locks is None:
            locks = app.state.tracker_locks = {}
        lock = locks.get(identity)
        if lock is None:
            lock = locks[identity] = asyncio.Lock()
        return lock


async def _update(app, cid: str, sid: str, key: str, client: LLMClient,
                  identity: str, flag_later: bool = False) -> dict:
    """One post's update, as a run's outcome. `sid` is a hint (see above)."""
    async with _scene_lock(app, identity):
        try:
            return await _update_locked(cid, sid, key, client, identity, flag_later)
        except BaseException as exc:
            # Cancelled (a shutdown mid-call) or a bug: the record must not be
            # left `pending` with no run behind it, which reads as an update
            # still on its way, for good. Shielded, as `character_turns._rescue`
            # is, so the write survives the cancellation that brought us here.
            reason = (_text(exc) if isinstance(exc, Exception)
                      else "the update was interrupted")
            with anyio.CancelScope(shield=True):
                await run_in_threadpool(_fail, cid, identity, sid, key, reason)
            raise


async def _update_locked(cid: str, sid: str, key: str, client: LLMClient,
                         identity: str, flag_later: bool = False) -> dict:
    """`_update`'s body, under the scene's tracker lock. Every failure it
    expects is settled here and returned as the run's outcome."""
    try:
        # Inside the coroutine, so a campaign with no usable connection is
        # a failed record saying why rather than an exception out of the run.
        conn = _require_connection("tracker-update", cid)
    except HTTPException as exc:
        await run_in_threadpool(_fail, cid, identity, sid, key, _detail(exc))
        return {"state": "failed", "error": run_error(exc)}
    try:
        prep = await run_in_threadpool(_prepare, cid, identity, sid, key)
    except Exception as exc:  # noqa: BLE001 -- the walk fails closed on a bad ledger
        await run_in_threadpool(_fail, cid, identity, sid, key, _text(exc))
        return {"state": "failed", "error": {"kind": "run_failed", "detail": _text(exc)}}
    if prep is None:
        return {"state": "landed", "result": {"skipped": True}}
    fields = prep["fields"]
    messages = store.tracker.prompt.build_messages(
        fields, prep["prior"], prep["roster"], prep["present"], prep["newcomers"],
        prep["context_posts"], prep["post_msg"])
    try:
        with store.usage.meter("tracker-update", campaign=cid, scene=prep["sid"],
                               post=prep["post"], response_id=prep["rid"]) as m:
            text = await client.complete(messages, conn, m.usage)
        reply = store.tracker.merge.parse_reply(text)
    except LLMError as exc:
        await run_in_threadpool(_fail, cid, identity, prep["sid"], key, _text(exc))
        return {"state": "failed", "error": run_error(_llm_http_error(exc))}
    except store.tracker.merge.TrackerReplyError as exc:
        await run_in_threadpool(_fail, cid, identity, prep["sid"], key, _text(exc))
        return {"state": "failed", "error": {"kind": "bad_reply", "detail": _text(exc)}}
    snapshot, changed = store.tracker.merge.apply_reply(
        prep["prior"], reply, fields, prep["roster"], prep["present"],
        store.scenes.match_name)
    try:
        written = await run_in_threadpool(
            _commit, cid, identity, prep["sid"], key, snapshot, changed,
            store.tracker.fields.digest(fields), effective_model(conn), flag_later)
    except Exception as exc:  # noqa: BLE001 -- as `_prepare`: never leave it `pending`
        await run_in_threadpool(_fail, cid, identity, prep["sid"], key, _text(exc))
        return {"state": "failed", "error": {"kind": "run_failed", "detail": _text(exc)}}
    if not written:
        return {"state": "landed", "result": {"skipped": True}}
    return {"state": "landed", "result": {"changed": len(changed)}}


def _detail(exc: HTTPException) -> str:
    detail = exc.detail
    if isinstance(detail, dict):
        return str(detail.get("detail") or detail.get("kind") or detail)
    return str(detail)


def _text(exc: Exception) -> str:
    return str(exc) or type(exc).__name__


def _current_sid(cid: str, hint: str, identity: str) -> str | None:
    """The scene's `sid` now, or `None` once it is deleted. The hint is tried
    first, so the ordinary case costs one header read rather than a scan."""
    if store.scenes.scene_identity(cid, hint) == identity:
        return hint
    return store.scenes.find_by_identity(cid, identity)


def _speaker(m: dict, player: str) -> str:
    if m.get("role") == "user":
        return m.get("speaker") or player or "You"
    return m.get("speaker") or "Grimoire"


def _locate(cid: str, sid: str, key: str) -> dict | None:
    """Where `key`'s post sits and what it says, or `None` once it is gone.

    A response's key is live while the response is on the transcript and the
    variant is still one of its variants -- active or not. The spec keeps every
    variant's record ("nothing is cancelled", and a swipe back must be free),
    so an update whose variant was swiped away while it waited still computes
    its record, at the response's position, from the variant's own text. Only a
    post that is GONE (cut, retconned, taken back after a failed turn) has
    nothing to record."""
    messages = store.scenes.read_scene(cid, sid)["messages"]
    walked = {k: i for i, k in store.tracker.walk.ordered_keys(cid, sid)}
    player = store.appearances.player_label(cid, sid)
    if key.startswith("p-"):
        index = walked.get(key)
        if index is None:
            return None
        m = messages[index]
        return {"index": index, "first": index, "post": index, "rid": "",
                "post_msg": {"speaker": _speaker(m, player), "content": m.get("content", "")}}
    rid, vid = key[2:34], key[35:]
    parts = [i for i, m in enumerate(messages) if m.get("response_id") == rid]
    if not parts:
        return None
    record = store.responses.get(cid, sid, rid)
    variant = next((v for v in record.get("variants", []) if v.get("id") == vid), None)
    if variant is None:
        return None
    if walked.get(key) is not None:
        # The active variant: the transcript's text, every part of it.
        content = "\n\n".join(messages[i].get("content", "") for i in parts)
    else:
        content = variant.get("content", "")
    speaker = record.get("speaker") or _speaker(messages[parts[-1]], player)
    return {"index": parts[-1], "first": parts[0], "post": record.get("post"), "rid": rid,
            "post_msg": {"speaker": speaker, "content": content}}


def _context_posts(cid: str, sid: str, first: int) -> list[dict]:
    """The two non-synthetic messages before the post, oldest first. Rolls,
    transitions and director notes are not things a character did."""
    messages = store.scenes.read_scene(cid, sid)["messages"]
    player = store.appearances.player_label(cid, sid)
    out: list[dict] = []
    for m in reversed(messages[:first]):
        if m.get("speaker") in store.scenes.SYNTHETIC_SPEAKERS:
            continue
        out.append({"speaker": _speaker(m, player), "content": m.get("content", "")})
        if len(out) == 2:
            break
    return out[::-1]


def _prepare(cid: str, identity: str, hint: str, key: str) -> dict | None:
    """Everything the update call needs, read under one campaign-lock hold so it
    describes one transcript. `None` when there is nothing to do -- the scene
    or the post is gone, or the tracker was switched off while this waited --
    and the record has been settled accordingly."""
    walk = store.tracker.walk
    with store.locks.campaign_lock(cid):
        sid = _current_sid(cid, hint, identity)
        if sid is None:
            return None         # deleted: `delete_scene` already dropped its records
        if not store.tracker.settings.enabled(cid):
            # Switched off after this was scheduled. Turning it off is a choice
            # not to pay for updates, including the ones still queued.
            _settle_unwritten(cid, sid, identity, key,
                              "the tracker was switched off before this update ran")
            return None
        located = _locate(cid, sid, key)
        if located is None:
            _settle_unwritten(cid, sid, identity, key, "")
            return None
        index = located["index"]
        _, prior = walk.state_before(cid, sid, index)
        present = walk.present_at(cid, sid, index)
        return {
            "sid": sid, "post": located["post"], "rid": located["rid"],
            "fields": store.tracker.fields.effective(cid, sid),
            "prior": prior, "roster": walk.roster(cid, sid), "present": present,
            "newcomers": [store.tracker.prompt.newcomer(cid, ref)
                          for ref in sorted(present) if ref not in prior],
            "context_posts": _context_posts(cid, sid, located["first"]),
            "post_msg": located["post_msg"],
        }


def _settle_unwritten(cid: str, sid: str, identity: str, key: str, reason: str) -> None:
    """A `pending` record no result will reach. Called under the campaign lock.

    A post that is gone has no record to keep, so its entry (and any older
    snapshot) is discarded -- left behind, it would read as `pending` forever.
    A post still there is marked `failed` with `reason`, which keeps any older
    snapshot file and offers Retry."""
    if _locate(cid, sid, key) is None:
        store.tracker.records.discard(cid, identity, [key])
    else:
        store.tracker.records.mark_failed(cid, identity, key, reason)
    # A detached run writes after the activity middleware stamped its request,
    # so it stamps for itself (CLAUDE.md, the write token).
    store.revision.bump(cid)


def _commit(cid: str, identity: str, hint: str, key: str, snapshot: dict,
            changed: list, digest: str, model: str, flag_later: bool = False) -> bool:
    """Store the result, unless the post went away while the model answered.

    RE-CHECKED under the lock that covers the write, because the check in
    `_prepare` is a provider call old: a cut, a retcon or a failed turn's
    take-back may have removed the post since, and a record written for it
    would be a snapshot of something that no longer happened (and, keyed by an
    id rather than an index, would never be overwritten). A rename is not a
    reason to drop it -- the identity finds the scene under its new id.

    `flag_later` (a Retry of a record that had no result): every later record
    on the walk was built without this one, so each gets `upstream_changed` --
    in the same hold, so no reader sees the new result beside unflagged
    successors. Only when the key is ON the walk: an inactive variant's record
    is not what any later record was built on."""
    with store.locks.campaign_lock(cid):
        sid = _current_sid(cid, hint, identity)
        if sid is None:
            # Deleted while the model answered. `delete_scene` dropped the
            # whole directory; writing now would resurrect it as an orphan.
            return False
        if _locate(cid, sid, key) is None:
            _settle_unwritten(cid, sid, identity, key, "")
            return False
        store.tracker.records.save(cid, identity, key, snapshot, changed=changed,
                                   fields_digest=digest, model=model)
        if flag_later:
            at = store.tracker.walk.index_of(cid, sid, key)
            if at is not None:
                store.tracker.walk.flag_after(cid, sid, at)
        # Inside the hold, for the write token's rule: a token minted after the
        # lock is released is one a reader can hold while this write is still
        # the newest thing in the campaign it has not seen.
        store.revision.bump(cid)
        return True


def _fail(cid: str, identity: str, hint: str, key: str, error: str) -> None:
    """Mark `key` failed. Never raises: this runs on paths that are already
    reporting a failure, and a second one would only bury the first."""
    try:
        with store.locks.campaign_lock(cid):
            if _current_sid(cid, hint, identity) is None:
                return          # deleted: nothing to mark, and nothing to resurrect
            store.tracker.records.mark_failed(cid, identity, key, error)
            store.revision.bump(cid)
    except Exception as exc:  # noqa: BLE001 -- see the docstring
        log.warning("tracker: could not mark %s failed in %s -- %s", key, cid, exc)


def _fail_by_sid(cid: str, sid: str, key: str, error: str) -> None:
    """`_fail` for a caller that has no identity in hand -- a reservation that
    never happened. Never raises."""
    try:
        ident = store.scenes.scene_identity(cid, sid)
    except Exception as exc:  # noqa: BLE001 -- as `_fail`
        log.warning("tracker: could not mark %s failed in %s -- %s", key, cid, exc)
        return
    if ident:
        _fail(cid, ident, sid, key, error)


# --- scene records -------------------------------------------------------------
#
# What the disclosure reads and writes. Reads work whatever the setting, so a
# campaign that switched the tracker off can still look at what it recorded;
# the three writes refuse with `tracker_off`, because each of them either
# schedules a paid update or edits a record nothing will keep up to date.

INTERRUPTED = "interrupted"
"""The `error` a `pending` record with no run behind it is reported with."""


def _require_on(cid: str) -> None:
    if not store.tracker.settings.enabled(cid):
        raise HTTPException(status_code=409, detail="tracker_off")


def _require_key(key: str) -> None:
    # Checked before anything is read: a key becomes a file name.
    if not store.tracker.paths.valid_key(key):
        raise HTTPException(status_code=404, detail="tracker record not found")


def _update_live(app, cid: str, identity: str | None) -> bool:
    """Whether anything still running can settle this scene's `pending` records.

    A tracker update can, obviously. So can a TURN: it marks its keys `pending`
    inside the hold that writes each post, but starts their runs only after its
    terminal frames (`character_turns._start_tracking`) -- and a client that
    reads the summary on the `done` frame lands in exactly that gap. The turn
    run is still live there (it finishes when its generator does, after that
    `finally`), so counting it keeps a record that is about to start from
    flickering to "interrupted"."""
    if not identity:
        return False
    for run in app.state.runs.for_subject(("scene", cid, identity)):
        if run.terminal.is_set():
            continue
        if run.kind == "tracker-update" or run.cls == "turn":
            return True
    return False


def _reported(entry: dict, live: bool) -> dict:
    """The entry as a reader should see it.

    A `pending` record with nothing live behind it is one no run will ever
    settle: the process stopped between the mark and the result, or a start
    that failed could not even write `failed`. Read literally it says
    "updating..." for good and offers nothing; reported as failed it offers
    Retry, which is the one action that settles it. Reported, not rewritten --
    a GET takes no lock, and the next mark or save overwrites it anyway."""
    if entry.get("status") == "pending" and not live:
        return {**entry, "status": "failed", "error": INTERRUPTED}
    return entry


def _flags(entry: dict) -> dict:
    return {**dict.fromkeys(store.tracker.records.FLAGS, False), **(entry.get("flags") or {})}


def _moods(snapshot: dict, fields: list[dict]) -> dict:
    """`{ref: visible_mood}` for the characters present at the tail, for the
    cast tiles. Nothing for a character with no mood yet, and nothing at all
    when the field is switched off for this scene."""
    if not any(f["key"] == "visible_mood" for f in store.tracker.fields.active(fields)):
        return {}
    out = {}
    for ref, ent in snapshot.items():
        value = ((ent.get("fields") or {}).get("visible_mood") or {}).get("value")
        if ent.get("present") and value:
            out[ref] = value
    return out


@router.get("/campaigns/{cid}/scenes/{sid}/tracker")
def get_scene_tracker(cid: str, sid: str, request: Request):
    """Every tracked post's key in transcript order, with its index entry --
    the disclosures' collapsed summaries in one read, no snapshot among them."""
    _require_scene(cid, sid)
    walk = store.tracker.walk
    ident = store.scenes.scene_identity(cid, sid)
    entries = store.tracker.records.read_index(cid, ident) if ident else {}
    live = _update_live(request.app, cid, ident)
    fields = store.tracker.fields.effective(cid, sid)
    _, current = walk.current(cid, sid)
    return {
        "enabled": store.tracker.settings.enabled(cid),
        "names": walk.roster(cid, sid),
        "keys": [{"index": i, "key": k} for i, k in walk.ordered_keys(cid, sid)],
        "entries": {k: _reported(e, live) for k, e in entries.items()},
        "moods": _moods(current, fields),
        # Switched-off fields included: a stored value still needs its label.
        "labels": {f["key"]: f["label"] for f in fields},
    }


def _record_body(app, cid: str, sid: str, identity: str, key: str) -> dict:
    entry = store.tracker.records.read_index(cid, identity).get(key)
    if entry is None:
        raise HTTPException(status_code=404, detail="tracker record not found")
    shown = _reported(entry, _update_live(app, cid, identity))
    body = store.tracker.records.read_snapshot(cid, identity, key)
    out = {
        "key": key,
        "status": shown.get("status"),
        "flags": _flags(shown),
        "snapshot": body["snapshot"] if body else None,
        "fields": store.tracker.fields.effective(cid, sid),
        "names": store.tracker.walk.roster(cid, sid),
    }
    if shown.get("error"):
        out["error"] = shown["error"]
    return out


def _identity_or_404(cid: str, sid: str) -> str:
    ident = store.scenes.scene_identity(cid, sid)
    if not ident:
        # A scene that never minted an identity has never stored a record.
        raise HTTPException(status_code=404, detail="tracker record not found")
    return ident


@router.get("/campaigns/{cid}/scenes/{sid}/tracker/records/{key}")
def get_tracker_record(cid: str, sid: str, key: str, request: Request):
    _require_scene(cid, sid)
    _require_key(key)
    return _record_body(request.app, cid, sid, _identity_or_404(cid, sid), key)


@router.put("/campaigns/{cid}/scenes/{sid}/tracker/records/{key}")
def put_tracker_record(cid: str, sid: str, key: str, body: TrackerEdit, request: Request):
    """A person's edit of one record's values. Nothing re-runs: every later
    record is flagged `upstream_changed`, and re-running is the person's call.

    Under one campaign-lock hold from the read to the flags, so the edit is
    applied to the snapshot it replaces and the flags land with it."""
    _require_scene(cid, sid)
    _require_key(key)
    _require_on(cid)
    records, walk = store.tracker.records, store.tracker.walk
    with store.locks.campaign_lock(cid):
        ident = _identity_or_404(cid, sid)
        entry = records.read_index(cid, ident).get(key)
        located = _locate(cid, sid, key) if entry is not None else None
        if entry is None or located is None:
            # No record, or one for a post that is gone (the next prune drops it).
            raise HTTPException(status_code=404, detail="tracker record not found")
        stored = records.read_snapshot(cid, ident, key)
        # A record with no snapshot (failed before it ever landed) is edited
        # from the state its post started from -- what the disclosure shows.
        prev = stored["snapshot"] if stored else walk.state_before(cid, sid, located["index"])[1]
        fields = store.tracker.fields.effective(cid, sid)
        try:
            snapshot, changed = store.tracker.merge.apply_edit(prev, body.edits, fields)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        # The post's own change list keeps what the update found, with the
        # person's changes laid over the same fields: it is the summary of what
        # changed AT this post, and the edit is one more thing that did.
        touched = {(c[0], c[1]) for c in changed}
        kept = ([c for c in entry.get("changed") or [] if (c[0], c[1]) not in touched]
                if entry.get("status") == "ok" else [])
        records.save(cid, ident, key, snapshot, changed=kept + changed,
                     fields_digest=store.tracker.fields.digest(fields), model="user")
        # `save` clears the flags, which is right for a re-run and wrong here:
        # a hand edit of one value does not make the rest of the record agree
        # with an edited post or an earlier change, so whatever was raised stays.
        for flag, raised in _flags(entry).items():
            if raised:
                records.set_flags(cid, ident, [key], flag)
        at = walk.index_of(cid, sid, key)
        if at is not None:
            # Only from a key ON the walk: an inactive variant is not what any
            # later record was built on.
            walk.flag_after(cid, sid, at)
    return _record_body(request.app, cid, sid, ident, key)


@router.post("/campaigns/{cid}/scenes/{sid}/tracker/records/{key}/retry")
def post_tracker_retry(cid: str, sid: str, key: str, request: Request,
                       client: LLMClient = Depends(get_llm)):
    """Run `key`'s update again. Its post must still be there; a record need
    not exist -- an untracked post is what Retry is offered on.

    When the record had no usable result (failed, interrupted, or never
    tracked), a success flags every later record: each was built without this
    post's contribution. A retry of an `ok` record leaves them alone."""
    _require_scene(cid, sid)
    _require_key(key)
    _require_on(cid)
    if _locate(cid, sid, key) is None:
        raise HTTPException(status_code=404, detail="tracker record not found")
    ident = store.scenes.scene_identity(cid, sid)
    entry = store.tracker.records.read_index(cid, ident).get(key) if ident else None
    # Captured BEFORE the mark, which overwrites the status with `pending`.
    status = (_reported(entry, _update_live(request.app, cid, ident))["status"]
              if entry else None)
    schedule(request.app, cid, sid, key, client,
             flag_later=status not in ("ok", "pending"))
    return get_scene_tracker(cid, sid, request)


@router.post("/campaigns/{cid}/scenes/{sid}/tracker/records/{key}/rerun-from")
def post_tracker_rerun_from(cid: str, sid: str, key: str, request: Request,
                            client: LLMClient = Depends(get_llm)):
    """Re-run `key` and every tracked post after it, in transcript order.

    Scheduled in that order, which is the order they run in (`_scene_lock`),
    so each starts from what the one before it just wrote. Each save clears its
    own flags; nothing needs flagging, because everything after is re-run."""
    _require_scene(cid, sid)
    _require_key(key)
    _require_on(cid)
    ordered = [k for _, k in store.tracker.walk.ordered_keys(cid, sid)]
    if key not in ordered:
        raise HTTPException(status_code=404, detail="tracker record not found")
    for later in ordered[ordered.index(key):]:
        schedule(request.app, cid, sid, later, client)
    return get_scene_tracker(cid, sid, request)


# --- transcript hooks -----------------------------------------------------------
#
# Called by the routes that change a transcript's shape, after their store
# write and inside the hold that covers it, so the records move with the
# transcript rather than after another request could have read it. None of
# them raises: the write they follow is the user's and has landed, and stale
# tracker bookkeeping is worth a log line, not a failed edit.

def _soft(what: str, cid: str, sid: str, fn, *args) -> None:
    try:
        fn(cid, sid, *args)
    except Exception as exc:  # noqa: BLE001 -- see the section comment
        log.warning("tracker: could not %s in %s/%s -- %s", what, cid, sid, exc)


def after_text_edit(cid: str, sid: str, index: int) -> None:
    """The post at `index` was rewritten (an edit or a retcon): its record no
    longer matches its text, and every later one was built on it."""
    _soft("flag an edit", cid, sid, store.tracker.walk.flag_edited, index)


def after_cut(cid: str, sid: str, index: int | None = None) -> None:
    """Posts left the transcript: drop their records. `index`, when known, is
    the first position the removal renumbered -- every record from there on
    was built on something that is gone, so it is flagged."""
    _soft("prune records", cid, sid, store.tracker.walk.prune)
    if index is not None:
        _soft("flag later records", cid, sid, store.tracker.walk.flag_after, index - 1)


def after_swipe(cid: str, sid: str, rid: str) -> None:
    """Response `rid`'s active variant changed. Its own record is the
    variant's cached one (nothing re-runs), but every later record was built
    on the variant that was active before."""
    def flag(c: str, s: str) -> None:
        messages = store.scenes.read_scene(c, s)["messages"]
        parts = [i for i, m in enumerate(messages) if m.get("response_id") == rid]
        if parts:
            store.tracker.walk.flag_after(c, s, parts[-1])
    _soft("flag a swipe", cid, sid, flag)
