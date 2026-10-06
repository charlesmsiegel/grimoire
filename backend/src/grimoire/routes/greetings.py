"""Greetings, for both worlds (the authored library) and campaigns (the
picked copies), plus the image-subject tagging that hangs off them and the
routes that open a scene from a greeting."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from urllib.parse import quote

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from .. import store
from ..llm import LLMClient, effective_model
from ..llm_errors import LLMError
from . import runs
from . import tracker as tracker_routes
from .common import (
    _campaign_root_or_404,
    _fresh_or_409,
    _record_prompt,
    _require_connection,
    _require_scene,
    _world_char_version_or_404,
    _world_root_or_404,
    computes_only,
    get_llm,
    image_id_field,
    thumb_query,
)
from .models import (
    CopyFromGreeting,
    Edges,
    FirstPost,
    GreetingCreate,
    GreetingSubjectsBody,
    GreetingUpdate,
    ImportGreetings,
    MarkBody,
    Opener,
    StartFromGreeting,
    SubjectsBody,
)
from .streaming import StreamOutcome, _persist_reply

router = APIRouter()

log = logging.getLogger(__name__)


def _opener_cast(cid: str, sid: str) -> list[dict]:
    """The draft order and its adoption fence, including non-generated PCs."""
    actors = [{"actor_ref": "grimoire", "speaker": "Grimoire", "version": ""}]
    for entry in store.appearances.scene_cast(cid, sid):
        ref = f"{entry['kind']}:{entry['id']}"
        actor = {"actor_ref": ref, "speaker": entry["name"],
                 "version": store.appearances.locked_version(
                     cid, entry["kind"], entry["id"]) or ""}
        if entry["role"] == "npc":
            actors.append(actor)
        elif entry["role"] == "player":
            actors.append({**actor, "role": "player"})
    return actors


def _opener_speakers(cast: list[dict]) -> list[dict]:
    return [actor for actor in cast if actor.get("role") != "player"]


def _opener_parts(parts: list[dict], cast: list[dict]) -> list[dict]:
    """Only a complete prefix can be retried or adopted."""
    speakers = _opener_speakers(cast)
    if len(parts) > len(speakers):
        raise HTTPException(400, detail="too many opener contributions")
    cleaned = []
    for part, expected in zip(parts, speakers, strict=False):
        if part.get("actor_ref") != expected["actor_ref"] or part.get("speaker") != expected["speaker"]:
            raise HTTPException(409, detail="opener cast changed")
        prose = str(part.get("content", "")).strip()
        own = re.compile(r"^\*\*" + re.escape(expected["speaker"]) + r":\*\*\s*", re.IGNORECASE)
        prose = own.sub("", prose).strip()
        if not prose or re.search(r"\*\*[^\n*]+:\*\*", prose):
            raise HTTPException(400, detail="opener contribution needs unlabeled prose")
        cleaned.append({**expected, "content": prose})
    return cleaned


def _opener_frames(cid: str, sid: str, prompt: str, cast: list[dict],
                   completed: list[dict], conn: dict, client: LLMClient,
                   outcome: StreamOutcome, adapt: bool = False):
    async def frames():
        parts = list(completed)
        try:
            yield f"data: {json.dumps({'snapshot': cast})}\n\n"
            for actor in _opener_speakers(cast)[len(parts):]:
                messages, breakdown = store.context.compose_opener(
                    cid, sid, prompt, actor_ref=actor["actor_ref"], prior=parts,
                    describe=store.prompt_log.capturing(), model=effective_model(conn),
                    adapt=adapt)
                _record_prompt(cid, sid, "opener", breakdown,
                               model=effective_model(conn), kind=conn["kind"], messages=messages,
                               conn=conn)
                yield f"data: {json.dumps({'speaker_start': actor})}\n\n"
                meter = store.usage.meter("opener", campaign=cid, scene=sid)
                prose = ""
                try:
                    async for delta in client.stream(messages, conn, meter.usage):
                        if delta:
                            prose += delta
                            yield f"data: {json.dumps({'delta': delta})}\n\n"
                        else:
                            yield ": heartbeat\n\n"
                    meter.done()
                except LLMError as exc:
                    meter.done("error", exc.kind, detail=exc.detail)
                    outcome.fail(exc.kind, exc.detail)
                    yield f"data: {json.dumps({'error': {'kind': exc.kind, 'detail': exc.detail}})}\n\n"
                    return
                except BaseException:
                    meter.done("aborted")
                    raise
                try:
                    parts = _opener_parts([*parts, {**actor, "content": prose}], cast)
                except HTTPException as exc:
                    outcome.fail("invalid_response", str(exc.detail))
                    yield f"data: {json.dumps({'error': {'kind': 'invalid_response', 'detail': str(exc.detail)}})}\n\n"
                    return
                yield f"data: {json.dumps({'speaker_done': parts[-1]})}\n\n"
            outcome.land()
            yield f"data: {json.dumps({'done': True})}\n\n"
        except BaseException:
            raise
    return frames


def _provided(model, field: str) -> bool:
    fields = getattr(model, "model_fields_set", None)
    if fields is None:  # pydantic 1.x, retained for the Android build
        fields = model.__fields_set__
    return field in fields


def _with_edges(rows: list[dict], read_plotmap: Callable[[], dict]) -> list[dict]:
    """`rows` with each greeting's plot-map `edges` added -- exactly what that
    greeting's own read reports (`edges_of`) -- from ONE read of the map.

    Every greeting's edges live in that one file, yet the list used to leave
    them out, so a client that wanted them issued one GET per greeting: the
    world overview on every open, to decide a single checklist row, and the
    plot map to draw its lines. Dozens of reads, fired just as the reader
    reaches for the next page, where one small file was enough.

    A map that cannot be read leaves `edges` OFF every row rather than failing
    the list. The single-greeting read still fails on it, as it always has; but
    the list never read the map before it carried edges, and a garbled
    plotmap.json must not start taking the greetings tab, a character page and
    the image gallery down with it. Absent, not empty: a client told "no edges"
    would draw a plot with no lines and let its next whole-array write erase
    the ones it could not see, so absent means "could not say" and the clients
    treat it that way.

    `ValueError` is a file that does not parse (`JSONDecodeError`, and a
    `UnicodeDecodeError`, are both one). `AttributeError` is one that parses to
    the wrong shape -- a list, or an entry that is not an object -- which both
    `edges_of` and the overlay's detachment filter take as a mapping of
    mappings, and meet as `.get`/`.items` on something else. `TypeError` is
    the same garbage one level down: that filter tests every id in an edge
    list against a set, and an id that is itself a list or an object cannot
    be hashed. Either way the file is garbage, which is the only thing a row
    can say about it."""
    try:
        plotmap = read_plotmap()
        edges = {g["id"]: store.greetings.edges_of(plotmap, g["id"]) for g in rows}
    except (OSError, ValueError, AttributeError, TypeError):
        return rows
    return [{**g, "edges": edges[g["id"]]} for g in rows]


# ---- world greetings ----
@router.get("/worlds/{wid}/greetings")
def get_world_greetings(wid: str):
    root = _world_root_or_404(wid)
    return _with_edges(store.greetings.list_greetings(root),
                       lambda: store.greetings.read_plotmap(root))


@router.post("/worlds/{wid}/greetings")
def post_world_greeting(wid: str, body: GreetingCreate):
    gid = store.greetings.create_greeting(_world_root_or_404(wid), body.name, body.character,
                                          body.version, body.body, body.requires_tags,
                                          body.predecessor_join, present=body.present,
                                          pcless=body.pcless, location=body.location,
                                          phase=body.phase, sequence=body.sequence,
                                          optional=body.optional)
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
                                        character=body.character, version=body.version,
                                        phase=body.phase,
                                        sequence=(body.sequence if _provided(body, "sequence")
                                                  else store.greetings.UNSET),
                                        optional=body.optional)
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


@router.put("/worlds/{wid}/greetings/{gid}/subjects")
def put_world_greeting_subjects(wid: str, gid: str, body: GreetingSubjectsBody):
    root = _world_root_or_404(wid)
    _greeting_or_404(root, gid)
    key = store.greeting_images.image_key(root, gid, body.image)
    if key not in store.greeting_images.catalog(root, gid):
        raise HTTPException(status_code=404, detail="image not found")
    known = set(store.characters.character_refs(root))
    bad = [c for c in body.subjects if c not in known]
    if bad:
        raise HTTPException(status_code=400, detail=f"unknown characters: {bad}")
    try:
        store.image_subjects.set_image_subjects(root, gid, key, body.subjects)
    except ValueError as exc:
        # A body edit can remove the reference after the initial inventory.
        raise HTTPException(status_code=404, detail="image not found") from exc
    return {"ok": True}


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
    of full-resolution art.

    Plus `image_id` where the picture served is a placement, as every other
    listing carries it (`image_id_field`): the greeting's own, or the world
    image a body reference points at -- or a format-2 collection member, which
    is its object (its `v` is then the blob's sha). Reported only when the
    picture resolves to the very file the URLs were versioned from."""
    if "url" in a:
        base = a["url"]
        p = store.greeting_images.local_path(root, base) if base.startswith("/api/worlds/") else None
        if p is None:
            return {"url": base, "copyable": False}
        target = store.greeting_images.local_target(root, base)
    else:
        base = f"/api/worlds/{quote(wid, safe='')}/greetings/{quote(a['gid'], safe='')}/images/{quote(a['name'], safe='')}"
        p = store.assets.image_path(root, a["gid"], "default", a["name"], base="greetings")
        # `image_path` answering at all proves both ids safe
        target = ("slot", (store.assets.version_dir(root, a["gid"], "default", base="greetings"),
                           a["name"]), None) if p is not None else None
    if p is None:  # vanished between sweep and stat: bare URLs, still renderable
        return {"url": base, "thumb": base}
    v = store.assets.image_version(p)
    placed = store.greeting_images.resolve(target)
    ident = image_id_field(placed.image_id) if placed is not None and placed.blob_path == p else {}
    return {"url": f"{base}?v={v}", "thumb": f"{base}{thumb_query(v)}", **ident}


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
    # `overlay.read_plotmap`, as the single read below uses: the campaign's own
    # map if it forked one, else the world's with detached greetings filtered.
    return _with_edges([{**g, "mark": mark_of.get(g["id"])} for g in store.overlay.list_greetings(cid)],
                       lambda: store.overlay.read_plotmap(cid))


@router.post("/campaigns/{cid}/greetings")
def post_campaign_greeting(cid: str, body: GreetingCreate):
    _campaign_root_or_404(cid)
    gid = store.overlay.create_greeting(cid, body.name, body.character, body.version,
                                       body.body, body.requires_tags,
                                       body.predecessor_join, present=body.present,
                                       pcless=body.pcless, location=body.location,
                                       phase=body.phase, sequence=body.sequence,
                                       optional=body.optional)
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
                                     character=body.character, version=body.version,
                                     phase=body.phase,
                                     sequence=(body.sequence if _provided(body, "sequence")
                                               else store.greetings.UNSET),
                                     optional=body.optional)
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
def post_start_from_greeting(cid: str, sid: str, body: StartFromGreeting, request: Request,
                             client: LLMClient = Depends(get_llm)):
    _require_scene(cid, sid)
    try:
        # This module was the one an inventory of the freeze kept missing, for
        # the plain reason that every other guarded route is in `scenes.py` or
        # `mechanics.py`. Both routes here mutate a live scene: this one seats a
        # cast and writes the greeting as the opener, and can RENAME the scene
        # while doing it.
        with runs.scene_held_open(request.app, cid, sid):
            new_sid = store.playing.start_from_greeting(cid, sid, body.greeting,
                                                        seed_location=body.seed_location,
                                                        seed=body.seed)
    except store.greetings.GreetingNotFound:
        raise HTTPException(status_code=404, detail="greeting not found")
    except (store.playing.PlayError, store.appearances.AppearError) as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    # `new_sid`: starting from a greeting may rename the scene.
    _track_opening(request.app, cid, new_sid, client)
    return {"ok": True, "id": new_sid}


def _track_opening(app, cid: str, sid: str, client: LLMClient) -> None:
    """Track the posts a scene was just opened with -- a greeting, an adopted
    opener -- once they are written and outside the hold that wrote them.

    The scene was empty before, so every tracked post on it is new and
    `schedule_untracked` is exactly right. The migration first: these posts are
    written as plain replies, and a post is only trackable once the response
    ledger has given it an id (which opening the scene would do anyway).

    Never raises. The opening is on disk and the player is owed the 200; a
    tracker that cannot start is not a scene that failed to open."""
    try:
        # Gated here as well as in `schedule_untracked`: with the tracker off
        # nothing needs the ids yet, and the scene's next open assigns them as
        # it always has -- so this route's write stays what it was.
        if not store.tracker.settings.enabled(cid):
            return
        store.responses.migrate_if_needed(cid, sid)
    except Exception as exc:  # noqa: BLE001 -- see the docstring
        log.warning("tracker: could not assign response ids in %s/%s -- %s", cid, sid, exc)
        return
    tracker_routes.schedule_untracked(app, cid, sid, client)


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
    # `prompt` defaults to "" only so an adapt request need not send one; an
    # ordinary opener still needs a premise, as it did when the field was
    # required, rather than spending a call on context alone.
    if not body.adapt and not body.prompt.strip():
        raise HTTPException(status_code=422, detail="an opener needs a prompt")
    prompt = _greeting_to_adapt(cid, sid) if body.adapt else body.prompt
    conn = _require_connection("opener", cid)
    cast = _opener_cast(cid, sid)
    if body.snapshot and body.snapshot != cast:
        raise HTTPException(409, detail="opener cast changed")
    completed = _opener_parts(body.completed, cast)
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
        runs.start_detached(request.app, run, _opener_frames(
            cid, sid, prompt, cast, completed, conn, client, outcome, adapt=body.adapt),
            outcome=outcome.result)
        return runs.tail_response(run, 0, lead=runs.lead_frame(run))


def _greeting_to_adapt(cid: str, sid: str) -> str:
    """The body of the greeting this scene was started from, macro-expanded
    once -- the text an adapted opener rewrites (#91).

    Read off the scene's own `greeting` stamp, which `start_from_greeting`
    writes whether or not it posted the body, so a scene opened for adaptation
    still knows its greeting after a reload. Expanded HERE, once per request,
    exactly as the verbatim path expands it: `compose_opener` expands its prompt
    on every call and the opener calls it once per speaker, so a raw body would
    roll a `{{roll}}` or pick a `{{random}}` afresh for each speaker and the
    narrator and an NPC would be adapting two different greetings. Expanded
    text carries no macros, so `compose_opener`'s own pass leaves it alone.
    A retry of the remaining speakers is a new request and expands again, so a
    roll the completed speakers already wrote into the opening can differ from
    the one the rest are shown -- one request is the unit this keeps whole."""
    gid = store.scenes.read_scene_meta(cid, sid).get("greeting", "")
    if not gid:
        raise HTTPException(status_code=409, detail="this scene was not started from a greeting")
    try:
        body = store.overlay.read_greeting(cid, gid)["body"]
    except store.greetings.GreetingNotFound:
        raise HTTPException(status_code=409,
                            detail="the greeting this scene was started from no longer exists") from None
    # Judged on the EXPANSION: a body that is only macros (a `{{date}}` in an
    # undated scene) can expand to nothing, and adapting nothing is a premise-
    # less opener wearing the adapt instruction.
    text = store.context.expand_macros(body, store.context.scene_substitutions(cid, sid), cid, sid)
    if not text.strip():
        raise HTTPException(status_code=409, detail="the greeting this scene was started from is empty")
    return text


@router.post("/campaigns/{cid}/scenes/{sid}/first-post")
def post_first_post(cid: str, sid: str, body: FirstPost, request: Request,
                    client: LLMClient = Depends(get_llm)):
    """Adopt a generated opener as the scene's first (assistant) message. The cast is
    already set up in the panel, so this just persists the text onto an empty scene."""
    _require_scene(cid, sid)
    # The hold opens HERE, not around the append alone: "is the scene empty" and
    # "write the opener" are a read-modify-write, and a turn reserved between
    # them would append the opener under a run that composed its prompt from an
    # empty scene. It also puts the busy refusal ahead of the already-has-
    # messages one, which is the honest order -- a scene a turn is generating
    # into is refused for that reason, whatever else is true of it.
    with runs.scene_held_open(request.app, cid, sid), store.locks.campaign_lock(cid):
            if body.contributions:
                cast = _opener_cast(cid, sid)
                if body.snapshot != cast:
                    raise HTTPException(409, detail="opener cast changed")
                parts = _opener_parts(body.contributions, cast)
                if len(parts) != len(_opener_speakers(cast)):
                    raise HTTPException(409, detail="opener draft is incomplete")
                text = "\n\n".join(f"**{part['speaker']}:** {part['content']}" for part in parts)
            else:
                text = body.text
            adopted = _adopt_first_post(cid, sid, text)
    _track_opening(request.app, cid, sid, client)
    return adopted


def _adopt_first_post(cid: str, sid: str, text: str) -> dict:
    if store.scenes.read_scene(cid, sid)["messages"]:
        raise HTTPException(status_code=409, detail="scene already has messages")
    if not text.strip():
        raise HTTPException(status_code=400, detail="empty first post")
    # Judged on what LANDED, not on what was sent. Text can be non-empty and
    # still produce no post: a trailing ```state block is split off before the
    # reply is segmented (`state_fence`), and a bare speaker marker segments into
    # nothing. Either way `append_reply` writes no message, and answering `ok`
    # over a scene that is still empty loses the opener the user was adopting
    # with no error to show for it. Nothing has been written when the count is
    # zero, so the 400 leaves the scene exactly as it was.
    if not _persist_reply(cid, sid, text):
        raise HTTPException(status_code=400, detail="empty first post")
    return {"ok": True}
