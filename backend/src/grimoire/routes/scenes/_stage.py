"""A scene stages its cast, location, clock and response scope before play."""

from __future__ import annotations

from fastapi import HTTPException, Request

from ... import store
from .. import runs
from ..common import _dump, _require_scene, _response_body, _write_response
from ..models import (
    Appear,
    AppearBatch,
    Dismiss,
    EmergentCast,
    ResponseSettings,
    SceneDatetime,
    SceneLocation,
)
from ._router import router as router


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
    except (store.characters.CharacterNotFound, store.pcs.PCNotFound) as exc:
        raise HTTPException(status_code=404, detail="actor not found") from exc
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
        with runs.scene_held_open(request.app, cid, sid):
            _seat_cast_member(cid, sid, body)
    except store.appearances.AppearError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True}


@router.delete("/campaigns/{cid}/scenes/{sid}/cast/{kind}/{id}")
def delete_scene_cast(cid: str, sid: str, kind: str, id: str, request: Request):
    _require_scene(cid, sid)
    if kind not in store.appearances.ACTOR_KINDS:
        raise HTTPException(status_code=404, detail="unknown actor kind")
    # The most destructive of the cast routes, and the one an inventory of them
    # is most likely to miss: `leave` removes the actor AND appends a "leaves
    # the scene" transition whenever the transcript is non-empty.
    with runs.scene_held_open(request.app, cid, sid):
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
    with runs.scene_held_open(request.app, cid, sid):
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
    with store.locks.campaign_lock(cid):
        try:
            store.actor_names.require_unique(name, scope="campaign", scope_id=cid)
        except store.actor_names.ActorNameError as exc:
            raise HTTPException(409, detail=str(exc)) from exc
        char, version = store.overlay.create_character(cid, name)
    try:
        # The seat is the transcript-touching half (`appear` appends a
        # transition), so the guard belongs around it rather than around the
        # create -- refusing after the character exists is the same shape as the
        # AppearError below, and the character is campaign state either way.
        with runs.scene_held_open(request.app, cid, sid):
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
        raise HTTPException(status_code=409, detail=str(exc)) from exc
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
        with runs.scene_held_open(request.app, cid, sid):
            result = store.scenes.set_location(cid, sid, body.location)
    except store.entities.EntityNotFound as exc:
        raise HTTPException(status_code=404, detail="location not found") from exc
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
        with runs.scene_held_open(request.app, cid, sid):
            result = store.scenes.set_datetime(cid, sid, body.datetime)
    except store.calendars.CalendarError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
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
    except store.campaigns.CampaignNotFound as exc:
        raise HTTPException(status_code=404, detail="campaign not found") from exc
    cfg = store.read_config()
    return _response_body(scene["meta"], campaign_meta, cfg, scene["meta"])


@router.put("/campaigns/{cid}/scenes/{sid}/response")
def put_scene_response(cid: str, sid: str, body: ResponseSettings):
    _require_scene(cid, sid)
    fields = {k: v for k, v in _dump(body).items() if v is not None}
    _write_response(lambda f: store.scenes.set_response(cid, sid, f), fields)
    return {"ok": True}


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
    `store.casefile`, whose docstring carries the argument for each. The one
    field added here is the portrait's token, for `_with_avatar_v`'s reason.
    """
    _require_scene(cid, sid)
    if kind not in store.appearances.ACTOR_KINDS:
        raise HTTPException(status_code=404, detail="unknown actor kind")
    try:
        built = store.casefile.build(cid, sid, kind, id)
    except (store.appearances.AppearError, store.characters.CharacterNotFound,
            store.characters.VersionNotFound, store.pcs.PCNotFound, store.pcs.PCVersionNotFound) as exc:
        raise HTTPException(status_code=404, detail="actor not found") from exc
    return _with_avatar_v(cid, built)


@router.get("/campaigns/{cid}/scenes/{sid}/cast/{kind}/{id}")
def get_cast_detail(cid: str, sid: str, kind: str, id: str):
    _require_scene(cid, sid)
    if kind not in store.appearances.ACTOR_KINDS:
        raise HTTPException(status_code=404, detail="unknown actor kind")
    try:
        detail = store.appearances.cast_detail(cid, sid, kind, id)
    except (store.appearances.AppearError, store.characters.CharacterNotFound,
            store.characters.VersionNotFound, store.pcs.PCNotFound, store.pcs.PCVersionNotFound) as exc:
        raise HTTPException(status_code=404, detail="actor not found") from exc
    return _with_avatar_v(cid, detail)


def _with_avatar_v(cid: str, actor: dict) -> dict:
    """`actor` with the token of the portrait its `version` resolves to, which
    the dossier and the record drawer spend as `?v=` -- the same field and the
    same reason as `GET /appearances`, for the one actor these two read. A
    casefile that could not name a version draws no portrait, so asks nothing."""
    vid = actor["version"]
    return {**actor, "avatar_v": (store.overlay.avatar_v(cid, actor["id"], vid, actor["kind"])
                                  if vid else None)}
