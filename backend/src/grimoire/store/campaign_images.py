"""The campaign's own image library: ``<campaign>/assets/images/<name>.<ext>``.

Art that belongs to the campaign and to none of its records. A campaign could
already store an image that belongs to a character, a PC, an entity or a
greeting; a map of the coastline, a photograph of the party's handout, a
picture of the room the narrator is describing belongs to none of them, and
until this module there was nowhere to put it (#376).

``store.covers`` is the precedent, and most of its reasoning carries over
verbatim:

- **Not a key in ``campaign.md``.** That file is read-modify-written *unlocked*
  by ``campaigns.read.touch``, ``rename_campaign`` and ``set_campaign_response``
  (see ``OUTSIDE_DOMAIN`` in ``locks.py``), so a name recorded there could be
  dropped by a concurrent rename. The files' presence on disk is the record.
- **Not under the overlay.** These images are campaign-local and are never
  inherited from the campaign's world, so there is no world-side copy to shadow
  and nothing to tombstone. ``store/overlay.py`` does not know about them.
- **``assets``' directory-level primitives**, with ``supported_only``, because
  this is a directory a human browses and a sync client writes into: a
  ``notes.txt`` left beside ``coastline.png`` must neither win resolution nor be
  deleted by a replace or a remove -- it is not ours.

What differs from a cover is only cardinality: a campaign has one cover and any
number of these, so the directory gets a level of its own rather than a fixed
stem, and enumeration goes through ``assets.list_in`` -- the same newest-wins,
one-entry-per-logical-image rule the per-version folders enumerate by.

Nothing here hangs off a *scene*, which is what keeps this out of
``store/scene_refs.py``: fork, undo and retcon renumber scene ids, and an image
addressed by scene id would be orphaned by every one of them.

Two limits worth stating rather than discovering:

- **A fork's posts still name the campaign they were written in.** The serving
  URL carries a campaign id, and ``store.fork`` copies a campaign's text
  verbatim, so a branch's transcript points at the source's library -- the same
  thing that has always been true of the campaign-scoped *record* image URLs a
  post can carry. Exports are unaffected, because ``export._resolve_image``
  resolves every localized URL against the campaign being exported rather than
  against the id written in it; only the app's own ``<img>`` follows the id, and
  only a deleted source breaks it.
- **A name this module will not offer is not the same as a name it will not
  serve.** ``image_path`` resolves anything ``assets`` considers a safe name, so
  a file a sync client dropped is still served if something asks for it by name
  exactly. What ``list_images`` and ``put_image`` agree on is narrower -- see
  ``addressable``.
"""

from __future__ import annotations

from pathlib import Path

from . import assets, locks
from .campaigns import paths as campaigns_paths

DIRNAME = "images"

#: Ceiling on one stored image, and the same number and the same reason as
#: ``covers.MAX_BYTES``: the backend is packaged verbatim into the Android app
#: (Chaquopy), where an upload exists as the request body, again as one `bytes`
#: object, and again inside the EPUB the export builds in memory. The route
#: refuses an oversized upload from ``UploadFile.size`` before that `bytes`
#: allocation happens.
MAX_BYTES = 25 * 1024 * 1024

#: The 413 text, shared with the route that also re-checks the bytes it got
#: (``size`` is Optional in the ASGI contract, so a client that omits it must
#: not buy an unbounded read).
TOO_LARGE = "image is too large (max 25 MB)"

#: Characters an addressable name may not contain. Every one of them breaks the
#: single thing this library exists for -- putting the image into a post as
#: ``![alt](url)`` -- rather than breaking the filesystem, which ``assets``
#: already guards:
#:
#:   - whitespace ends a markdown link destination, and ends the URL the export's
#:     own scanner (``export._IMG_URL``) matches;
#:   - ``(`` and ``)`` nest and close that destination;
#:   - ``<`` and ``>`` are its alternate syntax;
#:   - ``#`` and ``?`` truncate the path in a URL;
#:   - ``%`` would leave the path ambiguous about whether it is already encoded,
#:     which is how a name round-trips to a different file;
#:   - ``"``, ``'``, `` ` `` and ``\\`` quote or escape the thing they sit in.
#:
#: ``[`` and ``]`` -- the alt-text delimiters, and glob metacharacters -- are
#: already refused by ``assets``, as is ``.``.
#:
#: Stated as a denylist rather than an ASCII allowlist on purpose: a library is
#: not English, and a name in any script survives a URL and a markdown link
#: unharmed. What cannot survive is punctuation the surrounding syntax owns.
UNADDRESSABLE = frozenset("()<>#?%\"'`\\") | frozenset(" \t\n\r\v\f")


def addressable(name: str) -> bool:
    """Can a post link to this image?

    The rule ``put_image`` gates on and ``list_images`` filters by, and they
    must be the same rule: #373's lesson was a token that named a file the
    server would not serve, and offering a picker tile whose insert renders as
    broken markdown is that bug wearing a different hat. Both ends ask this, so
    a name the library accepts is a name the library will offer, and vice versa.

    ``assets`` still owns the filesystem half (``safe_id``, no ``.``, no glob
    metacharacter, ``promote-tmp`` reserved); this is only the URL half on top.
    A name that passes here can still be refused by ``put_in``, which raises the
    same ``ValueError`` this does -- one answer, one status, either way.
    """
    return bool(name) and not any(c in UNADDRESSABLE for c in name)


def images_dir(cid: str) -> Path:
    """``<campaign>/assets/images``, after proving the campaign is actually there.

    ``campaign_root`` is a syntax guard, not an existence check -- it only
    rejects ids ``safe_id`` refuses. Without this, a put for an unknown id would
    create a campaign directory holding images and no ``campaign.md``: bytes no
    listing can ever show and no delete route can ever name, reported to the
    caller as a successful upload (#360, #373). The route gates on the campaign
    too, so this is defence in depth for any other caller.
    """
    if not campaigns_paths.campaign_exists(cid):
        raise campaigns_paths.CampaignNotFound(cid)
    return campaigns_paths.campaign_root(cid) / "assets" / DIRNAME


def list_images(cid: str) -> list[dict]:
    """``[{"name", "ext", "v"}, ...]`` for every image a post could link to.

    Filtered by ``addressable``, so a file dropped into the directory under a
    name that cannot go in a markdown link is simply not offered -- the picker
    has no way to insert it and would produce a broken image if it tried. The
    file is untouched, and renaming it in place is all it takes to surface.
    """
    return [i for i in assets.list_in(images_dir(cid)) if addressable(i["name"])]


def image_path(cid: str, name: str) -> Path | None:
    """The file backing `name`, or None.

    Deliberately *not* filtered by ``addressable``: this answers "what is on
    disk under this name", and a caller that already holds a name -- a serve
    route, the export resolving a URL a post carries -- is asking about a file,
    not about what the picker offers.
    """
    return assets.path_in(images_dir(cid), name, supported_only=True)


def put_image(cid: str, name: str, data: bytes, ext: str) -> str:
    """Store `data` as `<name>.<ext>`; returns the stored extension.

    `ext` is what the route detected in these bytes (``_upload_image_ext``),
    never what a filename claimed -- see ``routes.common._upload_image_ext``
    and #321.
    """
    if not addressable(name):
        raise ValueError("image name cannot be used in a link")
    d = images_dir(cid)
    with locks.campaign_lock(cid):
        return assets.put_in(d, name, data, ext, supported_only=True)


def delete_image(cid: str, name: str) -> None:
    """Remove `name`, and confirm it.

    ``assets.delete_in`` swallows a failed unlink by design -- a lost cleanup
    self-heals there, because resolution prefers the newest file. Here the
    unlink IS the operation: on Windows a sync client or a scanner can hold the
    file, and a swallowed failure would answer "removed" to a Remove that did
    nothing. Same shape as ``covers.delete_cover``.
    """
    d = images_dir(cid)
    with locks.campaign_lock(cid):
        assets.delete_in(d, name, supported_only=True)
        if assets.path_in(d, name, supported_only=True) is not None:
            raise OSError(f"image could not be removed: {name}")
