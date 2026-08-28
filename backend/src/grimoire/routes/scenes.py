"""Scenes and the play loop: scene CRUD and suggestions, the generating
routes (chat / retry / regenerate), cast seating, scene location, datetime and
response scope, the chronicle, and the absorb/audit end-of-scene flow."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
import time
import uuid
from typing import NamedTuple

from fastapi import (
    APIRouter,
    Depends,
    File,
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from starlette.concurrency import run_in_threadpool

from .. import prompts, store
from ..llm import LLMClient, effective_model
from ..llm_errors import LLMError
from . import runs, streaming
from .common import (
    _campaign_root_or_404,
    _dump,
    _llm_http_error,
    _noting,
    _override_connection,
    _page_of,
    _page_window,
    _record_prompt,
    _require_connection,
    _require_scene,
    _response_body,
    _turn_override,
    _write_response,
    computes_only,
    draft_completion,
    get_llm,
    run_error,
)
from .models import (
    Appear,
    AppearBatch,
    ChatTurn,
    ChronicleSave,
    Dismiss,
    EditMessage,
    EmergentCast,
    NewScene,
    RegenerateBody,
    RenameScene,
    ReplayCancel,
    ReplayStart,
    ResponseSettings,
    RetryBody,
    SceneDatetime,
    SceneIdeaCreate,
    SceneIdeaStatus,
    SceneImportCommit,
    SceneIntent,
    SceneLocation,
)
from .streaming import StreamOutcome, _chat_stream

router = APIRouter()

log = logging.getLogger(__name__)


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
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")


@router.post("/campaigns/{cid}/scenes")
def post_scene(cid: str, body: NewScene, request: Request):
    title = body.title or "New scene"
    # Only when this create would WIDEN the campaign. Crossing 999 -> 1000
    # scenes repads every scene in it -- renaming them all and repointing their
    # sidecars -- so every live turn loses the path it captured, and the
    # per-scene guard cannot express that. An ordinary create touches nothing
    # but the new file, so it stays allowed while a turn generates elsewhere:
    # refusing every create during any turn would be a much larger change to
    # how the app feels, for a case that is not dangerous.
    #
    # ONE HOLD over the boundary read, the guard and the create. The three were
    # separate steps, and both gaps are real: a turn can reserve after
    # `require_campaign_free` returns, and two concurrent creates can each
    # preflight below the boundary so that the second one repads anyway. Either
    # way `_create_scene` renames the live turn's transcript and its terminal
    # fence discards the reply. The campaign lock is reentrant, so
    # `_create_scene`'s own `@_serialized` acquisition costs nothing, and
    # `reserve_turn` takes the same lock across its identity capture and its
    # reservation -- which is what makes holding it here exclude a turn rather
    # than merely narrow the window before one.
    try:
        with store.locks.campaign_lock(cid):
            if store.scenes.create_would_repad(cid):
                runs.require_campaign_free(request.app, cid)
            return {"id": store.scenes.create_scene(cid, title, body.suggested_date,
                                                    pcless=body.pcless)}
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")
    except OSError as exc:
        # The boundary read failed, so nobody knows whether this create would
        # repad -- and `create_would_repad` now says so rather than answering
        # "no". Reported as store contention, which the client retries, because
        # that is what it almost always is; going ahead would skip the guard and
        # then repad anyway on `_create_scene`'s own read, stranding a live turn
        # on a path that no longer exists.
        raise HTTPException(status_code=409, detail={
            "kind": "busy",
            "detail": f"the campaign could not be read: {exc}"}) from exc


# ---- importing an existing transcript (#92) ----
# Declared before the `{sid}` routes below and before `entities`' generic
# `/campaigns/{cid}/{kind}` catch-alls (which `routes/__init__` includes last),
# so "import" is read as the literal it is rather than as a scene id.
#
# Two routes, one flow, the same split `lorebook` makes: the first reads an
# upload into a draft and writes NOTHING, the second writes the draft the
# reviewer approved. Only the second creates anything, which is what makes the
# review step a gate rather than a confirmation dialog over work already done.
@router.post("/campaigns/{cid}/scenes/import/parse")
@computes_only
async def post_scene_import_parse(cid: str, file: UploadFile = File(...)):
    """Read a grimoire transcript into a reviewable draft. Writes nothing.

    Which is what `@computes_only` says, and it is load-bearing since #409:
    the marker keeps this off both the recents rail and the campaign's write
    token, so reviewing an import in another tab cannot refuse a clock
    confirmation over a campaign nothing has written. `POST .../scenes/import`,
    one route down, is the one that commits.
    """
    _campaign_root_or_404(cid)
    # Before the read, like every other upload route here: `read()` materializes
    # the whole body as one `bytes`, and that allocation is what the bound
    # exists for (see `scene_import.MAX_BYTES`). Starlette spools the body to
    # disk above 1 MB and fills in `size`, so this costs nothing -- and `parse`
    # re-checks the bytes, since `size` is Optional in the ASGI contract.
    if file.size is not None and file.size > store.scene_import.MAX_BYTES:
        raise HTTPException(status_code=413, detail=store.scene_import.TOO_LARGE)
    data = await file.read()
    try:
        # In a worker, not on the loop. This route is `async` only because the
        # upload has to be awaited; the parse itself is a megabyte of regex plus
        # a card read per actor in the campaign, and the lifespan loop it would
        # otherwise run on is the one every detached turn streams through.
        return await run_in_threadpool(store.scene_import.parse, cid, data)
    except store.scene_import.TranscriptTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except store.scene_import.SceneImportError as exc:
        raise HTTPException(status_code=400, detail=f"could not parse: {exc}") from exc


@router.post("/campaigns/{cid}/scenes/import")
def post_scene_import(cid: str, body: SceneImportCommit, request: Request):
    """Write a reviewed import draft as a new scene.

    Everything the draft can get wrong is settled BEFORE the create: an actor
    this campaign does not have, a role that fights the campaign's lock, a
    player seated in an offscreen scene. Those are the failures an import
    actually has -- the review step surfaces the speakers it could not match,
    and this is where a reviewer who confirmed one anyway finds out -- and
    raising them after the create would leave a stray half-scene behind for
    every one of them.

    What cannot be settled early still cleans up after itself: a date the
    calendar refuses, a location that was deleted between the parse and the
    commit, a store that goes busy mid-append all discard the scene rather than
    leave a fragment of the transcript standing (see `scene_import.commit`).
    """
    _campaign_root_or_404(cid)
    messages = [_dump(m) for m in body.messages]
    if not messages:
        raise HTTPException(status_code=400,
                            detail="an imported scene needs at least one message")
    # The parse route bounds its upload; this one is plain JSON and had no
    # bound at all. The cap is on the SCENE, not on the request: a transcript
    # of this length is already past what the app can render or absorb, and the
    # store's own guidance is to break a log into scenes. Without it a single
    # request could hold the campaign lock for minutes.
    if len(messages) > store.scene_import.MAX_MESSAGES:
        raise HTTPException(status_code=413, detail=store.scene_import.TOO_MANY)
    cast = [{"kind": ref.kind, "id": ref.id,
             "role": _resolve_role(ref.kind, ref.role, body.pcless),
             "version": _actor_version(cid, ref.kind, ref.id, ref.version)}
            for ref in body.cast]
    try:
        # The same hold, for the same reason, as `post_scene`: crossing the
        # number-width boundary repads every scene in the campaign, renaming
        # them all under any live turn. See that route.
        with store.locks.campaign_lock(cid):
            try:
                widens = store.scenes.create_would_repad(cid)
            except OSError as exc:
                # ONLY the boundary read. Spanning the create too would report a
                # full disk or a read-only store as a retryable "could not be
                # read", and the client retries `busy` forever over a condition
                # that never clears, named as the wrong operation.
                raise HTTPException(status_code=409, detail={
                    "kind": "busy",
                    "detail": f"the campaign could not be read: {exc}"}) from exc
            if widens:
                runs.require_campaign_free(request.app, cid)
            sid = store.scenes.create_scene(cid, body.title or "Imported scene",
                                            pcless=body.pcless)
    except store.campaigns.CampaignNotFound:
        # `from None`: the store's own exception says nothing the caller can act
        # on beyond the 404 -- the same reading `_campaign_root_or_404` takes.
        raise HTTPException(status_code=404, detail="campaign not found") from None
    try:
        # All or nothing: `commit` removes the scene again on any failure. It
        # has to be the one that does -- `set_datetime` renames the scene, so
        # after that step `sid` here is not the id the scene has.
        return store.scene_import.commit(cid, sid, messages, date=body.date,
                                         location=body.location, cast=cast,
                                         turn_sizes=body.turn_sizes)
    except (store.calendars.CalendarError, store.entities.EntityNotFound,
            store.appearances.AppearError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except store.scenes.SceneNotFound as exc:
        # The scene this call created was renamed or removed under it -- a
        # concurrent width-crossing repad is the reachable way. `commit` has
        # already discarded what it wrote (it follows the identity, not the
        # id), so this is retryable and says so rather than 500-ing: nothing
        # here is wrong with the request.
        raise HTTPException(status_code=409, detail={
            "kind": "busy",
            "detail": "the scene moved while it was being imported — try again"}) from exc


def _resolve_cast(cid: str, tokens: list[str], memo: dict[str, str] | None = None) -> list[dict]:
    """Cast tokens with the names to show for them.

    `memo` carries token -> name ACROSS calls, for the caller resolving a list
    of records rather than one: every miss is a markdown parse, and a campaign's
    ideas mostly cast the same handful of people over and over. Without it the
    scene ledger re-read the same character file once per row it appeared in --
    60 saved ideas casting three characters cost 180 file parses to render four
    cards. The suggestion and intent routes pass nothing and are unaffected:
    they resolve one record's cast, once.
    """
    out = []
    for tok in tokens:
        kind, _, aid = tok.partition(":")
        name = memo.get(tok) if memo is not None else None
        if name is None:
            try:
                if kind == "pcs":
                    name = store.pcs.read_pc(store.overlay.pc_root(cid, aid), aid)["meta"].get("name", aid)
                else:
                    name = store.characters.read_character(
                        store.overlay.char_root(cid, aid), aid)["meta"].get("name", aid)
            except (store.characters.CharacterNotFound, store.pcs.PCNotFound):
                name = aid
            if memo is not None:
                memo[tok] = name
        out.append({"kind": kind, "id": aid, "name": name})
    return out


@router.post("/campaigns/{cid}/scene-suggestions", status_code=202)
@computes_only
def post_scene_suggestions(cid: str, request: Request, after: str | None = None,
                           offscreen: bool = False, direction: str = "", rank: bool = True,
                           client: LLMClient = Depends(get_llm),
                           x_grimoire_attempt: str | None = Header(default=None)):
    """Start a set of drafted scene ideas. 202 and a run to poll.

    The ranking half of the prompt is built HERE, while the request is still
    there: it is the expensive read, but it is a read, and a campaign that
    cannot be read has to be refused as a 404 rather than as a run state.
    """
    try:
        store.campaigns.read_campaign(cid)
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")
    conn = _require_connection("suggestions", cid)
    # A refresh passes rank=false: re-ranking would reshuffle the greeting cards
    # under the user's cursor, and the ranking is the expensive half of the prompt.
    candidates = store.suggest.greeting_candidates(cid, after, pcless=offscreen) if rank else []
    messages = store.suggest.build_prompt(store.suggest.build_snapshot(cid, offscreen=offscreen),
                                          candidates, offscreen=offscreen, direction=direction)

    async def work():
        return await draft_completion(
            client, conn, messages, "suggestions",
            lambda text: _suggestions_payload(cid, text, candidates, offscreen),
            cid=cid)

    return runs.run_draft(request.app, runs.campaign_subject(cid), "suggestions",
                          x_grimoire_attempt, work)


def _suggestions_payload(cid: str, text: str, candidates: list[dict],
                         offscreen: bool) -> dict:
    """The model's suggestions as the body this route has always returned.

    Split from the route so the shape survives detachment unchanged: what a
    client receives is the same dict it received when this blocked, and only
    where it reads it from moved.
    """
    loc_names = {e["id"]: e.get("name", e["id"]) for e in store.overlay.list_entities(cid, "locations")}
    out = []
    for s in store.suggest.parse_output(text, cid, offscreen=offscreen):
        loc = {"id": s["location"], "name": loc_names.get(s["location"], s["location"])} if s["location"] else None
        out.append({"title": s["title"], "premise": s["premise"], "date": s["date"],
                    "cast": _resolve_cast(cid, s["cast"]), "location": loc})
    picks = (store.suggest.parse_greeting_picks(text, {c["id"] for c in candidates})
             if candidates else [])
    return {"suggestions": out, "greeting_picks": picks,
            "next_date": store.suggest.parse_next_date(text, cid)}


@router.post("/campaigns/{cid}/scene-intent", status_code=202)
@computes_only
def post_scene_intent(cid: str, body: SceneIntent, request: Request,
                      client: LLMClient = Depends(get_llm),
                      x_grimoire_attempt: str | None = Header(default=None)):
    """Metadata implied by the user's own scene-start description. Computes and
    returns; the confirm form is what decides whether any of it is written."""
    try:
        store.campaigns.read_campaign(cid)
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")
    if not body.text.strip():
        raise HTTPException(status_code=400, detail="empty scene description")
    conn = _require_connection("intent", cid)
    messages = store.suggest.build_intent_prompt(cid, body.text, offscreen=body.offscreen)

    async def work():
        return await draft_completion(
            client, conn, messages, "intent",
            lambda text: _intent_payload(cid, text, body.offscreen), cid=cid)

    return runs.run_draft(request.app, runs.campaign_subject(cid), "intent",
                          x_grimoire_attempt, work)


def _intent_payload(cid: str, text: str, offscreen: bool) -> dict:
    """The parsed intent as the body this route has always returned -- see
    `_suggestions_payload` for why it is a function rather than inline."""
    got = store.suggest.parse_intent(text, cid, offscreen=offscreen)
    loc_names = {e["id"]: e.get("name", e["id"]) for e in store.overlay.list_entities(cid, "locations")}
    loc = ({"id": got["location"], "name": loc_names.get(got["location"], got["location"])}
           if got["location"] else None)
    return {"title": got["title"], "date": got["date"], "location": loc,
            "cast": _resolve_cast(cid, got["cast"])}


# ---- the scene ledger (#88) ----
# Literal third segments, so they are registered before `entities`' generic
# `/campaigns/{cid}/{kind}` (see that module's docstring and
# tests/test_route_order.py). Named `scene-ideas` rather than `ledger`: that
# route is the continuity ledger's (`routes.campaigns.get_ledger`).
def _idea_card(cid: str, idea: dict, loc_names: dict[str, str],
               cast_names: dict[str, str]) -> dict:
    """A ledger row in the shape the picker already renders a suggestion in --
    cast resolved to names, location to an {id, name} or null -- plus the
    ledger's own fields. `validate_ideas` has already dropped every id the
    campaign no longer has, so nothing here has to guess at a dangling one.

    Both name maps are resolved once for the whole list and threaded through:
    locations as a dict the caller builds, actors as the memo `_resolve_cast`
    fills in as it goes."""
    loc = ({"id": idea["location"], "name": loc_names.get(idea["location"], idea["location"])}
           if idea["location"] else None)
    return {**idea, "cast": _resolve_cast(cid, idea["cast"], cast_names), "location": loc}


@router.get("/campaigns/{cid}/scene-ideas")
def get_scene_ideas(cid: str, greetings: bool = True):
    """The whole ledger: saved ideas (re-validated against the campaign as it
    stands now) followed by the greeting entries `playing` composes.

    Status and mode (`pcless`) filtering is deliberately absent -- the picker
    wants the active entries for its own mode, a management surface wants
    everything, and a campaign's ledger is small enough that one read serving
    both beats a query language neither needs.

    `greetings=false` is the exception, and it is about cost rather than taste.
    Composing the greeting half means `available_greetings`, which parses the
    frontmatter of every greeting in the campaign; the picker renders greetings
    from its own `/greetings/available` call (ranked, and chipped with
    `unlocked`) and drops every greeting row this route composes. Asking for
    the saved half alone is what keeps opening the chooser from paying for that
    sweep twice and using neither copy.

    Each half is read tolerantly, the failure policy `get_ledger` states for
    the continuity ledger: scene_ideas.json is hand-editable and read by a bare
    `json.loads`, so a garbled one must cost its own section rather than the
    whole view -- taking the greeting half, and the reader's ability to start a
    scene at all, down with it.
    """
    _campaign_root_or_404(cid)

    def _tolerant(read):
        try:
            return read()
        except Exception:  # noqa: BLE001 — a garbled file empties its half, not the view
            return []

    loc_names = {e["id"]: e.get("name", e["id"]) for e in store.overlay.list_entities(cid, "locations")}
    cast_names: dict[str, str] = {}
    saved = _tolerant(lambda: store.suggest.validate_ideas(cid, store.scene_ideas.records(cid)))
    # `loc_names` is the campaign's whole location list, already read a line
    # above to caption these very cards -- handing the ids over keeps
    # `greeting_ideas` from reading every location file a second time to answer
    # one request.
    composed = (_tolerant(lambda: store.playing.greeting_ideas(cid, known_locations=set(loc_names)))
                if greetings else [])
    return [_idea_card(cid, i, loc_names, cast_names) for i in saved + composed]


@router.post("/campaigns/{cid}/scene-ideas")
def post_scene_idea(cid: str, body: SceneIdeaCreate):
    """Save an idea. References are validated here as well as on every read, so
    a token this campaign never had cannot enter the file at all."""
    _campaign_root_or_404(cid)
    if not body.title.strip() and not body.premise.strip():
        raise HTTPException(status_code=400, detail="an idea needs a title or a premise")
    refs = store.suggest.valid_refs(cid, body.cast, body.location, body.date,
                                    offscreen=body.pcless)
    return {"id": store.scene_ideas.add(cid, body.title, body.premise, refs["cast"],
                                        refs["location"], refs["date"], body.pcless,
                                        body.source)}


@router.put("/campaigns/{cid}/scene-ideas/{lid}")
def put_scene_idea(cid: str, lid: str, body: SceneIdeaStatus):
    """Dismiss, restore, or record that an idea became a scene.

    A greeting entry is not in scene_ideas.json -- its lifecycle is
    `played.json`'s -- so those ids delegate to `playing.mark_greeting` rather
    than being copied here: dismissed is "skipped", used is "completed" (the
    off-screen mark), active clears the mark. A greeting actually *played* in a
    scene refuses to move, which surfaces as the same 409 `POST
    /greetings/{gid}/mark` already returns.
    """
    _campaign_root_or_404(cid)
    if lid.startswith(store.scene_ideas.GREETING_PREFIX):
        gid = lid[len(store.scene_ideas.GREETING_PREFIX):]
        mark = {store.scene_ideas.ACTIVE: "none", store.scene_ideas.USED: "completed",
                store.scene_ideas.DISMISSED: "skipped"}[body.status]
        try:
            store.playing.mark_greeting(cid, gid, mark)
        except store.greetings.GreetingNotFound:
            raise HTTPException(status_code=404, detail="greeting not found")
        except store.playing.PlayError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return {"ok": True}
    if not store.scene_ideas.set_status(cid, lid, body.status, body.scene):
        raise HTTPException(status_code=404, detail="idea not found")
    return {"ok": True}


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
        if limit is None:
            return store.scenes.read_scene(cid, sid)
        return store.scenes.read_scene_window(cid, sid, limit, before)
    except (store.scenes.SceneNotFound, store.campaigns.CampaignNotFound):
        # a scene path is built from campaign_root, so an unusable campaign id
        # surfaces here as CampaignNotFound -- still a 404, not a 500
        raise HTTPException(status_code=404, detail="scene not found")


@router.put("/campaigns/{cid}/scenes/{sid}")
def put_scene(cid: str, sid: str, body: RenameScene, request: Request):
    title = body.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="title is required")
    # A rename mints a new `sid`, and a detached turn holds the old one -- see
    # `require_scene_free`. Ahead of the title check would refuse a malformed
    # request with the wrong reason; behind the rename would be too late.
    try:
        with runs.scene_held_free(request.app, cid, sid):
            new_sid = store.scenes.rename_scene(cid, sid, title)
    except (store.scenes.SceneNotFound, store.campaigns.CampaignNotFound):
        # a scene path is built from campaign_root, so an unusable campaign id
        # surfaces here as CampaignNotFound -- still a 404, not a 500
        raise HTTPException(status_code=404, detail="scene not found")
    return {"id": new_sid, "title": title}


@router.delete("/campaigns/{cid}/scenes/{sid}")
def delete_scene(cid: str, sid: str, request: Request):
    # Same reason as the rename above, one step worse: the transcript a live
    # turn is about to write into would be gone. The identity fence keeps the
    # reply off a REPLACEMENT scene, which is the corruption; this keeps the
    # reply from being thrown away in the first place.
    try:
        with runs.scene_held_free(request.app, cid, sid):
            store.scenes.delete_scene(cid, sid)
    except (store.scenes.SceneNotFound, store.campaigns.CampaignNotFound):
        # a scene path is built from campaign_root, so an unusable campaign id
        # surfaces here as CampaignNotFound -- still a 404, not a 500
        raise HTTPException(status_code=404, detail="scene not found")
    return {"ok": True}


def _disown_dead_pending(cid: str, sid: str) -> None:
    """Re-aim a dead reroll's hint AND route before streaming something that
    sent neither.

    A reroll whose stream died leaves its pending pair parked for "whatever
    lands next" (`store.alternates`). Every other way of streaming into that
    empty slot — Retry, and the empty-send / director turn, which persist no
    player message — sent neither the hint nor the route, so the take they
    produce must not be labelled with either.

    Both halves, and named for both since #77 gave the pair a second field.
    The call below passes them both explicitly rather than leaning on
    `archive`'s defaults, which is the same visibility the `disown_guidance` ->
    `disown_pending` rename was made for: re-aiming only the hint would credit
    the take that lands with a route it was not generated on, and a signature
    default is not where a reader looks to find that out.

    Only over an *empty* slot. Above a live reply these paths append a
    consecutive generation rather than replacing one, which moves the slot and
    retires the set anyway; archiving there would park a reply nobody asked to
    replace. A normal send needs none of this: it appends a player message,
    which moves the anchor and retires the set on its own.
    """
    parked = store.alternates.state(cid, sid)
    if parked["runs"] and parked["active"] is None:
        # Both fields said out loud rather than left to the signature's
        # defaults, so the next reader can see that the route is re-aimed too.
        store.alternates.archive(cid, sid, guidance="", model="")


@router.post("/campaigns/{cid}/scenes/{sid}/chat")
def post_chat(cid: str, sid: str, turn: ChatTurn, request: Request,
              client: LLMClient = Depends(get_llm),
              x_grimoire_attempt: str | None = Header(default=None)):
    # BEFORE the preflight checks, and that order is the whole point of an
    # attempt id. This is a REPLAY: the work already ran and its outcome is
    # buffered, so a client that lost the original response must get that
    # outcome back. Behind `_require_connection` it would not -- a connection
    # removed or re-keyed in the meantime answers `missing_key`, telling the
    # player a turn that actually landed did not, which is the exact ambiguity
    # the attempt id exists to remove. No provider is needed to replay.
    replay = runs.replay_attempt(request.app, cid, sid, x_grimoire_attempt)
    if replay is not None:
        return replay
    _require_scene(cid, sid)
    conn = _require_connection("chat", cid)
    # RESERVED BEFORE THE FIRST MUTATOR. `heal` can append a line and the
    # sidecar block can retire a proposal, so a 409 raised after them would
    # tell the player nothing happened when something already had. The
    # reservation is also what makes a second send on this scene refusable
    # rather than merely discouraged.
    run, fresh = runs.reserve_turn(request.app, cid, sid, "chat",
                                   x_grimoire_attempt)
    if not fresh:
        # This exact POST already ran -- a duplicate delivery after a lost
        # response. Replay it rather than doing the work twice.
        return runs.tail_response(run, 0, lead=runs.lead_frame(run))
    with runs.reservation(request.app, run):
        return _chat_run(cid, sid, turn, request, client, conn, run)


def _take_the_post_back(cid: str, sid: str, posted_at, content: str, run) -> bool:
    """Remove the player's post AND retire the record that says it is there.

    One function because the two have to be one step. `on_error` calls this
    inside its campaign-lock hold, so both land together; split apart -- the
    removal here and the record cleared on the way out -- there is a window
    where the transcript has lost the post and the record still claims it, and
    a recovery landing in that window settles and discards the only surviving
    copy of what the player typed.

    **The record is retired FIRST, and the order is the fail-safe.** These are
    two files, so the lock makes them one step for a concurrent reader but not
    for a process that exits between them. Whichever way round, one crash
    window is unavoidable; the choice is which side of it the player lands on:

    - forget, then remove: the marker says "gone" while the post is still
      there. Recovery believes it, hands the words back, and the player sees a
      DUPLICATE they can read and delete.
    - remove, then forget: the post is gone while the marker still says
      "retained". Recovery believes that, settles the attempt, and the only
      copy of what the player typed is discarded with no trace at all.

    The same asymmetry every ambiguity in this feature is resolved by, and the
    reason a `forget` that raises must not be swallowed either -- letting it
    out leaves the post in place, which is the recoverable side.
    """
    store.attempts.forget(cid, run.scene_identity, run.attempt_id)
    return store.scenes.remove_trailing_user_post(cid, sid, posted_at, content)


def _chat_run(cid: str, sid: str, turn: ChatTurn, request: Request,
              client: LLMClient, conn: dict, run):
    """The body of a send, once the scene is reserved.

    Split out so `runs.reservation` can wrap every exit from it. The run is
    published and discoverable from the moment it is reserved, so a path that
    returns or raises in here without handing a producer to the runner leaves
    it `running` for the life of the process -- never reaped, and refusing
    every later turn on the scene. There are more such paths than there are
    `return` statements (any mutator below can raise), which is why this is a
    wrapper rather than a checklist.
    """
    # ephemeral turn, never stored: a director note steering one generation --
    # asked for outright (`director`, the composer's Direct mode), implied by an
    # offscreen scene, which has no other kind of turn, or implied — in any
    # scene — by an empty send meaning "next NPC round".
    ephemeral = (turn.director or store.scenes.is_pcless(cid, sid)
                 or not turn.content.strip())
    # Heal, then the sidecar, then retire — the same split regenerate makes, and
    # for the same reason. `_disown_dead_pending` writes a file that can refuse
    # the write, and it used to run AFTER the retirement: an empty send over an
    # unwritable sidecar then retired a recoverable decision and returned 500
    # having generated nothing. Heal still leads, because it is what can append
    # a 🎲 line, and the sidecar has to resolve against the transcript that
    # leaves behind. One lock, so a resolution cannot land between the three.
    #
    # The player's post joined them under that SAME acquisition when review
    # caught what the terminal identity fence does not cover. `reserve_turn`
    # captures the scene's identity and then returns; every mutator below used
    # to run outside any fence, so a scene deleted and replaced in that window
    # got this turn's `heal`, its retired proposal and its player post -- and
    # the fence, which only guards the terminal WRITE, would then refuse the
    # reply and leave the replacement holding somebody else's post with no
    # answer coming. Setup is now one locked, fenced unit: either all of it
    # lands on the scene the run captured, or none of it does.
    with store.locks.campaign_lock(cid):
        if streaming._scene_moved(cid, sid, run.scene_identity):
            raise HTTPException(status_code=404, detail="scene not found")
        store.proposals.heal(cid, sid)
        if ephemeral:
            _disown_dead_pending(cid, sid)
        store.proposals.supersede(cid, sid)  # a new send retires any pending decision
        posted_at, content, speaker = None, "", None
        if not ephemeral:
            names = store.appearances.player_names(cid, sid)
            speaker = names[0] if len(names) == 1 else None
            if speaker:
                store.scenes.stamp_user_speaker(cid, sid, speaker)
            # Macros resolved once at persist time (#137): a player's
            # {{roll:1d20}} must not re-roll on every later context build
            # (retry, next turn, ...).
            content = store.context.expand_macros(
                turn.content, store.context.scene_substitutions(cid, sid), cid, sid)
            posted_at = store.scenes.append_message(
                cid, sid, "user", content, speaker=speaker)
            # In the SAME hold as the append it describes. This is what lets a
            # recovery after the run record expired ask the only decisive
            # question -- is this attempt's post still here? -- rather than
            # matching text, which is not an identifier.
            #
            # Swallowed, and only here. `attempts` refuses to rewrite a file it
            # could not read (see `_read`), which on the ROLLBACK path is what
            # keeps a post from being deleted against a marker still saying it
            # is there. On this path the same refusal would fail an otherwise
            # good send over a bookkeeping file, and the cost of the missing
            # marker is a recovery that hands back words already in the
            # transcript -- a duplicate, the recoverable side again.
            with contextlib.suppress(OSError):
                store.attempts.remember(cid, run.scene_identity, run.attempt_id)
    if ephemeral:
        note = turn.content.strip() or prompts.render("scene/director_note.j2")
        messages, breakdown = store.context.compose_director_turn(
            cid, sid, note, turn=_turn_override(turn),
            describe=store.prompt_log.capturing())
        # AFTER the stream is constructed, not before. `_chat_stream` claims the
        # turn under the campaign lock synchronously, before it returns -- so a
        # contended campaign raises StoreBusy there and nothing is ever sent.
        # Recording first would leave Turn history showing a request the model
        # never saw. The generator body has not run at this point; only the
        # claim has.
        outcome = StreamOutcome()
        stream = _chat_stream(cid, sid, messages, conn, client, task="director",
                              identity=run.scene_identity, outcome=outcome,
                              after_turn=_follow_up_hook(request.app, cid, sid, client))
        _record_prompt(cid, sid, "director", breakdown)
        # DETACHED, like the ordinary send below. This branch used to return the
        # response directly, which left its reservation running forever -- the
        # scene answered `run_in_flight` from then on -- while the generation
        # itself still died with the socket. It persists a reply like any other
        # turn; only the player's note is ephemeral.
        runs.start_detached(request.app, run, lambda: stream.body_iterator,
                            outcome=outcome.result)
        return runs.tail_response(run, 0, lead=runs.lead_frame(run))
    messages, breakdown = store.context.compose_turn(
        cid, sid, turn=_turn_override(turn), describe=store.prompt_log.capturing())

    # The post has to precede the stream — `build_messages` renders history out
    # of the transcript, so a turn the model never sees is a turn it cannot
    # answer — which is exactly what makes a failed generation able to strand
    # it. Hand `_chat_stream` the undo so the pair is transactional (#95): if
    # the turn produces nothing at all, the post comes back off. `posted_at`
    # travels with it because nothing holds a lock across the stream, so by the
    # time the undo runs the tail may belong to a different turn entirely.
    # The producer is unchanged; what changed is who consumes it. `runner`
    # drives it on the lifespan loop and buffers every frame on the run, so the
    # work outlives this request -- a locked phone or a backgrounded tab drops
    # a subscriber, not the turn. The response tails that same buffer.
    #
    # Called HERE, synchronously, and only its iteration is deferred.
    # `_chat_stream` claims the turn under the campaign lock before it returns,
    # so a contended campaign raises `StoreBusy` at this line and the route
    # answers 409 having sent nothing -- exactly as it did before. Building it
    # inside the runner's factory instead moved that claim off the request, and
    # a contended turn started answering 200 and failing later, which
    # `test_a_turn_that_never_claims_records_nothing` caught.
    outcome = StreamOutcome()
    stream = _chat_stream(
        cid, sid, messages, conn, client,
        undo_user_post=lambda: _take_the_post_back(cid, sid, posted_at, content, run),
        task="chat", identity=run.scene_identity, outcome=outcome,
        after_turn=_follow_up_hook(request.app, cid, sid, client))
    # AFTER the stream is built, never before: `_chat_stream` claims the turn
    # and can raise, and a prompt recorded ahead of it leaves Turn history
    # showing a request the model never saw (`test_a_turn_that_never_claims_
    # records_nothing`).
    _record_prompt(cid, sid, "chat", breakdown)
    runs.start_detached(request.app, run, lambda: stream.body_iterator,
                        outcome=outcome.result)
    return runs.tail_response(run, 0, lead=runs.lead_frame(run))


@router.post("/campaigns/{cid}/scenes/{sid}/retry")
def post_retry(cid: str, sid: str, request: Request, body: RetryBody | None = None,
               client: LLMClient = Depends(get_llm),
               x_grimoire_attempt: str | None = Header(default=None)):
    replay = runs.replay_attempt(request.app, cid, sid, x_grimoire_attempt)
    if replay is not None:
        return replay
    scene = _require_scene(cid, sid)
    conn = _require_connection("retry", cid)
    # Ahead of the retirement, not behind it: a refusal must not cost a decision
    # for a request that then does nothing at all. Ahead of the RESERVATION too:
    # a run reserved for a request that was never going to do anything has to be
    # released again, and not taking it is simpler than unwinding it.
    if not scene["messages"]:
        raise HTTPException(status_code=400, detail="nothing to retry")
    run, fresh = runs.reserve_turn(request.app, cid, sid, "retry", x_grimoire_attempt)
    if not fresh:
        return runs.tail_response(run, 0, lead=runs.lead_frame(run))
    with runs.reservation(request.app, run):
        return _retry_run(cid, sid, body, request, client, conn, run)


def _retry_run(cid: str, sid: str, body, request: Request,
               client: LLMClient, conn: dict, run):
    """The body of a retry, once the scene is reserved -- see `_chat_run` for
    why every exit from it has to be wrapped rather than audited."""
    # Same order as the send above, for the same reason — see there. Fenced like
    # it too: a scene replaced between the reservation and here must not collect
    # this turn's heal and retired proposal.
    with store.locks.campaign_lock(cid):
        if streaming._scene_moved(cid, sid, run.scene_identity):
            raise HTTPException(status_code=404, detail="scene not found")
        store.proposals.heal(cid, sid)
        _disown_dead_pending(cid, sid)
        store.proposals.supersede(cid, sid)  # a fresh generation retires the old decision
    messages, breakdown = store.context.compose_turn(
        cid, sid, turn=_turn_override(body), describe=store.prompt_log.capturing())
    outcome = StreamOutcome()
    stream = _chat_stream(cid, sid, messages, conn, client,   # claims the turn; see above
                          task="retry", identity=run.scene_identity, outcome=outcome,
                          after_turn=_follow_up_hook(request.app, cid, sid, client))
    _record_prompt(cid, sid, "retry", breakdown)
    runs.start_detached(request.app, run, lambda: stream.body_iterator,
                        outcome=outcome.result)
    return runs.tail_response(run, 0, lead=runs.lead_frame(run))


def _restore_reroll(cid: str, sid: str, removed: dict):
    """Put the outgoing reply back, and take the hint back with it.

    Two halves of one undo. The transcript half is #95's: a reroll that produces
    nothing must not cost the reply it deleted. The sidecar half is this PR's —
    `archive` recorded the guidance against a replacement that never landed, and
    with the original run live again `_resolve` would credit it with an
    instruction it was not generated from, then persist that on the next write.

    Best-effort on the second, deliberately: the reply is the artifact that
    cannot be regenerated, and a wrong label is not worth failing a restore for.
    """
    def restore() -> None:
        store.scenes.restore_trailing_assistant_run(cid, sid, removed)
        try:
            store.alternates.disown_pending(cid, sid)
        except OSError:
            pass
    return restore


def _put_back(cid: str, sid: str, showing: str | None) -> bool:
    """Promote the take that was in the transcript when a swap began.

    Addressed by content, like the request itself: retention shifts indices, so
    the position it sat at is not the position it sits at now. Reports whether
    the transcript really is showing it again, because the callers differ in
    what they do when it is not.

    Best-effort. Every failure it swallows is a second failure on top of the one
    being repaired, and that first one is the one worth reporting. Returns False
    for an empty slot too: `promote` can fill one but not empty one, and a
    decision derived from a reply a dead reroll already removed is not on screen
    whatever happens next.
    """
    if showing is None:
        return False
    try:
        back = store.alternates.state(cid, sid)
        at = next((i for i, r in enumerate(back["runs"])
                   if store.alternates.variant_id(r) == showing), None)
        if at is None:
            return False
        store.alternates.promote(cid, sid, at)
        return True
    except (OSError, store.scenes.TurnSizesDesynced,
            store.alternates.AlternateNotFound):
        return False


@router.post("/campaigns/{cid}/scenes/{sid}/regenerate")
def post_regenerate(cid: str, sid: str, request: Request,
                    body: RegenerateBody | None = None,
                    client: LLMClient = Depends(get_llm),
                    x_grimoire_attempt: str | None = Header(default=None)):
    """Redo the most recent post: park the trailing assistant reply as an
    alternate, stream a fresh one."""
    replay = runs.replay_attempt(request.app, cid, sid, x_grimoire_attempt)
    if replay is not None:
        return replay
    _require_scene(cid, sid)
    # The one-shot route override (#77), resolved here rather than inside
    # `_regenerate_run`: a body naming a connection that does not exist, or one
    # with no key, must refuse BEFORE the reservation below — past it the route
    # has archived and removed the outgoing reply, and a 400 raised there would
    # report a rejected request over a scene that is one reply short.
    # `conn` is always the resolved connection and `routed` says whether it is
    # somewhere this turn would not have gone anyway — two answers, because the
    # stamps below need the second and the generation needs the first, and a
    # single sentinel could not carry both without the caller re-resolving.
    # The task literal lives here, at the call site, because this is where a
    # generation knows what it is: a reroll of a scene reply routes exactly
    # where the reply it replaces would have (#142).
    conn, routed = _override_connection(body, "regenerate", cid)
    # RESERVED BEFORE THE FIRST MUTATOR, which matters more here than anywhere
    # else: this route archives the outgoing reply and removes it from the
    # transcript before the replacement exists, so a 409 raised afterwards
    # would report "nothing happened" over a scene that is one reply short.
    run, fresh = runs.reserve_turn(request.app, cid, sid, "regenerate",
                                   x_grimoire_attempt)
    if not fresh:
        return runs.tail_response(run, 0, lead=runs.lead_frame(run))
    with runs.reservation(request.app, run):
        return _regenerate_run(cid, sid, body, request, client, conn, run, routed=routed)


def _regenerate_run(cid: str, sid: str, body, request: Request,
                    client: LLMClient, conn: dict, run, *, routed: bool):
    """The body of a reroll, once the scene is reserved -- see `_chat_run`."""
    guidance = (body.guidance or "").strip() if body else ""
    # What this reroll will actually be sent to, after `_override_connection`
    # has folded in whatever the body asked for. Read off `conn` rather than off
    # the body, so it names the resolved route in every case -- an override
    # naming only a connection reports THAT connection's model, and a Claude
    # connection with none configured reports the one the dispatcher
    # substitutes rather than the empty string it stores.
    ran_on = effective_model(conn)
    # `routed` is whether this turn ran somewhere it would NOT have gone
    # anyway -- not merely whether the caller typed something. A body naming
    # the active connection at its own model is not an override, and review
    # caught the earlier reading of this flag turning such a request into one
    # that displaced the snapshot stamp below. Decided once, by
    # `_override_connection`, which compares the resolved route against the
    # standing one on the EFFECTIVE model; re-deriving it here off `body` would
    # be a second place for that rule to drift. The two stamps below answer to
    # it differently -- and deliberately, because they answer different
    # questions:
    #
    # - The ALTERNATE is stamped on every reroll. It is the only record a
    #   variant has of the generation that produced it, and the whole point of
    #   the set is reading variants side by side; "which model wrote this one"
    #   is answerable nowhere else. Always stamping also catches the case an
    #   override-only rule would miss, since the scene's `model` frontmatter is
    #   written once at creation and goes stale the moment the active
    #   connection is repointed. "" is then left meaning "no record" -- every
    #   pre-#77 variant, and every variant reconciled out of the transcript.
    # - The SNAPSHOT is stamped only when the route was overridden, because its
    #   `model` is a field the LIVE context panel reports for the same scene
    #   too, and `store.prompt_log` argues at length that the two agreeing
    #   matters more than either being exactly right. A deliberate one-shot
    #   override is the one case where they are describing different things
    #   (this turn, versus the next one) rather than drifting apart.
    removed: dict | None = None   # set only when there is actually a reply to drop
    # ONE lock across the heal, the decision, the archive and the removal. A gap
    # anywhere in that span is a gap another writer's generation can land in —
    # and the removal would then take a reply the archive never saw, losing
    # exactly what the non-destructive guarantee promises to keep. Held only
    # across a read and two file writes; the stream starts after it is released.
    with store.locks.campaign_lock(cid):
        # Fenced first, like every other setup block: a scene replaced between
        # the reservation and here must not have its reply archived and removed
        # by a turn that started on a different scene entirely.
        if streaming._scene_moved(cid, sid, run.scene_identity):
            raise HTTPException(status_code=404, detail="scene not found")
        # Heal now, retire later. Healing is what can append a 🎲 line, and the
        # checks below have to judge the transcript that leaves behind — but
        # RETIRING the decision waits until the reroll has actually committed to
        # happening, or a failure cancels a decision whose narration is still
        # exactly what the reader sees.  `heal` is idempotent and `supersede`
        # calls it again itself.
        #
        # INSIDE the lock, not before it: `heal` is a no-op on a proposal that
        # is still `resolving`, and the resolution needs this same lock to
        # persist. Healing outside it let that resolution land in the gap, so
        # the read below saw no roll line, the guards passed, and the line only
        # appeared when `supersede` healed it — after the narration that
        # produced the roll had already been removed. Reentrant, so `heal`
        # taking it again is free.
        store.proposals.heal(cid, sid)
        # Read AFTER the heal, which can append a 🎲 line the pre-heal snapshot
        # doesn't have. Judging the checks below on a stale snapshot let the
        # ROLL_SPEAKER guard pass and `remove_trailing_assistant_run` then
        # refuse (IndexError -> 500) — it never deletes a roll line, but the
        # caller deserves the 400 instead.
        msgs = _require_scene(cid, sid)["messages"]
        if not msgs:
            raise HTTPException(status_code=400, detail="nothing to regenerate")
        # Trailing scene transitions are stepped over, not consumed: reroll
        # targets the last generation BENEATH them, and every check below has to
        # look at that generation rather than at the transition line on top.
        core = msgs[:len(msgs) - store.scenes.trailing_transitions(msgs)]
        # An archived set whose slot is EMPTY means the previous reroll's stream
        # died before its replacement landed. Generations can sit back to back
        # (an empty send and a director turn persist no player message), so what
        # is exposed at the tail is then the generation BEFORE that slot —
        # removing it would delete a reply nobody asked to reroll and carry the
        # slot past the parked one, losing both. Stream into the empty slot.
        parked = store.alternates.state(cid, sid)
        replacing = (bool(core) and core[-1]["role"] == "assistant"
                     and not (parked["runs"] and parked["active"] is None))
        if replacing:
            if all(m["role"] == "assistant" for m in core):
                raise HTTPException(status_code=400,
                                    detail="cannot regenerate the opening post")
            if core[-1].get("speaker") in store.scenes.SYNTHETIC_SPEAKERS:
                # Enumerated from the same tuple `remove_trailing_assistant_run`
                # refuses on, not from one speaker name: only ROLL_SPEAKER is
                # reachable here (trailing transitions are already stripped
                # above), but a future synthetic speaker gets this 400 rather
                # than the bare IndexError (500) that refusal would surface.
                raise HTTPException(status_code=400,
                                    detail="cannot regenerate past a manual dice roll")
        # Archive BEFORE the removal, and let the replacement join the set on
        # its own when the next read reconciles: no callback has to fire deep
        # inside the stream's persist path, and a stream that dies between the
        # two leaves the outgoing reply recoverable rather than gone.
        #
        # Called even with nothing to remove. There is no run to keep then, but
        # the hint still has to be re-aimed at whatever lands next, or the
        # variant that does land is filed under the *previous* attempt's
        # guidance. `archive` writes nothing at all for a scene that has no set,
        # so the refusals above still leave the scene untouched.
        store.alternates.archive(cid, sid, guidance, ran_on)
        if replacing:
            try:
                removed = store.scenes.remove_trailing_assistant_run(cid, sid)
            except Exception as exc:
                # The archive above recorded the hint against a replacement that
                # is now never going to land, and the reply it was meant to
                # replace is still live. Left there, the next resolution credits
                # that unchanged reply to an instruction it never received.
                #
                # Best-effort: whatever stopped the removal (a full disk, most
                # likely) may stop this too, and the original failure is the one
                # worth reporting.
                try:
                    store.alternates.disown_pending(cid, sid)
                except OSError:
                    pass    # the disk that stopped the removal can stop this too
                if isinstance(exc, store.scenes.TurnSizesDesynced):
                    # Refusing beats guessing: the recorded turn boundaries don't
                    # fit the transcript, so any deletion could take blocks from
                    # an earlier generation. The transcript is untouched.
                    raise HTTPException(
                        status_code=400,
                        detail="this scene's recorded turn boundaries no longer match its "
                               "transcript — delete the last reply manually to regenerate") from exc
                raise
        # Built BEFORE the retirement below, not after: `supersede` writes
        # proposals.json and can fail, and until this exists there is nothing
        # holding the way back — the reply would be gone with the decision it
        # was derived from still pending and still acceptable.
        restore = _restore_reroll(cid, sid, removed) if removed else None
    # Everything from here to the `return` runs with the scene one reply short,
    # and until the stream exists there is nothing holding the way back: the
    # restore hooks live inside `_chat_stream`'s generator, so a raise here
    # would delete a reply and hand the caller a 500 with no trace of it.
    # Reachable without any race — `build_messages` reads the whole store, and
    # `prompts.render` compiles a template (review, #95).
    try:
        # rendered before the context build so its tokens can be reserved against
        # the context budget -- it is appended unconditionally, so the packer must
        # not fit the prompt to a ceiling this then pushes it over. Named in
        # `appended` rather than reserved-then-appended, so the snapshot reports
        # the guidance the model actually read (#157).
        block = prompts.render("scene/regenerate_guidance.j2", guidance=guidance) if guidance else ""
        messages, breakdown = store.context.compose_turn(
            cid, sid, turn=_turn_override(body),
            appended=(("Regenerate guidance", "system", block),) if block else (),
            describe=store.prompt_log.capturing())
    except BaseException:
        if restore is not None:
            restore()
        raise
    # LAST, because it is the one step with no way back. Everything that can
    # refuse has now refused — the guards, the removal, and the setup above,
    # which reads the whole store and compiles a template and so can fail on its
    # own — and the stream is the next statement. Retiring any earlier cancelled
    # a decision whose narration the restore then put straight back, still valid
    # and no longer resolvable.
    #
    # Outside the lock, unlike the archive and the removal: `supersede` takes it
    # itself, and holding it across the context build would stretch a span
    # documented as a read and two writes over the slowest part of the request.
    try:
        store.proposals.supersede(cid, sid)
    except BaseException:
        if restore is not None:
            restore()
        raise
    # AFTER the supersede, not beside the compose that built it. Everything above
    # can still refuse and unwind the reroll without the model ever being called,
    # and a snapshot written before that point would leave Turn history showing a
    # regeneration that never happened. From here the stream is the next
    # statement, so a recorded turn is one that was really attempted. `record`
    # cannot raise, so this adds no failure of its own to a path that has just
    # passed its last one.
    # The old reply had to go before the context was built — the builders read
    # the transcript, so the model cannot be asked to replace something it can
    # still see. That leaves a window where the scene is one reply short and the
    # replacement does not exist yet, and a generation producing nothing would
    # end it there: a reply destroyed by a reroll the player stopped or that
    # never started (#95). Hand the stream the way back.
    #
    # The parked alternate is the other half of that guarantee and survives
    # either way: `archive` ran before the removal, so a stream that never
    # lands leaves the reply recoverable through the swipe control even if the
    # restore hook never fires.
    #
    # One window this does NOT close, deliberately, because closing it is a
    # redesign rather than a guard: between this return and the generator's
    # first step, the response body can be cancelled outright — uvicorn reports
    # ASGI spec 2.3, so Starlette races `stream_response` against
    # `listen_for_disconnect` in a task group, and an already-queued disconnect
    # cancels the former before it runs. An async generator that never started
    # runs none of its body on close, so no hook here can fire. The fix is to
    # stop deleting ahead of the replacement at all — remove the old run inside
    # `finalize`, under the same lock that writes the new reply — which is
    # tracked with the other transcript-identity work rather than bolted on.
    outcome = StreamOutcome()
    stream = _chat_stream(cid, sid, messages, conn, client, restore_removed=restore,
                          identity=run.scene_identity, outcome=outcome,
                          task="regenerate",
                          after_turn=_follow_up_hook(request.app, cid, sid, client))
    # Last of all: after `supersede` (which can refuse and unwind the reroll) AND
    # after the turn claim inside `_chat_stream` (which can raise StoreBusy on a
    # contended campaign). Both would leave Turn history showing a regeneration
    # the model never saw.
    # `ran_on` for a routed reroll, and None -- "use the scene's own stamp" --
    # otherwise (see `routed` above). None rather than "", because "" is a real
    # answer here: a custom endpoint with no model configured generates on the
    # provider's default, and `_record_prompt` must not read that as "nothing
    # to say" and fall back to the campaign's model.
    #
    # `SceneInspector` reads the recorded model back to look up the context
    # size it measures a snapshot against, so a reroll sent to a 32k local
    # endpoint and filed under the campaign's 200k model is a percentage bar
    # that reads comfortable for a prompt that did not fit.
    _record_prompt(cid, sid, "regenerate", breakdown, model=ran_on if routed else None)
    # `on_unstarted` is `restore` again, for the one path the stream's own hooks
    # cannot cover: a Stop that arrived while this route was still in the
    # synchronous setup above. The runner honours it with a checkpoint BEFORE
    # entering the producer, so the generator's `finally` -- where `restore`
    # otherwise lives -- never runs, and review caught what that leaves behind:
    # a reroll reported `cancelled` with the old reply removed and no
    # replacement, permanently one reply short.
    runs.start_detached(request.app, run, lambda: stream.body_iterator,
                        outcome=outcome.result, on_unstarted=restore)
    return runs.tail_response(run, 0, lead=runs.lead_frame(run))


_PREVIEW_CHARS = 200


def _alternate(run: dict) -> dict:
    """One variant as the transcript would read it — the posts joined the way
    they render, clipped. The full text is never sent: the client's job is to
    pick a variant, and the one it picks arrives as transcript."""
    text = "\n\n".join(s["content"] for s in run["segments"])
    return {"id": store.alternates.variant_id(run),
            "created": run.get("created", ""), "guidance": run.get("guidance", ""),
            # "" for a variant with no record of its route — every one that
            # predates #77, and every one reconciled out of the transcript
            # rather than archived. The client reads that as "the scene's
            # model", which is what it was before an override could exist.
            "model": run.get("model", ""),
            "posts": len(run["segments"]),
            "preview": text[:_PREVIEW_CHARS] + ("…" if len(text) > _PREVIEW_CHARS else "")}


@router.get("/campaigns/{cid}/scenes/{sid}/alternates")
def get_scene_alternates(cid: str, sid: str):
    """Every variant of the generation a reroll would replace. `active` is the
    one in the transcript, and null means the slot is empty — what a reroll
    whose stream died leaves behind."""
    _require_scene(cid, sid)
    try:
        # The check above guards only itself. Resolving the set makes several
        # more reads, and the scene can go between them — see the swap below for
        # what actually reaches this, which is the sidecar outliving its
        # transcript rather than an ordinary rename or delete. A read for a
        # scene that is not there is a 404 however late it finds out.
        state = store.alternates.state(cid, sid)
    except (store.scenes.SceneNotFound, store.campaigns.CampaignNotFound):
        raise HTTPException(status_code=404, detail="scene not found")
    return {"active": state["active"],
            "alternates": [_alternate(r) for r in state["runs"]]}


@router.post("/campaigns/{cid}/scenes/{sid}/alternates/{vid}")
def post_scene_alternate(cid: str, sid: str, vid: str, request: Request):
    """Cycle (or pin) a stored variant into the transcript, parking the live one.

    Addressed by `variant_id`, not by position: retention shifts every index
    when a full set gains a variant, and a client snapshot from before that
    shift would otherwise name a different take than the one it previewed.
    """
    # One lock over the whole check-retire-swap span, so nothing lands between
    # the three and the request is all-or-nothing.
    with store.locks.campaign_lock(cid):
        # The existence check belongs INSIDE the lock. Outside it, a rename or
        # delete could land while this request waited, and the reads below would
        # then fault on a scene that was there when they were authorized. Every
        # `store.scenes` mutator runs under this same lock (`@_serialized`), so
        # from here on the scene cannot go anywhere.
        #
        # A plain race with either already answered 404, because both carry the
        # sidecar with the transcript and the resolve then finds no set. What
        # this catches is the sidecar OUTLIVING its transcript — the stranded
        # source `repoint_scenes` leaves behind on purpose, and the over-long
        # sidecar name `delete_scene` is allowed to fail to unlink.
        _require_scene(cid, sid)
        # A promotion rewrites the post a live turn is about to append after,
        # and parks the current one as a variant. Inside the hold that already
        # covers the swap, so nothing reserves a turn between the two.
        runs.require_scene_free(request.app, cid, sid)
        # Resolve FIRST. The id comes from a client snapshot that another tab
        # may have moved on from, and retiring a decision is not something a
        # request that then 404s gets to do: a stale click would cancel a
        # proposal belonging to a turn it never swaps past. Position is safe
        # from here on — `promote` re-resolves under this same lock.
        state = store.alternates.state(cid, sid)
        index = next((i for i, r in enumerate(state["runs"])
                      if store.alternates.variant_id(r) == vid), None)
        if index is None:
            raise HTTPException(status_code=404, detail="alternate not found")
        if state["active"] == index:
            # Already showing. `promote` would return without touching the
            # transcript, so retiring anything below would be a side effect of a
            # request that changed nothing — and a delayed click for a variant
            # another tab has since promoted would cancel that tab's proposal.
            return {"ok": True}
        # What is on screen right now, addressed by content rather than by
        # position — the same rule the request itself follows, and what the
        # rollback below promotes if the retirement cannot be written.
        showing = (store.alternates.variant_id(state["runs"][state["active"]])
                   if state["active"] is not None else None)
        # Heal now, retire after. Healing is what can append a 🎲 line, and
        # `promote` has to reconcile against the transcript that leaves behind —
        # but a swap that fails must not take the decision with it. The sidecar
        # preflight is not enough on its own: `promote` writes the TRANSCRIPT
        # too, and that write can fail after the sidecar's has succeeded,
        # leaving the reader looking at the exact narration the proposal was
        # derived from with no way to resolve it. Same split as regenerate.
        store.proposals.heal(cid, sid)
        try:
            store.alternates.promote(cid, sid, index)
        except store.alternates.AlternateNotFound:
            # Reachable despite the check above only if that heal appended a
            # roll line and moved the slot -- in which case the transcript now
            # ends on a roll and there is nothing to swap.
            raise HTTPException(status_code=404, detail="alternate not found")
        except store.scenes.TurnSizesDesynced:
            # Same refusal regenerate makes, for the same reason: boundaries that
            # do not fit the transcript cannot authorize replacing a run.
            raise HTTPException(
                status_code=400,
                detail="this scene's recorded turn boundaries no longer match its "
                       "transcript — delete the last reply manually to swap alternates")
        except BaseException:
            # `promote` is TWO transcript writes — drop the live run, append the
            # chosen one — and failing between them is a third window: the slot
            # is empty, so the pending decision's narration is not on screen at
            # all, and neither refusal above applies because a write really did
            # land. Both repairs are correct here; they just differ in what the
            # reader ends up looking at.
            #
            # Only when there was a live run to lose, though. Filling an EMPTY
            # slot appends and removes nothing, so a failed append leaves the
            # transcript exactly as it was: the decision's narration never
            # moved, and there is nothing to repair. Retiring it there would
            # cancel a still-valid roll for a swap that did not happen — which
            # is the state a reroll that emitted a fence and no narration
            # deliberately leaves recoverable.
            if showing is not None and not _put_back(cid, sid, showing):
                # The put-back is preferred: it restores the state the request
                # started from, decision and narration together. Failing that,
                # the decision has to go, because what it was derived from is
                # gone — an empty slot the ‹/› control can refill, beside a
                # proposal that would otherwise still be acceptable.
                try:
                    store.proposals.supersede(cid, sid)
                except (OSError, store.scenes.TurnSizesDesynced):
                    pass    # the original failure is the one worth reporting
            raise
        # The transcript is now showing a different take, so the decision the
        # old narration produced is retired — and only now that it really is.
        # Accepting a proposal whose text is no longer on screen would continue
        # a mechanical decision nothing there asked for.
        #
        # Which is exactly why a failure HERE cannot simply be reported: the
        # swap has landed, so the reader would be shown new narration beside a
        # still-actionable decision the old narration produced. The transcript
        # and proposals.json cannot be written as one, so whichever goes second
        # leaves a window; this is the one that is worse to leave open, and the
        # only one with a compensating action available.
        try:
            store.proposals.supersede(cid, sid)
        except BaseException:
            # Put the take that was showing back. A different file failed
            # (proposals.json), so the write that just succeeded is likely to
            # succeed again — unlike the reroll restore, where the same disk
            # stopped both.
            #
            # No second repair here, unlike the partial-promotion path above:
            # the fallback there is to retire the decision, and retiring it is
            # precisely what has just failed.
            if showing is not None:
                _put_back(cid, sid, showing)
            else:
                # The slot was EMPTY and `promote` has just filled it, so
                # putting it back means emptying it again — there is no take to
                # promote, and `_put_back` correctly reports nothing to do.
                # Without this the reader is left with the archived take on
                # screen beside a decision that was produced with the slot
                # empty: the same mismatch the branch above exists to prevent,
                # reached from the state a reroll that emitted a fence and no
                # narration leaves behind.
                #
                # Safe to aim at the tail: the lock has been held since before
                # `promote`, so the trailing run is the one it just appended.
                try:
                    store.scenes.remove_trailing_assistant_run(cid, sid)
                except (OSError, store.scenes.TurnSizesDesynced, IndexError):
                    pass    # the original failure is the one worth reporting
            raise
    return {"ok": True}


#: How many chronicle records this route returns when the caller names no
#: `limit`. The window has always been the newest `CHRONICLE_PAGE`, not the
#: whole file: the panel that reads it renders a recap, and a campaign accretes
#: one record per absorbed scene forever. `limit` is what an explicit caller
#: raises (or lowers); `offset` is how it reaches what this default cuts off.
CHRONICLE_PAGE = 50


@router.get("/campaigns/{cid}/chronicle")
def get_chronicle(cid: str, limit: int | None = None, offset: int | None = None):
    """The campaign's chronicle records, oldest-first, newest page by default.

    The one route here whose window is anchored at the *newest* end rather than
    at the front of what it prints: `offset` skips that many of the NEWEST
    records, so a reader pages backwards by raising it. `chronicle.page` holds
    that shape and says why it is the only one available here.

    Sending neither parameter returns the newest `CHRONICLE_PAGE`, byte for byte
    what this route returned before it could be paged (#216). `offset` on its
    own therefore slides that same default-sized window back, rather than
    returning everything after a skip.

    The anchor is what moves: absorbing a scene appends a record and shifts
    every offset by one, so a reader paging backwards across a campaign still
    being played can see a record twice. Same caveat as `GET .../scenes`, and
    the same answer — an index-anchored window (`GET .../scenes/{sid}`, which
    takes `before`) is what does not drift.
    """
    limit, offset = _page_window(limit, offset)
    _campaign_root_or_404(cid)
    return store.chronicle.page(cid, CHRONICLE_PAGE if limit is None else limit, offset)


# Indirection so tests can drive budget arithmetic off a fake clock instead of
# real waiting. Deliberately NOT time.time(): a wall-clock jump (NTP, DST,
# sleep/wake) must not expire or extend a running absorb.
_clock = time.monotonic
BUDGET_EXHAUSTED = "absorb time budget exhausted"


class Abandoned(Exception):
    """Nobody is waiting for this work any more, so it stopped.

    Not an LLMError: no call failed and no clock ran out. The phases that catch
    it turn it into their own "the review was closed" status, and both scoped
    retries hand that back to a client that has already gone -- the value is in
    stopping, not in what is returned.
    """

    #: `store.usage.NOT_A_FAILURE`: a reviewer walking away is not a provider
    #: failing them, so this must not reach the error store (#156).
    llm_call_failed = False


ABANDON_POLL = 0.5

# What a review says about itself once the reviewer has dismissed it. The
# vestigial `_ABANDONED_DOSSIERS`/`_ABANDONED_AUDIT` bodies that used to sit
# here went with the socket: a scoped retry no longer answers down a connection
# nobody is holding, so there is no body to invent -- the run is marked
# `cancelled` and this is what its error says.
_ABANDONED_REASON = "the review this was for was closed before it finished"


async def _watched(coro, abandoned, poll: float = ABANDON_POLL):
    """Await `coro`, giving up on it if the caller goes away while it runs.

    A client disconnect does NOT cancel a plain endpoint -- uvicorn runs it to
    completion -- so without this nothing notices. Checking only *between* calls
    is not enough either, which is the gap this closes: a single wedged call
    (the audit is one call; the first NPC of a dossier run is another) holds the
    request for as long as the provider keeps dribbling inside the idle bound,
    and `absorb_budget = 0` means no deadline ever ends it. Cancel is exactly
    what the panel offers as the way out of that, so the call is raced against
    the check rather than merely bracketed by it.

    The abandoned call is cancelled and detached, not awaited: `llm._settle`'s
    reason -- waiting for the cancellation you asked for hands the unwinding the
    very control you were taking back.
    """
    if abandoned is None:
        return await coro
    task = asyncio.ensure_future(coro)

    def detach():
        task.cancel()
        task.add_done_callback(lambda t: None if t.cancelled() else t.exception())

    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=poll)
            if done:
                return task.result()
            if await abandoned():
                detach()
                raise Abandoned
    except asyncio.CancelledError:
        # The REQUEST was cancelled -- a graceful-shutdown deadline, or a server
        # that does cancel handlers on disconnect. `asyncio.wait` re-raises that
        # straight through here, and the child is a separately scheduled task
        # that nothing else holds: without this it keeps generating, outliving
        # the request whose cost this helper exists to bound. Same condition
        # `_bounded_call` covers, same treatment.
        detach()
        raise


def _phase_or_raise(result):
    """One phase's `(edits, block)` pair, or its exception re-raised.

    `_stage_dossiers`, `_stage_voice_drift` and `_run_audit` each document that
    they never raise for absorb -- every failure comes back as a status the
    inspector renders. `gather(return_exceptions=True)` would quietly turn a
    breach of that into a tuple-unpacking error somewhere else, so it is
    surfaced here instead, where the traceback still points at the phase.
    """
    if isinstance(result, BaseException):
        raise result
    return result


async def _gather_phases(*coros, limit: int) -> list:
    """Run absorb's phase coroutines concurrently, at most `limit` in flight,
    returning their results POSITIONALLY.

    Positional, so the staged edits are assembled in a fixed order however the
    calls happen to finish -- the review a reviewer reads must not be shuffled
    by which provider replied first, and `test_frozen_campaign` would notice.

    `return_exceptions=True` is not a convenience. A bare `gather` propagates
    the first exception and leaves its siblings RUNNING: orphaned provider
    calls nobody will read and nothing is bounding. `Abandoned` and
    `BudgetRefused` both fly through this code, so that is the ordinary path
    here rather than the exotic one, and the caller unpacks each result and
    decides. Only the extraction's failure is fatal; the other three never
    raise for absorb (each has its own failure boundary) and report a status.

    Each phase still carries its own share of the budget: `_Budget.run` reads
    the remaining time when it is called, and every phase calls it at once, so
    each gets the whole window rather than a slice. Parallel phases do not
    consume one another's wall-clock.
    """
    sem = asyncio.Semaphore(limit)

    async def guarded(coro):
        async with sem:
            return await coro

    tasks = [asyncio.ensure_future(guarded(c)) for c in coros]
    try:
        return await asyncio.gather(*tasks, return_exceptions=True)
    except asyncio.CancelledError:
        # The REQUEST was cancelled -- shutdown, or a server that cancels
        # handlers on disconnect. Detach rather than await, for `_watched`'s
        # reason: waiting for the cancellation you asked for hands the
        # unwinding the very control you were taking back.
        for t in tasks:
            t.cancel()
            t.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        raise


class BudgetRefused(LLMError):
    """The budget was already gone, so the call was never issued.

    An LLMError subclass, so every existing `except LLMError` still covers it —
    but a phase that cares can tell "never sent" from "sent, then cancelled".
    Only the first means the step was never attempted, and only the first is a
    step to report as skipped rather than failed."""

    #: `store.usage.NOT_A_FAILURE`. This carries `kind="timeout"` like every
    #: other LLMError, which is exactly why it has to say so out loud: nothing
    #: downstream could otherwise tell a step the clock refused to start from a
    #: provider that really did time out, and the error store would report the
    #: second when it was the first.
    llm_call_failed = False


class _Budget:
    """A wall-clock ceiling on one absorb's whole LLM sequence (#243).

    Absorb awaits an extraction call, then one dossier call per present NPC,
    then one voice-drift call per present NPC that has a voice anchor, then an
    audit call, all inside a single HTTP request — the per-call idle
    timeout in `llm` bounds each *stall*, but nothing bounds the total. This
    does, and it is deliberately absorb's policy rather than the LLM facade's:
    only the caller knows which of its steps are droppable.

    `seconds <= 0` means no ceiling at all (config's escape hatch), in which
    case every method degrades to a plain await.
    """

    def __init__(self, seconds: float):
        self._deadline = None if seconds <= 0 else _clock() + seconds

    def remaining(self) -> float | None:
        return None if self._deadline is None else self._deadline - _clock()

    def spent(self) -> bool:
        left = self.remaining()
        return left is not None and left <= 0

    async def run(self, coro, on_start=None, on_timeout=None):
        """Await `coro` under the remaining budget, reporting an overrun as the
        same LLMError kind an upstream stall raises — so every caller's existing
        LLM failure handling covers it with no new branch.

        `on_start` fires only if the call actually goes out, and `BudgetRefused`
        says it did not. Both exist because this is the only place that can
        decide it: a caller's own `spent()` check is already stale by the time
        the deadline is read below, however few statements sit in between.

        `on_timeout` fires for a call that went out and was then cut off here,
        so the connection it was running on can be told (`common._noting`).

        wait_for waits for the cancellation it requests to complete, so the
        real ceiling is the budget plus however long the call takes to unwind;
        that unwinding is itself hard-bounded in `llm` (grace-then-abandon),
        which is what keeps this a bound rather than a hope.
        """
        left = self.remaining()
        if left is None:
            if on_start:
                on_start()
            return await coro
        if left <= 0:
            # Handled here rather than left to wait_for, which cancels a task
            # before its first step and so reports a timeout for a request that
            # never left. `close()` retires the coroutine we are not going to
            # await, which would otherwise warn at collection.
            coro.close()
            raise BudgetRefused("timeout", BUDGET_EXHAUSTED)
        # Past this point wait_for runs the task's first step before its timer
        # can fire, so the call is issued even if the answer never arrives.
        if on_start:
            on_start()
        try:
            return await asyncio.wait_for(coro, left)
        except TimeoutError as exc:
            # asyncio.TimeoutError is the builtin TimeoutError from 3.11 on, so
            # this also catches one raised *inside* the call. Only blame the
            # budget when the budget is actually gone.
            detail = BUDGET_EXHAUSTED if self.spent() else (str(exc) or "the call timed out")
            overrun = LLMError("timeout", detail)
            # This ceiling has the same blind spot `routes.common._bounded_call`
            # does, and for the same reason (#146): `wait_for` cancels the call
            # from outside the facade, so `_resilient` unwinds through
            # `GeneratorExit` rather than its `except LLMError` and the attempt
            # nobody got an answer from is the one the health registry never
            # hears about. An absorb phase stopped this way would leave the
            # connection reading green.
            #
            # Only here, not on the `BudgetRefused` path above: a step that was
            # never issued is a statement about this absorb's clock, not about
            # the provider.
            if on_timeout is not None:
                on_timeout(overrun)
            raise overrun from exc


def _budget_overrun(exc: BaseException) -> bool:
    """Whether `exc` is this absorb's own clock running out rather than an
    upstream stall.

    `_Budget.run` deliberately reports both as the same LLMError kind so callers
    need no extra branch to *handle* them -- but a phase needs to *say* which
    happened, because only one of them is fixed by a larger budget. The detail
    is the sentinel `_Budget.run` sets, not a substring guess."""
    return isinstance(exc, LLMError) and exc.detail == BUDGET_EXHAUSTED


def _phase_report(dossiers: dict, voice: dict, mechanics: dict) -> list[dict]:
    """One row per LLM-backed step of this absorb, in run order: was it
    attempted, how did it end, and was the shared time budget what stopped it
    (#243/#236 follow-up).

    Without it, a slow-but-healthy extraction that eats the whole budget returns
    an absorb with fewer proposed edits and no way to tell that apart from a
    model that simply had nothing to suggest.

    Each row is *projected* from the block that already reports that step -- the
    `audit` row from `mechanics`, which is that step's block -- so `phases` is a
    uniform view rather than a second source of truth that can drift from what
    the review panel renders beside it.

    Extraction gets no block because it has no partial outcome: `post_absorb`
    raises when it fails, so reaching this call already proves it succeeded.

    ("Phase" here means a step of one absorb run; the `Phase 2:`/`Phase 5:`
    comments elsewhere in this file are roadmap milestones, unrelated.)"""
    keys = store.pending_reviews.PHASE_KEYS
    return [{"name": "extraction", "status": "ok", "reason": None,
             "attempted": True, "budget_exhausted": False}] + \
           [{"name": name, **{k: block[k] for k in keys}}
            for name, block in (("dossiers", dossiers), ("voice", voice),
                                ("audit", mechanics))]


def _soft_connection(resolve) -> tuple[dict | None, str]:
    """A SECONDARY absorb phase's connection, or why it has none (#142).

    `(None, reason)` rather than a raised 409, because the three phases below
    each promise never to fail an absorb: a dossier refresh routed at a
    connection with no key is that phase reporting `failed` with a reason the
    inspector shows, not the extraction losing its result over a setting it does
    not use. The extraction itself keeps the 409 -- it is the phase whose failure
    is fatal anyway.

    Takes a THUNK rather than a task name, so the task stays a literal at the
    call site. `test_routing_guard.py` reads those literals, and a helper that
    forwarded `task` would hide all three calls from it behind one unroutable
    one -- which is how a routing map goes stale without anything failing.
    """
    try:
        return resolve(), ""
    except HTTPException as exc:
        detail = exc.detail
        return None, str(detail.get("detail") if isinstance(detail, dict) else detail)


async def _run_audit(cid: str, sid: str, client: LLMClient, conn: dict | None,
                     budget: _Budget, abandoned=None,
                     unroutable: str = "") -> tuple[list[dict], dict]:
    """(edits, mechanics) for the scene audit. Never raises for absorb; every
    failure is an explicit mechanics status (spec: audit visibility) so absorb
    stays intact even when the audit pipeline blows up.

    `attempted` says whether a request actually reached the model and
    `budget_exhausted` says whether this absorb's clock is why it did not --
    the two facts a bare `status: failed` cannot carry.

    `abandoned` is _stage_dossiers' predicate, and matters more here: the audit
    is ONE call, so there is no "between calls" for a check to sit in. Watching
    the call itself is the only thing that can end it. Absorb passes nothing, so
    the never-raises rule above is untouched on that path."""
    mech = {"status": "skipped", "reason": None, "warnings": [], "dropped": [],
            "attempted": False, "budget_exhausted": False}
    if conn is None:
        # No usable connection for this phase's route -- reported the way every
        # other audit failure is, so the absorb around it still lands.
        return [], {**mech, "status": "failed", "reason": unroutable or "no connection"}
    excluded: list = []
    try:
        if store.modules.resolve(cid) is None:
            mech["reason"] = "no module"
            return [], mech
        # ONE failure boundary around the ENTIRE audit pipeline (spec:
        # never-fail-absorb) — sheet_blocks, read_scene, transcript,
        # roll_lines, build_prompt, complete, parse AND materialize. Any
        # exception anywhere here is a failed audit, never a 500 absorb.
        blocks, excluded = store.audit.sheet_blocks(cid, sid)
        if not blocks and not excluded:
            mech["reason"] = "no sheeted scope"
            return [], mech
        if not blocks:
            return [], {**mech, "status": "failed",
                        "reason": "all scoped sheets invalid", "dropped": excluded}
        scene = store.scenes.read_scene(cid, sid)
        transcript = store.chronicle.transcript_text(scene["messages"])
        messages = store.audit.build_prompt(transcript, blocks,
                                            store.audit.roll_lines(cid, sid))
        # `mech` is the accumulator every failure return below spreads, so the
        # callback reaches all of them -- and it fires only if the request goes
        # out, which is a fact only `run` holds.
        with store.usage.meter("audit", campaign=cid, scene=sid) as m:
            text = await _watched(
                budget.run(client.complete(messages, conn, m.usage),
                           lambda: mech.__setitem__("attempted", True),
                           on_timeout=_noting(client, conn, m.usage)), abandoned)
        parsed = store.audit.parse_output(text)
        edits, dropped = store.audit.materialize(cid, sid, parsed)
    except Abandoned:
        # Ahead of the boundary below on purpose. That boundary exists so a
        # broken audit cannot fail an absorb; this is not a broken audit, it is
        # nobody waiting for one, and turning it into a `failed` mechanics
        # status would report a phase that ran and did not work.
        raise
    except BudgetRefused:
        # Never asked, so there is no finding to doubt -- only work still owed.
        # Still `failed` (not `skipped`) and still paired with the POST /audit
        # retry the UI renders: a fresh budget is exactly what that retry gets.
        return [], {**mech, "status": "failed", "budget_exhausted": True,
                    "reason": "the absorb time budget ran out before the audit could run",
                    "dropped": excluded}
    except store.audit.AuditParseError as exc:
        return [], {**mech, "status": "failed", "reason": str(exc),
                    "dropped": excluded}
    except Exception as exc:  # noqa: BLE001 -- LLMError, store errors, anything
        # A budget overrun reaching *this* handler was cancelled mid-flight, so
        # the request did go out and `mech["attempted"]` stays true -- the never
        # -sent case is `BudgetRefused`, caught above.
        return [], {**mech, "status": "failed", "reason": f"audit failed: {exc}",
                    "dropped": excluded, "budget_exhausted": _budget_overrun(exc)}
    dropped = excluded + dropped
    status = "degraded" if dropped else "ok"
    reason = ("some sheets could not be audited" if excluded else
              "some findings could not be validated") if dropped else None
    return edits, {"status": status, "reason": reason,
                   "warnings": parsed["warnings"], "dropped": dropped,
                   "attempted": True, "budget_exhausted": False}


async def _stage_dossiers(cid: str, sid: str, transcript: str, client: LLMClient,
                          conn: dict | None, budget: _Budget,
                          abandoned=None,
                          unroutable: str = "") -> tuple[list[dict], dict]:
    """Propose a refreshed campaign dossier for every present NPC, reporting the
    outcome.

    The LLM call happens here; the WRITE does not (#235). Each dossier comes back
    as a StagedEdit that lands with the rest of the batch in PUT /chronicle, so an
    absorb that dies partway through this loop -- or a reviewer who hits Cancel --
    leaves nothing behind. `proposed` therefore names the NPCs whose dossier was
    generated, not written; an NPC whose paragraph came back unchanged is proposed
    with no edit to show for it.

    Never raises for absorb -- a dossier failure must not fail absorb -- but it
    is not silent either: failures (#236) and budget skips (#243) come back as a
    status the inspector renders, mirroring _run_audit's shape -- including
    `attempted` and `budget_exhausted`, the two flags a phase row is built from.

    `abandoned` is an optional async predicate: "is anyone still waiting for
    this?". It is asked between NPCs and, via `_watched`, alongside the call in
    flight; when it says no, `Abandoned` comes out and the caller that supplied
    the predicate turns it into a response. Absorb passes nothing -- its caller
    is holding a review open and has not gone anywhere -- so the never-raises
    rule above is untouched on that path: no predicate, no Abandoned."""
    out: dict = {"status": "skipped", "reason": None,
                 "proposed": [], "failed": [], "skipped": [],
                 "attempted": False, "budget_exhausted": False}
    if conn is None:
        return [], {**out, "status": "failed", "reason": unroutable or "no connection"}
    edits: list[dict] = []
    try:
        cast = store.appearances.scene_cast(cid, sid)
        croot = store.appearances.locked_actor_root(cid)   # cast actors are locked, so campaign-side
    except Exception as exc:  # noqa: BLE001 -- an unreadable cast is a failed phase, not a 500
        # Recorded as well as reported (#156): the phase status is visible in
        # the review that is open right now, and gone the moment it is closed.
        # `record_exception`, like the generic handler below, so the frames
        # survive too. This row is the only durable trace of a cast read that
        # failed -- the phase status dies with the review it is rendered in --
        # and a row without the stack is the one that cannot be acted on.
        store.errors.record_exception(exc, "dossier",
                                      detail=f"could not read the scene cast: {exc}",
                                      campaign=cid, scene=sid, task="dossier")
        return [], {**out, "status": "failed", "reason": f"could not read the scene cast: {exc}"}
    def drop_tail(i: int) -> None:
        """Record the NPC at `i` and everyone after it as never reached.

        The extraction call is the part worth keeping, so the tail is dropped
        rather than run unbounded (#243) — but named, not silently, for the same
        reason failures are (#236)."""
        out["skipped"] = [b["id"] for b in cast[i:]
                          if b["kind"] == "characters" and b["role"] == "npc"]
        out["budget_exhausted"] = True

    for i, a in enumerate(cast):
        if a["kind"] != "characters" or a["role"] != "npc":
            continue  # dossiers feed the npc-only "Active elsewhere" tier; skip player cards
        if budget.spent():
            drop_tail(i)
            break
        # The reviewer walked away -- closed the review, saved it, switched
        # campaigns -- and the client aborted. Nothing will ever read this
        # reply, so every remaining NPC is an LLM call spent on nobody's behalf,
        # and `absorb_budget = 0` means the check above will never stop them.
        #
        # This one keeps the NEXT call from being issued at all; `_watched`
        # below stops the one already in flight. Both, because they cover
        # different moments: a disconnect during the gap between NPCs is caught
        # here at once, rather than costing a doomed call that `_watched` then
        # has to cancel a poll interval later.
        if abandoned is not None and await abandoned():
            raise Abandoned
        try:
            name = store.characters.read_character(croot, a["id"])["meta"].get("name", a["id"])
            # Read ONCE, before the await. Re-reading after it to build the
            # staged `before` would record a paragraph another review wrote
            # while this call was in flight -- the conflict guard would then
            # pass and this stale output would overwrite that newer one (#235).
            prior = store.dossiers.read(croot, a["id"])
            msgs = store.dossiers.build_prompt(name, prior, transcript)
            # The loop's own check is stale by now -- the two reads and the
            # prompt build above are not free -- so the attempt is recorded by
            # `run`, which alone can decide it atomically with the deadline.
            with store.usage.meter("dossier", campaign=cid, scene=sid) as m:
                d_text = await _watched(
                    budget.run(client.complete(msgs, conn, m.usage),
                               lambda: out.__setitem__("attempted", True),
                               on_timeout=_noting(client, conn, m.usage)), abandoned)
            parsed_dossier = store.dossiers.parse_output(d_text)
            # stage_edit returns None for an unchanged paragraph AND for a blank
            # reply; only the first is a success. Left conflated, a model that
            # answers "" for every NPC reports `ok` with nothing staged -- exactly
            # #236's symptom (dossiers quietly stop updating) wearing a status.
            if not parsed_dossier:
                # #236's symptom, recorded rather than only reported. This is
                # the branch the error store was asked for by name: no
                # exception is raised here, so nothing else in the app --
                # not the meter, not the logging bridge -- ever sees it, and
                # the phase status carrying it dies with the review.
                store.errors.record("dossier", "empty_reply",
                                    f"{name} came back with an empty dossier",
                                    campaign=cid, scene=sid, task="dossier")
                out["failed"].append({"id": a["id"], "reason": "empty dossier reply"})
                continue
            edit = store.dossiers.stage_edit(a["id"], name, prior, parsed_dossier)
        except Abandoned:
            # Ahead of the generic handler below on purpose: this is not this
            # NPC's failure to record and move past, it is the whole run being
            # over. The loop's caller turns it into a status rather than a 500.
            raise
        except BudgetRefused:
            # Refused, not failed: nothing was sent, so this NPC is one more the
            # clock never reached — and so is everyone after them.
            drop_tail(i)
            break
        except Exception as exc:  # noqa: BLE001 -- LLMError, store errors, anything
            # Recorded here as well as reported, and `record_exception` is what
            # makes that safe: a provider failure has already been written down
            # by the meter above and is marked, so this call leaves it alone,
            # while the failures the meter never saw -- an unreadable character
            # file, a dossier that would not parse -- are #156's "fails with
            # zero trace anywhere" and get their row here.
            store.errors.record_exception(exc, "dossier", campaign=cid, scene=sid,
                                          task="dossier")
            # Type-prefixed: a bare str() is useless for the store's own errors
            # (CharacterNotFound("aese") stringifies to just "aese").
            detail = str(exc).strip()
            out["budget_exhausted"] = out["budget_exhausted"] or _budget_overrun(exc)
            out["failed"].append({
                "id": a["id"],
                "reason": f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__})
        else:
            out["proposed"].append(a["id"])
            if edit:
                edits.append(edit)
    if not out["proposed"] and not out["failed"] and not out["skipped"]:
        return edits, {**out, "reason": "no npcs present"}
    if not out["proposed"]:
        # A budget that ran out before the first call is a different story from
        # calls that were made and went wrong; say which one happened.
        return edits, {**out, "status": "failed",
                       "reason": "no dossier could be prepared" if out["failed"] else
                                 "the absorb time budget ran out before any dossier "
                                 "could be prepared"}
    if out["failed"]:  # the more specific story when both happened; `skipped` still lists the rest
        return edits, {**out, "status": "degraded",
                       "reason": "some dossiers could not be prepared"}
    if out["skipped"]:
        return edits, {**out, "status": "degraded",
                       "reason": "the absorb time budget ran out before the rest "
                                 "could be prepared"}
    return edits, {**out, "status": "ok"}


async def _stage_voice_drift(cid: str, sid: str, transcript: str, client: LLMClient,
                             conn: dict | None, budget: _Budget,
                             unroutable: str = "") -> tuple[list[dict], dict]:
    """Judge every present NPC's dialogue in this scene against their voice
    anchor, proposing a drift flag (or a clear) for each.

    Runs ONLY for NPCs that actually have an anchor (#59). That is the cost
    control for the whole feature: a library with no anchors makes no extra LLM
    calls, and adding one is how a user opts a character in.

    The LLM call happens here; the WRITE does not, for _stage_dossiers' reason
    (#235) -- a flag that landed before the reviewer saved would survive a
    Cancel and go on correcting the model for a scene the chronicle never
    recorded.

    Never raises -- voice drift must not fail absorb -- but it is not silent
    either: failures and budget skips come back as a status the inspector
    renders, mirroring _stage_dossiers' shape -- including `attempted` and
    `budget_exhausted`, the two flags a phase row is built from."""
    out: dict = {"status": "skipped", "reason": None, "checked": [], "flagged": [],
                 "unjudged": [], "failed": [], "skipped": [],
                 "attempted": False, "budget_exhausted": False}
    if conn is None:
        return [], {**out, "status": "failed", "reason": unroutable or "no connection"}
    edits: list[dict] = []
    try:
        cast = store.appearances.scene_cast(cid, sid)
        croot = store.appearances.locked_actor_root(cid)   # cast actors are locked, so campaign-side
        speakers = store.appearances.roster_names(cid)
    except Exception as exc:  # noqa: BLE001 -- an unreadable cast is a failed phase, not a 500
        return [], {**out, "status": "failed", "reason": f"could not read the scene cast: {exc}"}
    # The speaker labels this scene's transcript can carry. transcript.j2 labels
    # each line with the speaker's card name and nothing else, so an anchored NPC
    # sharing a label with ANY other speaker is unjudgeable -- the judge cannot
    # tell whose lines belong to its subject, and answers confidently regardless.
    #
    # Counted over the CAMPAIGN roster, not the present cast, because the present
    # cast is not the set of speakers: `scene_cast` drops an actor the moment it
    # leaves, while the transcript keeps every line it spoke, still wearing its
    # name. `_drift_roster` reaches for the same list against the same problem.
    # Nothing records which scene a departed actor spoke in, so this over-counts
    # a same-named actor who was never here -- the safe direction, since the
    # alternative is a corrective persisted against the wrong character.
    # Plus the role fallbacks transcript.j2 uses for a line carrying no speaker
    # stamp -- those are labels too. Whether this scene has any such line is not
    # recoverable from the rendered transcript, and an ambiguous judgment must
    # never be persisted, so a character wearing one of these names is
    # disqualified unconditionally rather than on a guess.
    speakers = [n for n in speakers if isinstance(n, str) and n.strip()]
    speakers += ["You", "Grimoire"]

    # Resolved up front so the budget check below covers only the actors that
    # will really cost a call: an anchorless NPC must not be reported as
    # "skipped for time" when it was never going to be judged at all.
    todo = []
    for a in cast:
        if a["kind"] != "characters" or a["role"] != "npc":
            continue   # a player character's voice is the user's to drift
        try:
            record = store.overlay.voice_anchor_record(cid, a["id"])
        except Exception as exc:  # noqa: BLE001 -- unreadable anchor: skip this actor, keep the phase
            out["failed"].append({"id": a["id"], "reason": f"{type(exc).__name__}: {exc}"})
            continue
        if record["text"]:
            # `a["name"]` is the LOCKED VERSION's card name, which is what the
            # transcript labels this character's lines with. The container's
            # meta name can differ from it, and naming the judge a character the
            # transcript never mentions is how a multi-NPC scene gets judged
            # against the wrong lines (or comes back `not_enough`).
            #
            # Cards are stored as arbitrary dicts, so `data.name` can be a
            # number or an object -- import and version PUT both accept them.
            # Checked HERE, inside the per-actor boundary: everything below
            # treats the name as text, and one bad card must cost that actor its
            # voice check rather than 500 the whole absorb.
            # The RAW card name, not `a["name"]`: `_actor_name` substitutes the
            # actor id for a card that carries no usable one, and that id is a
            # display convenience, not something the transcript is known to
            # label anyone with. Judging against it means judging against a
            # name nobody agreed on.
            try:
                vid = store.appearances.locked_version(cid, "characters", a["id"])
                data = store.characters.read_card(croot, a["id"], vid).get("data")
            except Exception as exc:  # noqa: BLE001 -- unreadable card: skip this actor
                out["failed"].append({"id": a["id"], "reason": f"{type(exc).__name__}: {exc}"})
                continue
            name = data.get("name") if isinstance(data, dict) else None
            # `label_preserved`, not merely "is a nonblank string": the
            # serializer silently writes the generic role label instead of a
            # name it cannot form a marker from (one holding `*` or a newline,
            # or over 64 characters). Those lines land in the transcript as
            # "Grimoire", so pointing the judge at the card name hunts for a
            # speaker that cannot appear -- and risks charging generic
            # assistant prose to this character instead.
            if not isinstance(name, str) or not store.scenes.label_preserved(name):
                out["failed"].append({
                    "id": a["id"],
                    "reason": "the locked card has no name that can appear as a transcript "
                              "label, so the judge cannot be pointed at its lines"})
                continue
            # Report the clash instead of judging through it -- naming it is
            # actionable (rename a card), whereas judging is a coin flip
            # presented as a finding, and a wrong one persists a corrective
            # that nags a character for dialogue someone else spoke.
            # `scenes.confusable`, not a whole-name comparison: `match_name` is
            # what decided which cast member a written label meant when the
            # reply was stored, so it is what decides whether a label is
            # ambiguous. "Winifred Vance" and "Winifred Vale" are distinct
            # strings, but a block labelled "Winifred" belongs to neither.
            if store.scenes.confusable(name, speakers):
                out["failed"].append({
                    "id": a["id"],
                    "reason": f"another speaker in this scene can also be labelled {name!r}, "
                              "so their lines cannot be told apart in the transcript"})
                continue
            todo.append((a["id"], name, record))

    def drop_tail(i: int) -> None:
        """Record the anchored NPC at `i` and everyone after it as never
        reached -- named, not silently dropped, for _stage_dossiers' reason."""
        out["skipped"] = [b for b, _, _ in todo[i:]]
        out["budget_exhausted"] = True

    for i, (aid, name, record) in enumerate(todo):
        if budget.spent():
            drop_tail(i)
            break
        try:
            # Read ONCE, before the await -- dossiers' rule: re-reading after it
            # would record a flag another review wrote while this call was in
            # flight, and the conflict guard would then pass on stale output.
            # One snapshot, so the note and the provenance staged as `before`
            # always describe the same committed flag (voice_drift.read_record).
            flag = store.voice_drift.read_record(croot, aid)
            prior, prior_fp = flag["note"], flag["anchor"]
            # Only a correction that is STILL IN FORCE may reach the judge.
            # `context/cast.py` applies exactly this test before putting a note
            # in the SCENE prompt; without it here, a note fingerprinted to a
            # REPLACED anchor is suppressed for the writer and handed to the
            # judge as current -- which mints a fresh flag against the anchor
            # that replaced it. "" is a pre-nonce flag, which counts as valid
            # for the reason `anchor_fingerprint` documents.
            live = prior if (not prior_fp or store.voice_drift.fingerprint_matches(
                prior_fp, record["text"], record["id"])) else ""
            # The judge is sent the EFFECTIVE anchor, because that is all the
            # generator ever saw: a rule past the cap is enforced against
            # neither. The FINGERPRINT above still uses the raw stored text --
            # capping it would retire every correction whose anchor is long.
            msgs = store.voice_drift.build_prompt(
                name, store.voice_anchors.effective(record["text"]), transcript,
                correction=live)
            # The loop's own check is stale by now, so the attempt is recorded
            # by `run`, which alone can decide it atomically with the deadline.
            with store.usage.meter("voice-drift", campaign=cid, scene=sid) as m:
                text = await budget.run(client.complete(msgs, conn, m.usage),
                                        lambda: out.__setitem__("attempted", True),
                                        on_timeout=_noting(client, conn, m.usage))
            finding = store.voice_drift.parse_output(text)
            # An unreadable verdict is a FAILED call, not a quiet pass. Left
            # conflated with "in voice" it would stage a default-approved clear
            # of a standing flag on the strength of a garbled reply -- and a
            # model that answers nonsense for every NPC would report `ok` while
            # retiring the campaign's correctives one by one.
            if finding["verdict"] == store.voice_drift.UNKNOWN:
                out["failed"].append({"id": aid, "reason": "unreadable verdict from the voice judge"})
                continue
            # Both note checks are DRIFT-only, because only a drift verdict
            # stores a note: `stage_edit` writes `after=""` for IN_VOICE and
            # proposes nothing at all for NOT_ENOUGH, so their notes never reach
            # a prompt. Failing the call on an oversized note there would punish
            # a chatty explanation by leaving an obsolete corrective standing --
            # the clear is the whole point of a clean verdict, and a note nobody
            # stores cannot cost a single token.
            if finding["verdict"] == store.voice_drift.DRIFT:
                # No note is unusable: the note IS the corrective the next turn
                # gets. Report it rather than staging a flag that would say
                # nothing, or silently downgrading it to "fine".
                if not finding["note"]:
                    out["failed"].append({"id": aid, "reason": "drift reported with no corrective"})
                    continue
                # ...and the corrective is rendered into the post-history
                # message, which the packer reserves and cannot trim, so an
                # oversized one is charged against every later generation with
                # nothing able to give way.
                if len(finding["note"]) > store.voice_drift.MAX_NOTE:
                    out["failed"].append({
                        "id": aid,
                        "reason": f"the voice judge returned a corrective over "
                                  f"{store.voice_drift.MAX_NOTE} characters, too long to put "
                                  f"in front of every following turn"})
                    continue
            edit = store.voice_drift.stage_edit(aid, name, prior, finding,
                                                record["text"], record["id"], prior_fp)
        except BudgetRefused:
            # Refused, not failed: nothing was sent, so this NPC is one more the
            # clock never reached — and so is everyone after them.
            drop_tail(i)
            break
        except Exception as exc:  # noqa: BLE001 -- LLMError, store errors, anything
            detail = str(exc).strip()
            out["budget_exhausted"] = out["budget_exhausted"] or _budget_overrun(exc)
            out["failed"].append({
                "id": aid,
                "reason": f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__})
        else:
            out["checked"].append(aid)
            if finding["verdict"] == store.voice_drift.DRIFT:
                out["flagged"].append(aid)
            elif finding["verdict"] == store.voice_drift.NOT_ENOUGH:
                # A real judgment that produces no edit -- like a dossier that
                # came back unchanged. Named so "checked, not flagged" does not
                # read as "confirmed in voice" when nobody actually heard them.
                out["unjudged"].append(aid)
            if edit:
                edits.append(edit)
    if not out["checked"] and not out["failed"] and not out["skipped"]:
        return edits, {**out, "reason": "no anchored npcs present"}
    if not out["checked"]:
        return edits, {**out, "status": "failed",
                       "reason": "no voice check could be run" if out["failed"] else
                                 "the absorb time budget ran out before any voice check "
                                 "could be run"}
    if out["failed"]:  # the more specific story when both happened; `skipped` still lists the rest
        return edits, {**out, "status": "degraded",
                       "reason": "some voice checks could not be run"}
    if out["skipped"]:
        return edits, {**out, "status": "degraded",
                       "reason": "the absorb time budget ran out before the rest "
                                 "could be checked"}
    return edits, {**out, "status": "ok"}


def _already_absorbed(scene: dict) -> bool:
    """Whether THIS scene was absorbed, read from its own frontmatter.

    Deliberately not `sid in chronicle`: scene numbers are derived from the files
    on disk and `delete_scene` leaves the chronicle entry behind, so deleting the
    highest-numbered absorbed scene and remaking it under the same title hands the
    new scene the same id. A chronicle lookup would then refuse to absorb a
    brand-new scene. `done` is written only by mark_absorbed, into the scene file
    itself, so a recycled id starts clean."""
    return str(scene.get("meta", {}).get("done", "")).lower() == "true"


def _absorb_snapshot(cid: str, sid: str, identity: str | None) -> tuple[int, dict, list]:
    """The scene, its commit epoch and its transient-state ledger as of one
    instant, under one lock hold.

    Split out of `post_absorb` so the whole of it is one critical section.
    Raises `_require_scene`'s 404 like any other handler code.

    Taken in the HANDLER and handed to the run, never read when the result is
    collected. Detached, those two moments are minutes apart, and an epoch read
    at collection would date the response rather than the snapshot -- letting a
    review built from pre-save state pass its own supersession check, which is
    exactly what #271 warns against. `_already_absorbed` is judged from this
    same snapshot for the other half of the pair: it has to raise its 409
    before a token is spent.

    `identity` is the one the reservation captured. Checked here rather than
    trusted, because a scene deleted and replaced between the reservation and
    this read lands on the same `sid` -- ids are recycled by design -- and the
    review would then be built from the replacement's transcript and published
    onto it.
    """
    with store.locks.campaign_lock(cid):
        scene = _require_scene(cid, sid)
        if streaming._scene_moved(cid, sid, identity):
            raise HTTPException(status_code=404, detail="scene not found")
        # The ledger travels with the scene, not derived from it afterwards.
        # An edit or a reroll landing while the extraction call is in flight
        # rewrites entries *below* the tail, so a length is not a snapshot --
        # only a copy taken under this same hold is one (#120/#121).
        ledger = store.turnstate.entries(cid, sid, len(scene["messages"]))
        return store.commits.scene_epoch(cid, sid), scene, ledger


def _contradictions(cid: str, sid: str, edits: list) -> list[dict]:
    """`retcon.contradictions`, and never a reason to lose an extraction.

    The badges are advisory by design, and this runs at the very end of a
    handler that has just made several model calls: a garbled `plot.json` or a
    hand-edited `changes.json` costing the reviewer the whole review would be a
    worse failure than showing it with no badges on it. Logged, because a
    reader who cannot see the badges cannot report their absence.
    """
    try:
        return store.retcon.contradictions(cid, sid, edits)
    except Exception:  # an advisory pass, over hand-editable files
        log.warning("contradiction pass failed for %s/%s", cid, sid, exc_info=True)
        return []


# ---- the review family, detached (#396) -------------------------------------
#
# `absorb`, `audit` and `dossiers` are the `review` class: their value is a
# payload nobody has written down, and losing it costs the longest generation
# in the app rather than a cheap retry. So each starts a run, answers 202 with
# the run's id, and persists its result to `store.pending_reviews`; the client
# polls `GET .../runs/{id}` and then reads the review back off the store, which
# is what makes it survive a locked phone, a closed tab and a process restart
# alike.
#
# Three refusals guard the record, and each has its own kind because a client
# does something different with each:
#
# * `review_cancelled` -- the reviewer pressed Cancel. Recorded on the run
#   under the campaign lock BEFORE the record is deleted, and read by the
#   terminal persist under that same hold, so the run cannot recreate a review
#   that has just been dismissed.
# * `scene_gone` -- the scene this run captured is no longer at that id. An
#   existence check is not enough: `scenes/serialize._numbering` derives the
#   next number from the files on disk, so a deleted scene's id comes back, and
#   publishing onto it would put one scene's review on another's transcript.
# * `review_replaced` / `review_missing` -- a retry had nothing of its own to
#   fold into. Writing `{mechanics, edits}` or `{dossiers, edits}` whole would
#   destroy the absorb's prose, its staged edits and its commit token.


class _Prepared(NamedTuple):
    """Everything an absorb decides before it starts generating.

    One value rather than seven parameters, and it is not only tidiness: these
    are the snapshot -- read under one campaign-lock hold, at one instant, and
    the review is built from all of them or none. Threaded individually through
    the handoff they read as seven unrelated arguments that a later change is
    free to source from somewhere fresher, which is the exact mistake #271
    warns about (`epoch` read at collection instead of at snapshot).
    """

    epoch: int
    scene: dict
    ledger: list
    facts: dict
    transcript: str
    messages: list
    watermark: dict


class _ReviewCancelledError(Exception):
    """The reviewer dismissed this review while the run was still preparing it."""


class _SceneMovedError(Exception):
    """The scene this run captured is no longer the scene at that id."""


#: How many times a terminal persist waits out a contended campaign lock.
#: `campaign_lock` already blocks for `LOCK_TIMEOUT`, so this is a second and
#: third full wait rather than a busy loop -- worth it here and nowhere else,
#: because what is being written is ten minutes of generation and there is no
#: copy of it anywhere: a `StoreBusy` swallowed at this one line costs the
#: whole absorb.
_PERSIST_ATTEMPTS = 3
_PERSIST_BACKOFF = 0.5


def _review_abandoned(run):
    """"Is anyone still waiting for this?", asked of the RUN rather than the socket.

    `abandoned=request.is_disconnected` was right when a disconnect could only
    mean the reviewer walked away. It is exactly wrong once a disconnect
    routinely means the screen locked, which is the whole of #396: the work now
    outlives the request that asked for it, and the only thing that means
    "nobody wants this any more" is the reviewer's own Cancel.

    Both flags, because they are different intents that want the same answer: a
    Stop on the run (`POST .../runs/{id}/cancel`) and a Discard of the review
    (`DELETE .../pending-review`). An awaitable, because that is the shape
    `_watched` and the phase loops already expect -- a bare bool would make
    `Abandoned` unreachable without a word of warning.
    """
    async def gone() -> bool:
        return run.review_cancelled or run.cancel_requested
    return gone


# `run_error` under its old local name: the `draft` routes need exactly the
# same translation, so it moved to `common` and this alias keeps the dozen call
# sites below reading as they did.
_run_error = run_error


def _cancelled_error() -> dict:
    return {"kind": "review_cancelled", "detail": _ABANDONED_REASON, "status": 409}


def _under_review_lock(cid: str, sid: str, run, write) -> None:
    """Run `write` under the campaign lock, fenced on the two things that make
    a terminal review write wrong rather than merely late.

    The cancellation check and the write are ONE hold, deliberately: split
    apart, the reviewer's Cancel lands between them and the run recreates the
    review they just dismissed. The identity check is in the same hold for the
    same reason -- a scene replaced between the two would receive this review.

    Retried on contention, and only here: everything else in this file answers
    a busy campaign with a 409 the caller can repeat, and this caller cannot --
    the payload exists nowhere else.
    """
    # `while` rather than `for`, so there is no falling out of it. A loop that
    # can end without either writing or raising would return `None` for a
    # review nobody wrote and nobody was told about, which is the worst outcome
    # this function has -- and a shape that cannot express it beats a comment
    # promising it will not happen.
    left = _PERSIST_ATTEMPTS
    while True:
        left -= 1
        try:
            with store.locks.campaign_lock(cid):
                # BOTH flags, exactly as `_review_abandoned` reads both. They
                # are different intents that want the same answer: a Discard of
                # the review (`DELETE .../pending-review`) and a Stop on the run
                # (`POST .../runs/{id}/cancel`). Reading only the first left the
                # second able to stop the generating and then publish anyway,
                # because a cancelled scope does not reach into a threadpool
                # worker that has already started this write.
                if run.review_cancelled or run.cancel_requested:
                    raise _ReviewCancelledError
                if streaming._scene_moved(cid, sid, run.scene_identity):
                    raise _SceneMovedError
                write()
                # The campaign's write token (#409), in the same hold as the
                # write it records. A review is the one durable campaign write
                # with no request behind it at the moment it lands: the route
                # that started it answered 202 and is marked `@computes_only`
                # (correctly -- at 202 nothing had been written yet), and the
                # run publishes minutes later with no response line for the
                # middleware to stamp. Without this, a token read before an
                # absorb landed stays current after it, and a clock advance or
                # a checkpoint priced against it proceeds as though the scene
                # had not gained a review at all.
                store.revision.bump(cid)
                return
        except store.locks.StoreBusy:
            if left <= 0:
                raise
            time.sleep(_PERSIST_BACKOFF)


def _accepted(run, generation: str) -> dict:
    """The 202 body: which run to poll, and which review it is preparing.

    The generation rather than only the run id, because Cancel is addressed to
    the REVIEW and not to one of the runs that build it -- an absorb and the
    retries of its phases all carry the same one.
    """
    return {"run": runs.run_payload(run), "generation": generation}


@router.post("/campaigns/{cid}/scenes/{sid}/absorb", status_code=202)
@computes_only  # every edit here is staged; PUT /chronicle is what persists them
def post_absorb(cid: str, sid: str, request: Request, force: bool = False,
                client: LLMClient = Depends(get_llm)):
    """Start the end-of-scene review. Answers 202 and a run to poll.

    Not a stream, so there is no `tail_response` here: End Scene is a form the
    reviewer reads, edits and saves, and the client wants "started, here is a
    run id" followed by polling. The result lands in `store.pending_reviews`,
    which is what survives the phone locking mid-absorb -- the failure this
    route exists to fix, and by user impact the worst one in the app, because
    an absorb is the longest single generation in it and putting the phone down
    while it runs is exactly what people do.

    **Reserved before the snapshot**, which is the ordering the whole design
    turns on -- see `runs.reserve_review`. The pre-flight refusals below then
    release the reservation through `runs.reservation`, so a scene refused for
    having nothing to absorb is not left holding its own exclusion key.
    """
    _campaign_root_or_404(cid)
    _require_scene(cid, sid)
    conn = _require_connection("absorb", cid)
    # Before the reservation and before a token of the budget: a scene whose id
    # predates the cap can be one the review sidecar's longer name will not fit
    # beside, and finding that out in `publish` costs the whole generation and
    # then costs it again on every retry. Refused with what to do about it.
    if not store.pending_reviews.name_usable(cid, sid):
        raise HTTPException(
            status_code=409,
            detail={"kind": "scene_id_too_long",
                    "detail": "this scene's id is too long for its review file — "
                              "rename the scene to something shorter, then end it"})
    generation = uuid.uuid4().hex
    run = runs.reserve_review(request.app, cid, sid, "absorb", generation)
    with runs.reservation(request.app, run):
        return _absorb_start(cid, sid, force, request, client, conn, run, generation)


def _absorb_start(cid: str, sid: str, force: bool, request: Request,
                  client: LLMClient, conn: dict, run, generation: str) -> dict:
    """Everything an absorb decides synchronously, then the handoff.

    Split out so `runs.reservation` can wrap every exit: the run is
    discoverable from the moment it is reserved, and a path that returns or
    raises in here without handing a producer to the runner would leave it
    `running` for the life of the process -- refusing every later turn and
    review on the scene.
    """
    epoch, scene, ledger = _absorb_snapshot(cid, sid, run.scene_identity)
    if not scene["messages"]:
        raise HTTPException(status_code=400, detail="nothing to absorb")
    # Absorb is not idempotent: lore edits append and plot movements add a beat,
    # so a second pass over the same scene duplicates both. Refuse by default
    # (before spending a token) and make the re-run an explicit choice (#235).
    if _already_absorbed(scene) and not force:
        raise HTTPException(
            status_code=409,
            detail={"detail": "this scene has already been absorbed", "kind": "already_absorbed"})
    # The same snapshot the review is built from, digested. Validated again
    # when the review is retrieved and again at save: once this run lands its
    # exclusion slot is released and the composer re-enables, so the player can
    # keep playing while the review sits on disk -- and none of that advances
    # the commit epoch (`commits.reserve` is called from exactly one place),
    # so the token alone would let a summary of a transcript that has moved
    # save with every check returning green.
    facts = store.chronicle.scene_facts(cid, sid)
    transcript = store.chronicle.transcript_text(scene["messages"])
    prepared = _Prepared(
        epoch=epoch, scene=scene, ledger=ledger, facts=facts, transcript=transcript,
        messages=store.absorb.build_prompt(
            transcript, facts,
            store.absorb.state_snapshot(cid, sid),
            store.absorb.relationships_snapshot(cid, sid),
            store.absorb.plot_snapshot(cid), store.absorb.group_snapshot(cid),
            store.absorb.commitment_snapshot(cid), store.absorb.fact_snapshot(cid, sid)),
        watermark=store.pending_reviews.watermark(scene["messages"]))

    async def work():
        return await _absorb_work(cid, sid, client, conn, run, generation, prepared)

    runs.start_computing(request.app, run, work)
    return _accepted(run, generation)


async def _absorb_work(cid: str, sid: str, client: LLMClient, conn: dict, run,
                       generation: str, prepared: _Prepared) -> dict:
    """The four phases, and the durable write that is this run's whole value."""
    abandoned = _review_abandoned(run)
    budget = _Budget(store.config.absorb_budget())
    try:
        # All four phases AT ONCE. Nothing here ever needed the one before it:
        # `_run_audit` re-reads the scene and transcript itself and never
        # touches `parsed`, and both per-NPC phases take only `transcript`,
        # captured from the snapshot above -- so what read as a pipeline was
        # only ever a fan-out written as a chain.
        #
        # The extraction is first in the list so it claims the first semaphore
        # slot: it is the one phase whose failure is fatal, so it must never be
        # the one left queued.
        #
        # Each phase resolves its OWN connection (#142): they are four
        # different routes -- extraction, the per-NPC dossier loop, voice drift
        # and the mechanics audit -- and sharing one `conn` would make three of
        # those four settings do nothing whenever an absorb was what ran them.
        #
        # All three resolved HERE, before the meter opens and before a single
        # coroutine is built. Resolved inline in the `_gather_phases(...)`
        # argument list instead, a failure from the third would leave the first
        # two coroutines created and never awaited.
        #
        # And resolved through `_phase_connection`, which reports instead of
        # raising: these three promise never to fail an absorb, so a route
        # pointing one of them at a keyless connection has to come back as that
        # phase's status rather than as a 409 that discards the extraction's
        # result too.
        dossier_conn, dossier_why = _soft_connection(
            lambda: _require_connection("dossier", cid))
        voice_conn, voice_why = _soft_connection(
            lambda: _require_connection("voice-drift", cid))
        audit_conn, audit_why = _soft_connection(
            lambda: _require_connection("audit", cid))
        with store.usage.meter("absorb", campaign=cid, scene=sid) as m:
            # ONE race, around the whole fan-out, rather than a predicate
            # threaded into each phase. Two reasons, and the second is the one
            # that decided it:
            #
            # * it covers the extraction, which has no per-NPC loop to check in
            #   and is the phase most likely to be the one wedged;
            # * it changes nothing about how the phases are scheduled.
            #   `_watched` puts its subject on the loop as a task of its own,
            #   and wrapping each phase individually inserts a hop before its
            #   first statement -- long enough, with a tight budget, for a
            #   sibling to spend the clock the extraction was about to read.
            #
            # `_gather_phases` already cancels and detaches its children when
            # the task holding it is cancelled, which is exactly what
            # `_watched` does to it here.
            results = await _watched(_gather_phases(
                budget.run(client.complete(prepared.messages, conn, m.usage),
                           on_timeout=_noting(client, conn, m.usage)),
                _stage_dossiers(cid, sid, prepared.transcript, client, dossier_conn,
                                budget, unroutable=dossier_why),
                _stage_voice_drift(cid, sid, prepared.transcript, client, voice_conn,
                                   budget, unroutable=voice_why),
                _run_audit(cid, sid, client, audit_conn, budget, unroutable=audit_why),
                limit=store.config.absorb_concurrency()), abandoned)
        text, dossier_result, voice_result, audit_result = results
        if isinstance(text, BaseException):
            # Only the extraction is fatal, and a budget overrun on it is
            # included: nothing has been produced yet, so there is nothing to
            # degrade to. The other three never raise for absorb except to
            # report abandonment (each has its own failure boundary), so
            # anything else from one of them is a bug in that boundary rather
            # than a state to report -- `_phase_or_raise` says so.
            raise text
        parsed = store.absorb.parse_output(text)
        # Both halves come from the SAME snapshot, and for the same reason: a
        # reroll or an append landing while the call was in flight would
        # otherwise have the citations (#112) judged against text the model
        # never saw, and promotion (#121) measure a ledger this review does not
        # summarize.
        edits = store.absorb.materialize(cid, sid, parsed, prepared.scene["messages"],
                                         turn_ledger=prepared.ledger)
        # Unpacked in the order the phases were listed, not the order they
        # finished, so `edits` reads the same way every time.
        dossier_edits, dossiers = _phase_or_raise(dossier_result)
        voice_edits, voice = _phase_or_raise(voice_result)
        audit_edits, mechanics = _phase_or_raise(audit_result)
    except Abandoned:
        # The reviewer cancelled while this was generating. The record is
        # already gone (the DELETE removed it under the lock that flagged this
        # run), so there is nothing to write and nothing to report but the
        # state.
        return {"state": "cancelled", "error": _cancelled_error()}
    except LLMError as exc:
        return {"state": "failed", "error": _run_error(_llm_http_error(exc))}
    edits += dossier_edits
    edits += voice_edits
    staged = edits + audit_edits
    review = {
        "one_line": parsed["one_line"], "summary": parsed["summary"],
        "keywords": parsed["keywords"], "timeline_events": parsed["timeline_events"],
        **prepared.facts, "edits": staged, "mechanics": mechanics,
        # Which staged rows a LATER scene has already answered differently
        # (#78). Empty for the ordinary case -- absorbing the newest scene,
        # which has no later scene to disagree with -- so the pass is
        # unconditional rather than a mode the caller has to know to ask
        # for, and it lights up exactly where it matters: a re-extraction
        # after a retcon of an old scene.
        "contradictions": _contradictions(cid, sid, staged),
        "dossiers": dossiers, "voice": voice,
        # One uniform row per step so a short absorb is legible as one
        # (see _phase_report) rather than as a model with nothing to say.
        "phases": _phase_report(dossiers, voice, mechanics),
        # Idempotency key for the save this review will become (#235): the
        # commit appends in six places, so a replay whose first response was
        # lost must return that result rather than apply it again. It also
        # carries the scene's commit epoch as captured at the TOP of this
        # run -- what tells a save that some OTHER review of the scene
        # committed while this one was being prepared or sat open (#271).
        "commit_token": store.commits.mint(prepared.epoch)}
    return await _persist_review(
        cid, sid, run, generation,
        lambda: store.pending_reviews.publish(cid, sid, generation, review, prepared.watermark))


async def _persist_review(cid: str, sid: str, run, generation: str, write,
                          result: dict | None = None) -> dict:
    """The terminal write shared by an absorb and by both retries.

    Off the event loop, because the acquire blocks and this runs on the
    lifespan's loop: a campaign held by a save would otherwise stall every
    unrelated request and open stream in the process rather than just this
    review.
    """
    try:
        await run_in_threadpool(_under_review_lock, cid, sid, run, write)
    except _ReviewCancelledError:
        return {"state": "cancelled", "error": _cancelled_error()}
    except _SceneMovedError:
        return {"state": "failed", "error": {
            "kind": "scene_gone", "status": 404,
            "detail": "the scene this review was prepared for is gone"}}
    except store.pending_reviews.NoPendingReviewError:
        return {"state": "failed", "error": {
            "kind": "review_missing", "status": 409,
            "detail": "there is no review to fold this into any more"}}
    except store.pending_reviews.ReviewReplacedError:
        return {"state": "failed", "error": {
            "kind": "review_replaced", "status": 409,
            "detail": "a newer review of this scene replaced the one this was retrying"}}
    except (OSError, store.locks.StoreBusy, store.pending_reviews.CorruptReviewError) as exc:
        return {"state": "failed", "error": {
            "kind": "busy", "status": 409,
            "detail": f"the review could not be stored: {exc}"}}
    return {"state": "landed",
            "result": {"generation": generation, "sid": sid, **(result or {})}}


@router.get("/campaigns/{cid}/scenes/{sid}/pending-review")
def get_pending_review(cid: str, sid: str) -> dict:
    """The review waiting to be saved for this scene, if there is one.

    `{"review": None}` rather than a 404 for a scene with none: that is the
    ordinary case on every mount, and an error there would make every quiet
    scene look broken -- the same call `GET .../run` makes.

    **The watermark is checked here, under the campaign lock, and the review is
    withheld when it fails.** Once the absorb landed the scene stopped being
    held, so the player can have appended turns, cut posts or retconned since;
    none of that advances the commit epoch, so the review's token would still
    pass its supersession check and the save would mark the scene absorbed with
    a summary of posts that are no longer there. Saying so on the way in is
    what lets the panel offer "re-run" instead of letting the reviewer fill in
    a form that is going to be refused.

    A review whose token has ALREADY committed is withheld too, and cleaned up
    on the way past. `_clear_pending_review` is deliberately non-fatal -- a save
    that landed must not be turned into a 500 by a sidecar that will not unlink
    -- so a record can outlive the commit it belongs to, and nothing else here
    would notice: `mark_absorbed` writes frontmatter and the watermark digests
    messages, so the transcript still matches and the stale review reads as
    perfectly current. The reviewer reloads and is handed a form for work that
    is already in the chronicle.
    """
    with store.locks.campaign_lock(cid):
        scene = _require_scene(cid, sid)
        try:
            record = store.pending_reviews.read(cid, sid)
        except store.pending_reviews.CorruptReviewError as exc:
            # NOT "no review". A file that will not parse cannot be repaired by
            # asking again, and an empty panel reads as "the absorb never
            # happened" -- inviting the reviewer to spend the whole budget a
            # second time rather than telling them what is wrong.
            raise HTTPException(status_code=409, detail={
                "kind": "review_unreadable",
                "detail": f"the stored review could not be read: {exc}"}) from exc
        except OSError as exc:
            raise HTTPException(status_code=409, detail={
                "kind": "busy",
                "detail": f"the stored review could not be read: {exc}"}) from exc
        answer: dict = {"review": None, "generation": None, "stale": None}
        if record is not None and _already_committed(cid, record):
            # Under the same hold as the read, so nothing serves it in between.
            _clear_pending_review(cid, sid,
                                  record["review"].get("commit_token") or "")
            record = None
        if record is not None:
            mark = store.pending_reviews.watermark(scene["messages"])
            prepared = record.get("watermark") or {}
            answer["generation"] = record.get("generation")
            if mark == prepared:
                answer["review"] = record["review"]
            else:
                answer["stale"] = {"prepared_posts": prepared.get("count"),
                                   "current_posts": mark["count"]}
    return answer


def _already_committed(cid: str, record: dict) -> bool:
    """Whether this record's save has already landed.

    `done`, not merely present: an entry is reserved before the writes and
    marked done after them, and the in-between state is a commit that began and
    may yet be resumed by a retry carrying the same token -- which needs the
    record it is resuming, not a panel told there is nothing here.

    A caller-minted token that this app never reserved is simply unseen, which
    is the same answer as "not committed" and the right one.
    """
    entry = store.commits.lookup(cid, record["review"].get("commit_token") or "")
    return bool(entry and entry.get("done"))


@router.delete("/campaigns/{cid}/scenes/{sid}/pending-review")
def delete_pending_review(cid: str, sid: str, request: Request,
                          generation: str = Query(...)) -> dict:
    """Discard this scene's pending review, and stop whatever is still making it.

    **The run is flagged before the record is deleted, both under one hold.**
    Left unordered the two lose to each other: the reviewer cancels while an
    absorb is finishing, the DELETE lands, and the runner then publishes and
    recreates the review they just dismissed. A cancelled review that reappears
    minutes later is worse than one that was never saved.

    Idempotent, and named by generation rather than by run id: a DELETE for a
    generation that has already gone flags nothing, removes nothing and reports
    success, because the reviewer's intent is satisfied either way.

    **It answers only once the runs it flagged have really stopped**, and the
    wait is outside the lock. Answering earlier would let the caller's next act
    -- which is usually "end the scene again" -- race a retry that is still
    unwinding, and be refused with `run_in_flight` by a review it has just
    discarded. Cancel means stopped, the way it does for a turn.
    """
    _require_scene(cid, sid)
    removed, refused = False, None
    with store.locks.campaign_lock(cid):
        # The flag lands FIRST and stays landed even if the removal below
        # refuses: stopping the work is the half of Cancel that cannot be
        # retried into existence later, and a record that outlives its runs is
        # refused by its own watermark at save.
        flagged = runs.cancel_review(request.app, cid, sid, generation)
        try:
            removed = store.pending_reviews.clear(cid, sid, generation)
        except OSError as exc:
            # Carried OUT of the hold rather than raised inside it. The wait
            # below must happen outside the campaign lock -- a flagged run
            # reaches its terminal persist through that same lock, so waiting
            # for it while holding it is a deadlock with a thirty-second fuse
            # per run. There is exactly one `await_reviews_stopped` call in
            # this route for that reason, and it is after the `with`.
            refused = exc
    stopped = runs.await_reviews_stopped(flagged)
    if refused is not None:
        # Not a 500: `clear` declines to remove a record it could not identify
        # (the safe direction), and the condition is transient -- a sync client
        # holding the file, a sharing violation -- so the client is told to
        # retry rather than shown a crash for a button that half worked. The
        # runs it flagged are stopped either way, which is the half that
        # cannot be retried into existence later.
        raise HTTPException(status_code=409, detail={
            "kind": "busy",
            "detail": f"the review could not be removed: {refused}"}) from refused
    return {"removed": removed, "stopped": stopped}


def _pending_for_retry(cid: str, sid: str) -> dict:
    """The review a scoped retry is about to re-run one phase of.

    Refused up front rather than at the terminal write, and before a token is
    spent: a retry with nothing to merge into can only be dropped, and a retry
    of a review whose transcript has moved would be folded into a review the
    save is going to refuse anyway.
    """
    try:
        record = store.pending_reviews.read(cid, sid)
    except store.pending_reviews.CorruptReviewError as exc:
        raise HTTPException(status_code=409, detail={
            "kind": "review_unreadable",
            "detail": f"the stored review could not be read: {exc}"}) from exc
    except OSError as exc:
        raise HTTPException(status_code=409, detail={
            "kind": "busy",
            "detail": f"the stored review could not be read: {exc}"}) from exc
    if record is None:
        raise HTTPException(status_code=409, detail={
            "kind": "review_missing",
            "detail": "there is no open review for this scene to retry a step of"})
    scene = _require_scene(cid, sid)
    mark = store.pending_reviews.watermark(scene["messages"])
    if mark != record.get("watermark"):
        # The same refusal the retrieval and the save give, and it belongs here
        # too rather than only there: a phase folded into a review the save is
        # going to refuse anyway is the most expensive way to reach that
        # answer, and the reviewer would be shown a freshly-retried panel that
        # cannot be committed.
        raise HTTPException(status_code=409, detail={
            "kind": "review_stale",
            "detail": "the scene changed after this review was prepared — "
                      "re-run End Scene to review what is there now",
            "prepared_posts": (record.get("watermark") or {}).get("count"),
            "current_posts": mark["count"]})
    return record


def _retry_start(cid: str, sid: str, request: Request, kind: str, work_for) -> dict:
    """Reserve a `review` run for a scoped retry and hand it its work.

    The generation comes from the STORED review, not from this request: a retry
    belongs to the review it is retrying, so Cancel on that review stops it and
    a fresh absorb landing in the meantime makes its merge refuse rather than
    fold one review's phase into another's.
    """
    # ONE hold across the check and the reservation. Cancel flags and deletes
    # under this same lock, so split apart there is a window where the record
    # is read, the DELETE removes it and flags nothing (no run exists yet), and
    # this then reserves a run for a review that is gone: it holds the scene
    # and spends the whole retry budget before ending in `review_missing`, and
    # aborting the browser's request stops none of that. Reentrant, so
    # `reserve_review`'s own acquisition costs nothing.
    with store.locks.campaign_lock(cid):
        record = _pending_for_retry(cid, sid)
        generation = record.get("generation") or ""
        run = runs.reserve_review(request.app, cid, sid, kind, generation)
    with runs.reservation(request.app, run):
        runs.start_computing(request.app, run, work_for(run, generation))
        return _accepted(run, generation)


@router.post("/campaigns/{cid}/scenes/{sid}/audit", status_code=202)
@computes_only  # a retry of absorb's audit step alone: same staged edits, same nothing written
def post_audit(cid: str, sid: str, request: Request,
               client: LLMClient = Depends(get_llm)):
    """Standalone audit retry: re-runs ONLY the audit step (never the prose
    absorb), returning fresh `expect` values on any resulting sheet edits.

    Detached like its absorb (#396), and merged into the stored review rather
    than returned whole: this answers `{mechanics, edits}`, a *part* of a
    review, and writing that part over the record would destroy the absorb's
    prose, its staged edits and its commit token -- the token being the piece
    nothing else can reconstruct.
    """
    _require_scene(cid, sid)
    conn = _require_connection("audit", cid)
    if store.modules.resolve(cid) is None:
        raise HTTPException(status_code=400, detail="no module resolved")

    def work_for(run, generation):
        async def work():
            # A retry gets its own budget — it never inherits the deadline of
            # whatever absorb ran out of time earlier.
            try:
                edits, mechanics = await _run_audit(
                    cid, sid, client, conn, _Budget(store.config.absorb_budget()),
                    abandoned=_review_abandoned(run))
            except Abandoned:
                return {"state": "cancelled", "error": _cancelled_error()}
            result = {"mechanics": mechanics, "edits": edits}
            return await _persist_review(
                cid, sid, run, generation,
                lambda: store.pending_reviews.merge(
                    cid, sid, generation,
                    lambda review: store.pending_reviews.merge_audit(review, result)),
                result=result)
        return work

    return _retry_start(cid, sid, request, "audit", work_for)


# ---- the live rolling summary (#85) ----
# A reading aid for the scene being PLAYED, where every other summary grimoire
# holds describes a scene that has ended. Display-only: it is deliberately not
# injected into the scene context (see the design spec), so nothing here can
# change what the model is told on the next turn.

# Scenes with a refresh already at the provider. The stored coverage does not
# advance until a call RETURNS, so without this every turn landing during a slow
# completion passes the same due check and starts its own call: one threshold
# crossing, billed several times. `_rolling_commit`'s supersession check stops
# the duplicate WRITE, but only after both calls have been paid for, and paying
# is the part that matters here.
#
# In-process only, which is the same scope `streaming._turn_tokens` documents
# for the same reason: this distinguishes two refreshes racing inside one
# backend, which is what one player generates. Two *processes* on one scene is
# the case `store/locks.py` already calls beyond what this app can serialize.
#
# A real lock rather than `_claim_turn`'s bare-dict-under-the-GIL idiom, because
# this is a check-then-add and that is two bytecodes: `next()` and one assignment
# are individually atomic, `key not in s` followed by `s.add(key)` is not.
_rolling_inflight: set[tuple[str, str]] = set()
_rolling_inflight_guard = threading.Lock()
# How a forced refresh waits out a fold already at the provider (`_rolling_wait`).
# Polled rather than signalled: the claim is a plain set behind a threading lock,
# shared with whatever thread a sync route runs on, and a poll keeps it that way
# instead of introducing a per-scene asyncio primitive bound to one loop.
_ROLLING_WAIT_POLL = 0.05
# How long a forced refresh may be held open waiting, in wall-clock seconds.
#
# Its OWN number, deliberately not `llm_timeout`. Review caught the first
# version borrowing that one on the reasoning that a wait could then only run
# out after the call it waited for was itself entitled to give up -- which is
# false: `llm._guard` is an *idle* bound that resets after every provider frame,
# and says so ("deliberately an *idle* bound, not a total one. Callers that need
# a ceiling on total duration impose it themselves"). A reasoning-heavy
# completion can stream frames for many minutes while perfectly healthy, so
# borrowing that value would 503 a forced refresh whose fold was progressing
# fine. This is the ceiling `_guard`'s docstring is talking about, and what it
# actually measures is how long a browser request may hang -- `absorb_budget` is
# the same shape of decision for the same reason.
_ROLLING_WAIT_CEILING = 120.0


@contextlib.contextmanager
def _rolling_claim(cid: str, sid: str):
    """Yield whether this refresh may go to the provider, releasing on the way
    out however it leaves.

    The release is in a `finally` and gated on having claimed, so a failed call
    -- a provider error, a cancelled request, a raised anything -- frees the
    scene. A claim that outlived its request would wedge that scene's summary for
    the life of the process, which is strictly worse than the duplicate call this
    prevents.
    """
    key = (cid, sid)
    with _rolling_inflight_guard:
        claimed = key not in _rolling_inflight
        if claimed:
            _rolling_inflight.add(key)
    try:
        yield claimed
    finally:
        if claimed:
            with _rolling_inflight_guard:
                _rolling_inflight.discard(key)
def _rolling_view(cid: str, sid: str, scene: dict, facts: dict) -> dict:
    """The stored summary reconciled against the transcript as it stands now.

    Three things come out of this, and keeping them apart is the point:

    - `summary` is what is stored, ALWAYS, however stale. A summary whose posts
      were rerolled still describes most of the scene, and it is the best thing
      anyone has until the next refold; blanking it would leave the panel saying
      "no summary yet" about a scene that has one. Review caught that: it also
      made the panel's own staleness warning unreachable, since the warning
      renders beside prose there was then never any of.
    - `prior` is what a fold may build ON, which is `summary` only while the
      prefix it covered is still the prefix on disk. Otherwise "" -- start over
      from the whole transcript rather than carry prose about a post the player
      deleted.
    - `stale` is that same finding said out loud, so the panel can present the
      summary as behind rather than as current.

    An unsummarized scene is not stale: it has nothing to be stale about, and
    saying otherwise would put a warning on every scene nobody has summarized.
    """
    messages = scene["messages"]
    total = len(messages)
    # Out of the snapshot this was handed, NOT a second read of the same file.
    # Review caught that reading twice pairs one snapshot's transcript with
    # another's metadata: a commit landing between the two yields `at: 11`
    # against a 10-message transcript, and reports a summary that is exactly
    # current as stale -- a contradiction the panel then holds until something
    # else refreshes it.
    stored = store.scenes.rolling_summary_fields(scene["meta"])
    # `at > total` short-circuits rather than slicing: `messages[:at]` past the
    # end yields the whole list and would digest-match a transcript that has
    # since been trimmed back to exactly what it covered.
    #
    # BOTH digests, because a summary can go stale two ways and only one of them
    # is visible in the transcript. The facts half is the price of putting facts
    # in the prompt at all: a scene's first location and first date are set
    # SILENTLY, so they can change with no message appended, and a summary built
    # from the wrong location is as stale as one built from a deleted post.
    #
    # Coverage without prose is not coverage, which is why the emptiness test
    # comes first rather than being left to `prior` alone. `rolling_at` means
    # "how much of the transcript this summary describes", so with no summary it
    # describes nothing however well its digests still check out -- and every
    # store here is a hand-editable markdown tree, so a scene whose
    # `rolling_summary:` line was blanked while `rolling_at`/`rolling_digest`
    # survive is a file someone can actually produce. Review caught what that
    # cost: `prior` was correctly "" and `base` was NOT, so the fold took the
    # from-scratch branch of the prompt and was handed only `messages[base:]` --
    # a summary of the tail, written as if it were the whole scene, then stored
    # as covering all of it. The `upto` overtaken check below already tested
    # `ahead["summary"]` for this reason; this puts the rule in one place.
    has_summary = bool(stored["summary"])
    intact = (has_summary
              and stored["at"] <= total
              and store.rolling_summary.covered_digest(messages[:stored["at"]])
              == stored["digest"]
              and store.rolling_summary.facts_digest(facts) == stored["facts"])
    return {"summary": stored["summary"],
            # Reported as 0 for the same reason, so the panel cannot say
            # "covers 12 of 14 posts" beside "no summary yet".
            "at": stored["at"] if has_summary else 0, "total": total,
            "stale": has_summary and not intact,
            "prior": stored["summary"] if intact else "",
            "base": stored["at"] if intact else 0}


def _rolling_due(view: dict, every: int, force: bool) -> bool:
    """Whether a refresh is worth an LLM call.

    Server-side rather than in `CampaignView`, so the policy has one home and is
    exercised by this suite: the client fires after every turn and this decides.

    `pending > 0` guards both callers. Automatic refreshes cannot reach here
    with nothing pending, but *force* can -- the panel's button on a
    already-current scene -- and folding an empty list of posts onto a summary
    would pay a provider to restate it.
    """
    pending = view["total"] - view["base"]
    if pending <= 0:
        return False
    return force or (every > 0 and pending >= every)


def _rolling_commit(cid: str, sid: str, summary: str, covered: int, digest: str,
                    facts_key: str) -> dict:
    """Store a fold only if the prefix it was computed FROM is still the prefix
    on disk, and report the state that results either way.

    Read-verify-write under ONE campaign hold (reentrant, so the mutator's own
    acquisition is free). The verification is the whole point, and review caught
    that "self-healing on the next refresh" was not enough for two reasons:

    - The panel's Refresh button trusts this route's answer directly, so a
      summary stored over a transcript that changed during the call would be
      presented as CURRENT until some later GET happened to notice.
    - `delete_scene` frees a scene's id and the numbering reuses it, so a scene
      deleted and remade under the same title mid-call hands this write the very
      id it holds -- attaching one scene's prose to another. On an empty
      replacement not even Refresh clears it: there is nothing pending for a
      forced refold to fold.

    Both are the same question, so one check answers them: is `messages[:covered]`
    still what it was? A recycled scene fails it (different transcript, usually
    none), an edit or reroll inside the covered prefix fails it, a trim fails it
    -- and an ordinary turn APPENDING during the call passes, which it must, or
    every busy scene would throw away the summary it just paid for.
    """
    with store.locks.campaign_lock(cid):
        scene = store.scenes.read_scene(cid, sid)
        facts = store.chronicle.scene_facts(cid, sid)
        stored = _rolling_view(cid, sid, scene, facts)
        # Two independent refusals, and review found the second one after the
        # first was in place, because they fail on opposite facts.
        #
        # The prefix must be intact -- the fold describes those messages.
        intact = store.rolling_summary.covered_digest(
            scene["messages"][:covered]) == digest
        # ...the facts must be the facts it was given, for the same reason the
        # prefix must be the prefix. Review caught that adding facts to the
        # stored validity key did not, on its own, make them a PRECONDITION: a
        # first location assigned while the model was answering is a silent
        # write, so the message digest stays intact and prose generated without
        # that location would land stamped with the old facts key -- immediately
        # stale on the very next read, with no refold scheduled to repair it.
        facts_intact = (store.rolling_summary.facts_digest(facts) == facts_key)
        # ...and nothing at least as complete may already be stored. Two
        # background refreshes can overlap, and the newer one can finish first:
        # its extra posts are all APPENDED, so the older one's prefix is still
        # perfectly intact and passes the check above. Writing then regresses
        # `rolling_at` from twelve back to ten, showing the less complete prose
        # as current and pulling the next automatic refresh early. Coverage only
        # ever moves forward while the stored summary is itself valid; a stale
        # stored summary is no bar, since re-folding over it is the repair.
        superseded = bool(stored["summary"]) and not stored["stale"] \
            and stored["at"] >= covered
        landed = intact and facts_intact and not superseded
        if landed:
            store.scenes.set_rolling_summary(cid, sid, summary, covered, digest,
                                             facts_key)
            # In the hold that covers the write, and only when it landed (#409).
            # This is reached two ways and one of them has no response line
            # behind it: the panel's Refresh button is a request the activity
            # middleware stamps, but `schedule_follow_ups` fires this as a
            # BACKGROUND run after every turn (#397), minutes after that turn's
            # own status line went out. A token read while the fold was running
            # would otherwise still be current once it had written.
            store.revision.bump(cid)
            scene = store.scenes.read_scene(cid, sid)
            facts = store.chronicle.scene_facts(cid, sid)
        return {"landed": landed, "view": _rolling_view(cid, sid, scene, facts)}


def _rolling_reread(cid: str, sid: str, fallback: dict) -> dict:
    """The scene's rolling state as it is right now, for a path that is about to
    answer without writing.

    Every answer this route gives AFTER the LLM call has to be reconciled, not
    asserted -- the panel's Refresh button renders it directly. `_rolling_commit`
    does that for the write path; this is the same duty for the early return
    that had nothing to write, where the pre-call snapshot would report
    `stale: false` about a summary that went stale during the call.

    A scene that vanished falls back: there is nothing left to reconcile
    against, and this call is fire-and-forget from the client anyway.
    """
    try:
        return _rolling_view(cid, sid, _require_scene(cid, sid),
                             store.chronicle.scene_facts(cid, sid))
    except HTTPException:
        return fallback


def _rolling_body(view: dict, every: int) -> dict:
    """`due` always answers the AUTOMATIC question — would a plain per-turn POST
    spend a call — never the forced one. A forced call that found nothing new
    would otherwise report `due: false` for an unrelated reason, and the panel
    reads this field to say when the next refresh is coming."""
    return {"summary": view["summary"], "at": view["at"], "total": view["total"],
            "stale": view["stale"], "every": every,
            "due": _rolling_due(view, every, force=False)}


@router.get("/campaigns/{cid}/scenes/{sid}/rolling-summary")
def get_rolling_summary(cid: str, sid: str):
    """Read the summary without ever spending a call.

    Needs no LLM connection on purpose: the inspector reads this on every scene
    select, and a store with no key configured must still render the panel.
    """
    scene = _require_scene(cid, sid)
    return _rolling_body(_rolling_view(cid, sid, scene,
                                       store.chronicle.scene_facts(cid, sid)),
                         store.config.rolling_summary_every())


@router.post("/campaigns/{cid}/scenes/{sid}/rolling-summary")
async def post_rolling_summary(cid: str, sid: str, force: bool = False,
                               upto: int | None = None,
                               client: LLMClient = Depends(get_llm)):
    """Refold the summary if enough has happened since the last one.

    Fired by the client after every turn and not awaited by it, so the ordinary
    outcome is `refreshed: false` having touched no provider. `force` is the
    panel's own Refresh button -- also the only way to reach the feature at all
    when `rolling_summary_every` is 0.

    The connection check runs BEFORE the due check, deliberately: a 409 that
    only appeared once a scene happened to be due would be indistinguishable, on
    the client, from the quiet no-op that is this route's normal answer.

    `upto` bounds the fold to a transcript the caller knows was a clean
    boundary. The play loop releases the scene before firing this, so a fast
    next send can append its player post before this request takes its snapshot
    -- and a fold that swallows an unanswered prompt does not self-repair: the
    eventual reply is an APPEND, which leaves the digest valid, so it stays out
    of the "current" summary until another whole threshold goes by. The client
    already knows how long the transcript was when its turn finished, so it says
    so. Omitted (the panel's own button, which is held while a turn streams) the
    scene is taken as it is.
    """
    # Two passes at most. The second only happens for a FORCED call that found
    # another fold already at the provider: it waits for that one, then starts
    # over from a fresh read, because everything below -- the scene, the facts,
    # the view, whether anything is still due -- may have moved while it waited.
    for attempt in (0, 1):
        answer = await _rolling_once(cid, sid, force, upto, client)
        if answer is not None:
            return answer
        # None means a FORCED call was coalesced (an automatic one answers for
        # itself inside). The player pressed a button that says "now", and
        # review caught that answering `refreshed: false` there let the panel
        # clear its busy state and report success while nothing had happened:
        # if that fold belongs to another tab, or fails and has its error
        # swallowed by the automatic caller that made it, the summary this tab
        # was promised never arrives and nothing says so.
        if attempt or not await _rolling_wait(cid, sid):
            raise HTTPException(
                status_code=503,
                detail="a refresh for this scene is already running")
    raise HTTPException(status_code=503,  # unreachable; the loop returns or raises
                        detail="a refresh for this scene is already running")


async def _rolling_wait(cid: str, sid: str) -> bool:
    """Wait for another request's fold on this scene to leave the provider.

    True if it freed, False if the wait ran out -- bounded rather than
    open-ended because this holds a request open, by a ceiling that is about
    exactly that and nothing else (see `_ROLLING_WAIT_CEILING`).

    Running out is not a claim that the fold failed, and the 503 does not say
    it did: it says one is already running, which stays true either way.
    """
    limit = _ROLLING_WAIT_CEILING
    waited = 0.0
    while waited < limit:
        with _rolling_inflight_guard:
            if (cid, sid) not in _rolling_inflight:
                return True
        await asyncio.sleep(_ROLLING_WAIT_POLL)
        waited += _ROLLING_WAIT_POLL
    return False


async def _rolling_once(cid: str, sid: str, force: bool, upto: int | None,
                        client: LLMClient) -> dict | None:
    """One evaluation of the whole route, from a fresh read of the scene.

    Returns the response, or None to mean "another fold for this scene is
    already at the provider" -- the one outcome the caller may retry, and the
    reason this is a function rather than a block: a retry has to redo the read
    and every decision that hangs off it, not just the claim.
    """
    scene = _require_scene(cid, sid)
    if upto is not None:
        if upto < 0:
            raise HTTPException(status_code=400, detail="upto must not be negative")
        # A bounded request that a NEWER fold has already overtaken is finished
        # before it starts, and this is checked against the unclamped scene
        # because clamping is what would hide it: stored coverage of 20 against a
        # transcript clamped to 10 reads as `at > total`, i.e. stale, which resets
        # `base` to zero and buys a whole-transcript refold -- one that
        # `_rolling_commit` then refuses as superseded, after the provider has
        # been paid. Review caught it there: the refusal was in the right place
        # to protect the STORE and the wrong place to protect the bill.
        ahead = _rolling_view(cid, sid, scene, store.chronicle.scene_facts(cid, sid))
        if ahead["summary"] and not ahead["stale"] and ahead["at"] >= upto:
            return {**_rolling_body(ahead, store.config.rolling_summary_every()),
                    "refreshed": False}
        # Clamped here and nowhere else: every decision below -- due, base,
        # digest, what gets folded, what `covered` records -- then works from the
        # same bounded transcript, rather than each having to remember the bound.
        scene = {**scene, "messages": scene["messages"][:upto]}
    conn = _require_connection("rolling-summary", cid)
    every = store.config.rolling_summary_every()
    facts = store.chronicle.scene_facts(cid, sid)
    view = _rolling_view(cid, sid, scene, facts)
    if not _rolling_due(view, every, force):
        return {**_rolling_body(view, every), "refreshed": False}

    with _rolling_claim(cid, sid) as claimed:
        if not claimed:
            # A refresh for this scene is already at the provider and will cover
            # these posts too. Claimed AFTER the due check on purpose: a POST
            # that was never going to spend anything has nothing to coalesce.
            # An automatic refresh is content to be coalesced -- the fold in
            # flight covers its posts too, and the client fires again after the
            # next transcript write. A forced one is not, and says so by
            # returning None for the caller to wait on and retry.
            if not force:
                return {**_rolling_body(view, every), "refreshed": False}
            return None
        return await _rolling_refresh(cid, sid, scene, view, every, conn, client,
                                      facts)


async def _rolling_refresh(cid: str, sid: str, scene: dict, view: dict, every: int,
                           conn: dict, client: LLMClient, facts: dict) -> dict:
    """The paid half of `post_rolling_summary`, split out so the in-flight claim
    brackets exactly the span that reaches the provider."""
    messages = scene["messages"]
    # The snapshot is what gets recorded, not a re-read afterwards: a turn
    # landing while the model is answering must not be counted as covered by a
    # summary that never saw it.
    covered, base = len(messages), view["base"]
    digest = store.rolling_summary.covered_digest(messages)
    prompt = store.rolling_summary.build_prompt(
        view["prior"], store.chronicle.transcript_text(messages[base:]), facts)
    try:
        with store.usage.meter("rolling-summary", campaign=cid, scene=sid) as m:
            text = await client.complete(prompt, conn, m.usage)
    except LLMError as exc:
        raise _llm_http_error(exc) from exc
    summary = store.rolling_summary.parse_output(text)
    if not summary:
        # A provider can return an empty completion. Storing it would blank a
        # summary the player can still read AND record it as covering the whole
        # scene, so the next refresh would fold new posts onto nothing and never
        # recover what was lost.
        #
        # Reconciled rather than answered from the pre-call snapshot: the
        # transcript may have moved under us during the call, and reporting
        # `stale: false` here would present a summary that just went stale as
        # current on a panel that renders this answer directly.
        return {**_rolling_body(_rolling_reread(cid, sid, view), every),
                "refreshed": False}
    # Off the event loop: this takes the campaign lock, whose acquisition blocks
    # for up to LOCK_TIMEOUT, and this handler is async -- inline it would freeze
    # every unrelated request and open stream on the backend rather than just
    # this refresh. Same treatment `post_absorb` and `streaming.py` give theirs.
    try:
        result = await run_in_threadpool(_rolling_commit, cid, sid, summary, covered,
                                         digest,
                                         store.rolling_summary.facts_digest(facts))
    except store.scenes.SceneNotFound:
        # The scene was renamed or deleted while the model was answering, which
        # mints a new id and moves the file out from under this write -- the
        # same race `streaming.py`'s `on_error` already catches, and reachable
        # here from the UI because the play loop fires this refresh AFTER
        # releasing the scene lock that holds rename off during a turn.
        #
        # Not an error to report: the summary describes a transcript that has
        # moved on, there is nowhere to put it, and this call is fire-and-forget
        # from the client. Left unhandled it is a 500 with no handler above it.
        return {**_rolling_body(view, every), "refreshed": False}
    # The reconciled view, never the snapshot: `_rolling_commit` re-reads under
    # its own hold, so this reports the scene as it actually is -- including the
    # posts that landed during the call, and including a refusal's `stale: true`
    # rather than the `stale: false` a just-written summary would otherwise
    # always claim. The panel's Refresh button renders this answer directly.
    return {**_rolling_body(result["view"], every), "refreshed": result["landed"]}


# ---- scene-break detection (#84) -------------------------------------------
#
# Heuristic first, model second, player last. `store.scene_break.evaluate` scores
# what the scene already records -- posts, moves, clock advances -- and only a
# crossing buys the one call that asks whether the story actually arrived
# somewhere. Nothing here ever ends or splits a scene: the answer is a
# suggestion in the inspector, and the only writes are the watermark that says
# what has been asked about and the proposal the player accepts or dismisses.


def _break_provider(cid: str):
    """This campaign's primary calendar, or None if it cannot be loaded.

    Only used to SIZE a time skip, so None is not a failure: the fact that the
    clock moved is in `time_history` regardless, and only the extra point for a
    long skip depends on measuring it. Swallowed rather than raised because a
    campaign with an unreadable calendar must still get the other two signals,
    and because this runs off the play loop where raising would put a banner
    over a scene mid-turn.
    """
    root = _campaign_root_or_404(cid)   # outside the try: a 404 is not a calendar failure
    try:
        return store.calendars.primary_provider(root)
    except Exception:  # noqa: BLE001 -- user-authored provider code, hand-edited calendar.json
        return None


def _break_intact(messages: list[dict], stored: dict) -> bool:
    """Whether a stored watermark still describes the transcript on disk.

    `_rolling_view`'s check, for `_rolling_view`'s reason and with its own
    tool: the transcript is not append-only, so "we have already asked about
    the first thirty posts" stops being true the moment one of those thirty is
    rerolled, edited away or trimmed. An APPEND leaves the prefix alone and
    keeps the watermark good, which it must, or an ordinary scene would forget
    every question it had ever been asked.

    `at > total` short-circuits rather than slicing: `messages[:at]` past the
    end yields the whole list, which would digest-match a transcript trimmed
    back to exactly what the watermark covered.

    A watermark with no digest at all is not intact. That is a scene written
    before this key existed (or hand-edited), and the safe reading of "we asked
    about thirty posts, but cannot say which" is that we did not.
    """
    return bool(stored["digest"]) and stored["at"] <= len(messages) \
        and store.rolling_summary.covered_digest(messages[:stored["at"]]) == stored["digest"]


def _break_view(scene: dict, every: int, provider) -> dict:
    """The scene's break state, scored out of ONE snapshot of the scene.

    The transcript, the watermark and both histories all come off the `scene`
    handed in rather than from fresh reads, for `_rolling_view`'s reason and a
    sharper version of it: the histories and the transcript move TOGETHER (a
    move appends its transition line in the same mutator that extends
    `location_history`), so reading them separately can produce a location move
    counted against a transcript that does not contain the post announcing it.

    A watermark whose prefix moved is VOID, not merely old: the scene is scored
    from zero, which is the same answer the rolling summary gives a fold whose
    ground moved. Review caught what carrying it forward instead did -- a scene
    rewound from thirty posts to ten and played back up to twenty-five reported
    nothing new for fifteen posts of real story, and went on reporting nothing
    until the count passed thirty again.
    """
    stored = store.scenes.scene_break_fields(scene["meta"])
    intact = _break_intact(scene["messages"], stored)
    history = store.scenes.histories(scene["meta"])
    scored = store.scene_break.evaluate(
        scene["messages"], history["locations"], history["times"],
        stored if intact else {"at": 0, "locs": 0, "times": 0}, every, provider)
    return {**scored, "stored": stored, "intact": intact}


def _break_body(view: dict, every: int) -> dict:
    """What both scene-break routes answer with.

    `verdict` is a tri-state string rather than a boolean: `""` is "nothing has
    been asked, or the last answer was dismissed", which is a different thing
    from "asked, and the model said no" -- and the panel says different things
    about them.

    `due` always answers the AUTOMATIC question, never the forced one, for
    `_rolling_body`'s reason: the panel reads this field to say when the next
    question is coming, and a forced call that found nothing new would
    otherwise report `due: false` about a different question entirely.
    """
    stored = view["stored"]
    return {"verdict": stored["verdict"], "reason": stored["reason"],
            "title": stored["title"],
            # A standing answer whose watermark was voided describes a
            # transcript that no longer exists -- the posts it reasoned about
            # were rerolled, edited or cut. `_rolling_body`'s `stale`, for its
            # reason: the panel may still show the prose, it may just not
            # present it as current. A scene with no answer is not stale; it has
            # nothing to be stale about.
            "stale": bool(stored["verdict"]) and not view["intact"],
            "posts": view["posts"], "score": view["score"],
            "signals": view["signals"], "every": every, "due": view["due"]}


@router.get("/campaigns/{cid}/scenes/{sid}/scene-break")
def get_scene_break(cid: str, sid: str):
    """Read the score and the standing proposal without ever spending a call.

    Needs no LLM connection on purpose, like the rolling-summary GET: the
    inspector reads this on every scene select, and a store with no key
    configured must still render the panel.
    """
    scene = _require_scene(cid, sid)
    every = store.config.scene_break_every()
    return _break_body(_break_view(scene, every, _break_provider(cid)), every)


@router.post("/campaigns/{cid}/scenes/{sid}/scene-break")
async def post_scene_break(cid: str, sid: str, force: bool = False,
                           upto: int | None = None,
                           client: LLMClient = Depends(get_llm)):
    """Ask the model whether the scene has reached a place to stop.

    Fired by the client after every turn and not awaited by it, so the ordinary
    outcome is `asked: false` having touched no provider -- the heuristic
    declined. `force` is the panel's own button, and also the only way to reach
    the feature when `scene_break_every` is 0.

    The connection check runs BEFORE the due check, deliberately and for
    `post_rolling_summary`'s reason: a 409 that only appeared once a scene
    happened to be due would be indistinguishable, on the client, from the quiet
    no-op that is this route's normal answer.

    `upto` bounds the question to a transcript the caller knows was a clean
    boundary, exactly as the rolling summary does: the play loop releases the
    scene before firing this, so a fast next send can append an unanswered
    player post, and a question that took that post as the scene's END would be
    asking about a beat whose reply had not arrived.

    No in-flight coalescing, unlike the rolling summary, and the difference is
    the write rather than the call: two overlapping folds regress a summary's
    coverage if the older one lands second, where two overlapping questions
    produce two answers about nearly the same transcript and the later write
    simply wins. `_break_commit` still refuses a write whose transcript moved
    underneath it, which is the case that actually corrupts.
    """
    return await _break_once(cid, sid, force, upto, client)


async def _break_once(cid: str, sid: str, force: bool, upto: int | None,
                      client: LLMClient) -> dict:
    """The whole of the route above, from a fresh read of the scene.

    Split out for `_rolling_once`'s second reason rather than its first:
    there is no coalesced retry to redo the read for here, but there is a
    second caller. A landed turn asks this question itself now (#397), and
    that caller has no request to resolve `Depends` against -- so what it
    drives has to be a plain function of the same four decisions the route
    makes, or the two callers would disagree about what is due."""
    scene = _require_scene(cid, sid)
    if upto is not None:
        if upto < 0:
            raise HTTPException(status_code=400, detail="upto must not be negative")
        scene = {**scene, "messages": scene["messages"][:upto]}
    conn = _require_connection("scene-break", cid)
    every = store.config.scene_break_every()
    provider = _break_provider(cid)
    view = _break_view(scene, every, provider)
    # `force` overrides the threshold, never the emptiness: a forced question
    # about a scene with nothing new since the last one would pay a provider to
    # answer the question it just answered.
    if not (view["due"] or (force and view["posts"] > 0)):
        return {**_break_body(view, every), "asked": False}
    return await _break_ask(cid, sid, scene, view, every, conn, client, provider)


async def _break_ask(cid: str, sid: str, scene: dict, view: dict, every: int,
                     conn: dict, client: LLMClient, provider) -> dict:
    """The paid half of `post_scene_break`, split out so the route above reads as
    the decision it is."""
    messages = scene["messages"]
    # Zero when the watermark was VOIDED, not the stale count it still holds.
    # Review caught the pair coming apart: a rewound scene scores from zero and
    # records a watermark covering the whole transcript, so slicing from the old
    # count would show the model the last ten posts while the answer went on
    # file as an answer about all fifty.
    base = min(view["stored"]["at"], len(messages)) if view["intact"] else 0
    facts = store.chronicle.scene_facts(cid, sid)
    prompt = store.scene_break.build_prompt(
        store.chronicle.transcript_text(messages[base:]), view["signals"], facts,
        scene["meta"].get("title", ""))
    try:
        with store.usage.meter("scene-break", campaign=cid, scene=sid) as m:
            text = await client.complete(prompt, conn, m.usage)
    except LLMError as exc:
        raise _llm_http_error(exc) from exc
    answer = store.scene_break.parse_output(text)
    # The prefix the question was asked ABOUT, digested before the write goes
    # anywhere near the file. `rolling_summary.covered_digest` is the right tool
    # and is reused rather than reimplemented: same transcript, same question
    # ("is this still the same prose?"), same three fields.
    digest = store.rolling_summary.covered_digest(messages)
    try:
        result = await run_in_threadpool(_break_commit, cid, sid, view["watermark"],
                                         answer, digest)
    except store.scenes.SceneNotFound:
        # Renamed or deleted while the model was answering -- the race
        # `_rolling_refresh` documents, reachable here for the same reason (the
        # play loop fires this after releasing the scene). There is nowhere to
        # put the answer and this call is fire-and-forget from the client.
        return {**_break_body(view, every), "asked": False}
    # Scored OUTSIDE the hold, from the scene the commit read back. Sizing a
    # time skip runs the campaign's calendar provider, which is user-authored
    # code, and this codebase does not run that under the campaign lock --
    # `scenes.moment.set_datetime` and `scenes.lifecycle._date_hint` make the
    # same cut, and review caught that resolving the provider outside the hold
    # while still CALLING it inside was only half of the rule.
    return {**_break_body(_break_view(result["scene"], every, provider), every),
            "asked": result["landed"]}


def _break_commit(cid: str, sid: str, watermark: dict, answer: dict,
                  digest: str) -> dict:
    """Store a verdict only if the transcript it was formed FROM is still there,
    and report the state that results either way.

    Read-verify-write under one campaign hold (reentrant, so the mutators' own
    acquisitions are free). The verification is `_rolling_commit`'s, for its
    reasons and one of this feature's own: `delete_scene` frees a scene's id and
    the numbering reuses it, so a scene deleted and remade under the same title
    mid-call hands this write the very id it holds -- and a proposal is prose
    ABOUT a story, so landing one on a different scene puts a suggestion to end
    a scene that has not started under a reason nobody can place.

    An ordinary turn appending during the call passes, which it must, or a busy
    scene could never be asked about at all. What fails is an edit or a reroll
    inside the prefix, a trim, and a recycled id.

    ...and nothing that already covers at least as much may be stored. Review
    caught that "the later write simply wins" was the wrong rule, and not even a
    description of what happened: two questions can be in flight at once (the
    panel's button beside the play loop's), the newer one can finish first, and
    the older one's prefix is still perfectly intact because everything since is
    an APPEND. Writing then replaced the answer the player had just asked for
    with a staler verdict about fewer posts AND regressed the watermark, so the
    next automatic question came early and re-asked what had just been answered.
    A DISMISSAL moves the watermark the same way and is caught by the same rule,
    which is what it should be: "not here" must not be undone by a question that
    was already out when the player said it.

    Scores nothing and reads no calendar: this runs under the hold, and the view
    its caller needs is built outside it.
    """
    with store.locks.campaign_lock(cid):
        scene = store.scenes.read_scene(cid, sid)
        stored = store.scenes.scene_break_fields(scene["meta"])
        # Two questions about the same file, named apart because they are
        # answered against different watermarks: whether the posts THIS answer
        # was formed from are still on disk, and whether a DIFFERENT answer
        # already covers at least as much.
        unchanged = (watermark["at"] <= len(scene["messages"])
                     and store.rolling_summary.covered_digest(
                         scene["messages"][:watermark["at"]]) == digest)
        # Against the stored watermark only while IT is still about this
        # transcript: a rewind voids it, and a voided watermark is no bar to an
        # answer about the scene as it now stands.
        superseded = (_break_intact(scene["messages"], stored)
                      and stored["at"] >= watermark["at"])
        landed = unchanged and not superseded
        if landed:
            store.scenes.set_scene_break(
                cid, sid, watermark["at"], watermark["locs"], watermark["times"],
                digest, "yes" if answer["break"] else "no", answer["reason"],
                answer["title"] if answer["break"] else "")
            # As in `_rolling_commit`, and for the same reason: the play loop
            # fires this as a background run after every turn, with no response
            # line for the middleware to stamp (#409).
            store.revision.bump(cid)
            scene = store.scenes.read_scene(cid, sid)
        return {"landed": landed, "scene": scene}


@router.post("/campaigns/{cid}/scenes/{sid}/scene-break/dismiss")
def post_scene_break_dismiss(cid: str, sid: str):
    """"Not here" — retire the proposal and start counting from the scene as it
    stands.

    The watermark is moved by the mutator off the file it already holds open,
    not by this route off a read of its own: a dismissal that recorded a length
    read a moment earlier would leave the post that landed in between outside
    both the retired question and the next one.
    """
    _require_scene(cid, sid)
    store.scenes.dismiss_scene_break(cid, sid)
    scene = _require_scene(cid, sid)
    every = store.config.scene_break_every()
    return _break_body(_break_view(scene, every, _break_provider(cid)), every)


# ---- what a landed turn asks for next (#397) --------------------------------
#
# The two routes above are the `background` class: no exclusion key, no
# notification, their own stores, fire-and-forget. Until now they were fired by
# `CampaignView.askAfterPost`, once the streaming promise settled -- which is
# exactly the thing that does not happen in the case detached runs exist for.
# The phone locks, the JavaScript is suspended or the page is gone, the turn
# lands server-side, and nobody is left to POST either one: the summary falls
# behind by however many turns were taken with the app backgrounded, and the
# scene-break prompt never appears -- indefinitely, since the next opportunity
# only comes with the next send.
#
# So the turn schedules them, at the point its terminal write is done, where
# the trigger cannot be detached from the event that should cause it
# (`streaming._fire_follow_up`). Three properties keep that from costing the
# turn anything, and each is structural rather than a rule to remember:
#
# * `background` declares no exclusion key, so neither of these can refuse a
#   chat with `run_in_flight` nor hold the scene against an edit, a cut or an
#   End Scene (`scene_busy`);
# * the call is outside the turn's own outcome and its failures are swallowed,
#   so a summary that could not even be reserved is not a failed turn;
# * each run gets `runner._guarded`'s failure boundary, so one that dies takes
#   no sibling with it.
#
# What this does NOT cover is every other transcript write -- an edit, a cut, a
# retcon, a manual roll, a check, a replay. Those are still asked for by the
# client, and correctly so: each is a request the player made and is waiting
# on, so there is a client there by construction. It is only the turn whose
# completion the client can miss.


def schedule_follow_ups(app, cid: str, sid: str, upto: int,
                        client: LLMClient) -> None:
    """Start this scene's rolling-summary fold and scene-break check as runs.

    `upto` is the boundary the client used to supply as `seen`: how long the
    transcript was when the turn finished. Read server-side under the campaign
    lock the terminal write held (`streaming._tail_length`), which is strictly
    better than the client's read-after-the-fact -- there is no window left for
    a fast next send to append a post the fold would then swallow.

    `client` is the turn's own gateway client rather than a fresh resolve of
    `get_llm`: there is no request here to resolve a dependency against, and
    the facade dispatches per connection anyway, so the turn's is the same
    object either route would have been handed -- including the fake a test
    injected at `routes.get_llm`.

    Nothing is awaited and nothing comes back. Both halves decide for
    themselves whether anything is due and answer "no" without reaching a
    provider, which is what makes firing after every turn cheap enough to be
    unconditional.
    """
    _start_background(app, cid, sid, "rolling-summary",
                      lambda: _rolling_follow_up(cid, sid, upto, client))
    _start_background(app, cid, sid, "scene-break",
                      lambda: _break_follow_up(cid, sid, upto, client))


def _start_background(app, cid: str, sid: str, kind: str, work) -> None:
    """One `background` run, or nothing at all.

    The release is not defensive noise. `runner.start` raises when the app has
    no lifespan running -- a bare `TestClient`, or a shutdown that closed the
    portal between the reservation and the handoff -- and a run reserved but
    never entered stays `running` for the life of the process. Only terminal
    runs are reaped, so that one would keep `PUT /config/data-dir` answering
    `run_in_flight` forever. `runs.reservation` makes the same guarantee for a
    route; this is the same duty for a caller that is not one.
    """
    run = runs.reserve_background(app, cid, sid, kind)
    if run is None:
        return
    try:
        runs.start_computing(app, run, work)
    except Exception as exc:  # noqa: BLE001 -- see the docstring
        log.warning("could not start the %s follow-up for %s/%s -- %s",
                    kind, cid, sid, exc)
        runs.release_before_start(app, run, "failed",
                                  {"kind": "run_failed", "detail": str(exc)})


async def _rolling_follow_up(cid: str, sid: str, upto: int,
                             client: LLMClient) -> dict:
    """The automatic rolling-summary refresh, as a run's outcome.

    `force=False`, always: this is the per-turn question, so the whole "is it
    worth a call" decision stays exactly where it already lives. That is also
    what makes the single pass right -- the coalescing branch answers for
    itself for an automatic call, and the retry loop in `post_rolling_summary`
    belongs to the panel's button, which has a request to hold open and a
    player waiting on it.

    A refusal comes back as the run's error rather than as an exception, so a
    scene with no `rolling-summary` connection reads as a background run that
    said why, not as a traceback in the log after every turn.
    """
    try:
        answer = await _rolling_once(cid, sid, False, upto, client)
    except HTTPException as exc:
        return {"state": "failed", "error": _run_error(exc)}
    return {"state": "landed",
            "result": {"refreshed": bool(answer and answer["refreshed"])}}


async def _break_follow_up(cid: str, sid: str, upto: int,
                           client: LLMClient) -> dict:
    """The automatic scene-break question, as a run's outcome.

    `_rolling_follow_up`'s shape, for its reasons. Fired separately rather than
    chained onto it, exactly as the client did: neither answer is a
    precondition for the other, and chaining would make a failed summary
    silently skip the break question.
    """
    try:
        answer = await _break_once(cid, sid, False, upto, client)
    except HTTPException as exc:
        return {"state": "failed", "error": _run_error(exc)}
    return {"state": "landed", "result": {"asked": bool(answer["asked"])}}


def _follow_up_hook(app, cid: str, sid: str, client: LLMClient):
    """The callback a turn producer fires once its terminal write is done.

    One argument, the boundary, because that is the only thing the producer
    knows that this side does not -- and it is exactly the thing that used to
    travel from the client.
    """
    return lambda upto: schedule_follow_ups(app, cid, sid, upto, client)


@router.post("/campaigns/{cid}/scenes/{sid}/dossiers", status_code=202)
@computes_only  # a retry of absorb's dossier step alone: same staged edits, same nothing written
def post_dossiers(cid: str, sid: str, request: Request,
                  client: LLMClient = Depends(get_llm)):
    """Standalone dossier retry: re-runs ONLY the dossier phase (never the prose
    absorb), returning the same `dossiers` block and staged edits an absorb
    carries (#286).

    The audit has had this since #235 and the dossier phase has not, so a budget
    that ran out mid-flight left "End scene again" as the only way to get those
    dossiers -- and that re-runs every phase and replaces the review wholesale,
    discarding whatever the reviewer had already approved or typed.

    Every present NPC is re-run, not just the ones `skipped`/`failed` last time.
    That is deliberate: a per-NPC retry list would have to merge two dossier
    blocks in the client, and neither the status nor the reason is a merge of
    its parts. The re-run is also not the wasted work it looks like -- what
    starved the phase was sharing one budget with the extraction, voice and
    audit steps, and this hands the whole budget to dossiers alone, so the run
    that was cut short at NPC 6 of 8 typically now reaches all 8. A phase that
    cannot finish even on its own full budget genuinely needs a bigger one.

    Staged, never written (#235), for the same reason absorb stages: these land
    with the rest of the batch in PUT /chronicle, so a reviewer who hits Cancel
    leaves nothing behind.

    Detached like its absorb (#396), and folded into the stored review rather
    than replacing it: only the NPCs this run actually re-proposed are swapped,
    so a retry that failed for one of them cannot delete their perfectly good
    proposal from the first pass and put nothing in its place.
    """
    scene = _require_scene(cid, sid)
    conn = _require_connection("dossier", cid)
    if not scene["messages"]:
        # A dossier is a paragraph the model rewrites FROM the transcript, so an
        # empty one can only produce invention. The audit needs no equivalent
        # guard: with nothing to audit it simply finds nothing, where this would
        # stage a proposal to overwrite a real dossier with fiction.
        raise HTTPException(status_code=400, detail="nothing to build dossiers from")
    transcript = store.chronicle.transcript_text(scene["messages"])

    def work_for(run, generation):
        async def work():
            # A retry gets its own budget — it never inherits the deadline of
            # whatever absorb ran out of time earlier (post_audit's reason).
            try:
                edits, dossiers = await _stage_dossiers(
                    cid, sid, transcript, client, conn,
                    _Budget(store.config.absorb_budget()),
                    abandoned=_review_abandoned(run))
            except Abandoned:
                return {"state": "cancelled", "error": _cancelled_error()}
            result = {"dossiers": dossiers, "edits": edits}
            return await _persist_review(
                cid, sid, run, generation,
                lambda: store.pending_reviews.merge(
                    cid, sid, generation,
                    lambda review: store.pending_reviews.merge_dossiers(review, result)),
                result=result)
        return work

    return _retry_start(cid, sid, request, "dossiers", work_for)


def _require_unmoved_review(cid: str, sid: str, token: str) -> None:
    """Refuse a save whose review was prepared against a transcript that moved.

    MUST be called inside the campaign-lock hold that covers the writes: read
    outside one, it answers a question that was true a moment ago.

    A scene with NO stored review falls through to the epoch check alone, and
    that is a KNOWN GAP rather than a claim that nothing can go wrong there: one
    tab discarding a review while another still has it open leaves the second
    tab holding a token whose watermark no longer exists, and play continuing
    advances no epoch, so it can commit a summary of posts nobody reviewed.

    It is left open deliberately, because nothing on disk can tell that case
    apart from the ordinary one. "The token was server-minted" does not do it --
    `commits.mint` stamps a token for any save that asks, including one for a
    scene that never had a review at all, so refusing on that reads a fresh
    hand-made save as a discarded review (`test_routes.py`'s
    `test_a_caller_minted_token_is_fenced_from_newer_saves_too` is exactly that
    shape). Closing it needs the discard to leave something behind -- a spent
    token recorded where the ledger can see it -- which is a durable structure
    with a lifecycle of its own, not a condition to add here.

    A save with **no token at all** falls through too. That is the documented
    opt-out from idempotency (`models.ChronicleSave`), not a claim about any
    review, so there is nothing for the stored record to contradict -- and
    reading the default `""` as "a different token" refused every one of them
    on any scene with a review waiting.

    Everything else refuses, and each for its own reason:

    * **A record for a DIFFERENT token.** Another device force-absorbed this
      scene while this review sat open, so the record describes a newer review
      of the same posts. Falling through committed this older one's summary and
      edits on the strength of an epoch check that a fresh scene passes anyway
      -- the exact stale write the watermark exists to stop, arrived at by
      declining to look. Only reachable for a *fresh* save: a replay of an
      already-recorded token returns its prior result well above this.
    * **Unreadable.** Not evidence that this save is stale, but not evidence
      that it is sound either, and the epoch check does not cover what this one
      does: play continuing advances no epoch. Refusing is recoverable -- a
      fresh absorb replaces the sidecar outright, which is what the message
      says to do -- where committing is not. `OSError` is split off from a
      garbled file because it says something different: a sync client holding
      the sidecar for a moment clears on its own, so that one is the ordinary
      retryable refusal rather than an instruction to re-run.
    """
    try:
        record = store.pending_reviews.read(cid, sid)
    except OSError as exc:
        log.warning("pending review for %s/%s could not be read at save",
                    cid, sid, exc_info=True)
        raise HTTPException(
            status_code=409,
            detail={"kind": "busy",
                    "detail": f"this scene's review could not be read: {exc}"}) from exc
    except store.pending_reviews.CorruptReviewError as exc:
        log.warning("pending review for %s/%s is corrupt at save",
                    cid, sid, exc_info=True)
        raise HTTPException(
            status_code=409,
            detail={"kind": "review_unreadable",
                    "detail": "this scene's stored review is unreadable, so this save "
                              "cannot be checked against it — re-run End Scene"}) from exc
    if record is None or not token:
        return
    if record["review"].get("commit_token") != token:
        raise HTTPException(
            status_code=409,
            detail={"kind": "review_superseded",
                    "detail": "a newer review of this scene is waiting to be saved — "
                              "reload the campaign to see it"})
    scene = _require_scene(cid, sid)
    mark = store.pending_reviews.watermark(scene["messages"])
    if mark != record.get("watermark"):
        raise HTTPException(
            status_code=409,
            detail={"detail": "the scene changed after this review was prepared — "
                              "re-run End Scene to review what is there now",
                    "kind": "review_stale",
                    "prepared_posts": (record.get("watermark") or {}).get("count"),
                    "current_posts": mark["count"]})


def _clear_pending_review(cid: str, sid: str, token: str) -> None:
    """Drop the pending review THIS save has made obsolete, and no other.

    Scoped to the committed token, because "whatever is stored" is a different
    record on the path that needs this most. The commit is idempotent by design
    (#235), so a duplicate PUT of an already-recorded token arrives long after
    its first response was lost -- by which time another device may have
    force-absorbed the scene and stored a *newer* review, whose own token
    carries the epoch that save advanced and is therefore still savable.
    Clearing that one deletes an entire end-of-scene generation for a save that
    wrote nothing: the loss this whole change exists to prevent, arrived at
    through its own cleanup.

    A **tokenless** save clears whatever is stored, and that is not a hole in
    the scoping. It has no ledger entry, so it cannot be the replay the
    paragraph above is about; and it advances the scene epoch like any other
    commit, so a review left behind by it is one whose own token can no longer
    pass -- a panel that could only ever be refused.

    Never fatal. The commit has landed by the time this runs, so a sidecar that
    will not go must not turn a successful save into a 500 -- the review it
    leaves behind is refused at its next retrieval by the watermark, and
    replaced outright by the next absorb.
    """
    try:
        record = store.pending_reviews.read(cid, sid)
        # `read` has already refused anything whose `review` is not a dict.
        if record is None:
            return
        if token and record["review"].get("commit_token") != token:
            return
        store.pending_reviews.clear(cid, sid, record.get("generation"))
    except (OSError, store.pending_reviews.CorruptReviewError):
        log.warning("pending review for %s/%s could not be cleared after the save",
                    cid, sid, exc_info=True)


@router.put("/campaigns/{cid}/scenes/{sid}/chronicle")
def put_chronicle(cid: str, sid: str, body: ChronicleSave, request: Request):
    _require_scene(cid, sid)
    facts = store.chronicle.scene_facts(cid, sid)
    # One hold across the whole persistence sequence (#234). These are four
    # independent writes; with a lock taken per write, contention arriving
    # partway returns 409 after the chronicle record and timeline events are
    # already durable -- and the retry that 409 invites appends the timeline
    # events a second time while the first attempt's approved edits were never
    # applied. Holding it here means a busy response is reported before the
    # first write, so a retry is safe. Reentrant, so the inner acquisitions
    # cost nothing.
    #
    # The same hold is what makes apply_edits' dossier branch safe (#235): it
    # compares the staged `before` with what is stored and then writes, and two
    # concurrent saves could otherwise both read a matching `before` before
    # either wrote -- a guard that stops neither.
    # The token names the attempt; the fingerprint names what it was for.
    fp = store.commits.fingerprint({
        "one_line": body.one_line, "summary": body.summary, "keywords": body.keywords,
        "timeline_events": body.timeline_events, "edits": body.edits})
    with store.locks.campaign_lock(cid):
        # A review save writes back into the scene and marks it absorbed, so it
        # reshapes what a live turn is generating into. Inside the hold that
        # already covers the whole sequence, so nothing reserves a turn between
        # the check and the first write.
        runs.require_scene_free(request.app, cid, sid)
        # Inside the lock: two saves racing on one token must not both miss.
        prior = store.commits.lookup(cid, body.commit_token)
        progress: dict = {}
        if prior is not None:
            if prior.get("sid") and prior["sid"] != sid:
                # The review panel survives a scene switch, so a retry can carry
                # scene A's token to scene B's route. The ledger is
                # campaign-scoped; without this, B's save would return A's
                # result and write nothing.
                raise HTTPException(
                    status_code=409,
                    detail={"detail": "this save was already committed for a different "
                                      "scene — reopen that scene's review",
                            "kind": "commit_scene_mismatch"})
            if prior.get("fingerprint") and prior["fingerprint"] != fp:
                # The review stayed editable after the failed save, and this
                # retry carries different content. Returning the first result
                # would report success while discarding the edits made since.
                raise HTTPException(
                    status_code=409,
                    detail={"detail": "this review changed after a save that already "
                                      "committed — reload the campaign and edit the "
                                      "records directly",
                            "kind": "commit_body_changed"})
            if prior["done"]:
                # The review goes on the REPLAY path too, not only the
                # fresh-success tail. The commit is idempotent by design
                # (#235), so a save whose first response was lost returns the
                # recorded result through here -- and cleanup that lived only
                # on the first-execution path would never run for a process
                # that exited after recording the commit and before deleting
                # the review, leaving an obsolete review retrievable forever
                # for a scene that is demonstrably absorbed. Under the same
                # hold, so nothing reads it between the two.
                _clear_pending_review(cid, sid, body.commit_token)
                return prior["result"]
            if not prior["journalled"]:
                # Reserved before #271, so there is no account of what it did --
                # and it could have appended the timeline and applied any number
                # of edits before it died. Resuming it as fresh work would repeat
                # every one of them, so this keeps the pre-#271 refusal. Only
                # entries written by an older build can land here.
                raise HTTPException(
                    status_code=409,
                    detail={"detail": "an earlier save of this review started and did "
                                      "not finish — reload the campaign to see what "
                                      "landed",
                            "kind": "commit_incomplete"})
            claimed = prior.get("claimed")
            if claimed is not None and store.commits.scene_epoch(cid, sid) > claimed:
                # The epoch this reservation's own claim produced, recorded by
                # `reserve`. Anything past it is a LATER commit for this scene --
                # the re-absorb that the wedge deliberately leaves room for, or
                # the scene's deletion. Resuming now would rewrite the chronicle
                # entry and the scene summary with this older review on top of
                # that one. Being stranded half-applied is the better of the two:
                # the newer save is the current record, this one is history.
                #
                # Read from the entry rather than derived from the token, so a
                # caller-minted key -- which carries no epoch and is a supported
                # thing to send -- is fenced exactly like a server-minted one.
                raise HTTPException(
                    status_code=409,
                    detail={"detail": "a newer save of this scene completed while this "
                                      "one was unfinished — reload the campaign to see "
                                      "what landed",
                            "kind": "commit_incomplete"})
            # Reserved and never completed: some of the four writes landed and
            # nobody knows which. Its journal does, so this attempt RESUMES it
            # (#271) -- every step the journal accounts for is skipped, and a
            # step it marked attempted without confirming is reported rather
            # than repeated.
            progress = prior["progress"]
        else:
            # The transcript watermark, and it does NOT overlap with the epoch
            # check below. The epoch guards against another REVIEW committing;
            # this guards against play continuing. `commits.reserve` is called
            # from exactly one place -- this route -- so appending a turn,
            # cutting a post or retconning one after the review landed advances
            # nothing, and the token minted from the older snapshot still
            # passes every check while the scene is marked absorbed with a
            # summary of posts nobody reviewed.
            #
            # Read under the hold that already covers the whole sequence, and
            # ahead of the first write, so a refusal leaves the chronicle
            # untouched and the commit token unspent.
            _require_unmoved_review(cid, sid, body.commit_token)
            epoch = store.commits.token_epoch(body.commit_token)
            if epoch is not None and epoch != store.commits.scene_epoch(cid, sid):
                # Two reviews of one scene carry different tokens, so the key
                # cannot order them: the second to save would append a second
                # set of timeline events and plot beats for the same scene. The
                # epoch stamped into the token is what the key is missing --
                # this review was prepared before some other save of this scene
                # completed, so it describes a state that no longer exists.
                raise HTTPException(
                    status_code=409,
                    detail={"detail": "another review of this scene was saved after "
                                      "this one was prepared — re-absorb the scene to "
                                      "review it against what is now recorded",
                            "kind": "commit_superseded"})
            # Contradiction check (#111), before the first write and after the
            # replay branches above -- a replay of a save that already landed must
            # return its result, not be told the records it wrote now contradict it.
            # On a FRESH commit only, for the same reason taken one step further: a
            # resume's own earlier writes have already moved the records its
            # remaining edits were staged against, so this pass would refuse the
            # very commit it is trying to finish. Those edits still meet
            # `apply_edits`' per-edit pass, which reports them as conflict failures
            # rather than as a clean refusal.
            #
            # This is the REVIEWER'S check, not the guard: it runs ahead of every
            # write so a 409 leaves the chronicle untouched and this commit token
            # unspent, letting the panel offer keep/replace/merge on a review that is
            # still intact. The guard is `apply_edits`' own pass, which re-judges the
            # batch immediately before it writes -- so a target that moves between
            # here and there is still caught, as a conflict failure rather than a
            # clean refusal.
            #
            # Neither pass makes check-and-write atomic, and the campaign lock does
            # not either: `overlay` (and most of `locks.UNREVIEWED`) mutates without
            # taking it, and the lock is machine-local, so a synced store sees none
            # of it. Closing the remaining window means compare-and-swap in each
            # mutator -- what `sheets.write(expected=...)` already does, and what the
            # other seven would need -- which is the concurrency change `locks.py`
            # says those modules are waiting on, not something to bolt on here.
            drifted = store.absorb.check_conflicts(cid, body.edits)
            if drifted:
                raise HTTPException(
                    status_code=409,
                    detail={"detail": "some proposed changes no longer match what is "
                                      "stored — review them and save again",
                            "kind": "edit_conflicts", "conflicts": drifted})
        record = store.chronicle.absorb(cid, {
            "id": sid, "one_line": body.one_line, "summary": body.summary,
            "keywords": body.keywords, **facts})
        # The one step of the four that appends -- the other three overwrite (the
        # chronicle entry is keyed by scene id, the frontmatter below is a
        # rewrite, and each edit has its own slot in the journal). So it is the
        # one that has to be durably *attempted* before it is attempted: the
        # journal entry rides along on the reservation below, which is claimed
        # BEFORE any non-idempotent write. `progress` carries a resumed
        # attempt's journal forward so this one adds to that account rather than
        # opening a second one.
        timeline = progress.get("timeline")
        if timeline is None:
            progress["timeline"] = "pending"
        store.commits.reserve(cid, body.commit_token, fp, sid, progress)
        started: list[dict] = []
        if timeline is None:
            try:
                store.chronicle.append_timeline(cid, body.timeline_events)
            except Exception as exc:  # noqa: BLE001 — unreadable timeline, full disk, ...
                # An exception means the append did NOT publish: atomic.write_text
                # replaces by rename as its last act. So this is an ordinary
                # reported failure, and the mark comes back off -- "unconfirmed"
                # is for a process that died without returning, not for a call
                # that returned by raising.
                progress.pop("timeline", None)
                started.append({"id": "timeline", "kind": "error",
                                "reason": f"the timeline events could not be "
                                          f"recorded: {exc}"})
            else:
                progress["timeline"] = "done"
            # Settled straight away rather than left to apply_edits' first
            # checkpoint: a failure in mark_absorbed below would otherwise leave
            # an append that demonstrably landed reading as unconfirmed forever.
            store.commits.checkpoint(cid, body.commit_token, progress)
        elif timeline == "pending" and body.timeline_events:
            # An earlier attempt journalled the append and never confirmed it.
            # Repeating it would double the campaign's timeline, so say so
            # instead -- the events are in the review and in the response.
            started.append({"id": "timeline", "kind": "error",
                            "reason": store.absorb.UNCONFIRMED})
        store.scenes.mark_absorbed(cid, sid, body.one_line, body.summary)
        applied, failures = store.absorb.apply_edits(
            cid, body.edits, sid, progress=progress,
            checkpoint=lambda: store.commits.checkpoint(cid, body.commit_token, progress))
        result = {**record, "applied": applied, "failures": started + failures}
        store.commits.record(cid, body.commit_token, result, fp, sid)
        # The scene is absorbed, so its pending review describes work that has
        # now landed. Inside the hold, after the ledger entry, so a save that
        # raised before this point leaves the review where the retry can find
        # it.
        _clear_pending_review(cid, sid, body.commit_token)
    return result


@router.get("/campaigns/{cid}/scenes/{sid}/briefing")
def get_scene_briefing(cid: str, sid: str):
    """The pre-scene briefing (#118): open threads and commitments flagged with
    which of this scene's cast they involve, the relationships between the
    people on stage, and the fact that came immediately before.

    Thin on purpose — the join, the tolerance and the lock all live in
    `store.briefing`, whose docstring carries the argument for each. Declared
    ahead of `GET /scenes/{sid}/cast/{kind}/{id}` for the reason every specific
    scene route here is: a generic path segment would otherwise swallow it.
    """
    _require_scene(cid, sid)
    return store.briefing.build(cid, sid)


@router.get("/campaigns/{cid}/scenes/{sid}/cast")
def get_scene_cast(cid: str, sid: str):
    _require_scene(cid, sid)
    return store.appearances.scene_cast(cid, sid)


def _resolve_role(kind: str, role: str | None, pcless: bool) -> str:
    """The role a cast addition will take, given what the scene IS.

    Takes `pcless` rather than reading it, so the import route can ask the same
    question about a scene it has not created yet (`is_pcless` answers False
    for a scene that does not exist, which would let a player through into an
    offscreen import and then fail inside `appear`).
    """
    if kind not in store.appearances.ACTOR_KINDS:
        raise HTTPException(status_code=404, detail="unknown actor kind")
    resolved = "player" if kind == "pcs" else (role or "npc")
    if resolved not in ("player", "npc"):
        raise HTTPException(status_code=400, detail="role must be player or npc")
    if resolved == "player" and pcless:
        raise HTTPException(status_code=400, detail="cannot seat a player in an offscreen scene")
    return resolved


def _cast_role(cid: str, sid: str, kind: str, role: str | None) -> str:
    """The role a cast addition will take. Raises HTTPException saying why not.

    Split out of `_seat_cast_member` so a caller that CREATES the actor first
    (the emergent route) can settle the role before writing anything: a 400
    raised after the create would leave a character behind that nothing seats.
    """
    return _resolve_role(kind, role, store.scenes.is_pcless(cid, sid))


def _actor_version(cid: str, kind: str, aid: str, version: str | None) -> str:
    """The version a seat locks: the one asked for, or the actor's default.

    A first appearance locks lazily by copying from the world when the campaign
    lacks the version; validate a supplied version against the campaign-visible
    actor first so a purged/tombstoned one can't be revived. An already-cast
    actor skips this -- `appear()` reports the lock conflict (409), no revival.
    """
    try:
        if version is None:
            if kind == "characters":
                version = store.characters.read_character(
                    store.overlay.char_root(cid, aid), aid)["meta"]["default_version"]
            else:
                version = store.pcs.read_pc(
                    store.overlay.pc_root(cid, aid), aid)["meta"]["default_version"]
    except (store.characters.CharacterNotFound, store.pcs.PCNotFound):
        raise HTTPException(status_code=404, detail="actor not found")
    if store.appearances.locked_version(cid, kind, aid) is None and store.appearances.actor_hash(
            store.overlay.actor_root(cid, kind, aid), kind, aid, version) is None:
        raise HTTPException(status_code=404, detail="actor or version not found in campaign")
    return version


def _seat_cast_member(cid: str, sid: str, body: Appear) -> None:
    """Validate + resolve one cast addition and record it. Raises HTTPException
    (404 unknown, 400 bad role) or store.appearances.AppearError (already cast)."""
    role = _cast_role(cid, sid, body.kind, body.role)
    version = _actor_version(cid, body.kind, body.id, body.version)
    store.appearances.appear(cid, sid, body.kind, body.id, version, role)


@router.post("/campaigns/{cid}/scenes/{sid}/cast")
def post_scene_cast(cid: str, sid: str, body: Appear, request: Request):
    _require_scene(cid, sid)
    # `appear`/`leave` APPEND a transition line to a non-empty transcript, so
    # these are transcript-shape changes and not merely cast bookkeeping -- the
    # reason the plan lists them beside the rename. Held across the check and
    # the write, like every other door.
    try:
        with runs.scene_held_free(request.app, cid, sid):
            _seat_cast_member(cid, sid, body)
    except store.appearances.AppearError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"ok": True}


@router.delete("/campaigns/{cid}/scenes/{sid}/cast/{kind}/{id}")
def delete_scene_cast(cid: str, sid: str, kind: str, id: str, request: Request):
    _require_scene(cid, sid)
    if kind not in store.appearances.ACTOR_KINDS:
        raise HTTPException(status_code=404, detail="unknown actor kind")
    # The most destructive of the cast routes, and the one an inventory of them
    # is most likely to miss: `leave` removes the actor AND appends a "leaves
    # the scene" transition whenever the transcript is non-empty.
    with runs.scene_held_free(request.app, cid, sid):
        store.appearances.leave(cid, sid, kind, id)
    return {"ok": True}


@router.post("/campaigns/{cid}/scenes/{sid}/cast/batch")
def post_scene_cast_batch(cid: str, sid: str, body: AppearBatch, request: Request):
    """Seat a whole suggestion cast in one request. Already-cast members are
    skipped (the per-member 409), matching what the chooser's serial loop
    tolerated; unknown actors still 404 the request."""
    _require_scene(cid, sid)
    added, skipped = 0, []
    # ONE hold over the whole batch, not one per member: a turn reserved
    # mid-loop would otherwise have half the cast seated under it.
    with runs.scene_held_free(request.app, cid, sid):
        for ref in body.refs:
            try:
                _seat_cast_member(cid, sid, ref)
                added += 1
            except store.appearances.AppearError:
                skipped.append(f"{ref.kind}/{ref.id}")
    return {"ok": True, "added": added, "skipped": skipped}


@router.post("/campaigns/{cid}/scenes/{sid}/cast/emergent")
def post_emergent_cast(cid: str, sid: str, body: EmergentCast, request: Request):
    """Create a character this campaign invented mid-play, and seat it (#98).

    Campaign-scoped on purpose: the name came out of one scene's prose, so it
    starts in the campaign's own overlay rather than the shared world library.
    `overlay.create_character` allocates the id against the world's characters
    and the campaign's tombstones as well as its own, so an emergent Seraphine
    beside a world Seraphine gets a distinct id instead of shadowing her.
    Promoting one into the library is #60's job, not this route's.

    The seat goes through `_seat_cast_member`, so an emergent character locks
    its version exactly as a library one does -- there is no second casting
    path to keep in step.
    """
    _require_scene(cid, sid)
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    # Role first, character second: every way this request can be refused has to
    # be settled before the create, or a rejected seat leaves an unseated
    # character in the campaign that nothing points at.
    role = _cast_role(cid, sid, "characters", body.role)
    char, version = store.overlay.create_character(cid, name)
    try:
        # The seat is the transcript-touching half (`appear` appends a
        # transition), so the guard belongs around it rather than around the
        # create -- refusing after the character exists is the same shape as the
        # AppearError below, and the character is campaign state either way.
        with runs.scene_held_free(request.app, cid, sid):
            _seat_cast_member(cid, sid,
                              Appear(kind="characters", id=char, version=version, role=role))
    except store.appearances.AppearError as exc:
        # Not unreachable, though it takes a deleted character to get here: a
        # campaign-side delete deliberately does NOT sweep `appearances.json`
        # (overlay.forget_everywhere names it as out of scope), while
        # `create_character` allocates ids against the filesystem and the
        # tombstones only. So a re-used slug can meet its own stale record and
        # be refused for the role or version that record still holds. 409 says
        # that; letting it out says 500.
        raise HTTPException(status_code=409, detail=str(exc))
    return {"character": char, "version": version, "name": name}


@router.get("/campaigns/{cid}/scenes/{sid}/cast-changes")
def get_cast_changes(cid: str, sid: str):
    """Enter/leave/unknown candidates read out of the newest turn's prose
    (#97, #98). A GET the client issues once a turn has landed, rather than a
    rider on the chat stream's `done` frame: detection is a read over the
    persisted transcript, and hanging it off the stream would put it inside
    the one code path where a failure costs the reply itself."""
    _require_scene(cid, sid)
    return store.appearances.cast_changes(cid, sid)


@router.get("/campaigns/{cid}/scenes/{sid}/suggestions")
def get_scene_suggestions(cid: str, sid: str):
    _require_scene(cid, sid)
    return store.appearances.suggestions(cid, sid)


@router.post("/campaigns/{cid}/scenes/{sid}/suggestions/dismiss")
def post_dismiss(cid: str, sid: str, body: Dismiss):
    """Hide one suggestion for this scene, for good.

    Slugified rather than stored verbatim, which is a no-op for the character
    ids this has always taken (`create_character` allocates them by slugifying
    the name, and slugify is idempotent) and is what lets an *unknown name*
    from `cast_changes` be dismissed through the same route: the detector
    filters its unknown bucket by the slug of each candidate, so "Winifred"
    dismissed here stays dismissed under the id an emergent create would give
    it (#98)."""
    _require_scene(cid, sid)
    store.scenes.add_dismissed(cid, sid, store.paths.slugify(body.character))
    return {"ok": True}


@router.get("/campaigns/{cid}/scenes/{sid}/location")
def get_scene_location(cid: str, sid: str):
    _require_scene(cid, sid)
    history = store.scenes.get_location_history(cid, sid)

    def ref(eid: str) -> dict:
        try:
            name = store.overlay.read_entity(cid, "locations", eid)["meta"].get("name", eid)
        except store.entities.EntityNotFound:
            name = eid
        return {"id": eid, "name": name}

    return {"current": ref(history[-1]) if history else None,
            "visited": [ref(e) for e in history[:-1]]}


@router.put("/campaigns/{cid}/scenes/{sid}/location")
def put_scene_location(cid: str, sid: str, body: SceneLocation, request: Request):
    _require_scene(cid, sid)
    try:
        # `set_location` appends a transition line, so this moves the transcript
        # a live turn is generating into -- not merely the context it was built
        # from.
        with runs.scene_held_free(request.app, cid, sid):
            result = store.scenes.set_location(cid, sid, body.location)
    except store.entities.EntityNotFound:
        raise HTTPException(status_code=404, detail="location not found")
    return {"ok": True, **result}


@router.get("/campaigns/{cid}/scenes/{sid}/datetime")
def get_scene_datetime(cid: str, sid: str):
    _require_scene(cid, sid)
    history = store.scenes.get_time_history(cid, sid)
    current = None
    suggested = None
    if history:
        croot = store.campaigns.campaign_root(cid)
        cfg = store.calendars.read_calendar(croot)
        native = history[-1]
        try:
            current = {"native": native, **store.calendars.today_facts(cfg, native),
                       "cast": store.context.cast_datetime_facts(cid, sid, native),
                       # What is imminent and not yet acknowledged (#106), judged
                       # from THIS scene's moment rather than the campaign clock:
                       # a scene set in the past must not be warned about next
                       # week. `pending` is tolerant end to end, so a calendar
                       # that half-works costs the banner, not the panel.
                       "notices": store.notices.pending(cid, croot, native)}
        except store.calendars.CalendarError:
            current = None  # misconfigured calendar — surface "no date" rather than 500
    else:
        # dateless: offer a pre-fill — the creation-time hint, else the campaign
        # clock (#100). The clock is the stored "now" when there is one and the
        # latest chronicle date when there is not, so this is a superset of the
        # chronicle read it replaces: an unclocked campaign pre-fills exactly
        # what it always did, and one that skipped a month between scenes now
        # pre-fills the month it is actually in.
        hint = store.scenes.get_suggested_date(cid, sid)
        if not hint:
            hint = store.clock.now(cid)
        if hint:
            suggested = store.calendars.split_native(hint)[0]
    return {"current": current, "history": history, "suggested": suggested}


@router.put("/campaigns/{cid}/scenes/{sid}/datetime")
def put_scene_datetime(cid: str, sid: str, body: SceneDatetime, request: Request):
    _require_scene(cid, sid)
    # Captured before the write: the sweep needs the moment being left, and
    # set_datetime appends to the same history it would read back.
    history = store.scenes.get_time_history(cid, sid)
    previous = history[-1] if history else None
    try:
        # The sharpest of the moment routes: the FIRST `set_datetime` renames
        # the scene, so this does not merely change the context a live turn was
        # built from -- it mints a new `sid` and can trip the identity fence
        # that discards a completed reply.
        with runs.scene_held_free(request.app, cid, sid):
            result = store.scenes.set_datetime(cid, sid, body.datetime)
    except store.calendars.CalendarError as e:
        raise HTTPException(status_code=400, detail=str(e))
    # Reconcile the campaign clock (#100) with the moment this scene just took:
    # forward only, so a flashback cannot drag the campaign's present backwards.
    # Called from the route rather than from `set_datetime`, which keeps `scenes`
    # free of any import of `clock` — `clock` reads the chronicle and the
    # chronicle reads `scenes`, so the other direction would be a cycle.
    #
    # Before the weather sweep, not after: the scene's date is already written by
    # here, and the sweep resolves a climate and a provider per visited location.
    # Leaving the clock behind because the *weather* failed would be the worst of
    # the three outcomes.
    #
    # Everything from here is stamped on the way out even when it raises (#409).
    # `set_datetime` above has already landed -- it renames the scene and writes
    # its date -- and `observe` commits the clock, so a failure in the sweep
    # answers non-2xx over a campaign that has genuinely changed, and the
    # middleware stamps success only (Codex review). A raise before either wrote
    # costs an over-bump, which is a re-price for somebody and the direction
    # this value is deliberately wrong in.
    try:
        clock = store.clock.observe(cid, body.datetime, f"scene {result.get('id', sid)}")
        # Names the transitions for the advance digest. Generation is pure, so the
        # changes happen either way; without this they are simply never reported.
        weather_changes = store.weather.sweep(cid, result.get("id", sid),
                                              previous, body.datetime)
    except BaseException:
        store.revision.bump(cid)
        raise
    return {"ok": True, **result, "weather_changes": weather_changes, "clock": clock}


@router.get("/campaigns/{cid}/scenes/{sid}/response")
def get_scene_response(cid: str, sid: str):
    scene = _require_scene(cid, sid)
    try:
        campaign_meta = store.campaigns.read_campaign(cid)["meta"]
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")
    cfg = store.read_config()
    return _response_body(scene["meta"], campaign_meta, cfg, scene["meta"])


@router.put("/campaigns/{cid}/scenes/{sid}/response")
def put_scene_response(cid: str, sid: str, body: ResponseSettings):
    _require_scene(cid, sid)
    fields = {k: v for k, v in _dump(body).items() if v is not None}
    _write_response(lambda f: store.scenes.set_response(cid, sid, f), fields)
    return {"ok": True}


@router.get("/campaigns/{cid}/scenes/{sid}/context")
def get_scene_context(cid: str, sid: str):
    """The context breakdown, as packed. `total_tokens` is what was actually
    sent, measured the way the packer measures it — a section the packer
    dropped still ships here, with its text and `dropped: true`, so the
    inspector can show what was cut without that cut counting toward the total
    it was cut to fit. See `context.context_breakdown` for why the total is not
    the sum of the rows."""
    scene = _require_scene(cid, sid)
    return {"model": scene["meta"].get("model", ""),
            **store.context.context_breakdown(cid, sid)}


@router.get("/campaigns/{cid}/scenes/{sid}/prompts")
def get_scene_prompts(cid: str, sid: str):
    """This scene's frozen per-turn prompt snapshots, newest first (#157).

    Rows only — id, when, which kind of turn, the model, and the three totals.
    The section text lives in the individual entries, which are large enough
    that listing them all would defeat the point of a list."""
    _require_scene(cid, sid)
    return {"entries": store.prompt_log.list_entries(cid, sid)}


@router.get("/campaigns/{cid}/scenes/{sid}/prompts/{eid}")
def get_scene_prompt(cid: str, sid: str, eid: str):
    """One frozen breakdown, in the same shape `GET .../context` returns — so
    the inspector renders a past turn with the code it already has, pointed at
    stored text instead of a fresh composition.

    404 covers both "never existed" and "evicted by the retention window";
    nothing downstream can tell them apart and nothing needs to."""
    _require_scene(cid, sid)
    # Scoped to the scene, not merely nested under it in the URL: ids are
    # campaign-wide, so an unscoped read would serve one scene's prompt under
    # another's heading.
    entry = store.prompt_log.read_entry(cid, eid, scene=sid)
    if entry is None:
        raise HTTPException(status_code=404, detail="prompt snapshot not found")
    return entry


#: The `against` value that means "the composition as it stands now" rather than
#: a second frozen turn. Not a valid entry id -- those are all digits -- so it
#: cannot collide with one.
LIVE_SIDE = "live"


def _diff_side(entry: dict) -> dict:
    """One end of a comparison, as the panel labels it. The metadata only: the
    section rows are what the diff itself carries, and shipping them twice more
    would put the whole of both prompts in a response that exists to say what is
    different about them."""
    return {k: entry.get(k) for k in
            ("id", "task", "ts", "model", "total_tokens", "dropped_tokens", "budget_tokens")}


@router.get("/campaigns/{cid}/scenes/{sid}/prompts/{eid}/diff")
def get_scene_prompt_diff(cid: str, sid: str, eid: str, against: str = LIVE_SIDE):
    """What changed between two compositions of this scene's prompt (#130).

    `eid` is the BEFORE side and `against` the after -- either another entry id
    or `live`, the composition as it stands now, which is the comparison the
    feature is named for: a past turn against the preview. The direction is the
    caller's and is not reordered by timestamp, so a reader who picks an older
    turn to compare against gets the diff they asked for rather than a silently
    flipped one.

    Both entries are scoped to the scene for the same reason the detail route
    scopes one: ids are campaign-wide, and an unscoped `against` would diff this
    scene's prompt against another scene's while naming neither.

    404 covers a missing scene, a missing entry and an evicted one -- the
    retention window is why the third is routine rather than exceptional, and
    nothing downstream can tell it from the second.

    The live side is COMPOSED on each request, which is what makes it live and
    is also its one caveat, the same one `store.prompt_log` opens with:
    `context.macros.expand_macros` resolves `{{random:a,b}}` and `{{roll:1d20}}`
    at render time, so a section built out of those differs between two calls
    over an unchanged store. Against a preview, a handful of such lines can show
    as changed when nothing in the campaign moved. Two frozen entries do not
    have this: both were recorded, so the comparison is exact.
    """
    scene = _require_scene(cid, sid)
    base = store.prompt_log.read_entry(cid, eid, scene=sid)
    if base is None:
        raise HTTPException(status_code=404, detail="prompt snapshot not found")
    if against == LIVE_SIDE:
        # Composed here rather than read: `context_breakdown` runs the same
        # assemble/pack pass `GET .../context` does, so the side this diff calls
        # "live" is the one the Context panel is showing.
        head = {"id": LIVE_SIDE, "task": LIVE_SIDE, "ts": "",
                "model": scene["meta"].get("model", ""),
                **store.context.context_breakdown(cid, sid)}
    else:
        other = store.prompt_log.read_entry(cid, against, scene=sid)
        if other is None:
            raise HTTPException(status_code=404, detail="prompt snapshot not found")
        head = other
    return {"base": _diff_side(base), "head": _diff_side(head),
            **store.context.compare_breakdowns(base, head)}


@router.get("/campaigns/{cid}/scenes/{sid}/cast/{kind}/{id}/casefile")
def get_cast_casefile(cid: str, sid: str, kind: str, id: str):
    """Everything the campaign has decided about one actor: standing state,
    what she knows and suspects, her dossier paragraph, how she feels about the
    rest of the room, and the standing facts that name her.

    The play view's context column swaps to this in place of the cast grid.
    Every field is a record the absorb pass already writes; none of it costs a
    token, and until now most of it was visible only on a staged review row,
    for the few seconds before that row was approved and disappeared.

    Declared ahead of `GET /scenes/{sid}/cast/{kind}/{id}` for the reason every
    specific scene route here is: the generic path would otherwise swallow it.
    The cast membership check is `store.casefile`'s access control as much as
    its correctness condition -- without it this reads any character's campaign
    state from a guessed id.

    Thin on purpose: the joins, the tolerance and the lock all live in
    `store.casefile`, whose docstring carries the argument for each.
    """
    _require_scene(cid, sid)
    if kind not in store.appearances.ACTOR_KINDS:
        raise HTTPException(status_code=404, detail="unknown actor kind")
    try:
        return store.casefile.build(cid, sid, kind, id)
    except (store.appearances.AppearError, store.characters.CharacterNotFound,
            store.characters.VersionNotFound, store.pcs.PCNotFound, store.pcs.PCVersionNotFound):
        raise HTTPException(status_code=404, detail="actor not found")


@router.get("/campaigns/{cid}/scenes/{sid}/cast/{kind}/{id}")
def get_cast_detail(cid: str, sid: str, kind: str, id: str):
    _require_scene(cid, sid)
    if kind not in store.appearances.ACTOR_KINDS:
        raise HTTPException(status_code=404, detail="unknown actor kind")
    try:
        return store.appearances.cast_detail(cid, sid, kind, id)
    except (store.appearances.AppearError, store.characters.CharacterNotFound,
            store.characters.VersionNotFound, store.pcs.PCNotFound, store.pcs.PCVersionNotFound):
        raise HTTPException(status_code=404, detail="actor not found")


@router.put("/campaigns/{cid}/scenes/{sid}/messages/{index}")
def put_scene_message(cid: str, sid: str, index: int, body: EditMessage,
                      request: Request):
    _require_scene(cid, sid)
    # Macros resolved once at persist time (#137), same as a fresh send.
    content = store.context.expand_macros(
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
        with runs.scene_held_free(request.app, cid, sid):
            store.scenes.edit_message(cid, sid, index, content)
            # Retire the transient-state ledger from this post on (#120). An
            # edit is the one transcript change the tail filter cannot see:
            # rewriting a furious exchange as a calm one leaves the entry at a
            # perfectly valid index, so the discarded mood keeps being injected
            # and can still be promoted into canonical state. Everything AFTER
            # the edit goes too -- editing text can add or remove blocks, which
            # shifts every later index onto a post it does not describe.
            try:
                store.turnstate.supersede(cid, sid, index)
            except OSError:
                pass          # same judgement as the sidecar below
            try:
                store.alternates.reconcile(cid, sid)
            except OSError:
                pass          # the edit is on disk; the sidecar is not a reason to fail it
    except IndexError:
        raise HTTPException(status_code=400, detail="message index out of range")
    except store.scenes.RollMessageImmutable:
        raise HTTPException(status_code=400, detail="a dice roll's transcript line can't be edited")
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
        with runs.scene_held_free(request.app, cid, sid):
            return store.cascade.delete_from(cid, sid, index)
    except IndexError:
        raise HTTPException(status_code=400, detail="message index out of range")
    except (store.SceneNotFound, store.CampaignNotFound):
        raise HTTPException(status_code=404, detail="scene not found")


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
    # re-roll on every later context build.
    content = store.context.expand_macros(
        body.content, store.context.scene_substitutions(cid, sid), cid, sid)
    try:
        # One hold over check and rewrite, as for the cascade delete above.
        with runs.scene_held_free(request.app, cid, sid):
            return store.retcon.retcon(cid, sid, index, content)
    except IndexError:
        raise HTTPException(status_code=400, detail="message index out of range")
    except store.scenes.RollMessageImmutable:
        raise HTTPException(status_code=400,
                            detail="a dice roll's transcript line can't be edited")
    except (store.SceneNotFound, store.CampaignNotFound):
        raise HTTPException(status_code=404, detail="scene not found")


def _replay_session(cid: str, sid: str) -> dict:
    """This scene's live replay, or a 409 naming the scene that has one.

    A campaign holds at most one session (`store/replay.py`), so a request
    against the wrong scene is not a not-found — the session exists, it is just
    somewhere else, and saying which scene is what lets the client offer to go
    there.
    """
    session = store.replay.state(cid)
    if session is None:
        raise HTTPException(status_code=409,
                            detail={"detail": "no replay is running", "kind": "no_replay"})
    if session["scene"] != sid:
        raise HTTPException(
            status_code=409,
            detail={"detail": "a replay is running in another scene",
                    "kind": "replay_elsewhere", "scene": session["scene"]})
    return session


@router.get("/campaigns/{cid}/scenes/{sid}/replay/preview")
def get_replay_preview(cid: str, sid: str, index: int):
    """What replaying from `index` would cost, before anything is cut (#79/#80).

    Read-only and cheap — it parses the transcript and counts generations — so
    the client can ask on hover. `fork` is the nudge: over the configured
    threshold, offer to copy the campaign first (`POST /campaigns/{cid}/fork`)
    and replay in the copy. `blocked`, when non-empty, is why this span cannot
    be replayed at all, and the client shows it in place of the button.
    """
    _require_scene(cid, sid)
    try:
        return store.replay.preview(cid, sid, index)
    except IndexError:
        raise HTTPException(status_code=400, detail="message index out of range")
    except (store.SceneNotFound, store.CampaignNotFound):
        raise HTTPException(status_code=404, detail="scene not found")


@router.get("/campaigns/{cid}/scenes/{sid}/replay")
def get_replay(cid: str, sid: str):
    """The live replay session for this scene, or null.

    Null rather than a 404 for "there is none": the client asks this on every
    scene open to decide whether to show the walk, and an absent session is the
    ordinary answer. A session belonging to a DIFFERENT scene is also null here
    — it is not this scene's state — and the campaign-wide banner is what
    surfaces it, from the 409 the acting routes give.
    """
    _require_scene(cid, sid)
    session = store.replay.state(cid)
    return session if session and session["scene"] == sid else None


@router.post("/campaigns/{cid}/scenes/{sid}/replay")
def post_replay(cid: str, sid: str, body: ReplayStart, request: Request):
    """Start a replay: cut the scene at `index` and hold the rest for review.

    The cut is the cascade's (#75), so everything the removed posts caused the
    absorb to write comes back out with them — the reply carries that report
    under `cascade`, which is the same one the gutter's delete returns.

    Deliberately NOT the same request as the retcon edit above. A retcon is
    worth doing on its own (re-absorb the scene and read the contradictions),
    replaying is the expensive escalation, and pricing it (`GET
    .../replay/preview`) is a step the client takes in between.
    """
    _require_scene(cid, sid)
    try:
        # The most destructive shape change there is -- it cuts the transcript
        # at `index` and reverses everything the removed posts caused. Under a
        # live turn that is history moving out from under a reply already being
        # written.
        with runs.scene_held_free(request.app, cid, sid):
            return store.replay.begin(cid, sid, body.index)
    except IndexError:
        raise HTTPException(status_code=400, detail="message index out of range")
    except store.replay.ReplayError as exc:
        raise HTTPException(status_code=409,
                            detail={"detail": str(exc), "kind": "replay_refused"})
    except (store.SceneNotFound, store.CampaignNotFound):
        raise HTTPException(status_code=404, detail="scene not found")


@router.post("/campaigns/{cid}/scenes/{sid}/replay/turn")
def post_replay_turn(cid: str, sid: str, request: Request,
                     client: LLMClient = Depends(get_llm),
                     x_grimoire_attempt: str | None = Header(default=None)):
    """Replay the next turn: re-post the originals that were the player's, then
    stream a fresh reply against the edited history.

    No new streaming machinery. The player's own posts go back verbatim — no
    model rewrites what the player said — and the reply is composed by the same
    `compose_turn` an ordinary turn uses, which reads the transcript as it now
    stands and so is already "replay" by construction. Rerolling the result is
    plain `POST .../regenerate`: the fresh reply is the trailing run.

    Staging is idempotent (`replay.stage`), so a turn whose stream died is
    retried by calling this again — it re-posts nothing and generates once more.
    """
    replay = runs.replay_attempt(request.app, cid, sid, x_grimoire_attempt)
    if replay is not None:
        return replay
    _require_scene(cid, sid)
    conn = _require_connection("replay", cid)
    _replay_session(cid, sid)
    run, fresh = runs.reserve_turn(request.app, cid, sid, "replay",
                                   x_grimoire_attempt)
    if not fresh:
        return runs.tail_response(run, 0, lead=runs.lead_frame(run))
    with runs.reservation(request.app, run):
        return _replay_turn_run(cid, sid, request, client, conn, run)


def _replay_turn_run(cid: str, sid: str, request: Request,
                     client: LLMClient, conn: dict, run):
    """The body of a replay turn, once the scene is reserved -- see `_chat_run`."""
    try:
        store.replay.stage(cid)
        session = store.replay.state(cid)
    except store.replay.ReplayError as exc:
        raise HTTPException(status_code=409,
                            detail={"detail": str(exc), "kind": "replay_refused"})
    if session is None or session["next"] != "generation":
        raise HTTPException(status_code=409,
                            detail={"detail": "this replay has no model turn left to run",
                                    "kind": "replay_done"})
    messages, breakdown = store.context.compose_turn(
        cid, sid, describe=store.prompt_log.capturing())
    # No `undo_user_post` hook, unlike `post_chat`. The staged posts are not
    # this request's to take back: `stage` recorded them as staged, a retry
    # re-uses them, and cancelling the replay is what puts the scene back.
    # No `after_turn` hook either, and that is the one deliberate omission
    # among the turn producers (#397). A replay is a WALK: the client cuts,
    # regenerates and accepts turn by turn, so every step of it has a client
    # waiting on the step's own response by construction -- and folding a
    # summary between two steps would describe a transcript the next step is
    # about to rewrite, paying for prose that is stale before it lands.
    # `ReplayPanel` asks once when the walk ends, which is the boundary that
    # means anything here.
    outcome = StreamOutcome()
    stream = _chat_stream(cid, sid, messages, conn, client, task="replay",
                          identity=run.scene_identity, outcome=outcome)
    _record_prompt(cid, sid, "replay", breakdown)
    runs.start_detached(request.app, run, lambda: stream.body_iterator,
                        outcome=outcome.result)
    return runs.tail_response(run, 0, lead=runs.lead_frame(run))


@router.post("/campaigns/{cid}/scenes/{sid}/replay/accept")
def post_replay_accept(cid: str, sid: str, request: Request):
    """Keep the replayed turn and step forward. Null when that was the last one."""
    _require_scene(cid, sid)
    _replay_session(cid, sid)
    try:
        # Stepping the walk forward moves the transcript: the accepted turn
        # stays and the next original comes back out of the held tail.
        with runs.scene_held_free(request.app, cid, sid):
            session = store.replay.accept(cid)
    except store.replay.ReplayError as exc:
        raise HTTPException(status_code=409,
                            detail={"detail": str(exc), "kind": "replay_refused"})
    except (store.SceneNotFound, store.CampaignNotFound):
        raise HTTPException(status_code=404, detail="scene not found")
    return store.replay.state(cid) if session else None


@router.post("/campaigns/{cid}/scenes/{sid}/replay/cancel")
def post_replay_cancel(cid: str, sid: str, request: Request,
                       body: ReplayCancel | None = None):
    """Stop the replay. By default the unreplayed originals go back.

    The scene the session names is trusted over the path's — a session whose
    scene was renamed mid-walk is followed by `scene_refs.repoint`, and the
    restore has to land in the scene that actually holds the transcript.
    """
    _require_scene(cid, sid)
    _replay_session(cid, sid)
    try:
        # Restoring the unreplayed originals appends them back, which is the
        # same shape change as the cut that removed them.
        with runs.scene_held_free(request.app, cid, sid):
            return store.replay.cancel(cid, restore=body.restore if body else True)
    except store.replay.ReplayError as exc:
        raise HTTPException(status_code=409,
                            detail={"detail": str(exc), "kind": "replay_refused"})
    except (store.SceneNotFound, store.CampaignNotFound):
        raise HTTPException(status_code=404, detail="scene not found")
