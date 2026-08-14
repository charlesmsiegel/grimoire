"""The generic entity surface shared by worlds and campaigns.

``/worlds/{wid}/{kind}`` and ``/campaigns/{cid}/{kind}`` capture *any* third
path segment, so every literal-segment route in every other module has to be
registered before these. That is the one ordering rule the package has, and
``__init__`` keeps it by including this router last — see
``tests/test_route_order.py``, which fails if a literal route ends up shadowed.
"""

from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, Request, UploadFile

from .. import store
from .common import (_campaign_root_or_404, _serve_image, _upload_image_ext,
                     _world_root_or_404)
from .models import EntityCreate, EntityUpdate

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


def _entity_create(root, kind: str, body: EntityCreate):
    _check_fields(kind, body.fields)
    try:
        return {"id": store.entities.create_entity(root, kind, body.name, body.body, body.keys, body.owners,
                                                    fields=body.fields)}
    except store.entities.UnknownKind:
        raise HTTPException(status_code=404, detail="unknown kind")


def _entity_read(root, kind: str, eid: str):
    try:
        return store.entities.read_entity(root, kind, eid)
    except store.entities.UnknownKind:
        raise HTTPException(status_code=404, detail="unknown kind")
    except store.entities.EntityNotFound:
        raise HTTPException(status_code=404, detail="entity not found")


def _entity_update(root, kind: str, eid: str, body: EntityUpdate):
    _check_fields(kind, body.fields)
    try:
        store.entities.update_entity(root, kind, eid, name=body.name, body=body.body,
                                     keys=body.keys, owners=body.owners, fields=body.fields)
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
    try:
        return {"id": store.overlay.create_entity(cid, kind, body.name, body.body, body.keys, body.owners,
                                                   fields=body.fields)}
    except store.entities.UnknownKind:
        raise HTTPException(status_code=404, detail="unknown kind")


def _campaign_entity_read(cid: str, kind: str, eid: str):
    try:
        return store.overlay.read_entity(cid, kind, eid)
    except store.entities.UnknownKind:
        raise HTTPException(status_code=404, detail="unknown kind")
    except store.entities.EntityNotFound:
        raise HTTPException(status_code=404, detail="entity not found")


def _campaign_entity_update(cid: str, kind: str, eid: str, body: EntityUpdate):
    _check_fields(kind, body.fields)
    # Journalled (#31): a hand edit to a campaign copy is the same kind of write
    # an absorb makes, and until now it was the one that left no trace and could
    # not be taken back. The BODY only -- that is what `undo`'s entity writer
    # restores, and claiming to reverse an edit while leaving a renamed record
    # renamed would be worse than not offering it.
    label = f"{body.name or eid} — {kind}"
    try:
        with store.undo.journalled(cid, {"w": "entity", "kind": kind, "id": eid},
                                   kind="lore", ref={"kind": kind, "id": eid},
                                   field="body", label=label):
            store.overlay.update_entity(cid, kind, eid, name=body.name, body=body.body,
                                        keys=body.keys, owners=body.owners, fields=body.fields)
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
def _entity_kind_or_404(kind: str) -> None:
    if kind not in store.entities.ENTITY_KINDS:
        raise HTTPException(status_code=404, detail="unknown kind")


_IMAGE_KINDS = store.entities.ENTITY_KINDS + ("greetings",)


def _image_kind_or_404(kind: str) -> None:
    # read side only: greeting images are stored by localize_greeting / scripts,
    # not uploaded over HTTP, so the write routes keep the strict entity check
    if kind not in _IMAGE_KINDS:
        raise HTTPException(status_code=404, detail="unknown kind")


async def _entity_image_put(root, kind: str, eid: str, name: str, file: UploadFile):
    _entity_kind_or_404(kind)
    data = await file.read()
    ext = _upload_image_ext(data)  # the bytes name the type, not `file.filename` (#321)
    try:
        stored = store.assets.put_image(root, eid, "default", name, data, ext, base=kind)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"name": name, "ext": stored}


def _entity_image_promote(root, kind: str, eid: str, name: str):
    _entity_kind_or_404(kind)
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
    return store.assets.list_images(_world_root_or_404(wid), eid, "default", base=kind)


@router.get("/worlds/{wid}/{kind}/{eid}/images/{name}")
def get_world_entity_image(wid: str, kind: str, eid: str, name: str, request: Request):
    _image_kind_or_404(kind)
    return _serve_image(_world_root_or_404(wid), eid, "default", name, base=kind, request=request)


@router.put("/worlds/{wid}/{kind}/{eid}/images/{name}")
async def put_world_entity_image(wid: str, kind: str, eid: str, name: str, file: UploadFile = File(...)):
    return await _entity_image_put(_world_root_or_404(wid), kind, eid, name, file)


@router.delete("/worlds/{wid}/{kind}/{eid}/images/{name}")
def delete_world_entity_image(wid: str, kind: str, eid: str, name: str):
    _entity_kind_or_404(kind)
    store.assets.delete_image(_world_root_or_404(wid), eid, "default", name, base=kind)
    return {"ok": True}


@router.post("/worlds/{wid}/{kind}/{eid}/images/{name}/promote")
def promote_world_entity_image(wid: str, kind: str, eid: str, name: str):
    return _entity_image_promote(_world_root_or_404(wid), kind, eid, name)


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


@router.get("/campaigns/{cid}/{kind}/{eid}/images")
def list_campaign_entity_images(cid: str, kind: str, eid: str):
    _campaign_root_or_404(cid)
    _entity_kind_or_404(kind)
    return store.overlay.list_images(cid, eid, "default", base=kind)


@router.get("/campaigns/{cid}/{kind}/{eid}/images/{name}")
def get_campaign_entity_image(cid: str, kind: str, eid: str, name: str, request: Request):
    _campaign_root_or_404(cid)
    _image_kind_or_404(kind)
    return _serve_image(store.overlay.image_root(cid, eid, "default", name, base=kind),
                        eid, "default", name, base=kind, request=request)


@router.put("/campaigns/{cid}/{kind}/{eid}/images/{name}")
async def put_campaign_entity_image(cid: str, kind: str, eid: str, name: str, file: UploadFile = File(...)):
    return await _entity_image_put(_campaign_root_or_404(cid), kind, eid, name, file)


@router.delete("/campaigns/{cid}/{kind}/{eid}/images/{name}")
def delete_campaign_entity_image(cid: str, kind: str, eid: str, name: str):
    _campaign_root_or_404(cid)
    _entity_kind_or_404(kind)
    store.overlay.delete_image(cid, eid, "default", name, base=kind)
    return {"ok": True}


@router.post("/campaigns/{cid}/{kind}/{eid}/images/{name}/promote")
def promote_campaign_entity_image(cid: str, kind: str, eid: str, name: str):
    _campaign_root_or_404(cid)
    _entity_kind_or_404(kind)
    try:
        store.overlay.promote_image(cid, eid, "default", name, base=kind)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="image not found")
    except ValueError as exc:
        # an externally-placed file whose extension we never accepted for upload
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True}
