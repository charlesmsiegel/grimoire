"""common: images responsibilities."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import quote

from fastapi import HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel

from ... import store

# ---- image serving ----
_IMAGE_MEDIA = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                "gif": "image/gif", "webp": "image/webp"}


def _with_descriptions(images: list[dict], descriptions: dict[str, str]) -> list[dict]:
    """One image listing, each entry carrying what it depicts.

    The descriptions ride with the listing the editors already fetch rather
    than through a second round trip -- the same choice the per-version payloads
    make for `avatar_focus`.

    `described` is separate from `description` and is not redundant: absent and
    `""` are different states in the sidecar (never reviewed vs reviewed and
    deliberately undescribed), and collapsing both to an empty string here would
    throw that away at the one boundary where the UI still needs it.
    """
    return [{**i,
             "description": descriptions.get(i["name"], ""),
             "described": i["name"] in descriptions}
            for i in images]


def image_id_field(image_id: str | None) -> dict:
    """``{"image_id": ...}`` for a picture the image store holds, else nothing.

    The one shape every image answer carries it in -- listings and uploads
    alike: absent (the key, never null) for a picture with no placement, so
    the client reads "no identity" as one thing."""
    return {"image_id": image_id} if image_id else {}


def _upload_image_ext(data: bytes) -> str:
    """The extension an uploaded record image is stored under, from its bytes (#321).

    The client's filename is not asked. Every consumer names a media type from
    the stored suffix -- the EPUB manifest, an export's data URIs, the
    Content-Type `_serve_image_file` sets -- so a JPEG uploaded as `avatar.png`
    used to be stored as `.png` and declared `image/png` by all three, which
    epubcheck reports as an error and some readers refuse to render. The
    extension allowlist never closed this: it was only ever checked against the
    filename, which is the thing that lied. `store.covers` settled it for
    campaign covers; this is the same rule for character avatars and galleries,
    greeting art, and location and lore images.

    Magic bytes rather than the PIL decode `covers.validate` runs (it also has
    to bound the raster it is about to thumbnail, which a signature cannot):
    the export names every packed image with the very same detector, and using
    one at both ends is what makes a stored image's suffix the suffix the packer
    derives, rather than two rules that merely happen to agree today. The two
    ends differ only in what they do with an answer of "no format I can name" --
    this refuses the upload, `export.Images` drops the image from the book.

    Bytes in no format we can label are refused rather than stored under a name
    that lies about them: an AVIF uploaded as `avatar.png` renders in a browser
    today only because browsers sniff, and it has never been packable.
    """
    ext = store.fetch.sniff_ext(data)
    if ext is None:
        raise HTTPException(status_code=400, detail="unsupported image type")
    return ext


#: The width a gallery tile requests through `?w=`, which `store.thumbs` honours
#: with an on-the-fly downscale (WebP, or JPEG/PNG on a Pillow that cannot
#: write WebP). Tiles render at 96-154px and 320 covers retina. Here rather
#: than in each listing route because two routes now build thumbnail URLs --
#: the greeting tagger's and the world gallery's -- and a width that drifts
#: between them is two cache keys for one picture.
THUMB_W = 320

#: The widths `?w=` is actually served at. A request snaps UP to the smallest
#: bucket that holds it (a tile asking for 154 gets 256, never 128 -- snapping
#: down would hand a slot fewer pixels than it asked for), and past the last
#: one it is capped there. A handful rather than any integer because every
#: distinct width is a cache entry and a cold Pillow resize per picture: a
#: layout asking for its own exact pixel count multiplies both by the number
#: of layouts that draw it. The client asks for these exactly
#: (`frontend/src/api/thumbs.ts`), which is what keeps the width a `srcset`
#: candidate is described by true of the bytes that come back. THUMB_W stays
#: a bucket so the gallery thumbnails built with it keep their own entry.
THUMB_BUCKETS = (128, 256, THUMB_W, 512, 1024)


def thumb_query(v: str) -> str:
    """The query of a thumbnail URL this side builds: the gallery width, the
    thumbnail pipeline's revision and the source's version token.

    The revision (`t`) is read by no route. It is there for the browser: a `v`
    URL is cached immutable, so a thumbnail a browser holds from before a
    change to how thumbnails are made would otherwise go on being drawn for a
    year -- a phone photo on its side, say, after the fix that stood it up.
    The client builds its own `?w=` URLs with the same number
    (`frontend/src/api/thumbs.ts`)."""
    return f"?w={THUMB_W}&t={store.thumbs.REVISION}&v={v}"


def _thumb_width(w: int) -> int:
    """The bucket a requested `?w=` is served at: the smallest that is at
    least `w`, or the largest when `w` is past them all."""
    return next((b for b in THUMB_BUCKETS if b >= w), THUMB_BUCKETS[-1])


def _serve_image(root, cid: str, vid: str, name: str, base: str = "characters",
                 request: Request | None = None):
    p = store.assets.image_path(root, cid, vid, name, base)
    if p is None:
        raise HTTPException(status_code=404, detail="image not found")
    return _serve_image_file(p, request)


#: A `?v=` token that names a content-addressed blob (its byte sha).
_BLOB_TOKEN = re.compile(r"[0-9a-f]{64}\Z")


def _image_source(p: Path) -> str:
    """What `_serve_image_file`'s validators name `p` by.

    A content-addressed blob is named by its byte sha: the bytes are the
    identity, so the validator is the sha and no stat is paid (a missing blob
    is still a 404, from the read or the thumbnail path). A legacy file has
    only its stat to say what it holds."""
    source = store.image_store.blob_sha_of(p)
    if source is not None:
        return source
    try:
        st = p.stat()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="image not found") from exc
    return f"{st.st_mtime_ns:x}-{st.st_size:x}"


def _require_intact(p: Path) -> None:
    """404 for a blob whose bytes no longer match its name, before its own
    bytes are answered for -- by a 304 or a read.

    The sha is the ETag and the `?v=` token a browser keeps for good, so
    damaged bytes served under it would be cached past the repair. Asked only
    where the ORIGINAL is served: a thumbnail already made under the sha was
    made from intact bytes (`thumbs.thumbnail` asks before it makes one), so
    it is served without hashing. Every lookup that is not serving -- a
    listing, an existence check, a promote -- sees the placement as it is."""
    if store.image_store.blob_sha_of(p) is not None and not store.image_store.blob_intact(p):
        raise HTTPException(status_code=404, detail="image not found")


def _honours_version(request: Request | None, source: str) -> bool:
    """Whether this request's `?v=` makes the answer immutable.

    A `?v=` that names a blob is honoured by that blob alone: anything else
    answering it (a slot that has moved on to other bytes since the URL was
    built) would be kept for a year under a name for other bytes."""
    v = request.query_params.get("v") if request is not None else None
    return v is not None and not (_BLOB_TOKEN.match(v) and v != source)


def attachment_disposition(label: str, ext: str) -> str:
    """A readable slot filename, safe in a header and on desktop filesystems.

    Keep Unicode in RFC 5987's encoded parameter and supply an ASCII fallback.
    The label is a serving route's slot, never a filesystem path or blob id.
    Control characters, separators, quotes, and Windows reserved names cannot
    escape the header or become an unusable suggested filename.
    """
    stem = re.sub(r'[\x00-\x1f\x7f<>:"/\\|?*]', "_", label).strip(" .")[:100] or "image"
    ext = re.sub(r"[^a-zA-Z0-9]", "", ext)[:12] or "bin"
    reserved = {"con", "prn", "aux", "nul", *(f"com{n}" for n in range(1, 10)),
                *(f"lpt{n}" for n in range(1, 10))}
    if stem.split(".", 1)[0].casefold() in reserved:
        stem = "image_" + stem
    filename = f"{stem}.{ext}"
    fallback = stem.encode("ascii", "ignore").decode().strip(" .") or "image"
    return f'attachment; filename="{fallback}.{ext}"; filename*=UTF-8\'\'{quote(filename, safe="")}'


def _serve_attachment(p: Path, request: Request, source: str, cache: str) -> Response:
    # Attachment and inline requests are different representations, including
    # their validators. A thumbnail query never changes the downloaded bytes.
    _require_intact(p)
    etag = f'"{source}-download"'
    try:
        content = p.read_bytes()
    except FileNotFoundError as exc:
        raise HTTPException(404, detail="image not found") from exc
    ext = store.fetch.sniff_ext(content) or p.suffix.lstrip(".").lower()
    label = request.url.path.rsplit("/", 1)[-1]
    headers = {"Cache-Control": cache, "ETag": etag,
               "Content-Disposition": attachment_disposition(label, ext)}
    if etag in request.headers.get("if-none-match", ""):
        return Response(status_code=304, headers=headers)
    return Response(content=content, media_type=_IMAGE_MEDIA.get(ext, "application/octet-stream"),
                    headers=headers)


def _serve_image_file(p: Path, request: Request | None = None) -> Response:
    """Serve one image file with the app's caching contract.

    Bare URLs are no-cache: promotions swap file contents under stable URLs,
    so the browser must revalidate — with an ETag that's a 304, not a
    re-download. A `?v=` URL (built from list responses' version tokens) names
    one exact content state, so it caches immutable: zero requests on later
    renders.

    A `?w=` thumbnail's ETag names what made it as well as its source: the
    bucket and the thumbnail generation. With the source's alone, a thumbnail
    made the old way revalidated as current for as long as the source sat
    unchanged, so no fix to how thumbnails are made ever reached a browser
    that had one. The original served in a thumbnail's place (an animated
    picture, one that would not decode, a cache write that failed) is never
    cached immutable, and carries a tag of its own: it may be a stand-in for
    a failure that has since cleared, and a year of a multi-MB original in a
    128px slot is what `immutable` would have made of it.

    A `FileNotFoundError` reading the file is a 404, not a 500: an image can be
    replaced or removed between the caller resolving its path and this reading
    it, and that is a missing image rather than a server fault. That applies to
    every image route, not only covers — a deliberate widening, since a 500 was
    never the right answer for a file that went away mid-request.

    Only that one, though. Catching `OSError` whole would swallow a
    `PermissionError`, a Windows sharing violation, an exhausted file-descriptor
    table or a disk read error — cases where the image is still there — and
    report a real operational fault to the user as missing data, with the
    frontend dutifully marking a valid cover broken. Those surface as a 500,
    which is what they are.
    """
    source = _image_source(p)
    etag = f'"{source}"'
    versioned = _honours_version(request, source)
    cache = "public, max-age=31536000, immutable" if versioned else "no-cache"
    if request is not None and request.query_params.get("download") == "1":
        return _serve_attachment(p, request, source, cache)
    asked = request.headers.get("if-none-match", "") if request is not None else ""
    # ?w= asks for a downscaled variant — tiles shouldn't pull multi-MB originals.
    if request is not None and (w := request.query_params.get("w", "")).isdigit():
        bucket = _thumb_width(int(w))
        tag = f'"{source}-{bucket}-{store.thumbs.generation()}"'
        headers = {"Cache-Control": cache, "ETag": tag}
        if tag in asked:
            return Response(status_code=304, headers=headers)
        tp = store.thumbs.thumbnail(p, bucket)
        if tp is not None:
            try:
                thumb = tp.read_bytes()
            except OSError:
                thumb = None  # cache entry swept between generation and read
            if thumb is not None:
                return Response(content=thumb, media_type=_IMAGE_MEDIA[tp.suffix[1:]],
                                headers=headers)
        # No thumbnail: the original stands in, revalidated every time.
        cache, etag = "no-cache", f'"{source}-full"'
    _require_intact(p)
    headers = {"Cache-Control": cache, "ETag": etag}
    if etag in asked:
        return Response(status_code=304, headers=headers)
    try:
        content = p.read_bytes()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="image not found") from exc
    # The bytes name the type; the stored suffix answers only for bytes that
    # sniff as nothing (#321). A new upload can no longer be misnamed, but a
    # store already on disk holds files that are, and serving one as
    # `image/png` works only because browsers sniff too.
    #
    # Serving keeps that fallback where the export drops the image instead: a
    # response has to carry some type, the app has to render what the user put
    # in it, and a browser sniffs past a wrong one. A book gets neither -- an
    # EPUB reader validates the manifest and may refuse the image outright.
    ext = store.fetch.sniff_ext(content) or p.suffix.lstrip(".").lower()
    return Response(content=content,
                    media_type=_IMAGE_MEDIA.get(ext, "application/octet-stream"),
                    headers=headers)


# ---- 404 guards and other lookups shared by worlds and campaigns ----
def field_provided(model: BaseModel, name: str) -> bool:
    """Plain-model field presence on both pydantic versions: null is a real
    primary expectation, while omission keeps old callers' unguarded intent."""
    fields = getattr(model, "model_fields_set", None)
    if fields is None:
        fields = getattr(model, "__fields_set__", set())
    return name in (fields or set())


def image_promote_guards(body: BaseModel | None) -> dict:
    if body is None:
        return {}
    return {"expected_v": getattr(body, "expected_v", None),
            "expected_primary_v": getattr(body, "expected_primary_v", None),
            "check_primary": field_provided(body, "expected_primary_v")}


def _description_failure(exc: Exception) -> HTTPException:
    if isinstance(exc, store.image_descriptions.DescriptionConflictError):
        return HTTPException(409, detail={"kind": "stale_description", "detail": str(exc),
                                         **exc.state})
    if isinstance(exc, store.image_descriptions.DescriptionCleanupError):
        return HTTPException(500, detail={"kind": "description_cleanup_incomplete",
                                         "detail": str(exc), "committed": True,
                                         "confirmed_text": exc.text, "recovery": exc.recovery})
    if isinstance(exc, store.image_descriptions.DescriptionUnavailableError):
        return HTTPException(503, detail={"kind": "description_unavailable", "detail": str(exc)})
    return HTTPException(400, detail=str(exc))


def record_art_mutation(scope: str, sid: str, kind: str, rid: str, version: str | None,
                        name: str, operation: str, **guards) -> dict:
    # Keep old callers' missing-image deletes idempotent, distinguish placed
    # but unarrived pictures from invalid formats, and refuse a too-long
    # caption before its ValueError superclass can disguise it as a 404.
    writer = store.record_art.guarded_world if scope == "world" else store.record_art.guarded_campaign
    try:
        return writer(sid, kind, rid, version, name, operation, **guards)
    except store.record_art.ArtConflictError as exc:
        raise HTTPException(409, detail={"kind": exc.kind, "detail": str(exc)}) from exc
    except (store.image_descriptions.DescriptionConflictError,
            store.image_descriptions.DescriptionCleanupError,
            store.image_descriptions.DescriptionGuardRequiredError,
            store.image_descriptions.DescriptionUnavailableError) as exc:
        raise _description_failure(exc) from exc
    except store.record_art.ArtTargetNotFoundError as exc:
        raise HTTPException(404, detail=str(exc)) from exc
    except store.image_descriptions.DescriptionTooLongError as exc:
        raise HTTPException(422, detail=str(exc)) from exc
    except store.assets.ImageNotYetAvailableError as exc:
        raise HTTPException(404, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(404, detail="image not found") from exc
    except ValueError as exc:
        status = 404 if operation == "description" else 400
        raise HTTPException(status, detail="image not found" if status == 404 else str(exc)) from exc
