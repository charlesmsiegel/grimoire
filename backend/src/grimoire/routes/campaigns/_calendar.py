"""Campaign HTTP endpoints: calendar domain."""


from __future__ import annotations

import contextlib
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
from fastapi.responses import JSONResponse, Response

from ... import store
from ...llm import LLMClient
from ...store import campaign_settings, compiled, overview
from ...store import paths as library_paths
from ...store.continuity import doc as continuity_doc
from ...store.continuity import effective as continuity_effective
from ...store.inference import settings as inference_settings
from .. import characters as character_routes
from .. import ledger as ledger_routes
from .. import runs
from ..common import (
    PC_HISTORY_MISSES,
    _campaign_root_or_404,
    _content_fields,
    _display_name_or_400,
    _dump,
    _page_of,
    _page_window,
    _response_body,
    _serve_image,
    _serve_image_file,
    _sheet_failure_status,
    _upload_image_ext,
    _with_descriptions,
    _write_response,
    computes_only,
    draft_completion,
    get_llm,
    image_draft_prompt,
    image_id_field,
    image_promote_guards,
    leaves_campaign_unchanged,
    pc_history_404,
    record_art_mutation,
    require_inference,
    stamps_campaign_write,
)
from ..models import (
    AdvanceTime,
    AvatarFocus,
    CalendarConfig,
    CampaignClimate,
    CharacterBirthdate,
    CharacterCreate,
    CharacterTags,
    CopyFromGreeting,
    DefaultVersion,
    ForkCampaign,
    GroupStateSave,
    ImageDescription,
    ImagePromote,
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
    ScheduledEventCreate,
    ScheduledEventEdit,
    SyncPin,
    TaglineSave,
    VersionCreate,
    VersionUpdate,
    VoiceAnchorSave,
)
from ..overview_log import overview_read


from ._router import router
from ._settings import _clock_friendly

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
        raise HTTPException(status_code=400, detail=str(e)) from e
    except store.events.EventError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True, "id": eid}


@router.put("/campaigns/{cid}/events/{eid}")
def put_campaign_event(cid: str, eid: str, body: ScheduledEventEdit):
    _campaign_root_or_404(cid)
    try:
        found = store.events.update(cid, eid, name=body.name, date=body.date,
                                    note=body.note)
    except store.calendars.CalendarError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except store.events.EventError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
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
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not found:
        raise HTTPException(status_code=404, detail="event not found")
    return {"ok": True}


@router.delete("/campaigns/{cid}/events/{eid}")
def delete_campaign_event(cid: str, eid: str):
    """Delete one event, and retire the pre-notice it was dismissed under (#106).

    `events.create` allocates an id by uniquifying the slug against the events
    that exist *now*, so deleting "the-envoy-arrives" frees that id for the next
    event of the same name. A notice key is `event:{fixed}:{id}`, so a recreated
    event on the same day would regenerate the exact key the deleted one was
    dismissed under and inherit its acknowledgement -- a distinct occurrence,
    silently never warned about.

    Retiring the key here rather than giving events non-reusable ids: this is
    the notices ledger's problem with a reusable identifier, not a defect in
    #101's id scheme, and it is the only place that knows both. Done from the
    route because `notices` imports `events` -- the reverse call would close a
    cycle the import guard forbids -- and after the delete, so a refused delete
    cannot clear an acknowledgement for an event that is still there.

    Every day it was ever warned about, not just its current one: an event
    dismissed, re-dated and dismissed again holds an acknowledgement under each
    day, and clearing only the latest would leave the earlier key to suppress a
    recreation dated back to it. `notices.forget_event` keys on the id alone,
    which needs no calendar and so cannot half-succeed.

    Both under ONE hold, because keying on the id alone is only safe while no
    other id of that name can exist. `events.delete` frees the id the moment it
    releases the lock, so between the two an unlucky concurrent `create` can
    take it back, and a dismissal of the NEW event's notice would then be
    matched and removed by this cleanup — a banner returning after a dismissal
    that succeeded. The lock is an `RLock` and both mutators take it themselves,
    so this costs two reentrant acquires and closes the window; neither of them
    runs calendar code, which is the thing that must never happen under it.

    Reviewed continuity links naming the event go in the same hold, for the same
    reason: a link is keyed by ``event:<id>``, so one left behind would bind to a
    recreation of the same name and date a commitment against the wrong day. An
    event is never part of a merge, so only an unreadable ``links`` section can
    stop that cleanup -- and then the delete is refused before anything is
    written, like a thread's: once the id is free, a link nobody could remove
    would attach to whatever is created under it next. A disk that fails after
    the delete landed answers `ledger.forget_or_partial`'s ``partial_delete``,
    which says the event is gone and where to remove what is left.
    """
    _campaign_root_or_404(cid)
    with store.locks.campaign_lock(cid):
        if set(continuity_doc.malformed(cid)) & {"file", "links"}:
            raise HTTPException(status_code=409, detail={
                "kind": "malformed",
                "detail": "continuity.json cannot be read; repair it before deleting events"})
        stored = store.events.read(cid).get(eid)
        name = str(stored.get("name") or "") if isinstance(stored, dict) else ""
        try:
            found = store.events.delete(cid, eid)
        except store.events.EventError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        if not found:
            raise HTTPException(status_code=404, detail="event not found")
        # A corrupt ledger is the writer's refusal, not this completed delete's
        # failure: the event is gone by here, and raising would report a write
        # that actually landed as an error.
        with contextlib.suppress(store.notices.NoticeError):
            store.notices.forget_event(cid, eid)
        ledger_routes.forget_or_partial(cid, f"event:{eid}", name=name, noun="event")
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
    try:
        return {"ok": True, "marked": store.notices.mark(cid, body.keys, body.scene)}
    except store.notices.NoticeError as e:
        # A hand-edited or sync-damaged ledger is refused rather than replaced:
        # say so, so it can be repaired instead of silently losing every
        # acknowledgement it still holds.
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/campaigns/{cid}/notices/forget")
def post_campaign_notices_forget(cid: str, body: NoticeMark):
    """Take back an acknowledgement, so the notice shows again.

    The undo for a banner dismissed by mistake, and the only way back: `mark`
    will not overwrite an existing stamp, so without this one misclick silences
    an event until its day has gone by.
    """
    _campaign_root_or_404(cid)
    try:
        return {"ok": True, "forgotten": store.notices.forget(cid, body.keys)}
    except store.notices.NoticeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


