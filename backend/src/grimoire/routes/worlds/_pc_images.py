"""worlds: pc_images responsibilities."""

from __future__ import annotations

from fastapi import Depends, File, Header, HTTPException, Request, UploadFile

from ... import store
from ...llm import LLMClient
from .. import runs
from ..common import (
    _serve_image,
    _upload_image_ext,
    _world_root_or_404,
    draft_completion,
    get_llm,
    image_draft_prompt,
    image_id_field,
    image_promote_guards,
    record_art_mutation,
)
from ..models import AvatarFocus, ImageDescription, ImagePromote
from ._router import router as router


# ---- world PC images (#219) — the same surface world characters have, keyed
# on `pcs.ASSET_BASE` so `<world>/pcs/<pid>/assets/<vid>/` holds the files.
# Deliberately under `/versions/{vid}/` rather than the entity kinds' flat
# `/pcs/{pid}/images`: a PC's art belongs to the version it depicts, exactly as
# a character's does, and the flat shape is already claimed by the generic
# `/worlds/{wid}/{kind}/{eid}/images` routes.
def _world_pc_version_or_404(wid: str, pid: str, vid: str):
    """The world root, once `pid`/`vid` are known to name a real PC version.

    Every image route below goes through this, and that is a deliberate
    departure from the character routes it otherwise mirrors: those hand
    `assets.put_image` whatever id the URL carried, and `put_image` creates the
    directory it writes into. A typo'd PC or version therefore *succeeds* with
    200 and leaves `pcs/<typo>/assets/<vid>/avatar.png` on disk -- a folder no
    listing shows (`list_pcs` needs `pc.md`, `read_pc` needs the version file)
    and nothing ever collects. The character surface has the same hole -- filed
    as #360, since closing it changes the status code of nine live handlers --
    but there is no reason to ship a second copy of it here.
    """
    root = _world_root_or_404(wid)
    try:
        store.pcs.require_version(root, pid, vid)   # two stats, no read
    except store.pcs.PCNotFound as exc:
        raise HTTPException(status_code=404, detail="pc not found") from exc
    except store.pcs.PCVersionNotFound as exc:
        raise HTTPException(status_code=404, detail="version not found") from exc
    return root


@router.get("/worlds/{wid}/pcs/{pid}/versions/{vid}/images")
def list_world_pc_images(wid: str, pid: str, vid: str):
    return store.assets.list_images(_world_pc_version_or_404(wid, pid, vid), pid, vid,
                                    base=store.pcs.ASSET_BASE)


@router.get("/worlds/{wid}/pcs/{pid}/versions/{vid}/images/{name}")
def get_world_pc_image(wid: str, pid: str, vid: str, name: str, request: Request):
    return _serve_image(_world_pc_version_or_404(wid, pid, vid), pid, vid, name,
                        base=store.pcs.ASSET_BASE, request=request)


@router.put("/worlds/{wid}/pcs/{pid}/versions/{vid}/images/{name}")
async def put_world_pc_image(wid: str, pid: str, vid: str, name: str,
                             file: UploadFile = File(...)):
    root = _world_pc_version_or_404(wid, pid, vid)
    data = await file.read()
    ext = _upload_image_ext(data)  # the bytes name the type, not `file.filename` (#321)
    try:
        stored = store.assets.put_image(root, pid, vid, name, data, ext,
                                        base=store.pcs.ASSET_BASE)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"name": name, "ext": stored,
            **image_id_field(store.assets.image_id(root, pid, vid, name,
                                                   base=store.pcs.ASSET_BASE))}


@router.delete("/worlds/{wid}/pcs/{pid}/versions/{vid}/images/{name}")
def delete_world_pc_image(wid: str, pid: str, vid: str, name: str, expected_v: str | None = None):
    # Gated on the PC and version, not on the image: removing an image that is
    # already gone is the caller getting what they asked for, but removing one
    # from a PC that does not exist is a typo worth reporting.
    _world_pc_version_or_404(wid, pid, vid)
    return record_art_mutation("world", wid, "pcs", pid, vid, name,
                               "delete", expected_v=expected_v)


@router.post("/worlds/{wid}/pcs/{pid}/versions/{vid}/images/{name}/promote")
def promote_world_pc_image(wid: str, pid: str, vid: str, name: str, body: ImagePromote | None = None):
    _world_pc_version_or_404(wid, pid, vid)
    return record_art_mutation("world", wid, "pcs", pid, vid, name,
                               "promote", **image_promote_guards(body))


@router.put("/worlds/{wid}/pcs/{pid}/versions/{vid}/images/avatar/focus")
def put_world_pc_avatar_focus(wid: str, pid: str, vid: str, body: AvatarFocus):
    _world_pc_version_or_404(wid, pid, vid)
    return record_art_mutation("world", wid, "pcs", pid, vid, "avatar",
                               "focus", focus=body.focus, expected_v=body.expected_v)


@router.post("/worlds/{wid}/pcs/{pid}/versions/{vid}/images/{name}/description/draft",
             status_code=202)
def post_world_pc_image_description_draft(
        wid: str, pid: str, vid: str, name: str, request: Request,
        client: LLMClient = Depends(get_llm),
        x_grimoire_attempt: str | None = Header(default=None)):
    """Start a model-drafted first pass at what this PC's picture shows."""
    root = _world_pc_version_or_404(wid, pid, vid)
    try:
        subject = store.pcs.read_pc(root, pid)["meta"]["name"]
    except store.pcs.PCNotFound:
        subject = ""
    resolved, messages = image_draft_prompt(
        store.assets.image_path(root, pid, vid, name, base=store.pcs.ASSET_BASE), subject)

    async def work():
        return await draft_completion(
            client, resolved, messages, "image-description",
            lambda text: {"description": store.image_drafts.parse_output(text)})

    return runs.run_draft(request.app, runs.world_subject(wid),
                          "image-description", x_grimoire_attempt, work)


@router.put("/worlds/{wid}/pcs/{pid}/versions/{vid}/images/{name}/description")
def put_world_pc_image_description(wid: str, pid: str, vid: str, name: str,
                                   body: ImageDescription):
    _world_pc_version_or_404(wid, pid, vid)
    return record_art_mutation("world", wid, "pcs", pid, vid, name,
                               "description", description=body.description, expected_v=body.expected_v,
                               expected_description_rev=body.expected_description_rev,
                               cleanup_only=body.cleanup_only)
