"""Campaign HTTP endpoints: images domain."""


from __future__ import annotations

from urllib.parse import quote

from fastapi import (
    Depends,
    File,
    Header,
    HTTPException,
    Request,
    UploadFile,
)

from ... import store
from ...llm import LLMClient
from .. import characters as character_routes
from .. import runs
from ..common import (
    _campaign_root_or_404,
    _serve_image_file,
    _upload_image_ext,
    _with_descriptions,
    computes_only,
    draft_completion,
    get_llm,
    image_draft_prompt,
    image_id_field,
)
from ..models import (
    ImageDescription,
)
from ._router import router


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
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except store.covers.CoverInvalid as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        stored = store.covers.put_cover(cid, data, ext)
    except ValueError as exc:
        # Nothing `validate` returns is an extension `put_in` refuses today, so
        # this is unreachable from here -- kept because what is storable is the
        # store's decision to make, not this route's to assume.
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ext": stored, "v": store.covers.cover_version(cid),
            **image_id_field(store.covers.cover_image_id(cid))}


@router.delete("/campaigns/{cid}/cover")
def delete_campaign_cover(cid: str):
    _campaign_root_or_404(cid)
    try:
        store.covers.delete_cover(cid)
    except OSError as exc:
        # `delete_cover` confirms the removal rather than swallowing a failed
        # unlink, so this is a cover that is genuinely still there -- a held
        # file on Windows, a read-only store. Reporting 200 would be a lie.
        raise HTTPException(status_code=500, detail="cover could not be removed") from exc
    return {"ok": True}


# ---- the campaign's own image library (store/campaign_images.py) ----------
# Declared here for the same reason the cover is: `routes/__init__` includes
# `campaigns` before `entities`, whose `/campaigns/{cid}/{kind}` would otherwise
# capture `images`.
@router.post("/campaigns/{cid}/images/{name}/description/draft", status_code=202)
@computes_only
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

    `@computes_only` for the reason `_draft_description` states about itself --
    the draft is persisted by the PUT on Save, never here -- and its three
    sibling surfaces are world-scoped, so this is the one place the missing
    marker was reachable. Nothing is written at the 202 either, which is the
    same reason `post_absorb` and the voice-anchor generate route carry it. It
    was a mis-ordered recents rail before #409 and is a false `campaign_moved`
    after it: drafting a description in another tab would otherwise refuse a
    clock confirmation over a campaign nothing had written.
    """
    _campaign_root_or_404(cid)
    resolved, messages = image_draft_prompt(
        store.campaign_images.image_path(cid, name), "", cid=cid)

    async def work():
        return await draft_completion(
            client, resolved, messages, "image-description",
            lambda text: {"description": store.image_drafts.parse_output(text)},
            cid=cid)

    return runs.run_draft(request.app, runs.campaign_subject(cid),
                          "image-description", x_grimoire_attempt, work)


@router.put("/campaigns/{cid}/images/{name}/description")
def put_campaign_library_image_description(cid: str, name: str, body: ImageDescription):
    _campaign_root_or_404(cid)
    if any(i["name"] == name and i["inherited"]
           for i in store.campaign_images.list_images(cid)):
        # An inherited image whose world placement resolves is described on
        # the shared image object (D1, R3): the store writes it. One the world
        # holds as a LEGACY file has no object to share, so it is still
        # described in the WORLD's editor -- the split the campaign backlog
        # above already draws. 409 rather than 404: the image is right there,
        # it is just not this scope's to describe.
        wdir = store.campaign_images.world_dir(cid)
        if wdir is None or store.image_descriptions.object_id_in(wdir, name) is None:
            raise HTTPException(status_code=409,
                                detail="describe this image in its world")
    try:
        store.campaign_images.set_description(cid, name, body.description)
    except store.image_descriptions.DescriptionTooLongError as exc:
        # Before the ValueError below, which it subclasses: too long is not
        # "not found" -- the image is there and the text is refused.
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except ValueError:
        # `from None`: the strict-write ValueError is this module's own
        # implementation detail, and chaining it onto the 404 says nothing a
        # caller can act on.
        raise HTTPException(status_code=404, detail="image not found") from None
    return {"ok": True}


@router.get("/campaigns/{cid}/images")
def list_campaign_library(cid: str):
    """Every image this campaign can put in a post, and what it has hidden.

    `read_descriptions` rather than a read of `images_dir`: that directory holds
    only the campaign's OWN uploads now, so resolving descriptions against it
    while listing merged images would report every inherited picture as
    undescribed -- art described once in the world reading as unfinished in
    every campaign that inherits it.

    `hidden` rides along rather than taking a request of its own: it is the
    other half of the same question, and the surface that shows it is the same
    picker.
    """
    _campaign_root_or_404(cid)
    images = store.campaign_images.list_images(cid)
    return {"images": _with_descriptions(
                images, store.campaign_images.read_descriptions(cid)),
            "hidden": store.campaign_images.list_hidden(cid)}


@router.get("/campaigns/{cid}/images/undescribed")
def list_campaign_undescribed_images(cid: str, count: bool = False):
    """This campaign's OWN undescribed art — the queue's campaign half. With
    `?count=1`, only how many (`{"count": n}`), through the same filter as the
    list -- see the world queue's count form.

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
    # One overlay view for every record lookup below, as the listings share one.
    v = store.overlay.view(cid)
    # The world queue's record filter, with this scope's lookup: one read per
    # record, name and versions together -- see `_describable` for why both,
    # and why one memo -- so the two queues cannot drift apart on what counts.
    seen: dict[tuple[str, str], tuple[str, set[str]] | None] = {}

    def describable(base: str, rid: str, vid: str) -> str | None:
        return character_routes._describable(
            seen, base, rid, vid, lambda b, r: _campaign_record_name_and_versions(cid, b, r, v))

    if count:
        return {"count": store.campaign_images.own_undescribed_count(cid) + sum(
            k for base in character_routes.UNDESCRIBED_BASES
            for rid, vid, k in store.image_descriptions.undescribed_by_version(root, base)
            if describable(base, rid, vid) is not None)}
    out: list[dict] = []
    # `own_undescribed`, NOT the merged listing: the library reads through to
    # the world now, and handing the merged names to this queue would re-offer
    # every world image in every campaign's backlog -- the exact failure the
    # docstring above exists to prevent.
    out.extend({"kind": "campaign", "id": "", "vid": "", "name": image["name"],
                "record_name": "Campaign library",
                "url": f"/api/campaigns/{cid}/images/{quote(image['name'], safe='')}",
                **({"conflicts": image["conflicts"]} if "conflicts" in image else {})}
               for image in store.campaign_images.own_undescribed(cid))

    for base in character_routes.UNDESCRIBED_BASES:
        for item in store.image_descriptions.undescribed(root, base):
            name = describable(base, item["id"], item["vid"])
            if name is None:
                continue
            out.append({"kind": base, "id": item["id"], "vid": item["vid"],
                        "name": item["name"], "record_name": name,
                        "url": _campaign_image_url(cid, base, item),
                        **({"conflicts": item["conflicts"]} if "conflicts" in item else {})})
    return out


def _campaign_record_name_and_versions(cid: str, base: str, rid: str,
                                       v: store.overlay.View | None = None,
                                       ) -> tuple[str, set[str]] | None:
    """`_record_name_and_versions` (routes/characters/_gallery_sources.py) through the overlay."""
    try:
        if base == "characters":
            # Off `character.md` and a listing, not the full overlay read --
            # which lists and byte-compares every version's art to answer two
            # questions about the record.
            name, versions = store.overlay.character_name_and_versions(cid, rid, v=v)
            return str(name), set(versions)
        if base == store.pcs.ASSET_BASE:
            d = store.overlay.read_pc(cid, rid, v=v)
            return str(d["meta"]["name"]), {ver["id"] for ver in d["versions"]}
        return str(store.overlay.read_entity(cid, base, rid, v=v)["meta"]["name"]), set()
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
    if not store.image_library.addressable(name):
        raise HTTPException(status_code=400,
                            detail="image name cannot be used in a link")
    # Size BEFORE the read, exactly as `put_campaign_cover` does and for the
    # same reason: `read()` materializes the whole upload as one `bytes` object,
    # and on Android (Chaquopy) that allocation is what OOMs the process before
    # a 413 could be composed. `validate_size` is the belt to those braces --
    # see it for why `file.size` alone is not enough.
    if file.size is not None and file.size > store.image_library.MAX_BYTES:
        raise HTTPException(status_code=413, detail=store.image_library.TOO_LARGE)
    data = await file.read()
    try:
        store.image_library.validate_size(data)
    except store.image_library.ImageTooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    ext = _upload_image_ext(data)  # the bytes name the type, not `file.filename` (#321)
    try:
        stored = store.campaign_images.put_image(cid, name, data, ext)
    except ValueError as exc:
        # 409 for a name the world holds, 400 for a name no link could carry.
        # The distinction is the caller's to act on: one is "pick another name",
        # the other is "that name is not usable at all".
        code = 409 if "belongs to the world" in str(exc) else 400
        raise HTTPException(status_code=code, detail=str(exc)) from exc
    # `v` so the client can build the immutable `?v=` URL without a second round
    # trip. Taken from the placement `image_id` comes from, so the two describe
    # the same upload; only with no placement (a legacy file) does it resolve
    # and stat, answering "" rather than raising if the file went between the
    # two -- a write that landed must not report a 500.
    placed = store.assets.resolve(store.campaign_images.images_dir(cid), name)
    if placed is not None:
        return {"name": name, "ext": stored, "v": placed.blob_sha256,
                "image_id": placed.image_id}
    return {"name": name, "ext": stored,
            "v": store.campaign_images.image_version(cid, name)}


@router.post("/campaigns/{cid}/images/{name}/restore")
def post_restore_campaign_library_image(cid: str, name: str):
    """Un-hide an inherited library image.

    The exit from a tombstone, and the reason the world-side delete sweep can
    afford to be best-effort: a campaign the sweep could not reach keeps a
    stale hidden entry, and this is what clears it. Idempotent -- restoring a
    name that was never hidden is a no-op rather than a 404, because the
    caller's intent ("this should be visible") is already satisfied.
    """
    _campaign_root_or_404(cid)
    store.campaign_images.restore_image(cid, name)
    return {"ok": True}


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
    except OSError as exc:
        # `delete_image` confirms the removal rather than swallowing a failed
        # unlink, so this is a file that is genuinely still there -- held by a
        # sync client on Windows, or a read-only store. 200 would be a lie.
        raise HTTPException(status_code=500, detail="image could not be removed") from exc
    return {"ok": True}


