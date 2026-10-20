"""Campaign HTTP endpoints: settings domain."""


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
from ._names import _record_name



@router.get("/campaigns/{cid}/settings")
def get_campaign_settings(cid: str):
    try:
        return campaign_settings.view(cid)
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found") from None
    except (OSError, UnicodeError):
        raise HTTPException(status_code=409, detail="campaign.md is unreadable") from None


@router.put("/campaigns/{cid}/settings")
@stamps_campaign_write
def put_campaign_settings(cid: str, body: dict):
    try:
        campaign_settings.write(cid, body)
        return campaign_settings.view(cid)
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found") from None
    except (OSError, UnicodeError):
        raise HTTPException(status_code=409, detail="campaign.md is unreadable") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


# ---- group state (#47): campaign-local, not covered by generic entity CRUD
# (path shape groups/{gid}/state cannot collide with /{kind}/{eid} or its
# /images sub-paths, so order relative to the generic routes doesn't matter)
@router.get("/campaigns/{cid}/groups/{gid}/state")
def get_group_state(cid: str, gid: str):
    if not store.campaigns.campaign_exists(cid):
        raise HTTPException(status_code=404, detail="campaign not found")
    try:
        store.overlay.read_entity(cid, "groups", gid)
    except store.entities.EntityNotFound as exc:
        raise HTTPException(status_code=404, detail="group not found") from exc
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
    except store.entities.EntityNotFound as exc:
        raise HTTPException(status_code=404, detail="group not found") from exc
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
        raise HTTPException(status_code=400, detail=str(e)) from e
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"could not write climate: {e}") from e
    return {"ok": True, "default_climate": body.default_climate}


# ---- campaigns ----
@router.get("/campaigns")
def get_campaigns():
    # Composed by `store.overview.campaign_cards` (spec 04 section 4.2): the
    # campaign row, the scene summary (head-only, remembered in-process) and
    # three small live reads. `activity` is the campaign's high-water mark
    # (campaign.md's `updated` only moves on a metadata write, so playing a
    # scene would not rank it), folded through `best_stamp` over every scene
    # stamp so one bad stamp cannot win; `absorbed` is how much of the
    # chronicle is caught up, a count rather than a high-water mark so a gap
    # in the middle shows.
    with overview_read("campaigns", "library"):
        return overview.campaign_cards()


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
           # `None` is "the request said nothing about it"; `write_calendar`
           # resolves it against the stored window rather than the default.
           "warn_days": body.warn_days}
    try:
        store.calendars.validate_calendar(cfg)
    except store.calendars.CalendarError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
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
    # The write token beside the moment (#409): this is the panel's one read of
    # the clock, so it is where a caller that has not previewed anything yet
    # picks one up. Read BEFORE the state it travels with, the same order
    # `/advance/preview` takes and for the same reason: a write landing between
    # the two then makes the token stale for a reading that already reflects
    # it, so anything priced against it is refused. Read after, the token would
    # be current for a reading taken before that write -- the one pairing this
    # guard exists to refuse, handed out certified as good.
    token = store.revision.current(cid)
    clock = store.clock.state(cid)   # one read for the moment and the log both
    return {"now": clock["now"], "friendly": _clock_friendly(cid, clock["now"]),
            "log": clock["log"], "revision": token}


@router.post("/campaigns/{cid}/advance/preview")
@computes_only
def post_advance_preview(cid: str, body: AdvanceTime):
    """The digest the same body would produce, writing nothing — so the reader
    sees what a skip crosses *before* confirming it. Deterministic, so the
    preview and the advance that follows agree.

    `@computes_only` because it persists nothing, which was always true and was
    always the rule for a POST like this one — the marker was simply missed. It
    is load-bearing now rather than tidy: this is where a caller picks up the
    write token it confirms with (#409), and a preview that stamped the campaign
    would hand back a token its own response had already invalidated.
    """
    _campaign_root_or_404(cid)
    # Read BEFORE the digest, and that order is the whole of the guarantee: a
    # write landing between the two makes the token stale for a digest that
    # already reflects it, so confirming with it is refused and the caller
    # re-prices. Reading it after would hand back a token that is current for a
    # digest measured before that write -- the exact pairing the guard exists to
    # refuse, certified as good.
    token = store.revision.current(cid)
    try:
        return {"digest": store.clock.preview(cid, to=body.to, days=body.days),
                "revision": token}
    except (store.clock.ClockError, store.calendars.CalendarError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


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
        result = store.clock.advance(cid, to=body.to, days=body.days, reason=body.reason,
                                     expect_revision=body.expect_revision)
    except (store.clock.ClockError, store.calendars.CalendarError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except store.RevisionMismatchError as e:
        # A refusal in the shape the client already handles for `scene_busy` and
        # `runs_in_flight`: a `kind` it can branch on, a sentence it can show,
        # and -- unlike those two -- what to price against next, so re-asking
        # costs one call rather than two. 409 rather than 412: the request was
        # not conditional on a representation the client fetched, it raced
        # another writer, which is what this status is for everywhere else in
        # this app.
        raise HTTPException(status_code=409, detail={
            "kind": "campaign_moved", "revision": e.current,
            "detail": "this campaign changed while the skip was being decided; "
                      "preview it again"}) from e
    # Taken off the digest rather than resolved again: the digest's target IS the
    # new `now` (both branches — a no-op advance returns the moment it was already
    # at), and resolving the provider a second time re-imports and re-runs a
    # user-authored calendar plugin for a string we are already holding.
    return {"ok": True, **result, "friendly": result["digest"]["to_friendly"]}


