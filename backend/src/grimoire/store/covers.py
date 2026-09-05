"""The campaign's cover image: ``<campaign>/assets/cover.<ext>``.

One image per campaign, used as the EPUB cover and as the campaigns-list
thumbnail. Deliberately *not* a key in ``campaign.md``: that file is
read-modify-written unlocked by ``campaigns.read.touch``, ``rename_campaign``
and ``set_campaign_response`` (see ``OUTSIDE_DOMAIN`` in ``locks.py``), so a
cover recorded there could be dropped by a concurrent rename. The file's
presence on disk is the record.

Not under the overlay either: a cover is campaign-local and is never inherited
from the campaign's world, so there is no world-side copy to shadow and
nothing to tombstone. ``store/overlay.py`` does not know about covers.

The image work itself is ``assets``' directory-level primitives -- extension
allowlist, per-image lock, write-before-cleanup, newest-wins -- with
``supported_only``, because this directory is one a human browses and a sync
client writes into.
"""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image

from . import assets, locks
from .campaigns import paths as campaigns_paths
from .worlds import paths as worlds_paths

NAME = "cover"

#: Ceiling on a stored cover. The backend is packaged verbatim into the
#: Android app (Chaquopy), where one upload exists as the request body, as
#: `bytes`, inside the EPUB's in-memory `BytesIO` and again in its
#: `getvalue()`. Nothing is downscaled below it -- the book embeds the
#: full-resolution image.
MAX_BYTES = 25 * 1024 * 1024

#: Ceiling on the DECODED raster, which the byte cap does not bound: a few
#: hundred KB of PNG can describe a billion pixels, and `store.thumbs` decodes
#: it to serve the 96px list thumbnail. Pillow's own DecompressionBombError is
#: a backstop above its ~89 MP default; this is the policy.
MAX_PIXELS = 50_000_000

#: The 413 text, shared. The route rejects an oversized upload from
#: `UploadFile.size` BEFORE reading it into one `bytes` object (that read is
#: the memory spike `MAX_BYTES` exists to bound), and `validate` re-checks the
#: bytes it actually got, because `size` is Optional in the ASGI contract.
#: Two checks, one answer.
TOO_LARGE = "cover image is too large (max 25 MB)"

#: PIL's format name -> the extension we store the image under. The DETECTED
#: format decides that, never the client's filename: a JPEG uploaded as
#: `cover.png` would otherwise be stored as `cover.png`, served as
#: `image/png`, and packed into the EPUB's `package.opf` with
#: `media-type="image/png"` -- which epubcheck reports as an error, i.e. the
#: exact "produce an invalid book" outcome the decode check exists to prevent.
#: A decodable image in any other format is refused rather than stored under a
#: name that lies about it, and the filename stops mattering at all.
#:
#: Record images now hold the same line, by the same rule and in two places
#: (#321): `routes.common._upload_image_ext` names an uploaded one from its
#: bytes, and `export.Images` names a packed one from its bytes and drops the
#: image entirely when they name no format it can declare -- because stores
#: already on disk hold files misnamed before that and nothing renames them.
#: Those use magic bytes rather than a decode, which is why this stays
#: PIL-based: only the decode bounds `MAX_PIXELS`, and only a cover is
#: thumbnailed. The two map the same four formats to the same four extensions,
#: so an image both accept is named identically by either; where they part is
#: only whether to accept at all (a signature satisfies one, a structural
#: verify the other), which is each caller's own policy.
_FORMAT_EXT = {"PNG": "png", "JPEG": "jpg", "GIF": "gif", "WEBP": "webp"}


class CoverTooLarge(Exception):
    """The upload is bigger than `MAX_BYTES` (HTTP 413)."""


class CoverInvalid(Exception):
    """The upload is not a decodable image, or its raster is absurd (HTTP 400)."""


def validate(data: bytes) -> str:
    """Gate an upload before it is stored; return the extension to store it under.

    Never converts or downscales -- the returned extension names the format the
    bytes are already in (see `_FORMAT_EXT` for why the filename is not asked).
    """
    if len(data) > MAX_BYTES:
        raise CoverTooLarge(TOO_LARGE)
    try:
        with Image.open(io.BytesIO(data)) as im:
            width, height = im.size   # from the header; decodes no pixels
            fmt = im.format or ""     # read BEFORE verify(), which leaves `im` spent
            im.verify()               # structural integrity, still no full decode
    except Exception as exc:  # PIL raises a zoo of types for bad bytes
        raise CoverInvalid("not a readable image") from exc
    if width * height > MAX_PIXELS:
        raise CoverInvalid("cover image has too many pixels (max 50 MP)")
    ext = _FORMAT_EXT.get(fmt.upper())
    if ext is None:
        # Decodable, but in a format we cannot label honestly downstream (a BMP,
        # a TIFF, an ICO). CoverInvalid, not CoverTooLarge: it is a 400.
        raise CoverInvalid(f"unsupported image format: {fmt.lower() or 'unknown'}")
    return ext


def _assets_dir(cid: str) -> Path:
    """``<campaign>/assets``, after proving the campaign is actually there.

    ``campaign_root`` is a syntax guard, not an existence check -- it only
    rejects ids ``safe_id`` refuses. Without this, a put for an unknown id
    would create a campaign directory holding an image and no ``campaign.md``.
    """
    if not campaigns_paths.campaign_exists(cid):
        raise campaigns_paths.CampaignNotFound(cid)
    # overlay-ok: a cover is campaign-local and is never inherited from the
    # campaign's world -- there is no world-side copy to shadow and nothing
    # to tombstone, which is why `store/overlay.py` does not know about covers.
    return campaigns_paths.campaign_root(cid) / "assets"


def cover_path(cid: str) -> Path | None:
    return assets.path_in(_assets_dir(cid), NAME, supported_only=True)


def cover_version(cid: str) -> str:
    """Cache-busting token for the current cover, "" when there is none.

    Swallows `OSError`: this runs once per row in ``GET /campaigns``, and
    ``assets.image_version`` stats unguarded, so a cover deleted between
    resolution and stat must read as "no cover" rather than 500 the listing.
    """
    p = cover_path(cid)
    if p is None:
        return ""
    try:
        return assets.image_version(p)
    except OSError:
        return ""


def put_cover(cid: str, data: bytes, ext: str) -> str:
    """Store `data` as the cover; returns the stored extension.

    `ext` is what `validate` detected in these bytes, not anything a filename
    claimed -- the route passes one straight to the other.
    """
    d = _assets_dir(cid)
    with locks.campaign_lock(cid):
        return assets.put_in(d, NAME, data, ext, supported_only=True)


def delete_cover(cid: str) -> None:
    """Remove the cover, and confirm it.

    ``assets.delete_in`` swallows a failed unlink by design -- a lost cleanup
    self-heals there, because resolution prefers the newest file. Here the
    unlink IS the operation: on Windows a sync client or a scanner can hold the
    file, and a swallowed failure would answer "removed" to a Remove that did
    nothing. Same shape as ``assets.promote_image``'s "promoted image could not
    be cleared".
    """
    d = _assets_dir(cid)
    with locks.campaign_lock(cid):
        assets.delete_in(d, NAME, supported_only=True)
        if assets.path_in(d, NAME, supported_only=True) is not None:
            raise OSError("cover could not be removed")


# ---- the world's cover -----------------------------------------------------
#
# Same image, one directory up the ownership chain: ``<world>/assets/cover.<ext>``,
# drawn on the worlds shelf and carried in a world bundle. It shares `validate`
# and every constant with the campaign's, because a cover is a cover -- what
# differs is only whose it is, and therefore what lock protects it.
#
# **These take no lock**, and that is not an oversight this closes: worlds have
# no lock domain at all, and `focus.json`, `subjects.json` and the world-side
# description write all race there in exactly the same way (see
# `overlay.set_description`'s docstring, which names the asymmetry). Inventing
# a half-lock for one directory would imply a guarantee the world root does not
# make.
#
# A `wid`-taking function is not a campaign mutator by `_takes_cid`'s
# convention, so these do not disturb this module's `DOMAIN_MODULES` standing --
# and each keeps its own `assets` write call, which is what keeps the campaign
# faces visible to `test_lock_domain_guard`'s survey.


def _world_assets_dir(wid: str) -> Path:
    """``<world>/assets``, after proving the world is actually there.

    ``world_root`` is a syntax guard, not an existence check -- it only rejects
    ids ``safe_id`` refuses. Without this, a put for an unknown id would create
    a world directory holding an image and no ``world.md``: bytes no listing can
    show and no delete route can name, reported to the caller as a successful
    upload (#360, #373). The campaign face makes the same check for the same
    reason.
    """
    if not worlds_paths.world_exists(wid):
        raise worlds_paths.WorldNotFound(wid)
    return worlds_paths.world_root(wid) / "assets"


def world_cover_path(wid: str) -> Path | None:
    return assets.path_in(_world_assets_dir(wid), NAME, supported_only=True)


def world_cover_version(wid: str) -> str:
    """Cache-busting token for the current world cover, "" when there is none.

    Swallows `OSError` for `cover_version`'s reason: this runs once per row in
    ``GET /worlds``, and a cover deleted between resolution and stat must read
    as "no cover" rather than 500 the listing.
    """
    # `WorldNotFound` as well as `OSError`: this runs once per row in
    # ``GET /worlds``, and `_world_assets_dir` raises for a world that went
    # between `list_worlds` and this stat -- which would take down the whole
    # shelf for one row that is no longer there.
    try:
        p = world_cover_path(wid)
        if p is None:
            return ""
        return assets.image_version(p)
    except (OSError, worlds_paths.WorldNotFound):
        return ""


def put_world_cover(wid: str, data: bytes, ext: str) -> str:
    """Store `data` as the world's cover; returns the stored extension.

    `ext` is what `validate` detected in these bytes, not anything a filename
    claimed -- the route passes one straight to the other.
    """
    return assets.put_in(_world_assets_dir(wid), NAME, data, ext, supported_only=True)


def delete_world_cover(wid: str) -> None:
    """Remove the world's cover, and confirm it.

    ``assets.delete_in`` swallows a failed unlink by design -- a lost cleanup
    self-heals there, because resolution prefers the newest file. Here the
    unlink IS the operation: on Windows a sync client or a scanner can hold the
    file, and a swallowed failure would answer "removed" to a Remove that did
    nothing. Same shape as ``delete_cover``.
    """
    d = _world_assets_dir(wid)
    assets.delete_in(d, NAME, supported_only=True)
    if assets.path_in(d, NAME, supported_only=True) is not None:
        raise OSError("cover could not be removed")
