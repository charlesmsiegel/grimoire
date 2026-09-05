"""The world's cover and image library (`store/world_images.py`, `store/covers.py`).

Its own module, and **included after `characters`** in `routes/__init__.py`, for
a reason that is a live shadowing bug rather than taste:
``/worlds/{wid}/images/{name}`` generalizes ``/worlds/{wid}/images/undescribed``,
which `routes/characters.py` owns. Registered any earlier, the `{name}` route
matches first and the describe backlog becomes a 404 for an image nobody has --
the break `campaign_images.RESERVED` records on the campaign side, where it cost
a broken picker tile and a post that rendered as broken markdown.
``test_no_route_is_shadowed_by_an_earlier_one`` is what holds it.

`entities.router` is still included last, so its ``/worlds/{wid}/{kind}``
catch-all cannot claim ``/worlds/{wid}/images`` or ``/worlds/{wid}/cover``.

The 404s are hand-rolled. There is no world equivalent of
`_campaign_root_or_404` and no global handler -- every world route catches
`WorldNotFound` itself -- so mirroring the campaign block verbatim would answer
500 for an unknown world.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Header, HTTPException, Request, UploadFile

from .. import store
from ..llm import LLMClient
from . import runs
from .common import (
    _serve_image_file,
    _upload_image_ext,
    _with_descriptions,
    draft_completion,
    get_llm,
    image_draft_prompt,
)
from .models import ImageDescription

router = APIRouter()


def _world_or_404(wid: str) -> str:
    """Prove the world is there, or 404. Returns the id for call-site brevity."""
    if not store.worlds.world_exists(wid):
        raise HTTPException(status_code=404, detail="world not found")
    return wid


# ---- the world's cover (store/covers.py) -----------------------------------

@router.get("/worlds/{wid}/cover")
def get_world_cover(wid: str, request: Request):
    _world_or_404(wid)
    p = store.covers.world_cover_path(wid)
    if p is None:
        raise HTTPException(status_code=404, detail="cover not found")
    return _serve_image_file(p, request)


@router.put("/worlds/{wid}/cover")
async def put_world_cover(wid: str, file: UploadFile = File(...)):
    _world_or_404(wid)
    try:
        # Size BEFORE the read. `read()` materializes the whole upload as a
        # single `bytes` object, and that allocation -- not the receipt -- is
        # what `MAX_BYTES` exists to bound: the backend is packaged verbatim
        # into the Android app (Chaquopy), where a 300 MB image would OOM the
        # process before a 413 could be composed. `covers.validate` re-checks
        # `len(data)`, and must: `size` is Optional in the ASGI contract.
        if file.size is not None and file.size > store.covers.MAX_BYTES:
            raise store.covers.CoverTooLarge(store.covers.TOO_LARGE)
        data = await file.read()
        # `validate` also names the extension to store under, from the format it
        # decoded -- `file.filename` is not consulted at all, so a JPEG uploaded
        # as `cover.png` cannot be served (or manifested) as PNG (#321).
        ext = store.covers.validate(data)
    except store.covers.CoverTooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc))
    except store.covers.CoverInvalid as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    try:
        stored = store.covers.put_world_cover(wid, data, ext)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ext": stored, "v": store.covers.world_cover_version(wid)}


@router.delete("/worlds/{wid}/cover")
def delete_world_cover(wid: str):
    _world_or_404(wid)
    try:
        store.covers.delete_world_cover(wid)
    except OSError:
        # `delete_world_cover` confirms the removal rather than swallowing a
        # failed unlink, so this is a cover that is genuinely still there -- a
        # held file on Windows, a read-only store. 200 would be a lie.
        raise HTTPException(status_code=500, detail="cover could not be removed")
    return {"ok": True}


# ---- the world's image library (store/world_images.py) ---------------------

@router.get("/worlds/{wid}/images")
def list_world_library(wid: str):
    _world_or_404(wid)
    images = store.world_images.list_images(wid)
    return _with_descriptions(images, store.world_images.read_descriptions(wid))


@router.get("/worlds/{wid}/images/{name}")
def get_world_library_image(wid: str, name: str, request: Request):
    _world_or_404(wid)
    p = store.world_images.image_path(wid, name)
    if p is None:
        raise HTTPException(status_code=404, detail="image not found")
    return _serve_image_file(p, request)


@router.put("/worlds/{wid}/images/{name}")
async def put_world_library_image(wid: str, name: str, file: UploadFile = File(...)):
    _world_or_404(wid)
    # The name, BEFORE a byte is written and before the body is read (#373).
    # `assets.put_in` creates the directory it writes into, so a name that got
    # past this would file bytes under a token the picker can never insert and
    # this app can never show -- reported to the caller as a successful upload.
    if not store.image_library.addressable(name):
        raise HTTPException(status_code=400,
                            detail="image name cannot be used in a link")
    if file.size is not None and file.size > store.image_library.MAX_BYTES:
        raise HTTPException(status_code=413, detail=store.image_library.TOO_LARGE)
    data = await file.read()
    try:
        store.image_library.validate_size(data)
    except store.image_library.ImageTooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc))
    ext = _upload_image_ext(data)  # the bytes name the type, not `file.filename` (#321)
    try:
        stored = store.world_images.put_image(wid, name, data, ext)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    # `v` so the client can build the immutable `?v=` URL without a second round
    # trip. It resolves and stats, and answers "" rather than raising if the
    # file went between the two -- a write that landed must not report a 500.
    return {"name": name, "ext": stored,
            "v": store.world_images.image_version(wid, name)}


@router.delete("/worlds/{wid}/images/{name}")
def delete_world_library_image(wid: str, name: str):
    _world_or_404(wid)
    # Deliberately NOT gated by `addressable`, unlike the put. That gate exists
    # to stop unreachable bytes being *created*; a file already on disk under a
    # name the picker will not offer -- one a sync client dropped -- is exactly
    # the stray this store can hold, and refusing to remove it would leave it
    # with no way out of the app at all. `assets.delete_in` still applies its
    # own name rules, and the tombstone ref this builds is only ever compared,
    # never resolved.
    try:
        store.world_images.delete_image(wid, name)
    except OSError:
        raise HTTPException(status_code=500, detail="image could not be removed")
    return {"ok": True}


@router.put("/worlds/{wid}/images/{name}/description")
def put_world_library_image_description(wid: str, name: str, body: ImageDescription):
    _world_or_404(wid)
    try:
        store.world_images.set_description(wid, name, body.description)
    except ValueError:
        # `from None`: the strict-write ValueError is the store's own
        # implementation detail, and chaining it onto the 404 says nothing a
        # caller can act on.
        raise HTTPException(status_code=404, detail="image not found") from None
    return {"ok": True}


@router.post("/worlds/{wid}/images/{name}/description/draft", status_code=202)
def post_world_library_description_draft(
        wid: str, name: str, request: Request,
        client: LLMClient = Depends(get_llm),
        x_grimoire_attempt: str | None = Header(default=None)):
    """Start a model-drafted first pass at what a library picture shows.

    No subject name: this art belongs to the world and to no record, which is
    the whole reason the library exists. The template simply asks what is in the
    picture.

    No `@computes_only`, matching the three world description-draft routes that
    already exist. That marker is resolved from a `cid` path parameter and marks
    "a campaign-scoped POST" -- on a world route it would be inert, and an inert
    decorator reads as a claim that something was considered.
    """
    _world_or_404(wid)
    p = store.world_images.image_path(wid, name)
    if p is None:
        raise HTTPException(status_code=404, detail="image not found")
    conn, messages = image_draft_prompt(p, "")

    async def work():
        return await draft_completion(
            client, conn, messages, "image-description",
            lambda text: {"description": store.image_drafts.parse_output(text)})

    return runs.run_draft(request.app, runs.world_subject(wid),
                          "image-description", x_grimoire_attempt, work)
