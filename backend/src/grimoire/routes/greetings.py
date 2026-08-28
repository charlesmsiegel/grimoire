"""Greetings, for both worlds (the authored library) and campaigns (the
picked copies), plus the image-subject tagging that hangs off them and the
routes that open a scene from a greeting."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from .. import store
from ..llm import LLMClient
from . import runs
from .common import (
    THUMB_W,
    _campaign_root_or_404,
    _fresh_or_409,
    _record_prompt,
    _require_connection,
    _require_scene,
    _world_char_version_or_404,
    _world_root_or_404,
    computes_only,
    get_llm,
)
from .models import (
    CopyFromGreeting,
    Edges,
    FirstPost,
    GreetingCreate,
    GreetingUpdate,
    ImportGreetings,
    MarkBody,
    Opener,
    StartFromGreeting,
    SubjectsBody,
)
from .streaming import StreamOutcome, _persist_reply, ephemeral_frames

router = APIRouter()


# ---- world greetings ----
@router.get("/worlds/{wid}/greetings")
def get_world_greetings(wid: str):
    return store.greetings.list_greetings(_world_root_or_404(wid))


@router.post("/worlds/{wid}/greetings")
def post_world_greeting(wid: str, body: GreetingCreate):
    gid = store.greetings.create_greeting(_world_root_or_404(wid), body.name, body.character,
                                          body.version, body.body, body.requires_tags,
                                          body.predecessor_join, present=body.present,
                                          pcless=body.pcless, location=body.location)
    return {"id": gid}


@router.post("/worlds/{wid}/greetings/import")
def post_world_greetings_import(wid: str, body: ImportGreetings):
    root = _world_root_or_404(wid)
    try:
        gids = store.greetings.import_from_character(root, body.character, body.version)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    except store.characters.VersionNotFound:
        raise HTTPException(status_code=404, detail="version not found")
    return {"greetings": gids}


@router.get("/worlds/{wid}/greetings/{gid}")
def get_world_greeting(wid: str, gid: str):
    root = _world_root_or_404(wid)
    try:
        g = store.greetings.read_greeting_rev(root, gid)
    except store.greetings.GreetingNotFound:
        raise HTTPException(status_code=404, detail="greeting not found")
    plotmap = store.greetings.read_plotmap(root)
    g["edges"] = store.greetings.edges_of(plotmap, gid)
    g["predecessors"] = store.greetings.predecessors_of(plotmap, gid)
    return g


@router.put("/worlds/{wid}/greetings/{gid}")
def put_world_greeting(wid: str, gid: str, body: GreetingUpdate):
    root = _world_root_or_404(wid)
    # Greetings are one of `entities.SYNCED_KINDS`, stored at the same flat
    # `<root>/<kind>/<id>.md` path, so they hash through the same call sync.py
    # already uses for them (#35).
    _fresh_or_409(body.rev, store.entities.entity_hash(root, "greetings", gid))
    try:
        store.greetings.update_greeting(root, gid, name=body.name,
                                        body=body.body, requires_tags=body.requires_tags,
                                        predecessor_join=body.predecessor_join, present=body.present,
                                        pcless=body.pcless, location=body.location,
                                        character=body.character, version=body.version)
    except store.greetings.GreetingNotFound:
        raise HTTPException(status_code=404, detail="greeting not found")
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found") from None
    except store.characters.VersionNotFound:
        raise HTTPException(status_code=404, detail="version not found") from None
    return {"ok": True}


@router.put("/worlds/{wid}/greetings/{gid}/edges")
def put_world_greeting_edges(wid: str, gid: str, body: Edges):
    root = _world_root_or_404(wid)
    try:
        store.greetings.read_greeting(root, gid)
    except store.greetings.GreetingNotFound:
        raise HTTPException(status_code=404, detail="greeting not found")
    store.greetings.set_edges(root, gid, body.leads_to, body.excludes)
    return {"ok": True}


@router.delete("/worlds/{wid}/greetings/{gid}")
def delete_world_greeting(wid: str, gid: str):
    root = _world_root_or_404(wid)
    # `delete_greeting` unlinks the record and THEN cleans the world plot map, so
    # a malformed plotmap.json raises out of the second half with the record
    # already gone. The sweep has to run for it anyway (#225, Codex review): a
    # 500 that skipped it would leave every dependent campaign holding state for
    # an id the world can hand out again, and the retry 404s without sweeping.
    # Only the 404 path skips it -- there, nothing was removed.
    absent = False
    try:
        store.greetings.delete_greeting(root, gid)
    except store.greetings.GreetingNotFound:
        absent = True
        raise HTTPException(status_code=404, detail="greeting not found")
    finally:
        if not absent:
            store.overlay.forget_world_record(root, "greetings", gid)
    return {"ok": True}


# ---- greeting image subjects (who appears in each localized image) ----
def _greeting_or_404(root, gid: str) -> None:
    try:
        store.greetings.read_greeting(root, gid)
    except store.greetings.GreetingNotFound:
        raise HTTPException(status_code=404, detail="greeting not found")


@router.get("/worlds/{wid}/greetings/{gid}/subjects")
def get_world_greeting_subjects(wid: str, gid: str):
    root = _world_root_or_404(wid)
    _greeting_or_404(root, gid)
    return store.image_subjects.read_subjects(root, gid)


@router.get("/worlds/{wid}/greetings/{gid}/images/{name}/subjects")
def get_world_greeting_image_subjects(wid: str, gid: str, name: str):
    root = _world_root_or_404(wid)
    _greeting_or_404(root, gid)
    if store.assets.image_path(root, gid, "default", name, base="greetings") is None:
        raise HTTPException(status_code=404, detail="image not found")
    return {"subjects": store.image_subjects.read_subjects(root, gid).get(name, [])}


@router.put("/worlds/{wid}/greetings/{gid}/images/{name}/subjects")
def put_world_greeting_image_subjects(wid: str, gid: str, name: str, body: SubjectsBody):
    root = _world_root_or_404(wid)
    _greeting_or_404(root, gid)
    if store.assets.image_path(root, gid, "default", name, base="greetings") is None:
        raise HTTPException(status_code=404, detail="image not found")
    known = {c["id"] for c in store.characters.list_characters(root)}
    bad = [c for c in body.subjects if c not in known]
    if bad:
        raise HTTPException(status_code=400, detail=f"unknown characters: {bad}")
    store.image_subjects.set_image_subjects(root, gid, name, body.subjects)
    return {"ok": True}


def _greeting_image_urls(root, wid: str, a: dict) -> dict:
    """Versioned full + thumbnail URLs for one greeting image: both cache
    immutable, and the thumb keeps a 70-tile gallery from pulling 100MB+
    of full-resolution art."""
    base = f"/api/worlds/{wid}/greetings/{a['gid']}/images/{a['name']}"
    p = store.assets.image_path(root, a["gid"], "default", a["name"], base="greetings")
    if p is None:  # vanished between sweep and stat: bare URLs, still renderable
        return {"url": base, "thumb": base}
    v = store.assets.image_version(p)
    return {"url": f"{base}?v={v}", "thumb": f"{base}?w={THUMB_W}&v={v}"}


@router.get("/worlds/{wid}/subjects/untagged")
def get_world_untagged_images(wid: str):
    root = _world_root_or_404(wid)
    names = {g["id"]: g["name"] for g in store.greetings.list_greetings(root)}
    return [{**a, "greeting_name": names.get(a["gid"], a["gid"]),
             **_greeting_image_urls(root, wid, a)}
            for a in store.image_subjects.untagged(root)]


@router.get("/worlds/{wid}/characters/{cid}/appearances")
def get_world_character_appearances(wid: str, cid: str):
    root = _world_root_or_404(wid)
    names = {g["id"]: g["name"] for g in store.greetings.list_greetings(root)}
    return [{**a, "greeting_name": names.get(a["gid"], a["gid"]),
             **_greeting_image_urls(root, wid, a)}
            for a in store.image_subjects.appearances(root, cid)]


@router.post("/worlds/{wid}/characters/{cid}/versions/{vid}/images/copy-from-greeting")
def post_copy_image_from_greeting(wid: str, cid: str, vid: str, body: CopyFromGreeting):
    # the copy writes through `assets.put_image` like every other image write,
    # so it takes the same gate on the destination (#360)
    root = _world_char_version_or_404(wid, cid, vid)
    try:
        stored = store.image_subjects.copy_to_character(root, body.gid, body.name, cid, vid, body.slot)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="source image not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    p = store.assets.image_path(root, cid, vid, stored)
    return {"name": stored, "ext": p.suffix.lstrip(".").lower() if p else ""}


# ---- campaign greetings / play ----
@router.get("/campaigns/{cid}/greetings/available")
def get_available_greetings(cid: str, after: str | None = None):
    _campaign_root_or_404(cid)
    try:
        return store.playing.available_greetings(cid, after=after)
    except (store.scenes.SceneNotFound, store.campaigns.CampaignNotFound):
        # a scene path is built from campaign_root, so an unusable campaign id
        # surfaces here as CampaignNotFound -- still a 404, not a 500
        raise HTTPException(status_code=404, detail="scene not found")


@router.get("/campaigns/{cid}/greetings")
def get_campaign_greetings(cid: str):
    _campaign_root_or_404(cid)
    marks = store.playing.read_marks(cid)
    mark_of = dict.fromkeys(marks["played"], "played")
    mark_of.update(dict.fromkeys(marks["completed"], "completed"))
    mark_of.update(dict.fromkeys(marks["skipped"], "skipped"))
    return [{**g, "mark": mark_of.get(g["id"])} for g in store.overlay.list_greetings(cid)]


@router.post("/campaigns/{cid}/greetings")
def post_campaign_greeting(cid: str, body: GreetingCreate):
    _campaign_root_or_404(cid)
    gid = store.overlay.create_greeting(cid, body.name, body.character, body.version,
                                       body.body, body.requires_tags,
                                       body.predecessor_join, present=body.present,
                                       pcless=body.pcless, location=body.location)
    return {"id": gid}


@router.get("/campaigns/{cid}/greetings/{gid}")
def get_campaign_greeting(cid: str, gid: str):
    _campaign_root_or_404(cid)
    try:
        g = store.overlay.read_greeting_rev(cid, gid)
    except store.greetings.GreetingNotFound:
        raise HTTPException(status_code=404, detail="greeting not found")
    plotmap = store.overlay.read_plotmap(cid)
    g["edges"] = store.greetings.edges_of(plotmap, gid)
    g["predecessors"] = store.greetings.predecessors_of(plotmap, gid)
    return g


@router.put("/campaigns/{cid}/greetings/{gid}")
def put_campaign_greeting(cid: str, gid: str, body: GreetingUpdate):
    _campaign_root_or_404(cid)
    _fresh_or_409(body.rev, store.overlay.entity_rev(cid, "greetings", gid))
    try:
        store.overlay.update_greeting(cid, gid, name=body.name, body=body.body,
                                     requires_tags=body.requires_tags,
                                     predecessor_join=body.predecessor_join,
                                     present=body.present, pcless=body.pcless,
                                     location=body.location,
                                     character=body.character, version=body.version)
    except store.greetings.GreetingNotFound:
        raise HTTPException(status_code=404, detail="greeting not found")
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found") from None
    except store.characters.VersionNotFound:
        raise HTTPException(status_code=404, detail="version not found") from None
    return {"ok": True}


@router.put("/campaigns/{cid}/greetings/{gid}/edges")
def put_campaign_greeting_edges(cid: str, gid: str, body: Edges):
    _campaign_root_or_404(cid)
    try:
        store.overlay.read_greeting(cid, gid)
    except store.greetings.GreetingNotFound:
        raise HTTPException(status_code=404, detail="greeting not found")
    store.overlay.set_edges(cid, gid, body.leads_to, body.excludes)
    return {"ok": True}


@router.delete("/campaigns/{cid}/greetings/{gid}")
def delete_campaign_greeting(cid: str, gid: str):
    _campaign_root_or_404(cid)
    try:
        store.overlay.delete_greeting(cid, gid)
    except store.greetings.GreetingNotFound:
        raise HTTPException(status_code=404, detail="greeting not found")
    return {"ok": True}


@router.post("/campaigns/{cid}/greetings/{gid}/mark")
def post_campaign_greeting_mark(cid: str, gid: str, body: MarkBody):
    _campaign_root_or_404(cid)
    try:
        store.playing.mark_greeting(cid, gid, body.status)
    except store.greetings.GreetingNotFound:
        raise HTTPException(status_code=404, detail="greeting not found")
    except store.playing.PlayError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"ok": True}


@router.post("/campaigns/{cid}/scenes/{sid}/start-from-greeting")
def post_start_from_greeting(cid: str, sid: str, body: StartFromGreeting, request: Request):
    _require_scene(cid, sid)
    try:
        # This module was the one an inventory of the freeze kept missing, for
        # the plain reason that every other guarded route is in `scenes.py` or
        # `mechanics.py`. Both routes here mutate a live scene: this one seats a
        # cast and writes the greeting as the opener, and can RENAME the scene
        # while doing it.
        with runs.scene_held_free(request.app, cid, sid):
            new_sid = store.playing.start_from_greeting(cid, sid, body.greeting,
                                                        seed_location=body.seed_location)
    except store.greetings.GreetingNotFound:
        raise HTTPException(status_code=404, detail="greeting not found")
    except (store.playing.PlayError, store.appearances.AppearError) as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"ok": True, "id": new_sid}


@router.post("/campaigns/{cid}/scenes/{sid}/opener")
@computes_only
def post_opener(cid: str, sid: str, body: Opener, request: Request,
                client: LLMClient = Depends(get_llm),
                x_grimoire_attempt: str | None = Header(default=None)):
    """Generate a scene's first post, without writing it anywhere.

    **A `draft`, not a `turn`, and the distinction is about persistence rather
    than importance.** This route persists nothing: the text reaches the
    transcript only when the player accepts it through `post_first_post`, which
    takes `body.text` and makes no LLM call. Classing it as a `turn` would
    claim its result is already in the transcript when nothing has been
    written, so a locked phone would leave the run `landed` with its output
    owned by no one.

    It is still the longest generation on this side of the class boundary -- as
    long as a chat turn, and the first thing a new scene does -- which is why
    it is detached at all. As a `draft` it is re-attachable for the whole
    retention window: backgrounding the app used to lose the opener outright.

    **No exclusion key, deliberately.** An opener runs on a scene where no turn
    can be in flight, re-generating one the player did not like is an ordinary
    thing to do twice, and holding the scene would refuse the very
    `post_first_post` the opener exists to feed.

    `@computes_only` follows from the same fact as the class: nothing is
    persisted here, so a draft the reader discards must not move the campaign's
    write token and refuse somebody's clock price (#409). The one real write
    this path can make is a prompt capture, which is on by default and stamps
    for itself in `_record_prompt` -- the marker would otherwise have taken that
    write's stamp with it.
    """
    # BEFORE the preflight, exactly as `post_chat` does it: this is a REPLAY of
    # work that already ran, and a connection removed since would otherwise
    # answer `missing_key` for an opener sitting complete in a buffer.
    replay = runs.replay_attempt(request.app, cid, sid, x_grimoire_attempt)
    if replay is not None:
        return replay
    _require_scene(cid, sid)
    conn = _require_connection("opener", cid)
    messages, breakdown = store.context.compose_opener(
        cid, sid, body.prompt, describe=store.prompt_log.capturing())
    _record_prompt(cid, sid, "opener", breakdown)
    run, fresh = runs.reserve_scene_draft(request.app, cid, sid, "opener",
                                          x_grimoire_attempt)
    if not fresh:
        # A duplicate delivery of this exact POST. Replay the original rather
        # than spend a second opener-length call on the same prompt.
        return runs.tail_response(run, 0, lead=runs.lead_frame(run))
    with runs.reservation(request.app, run):
        # The box, not just the frames. `ephemeral_frames` handles an upstream
        # `LLMError` by emitting an error frame and finishing normally, so a
        # runner inferring success from a clean exhaustion would mark the run
        # `landed` with `error: null` -- and a client polling it would be told
        # an opener arrived whose only terminal frame says it did not.
        outcome = StreamOutcome()
        runs.start_detached(request.app, run, ephemeral_frames(
            messages, conn, client, task="opener", cid=cid, sid=sid,
            outcome=outcome), outcome=outcome.result)
        return runs.tail_response(run, 0, lead=runs.lead_frame(run))


@router.post("/campaigns/{cid}/scenes/{sid}/first-post")
def post_first_post(cid: str, sid: str, body: FirstPost, request: Request):
    """Adopt a generated opener as the scene's first (assistant) message. The cast is
    already set up in the panel, so this just persists the text onto an empty scene."""
    _require_scene(cid, sid)
    # The hold opens HERE, not around the append alone: "is the scene empty" and
    # "write the opener" are a read-modify-write, and a turn reserved between
    # them would append the opener under a run that composed its prompt from an
    # empty scene. It also puts the busy refusal ahead of the already-has-
    # messages one, which is the honest order -- a scene a turn is generating
    # into is refused for that reason, whatever else is true of it.
    with runs.scene_held_free(request.app, cid, sid):
        return _adopt_first_post(cid, sid, body.text)


def _adopt_first_post(cid: str, sid: str, text: str) -> dict:
    if store.scenes.read_scene(cid, sid)["messages"]:
        raise HTTPException(status_code=409, detail="scene already has messages")
    if not text.strip():
        raise HTTPException(status_code=400, detail="empty first post")
    # Judged on what LANDED, not on what was sent. Text can be non-empty and
    # still produce no post: a trailing tracker block is split off before the
    # reply is segmented (#120), and a bare speaker marker segments into
    # nothing. Either way `append_reply` writes no message, and answering `ok`
    # over a scene that is still empty loses the opener the user was adopting
    # with no error to show for it. Nothing has been written when the count is
    # zero, so the 400 leaves the scene exactly as it was.
    if not _persist_reply(cid, sid, text):
        raise HTTPException(status_code=400, detail="empty first post")
    return {"ok": True}
