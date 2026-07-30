"""Campaign-scoped routes: the campaign record, exports, the world->campaign
sync inbox, calendar and climate settings, group state, the campaign's own
copies of characters and PCs, and change review.

Scenes, weather, mechanics and greetings have their own modules; the generic
``/campaigns/{cid}/{kind}`` entity surface lives in ``entities``.
"""

from __future__ import annotations


from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import Response

from .. import store
from .common import (_campaign_root_or_404, _content_fields, _dump, _response_body,
                     _serve_image, _write_response)
from .models import (AvatarFocus, CalendarConfig, CampaignClimate, CopyFromGreeting,
                     DefaultVersion, GroupStateSave, NameBody, NewCampaign, PCCreate,
                     PCUpdate, PersonaVersionCreate, PersonaVersionUpdate, PickBody,
                     RefList, ResponseSettings, VersionCreate, VersionUpdate)

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
        return {**{k: "" for k in store.groupstate.FIELDS}, "updated": ""}
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
        out.append({**c, "scenes": len(scene_list),
                    "last_scene": scene_list[0]["title"] if scene_list else ""})
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
    cfg = {"primary": body.primary, "secondary": body.secondary, "confirmed": body.confirmed}
    try:
        store.calendars.validate_calendar(cfg)
    except store.calendars.CalendarError as e:
        raise HTTPException(status_code=400, detail=str(e))
    store.calendars.write_calendar(store.campaigns.campaign_root(cid), cfg)
    return {"ok": True}


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
        return {"id": store.campaigns.create_campaign(body.name, body.world,
                                                      body.region, body.calendar,
                                                      body.module, body.climate)}
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


@router.delete("/campaigns/{cid}")
def delete_campaign(cid: str):
    try:
        store.campaigns.delete_campaign(cid)
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")
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
def get_changes(cid: str):
    _campaign_root_or_404(cid)
    data = store.changes.read(cid)
    scenes_by_id = {s["id"]: s for s in store.scenes.list_scenes(cid)}
    try:
        chron = store.chronicle.read_chronicle(cid)
    except Exception:  # noqa: BLE001 — garbled chronicle.json: labels degrade, no 500
        chron = {}
    out: list[dict] = []
    for ref, entry in data.items():
        kind, _, eid = ref.partition("/")
        name = _record_name(cid, kind, eid)
        if name is None:
            continue  # record deleted since the change was captured
        sid = entry.get("scene", "")
        s, c = scenes_by_id.get(sid, {}), chron.get(sid, {})
        fields = [{"field": f.get("field", ""), "label": f.get("label", ""),
                   "diff": store.changes.line_diff(f.get("before", ""), f.get("after", ""))}
                  for f in entry.get("fields", [])]
        out.append({"ref": {"kind": kind, "id": eid}, "name": name,
                    "scene": {"id": sid, "title": s.get("title", sid), "date": c.get("date", "")},
                    "fields": fields})
    out.sort(key=lambda r: (r["ref"]["kind"], r["name"]))
    return out


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


@router.get("/campaigns/{cid}/characters/{char}/versions/{vid}/images/{name}")
def get_campaign_image(cid: str, char: str, vid: str, name: str, request: Request):
    _campaign_root_or_404(cid)
    return _serve_image(store.overlay.image_root(cid, char, vid, name), char, vid, name, request=request)


@router.put("/campaigns/{cid}/characters/{char}/versions/{vid}/images/{name}")
async def put_campaign_image(cid: str, char: str, vid: str, name: str, file: UploadFile = File(...)):
    root = _campaign_root_or_404(cid)
    data = await file.read()
    fn = file.filename or ""
    ext = fn.rsplit(".", 1)[-1] if "." in fn else ""
    try:
        stored = store.assets.put_image(root, char, vid, name, data, ext)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"name": name, "ext": stored}


@router.delete("/campaigns/{cid}/characters/{char}/versions/{vid}/images/{name}")
def delete_campaign_image(cid: str, char: str, vid: str, name: str):
    _campaign_root_or_404(cid)
    # tombstone so a still-materialized world image doesn't show back through
    # the overlaid read the moment the campaign's own copy is gone (get_campaign_image).
    store.overlay.delete_image(cid, char, vid, name)
    return {"ok": True}


@router.post("/campaigns/{cid}/characters/{char}/versions/{vid}/images/{name}/promote")
def promote_campaign_image(cid: str, char: str, vid: str, name: str):
    _campaign_root_or_404(cid)
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
    root = _campaign_root_or_404(cid)
    # a thin campaign may only have this avatar through the inherited world
    # character, so the existence gate must check the overlay union, not croot alone
    names = {i["name"] for i in store.overlay.list_images(cid, char, vid)}
    if store.assets.AVATAR not in names:
        raise HTTPException(status_code=404, detail="image not found")
    # the write always lands campaign-side; overlay.read_focus then finds this
    # campaign focus.json and treats the campaign as authoritative going forward
    store.assets.write_focus(root, char, vid, body.focus)
    return {"ok": True}


@router.post("/campaigns/{cid}/characters/{char}/versions/{vid}/images/copy-from-greeting")
def post_copy_campaign_image_from_greeting(cid: str, char: str, vid: str, body: CopyFromGreeting):
    root = _campaign_root_or_404(cid)
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


@router.get("/campaigns/{cid}/characters/{char}")
def get_campaign_character(cid: str, char: str):
    _campaign_root_or_404(cid)
    try:
        return store.overlay.read_character(cid, char)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")


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
        return store.pcs.read_pc(store.overlay.pc_root(cid, pid), pid)
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
