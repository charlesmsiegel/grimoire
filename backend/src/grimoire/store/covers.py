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


class CoverTooLarge(Exception):
    """The upload is bigger than `MAX_BYTES` (HTTP 413)."""


class CoverInvalid(Exception):
    """The upload is not a decodable image, or its raster is absurd (HTTP 400)."""


def validate(data: bytes) -> None:
    """Gate an upload before it is stored. Never converts or downscales."""
    if len(data) > MAX_BYTES:
        raise CoverTooLarge("cover image is too large (max 25 MB)")
    try:
        with Image.open(io.BytesIO(data)) as im:
            width, height = im.size   # from the header; decodes no pixels
            im.verify()               # structural integrity, still no full decode
    except Exception as exc:  # PIL raises a zoo of types for bad bytes
        raise CoverInvalid("not a readable image") from exc
    if width * height > MAX_PIXELS:
        raise CoverInvalid("cover image has too many pixels (max 50 MP)")


def _assets_dir(cid: str) -> Path:
    """``<campaign>/assets``, after proving the campaign is actually there.

    ``campaign_root`` is a syntax guard, not an existence check -- it only
    rejects ids ``safe_id`` refuses. Without this, a put for an unknown id
    would create a campaign directory holding an image and no ``campaign.md``.
    """
    if not campaigns_paths.campaign_exists(cid):
        raise campaigns_paths.CampaignNotFound(cid)
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
    """Store `data` as the cover; returns the stored extension."""
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
