"""Campaign-scoped routes: the campaign record, exports, the world->campaign
sync inbox, calendar and climate settings, group state, the campaign's own
copies of characters and PCs, change review (the rolling per-record delta, the
append-only journal behind it, and undo), the continuity ledger, and the
reader's context pins and excludes.

Scenes, weather, mechanics and greetings have their own modules; the generic
``/campaigns/{cid}/{kind}`` entity surface lives in ``entities``.
"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import (
    APIRouter,
    Depends,
    File,
    Header,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.responses import Response

from .. import store
from ..llm import LLMClient
from . import runs
from .common import (
    _campaign_root_or_404,
    _content_fields,
    _display_name_or_400,
    _dump,
    _page_of,
    _page_window,
    _require_connection,
    _response_body,
    _routing_body,
    _routing_fields,
    _serve_image,
    _serve_image_file,
    _upload_image_ext,
    _with_descriptions,
    _write_response,
    computes_only,
    draft_completion,
    get_llm,
    image_draft_prompt,
)
from .models import (
    AdvanceTime,
    AvatarFocus,
    CalendarConfig,
    CampaignClimate,
    CharacterCreate,
    CopyFromGreeting,
    DefaultVersion,
    ForkCampaign,
    GroupStateSave,
    ImageDescription,
    NameBody,
    NewCampaign,
    NoticeMark,
    PCCreate,
    PCUpdate,
    PersonaVersionCreate,
    PersonaVersionUpdate,
    PickBody,
    PinRule,
    RefList,
    ResponseSettings,
    RoutingUpdate,
    ScheduledEventCreate,
    ScheduledEventEdit,
    VersionCreate,
    VersionUpdate,
    VoiceAnchorSave,
)

router = APIRouter()


# ---- group state (#47): campaign-local, not covered by generic entity CRUD
# (path shape groups/{gid}/state cannot collide with /{kind}/{eid} or its
# /images sub-paths, so order relative to the generic routes doesn't matter)
@router.get("/campaigns/{cid}/groups/{gid}/state")
def get_group_state(cid: str, gid: str):
    if not store.campaigns.campaign_exists(cid):
        raise HTTPException(status_code=404, detail="campaign not found")
    try:
        store.overlay.read_entity(cid, "groups", gid)
    except store.entities.EntityNotFound:
        raise HTTPException(status_code=404, detail="group not found")
    st = store.groupstate.read_state(store.campaigns.campaign_root(cid), gid)
    if st is None:
        return {**dict.fromkeys(store.groupstate.FIELDS, ""), "updated": ""}
    return st


@router.put("/campaigns/{cid}/groups/{gid}/state")
def put_group_state(cid: str, gid: str, body: GroupStateSave):
    if not store.campaigns.campaign_exists(cid):
        raise HTTPException(status_code=404, detail="campaign not found")
    try:
        store.overlay.read_entity(cid, "groups", gid)
    except store.entities.EntityNotFound:
        raise HTTPException(status_code=404, detail="group not found")
    values = {"goals": body.goals, "resources": body.resources, "focus": body.focus,
              "public_perception": body.public_perception, "secrets": body.secrets}
    name = _record_name(cid, "groups", gid) or gid
    with store.undo.journalled(cid, {"w": "group_state", "id": gid},
                               kind="group_state", ref={"kind": "groups", "id": gid},
                               field="body", label=f"{name} — group state"):
        store.groupstate.write_state(store.campaigns.campaign_root(cid), gid,
                                     store.groupstate.compose_body(values))
    return {"ok": True}


@router.get("/campaigns/{cid}/climate")
def get_campaign_climate(cid: str):
    """The campaign's default climate id, and the resolved document."""
    _campaign_root_or_404(cid)
    resolved = store.campaign_climate.resolve_default(cid)
    return {"default_climate": resolved["id"], "climate": resolved}


@router.put("/campaigns/{cid}/climate")
def put_campaign_climate(cid: str, body: CampaignClimate):
    """Change the default after creation.

    Without this the create-time write is the only thing that ever sets a
    campaign's default climate, so it would be immutable short of hand-editing
    the store or tagging every location individually.
    """
    _campaign_root_or_404(cid)
    try:
        store.campaign_climate.write_default(cid, body.default_climate)
    except store.climates.ClimateError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"could not write climate: {e}")
    return {"ok": True, "default_climate": body.default_climate}


# ---- campaigns ----
@router.get("/campaigns")
def get_campaigns():
    out = []
    for c in store.campaigns.list_campaigns():
        scene_list = store.scenes.list_scenes(c["id"])
        # `updated` is campaign.md's, which only metadata writes advance --
        # playing a scene touches the scene file and nothing else, so ordering
        # by it ranks a campaign renamed months ago above one played into last
        # night. `activity` is the whole campaign's high-water mark, for
        # anything answering "what was I last working on".
        #
        # Through `best_stamp` rather than a bare `max`, and over EVERY scene
        # rather than `scene_list[0]`. The fold is lexical, so one unparseable
        # or far-future value anywhere in it outranks every genuine timestamp
        # and then blocks its own replacement -- and `list_scenes` sorts by the
        # very field that may be the bad one, so element zero is only the
        # newest if the sort key can be trusted. The list is already in memory
        # for the count; validating it costs a strptime per scene.
        # How many scenes carry an absorb mark, which is how much of the
        # chronicle, the ledger and every dossier is caught up. It is not the
        # same question as "how many scenes are there" and a campaign answers
        # them differently the moment you play one scene ahead of the absorb --
        # which is the normal state of a campaign being played. A count rather
        # than the newest absorbed title because the shelf line reads it as a
        # fraction of the scene count, and a gap in the middle (an older scene
        # left unabsorbed under a newer one that was) shows up in a count and
        # is invisible in a high-water mark.
        absorbed = sum(1 for s in scene_list if s.get("done"))
        out.append({**c, "scenes": len(scene_list),
                    "cover": store.covers.cover_version(c["id"]),
                    "last_scene": scene_list[0]["title"] if scene_list else "",
                    "absorbed": absorbed,
                    "activity": store.campaigns.best_stamp(
                        c["updated"], store.campaigns.read_activity(c["id"]),
                        *(s["updated"] for s in scene_list))})
    return out


@router.get("/campaigns/{cid}/calendar")
def get_calendar_config(cid: str):
    if not store.campaigns.campaign_exists(cid):
        raise HTTPException(status_code=404, detail="campaign not found")
    return store.calendars.read_calendar(store.campaigns.campaign_root(cid))


@router.put("/campaigns/{cid}/calendar")
def put_calendar_config(cid: str, body: CalendarConfig):
    if not store.campaigns.campaign_exists(cid):
        raise HTTPException(status_code=404, detail="campaign not found")
    # `stale_after_days` and `warn_days` ride with the rest of the campaign's
    # time config (#103, #106).
    # A client sending 0 -- or an older one not sending it at all -- means "no
    # opinion", and the store answers that with its own default rather than
    # storing a threshold that would call every record stale on the day it was
    # written. That coercion lives in `calendars._stale_days`, once.
    root = store.campaigns.campaign_root(cid)
    cfg = {"primary": body.primary, "secondary": body.secondary, "confirmed": body.confirmed,
           "stale_after_days": body.stale_after_days,
           # `None` is "the request said nothing about it", which must keep what
           # is stored rather than reset it -- see `calendars.warn_days_for_save`.
           "warn_days": store.calendars.warn_days_for_save(root, body.warn_days)}
    try:
        store.calendars.validate_calendar(cfg)
    except store.calendars.CalendarError as e:
        raise HTTPException(status_code=400, detail=str(e))
    store.calendars.write_calendar(root, cfg)
    return {"ok": True}


# ---- the campaign clock (#100) ----
#
# Declared here, in a module included before ``entities``, so
# ``/campaigns/{cid}/{kind}`` cannot capture ``advance`` or ``clock``.
# ``test_route_order.py`` is what actually holds that.


def _clock_friendly(cid: str, native: str) -> str:
    """`native` in the campaign's own reckoning, or "" when it cannot be read.

    The clock is a pre-fill and a header line; a calendar the campaign cannot
    load is worth an empty label, never a 500 on the page that would let the
    reader fix it.
    """
    provider = store.calendars.primary_provider(store.campaigns.campaign_root(cid))
    if not native or provider is None:
        return ""
    try:
        return store.calendars.friendly(provider, native)
    except store.calendars.CalendarError:
        return ""


@router.get("/campaigns/{cid}/clock")
def get_campaign_clock(cid: str):
    """The campaign's current moment and how it got there, newest entry last.

    `now` is the stored clock when there is one and the latest chronicle date
    when there is not, so this answers for a campaign that has never advanced.
    """
    _campaign_root_or_404(cid)
    clock = store.clock.state(cid)   # one read for the moment and the log both
    return {"now": clock["now"], "friendly": _clock_friendly(cid, clock["now"]),
            "log": clock["log"]}


@router.post("/campaigns/{cid}/advance/preview")
def post_advance_preview(cid: str, body: AdvanceTime):
    """The digest the same body would produce, writing nothing — so the reader
    sees what a skip crosses *before* confirming it. Deterministic, so the
    preview and the advance that follows agree."""
    _campaign_root_or_404(cid)
    try:
        return {"digest": store.clock.preview(cid, to=body.to, days=body.days)}
    except (store.clock.ClockError, store.calendars.CalendarError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/campaigns/{cid}/advance")
def post_advance(cid: str, body: AdvanceTime):
    """Move the campaign clock, recording why, and report what was crossed.

    A reason is required: the log exists to answer "why is it suddenly March?",
    and an entry without one cannot. Nothing is written to any transcript —
    `PUT .../scenes/{sid}/datetime` still owns the one line a time change puts
    in a scene.
    """
    _campaign_root_or_404(cid)
    if not body.reason.strip():
        raise HTTPException(status_code=400, detail="an advance needs a reason")
    try:
        result = store.clock.advance(cid, to=body.to, days=body.days, reason=body.reason)
    except (store.clock.ClockError, store.calendars.CalendarError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    # Taken off the digest rather than resolved again: the digest's target IS the
    # new `now` (both branches — a no-op advance returns the moment it was already
    # at), and resolving the provider a second time re-imports and re-runs a
    # user-authored calendar plugin for a string we are already holding.
    return {"ok": True, **result, "friendly": result["digest"]["to_friendly"]}


# ---- scheduled events (#101) ----
#
# Campaign-scoped and dated in the campaign's own calendar: a plot beat, a
# coronation, the night a debt comes due. Declared here for the reason the clock
# routes above are -- ``/campaigns/{cid}/{kind}`` would otherwise capture
# ``events`` -- and ``test_route_order.py`` is what actually holds that.
#
# The fire stamp is not writable through this CRUD. It is the clock's to write
# (``store.clock``), and the one way back is ``POST .../unfire``, which says so.


def _event_provider(cid: str):
    """The campaign's primary provider, or None. Only for labelling and order.

    A campaign whose calendar will not load can still read and edit its events —
    it simply gets them unlabelled and in id order rather than by date. Refusing
    the list instead would hide the very rows whose dates the reader has to fix.
    """
    return store.calendars.primary_provider(store.campaigns.campaign_root(cid))


@router.get("/campaigns/{cid}/events")
def get_campaign_events(cid: str):
    """Every scheduled event, soonest first, each with its fire stamp.

    The campaign's present rides along, and each row says whether the clock has
    gone by it unfired (`passed`). That state is reachable — schedule a beat for
    a day already behind the clock and no advance can ever cross it — and
    nothing else in the app would mention it. Resolved here rather than in the
    panel because the comparison is calendar arithmetic, which is the server's.
    """
    _campaign_root_or_404(cid)
    provider = _event_provider(cid)
    now = store.clock.now(cid)
    now_fixed = None
    if provider is not None and now:
        try:
            now_fixed = store.calendars.fixed_of(provider, now)
        except store.calendars.CalendarError:
            now_fixed = None   # a present this calendar cannot read marks nothing
    return {"events": store.events.list_events(cid, provider, now_fixed),
            "now": now, "friendly": _clock_friendly(cid, now)}


@router.post("/campaigns/{cid}/events")
def post_campaign_event(cid: str, body: ScheduledEventCreate):
    """File a new event. 400 on a date this campaign's calendar cannot read.

    A date is required in substance rather than by the model: the sentence a
    reader needs is the calendar's ("not a valid date in this calendar"), and a
    422 about a missing field would replace it with a worse one.
    """
    _campaign_root_or_404(cid)
    try:
        eid = store.events.create(cid, body.name, body.date, body.note)
    except store.calendars.CalendarError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except store.events.EventError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "id": eid}


@router.put("/campaigns/{cid}/events/{eid}")
def put_campaign_event(cid: str, eid: str, body: ScheduledEventEdit):
    _campaign_root_or_404(cid)
    try:
        found = store.events.update(cid, eid, name=body.name, date=body.date,
                                    note=body.note)
    except store.calendars.CalendarError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except store.events.EventError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not found:
        raise HTTPException(status_code=404, detail="event not found")
    return {"ok": True}


@router.post("/campaigns/{cid}/events/{eid}/unfire")
def post_campaign_event_unfire(cid: str, eid: str):
    """Take back a fire stamp — the reader's undo for an advance made by mistake."""
    _campaign_root_or_404(cid)
    try:
        found = store.events.unfire(cid, eid)
    except store.events.EventError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not found:
        raise HTTPException(status_code=404, detail="event not found")
    return {"ok": True}


@router.delete("/campaigns/{cid}/events/{eid}")
def delete_campaign_event(cid: str, eid: str):
    _campaign_root_or_404(cid)
    try:
        found = store.events.delete(cid, eid)
    except store.events.EventError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not found:
        raise HTTPException(status_code=404, detail="event not found")
    return {"ok": True}


# ---- warn-once pre-notices (#106) ----
#
# Declared here for the reason the clock and events routes above are --
# ``/campaigns/{cid}/{kind}`` would otherwise capture ``notices`` -- and
# ``test_route_order.py`` is what actually holds that.


@router.get("/campaigns/{cid}/notices")
def get_campaign_notices(cid: str):
    """What is imminent and unacknowledged, judged from the campaign clock.

    The campaign-wide surface: scene planning happens before there is a scene to
    ask from, so this asks from the clock's present (#100) rather than from a
    moment. ``GET .../scenes/{sid}/datetime`` answers the same question from the
    scene's own date, which is the sharper one when there is a scene — a
    flashback should not be warned about next week.

    `warn_days` rides along so the surface can say what window it is reporting,
    and 0 is a campaign that has switched the warnings off, not an empty one.
    """
    root = _campaign_root_or_404(cid)
    now = store.clock.now(cid)
    return {"notices": store.notices.pending(cid, root, now) if now else [],
            "now": now, "warn_days": store.calendars.warn_days(root)}


@router.post("/campaigns/{cid}/notices")
def post_campaign_notices(cid: str, body: NoticeMark):
    """Acknowledge these notices. Idempotent, and campaign-wide.

    Never called on the reader's behalf by anything that merely *renders* a
    notice — the model being told what is upcoming every turn is a different
    channel from the reader being warned once, and the ledger only records the
    second (`store/notices.py`).
    """
    _campaign_root_or_404(cid)
    return {"ok": True, "marked": store.notices.mark(cid, body.keys, body.scene)}


@router.post("/campaigns/{cid}/notices/forget")
def post_campaign_notices_forget(cid: str, body: NoticeMark):
    """Take back an acknowledgement, so the notice shows again.

    The undo for a banner dismissed by mistake, and the only way back: `mark`
    will not overwrite an existing stamp, so without this one misclick silences
    an event until its day has gone by.
    """
    _campaign_root_or_404(cid)
    return {"ok": True, "forgotten": store.notices.forget(cid, body.keys)}


@router.get("/campaigns/{cid}/calendar/months")
def get_calendar_months(cid: str, year: int):
    if not store.campaigns.campaign_exists(cid):
        raise HTTPException(status_code=404, detail="campaign not found")
    cfg = store.calendars.read_calendar(store.campaigns.campaign_root(cid))
    try:
        return {"months": store.calendars.get_provider(cfg["primary"]).months(year)}
    except store.calendars.CalendarError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/campaigns")
def post_campaign(body: NewCampaign):
    try:
        cid = store.campaigns.create_campaign(body.name, body.world,
                                              body.region, body.calendar,
                                              body.module, body.climate)
        store.config.mark_setup_done()   # see routes/worlds.post_world (#194)
        return {"id": cid}
    except store.worlds.WorldNotFound:
        raise HTTPException(status_code=400, detail="world not found")
    except store.calendars.CalendarError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except store.climates.ClimateError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except store.modules.ModuleNotFound:
        raise HTTPException(status_code=404, detail="module not found")


@router.get("/campaigns/{cid}")
def get_campaign(cid: str):
    try:
        store.campaigns.ensure_campaign_slim(cid)
        out = store.campaigns.read_campaign(cid)
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")
    # embedded so the frontend needn't chain a world fetch after this one
    wid = out["meta"].get("world", "")
    out["meta"]["world_name"] = store.worlds.world_name(wid) or wid
    # Derived, like world_name above -- nothing about the cover is written into
    # campaign.md, whose unlocked read-modify-writers would race it.
    out["meta"]["cover"] = store.covers.cover_version(cid)
    return out


@router.get("/campaigns/{cid}/export.epub")
def export_campaign_epub(cid: str):
    try:
        blob, filename = store.epub.build_epub(cid)
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")
    return Response(content=blob, media_type="application/epub+zip",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/campaigns/{cid}/export.md.zip")
def export_campaign_markdown(cid: str):
    try:
        blob, filename = store.export.build_markdown_bundle(cid)
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")
    return Response(content=blob, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/campaigns/{cid}/export.html")
def export_campaign_html(cid: str):
    try:
        blob, filename = store.export.build_html(cid)
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")
    return Response(content=blob, media_type="text/html",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/campaigns/{cid}/export.txt")
def export_campaign_text(cid: str):
    try:
        blob, filename = store.export.build_text(cid)
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")
    return Response(content=blob, media_type="text/plain",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/campaigns/{cid}/export.json")
def export_campaign_json(cid: str):
    try:
        blob, filename = store.export.build_json(cid)
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")
    return Response(content=blob, media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


# ---- the campaign's cover image (store/covers.py) --------------------------
# Declared here, in `campaigns`, which `routes/__init__` includes BEFORE
# `entities` -- `/campaigns/{cid}/{kind}` would otherwise capture `cover`.
@router.get("/campaigns/{cid}/cover")
def get_campaign_cover(cid: str, request: Request):
    _campaign_root_or_404(cid)
    p = store.covers.cover_path(cid)
    if p is None:
        raise HTTPException(status_code=404, detail="cover not found")
    return _serve_image_file(p, request)


@router.put("/campaigns/{cid}/cover")
async def put_campaign_cover(cid: str, file: UploadFile = File(...)):
    _campaign_root_or_404(cid)
    try:
        # Check the size BEFORE reading. `read()` below materializes the whole
        # upload as a single `bytes` object, and that allocation -- not the
        # receipt -- is what `MAX_BYTES` exists to bound: the backend is packaged
        # verbatim into the Android app (Chaquopy), where a 300 MB image would
        # OOM the process before a 413 could be composed. Starlette spools the
        # body to disk above 1 MB and populates `UploadFile.size`, so this costs
        # nothing and the bytes are already on disk by the time we look.
        #
        # `covers.validate` still re-checks `len(data)`, and must: `size` is
        # Optional in the ASGI contract, so a client (or a future transport)
        # that leaves it None would otherwise buy an unbounded read.
        if file.size is not None and file.size > store.covers.MAX_BYTES:
            raise store.covers.CoverTooLarge(store.covers.TOO_LARGE)
        data = await file.read()
        # `validate` also names the extension to store under, from the format it
        # decoded -- `file.filename` is not consulted at all, so a JPEG uploaded
        # as `cover.png` cannot be served (or manifested in the EPUB) as PNG.
        ext = store.covers.validate(data)
    except store.covers.CoverTooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc))
    except store.covers.CoverInvalid as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    try:
        stored = store.covers.put_cover(cid, data, ext)
    except ValueError as exc:
        # Nothing `validate` returns is an extension `put_in` refuses today, so
        # this is unreachable from here -- kept because what is storable is the
        # store's decision to make, not this route's to assume.
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ext": stored, "v": store.covers.cover_version(cid)}


@router.delete("/campaigns/{cid}/cover")
def delete_campaign_cover(cid: str):
    _campaign_root_or_404(cid)
    try:
        store.covers.delete_cover(cid)
    except OSError:
        # `delete_cover` confirms the removal rather than swallowing a failed
        # unlink, so this is a cover that is genuinely still there -- a held
        # file on Windows, a read-only store. Reporting 200 would be a lie.
        raise HTTPException(status_code=500, detail="cover could not be removed")
    return {"ok": True}


# ---- the campaign's own image library (store/campaign_images.py) ----------
# Declared here for the same reason the cover is: `routes/__init__` includes
# `campaigns` before `entities`, whose `/campaigns/{cid}/{kind}` would otherwise
# capture `images`.
@router.post("/campaigns/{cid}/images/{name}/description/draft", status_code=202)
def post_campaign_library_description_draft(
        cid: str, name: str, request: Request,
        client: LLMClient = Depends(get_llm),
        x_grimoire_attempt: str | None = Header(default=None)):
    """Start a model-drafted first pass at what a library picture shows.

    No subject name: this art belongs to the campaign and to no record, which is
    the whole reason the library exists. The template simply asks what is in the
    picture.

    202 and a run to poll, like every other computing draft: the call is a
    multimodal one over a whole image and is the slowest of the previews, so it
    is also the one most likely to be running when the phone locks.
    """
    _campaign_root_or_404(cid)
    conn, messages = image_draft_prompt(
        store.campaign_images.image_path(cid, name), "", cid=cid)

    async def work():
        return await draft_completion(
            client, conn, messages, "image-description",
            lambda text: {"description": store.image_drafts.parse_output(text)},
            cid=cid)

    return runs.run_draft(request.app, runs.campaign_subject(cid),
                          "image-description", x_grimoire_attempt, work)


@router.put("/campaigns/{cid}/images/{name}/description")
def put_campaign_library_image_description(cid: str, name: str, body: ImageDescription):
    _campaign_root_or_404(cid)
    try:
        store.campaign_images.set_description(cid, name, body.description)
    except ValueError:
        # `from None`: the strict-write ValueError is this module's own
        # implementation detail, and chaining it onto the 404 says nothing a
        # caller can act on.
        raise HTTPException(status_code=404, detail="image not found") from None
    return {"ok": True}


@router.get("/campaigns/{cid}/images")
def list_campaign_library(cid: str):
    _campaign_root_or_404(cid)
    images = store.campaign_images.list_images(cid)
    return _with_descriptions(
        images,
        store.image_descriptions.read_in(store.campaign_images.images_dir(cid),
                                         names={i["name"] for i in images}))


@router.get("/campaigns/{cid}/images/undescribed")
def list_campaign_undescribed_images(cid: str):
    """This campaign's OWN undescribed art — the queue's campaign half.

    Registered before `/images/{name}`, which would otherwise match
    "undescribed" as an image name.

    Campaign-side only, and deliberately so: art the campaign inherits belongs
    to the world's queue, where describing it once serves every campaign on
    that world. What is left is exactly what the world queue cannot reach —
    the campaign's own image library, which hangs off no record at all, and
    images a campaign has diverged, whose bytes differ from the world's and so
    need words of their own.
    """
    root = _campaign_root_or_404(cid)
    out: list[dict] = []
    lib = store.campaign_images.images_dir(cid)
    reviewed = store.image_descriptions.read_raw(lib)
    out.extend({"kind": "campaign", "id": "", "vid": "", "name": image["name"],
                "record_name": "Campaign library",
                "url": f"/api/campaigns/{cid}/images/{quote(image['name'], safe='')}"}
               for image in store.campaign_images.list_images(cid)
               if image["name"] not in reviewed)

    # One read per record, name and versions together -- see the world queue's
    # `_record_name_and_versions` for why both, and why one memo.
    seen: dict[tuple[str, str], tuple[str, set[str]] | None] = {}
    for base in ("characters", store.pcs.ASSET_BASE, *store.entities.ENTITY_KINDS):
        for item in store.image_descriptions.undescribed(root, base):
            key = (base, item["id"])
            if key not in seen:
                seen[key] = _campaign_record_name_and_versions(cid, base, item["id"])
            found = seen[key]
            if found is None or (found[1] and item["vid"] not in found[1]):
                continue
            out.append({"kind": base, "id": item["id"], "vid": item["vid"],
                        "name": item["name"], "record_name": found[0],
                        "url": _campaign_image_url(cid, base, item)})
    return out


def _campaign_record_name_and_versions(cid: str, base: str,
                                       rid: str) -> tuple[str, set[str]] | None:
    """`_record_name_and_versions` (routes/characters.py) through the overlay."""
    try:
        if base == "characters":
            d = store.overlay.read_character(cid, rid)
            return str(d["meta"]["name"]), {v["id"] for v in d["versions"]}
        if base == store.pcs.ASSET_BASE:
            d = store.overlay.read_pc(cid, rid)
            return str(d["meta"]["name"]), {v["id"] for v in d["versions"]}
        return str(store.overlay.read_entity(cid, base, rid)["meta"]["name"]), set()
    except (store.characters.CharacterNotFound, store.pcs.PCNotFound,
            store.entities.EntityNotFound, KeyError, OSError, UnicodeDecodeError):
        return None


def _campaign_image_url(cid: str, base: str, item: dict) -> str:
    if base in ("characters", store.pcs.ASSET_BASE):
        return (f"/api/campaigns/{cid}/{base}/{quote(item['id'], safe='')}"
                f"/versions/{quote(item['vid'], safe='')}"
                f"/images/{quote(item['name'], safe='')}")
    return (f"/api/campaigns/{cid}/{base}/{quote(item['id'], safe='')}"
            f"/images/{quote(item['name'], safe='')}")


@router.get("/campaigns/{cid}/images/{name}")
def get_campaign_library_image(cid: str, name: str, request: Request):
    _campaign_root_or_404(cid)
    p = store.campaign_images.image_path(cid, name)
    if p is None:
        raise HTTPException(status_code=404, detail="image not found")
    return _serve_image_file(p, request)


@router.put("/campaigns/{cid}/images/{name}")
async def put_campaign_library_image(cid: str, name: str, file: UploadFile = File(...)):
    _campaign_root_or_404(cid)
    # The name, BEFORE a byte is written and before the body is read (#373).
    # `assets.put_in` creates the directory it writes into, so a name that got
    # past this would file bytes under a token the picker can never insert and
    # this app can never show -- reported to the caller as a successful upload.
    # `put_image` refuses it too; this is what makes the refusal a 400 with a
    # reason rather than a `ValueError` mapped after the fact.
    if not store.campaign_images.addressable(name):
        raise HTTPException(status_code=400,
                            detail="image name cannot be used in a link")
    # Size BEFORE the read, exactly as `put_campaign_cover` does and for the
    # same reason: `read()` materializes the whole upload as one `bytes` object,
    # and on Android (Chaquopy) that allocation is what OOMs the process before
    # a 413 could be composed. `validate_size` is the belt to those braces --
    # see it for why `file.size` alone is not enough.
    if file.size is not None and file.size > store.campaign_images.MAX_BYTES:
        raise HTTPException(status_code=413, detail=store.campaign_images.TOO_LARGE)
    data = await file.read()
    try:
        store.campaign_images.validate_size(data)
    except store.campaign_images.ImageTooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc))
    ext = _upload_image_ext(data)  # the bytes name the type, not `file.filename` (#321)
    try:
        stored = store.campaign_images.put_image(cid, name, data, ext)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    # `v` so the client can build the immutable `?v=` URL without a second round
    # trip. It resolves and stats, and answers "" rather than raising if the
    # file went between the two -- a write that landed must not report a 500.
    return {"name": name, "ext": stored,
            "v": store.campaign_images.image_version(cid, name)}


@router.delete("/campaigns/{cid}/images/{name}")
def delete_campaign_library_image(cid: str, name: str):
    _campaign_root_or_404(cid)
    # Deliberately NOT gated by `_library_name_or_400`, unlike the put. That
    # gate exists to stop unreachable bytes being *created*; a file already on
    # disk under a name the picker will not offer -- one a sync client dropped
    # -- is exactly the stray this store can hold, and refusing to remove it
    # would leave it with no way out of the app at all. `assets.delete_in`
    # still applies its own name rules.
    try:
        store.campaign_images.delete_image(cid, name)
    except OSError:
        # `delete_image` confirms the removal rather than swallowing a failed
        # unlink, so this is a file that is genuinely still there -- held by a
        # sync client on Windows, or a read-only store. 200 would be a lie.
        raise HTTPException(status_code=500, detail="image could not be removed")
    return {"ok": True}


@router.put("/campaigns/{cid}")
def put_campaign(cid: str, body: NameBody):
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    try:
        store.campaigns.rename_campaign(cid, name)
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")
    return {"id": cid, "name": name}


# Declared here rather than in `entities`, which registers the
# `/campaigns/{cid}/{kind}` catch-alls -- `routes/__init__` includes that module
# last precisely so a literal third segment like `fork` is still reachable.
@router.post("/campaigns/{cid}/fork")
def post_campaign_fork(cid: str, body: ForkCampaign):
    """Fork `cid` into a new campaign, optionally cut back to an earlier scene.

    Returns the fork's id alongside the store's own report of the cut, which
    the client shows verbatim: a retrospective fork restores what carries the
    scene's id and reports the rest rather than pretending (`store/fork.py`).
    """
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    # "" and None mean the same thing here -- a client that always sends the
    # field should not get a 404 for a scene called "".
    from_scene = (body.from_scene or "").strip() or None
    try:
        return store.fork.fork_campaign(cid, name, from_scene)
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")
    except store.SceneNotFound:
        raise HTTPException(status_code=404, detail="scene not found")


@router.delete("/campaigns/{cid}")
def delete_campaign(cid: str, request: Request):
    try:
        store.campaigns.delete_campaign(cid)
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")
    # AFTER the delete, so a campaign that could not be removed keeps its runs.
    # Campaign ids are slugs and a slug is reusable, so a replacement created
    # inside the retention window would otherwise inherit this one's drafts --
    # see `RunRegistry.forget_subject`.
    runs.forget_subject(request.app, runs.campaign_subject(cid))
    return {"ok": True}


@router.get("/campaigns/{cid}/response")
def get_campaign_response(cid: str):
    try:
        campaign_meta = store.campaigns.read_campaign(cid)["meta"]
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")
    cfg = store.read_config()
    return _response_body({}, campaign_meta, cfg, campaign_meta)


@router.put("/campaigns/{cid}/response")
def put_campaign_response(cid: str, body: ResponseSettings):
    fields = {k: v for k, v in _dump(body).items() if v is not None}
    try:
        _write_response(lambda f: store.campaigns.set_campaign_response(cid, f), fields)
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")
    return {"ok": True}


# ---- per-task routing (#142) ----
def _campaign_meta_or_404(cid: str) -> dict:
    try:
        return store.campaigns.read_campaign(cid)["meta"]
    except store.campaigns.CampaignNotFound as exc:
        raise HTTPException(status_code=404, detail="campaign not found") from exc


@router.get("/campaigns/{cid}/routing")
def get_campaign_routing(cid: str):
    return _routing_body("campaign", _campaign_meta_or_404(cid))


@router.put("/campaigns/{cid}/routing")
def put_campaign_routing(cid: str, body: RoutingUpdate):
    # The campaign first: a 400 about which routes this scope may set is an
    # answer about a campaign, and answering it for one that does not exist
    # tells the caller the wrong thing about their request.
    _campaign_meta_or_404(cid)
    fields = _routing_fields("campaign", body)
    try:
        store.campaigns.set_campaign_routing(cid, fields)
    except store.campaigns.CampaignNotFound as exc:
        # Between the check above and the write: a campaign deleted in another
        # tab. A 404 either way, rather than a 500 out of the store.
        raise HTTPException(status_code=404, detail="campaign not found") from exc
    return _routing_body("campaign", _campaign_meta_or_404(cid))


# ---- campaign sync ----
@router.get("/campaigns/{cid}/incoming")
def get_incoming(cid: str):
    try:
        store.campaigns.ensure_campaign_slim(cid)
        return store.sync.incoming(cid)
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")


@router.post("/campaigns/{cid}/incoming/accept")
def post_accept(cid: str, body: RefList):
    try:
        store.campaigns.ensure_campaign_slim(cid)
        store.sync.accept(cid, [_dump(r) for r in body.refs])
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")
    return {"ok": True}


@router.post("/campaigns/{cid}/incoming/reject")
def post_reject(cid: str, body: RefList):
    try:
        store.campaigns.ensure_campaign_slim(cid)
        store.sync.reject(cid, [_dump(r) for r in body.refs])
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")
    return {"ok": True}


def _record_name(cid: str, kind: str, eid: str) -> str | None:
    """Display name for a campaign record (materialized or still inherited
    from the world), or None if it no longer exists anywhere."""
    try:
        if kind == "characters":
            return store.overlay.read_character(cid, eid)["meta"].get("name", eid)
        if kind in store.entities.ENTITY_KINDS:
            return store.overlay.read_entity(cid, kind, eid)["meta"].get("name", eid)
    except (store.characters.CharacterNotFound, store.entities.EntityNotFound):
        return None
    return None


@router.get("/campaigns/{cid}/changes")
def get_changes(cid: str, limit: int | None = None, offset: int | None = None):
    """The rolling write-back deltas, one row per record, by kind then name.

    With `limit`/`offset` (either, or both) the body is a slice of that same
    listing; sending neither returns all of it, unchanged (#216).

    The slice is taken between naming the records and rendering their diffs,
    which is the only place it can be. The name is what the sort orders by, so
    every surviving row has to resolve before any row can be dropped -- but
    `line_diff` runs per FIELD, and a page that is not going to be sent has no
    reason to pay for it.
    """
    limit, offset = _page_window(limit, offset)
    _campaign_root_or_404(cid)
    data = store.changes.read(cid)
    scenes_by_id = {s["id"]: s for s in store.scenes.list_scenes(cid)}
    try:
        chron = store.chronicle.read_chronicle(cid)
    except Exception:  # noqa: BLE001 — garbled chronicle.json: labels degrade, no 500
        chron = {}
    named: list[tuple[str, str, str, dict]] = []
    for ref, entry in data.items():
        kind, _, eid = ref.partition("/")
        name = _record_name(cid, kind, eid)
        if name is None:
            continue  # record deleted since the change was captured
        named.append((kind, name, eid, entry))
    named.sort(key=lambda r: r[:2])   # kind, then name
    out: list[dict] = []
    for kind, name, eid, entry in _page_of(named, limit, offset):
        sid = entry.get("scene", "")
        s, c = scenes_by_id.get(sid, {}), chron.get(sid, {})
        fields = [{"field": f.get("field", ""), "label": f.get("label", ""),
                   "diff": store.changes.line_diff(f.get("before", ""), f.get("after", ""))}
                  for f in entry.get("fields", [])]
        out.append({"ref": {"kind": kind, "id": eid}, "name": name,
                    "scene": {"id": sid, "title": s.get("title", sid), "date": c.get("date", "")},
                    "fields": fields})
    return out


# ---- the append-only change journal (#31) ----------------------------------
#
# `changes` above is a rolling upsert: one entry per record, replaced by the next
# write-back. These two routes read the history it cannot keep, and reverse an
# entry in it.

#: How many entries a listing returns, newest first. A cap rather than paging:
#: the panel is a "what just happened, and can I take it back" view, and each
#: row carries a rendered diff, so returning everything the store retains would
#: be a much larger response than anyone reads. The store keeps more than this
#: (`journal.RETENTION` / `journal.MAX_BYTES`) and an older entry is still
#: undoable by id — it is simply not in the list.
#:
#: Still a cap after #216 gave the route above a `limit`/`offset`, and for the
#: reason that route did not have: this one was already bounded. What #216 paged
#: were the listings that returned EVERYTHING and grew with play. Reaching past
#: this cap is a different feature — the store retains by age and by bytes, so
#: what falls off the end is not something a bare offset can name.
JOURNAL_PAGE = 100


def _journal_row(cid: str, entry: dict, scenes_by_id: dict, chron: dict,
                 names: dict) -> dict:
    """One journal entry as the panel reads it: the record's current display
    name when it still resolves, the scene's label, and the server-side line
    diff `changes` already renders for the rolling view.

    Every field is coerced, because journal.json is hand-editable and read by a
    bare `json.loads` -- the same rule `plot._field` states. A non-string `label`
    handed to React blanks the whole panel, and a non-string before/after would
    raise out of `line_diff`'s `.splitlines()`.
    """
    ref = entry.get("ref")
    ref = ref if isinstance(ref, dict) else {}
    kind = ref.get("kind") if isinstance(ref.get("kind"), str) else ""
    eid = ref.get("id") if isinstance(ref.get("id"), str) else ""
    key = f"{kind}/{eid}"
    if key not in names:
        names[key] = _record_name(cid, kind, eid) if kind and eid else None
    sid = entry.get("scene") if isinstance(entry.get("scene"), str) else ""
    s, c = scenes_by_id.get(sid, {}), chron.get(sid, {})
    undone = entry.get("undone")
    plan = entry.get("undo")
    # The same predicate `undo.undo` refuses on, not merely "has an `undo` key":
    # journal.json is hand-editable, and a row whose plan lost its target would
    # otherwise render an enabled button that 400s.
    reversible = isinstance(plan, dict) and isinstance(plan.get("target"), dict)
    return {
        "id": entry.get("id", "") if isinstance(entry.get("id"), str) else "",
        "ts": entry.get("ts", "") if isinstance(entry.get("ts"), str) else "",
        "source": entry.get("source", "") if isinstance(entry.get("source"), str) else "",
        "kind": entry.get("kind", "") if isinstance(entry.get("kind"), str) else "",
        "ref": {"kind": kind, "id": eid},
        "name": names[key] or "",
        "label": entry.get("label", "") if isinstance(entry.get("label"), str) else "",
        "field": entry.get("field", "") if isinstance(entry.get("field"), str) else "",
        "scene": {"id": sid, "title": s.get("title", sid), "date": c.get("date", "")},
        "diff": store.changes.line_diff(
            entry.get("before", "") if isinstance(entry.get("before"), str) else "",
            entry.get("after", "") if isinstance(entry.get("after"), str) else ""),
        # `undoable` is the server's answer, never the client's inference: the
        # button must not offer what the store would refuse.
        "undoable": reversible and not undone,
        "why": entry.get("why", "") if isinstance(entry.get("why"), str) else "",
        "undone": undone if isinstance(undone, dict) else None,
    }


@router.get("/campaigns/{cid}/journal")
def get_journal(cid: str):
    _campaign_root_or_404(cid)
    scenes_by_id = {s["id"]: s for s in store.scenes.list_scenes(cid)}
    try:
        chron = store.chronicle.read_chronicle(cid)
    except Exception:  # noqa: BLE001 — garbled chronicle.json: labels degrade, no 500
        chron = {}
    names: dict = {}
    entries = store.journal.read(cid)[-JOURNAL_PAGE:]
    return [_journal_row(cid, e, scenes_by_id, chron, names) for e in reversed(entries)]


@router.post("/campaigns/{cid}/journal/{jid}/undo")
def post_journal_undo(cid: str, jid: str):
    """Put one journalled change back.

    409 rather than 200 when the record moved since: the reversal is a
    compare-and-swap (`store/undo.py`), and a reader undoing one edit has not
    asked to discard whatever landed on that record afterwards.
    """
    _campaign_root_or_404(cid)
    try:
        written = store.undo.undo(cid, jid)
    except store.undo.EntryNotFound:
        raise HTTPException(status_code=404, detail="that change is not in this "
                                                    "campaign's history")
    except store.undo.AlreadyUndone:
        raise HTTPException(status_code=409, detail="that change has already been undone")
    except store.undo.NotUndoable as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except store.undo.UndoConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    scenes_by_id = {s["id"]: s for s in store.scenes.list_scenes(cid)}
    try:
        chron = store.chronicle.read_chronicle(cid)
    except Exception:  # noqa: BLE001 — garbled chronicle.json: labels degrade, no 500
        chron = {}
    return {"ok": True, "entry": _journal_row(cid, written, scenes_by_id, chron, {})}


# The recent-facts tier of the ledger. Shorter than GET /chronicle's 50: that
# route feeds the per-scene recap, where the reader is looking for one entry,
# while this is a standing overview meant to be read top to bottom.
LEDGER_RECENT = 20

#: `relationships.set_feeling` writes 0–5; the ledger draws the number back.
FEELING_AXES = ("trust", "affection", "tension")


def _ledger_text(value, fallback: str = "") -> str:
    """A projected field as text. The ledger renders these directly, and React
    refuses an object as a child -- so a hand-edited record with a dict-valued
    `title` would blank the whole view rather than showing one odd row. The
    per-section tolerance below cannot catch that: the read SUCCEEDS and the
    failure happens in the browser. `commitments.open_commitments` normalizes
    its own rows; `plot.open_threads` does not, and hardening `plot` is one of
    the pre-existing items flagged on that PR, so the coercion sits here where
    the ledger owns the projection.

    Module level rather than nested in `get_ledger` because the relationships
    projection below needs the same rule, and two copies of it is two places
    for one of them to stop being applied. That argument holds one level up as
    well, which is why the body is now `store.fieldtext.text`: nine modules had
    written these three lines out, this one included.
    """
    return store.fieldtext.text(value, fallback)


def _ledger_relationships(cid: str) -> list[dict]:
    """Who stands where with whom, as flat rows the ledger can put in a table.

    relationships.json holds two shapes and both belong on this view. A
    *feeling* is directed and metered -- A's trust, affection and tension
    toward B, which B does not return by construction -- and a *bond* is
    symmetric, named ("kin", "sworn") and dated to the scene it formed in. One
    list with a `kind` rather than two sections: the reader's question is what
    stands between two people, and answering it in two tables makes them read
    both to find out.

    Names are resolved once per token and cached. `relationships.actor_name`
    reads a card per call, and a campaign with a dozen actors carries upwards of
    a hundred directed pairs, so the uncached version is that many file reads
    for a dozen answers. A token whose card will not parse falls back to its own
    id rather than emptying the section -- `casefile.build` makes the same trade
    for a cast member whose card is broken, and for the same reason: a name is
    the least of what this row says.

    The meters are clamped rather than trusted, like `casefile._pips`: the file
    is hand-editable, the client draws five pips, and a stored 9 would draw four
    that do not exist. A non-integer reads as 0 -- visibly nothing, rather than
    a crash.
    """
    data = store.relationships.read(cid)
    feelings = data.get("feelings") if isinstance(data.get("feelings"), dict) else {}
    bonds = data.get("bonds") if isinstance(data.get("bonds"), dict) else {}
    names: dict[str, str] = {}

    def _name(token: str) -> str:
        if token not in names:
            try:
                names[token] = store.relationships.actor_name(cid, token)
            except Exception:  # noqa: BLE001 — an unreadable card costs a name, not the section
                names[token] = token.partition(":")[2] or token
        return names[token]

    def _meter(value) -> int:
        return min(5, max(0, value)) if isinstance(value, int) and not isinstance(value, bool) else 0

    rows: list[dict] = []
    for key, f in feelings.items():
        if not isinstance(key, str) or not isinstance(f, dict):
            continue
        a, _, b = key.partition("->")
        rows.append({"id": key, "kind": "feeling", "a": a, "b": b,
                     "a_name": _name(a), "b_name": _name(b),
                     **{axis: _meter(f.get(axis)) for axis in FEELING_AXES},
                     "note": _ledger_text(f.get("note")), "type": "", "since_scene": ""})
    for key, b_rec in bonds.items():
        if not isinstance(key, str) or not isinstance(b_rec, dict):
            continue
        a, _, b = key.partition("|")
        rows.append({"id": key, "kind": "bond", "a": a, "b": b,
                     "a_name": _name(a), "b_name": _name(b),
                     **dict.fromkeys(FEELING_AXES, 0),
                     "note": "", "type": _ledger_text(b_rec.get("type")),
                     "since_scene": _ledger_text(b_rec.get("since_scene"))})
    # Bonds after feelings, each group by its own key, so the table has a stable
    # order across reads -- a row that moves between two identical-looking loads
    # is a row the reader stops trusting.
    rows.sort(key=lambda r: (r["kind"] == "bond", r["id"]))
    return rows


#: How many timeline rows a listing returns, newest first, after any pair
#: filter. A cap rather than paging, for `JOURNAL_PAGE`'s reason: this is a
#: "how did these two get here" view, and the store retains by age and by bytes
#: (`relationship_history.RETENTION` / `MAX_BYTES`), so what falls off its end
#: is not something a bare offset could name either. Higher than the journal's
#: because a row here carries two short standings rather than a rendered diff of
#: a record body, and because narrowing to one pair is the intended use.
RELATIONSHIP_HISTORY_PAGE = 200


@router.get("/campaigns/{cid}/relationships/history")
def get_relationship_history(cid: str, a: str | None = None, b: str | None = None):
    """How each standing on the ledger got there: one row per applied feeling or
    bond delta, newest first (#63).

    `_ledger_relationships` above answers where two people stand *now*; this
    answers what moved them, which `relationships.json` overwrites and cannot
    say. Pass `a` and `b` (actor tokens) for one pair — matched unordered, so a
    directed feeling is returned in both directions along with the bond, since
    "what has passed between these two" is one question. Both or neither: one
    alone is a 400 rather than a quiet fall back to everything.

    Every field is coerced and the names are resolved tolerantly, the two rules
    `_journal_row` and `_ledger_relationships` respectively state: the file is
    hand-editable and read by a bare `json.loads`, and a card that will not
    parse must cost a name rather than the view.
    """
    _campaign_root_or_404(cid)
    # A pair is both tokens or neither. One alone would silently answer with the
    # unfiltered timeline -- up to `RELATIONSHIP_HISTORY_PAGE` rows for pairs the
    # caller did not ask about, looking exactly like a filtered response.
    if bool(a) != bool(b):
        raise HTTPException(status_code=400,
                            detail="filtering by pair needs both actor tokens, "
                                   "`a` and `b` — half a pair names no pair")
    entries = (store.relationship_history.for_pair(cid, a, b)
               if a and b else store.relationship_history.read(cid))
    entries = entries[-RELATIONSHIP_HISTORY_PAGE:]
    try:
        scenes_by_id = {s["id"]: s for s in store.scenes.list_scenes(cid)}
    except Exception:  # noqa: BLE001 — one unreadable scene header: titles degrade, no 500
        # `list_scenes` opens every scene's header, so a single file a sync
        # client mangled raises out of it — and would take this whole timeline
        # with it, including the rows of every healthy scene, over a label.
        scenes_by_id = {}
    try:
        chron = store.chronicle.read_chronicle(cid)
    except Exception:  # noqa: BLE001 — garbled chronicle.json: labels degrade, no 500
        chron = {}
    # And a chronicle.json that PARSES but is not an object degrades the same
    # way. `read_chronicle` hands back whatever `json.loads` returned, so a
    # hand-edited list reaches the `.get` below and raises past the handler
    # above — a date label nobody would miss, turning a valid request into a
    # 500. Each record is re-checked at the row for the same reason.
    if not isinstance(chron, dict):
        chron = {}
    names: dict[str, str] = {}

    def _name(token: str) -> str:
        if token not in names:
            try:
                names[token] = store.relationships.actor_name(cid, token)
            except Exception:  # noqa: BLE001 — an unreadable card costs a name, not the row
                names[token] = token.partition(":")[2] or token
        return names[token]

    out: list[dict] = []
    for e in reversed(entries):
        atok, btok = _ledger_text(e.get("a")), _ledger_text(e.get("b"))
        sid = _ledger_text(e.get("scene"))
        # A row whose scene has been DELETED is not resolved against either
        # join: ids are recycled, so whatever holds this one now is a different
        # scene, and lending the row its title and date would be the plainest
        # possible lie about where a standing came from. The id itself is kept
        # and shown, which is what an unresolvable id has always rendered as.
        gone = bool(e.get("scene_gone"))
        sc = {} if gone else scenes_by_id.get(sid, {})
        c = None if gone else chron.get(sid)
        c = c if isinstance(c, dict) else {}
        out.append({
            "id": _ledger_text(e.get("id")), "ts": _ledger_text(e.get("ts")),
            "source": _ledger_text(e.get("source")),
            # Anything but the two known kinds renders as a feeling would read
            # wrong; an unknown kind is passed through so the row still says
            # what the file says rather than being silently relabelled.
            "kind": _ledger_text(e.get("kind")),
            "a": atok, "b": btok, "a_name": _name(atok), "b_name": _name(btok),
            "label": _ledger_text(e.get("label")),
            "before": _ledger_text(e.get("before")), "after": _ledger_text(e.get("after")),
            "scene": {"id": sid, "title": sc.get("title", sid), "date": c.get("date", "")},
        })
    return out


@router.get("/campaigns/{cid}/provenance")
def get_provenance(cid: str):
    """Why each continuity line is there: the quote, speaker and certainty
    behind every edit that landed, keyed `"<kind>/<id>#<field>"`.

    `absorb/parse.py` has always asked the extractor to cite itself and
    `absorb/routing.py` has always weighed those citations into a band — and
    both were thrown away the moment the edit applied, so the citation existed
    for exactly as long as the review row you judged it on. Keeping it costs
    disk and no tokens.

    One flat read of one file: unlike the ledger this joins nothing, so it needs
    neither the lock nor the per-section tolerance. `store.provenance.read` is
    itself tolerant of a garbled file — this backs display markers, and one bad
    byte must cost the markers rather than the page.

    A field with no entry is the normal case and always will be: the later
    absorb phases rest on no transcript citation, records written before this
    existed have none, and a hand-edited record has none either. The client
    renders those as uncited rather than hiding them.
    """
    _campaign_root_or_404(cid)
    data = store.provenance.read(cid)
    if not data:
        return data
    # The recording scene is labelled here rather than at write time, for the
    # reason `get_ledger` labels its own: a scene can be renamed, and a title
    # frozen into the citation would then name a scene that no longer exists.
    # Tolerant — an unreadable scene list costs the labels, not the markers.
    try:
        titles = {s["id"]: s["title"] for s in store.scenes.list_scenes(cid)}
    except Exception:  # noqa: BLE001 — labels degrade, the citations do not
        titles = {}
    return {k: {**v, "scene_title": titles.get(v.get("scene"), v.get("scene", ""))}
            if isinstance(v, dict) else v
            for k, v in data.items()}


@router.get("/campaigns/{cid}/ledger")
def get_ledger(cid: str):
    """The continuity ledger (#117): what the campaign still owes, in one read.

    Six sections, and one route rather than six, because they are read together
    and share one failure policy — a garbled plot.json, commitments.json,
    facts.json or relationships.json costs its own section and nothing else, the
    same tolerance `plot.render_open` and `get_changes` already apply. Splitting
    them would put that policy in six places and make the page reason about six
    loading states to render one view.

    Each thread, commitment and standing fact carries the scene it came from,
    resolved the same way `get_changes` resolves its labels: the title from the
    scene list, the in-fiction date from the chronicle. For a thread or a
    commitment that is the scene that last moved it; for a fact it is the scene
    that recorded it, since a fact's text never changes after that (#114) — a
    fact that stopped being true is retired rather than rewritten.

    `retired` is that other half, and it is the reason this route grew: a
    retired fact and its `superseded_by` pointer have been on disk since #114
    and never left the server, so the one thing the ledger keeps that a snapshot
    cannot — the chain saying WHEN a truth stopped being true and what replaced
    it — was unreadable from the client. Its rows carry two resolved scenes, the
    one that recorded the fact and the one that ended it, because a retired row
    is dated twice and both dates are the point of it.

    Contradictions are the section this view is named for and are absent until
    #111 gives them a record.

    Every plot and commitment row carries an `aging` block (#103): `overdue`
    once a parseable `due` is behind the campaign's clock, `stale` once nothing
    has moved the record for longer than this campaign's `stale_after_days`.
    Computed at read time and never stored — see `store.aging`, which argues
    that — so a corrected clock or an edited scene date changes the answer on
    the next read rather than leaving a stamp nothing recomputes.
    """
    _campaign_root_or_404(cid)

    def _tolerant(read):
        try:
            return read()
        except Exception:  # noqa: BLE001 — a garbled file empties its section, not the view
            return []

    # All seven sources are read under ONE campaign lock, because they are seven
    # files and a save writes them one after another: `put_chronicle` holds this
    # same lock while it records the chronicle and then applies the absorb's
    # plot, commitment, fact and relationship edits. Reading without it can catch
    # that sequence half done and return a new fact beside the still-open
    # commitment the very same save fulfilled — or, since #114, beside the
    # standing fact that same save retired -- and the page keeps that
    # contradictory snapshot until something else bumps its revision. A
    # continuity view that contradicts itself is worse than one that is a moment
    # stale, which is the whole reason the writer takes the lock across the
    # sequence rather than per file.
    #
    # The two halves of the fact ledger are read here for a sharper version of
    # the same reason: `facts.record` retires a fact and files its replacement in
    # ONE write, so an unlocked pair of reads is exactly where a supersession
    # chain shows up with both ends standing, or with neither.
    #
    # It is a read, so it holds the lock only for the reads: the projections
    # below work on data already in hand.
    with store.locks.campaign_lock(cid):
        scenes_by_id = {s["id"]: s for s in store.scenes.list_scenes(cid)}
        try:
            chron = store.chronicle.read_chronicle(cid)
        except Exception:  # noqa: BLE001 — garbled chronicle.json: labels degrade, no 500
            chron = {}
        open_threads = _tolerant(lambda: store.plot.open_threads(cid))
        owed = _tolerant(lambda: store.commitments.open_commitments(cid))
        standing = _tolerant(lambda: store.facts.active(cid))
        ended = _tolerant(lambda: store.facts.retired(cid))
        # The whole projection sits inside `_tolerant`, not just the read: it
        # resolves a name per actor off the cards, so a broken card empties this
        # section rather than 500ing the view around it.
        bonds = _tolerant(lambda: _ledger_relationships(cid))
        # Read under the lock with everything else, so the moment the rows are
        # aged against is the same present the rows were read in. It is a read
        # of one small file, and it does no calendar work -- which is why the
        # provider resolution that `aging.prepare` does waits until the lock is
        # released, the cut every other calendar caller in this app makes.
        clock_now = store.clock.now(cid)
    # Unparseable is not the only way that file can be wrong. `read_chronicle`
    # is a bare `json.loads`, so a chronicle.json holding `[]` -- valid JSON of
    # the wrong shape -- returns a list and raises nothing, and the `.get` below
    # would then 500 the whole view for any campaign with one open thread. The
    # shape is checked where it is used rather than trusted from the read.
    if not isinstance(chron, dict):
        chron = {}

    # The text coercion these projections run on now lives at module scope
    # (`_ledger_text`), because the relationships projection needs the same rule
    # and it has to be the same rule. Bound to the local name the projections
    # below already use.
    _txt = _ledger_text

    def _scene(sid) -> dict:
        # The row's OWN scene id is the third place a wrong shape can arrive,
        # and the one the two checks above do not reach: these projections run
        # outside `_tolerant`, so a commitment whose `last_scene` is `[]` reaches
        # `scenes_by_id.get` as an unhashable key and 500s the view. Coerced per
        # row rather than guarded per section, so one malformed record loses its
        # scene label instead of emptying the section around it.
        #
        # The label's own fields go through `_txt` for the reason the row fields
        # do: `LedgerPanel.sceneNote` interpolates them, so a non-string `date`
        # in a hand-edited chronicle reaches the panel and renders as
        # `[object Object]`. This nested projection feeds the plot and
        # commitment sections, which the recent-facts coercion never touched.
        if not isinstance(sid, str):
            sid = ""
        s = scenes_by_id.get(sid, {})
        c = chron.get(sid)
        c = c if isinstance(c, dict) else {}   # a per-scene entry can be wrong too
        return {"id": sid, "title": _txt(s.get("title"), sid), "date": _txt(c.get("date"))}

    threads = [{**t, "id": _txt(t.get("id")), "title": _txt(t.get("title"), _txt(t.get("id"))),
                "status": _txt(t.get("status"), "open"),
                "last_scene": _txt(t.get("last_scene")),
                "latest_beat": _txt(t.get("latest_beat"))}
               for t in open_threads if isinstance(t, dict)]
    # One context for both lists (#103), so a thread and a commitment last moved
    # by the same scene cannot report different ages. `_tolerant` around it for
    # the reason every other read here has one: the aging pass resolves a
    # calendar provider, and a campaign whose provider is a broken plugin should
    # lose its badges, not its ledger.
    try:
        ctx = store.aging.prepare(cid, clock_now)
    except Exception:  # noqa: BLE001 — a broken calendar plugin costs the badges
        ctx = None     # (`_tolerant` is for the section reads; this empties no section)
    if ctx is not None:
        threads = store.aging.annotate(ctx, threads)
        owed = store.aging.annotate(ctx, owed)
    # Derived from `chron` rather than `chronicle.recent`, which sorts on the raw
    # `id` of every record: one list-valued id makes that comparison raise and
    # `_tolerant` empties the entire recent-facts section, losing every good fact
    # to one bad row. Sorting on a coerced key keeps the rest. It also saves a
    # second read of the same file.
    recent = sorted((r for r in chron.values() if isinstance(r, dict)),
                    key=lambda r: _txt(r.get("id")))[-LEDGER_RECENT:]
    return {
        # `stale_after` beside the rows rather than inside each one: it is a
        # property of the campaign, and a panel that says "40 days untouched"
        # needs to be able to say what this campaign calls stale.
        "stale_after_days": ctx["stale_after"] if ctx is not None
        else store.calendars.STALE_AFTER_DAYS,
        "plot": [{**t, "scene": _scene(t["last_scene"])} for t in threads],
        "commitments": [{**c, "scene": _scene(c["last_scene"])} for c in owed],
        # The fact ledger (#114). `facts.active` normalizes its own rows, like
        # `open_commitments` does, so only the scene label is resolved here.
        "facts": [{**f, "scene": _scene(f["scene"])} for f in standing],
        # The other half of the same file: the facts that stopped being true.
        # BOTH scenes are resolved and neither is redundant -- `scene` is still
        # the one that RECORDED the fact, so a retired row keeps its dated place
        # in the ledger, and `retired_scene` is the one that ended it, which is
        # the only thing on the row saying when it stopped. `superseded_by` stays
        # a bare fact id: it points INTO this same response, and inlining the
        # replacement's text here would ship one sentence twice and let the two
        # copies disagree the moment either is hand-edited.
        "retired": [{**f, "scene": _scene(f["scene"]),
                     "retired_scene": _scene(f["retired_scene"])} for f in ended],
        # `since_scene` is a bond's only date and is blank for every feeling,
        # which resolves to the empty label -- the same row shape the sections
        # above carry, so one renderer serves all of them.
        "relationships": [{**r, "scene": _scene(r["since_scene"])} for r in bonds],
        # Newest first: `chronicle.recent` returns the tail in ascending order,
        # which is right for a recap read forward and backwards for a ledger.
        # `one_line or summary`, the fallback every other chronicle consumer
        # uses (`context/story.py:_story_entries`): a save may leave `one_line`
        # empty, and a row with only its scene metadata is a blank line in the
        # panel rather than a fact.
        # `_scene` carries the same id coercion for the two sections above; a
        # chronicle record's `id` needs it for the same reason, and its text
        # needs `_txt` for the same reason the plot rows above do.
        "chronicle": [{**_scene(r.get("id")),
                       "one_line": _txt(r.get("one_line")) or _txt(r.get("summary")),
                       "date": _txt(r.get("date"))}
                      for r in reversed(recent)],
    }


@router.get("/campaigns/{cid}/timeline")
def get_timeline(cid: str):
    """The play timeline (#198): every scene in play order, with the plot beats
    that landed in it and the threads to filter them by.

    Thin on purpose — the join, the tolerance and the lock all live in
    ``store.timeline``, whose docstring carries the argument for each. Same
    split as ``get_scene_briefing``, and for the same reason: this is a read
    that fans across three files with one failure policy, which is a store
    question rather than an HTTP one.

    The ledger's own ``timeline`` section is a different view of a subset — the
    last few chronicle one-liners as table rows — and stays where it is. This
    route reaches the scenes that were never absorbed, which is most of the ones
    a campaign in progress has, and the per-scene beats nothing else reads back.
    """
    _campaign_root_or_404(cid)
    return store.timeline.build(cid)


# ---- user pins & excludes (#129) ----
#
# Campaign-scoped, with the scene as a query parameter rather than a path
# segment, because one rule set holds both scopes: a scene-scoped rule and the
# campaign-wide default it overrides are read, listed and removed together, and
# hanging the scene ones off /scenes/{sid} would split that in two.
def _pin_name(cid: str, kind: str, eid: str, pc_names) -> str | None:
    """Display name for a pinned target, or None when the campaign no longer has
    it. Rules outlive what they name -- a pinned character can be deleted -- and
    a dangling rule is inert rather than an error (see store/pins.py), so this
    reports the absence instead of hiding the row.

    `pc_names` is the campaign's PC list, resolved once by the caller and only
    when a row needs it: PCs are read as a LIST rather than one at a time
    because a PC the campaign has not materialized is still inherited from the
    world and still castable, and reading the campaign copy of one would report
    it as deleted. `_record_name`'s `read_character` resolves the same way for
    the other actor kind, one id at a time, so only this half needs hoisting.
    """
    if kind == "pcs":
        return pc_names().get(eid)
    return _record_name(cid, kind, eid)


def _pin_row(cid: str, rule: dict, pc_names) -> dict:
    """One stored rule, plus what a panel needs to draw it. The ONE projection:
    a POST answers with the same shape its GET will hand back, so the client can
    render what it just wrote without a second read disagreeing with it."""
    kind, _sep, eid = rule["ref"].partition(":")
    name = _pin_name(cid, kind, eid, pc_names)
    return {**rule, "kind": kind, "id": eid, "name": name or eid, "missing": name is None}


def _pc_name_reader(cid: str):
    """A memoized `{pid: name}` reader. Memoized rather than eager because most
    rule sets name no PC at all, and the list costs a directory walk over the
    campaign's PCs and the world's."""
    cache: dict[str, str] = {}
    loaded = False

    def pc_names() -> dict[str, str]:
        nonlocal loaded
        if not loaded:
            cache.update({p["id"]: p["name"] for p in store.overlay.list_pcs(cid)})
            loaded = True
        return cache

    return pc_names


def _pin_rows(cid: str, sid: str, posts: int) -> list[dict]:
    pc_names = _pc_name_reader(cid)
    return [_pin_row(cid, r, pc_names) for r in store.pins.records(cid, sid, posts)]


def _pin_posts(cid: str, sid: str) -> int:
    """The transcript length a TTL counts against, or 0 with no scene named.

    404s on a scene this campaign does not have: a rule filed against a
    mistyped id would never apply to anything and never be visible anywhere.
    """
    if not sid:
        return 0
    try:
        return len(store.scenes.read_scene(cid, sid)["messages"])
    except store.SceneNotFound:
        raise HTTPException(status_code=404, detail="scene not found")


@router.get("/campaigns/{cid}/pins")
def get_pins(cid: str, sid: str = ""):
    """Every rule in force for `sid` — that scene's, then the campaign's own.

    Spent rules are already gone from this list (`pins.records`), so `remaining`
    is always a live countdown or None for a standing rule.
    """
    _campaign_root_or_404(cid)
    return {"pins": _pin_rows(cid, sid, _pin_posts(cid, sid))}


@router.post("/campaigns/{cid}/pins")
def post_pin(cid: str, body: PinRule):
    """Pin or exclude one target, replacing whatever rule this scope held for it.

    The post count the TTL is measured from is read HERE rather than sent by the
    client: it is the length of the transcript this rule is about, and a client
    that guessed it wrong would set a window that expires at the wrong turn.
    """
    _campaign_root_or_404(cid)
    # Only a scene rule is measured against a scene, so only a scene rule is
    # refused for naming one that is gone. A client that keeps its scene id in
    # the same field for both scopes (ours does) would otherwise be unable to
    # file a CAMPAIGN rule from a scene that has just been renamed underneath
    # it -- over an id the campaign rule does not use and does not store.
    posts = _pin_posts(cid, body.sid) if body.scope == "scene" else 0
    try:
        rec = store.pins.set_rule(cid, body.ref, body.mode, scope=body.scope, sid=body.sid,
                                  ttl_posts=body.ttl_posts, posts=posts)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "pin": _pin_row(cid, rec, _pc_name_reader(cid))}


@router.delete("/campaigns/{cid}/pins")
def delete_pin(cid: str, ref: str, scope: str = "scene", sid: str = ""):
    """Lift one rule. 404 when this scope had none for that ref — removing a
    rule that is not there is a client working from a stale list, and reporting
    it as done would leave the panel showing a row nothing will clear."""
    _campaign_root_or_404(cid)
    try:
        removed = store.pins.remove(cid, ref, scope=scope, sid=sid)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not removed:
        raise HTTPException(status_code=404, detail="pin not found")
    return {"ok": True}


# ---- campaign cast & suggestions ----
@router.get("/campaigns/{cid}/appearances")
def get_appearances(cid: str):
    _campaign_root_or_404(cid)
    return store.appearances.roster(cid)


@router.get("/campaigns/{cid}/pcs")
def get_campaign_pcs(cid: str):
    _campaign_root_or_404(cid)
    return store.overlay.list_pcs(cid)


@router.post("/campaigns/{cid}/pcs")
def post_campaign_pc(cid: str, body: PCCreate):
    # Campaign-local PC overlay: tags are free strings (no world-vocabulary check).
    _campaign_root_or_404(cid)
    pid, vid = store.overlay.create_pc(cid, body.name, body.tags, body.version_name, body.persona)
    return {"pc": pid, "version": vid}


def _campaign_char_version_or_404(cid: str, char: str, vid: str):
    """The campaign root, once `char`/`vid` are known to name a real character
    version.

    Resolved through `overlay.char_root`, not the campaign root: on a thin
    campaign the character's card files are still the world's, so a croot-only
    check would 404 every inherited character -- which is all of them. The root
    this *returns* is always the campaign's, because that is where a write has
    to land. A character the campaign has tombstoned resolves to the campaign
    root, where there is no `character.md`, so it is refused for the same
    reason a typo is; so is a version `pick_version` purged.

    Why gate at all -- and why the reads on this surface are left ungated
    here too -- see `common._world_char_version_or_404` (#360).
    """
    root = _campaign_root_or_404(cid)
    try:
        store.characters.require_version(store.overlay.char_root(cid, char), char, vid)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    except store.characters.VersionNotFound:
        raise HTTPException(status_code=404, detail="version not found")
    return root


@router.get("/campaigns/{cid}/characters/{char}/versions/{vid}/images/{name}")
def get_campaign_image(cid: str, char: str, vid: str, name: str, request: Request):
    _campaign_root_or_404(cid)
    return _serve_image(store.overlay.image_root(cid, char, vid, name), char, vid, name, request=request)


@router.put("/campaigns/{cid}/characters/{char}/versions/{vid}/images/{name}")
async def put_campaign_image(cid: str, char: str, vid: str, name: str, file: UploadFile = File(...)):
    root = _campaign_char_version_or_404(cid, char, vid)
    data = await file.read()
    ext = _upload_image_ext(data)  # the bytes name the type, not `file.filename` (#321)
    try:
        stored = store.assets.put_image(root, char, vid, name, data, ext)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"name": name, "ext": stored}


@router.delete("/campaigns/{cid}/characters/{char}/versions/{vid}/images/{name}")
def delete_campaign_image(cid: str, char: str, vid: str, name: str):
    _campaign_char_version_or_404(cid, char, vid)
    # tombstone so a still-materialized world image doesn't show back through
    # the overlaid read the moment the campaign's own copy is gone (get_campaign_image).
    store.overlay.delete_image(cid, char, vid, name)
    return {"ok": True}


@router.post("/campaigns/{cid}/characters/{char}/versions/{vid}/images/{name}/promote")
def promote_campaign_image(cid: str, char: str, vid: str, name: str):
    _campaign_char_version_or_404(cid, char, vid)
    try:
        store.overlay.promote_image(cid, char, vid, name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="image not found")
    except ValueError as exc:
        # an externally-placed file whose extension we never accepted for upload
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True}


@router.put("/campaigns/{cid}/characters/{char}/versions/{vid}/images/avatar/focus")
def put_campaign_avatar_focus(cid: str, char: str, vid: str, body: AvatarFocus):
    root = _campaign_char_version_or_404(cid, char, vid)
    # a thin campaign may only have this avatar through the inherited world
    # character, so the existence gate must check the overlay union, not croot alone
    names = {i["name"] for i in store.overlay.list_images(cid, char, vid)}
    if store.assets.AVATAR not in names:
        raise HTTPException(status_code=404, detail="image not found")
    # the write always lands campaign-side; overlay.read_focus then finds this
    # campaign focus.json and treats the campaign as authoritative going forward
    store.assets.write_focus(root, char, vid, body.focus)
    return {"ok": True}


@router.put("/campaigns/{cid}/characters/{char}/versions/{vid}/images/{name}/description")
def put_campaign_image_description(cid: str, char: str, vid: str, name: str,
                                   body: ImageDescription):
    _campaign_char_version_or_404(cid, char, vid)
    try:
        store.overlay.set_description(cid, char, vid, name, body.description)
    except ValueError:
        # `from None`: the strict-write ValueError is this module's own
        # implementation detail, and chaining it onto the 404 says nothing a
        # caller can act on.
        raise HTTPException(status_code=404, detail="image not found") from None
    return {"ok": True}


@router.post("/campaigns/{cid}/characters/{char}/versions/{vid}/images/copy-from-greeting")
def post_copy_campaign_image_from_greeting(cid: str, char: str, vid: str, body: CopyFromGreeting):
    root = _campaign_char_version_or_404(cid, char, vid)
    # the source greeting image may still be inherited (unmaterialized) in a thin
    # campaign; resolve its root through the overlay so the copy still finds it,
    # while the destination character write always targets the campaign root
    src_root = store.overlay.image_root(cid, body.gid, "default", body.name, base="greetings")
    # the free gallery_N slot must account for inherited world gallery images too,
    # or a campaign-side copy can reuse a name that shadows one of them
    taken_names = {i["name"] for i in store.overlay.list_images(cid, char, vid)}
    try:
        # overlay-ok: the two things this reads are handed in already resolved --
        # src_root from overlay.image_root and taken_names from the overlay union
        # above; `root` is only the destination the copy writes to
        stored = store.image_subjects.copy_to_character(root, body.gid, body.name, char, vid, body.slot,
                                                         src_root=src_root, taken_names=taken_names)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="source image not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    # overlay-ok: reads back the file copy_to_character just wrote campaign-side,
    # only to report its extension — the overlay union would answer a different
    # question (which image wins), not "what did this call just store"
    p = store.assets.image_path(root, char, vid, stored)
    return {"name": stored, "ext": p.suffix.lstrip(".").lower() if p else ""}


def _campaign_wroot(cid: str):
    return store.campaigns.world_root_of(cid)


@router.get("/campaigns/{cid}/characters")
def get_campaign_characters(cid: str):
    _campaign_root_or_404(cid)
    return store.overlay.list_characters(cid)


@router.post("/campaigns/{cid}/characters")
def post_campaign_character(cid: str, body: CharacterCreate):
    """A character who exists only in this campaign — an NPC who walked on
    mid-scene and was never in the library (#60).

    The world side has had this route all along; the campaign side had every
    piece except the door. `overlay.create_character` writes no `sync.md` ref
    and the world has no counterpart, and that *absence* is the whole "emergent"
    signal: nothing in `sync.incoming` enumerates the record, so it can never
    surface as a phantom world change. `POST .../{kind}/{eid}/promote` is what
    ends that state.
    """
    _campaign_root_or_404(cid)
    aid, vid = store.overlay.create_character(cid, body.name, body.version_name, body.card)
    return {"character": aid, "version": vid}


@router.get("/campaigns/{cid}/diverged")
def get_diverged(cid: str):
    """Every campaign copy that no longer matches the library record it came
    from — the inverse of `/incoming`, and what `push` can act on (#53)."""
    # `_campaign_root_or_404` has already answered for a campaign that is not
    # there; past it, `diverged` reads sync.md, which is absent-means-empty.
    _campaign_root_or_404(cid)
    return store.sync.diverged(cid)


@router.get("/campaigns/{cid}/characters/{char}")
def get_campaign_character(cid: str, char: str):
    _campaign_root_or_404(cid)
    try:
        return store.overlay.read_character(cid, char)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")


@router.get("/campaigns/{cid}/characters/{char}/voice-anchor")
def get_campaign_voice_anchor(cid: str, char: str):
    """The anchor as this campaign sees it — its own copy if it has one, else
    the world's (`overlay.voice_anchor_record`).

    A campaign-local character has no world counterpart at all: an NPC accepted
    from an absorb `new_character` proposal exists only here. Without these
    routes such a character could never be given an anchor, so absorb would skip
    its voice check forever -- the one class of character the feature most
    obviously wants to cover (#59/#61)."""
    _campaign_root_or_404(cid)
    try:
        store.characters.read_character(store.overlay.char_root(cid, char), char)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    return {"voice_anchor": store.overlay.voice_anchor_record(cid, char)["text"]}


@router.put("/campaigns/{cid}/characters/{char}/voice-anchor")
def put_campaign_voice_anchor(cid: str, char: str, body: VoiceAnchorSave):
    """Write the anchor campaign-side, which is how the per-file overlay records
    a divergence — the same shape as any other campaign edit to an inherited
    record. A blank body opts the character out of voice checks in this campaign
    WITHOUT touching the world's anchor: see `overlay.set_voice_anchor` for why
    that cannot simply delete the local copy."""
    _campaign_root_or_404(cid)
    try:
        store.characters.read_character(store.overlay.char_root(cid, char), char)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    store.overlay.set_voice_anchor(cid, char, body.voice_anchor)
    return {"ok": True}


@router.post("/campaigns/{cid}/characters/{char}/voice-anchor/generate", status_code=202)
@computes_only
def post_campaign_voice_anchor_generate(
        cid: str, char: str, request: Request,
        client: LLMClient = Depends(get_llm),
        x_grimoire_attempt: str | None = Header(default=None)):
    """Start a drafted anchor from the character's card, resolved through the
    overlay so a campaign-local character (which has no world copy) can use it
    too. Preview only — the caller persists with PUT."""
    _campaign_root_or_404(cid)
    conn = _require_connection("voice-anchor", cid)
    root = store.overlay.char_root(cid, char)
    try:
        ch = store.characters.read_character(root, char)
        card = store.characters.read_card(root, char, ch["meta"]["default_version"])
    except (store.characters.CharacterNotFound, store.characters.VersionNotFound):
        raise HTTPException(status_code=404, detail="character not found")
    # See the world-side route: `{}` and `{"data": ["speech"]}` are both
    # supported card state, and the template renders "(none)" per field.
    data = card.get("data")
    messages = store.voice_anchors.build_prompt(data if isinstance(data, dict) else {})

    async def work():
        return await draft_completion(
            client, conn, messages, "voice-anchor",
            lambda text: {"voice_anchor": store.voice_anchors.parse_output(text)},
            cid=cid)

    return runs.run_draft(request.app, runs.campaign_subject(cid), "voice-anchor",
                          x_grimoire_attempt, work)


@router.put("/campaigns/{cid}/characters/{char}")
def put_campaign_character(cid: str, char: str, body: DefaultVersion):
    _campaign_root_or_404(cid)
    try:
        root = store.overlay.ensure_actor_writable(cid, "characters", char)
        store.characters.set_default_version(root, char, body.default_version)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    except store.characters.VersionNotFound:
        raise HTTPException(status_code=404, detail="version not found")
    return {"ok": True}


@router.delete("/campaigns/{cid}/characters/{char}")
def delete_campaign_character(cid: str, char: str):
    """Remove a character from this campaign (#60).

    The counterpart to the create route above, and it had been missing: the
    world side has had a delete all along, version-delete refuses the last
    version, and so an NPC invented by mistake could not be removed at all
    (Codex review). `overlay.delete_actor` decides what the delete MEANS here
    — tombstone an inherited actor, drop a campaign copy, or remove an
    emergent one outright.
    """
    _campaign_root_or_404(cid)
    try:
        store.overlay.delete_actor(cid, "characters", char)
    except store.characters.CharacterNotFound as exc:
        raise HTTPException(status_code=404, detail="character not found") from exc
    return {"ok": True}


@router.delete("/campaigns/{cid}/pcs/{pid}")
def delete_campaign_pc(cid: str, pid: str):
    """The same, for a campaign-scoped PC — `POST /campaigns/{cid}/pcs` has
    existed longer than the character one and had no delete either."""
    _campaign_root_or_404(cid)
    try:
        store.overlay.delete_actor(cid, "pcs", pid)
    except store.pcs.PCNotFound as exc:
        raise HTTPException(status_code=404, detail="pc not found") from exc
    return {"ok": True}


@router.put("/campaigns/{cid}/characters/{char}/name")
def put_campaign_character_name(cid: str, char: str, body: NameBody):
    """This campaign's own name for the character (#13). Materializes the actor
    copy-on-write like every other campaign-side character write, so the world's
    name is left alone."""
    _campaign_root_or_404(cid)
    name = _display_name_or_400(body.name)
    try:
        root = store.overlay.ensure_actor_writable(cid, "characters", char)
        store.characters.set_name(root, char, name)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    return {"ok": True}


@router.post("/campaigns/{cid}/characters/{char}/versions")
def post_campaign_character_version(cid: str, char: str, body: VersionCreate):
    _campaign_root_or_404(cid)
    if store.appearances.locked_version(cid, "characters", char) is not None:
        raise HTTPException(status_code=409, detail="character is locked to one version")
    try:
        root = store.overlay.ensure_actor_writable(cid, "characters", char)
        vid = store.characters.create_version(root, char, body.name, body.card)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    return {"version": vid}


@router.put("/campaigns/{cid}/characters/{char}/versions/{vid}")
def put_campaign_character_version(cid: str, char: str, vid: str, body: VersionUpdate):
    _campaign_root_or_404(cid)
    try:
        root = store.overlay.ensure_actor_writable(cid, "characters", char)
        store.characters.update_version(root, char, vid, body.card)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    except store.characters.VersionNotFound:
        raise HTTPException(status_code=404, detail="version not found")
    return {"ok": True}


@router.delete("/campaigns/{cid}/characters/{char}/versions/{vid}")
def delete_campaign_character_version(cid: str, char: str, vid: str):
    _campaign_root_or_404(cid)
    try:
        root = store.overlay.ensure_actor_writable(cid, "characters", char)
        store.characters.delete_version(root, char, vid)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    except store.characters.VersionNotFound:
        raise HTTPException(status_code=404, detail="version not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True}


@router.get("/campaigns/{cid}/pcs/{pid}")
def get_campaign_pc(cid: str, pid: str):
    _campaign_root_or_404(cid)
    try:
        # overlay.read_pc, not pcs.read_pc under pc_root: the persona files
        # resolve whole-directory, but images overlay per file, so a
        # materialized PC can still be wearing an avatar only the world has (#219)
        return store.overlay.read_pc(cid, pid)
    except store.pcs.PCNotFound:
        raise HTTPException(status_code=404, detail="pc not found")


@router.put("/campaigns/{cid}/pcs/{pid}")
def put_campaign_pc(cid: str, pid: str, body: PCUpdate):
    # Campaign tags are free strings: no world-vocabulary check on this side.
    _campaign_root_or_404(cid)
    try:
        root = store.overlay.ensure_actor_writable(cid, "pcs", pid)
        if body.tags is not None:
            store.pcs.set_tags(root, pid, body.tags)
        if body.default_version is not None:
            store.pcs.set_default_version(root, pid, body.default_version)
    except store.pcs.PCNotFound:
        raise HTTPException(status_code=404, detail="pc not found")
    except store.pcs.PCVersionNotFound:
        raise HTTPException(status_code=404, detail="version not found")
    return {"ok": True}


@router.post("/campaigns/{cid}/pcs/{pid}/versions")
def post_campaign_pc_version(cid: str, pid: str, body: PersonaVersionCreate):
    _campaign_root_or_404(cid)
    if store.appearances.locked_version(cid, "pcs", pid) is not None:
        raise HTTPException(status_code=409, detail="pc is locked to one version")
    try:
        root = store.overlay.ensure_actor_writable(cid, "pcs", pid)
        vid = store.pcs.create_version(root, pid, body.name, body.persona)
    except store.pcs.PCNotFound:
        raise HTTPException(status_code=404, detail="pc not found")
    return {"version": vid}


@router.put("/campaigns/{cid}/pcs/{pid}/versions/{vid}")
def put_campaign_pc_version(cid: str, pid: str, vid: str, body: PersonaVersionUpdate):
    _campaign_root_or_404(cid)
    try:
        root = store.overlay.ensure_actor_writable(cid, "pcs", pid)
        store.pcs.update_version(root, pid, vid, body.persona)
    except store.pcs.PCNotFound:
        raise HTTPException(status_code=404, detail="pc not found")
    except store.pcs.PCVersionNotFound:
        raise HTTPException(status_code=404, detail="version not found")
    return {"ok": True}


@router.delete("/campaigns/{cid}/pcs/{pid}/versions/{vid}")
def delete_campaign_pc_version(cid: str, pid: str, vid: str):
    _campaign_root_or_404(cid)
    try:
        root = store.overlay.ensure_actor_writable(cid, "pcs", pid)
        store.pcs.delete_version(root, pid, vid)
    except store.pcs.PCNotFound:
        raise HTTPException(status_code=404, detail="pc not found")
    except store.pcs.PCVersionNotFound:
        raise HTTPException(status_code=404, detail="version not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True}


# ---- campaign PC images (#219) — the campaign-side half of the world routes
# in `worlds.py`, resolved through the overlay exactly as the character image
# routes above are: reads take the union, deletes tombstone, promotion copies
# up first.
def _campaign_pc_version_or_404(cid: str, pid: str, vid: str):
    """The campaign root, once `pid`/`vid` are known to name a real PC version.

    Resolved through `overlay.pc_root`, not the campaign root: on a thin
    campaign the PC's persona files are still the world's, so a croot-only
    check would 404 every inherited PC. The root this *returns* is always the
    campaign's, because that is where a write has to land.

    Why gate at all: see `worlds._world_pc_version_or_404`. `put_image` creates
    the directory it writes into, so an unchecked id turns a typo into a
    permanent, unlisted folder of orphaned bytes.
    """
    root = _campaign_root_or_404(cid)
    try:
        store.pcs.require_version(store.overlay.pc_root(cid, pid), pid, vid)
    except store.pcs.PCNotFound:
        raise HTTPException(status_code=404, detail="pc not found")
    except store.pcs.PCVersionNotFound:
        raise HTTPException(status_code=404, detail="version not found")
    return root


@router.put("/campaigns/{cid}/pcs/{pid}/versions/{vid}/images/{name}/description")
def put_campaign_pc_image_description(cid: str, pid: str, vid: str, name: str,
                                      body: ImageDescription):
    _campaign_pc_version_or_404(cid, pid, vid)
    try:
        store.overlay.set_description(cid, pid, vid, name, body.description,
                                      base=store.pcs.ASSET_BASE)
    except ValueError:
        # `from None`: the strict-write ValueError is this module's own
        # implementation detail, and chaining it onto the 404 says nothing a
        # caller can act on.
        raise HTTPException(status_code=404, detail="image not found") from None
    return {"ok": True}


@router.get("/campaigns/{cid}/pcs/{pid}/versions/{vid}/images")
def list_campaign_pc_images(cid: str, pid: str, vid: str):
    _campaign_pc_version_or_404(cid, pid, vid)
    return store.overlay.list_images(cid, pid, vid, base=store.pcs.ASSET_BASE)


@router.get("/campaigns/{cid}/pcs/{pid}/versions/{vid}/images/{name}")
def get_campaign_pc_image(cid: str, pid: str, vid: str, name: str, request: Request):
    _campaign_pc_version_or_404(cid, pid, vid)
    return _serve_image(
        store.overlay.image_root(cid, pid, vid, name, base=store.pcs.ASSET_BASE),
        pid, vid, name, base=store.pcs.ASSET_BASE, request=request)


@router.put("/campaigns/{cid}/pcs/{pid}/versions/{vid}/images/{name}")
async def put_campaign_pc_image(cid: str, pid: str, vid: str, name: str,
                                file: UploadFile = File(...)):
    root = _campaign_pc_version_or_404(cid, pid, vid)
    data = await file.read()
    ext = _upload_image_ext(data)  # the bytes name the type, not `file.filename` (#321)
    try:
        stored = store.assets.put_image(root, pid, vid, name, data, ext,
                                        base=store.pcs.ASSET_BASE)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"name": name, "ext": stored}


@router.delete("/campaigns/{cid}/pcs/{pid}/versions/{vid}/images/{name}")
def delete_campaign_pc_image(cid: str, pid: str, vid: str, name: str):
    _campaign_pc_version_or_404(cid, pid, vid)
    # tombstone so a still-materialized world image doesn't show back through
    # the overlaid read the moment the campaign's own copy is gone
    store.overlay.delete_image(cid, pid, vid, name, base=store.pcs.ASSET_BASE)
    return {"ok": True}


@router.post("/campaigns/{cid}/pcs/{pid}/versions/{vid}/images/{name}/promote")
def promote_campaign_pc_image(cid: str, pid: str, vid: str, name: str):
    _campaign_pc_version_or_404(cid, pid, vid)
    try:
        store.overlay.promote_image(cid, pid, vid, name, base=store.pcs.ASSET_BASE)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="image not found")
    except ValueError as exc:
        # an externally-placed file whose extension we never accepted for upload
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True}


@router.put("/campaigns/{cid}/pcs/{pid}/versions/{vid}/images/avatar/focus")
def put_campaign_pc_avatar_focus(cid: str, pid: str, vid: str, body: AvatarFocus):
    root = _campaign_pc_version_or_404(cid, pid, vid)
    # a thin campaign may only have this avatar through the inherited world PC,
    # so the existence gate checks the overlay union, not croot alone
    names = {i["name"] for i in store.overlay.list_images(cid, pid, vid,
                                                          base=store.pcs.ASSET_BASE)}
    if store.assets.AVATAR not in names:
        raise HTTPException(status_code=404, detail="image not found")
    # the write always lands campaign-side; overlay.read_focus then finds this
    # campaign focus.json and treats the campaign as authoritative going forward
    store.assets.write_focus(root, pid, vid, body.focus, base=store.pcs.ASSET_BASE)
    return {"ok": True}


@router.post("/campaigns/{cid}/{kind}/{aid}/pick-version")
def post_pick_version(cid: str, kind: str, aid: str, body: PickBody):
    _campaign_root_or_404(cid)
    if kind not in store.appearances.ACTOR_KINDS:
        raise HTTPException(status_code=404, detail="unknown actor kind")
    if store.appearances.locked_version(cid, kind, aid) is not None:
        # checked before existence: the sibling versions were purged by the pick
        raise HTTPException(status_code=409, detail=f"{kind}/{aid} is already locked")
    if store.appearances.actor_hash(store.overlay.actor_root(cid, kind, aid), kind, aid, body.version) is None:
        raise HTTPException(status_code=404, detail="actor or version not found in campaign")
    try:
        store.appearances.pick_version(cid, kind, aid, body.version)
    except store.appearances.AppearError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"ok": True}


@router.post("/campaigns/{cid}/{kind}/{aid}/import-version")
def post_import_version(cid: str, kind: str, aid: str, body: PickBody):
    _campaign_root_or_404(cid)
    if kind not in store.appearances.ACTOR_KINDS:
        raise HTTPException(status_code=404, detail="unknown actor kind")
    if store.appearances.actor_hash(_campaign_wroot(cid), kind, aid, body.version) is None:
        raise HTTPException(status_code=404, detail="actor or version not found in world")
    try:
        store.appearances.import_version(cid, kind, aid, body.version)
    except store.appearances.AppearError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"ok": True}


@router.post("/campaigns/{cid}/{kind}/instantiate/{mid}/{content_id}")
def post_campaign_instantiate(cid: str, kind: str, mid: str, content_id: str):
    _campaign_root_or_404(cid)
    try:
        content = store.modules.read_content(mid, kind, content_id)
    except store.modules.ModuleNotFound:
        raise HTTPException(status_code=404, detail="module not found")
    except store.modules.ContentNotFound:
        raise HTTPException(status_code=404, detail="content not found")
    try:
        eid = store.overlay.create_entity(cid, kind, content["name"], content["body"],
                                          content.get("keys", ""), "",
                                          fields=_content_fields(kind, content))
    except store.entities.UnknownKind:
        raise HTTPException(status_code=404, detail="unknown kind")
    if content.get("sheet_type"):
        try:
            store.sheets.write(cid, kind, eid, content["sheet_type"], content.get("fields"),
                              expected=None)
        except (store.modules.ModuleNotFound, store.sheets.SheetError) as e:
            # The entity was just created campaign-side via overlay.create_entity
            # with no world counterpart, so overlay.delete_entity removes the
            # campaign file cleanly (no tombstone) -- roll it back so a failed
            # instantiate leaves no sheetless orphan.
            store.overlay.delete_entity(cid, kind, eid)
            raise HTTPException(status_code=400, detail=str(e))
    return {"id": eid}
