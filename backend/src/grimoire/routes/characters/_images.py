"""characters: images responsibilities."""

from __future__ import annotations

from fastapi import Depends, File, Header, HTTPException, Request, UploadFile

from ... import store
from ...llm import LLMClient
from .. import runs
from ..common import (
    _serve_image,
    _upload_image_ext,
    _world_char_version_or_404,
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


@router.get("/worlds/{wid}/characters/{cid}/versions/{vid}/images")
def list_world_images(wid: str, cid: str, vid: str):
    return store.assets.list_images(_world_root_or_404(wid), cid, vid)


@router.get("/worlds/{wid}/characters/{cid}/versions/{vid}/images/{name}")
def get_world_image(wid: str, cid: str, vid: str, name: str, request: Request):
    return _serve_image(_world_root_or_404(wid), cid, vid, name, request=request)


@router.put("/worlds/{wid}/characters/{cid}/versions/{vid}/images/{name}")
async def put_world_image(wid: str, cid: str, vid: str, name: str, file: UploadFile = File(...)):
    root = _world_char_version_or_404(wid, cid, vid)
    data = await file.read()
    ext = _upload_image_ext(data)  # the bytes name the type, not `file.filename` (#321)
    try:
        stored = store.assets.put_image(root, cid, vid, name, data, ext)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"name": name, "ext": stored,
            **image_id_field(store.assets.image_id(root, cid, vid, name))}


@router.delete("/worlds/{wid}/characters/{cid}/versions/{vid}/images/{name}")
def delete_world_image(wid: str, cid: str, vid: str, name: str, expected_v: str | None = None):
    # Gated on the character and the version, not on the image: removing an
    # image that is already gone is the caller getting what they asked for, but
    # removing one from a character that does not exist is a typo worth reporting.
    _world_char_version_or_404(wid, cid, vid)
    return record_art_mutation("world", wid, "characters", cid, vid, name,
                               "delete", expected_v=expected_v)


@router.post("/worlds/{wid}/characters/{cid}/versions/{vid}/images/{name}/promote")
def promote_world_image(wid: str, cid: str, vid: str, name: str, body: ImagePromote | None = None):
    _world_char_version_or_404(wid, cid, vid)
    return record_art_mutation("world", wid, "characters", cid, vid, name,
                               "promote", **image_promote_guards(body))


@router.put("/worlds/{wid}/characters/{cid}/versions/{vid}/images/avatar/focus")
def put_world_avatar_focus(wid: str, cid: str, vid: str, body: AvatarFocus):
    _world_char_version_or_404(wid, cid, vid)
    return record_art_mutation("world", wid, "characters", cid, vid, "avatar",
                               "focus", focus=body.focus, expected_v=body.expected_v)


@router.post("/worlds/{wid}/characters/{cid}/versions/{vid}/images/{name}/description/draft",
             status_code=202)
def post_world_image_description_draft(
        wid: str, cid: str, vid: str, name: str, request: Request,
        client: LLMClient = Depends(get_llm),
        x_grimoire_attempt: str | None = Header(default=None)):
    """Start a model-drafted first pass at what this character's picture shows.

    World-side only, and that is not an oversight -- it is true of all four
    surfaces. A description drafted from the bytes is a claim about the bytes,
    and a campaign reaches most of its art through its world, so the draft
    belongs where the art does. A campaign that has diverged an image can still
    describe it by hand; what it cannot do is spend a model call to caption a
    picture its world already captioned.
    """
    root = _world_char_version_or_404(wid, cid, vid)
    try:
        subject = store.characters.read_character(root, cid)["meta"]["name"]
    except store.characters.CharacterNotFound:
        subject = ""
    resolved, messages = image_draft_prompt(
        store.assets.image_path(root, cid, vid, name), subject)

    async def work():
        return await draft_completion(
            client, resolved, messages, "image-description",
            lambda text: {"description": store.image_drafts.parse_output(text)})

    return runs.run_draft(request.app, runs.world_subject(wid),
                          "image-description", x_grimoire_attempt, work)


@router.put("/worlds/{wid}/characters/{cid}/versions/{vid}/images/{name}/description")
def put_world_image_description(wid: str, cid: str, vid: str, name: str,
                                body: ImageDescription):
    _world_char_version_or_404(wid, cid, vid)
    return record_art_mutation("world", wid, "characters", cid, vid, name,
                               "description", description=body.description, expected_v=body.expected_v,
                               expected_description_rev=body.expected_description_rev,
                               cleanup_only=body.cleanup_only)
