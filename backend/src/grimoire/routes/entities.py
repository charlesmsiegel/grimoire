"""The generic entity surface shared by worlds and campaigns.

``/worlds/{wid}/{kind}`` and ``/campaigns/{cid}/{kind}`` capture *any* third
path segment, so every literal-segment route in every other module has to be
registered before these. That is the one ordering rule the package has, and
``__init__`` keeps it by including this router last — see
``tests/test_route_order.py``, which fails if a literal route ends up shadowed.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile

from .. import store
from ..llm import LLMClient
from .common import (
    _campaign_root_or_404,
    _draft_description,
    _fresh_or_409,
    _serve_image,
    _upload_image_ext,
    _with_descriptions,
    _world_root_or_404,
    get_llm,
)
from .models import (
    DemoteBody,
    EntityCreate,
    EntityReclassify,
    EntityUpdate,
    ImageDescription,
    PushBody,
)

router = APIRouter()


def _entity_list(root, kind: str):
    try:
        items = store.entities.list_entities(root, kind)
    except store.entities.UnknownKind:
        raise HTTPException(status_code=404, detail="unknown kind")
    for it in items:
        p = store.assets.image_path(root, it["id"], "default", store.assets.AVATAR, base=kind)
        it["has_image"] = p is not None
        it["image_v"] = store.assets.image_version(p) if p is not None else None
    return items


def _entity_kind_or_404(kind: str) -> None:
    if kind not in store.entities.ENTITY_KINDS:
        raise HTTPException(status_code=404, detail="unknown kind")


def _check_fields(kind: str, fields: dict | None) -> None:
    if kind not in store.entities.ENTITY_KINDS:
        return  # let the store's unknown-kind handling produce the 404
    bad = store.entity_schema.invalid_keys(kind, fields or {})
    if bad:
        raise HTTPException(status_code=400, detail=f"unknown fields for {kind}: {', '.join(bad)}")
    bad_values = store.entity_schema.invalid_values(kind, fields or {})
    if bad_values:
        raise HTTPException(status_code=400,
                            detail=f"invalid values for {kind}: {', '.join(bad_values)}")


def _check_secrecy(secrecy: str | None) -> None:
    """Reject an unknown secrecy level at the save boundary.

    `entities.normalize_secrecy` is deliberately lenient so a hand-edited file
    cannot break a turn, which makes this the only place a typo can be reported
    at all — and the direction it fails in matters: silently normalizing
    `secrecy: sercet` to public would save cleanly and publish the secret.
    """
    if not secrecy:
        # None == "not in this patch, leave the stored level alone"; "" reaches
        # the store as an explicit clear-to-public, the same as `owners`.
        # Neither is a level to validate.
        return
    if str(secrecy).strip().lower() not in store.entities.SECRECY_LEVELS:
        raise HTTPException(
            status_code=400,
            detail=f"unknown secrecy: {secrecy} (expected one of "
                   f"{', '.join(store.entities.SECRECY_LEVELS)})")


def _entity_create(root, kind: str, body: EntityCreate):
    _check_fields(kind, body.fields)
    _check_secrecy(body.secrecy)
    try:
        return {"id": store.entities.create_entity(root, kind, body.name, body.body, body.keys, body.owners,
                                                    fields=body.fields, secrecy=body.secrecy)}
    except store.entities.UnknownKind:
        raise HTTPException(status_code=404, detail="unknown kind")


def _entity_read(root, kind: str, eid: str):
    try:
        return store.entities.read_entity_rev(root, kind, eid)
    except store.entities.UnknownKind:
        raise HTTPException(status_code=404, detail="unknown kind")
    except store.entities.EntityNotFound:
        raise HTTPException(status_code=404, detail="entity not found")


def _entity_update(root, kind: str, eid: str, body: EntityUpdate):
    _check_fields(kind, body.fields)
    _check_secrecy(body.secrecy)
    # Kind first: `entity_hash` answers None for a kind it has never heard of,
    # exactly as it does for a record that is gone, so leaving the 404 to the
    # store below would report an unknown kind as a conflict. And both of those
    # after the body checks above -- a malformed request is a 400 whether or not
    # the record also moved on disk.
    _entity_kind_or_404(kind)
    _fresh_or_409(body.rev, store.entities.entity_hash(root, kind, eid))
    try:
        store.entities.update_entity(root, kind, eid, name=body.name, body=body.body,
                                     keys=body.keys, owners=body.owners, fields=body.fields,
                                     secrecy=body.secrecy)
    except store.entities.UnknownKind:
        raise HTTPException(status_code=404, detail="unknown kind")
    except store.entities.EntityNotFound:
        raise HTTPException(status_code=404, detail="entity not found")
    return {"ok": True}


def _entity_delete(root, kind: str, eid: str):
    try:
        store.entities.delete_entity(root, kind, eid)
    except store.entities.UnknownKind:
        raise HTTPException(status_code=404, detail="unknown kind")
    except store.entities.EntityNotFound:
        raise HTTPException(status_code=404, detail="entity not found")
    return {"ok": True}


# ---- campaign entity CRUD: same exception -> HTTP mapping, but reads/writes
# resolve through the overlay (campaign-over-world) instead of a bare root.
def _campaign_entity_list(cid: str, kind: str):
    try:
        items = store.overlay.list_entities(cid, kind)
    except store.entities.UnknownKind:
        raise HTTPException(status_code=404, detail="unknown kind")
    for it in items:
        root = store.overlay.image_root(cid, it["id"], "default", store.assets.AVATAR, base=kind)
        p = store.assets.image_path(root, it["id"], "default", store.assets.AVATAR, base=kind)
        it["has_image"] = p is not None
        it["image_v"] = store.assets.image_version(p) if p is not None else None
    return items


def _campaign_entity_create(cid: str, kind: str, body: EntityCreate):
    _check_fields(kind, body.fields)
    _check_secrecy(body.secrecy)
    try:
        return {"id": store.overlay.create_entity(cid, kind, body.name, body.body, body.keys, body.owners,
                                                   fields=body.fields, secrecy=body.secrecy)}
    except store.entities.UnknownKind:
        raise HTTPException(status_code=404, detail="unknown kind")


def _campaign_entity_read(cid: str, kind: str, eid: str):
    try:
        return store.overlay.read_entity_rev(cid, kind, eid)
    except store.entities.UnknownKind:
        raise HTTPException(status_code=404, detail="unknown kind")
    except store.entities.EntityNotFound:
        raise HTTPException(status_code=404, detail="entity not found")


def _campaign_entity_update(cid: str, kind: str, eid: str, body: EntityUpdate):
    _check_fields(kind, body.fields)
    _check_secrecy(body.secrecy)
    # Journalled (#31): a hand edit to a campaign copy is the same kind of write
    # an absorb makes, and until now it was the one that left no trace and could
    # not be taken back. The BODY only -- that is what `undo`'s entity writer
    # restores, and claiming to reverse an edit while leaving a renamed record
    # renamed would be worse than not offering it. Secrecy is in the same
    # position as the name: this write can change it, and undo will not put it
    # back, so an undone edit restores the text without re-hiding it.
    label = f"{body.name or eid} — {kind}"
    # Before the journal opens, so a refused write leaves no undo entry
    # offering to restore a state it never replaced.
    _entity_kind_or_404(kind)
    _fresh_or_409(body.rev, store.overlay.entity_rev(cid, kind, eid))
    try:
        with store.undo.journalled(cid, {"w": "entity", "kind": kind, "id": eid},
                                   kind="lore", ref={"kind": kind, "id": eid},
                                   field="body", label=label):
            store.overlay.update_entity(cid, kind, eid, name=body.name, body=body.body,
                                        keys=body.keys, owners=body.owners,
                                        fields=body.fields, secrecy=body.secrecy)
    except store.entities.UnknownKind:
        raise HTTPException(status_code=404, detail="unknown kind")
    except store.entities.EntityNotFound:
        raise HTTPException(status_code=404, detail="entity not found")
    return {"ok": True}


def _campaign_entity_delete(cid: str, kind: str, eid: str):
    try:
        store.overlay.delete_entity(cid, kind, eid)
    except store.entities.UnknownKind:
        raise HTTPException(status_code=404, detail="unknown kind")
    except store.entities.EntityNotFound:
        raise HTTPException(status_code=404, detail="entity not found")
    return {"ok": True}


def _reclassify_target(kind: str, body: EntityReclassify) -> str:
    """The kind to move to, refused here rather than in the store when the
    request cannot mean anything.

    Both refusals are 400s and not 404s: the record and its kind are real, and
    it is the *destination* the caller named that is wrong -- a 404 would read
    as "no such record" and send them looking for the wrong thing. `characters`
    is the one worth naming, because it is a real kind and an obvious thing to
    ask for; it is a conversion rather than a move (a folder plus a card per
    version, no id to keep) and is not built.
    """
    to = (body.to or "").strip()
    if to not in store.entities.ENTITY_KINDS:
        raise HTTPException(status_code=400,
                            detail=f"cannot reclassify as {to or '(nothing)'} (expected one of "
                                   f"{', '.join(store.entities.ENTITY_KINDS)})")
    if to == kind:
        raise HTTPException(status_code=400, detail=f"already a {kind} record")
    return to


@router.post("/worlds/{wid}/{kind}/{eid}/reclassify")
def post_world_entity_reclassify(wid: str, kind: str, eid: str, body: EntityReclassify):
    """Move a world record to another generic kind, and sweep every campaign of
    that world so none of them ends up with a stale copy under the old kind and
    a duplicate under the new one (#119)."""
    _entity_kind_or_404(kind)
    to = _reclassify_target(kind, body)
    root = _world_root_or_404(wid)
    # Kind, then destination, then freshness, then existence -- the same order
    # `_entity_update` documents: a malformed request is a 400 whether or not
    # the record also moved on disk, and a caller carrying a rev for a record
    # that has since been deleted is told it is a conflict rather than a 404,
    # because `entity_hash` answers None for both and the conflict is the truer
    # of the two. A caller with no rev falls through to the existence check.
    _fresh_or_409(body.rev, store.entities.entity_hash(root, kind, eid))
    _world_entity_or_404(wid, kind, eid)
    return store.reclassify.world_entity(wid, kind, eid, to)


@router.post("/campaigns/{cid}/{kind}/{eid}/reclassify")
def post_campaign_entity_reclassify(cid: str, kind: str, eid: str, body: EntityReclassify):
    """Move this campaign's copy of a record to another generic kind. The world
    keeps its own; the campaign's copy is materialized first and the world's is
    tombstoned, so the record is listed once, under its new kind (#119)."""
    _entity_kind_or_404(kind)
    to = _reclassify_target(kind, body)
    _campaign_root_or_404(cid)
    _fresh_or_409(body.rev, store.overlay.entity_rev(cid, kind, eid))
    # Resolves through the overlay, so an inherited record passes and a
    # tombstoned one does not -- the same two answers the reclassify itself
    # would give, asked where they can be reported as a 404.
    _campaign_entity_or_404(cid, kind, eid)
    return {"id": store.reclassify.campaign_entity(cid, kind, eid, to)}


@router.get("/worlds/{wid}/{kind}")
def get_world_entities(wid: str, kind: str):
    return _entity_list(_world_root_or_404(wid), kind)


@router.post("/worlds/{wid}/{kind}")
def post_world_entity(wid: str, kind: str, body: EntityCreate):
    return _entity_create(_world_root_or_404(wid), kind, body)


@router.get("/worlds/{wid}/{kind}/{eid}")
def get_world_entity(wid: str, kind: str, eid: str):
    return _entity_read(_world_root_or_404(wid), kind, eid)


@router.put("/worlds/{wid}/{kind}/{eid}")
def put_world_entity(wid: str, kind: str, eid: str, body: EntityUpdate):
    return _entity_update(_world_root_or_404(wid), kind, eid, body)


@router.delete("/worlds/{wid}/{kind}/{eid}")
def delete_world_entity(wid: str, kind: str, eid: str):
    root = _world_root_or_404(wid)
    out = _entity_delete(root, kind, eid)
    # After the delete, so a crash between the two leaves the pre-#225 state
    # rather than state stripped off a record that is still there.
    store.overlay.forget_world_record(root, kind, eid)
    return out


# ---- entity images (locations/lore) — assets keyed <kind>/<eid>/assets/default ----
_IMAGE_KINDS = store.entities.ENTITY_KINDS + ("greetings",)


def _image_kind_or_404(kind: str) -> None:
    # read side only: greeting images are stored by localize_greeting / scripts,
    # not uploaded over HTTP, so the write routes keep the strict entity check
    if kind not in _IMAGE_KINDS:
        raise HTTPException(status_code=404, detail="unknown kind")


def _world_entity_or_404(wid: str, kind: str, eid: str):
    """The world root, once `kind`/`eid` are known to name a real entity.

    Every *write* on the entity image surface goes through this (#373).
    `assets.put_image` creates the directory it writes into, so an unchecked id
    turned a typo into `locations/<typo>/assets/default/avatar.png`: bytes no
    listing shows (`list_entities` enumerates the records that exist) and no
    delete route can name, reported to the caller as a successful upload.

    The reads stay ungated, the same split `common._world_char_version_or_404`
    documents for the actor surface: they create nothing, they already answer
    "no image" for an id that names nothing, and `GET .../images/avatar` is hit
    once per tile per rendered grid. Honest about its reach, too: this refuses
    an id that names nothing *now*, not one deleted between the check and the
    write -- a guard against a typo, not against a race.
    """
    root = _world_root_or_404(wid)
    try:
        store.entities.require_entity(root, kind, eid)
    except store.entities.UnknownKind:
        raise HTTPException(status_code=404, detail="unknown kind")
    except store.entities.EntityNotFound:
        raise HTTPException(status_code=404, detail="entity not found")
    return root


def _campaign_entity_or_404(cid: str, kind: str, eid: str):
    """The campaign root, once `kind`/`eid` are known to name a real entity.

    Resolved through `overlay.entity_root`, not the campaign root: on a thin
    campaign an unmaterialized record is still the world's file, so a
    croot-only check would 404 every inherited entity -- which is all of them.
    The root this *returns* is always the campaign's, because that is where a
    write has to land. A tombstoned entity resolves to the campaign root, where
    there is no record, so it is refused for the same reason a typo is.

    Why gate at all, and why the reads here are left ungated: `_world_entity_or_404`.
    """
    root = _campaign_root_or_404(cid)
    try:
        store.entities.require_entity(store.overlay.entity_root(cid, kind, eid), kind, eid)
    except store.entities.UnknownKind:
        raise HTTPException(status_code=404, detail="unknown kind")
    except store.entities.EntityNotFound:
        raise HTTPException(status_code=404, detail="entity not found")
    return root


# `root` on both of these comes from `_world_entity_or_404` /
# `_campaign_entity_or_404`, which is where the kind and the id are checked --
# not here, and not in `assets` either. A handler that reaches these with a
# bare root files art under an id or a kind that names nothing; the two
# enumerating guards in `tests/test_routes.py` are what hold that.
async def _entity_image_put(root, kind: str, eid: str, name: str, file: UploadFile):
    data = await file.read()
    ext = _upload_image_ext(data)  # the bytes name the type, not `file.filename` (#321)
    try:
        stored = store.assets.put_image(root, eid, "default", name, data, ext, base=kind)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"name": name, "ext": stored}


def _entity_image_promote(root, kind: str, eid: str, name: str):
    try:
        store.assets.promote_image(root, eid, "default", name, base=kind)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="image not found")
    except ValueError as exc:
        # an externally-placed file whose extension we never accepted for upload
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True}


@router.get("/worlds/{wid}/{kind}/{eid}/images")
def list_world_entity_images(wid: str, kind: str, eid: str):
    _image_kind_or_404(kind)
    root = _world_root_or_404(wid)
    return _with_descriptions(
        store.assets.list_images(root, eid, "default", base=kind),
        store.image_descriptions.read_all(root, eid, "default", base=kind))


@router.get("/worlds/{wid}/{kind}/{eid}/images/{name}")
def get_world_entity_image(wid: str, kind: str, eid: str, name: str, request: Request):
    _image_kind_or_404(kind)
    return _serve_image(_world_root_or_404(wid), eid, "default", name, base=kind, request=request)


@router.put("/worlds/{wid}/{kind}/{eid}/images/{name}")
async def put_world_entity_image(wid: str, kind: str, eid: str, name: str, file: UploadFile = File(...)):
    return await _entity_image_put(_world_entity_or_404(wid, kind, eid), kind, eid, name, file)


@router.delete("/worlds/{wid}/{kind}/{eid}/images/{name}")
def delete_world_entity_image(wid: str, kind: str, eid: str, name: str):
    # Gated on the record, not on the image: removing an image that is already
    # gone is the caller getting what they asked for, but removing one from an
    # entity that does not exist is a typo worth reporting.
    store.assets.delete_image(_world_entity_or_404(wid, kind, eid), eid, "default", name, base=kind)
    return {"ok": True}


@router.post("/worlds/{wid}/{kind}/{eid}/images/{name}/promote")
def promote_world_entity_image(wid: str, kind: str, eid: str, name: str):
    return _entity_image_promote(_world_entity_or_404(wid, kind, eid), kind, eid, name)


# ---- campaign entity CRUD (generic; see the module docstring on ordering) ----
@router.get("/campaigns/{cid}/{kind}")
def get_campaign_entities(cid: str, kind: str):
    _campaign_root_or_404(cid)
    return _campaign_entity_list(cid, kind)


@router.post("/campaigns/{cid}/{kind}")
def post_campaign_entity(cid: str, kind: str, body: EntityCreate):
    _campaign_root_or_404(cid)
    return _campaign_entity_create(cid, kind, body)


@router.get("/campaigns/{cid}/{kind}/{eid}")
def get_campaign_entity(cid: str, kind: str, eid: str):
    _campaign_root_or_404(cid)
    return _campaign_entity_read(cid, kind, eid)


@router.put("/campaigns/{cid}/{kind}/{eid}")
def put_campaign_entity(cid: str, kind: str, eid: str, body: EntityUpdate):
    _campaign_root_or_404(cid)
    return _campaign_entity_update(cid, kind, eid, body)


@router.delete("/campaigns/{cid}/{kind}/{eid}")
def delete_campaign_entity(cid: str, kind: str, eid: str):
    _campaign_root_or_404(cid)
    return _campaign_entity_delete(cid, kind, eid)


@router.post("/worlds/{wid}/{kind}/{eid}/images/{name}/description/draft")
async def post_world_entity_image_description_draft(wid: str, kind: str, eid: str, name: str,
                                                    client: LLMClient = Depends(get_llm)):
    """A model-drafted first pass at what this entity's picture shows."""
    root = _world_entity_or_404(wid, kind, eid)
    try:
        subject = store.entities.read_entity(root, kind, eid)["meta"]["name"]
    except store.entities.EntityNotFound:
        subject = ""
    return await _draft_description(
        client, store.assets.image_path(root, eid, "default", name, base=kind), subject)


@router.put("/worlds/{wid}/{kind}/{eid}/images/{name}/description")
def put_world_entity_image_description(wid: str, kind: str, eid: str, name: str,
                                       body: ImageDescription):
    root = _world_entity_or_404(wid, kind, eid)
    try:
        store.image_descriptions.set_description(root, eid, "default", name,
                                                 body.description, base=kind)
    except ValueError:
        # `from None`: the strict-write ValueError is this module's own
        # implementation detail, and chaining it onto the 404 says nothing a
        # caller can act on.
        raise HTTPException(status_code=404, detail="image not found") from None
    return {"ok": True}


@router.put("/campaigns/{cid}/{kind}/{eid}/images/{name}/description")
def put_campaign_entity_image_description(cid: str, kind: str, eid: str, name: str,
                                          body: ImageDescription):
    _campaign_entity_or_404(cid, kind, eid)
    try:
        # Through the overlay: the write lands campaign-side, and the existence
        # gate is the overlay union, so a thin campaign can describe art whose
        # bytes it still inherits without diverging the art itself.
        store.overlay.set_description(cid, eid, "default", name, body.description, base=kind)
    except ValueError:
        # `from None`: the strict-write ValueError is this module's own
        # implementation detail, and chaining it onto the 404 says nothing a
        # caller can act on.
        raise HTTPException(status_code=404, detail="image not found") from None
    return {"ok": True}


@router.get("/campaigns/{cid}/{kind}/{eid}/images")
def list_campaign_entity_images(cid: str, kind: str, eid: str):
    _campaign_root_or_404(cid)
    _entity_kind_or_404(kind)
    return _with_descriptions(
        store.overlay.list_images(cid, eid, "default", base=kind),
        store.overlay.read_descriptions(cid, eid, "default", base=kind))


@router.get("/campaigns/{cid}/{kind}/{eid}/images/{name}")
def get_campaign_entity_image(cid: str, kind: str, eid: str, name: str, request: Request):
    _campaign_root_or_404(cid)
    _image_kind_or_404(kind)
    return _serve_image(store.overlay.image_root(cid, eid, "default", name, base=kind),
                        eid, "default", name, base=kind, request=request)


@router.put("/campaigns/{cid}/{kind}/{eid}/images/{name}")
async def put_campaign_entity_image(cid: str, kind: str, eid: str, name: str, file: UploadFile = File(...)):
    return await _entity_image_put(_campaign_entity_or_404(cid, kind, eid), kind, eid, name, file)


@router.delete("/campaigns/{cid}/{kind}/{eid}/images/{name}")
def delete_campaign_entity_image(cid: str, kind: str, eid: str, name: str):
    _campaign_entity_or_404(cid, kind, eid)
    # tombstone so a still-materialized world image doesn't show back through
    # the overlaid read the moment the campaign's own copy is gone.
    store.overlay.delete_image(cid, eid, "default", name, base=kind)
    return {"ok": True}


@router.post("/campaigns/{cid}/{kind}/{eid}/images/{name}/promote")
def promote_campaign_entity_image(cid: str, kind: str, eid: str, name: str):
    _campaign_entity_or_404(cid, kind, eid)
    try:
        store.overlay.promote_image(cid, eid, "default", name, base=kind)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="image not found")
    except ValueError as exc:
        # an externally-placed file whose extension we never accepted for upload
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True}


# ---- library moves: promote / push / dependents / demote (#52, #53, #60) ----
#
# All four live here rather than in `campaigns`/`worlds` because they are
# `{kind}`-shaped, and this module is included last -- so a literal fifth
# segment can neither shadow anything nor be shadowed. `kind` is left to the
# store to judge: unlike the CRUD above, these carry actors as well as flat
# entities, and which kinds each one accepts is part of what `store/sync.py`
# documents.
def _library_move_or_409(fn):
    """Run a store-level move, mapping its refusals onto HTTP.

    Every refusal is a 409 rather than a 400: each reports the *state* of two
    records that cannot both be what the caller assumed, which is what 409
    means -- and it keeps the frontend to one branch per code.
    """
    try:
        return fn()
    except store.entities.UnknownKind as exc:
        raise HTTPException(status_code=404, detail="unknown kind") from exc
    except (store.entities.EntityNotFound, store.greetings.GreetingNotFound,
            store.characters.CharacterNotFound, store.pcs.PCNotFound) as exc:
        raise HTTPException(status_code=404, detail="record not found") from exc
    except store.worlds.WorldNotFound as exc:
        raise HTTPException(status_code=404, detail="world not found") from exc
    except store.sync.PushConflictError as exc:
        # Its own `kind`: this is the one refusal the caller can resolve by
        # forcing, and the UI must be able to offer that without matching prose.
        raise HTTPException(status_code=409,
                            detail={"detail": str(exc), "kind": "push_conflict"}) from exc
    except store.sync.LibraryMoveError as exc:
        raise HTTPException(
            status_code=409,
            detail={"detail": str(exc), "kind": "library_move_refused"}) from exc


@router.post("/campaigns/{cid}/{kind}/{eid}/promote")
def post_promote_to_library(cid: str, kind: str, eid: str):
    """Publish a campaign-local record into the campaign's world (#52, #60)."""
    _campaign_root_or_404(cid)
    _library_move_or_409(lambda: store.sync.promote(cid, kind, eid))
    return {"ok": True}


@router.get("/campaigns/{cid}/{kind}/{eid}/library")
def get_library_status(cid: str, kind: str, eid: str):
    """Where this campaign record stands relative to the library — which of
    promote and push the editor should offer, decided server-side (#52, #53)."""
    _campaign_root_or_404(cid)
    return _library_move_or_409(lambda: store.sync.library_status(cid, kind, eid))


@router.post("/campaigns/{cid}/{kind}/{eid}/push")
def post_push_to_library(cid: str, kind: str, eid: str, body: PushBody | None = None):
    """Save a campaign's override of a library record back into the library (#53)."""
    _campaign_root_or_404(cid)
    force = bool(body.force) if body is not None else False
    _library_move_or_409(lambda: store.sync.push(cid, kind, eid, force=force))
    return {"ok": True}


@router.get("/worlds/{wid}/{kind}/{eid}/dependents")
def get_library_dependents(wid: str, kind: str, eid: str):
    """The campaigns that would notice this library record going away (#52)."""
    return _library_move_or_409(lambda: store.sync.dependents(wid, kind, eid))


@router.post("/worlds/{wid}/{kind}/{eid}/demote")
def post_demote_from_library(wid: str, kind: str, eid: str, body: DemoteBody | None = None):
    """Take a record out of the library, optionally leaving each dependent
    campaign holding its own copy (#52)."""
    opts = body or DemoteBody()
    return _library_move_or_409(
        lambda: store.sync.demote(wid, kind, eid,
                                  copy_down=bool(opts.copy_down), target=opts.target))
