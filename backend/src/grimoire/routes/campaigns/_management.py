"""Campaign HTTP endpoints: management domain."""


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

@router.put("/campaigns/{cid}")
def put_campaign(cid: str, body: NameBody):
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    try:
        store.campaigns.rename_campaign(cid, name)
    except store.campaigns.CampaignNotFound as exc:
        raise HTTPException(status_code=404, detail="campaign not found") from exc
    return {"id": cid, "name": name}


# Declared here rather than in `entities`, which registers the
# `/campaigns/{cid}/{kind}` catch-alls -- `routes/__init__` includes that module
# last precisely so a literal third segment like `fork` is still reachable.
@router.post("/campaigns/{cid}/fork")
@leaves_campaign_unchanged
def post_campaign_fork(cid: str, body: ForkCampaign, request: Request):
    """Fork `cid` into a new campaign, optionally cut back to an earlier scene.

    Returns the fork's id alongside the store's own report of the cut, which
    the client shows verbatim: a retrospective fork restores what carries the
    scene's id and reports the rest rather than pretending (`store/fork.py`).

    Held against image-store maintenance for the whole fork, which copies the
    campaign's placements and any legacy files a migration may be moving.
    """
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    # "" and None mean the same thing here -- a client that always sends the
    # field should not get a 404 for a scene called "".
    from_scene = (body.from_scene or "").strip() or None
    try:
        # The key is passed through VERBATIM, where `name` above is stripped:
        # a name is text a reader will see, and a key is an opaque value whose
        # only property is equality. Stripping it would make `"job"` and
        # `" job "` collide, so a client that sent the second would be answered
        # with the fork the first made (Codex review). The empty string is still
        # "no key" -- that is the store's own rule, not a normalization.
        with runs.maintenance_excluded(request.app):
            return store.fork.fork_campaign(cid, name, from_scene,
                                            key=body.idempotency_key,
                                            expect_revision=body.expect_revision,
                                            from_index=body.from_index)
    except IndexError as e:                     # a `from_index` outside the scene
        raise HTTPException(status_code=400, detail="message index out of range") from e
    except store.campaigns.CampaignNotFound as exc:
        raise HTTPException(status_code=404, detail="campaign not found") from exc
    except store.SceneNotFound as exc:
        raise HTTPException(status_code=404, detail="scene not found") from exc
    except ValueError as e:     # a key past `fork.KEY_LIMIT`, a `from_index` without a scene
        raise HTTPException(status_code=400, detail=str(e)) from e
    except store.fork.ForkContentionError as e:
        # Every id this fork computed was taken by another writer before it
        # could claim one. Transient by construction and nothing was created,
        # so 409 and let the caller ask again -- the same status a lock this
        # request could not take answers with (`main.store_busy_handler`).
        raise HTTPException(status_code=409, detail=str(e)) from e
    except store.RevisionMismatchError as e:
        # The same refusal `/advance` gives, in the same shape: this is one
        # operation across two endpoints, and a client that has to branch on two
        # spellings of "the campaign moved" will get one of them wrong.
        raise HTTPException(status_code=409, detail={
            "kind": "campaign_moved", "revision": e.current,
            "detail": "this campaign changed while the checkpoint was being "
                      "decided; preview it again"}) from e


@router.delete("/campaigns/{cid}")
def delete_campaign(cid: str, request: Request):
    # Held against image-store maintenance for the whole delete, for the world
    # route's reason: a pass must not have a tree removed beneath it.
    root, intent, prepared = None, None, False
    try:
        with runs.maintenance_excluded(request.app):
            with library_paths.library_guard():
                root = library_paths.home()
                # ASGI response/error bookkeeping runs after this thread's
                # pin exits; carry the same root across that boundary too.
                request.scope['grimoire_library_root'] = root
                with library_paths.pin_library(root):
                    try:
                        store.campaigns.lifecycle._preflight_delete(cid)
                        intent = compiled.prepare_delete(root)
                        prepared = True
                        store.campaigns.delete_campaign(cid)
                    except store.campaigns.CampaignNotFound as exc:
                        raise HTTPException(status_code=404, detail="campaign not found") from exc
                    # Its "not carried over" notes on `/models` go too: a
                    # replacement under this slug must not be shown a loss
                    # that happened here. Best effort, pinned to this library.
                    inference_settings.forget_campaign_notes(store.inference_retired.campaign_scope(cid))
            # AFTER the delete, so a campaign that could not be removed keeps
            # its runs. Slugs are reusable; a replacement inside the retention
            # window must not inherit this one's drafts (RunRegistry).
            # Release the leaf root mutex before entering that registry.
            runs.forget_subject(request.app, runs.campaign_subject(cid))
    finally:
        if prepared and root is not None:
            compiled.purge_for_delete(root, intent=intent)
    # Nothing is done about this campaign's ledger rows, and `usage_rollup` has
    # no `forget` for the same reason: the ledger is append-only and records
    # money that was actually spent, so a replacement campaign reusing the slug
    # inherits those rows on every all-time surface there is. Making one of
    # them answer differently would put two figures on screen with nothing to
    # say which was right.
    return {"ok": True}


@router.get("/campaigns/{cid}/response")
def get_campaign_response(cid: str):
    try:
        campaign_meta = store.campaigns.read_campaign(cid)["meta"]
    except store.campaigns.CampaignNotFound as exc:
        raise HTTPException(status_code=404, detail="campaign not found") from exc
    cfg = store.read_config()
    return _response_body({}, campaign_meta, cfg, campaign_meta)


@router.put("/campaigns/{cid}/response")
def put_campaign_response(cid: str, body: ResponseSettings):
    fields = {k: v for k, v in _dump(body).items() if v is not None}
    try:
        _write_response(lambda f: store.campaigns.set_campaign_response(cid, f), fields)
    except store.campaigns.CampaignNotFound as exc:
        raise HTTPException(status_code=404, detail="campaign not found") from exc
    return {"ok": True}


