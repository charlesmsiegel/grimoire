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
    world = store.tracker.fields.world_fields(_campaign_world(cid))
    return _layer_body(world, store.tracker.fields.campaign_layer(cid, world))


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
    campaign = store.tracker.fields.campaign_fields(cid)
    return _layer_body(campaign, store.tracker.fields.scene_layer(cid, sid, campaign))


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

Mark = tuple[str, int]
"""A key `mark` set `pending`, with the generation that mark owns
(`records.GEN`). The run started for it carries both: a run whose generation
the entry has moved past is obsolete -- a newer run for the same key was marked
after it, or a person edited the record -- and is skipped or its result
discarded rather than allowed to land over whatever superseded it."""


def mark(cid: str, sid: str, key: str) -> Mark | None:
    """The first half of scheduling: mark `key` `pending`, and return it with
    its generation -- or `None` when the tracker is off or the mark could not
    be written.

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
        gen = store.tracker.records.mark_pending(cid, ident, key)
    except Exception as exc:  # noqa: BLE001 -- see the docstring
        log.warning("tracker: could not mark %s pending in %s/%s -- %s", key, cid, sid, exc)
        return None
    return key, gen


def mark_response(cid: str, sid: str, rid: str) -> Mark | None:
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


def start(app, cid: str, sid: str, marked: Mark, client: LLMClient,
          identity: str | None = None, *, flag_later: bool = False,
          trust_base: bool = False) -> None:
    """The second half: start the run for what `mark` returned. Never raises.

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
    lands (`_commit`). `trust_base` is the first key of an explicit re-run
    from here: the person chose that starting point with its base record's
    warning in view, so a stale base does not make the result stale
    (`_prepare`) -- a base that is not its predecessor's final state still
    does (`Base`)."""
    key, gen = marked
    try:
        run = runs.reserve_background(app, cid, sid, "tracker-update")
    except Exception as exc:  # noqa: BLE001 -- fail-soft by contract
        log.warning("tracker: could not reserve %s for %s/%s -- %s", key, cid, sid, exc)
        run = None
    if run is None or not run.scene_identity:
        # A store mid-move or a scene that vanished: no run will ever settle
        # this record, so it must not sit at `pending` for good.
        _fail_by_sid(cid, sid, key, "the update could not be scheduled", gen)
        return
    ident = run.scene_identity
    if identity is not None and ident != identity:
        runs.release_before_start(app, run, "failed",
                                  {"kind": "scene_replaced", "detail": "the scene was replaced"})
        _fail(cid, identity, sid, key, "the scene was replaced before the update started", gen)
        return
    try:
        runs.start_computing(app, run, lambda: _update(app, cid, sid, key, gen, client, ident,
                                                       flag_later, trust_base))
    except Exception as exc:  # noqa: BLE001 -- `scenes._start_background`'s reason
        # `runner.start` raises with no lifespan running, or when shutdown
        # closed the portal between the reservation and the handoff. A run
        # reserved and never entered stays `running` for the life of the
        # process (and keeps `PUT /config/data-dir` refused), so it is released
        # here, and the record that would otherwise wait on it is failed.
        log.warning("tracker: could not start %s for %s/%s -- %s", key, cid, sid, exc)
        runs.release_before_start(app, run, "failed",
                                  {"kind": "run_failed", "detail": str(exc)})
        _fail(cid, ident, sid, key, str(exc) or "the update could not be started", gen)


def schedule(app, cid: str, sid: str, key: str, client: LLMClient, *,
             flag_later: bool = False, trust_base: bool = False) -> None:
    """`mark` then `start`, for a caller with no write hold of its own to mark
    inside -- a user action (Retry, re-run from here) or an opening. Never
    raises. A turn uses the two halves instead (see `mark`)."""
    marked = mark(cid, sid, key)
    if marked:
        start(app, cid, sid, marked, client, flag_later=flag_later, trust_base=trust_base)


def schedule_response(app, cid: str, sid: str, rid: str, client: LLMClient) -> None:
    """`schedule` for a response's active variant. Never raises."""
    marked = mark_response(cid, sid, rid)
    if marked:
        start(app, cid, sid, marked, client)


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


def _scene_lock(app, cid: str, identity: str) -> asyncio.Lock:
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
    `_update` on the loop, and a dict get-or-set is not atomic across both.

    Keyed by `(cid, identity)`, not the identity alone: a fork copies its
    source's scene files, identities included, and two campaigns' scenes
    sharing one lock would queue each other's updates for nothing.

    One PROCESS's lock: a second grimoire process on the same store runs its
    own updates beside these. What keeps that from landing a record built on
    a base the other process was replacing is `Base`, checked at commit."""
    with _LOCKS_GUARD:
        locks = getattr(app.state, "tracker_locks", None)
        if locks is None:
            locks = app.state.tracker_locks = {}
        lock = locks.get((cid, identity))
        if lock is None:
            lock = locks[(cid, identity)] = asyncio.Lock()
        return lock


async def _update(app, cid: str, sid: str, key: str, gen: int | None, client: LLMClient,
                  identity: str, flag_later: bool = False, trust_base: bool = False) -> dict:
    """One post's update, as a run's outcome. `sid` is a hint (see above)."""
    async with _scene_lock(app, cid, identity):
        try:
            return await _update_locked(cid, sid, key, gen, client, identity, flag_later,
                                        trust_base)
        except BaseException as exc:
            # Cancelled (a shutdown mid-call) or a bug: the record must not be
            # left `pending` with no run behind it, which reads as an update
            # still on its way, for good. Shielded, as `character_turns._rescue`
            # is, so the write survives the cancellation that brought us here.
            reason = (_text(exc) if isinstance(exc, Exception)
                      else "the update was interrupted")
            with anyio.CancelScope(shield=True):
                await run_in_threadpool(_fail, cid, identity, sid, key, reason, gen)
            raise


async def _update_locked(cid: str, sid: str, key: str, gen: int | None, client: LLMClient,
                         identity: str, flag_later: bool = False,
                         trust_base: bool = False) -> dict:
    """`_update`'s body, under the scene's tracker lock. Every failure it
    expects is settled here and returned as the run's outcome."""
    try:
        # Inside the coroutine, so a campaign with no usable connection is
        # a failed record saying why rather than an exception out of the run.
        # In a worker: resolving a connection reads the campaign and the
        # connection files, and this runs on the lifespan loop.
        conn = await run_in_threadpool(_require_connection, "tracker-update", cid)
    except HTTPException as exc:
        await run_in_threadpool(_fail, cid, identity, sid, key, _detail(exc), gen)
        return {"state": "failed", "error": run_error(exc)}
    try:
        prep = await run_in_threadpool(_prepare, cid, identity, sid, key, gen, trust_base)
    except Exception as exc:  # noqa: BLE001 -- the walk fails closed on a bad ledger
        await run_in_threadpool(_fail, cid, identity, sid, key, _text(exc), gen)
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
        await run_in_threadpool(_fail, cid, identity, prep["sid"], key, _text(exc), gen)
        return {"state": "failed", "error": run_error(_llm_http_error(exc))}
    except store.tracker.merge.TrackerReplyError as exc:
        await run_in_threadpool(_fail, cid, identity, prep["sid"], key, _text(exc), gen)
        return {"state": "failed", "error": {"kind": "bad_reply", "detail": _text(exc)}}
    snapshot, changed = store.tracker.merge.apply_reply(
        prep["prior"], reply, fields, prep["roster"], prep["present"],
        store.scenes.match_name)
    # A re-run or Retry rebuilds this record from the one before it; whatever
    # a person typed into it, and the reply did not itself move, is kept.
    snapshot, changed, restored = store.tracker.merge.keep_user_values(
        snapshot, prep["own"], prep["prior"], changed, prep["set_here"])
    try:
        written = await run_in_threadpool(
            _commit, cid, identity, prep["sid"], key, snapshot, changed,
            store.tracker.fields.digest(fields), effective_model(conn), flag_later,
            prep["seen_seq"], gen, prep["stale_base"], restored, prep["base"])
    except Exception as exc:  # noqa: BLE001 -- as `_prepare`: never leave it `pending`
        await run_in_threadpool(_fail, cid, identity, prep["sid"], key, _text(exc), gen)
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


def _shown(cid: str, messages: list[dict], indices: list[int]) -> dict[int, str]:
    """The prompt view (`store/regex`) of the messages at `indices`, by index:
    the update reads what a turn prompt would show, depth counted over the
    whole transcript."""
    lo, hi = min(indices), max(indices)
    window = store.regex.view.view(messages[lo:hi + 1], cid=cid, phase="prompt",
                                   offset=lo, total=len(messages))
    return {i: window[i - lo].get("content", "") for i in indices}


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
                "post_msg": {"speaker": _speaker(m, player),
                             "content": _shown(cid, messages, [index])[index]}}
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
        shown = _shown(cid, messages, parts)
        content = "\n\n".join(shown[i] for i in parts)
    else:
        # Viewed as the reply it would be, where the response stands.
        stand_in = {**messages[parts[-1]], "content": variant.get("content", ""),
                    "connection": variant.get("connection", "")}
        content = store.regex.view.view([stand_in], cid=cid, phase="prompt",
                                        offset=parts[-1], total=len(messages))[0]["content"]
    speaker = record.get("speaker") or _speaker(messages[parts[-1]], player)
    return {"index": parts[-1], "first": parts[0], "post": record.get("post"), "rid": rid,
            "post_msg": {"speaker": speaker, "content": content}}


def _context_posts(cid: str, sid: str, first: int) -> list[dict]:
    """The two non-synthetic messages before the post, oldest first. Rolls,
    transitions and director notes are not things a character did."""
    messages = store.scenes.read_scene(cid, sid)["messages"]
    player = store.appearances.player_label(cid, sid)
    picked: list[int] = []
    for i in range(min(first, len(messages)) - 1, -1, -1):
        if messages[i].get("speaker") in store.scenes.SYNTHETIC_SPEAKERS:
            continue
        picked.append(i)
        if len(picked) == 2:
            break
    if not picked:
        return []
    shown = _shown(cid, messages, picked)
    return [{"speaker": _speaker(messages[i], player), "content": shown[i]}
            for i in reversed(picked)]


def _obsolete(cid: str, identity: str, key: str, gen: int | None) -> bool:
    """Whether a run owning generation `gen` has been superseded (`Mark`).
    Called under the campaign lock. `None` -- a caller with no generation to
    compare -- is never obsolete."""
    if gen is None:
        return False
    entry = store.tracker.records.read_index(cid, identity).get(key)
    return store.tracker.records.generation(entry) != gen


def _prepare(cid: str, identity: str, hint: str, key: str, gen: int | None = None,
             trust_base: bool = False) -> dict | None:
    """Everything the update call needs, read under one campaign-lock hold so it
    describes one transcript. `None` when there is nothing to do -- the scene
    or the post is gone, the tracker was switched off while this waited, or the
    run is obsolete (`Mark`) -- and the record has been settled accordingly.

    `stale_base`: the record this one is built on (the latest `ok` record
    before the post) carries a staleness flag of its own, so the result will
    be built on a state the transcript no longer stands behind -- `_commit`
    raises `upstream_changed` on it. Not for `trust_base` (the first key of an
    explicit re-run, started from that base on purpose).

    `stale_base` also when a post between that base and this one still has an
    update `pending`, `trust_base` or not: the base is then not its
    predecessor's final state, and the result lacks whatever that update
    changes (`_base_unsettled`). And `base` is the base's fingerprint, which
    `_commit` takes again (`_base_mark`)."""
    walk = store.tracker.walk
    records = store.tracker.records
    with store.locks.campaign_lock(cid):
        sid = _current_sid(cid, hint, identity)
        if sid is None:
            return None         # deleted: `delete_scene` already dropped its records
        if _obsolete(cid, identity, key, gen):
            # A newer run for this key was marked, or a person edited the
            # record, after this one was. The newer run settles the record; an
            # edit already did. Either way this one has nothing to add.
            return None
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
        base_key, prior = walk.state_before(cid, sid, index)
        # Read in the hold that reads the post and its prior: the flags this
        # run answers are the ones raised up to here. Nothing is cleared now --
        # a run that fails or dies answered nothing -- and `_commit` clears
        # them only if none went up while the model answered.
        entries = records.read_index(cid, identity)
        seen = records.flag_seq(entries.get(key))
        base_flags = ((entries.get(base_key) or {}).get("flags") or {}) if base_key else {}
        stale_base = ((not trust_base and any(base_flags.get(f) for f in records.FLAGS))
                      or _base_unsettled(cid, sid, index, base_key, entries))
        own = records.read_snapshot(cid, identity, key)
        present = walk.present_at(cid, sid, index)
        return {
            "sid": sid, "post": located["post"], "rid": located["rid"],
            "fields": store.tracker.fields.effective(cid, sid),
            "prior": prior, "roster": walk.roster(cid, sid), "present": present,
            "newcomers": [store.tracker.prompt.newcomer(cid, ref)
                          for ref in sorted(present) if ref not in prior],
            "context_posts": _context_posts(cid, sid, located["first"]),
            "post_msg": located["post_msg"],
            "seen_seq": seen,
            "stale_base": stale_base,
            "base": _base_mark(base_key, entries),
            "own": own["snapshot"] if own else None,
            # The pairs this post's own record changed, and those a person
            # touched there without moving a value (an awareness-only edit is
            # in no change list) -- what was set AT it, as opposed to what it
            # inherited (`merge.keep_user_values`). Read in this hold, with
            # the snapshot it describes; the index is normalised on read, so
            # every change is a `[ref, field, value]`.
            "set_here": ({(c[0], c[1]) for c in (entries.get(key) or {}).get("changed") or []}
                         | {(p[0], p[1]) for p in records.pairs(
                             (entries.get(key) or {}).get(records.TOUCHED))}),
        }


Base = tuple[str | None, int, int]
"""What an update's input stood on, as `_prepare` read it: the base record's
key (the newest `ok` record before the post, `walk.state_before`) with its
mark generation and flag sequence. `_commit` reads it again under the lock
that covers the write, and a result whose base has moved lands with
`upstream_changed`.

WHY, when `_scene_lock` already runs a scene's updates one at a time: that
lock is one process's. Two grimoire processes on one store
(docs/store-guarantees.md, "A second process on the same store") share the
campaign lock and nothing else, so each can run an adjacent post's update
while the other's is answering -- and the later one then reads a base the
earlier is about to replace. Nothing re-runs on its account (the spec's
rule: later records are flagged, and re-running is the person's call); the
flag is what keeps a record built on a superseded state from reading fresh.

The generation moves on every `mark_pending` and every hand edit, so a base
re-run or edited under the update is caught even when it lands back on the
same key; the sequence catches a flag raised on it. A base re-marked and not
yet landed is no longer `ok`, so the walk steps past it to an older key."""


def _base_mark(base_key: str | None, entries: dict) -> Base:
    entry = entries.get(base_key) if base_key else None
    records = store.tracker.records
    return base_key, records.generation(entry), records.flag_seq(entry)


def _base_unsettled(cid: str, sid: str, index: int, base_key: str | None,
                    entries: dict) -> bool:
    """Whether a post between the base and the post at `index` has an update
    still `pending` -- one that will land after this update read its base.

    In one process that cannot be an update queued AHEAD of this one, which
    `_scene_lock` has already let land; it is another process's update, one
    queued behind this one (a Retry of an earlier post), or one that died.
    In every case this result is built without that post's contribution. A
    `failed` post is not counted: its final state is "no result", and a Retry
    that gives it one flags every later record itself (`flag_later`)."""
    for i, key in reversed(store.tracker.walk.ordered_keys(cid, sid)):
        if i >= index:
            continue
        if key == base_key:
            return False
        if (entries.get(key) or {}).get("status") == "pending":
            return True
    return False


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
            changed: list, digest: str, model: str, flag_later: bool = False,
            seen_seq: int = 0, gen: int | None = None, stale_base: bool = False,
            touched: list | None = None, base: Base | None = None) -> bool:
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
    is not what any later record was built on.

    `gen` is re-checked here too (`Mark`): a newer run marked, or a hand edit
    made, while the model answered means this result is not the one to keep.
    `stale_base` (`_prepare`) raises `upstream_changed` in the same write, and
    so does a `base` that has moved since `_prepare` read it (`Base`).
    `touched`: the pairs `merge.keep_user_values` restored, which stay the
    person's (`records.TOUCHED`) so the next re-run restores them too."""
    with store.locks.campaign_lock(cid):
        sid = _current_sid(cid, hint, identity)
        if sid is None:
            # Deleted while the model answered. `delete_scene` dropped the
            # whole directory; writing now would resurrect it as an orphan.
            return False
        if _obsolete(cid, identity, key, gen):
            return False
        located = _locate(cid, sid, key)
        if located is None:
            _settle_unwritten(cid, sid, identity, key, "")
            return False
        if base is not None and not stale_base:
            base_key, _ = store.tracker.walk.state_before(cid, sid, located["index"])
            entries = store.tracker.records.read_index(cid, identity)
            stale_base = _base_mark(base_key, entries) != base
        # `seen_seq`: the flags are cleared only if none went up since
        # `_prepare` read this result's inputs -- one that did is a write the
        # result never saw, and the record must keep saying so.
        store.tracker.records.save(cid, identity, key, snapshot, changed=changed,
                                   fields_digest=digest, model=model, seen_seq=seen_seq,
                                   raise_upstream=stale_base, touched=touched)
        if flag_later:
            at = store.tracker.walk.index_of(cid, sid, key)
            if at is not None:
                store.tracker.walk.flag_after(cid, sid, at)
        # Inside the hold, for the write token's rule: a token minted after the
        # lock is released is one a reader can hold while this write is still
        # the newest thing in the campaign it has not seen.
        store.revision.bump(cid)
        return True


def _fail(cid: str, identity: str, hint: str, key: str, error: str,
          gen: int | None = None) -> None:
    """Mark `key` failed -- unless the run owning `gen` is obsolete, when the
    record belongs to whatever superseded it (`records.mark_failed`). Never
    raises: this runs on paths that are already reporting a failure, and a
    second one would only bury the first."""
    try:
        with store.locks.campaign_lock(cid):
            if _current_sid(cid, hint, identity) is None:
                return          # deleted: nothing to mark, and nothing to resurrect
            store.tracker.records.mark_failed(cid, identity, key, error, gen)
            store.revision.bump(cid)
    except Exception as exc:  # noqa: BLE001 -- see the docstring
        log.warning("tracker: could not mark %s failed in %s -- %s", key, cid, exc)


def _fail_by_sid(cid: str, sid: str, key: str, error: str, gen: int | None = None) -> None:
    """`_fail` for a caller that has no identity in hand -- a reservation that
    never happened. Never raises."""
    try:
        ident = store.scenes.scene_identity(cid, sid)
    except Exception as exc:  # noqa: BLE001 -- as `_fail`
        log.warning("tracker: could not mark %s failed in %s -- %s", key, cid, exc)
        return
    if ident:
        _fail(cid, ident, sid, key, error, gen)


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
    entry = {k: v for k, v in entry.items() if k not in store.tracker.records.INTERNAL}
    if entry.get("status") == "pending" and not live:
        return {**entry, "status": "failed", "error": INTERRUPTED}
    return entry


def _flags(entry: dict) -> dict:
    return {**dict.fromkeys(store.tracker.records.FLAGS, False), **(entry.get("flags") or {})}


def _mood_text(value) -> str:
    """A stored mood as the one line a cast tile shows. A layer may retype
    `visible_mood` to a list, so a list is its non-empty items joined; anything
    that is neither is no mood at all."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return ", ".join(v.strip() for v in value if isinstance(v, str) and v.strip())
    return ""


def _moods(snapshot: dict, fields: list[dict]) -> dict:
    """`{ref: visible_mood}` for the characters present at the tail, for the
    cast tiles -- always a string, whatever type the field's layers gave it.
    Nothing for a character with no mood yet, and nothing at all when the field
    is switched off for this scene."""
    if not any(f["key"] == "visible_mood" for f in store.tracker.fields.active(fields)):
        return {}
    out = {}
    for ref, ent in snapshot.items():
        text = _mood_text(((ent.get("fields") or {}).get("visible_mood") or {}).get("value"))
        if ent.get("present") and text:
            out[ref] = text
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
        if entry.get("status") == "pending" and _update_live(request.app, cid, ident):
            # An update for this key is on its way, built on what the record
            # said before this edit; its save would replace the edit without a
            # trace. Refused rather than lost -- the person edits once it lands.
            raise HTTPException(status_code=409, detail="tracker_busy")
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
        moved = {(c[0], c[1]) for c in changed}
        kept = ([c for c in entry.get("changed") or [] if (c[0], c[1]) not in moved]
                if entry.get("status") == "ok" else [])
        # Every pair the edit wrote, value or only awareness, joins the ones
        # earlier edits (or a re-run restoring them) left: what a re-run must
        # put back. `changed` cannot carry it -- it is the display's list, and
        # an awareness-only edit moves no value.
        touched = (records.pairs(entry.get(records.TOUCHED))
                   + store.tracker.merge.touched_by(prev, snapshot))
        # `keep_flags`: clearing is a re-run's job. A hand edit of one value
        # does not make the rest of the record agree with an edited post or an
        # earlier change, so whatever was raised stays. `bump_gen`: a run
        # already queued for this key read the record before this edit, and
        # must not land over it (`Mark`).
        records.save(cid, ident, key, snapshot, changed=kept + changed,
                     fields_digest=store.tracker.fields.digest(fields), model="user",
                     keep_flags=True, bump_gen=True, touched=touched)
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
    own flags; nothing needs flagging, because everything after is re-run.
    The first is started from its base record on purpose (`trust_base`): the
    person chose to re-run from here with that record's warning in view."""
    _require_scene(cid, sid)
    _require_key(key)
    _require_on(cid)
    ordered = [k for _, k in store.tracker.walk.ordered_keys(cid, sid)]
    if key not in ordered:
        raise HTTPException(status_code=404, detail="tracker record not found")
    for n, later in enumerate(ordered[ordered.index(key):]):
        schedule(request.app, cid, sid, later, client, trust_base=n == 0)
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


def after_take_back(cid: str, identity: str, key: str) -> None:
    """A failed turn took its unanswered player post `key` back off the
    transcript (`scenes._take_the_post_back`): its record goes with it.

    Whichever finished first. An update that already landed wrote a record for
    a post that is now gone -- keyed by an id nothing will show again, so it
    would sit hidden until some later cut happened to prune it. An update still
    queued or answering is made obsolete by the same discard: its mark's
    generation no longer matches an entry that is not there (`_obsolete`), so
    it neither prepares nor commits. Called inside the hold that removed the
    post, by identity because that is what the turn was fenced on. Never
    raises: the removal it follows has landed."""
    try:
        store.tracker.records.discard(cid, identity, [key])
    except Exception as exc:  # noqa: BLE001 -- see the section comment
        log.warning("tracker: could not discard %s in %s -- %s", key, cid, exc)


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
