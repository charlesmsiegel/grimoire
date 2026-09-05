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

What an upload is checked for is what the other six image surfaces check for
and no more: the bytes have to name a format we can label (``routes.common.
_upload_image_ext``, magic bytes -- the same detector ``export.packed_ext``
names a packed image with, which is what keeps a stored suffix and a declared
media type from being two rules that merely agree today). Deliberately not
``covers.validate``'s PIL decode: a cover is thumbnailed for the campaigns list
and so has to bound its raster, and only a cover is. These are thumbnailed
too, by ``?w=``, and that path is bounded elsewhere -- ``MAX_BYTES`` caps the
file, PIL refuses a raster past its own bomb threshold, and ``thumbs.thumbnail``
answers None to any failed decode, which serves the original bytes. Identical
to the six surfaces this mirrors; diverging here would leave one of seven
stricter for no reason a reader could find.

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

from . import assets, image_descriptions, image_library, locks
from .campaigns import paths as campaigns_paths

DIRNAME = "images"

#: The policy half -- what a name may be, how big an upload may get -- lives in
#: `store.image_library`, shared with the world's library so the two cannot
#: disagree. Deliberately NOT re-exported here: a value copied onto this module
#: is a second source of truth, and a test that patches the copy (or the
#: original) silently stops reaching the other. Callers that want the policy
#: import the policy.


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
    return image_library.listing(images_dir(cid))


def image_path(cid: str, name: str) -> Path | None:
    """The file backing `name`, or None.

    Deliberately *not* filtered by ``addressable``: this answers "what is on
    disk under this name", and a caller that already holds a name -- a serve
    route, the export resolving a URL a post carries -- is asking about a file,
    not about what the picker offers.
    """
    return assets.path_in(images_dir(cid), name, supported_only=True)


def image_version(cid: str, name: str) -> str:
    """Cache-busting token for `name`'s current bytes, "" when there is none.

    Swallows `OSError` for the reason ``covers.cover_version`` does: this
    resolves a path and then stats it, and the file can go between the two --
    a sync client, another device. A missing token costs one revalidation; a
    500 would cost the caller the answer to a write that already landed.
    """
    p = image_path(cid, name)
    if p is None:
        return ""
    try:
        return assets.image_version(p)
    except OSError:
        return ""


def put_image(cid: str, name: str, data: bytes, ext: str) -> str:
    """Store `data` as `<name>.<ext>`; returns the stored extension.

    `ext` is what the route detected in these bytes (``_upload_image_ext``),
    never what a filename claimed -- see ``routes.common._upload_image_ext``
    and #321.
    """
    if not image_library.addressable(name):
        raise ValueError("image name cannot be used in a link")
    d = images_dir(cid)
    with locks.campaign_lock(cid):
        return assets.put_in(d, name, data, ext, supported_only=True)


def set_description(cid: str, name: str, text: str) -> None:
    """Describe one library image, under `campaign_lock`.

    The sidecar is read-modify-written whole, so two unlocked writers describing
    DIFFERENT images lose one of the two sentences -- and what is lost is
    something somebody sat and wrote. Every other campaign-scoped description
    write takes this lock (`overlay.set_description`); this one was reaching
    past it into `image_descriptions` directly, which made the library the one
    surface where that race was still open.

    This module owns the directory; `store.image_descriptions` owns every rule
    about what its sidecar means, which is why the write goes through there.
    """
    with locks.campaign_lock(cid):
        # Listed inside the lock: computed outside it, the check could pass for
        # an image a concurrent delete had already taken away.
        image_descriptions.set_in(images_dir(cid), name, text,
                                  names={i["name"] for i in list_images(cid)})


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
        # The description goes with the bytes, as it does on the record surfaces
        # (`assets.delete_image`). A kept entry would caption the NEXT image
        # uploaded under this name -- different art, immediately visible and
        # immediately eligible for the narrator's art section.
        assets.drop_sidecar_entry(d, assets.DESCRIPTIONS_FILE, name)
