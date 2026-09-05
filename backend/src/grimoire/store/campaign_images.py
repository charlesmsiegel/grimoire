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

from . import assets, image_descriptions, image_library, locks, overlay, world_images
from .campaigns import paths as campaigns_paths
from .campaigns import read as campaigns_read
from .worlds import paths as worlds_paths

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


def _world_of(cid: str) -> str:
    """The id of the world this campaign reads through to, or "".

    From the campaign's own meta rather than ``overlay.wroot_of(cid).name``: a
    path basename is a spelling, and what ``world_images`` takes is a reference.
    Answers "" rather than raising for a campaign whose meta cannot be read --
    the callers below turn that into an empty world half.
    """
    try:
        return str(campaigns_read.read_campaign(cid)["meta"].get("world") or "")
    except (campaigns_paths.CampaignNotFound, KeyError, OSError, UnicodeDecodeError):
        return ""


def _world_images(cid: str) -> list[dict]:
    """The world's library as this campaign sees it, empty when there is none.

    ``WorldNotFound`` is caught rather than propagated, and that is the whole
    reason this is a function rather than an inline call. A campaign whose world
    was deleted still has to answer ``GET /images``, the gallery, the narrator's
    art pool and an EPUB export; raising through all four turns a missing world
    into four 500s. An empty world half is what every other overlay read already
    degrades to.
    """
    wid = _world_of(cid)
    if not wid:
        return []
    try:
        return world_images.list_images(wid)
    except (worlds_paths.WorldNotFound, OSError):
        return []


def _world_image_path(cid: str, name: str) -> Path | None:
    """``_world_images``' twin for one name, with the same tolerance."""
    wid = _world_of(cid)
    if not wid:
        return None
    try:
        return world_images.image_path(wid, name)
    except (worlds_paths.WorldNotFound, OSError):
        return None


def _own_images(cid: str) -> list[dict]:
    """Just this campaign's own uploads -- what ``images_dir`` holds."""
    return image_library.listing(images_dir(cid))


def list_images(cid: str) -> list[dict]:
    """The campaign's own images, plus the world's that it neither holds nor hid.

    Each row carries ``inherited``, because every surface that shows one has a
    different sentence for the two: the picker offers "remove from this
    campaign" for one and "delete" for the other, and the gallery names the
    world as the owner.

    **The tombstone filter applies to the INHERITED half only.**
    ``overlay.list_images`` is the model, and the reason is not symmetry: a
    campaign may hold its own image under a name it previously hid, and
    subtracting tombstones from the whole union would drop that image from the
    listing while ``image_path`` still served it. Bytes that serve but never
    list are in no picker, no gallery and no describe row, with nothing left
    that can clear the tombstone hiding them -- #373's lesson inverted.
    """
    mine = _own_images(cid)
    have = {i["name"] for i in mine}
    gone = overlay.deleted(cid)
    inherited = [i for i in _world_images(cid)
                 if i["name"] not in have
                 and overlay.library_ref(i["name"]) not in gone]
    return sorted([{**i, "inherited": False} for i in mine]
                  + [{**i, "inherited": True} for i in inherited],
                  key=lambda i: i["name"])


def list_hidden(cid: str) -> list[str]:
    """World library names this campaign has hidden and could restore.

    What makes the world-side sweep affordable. That sweep is best-effort per
    campaign (``world_images._forget_in_dependents``), so a busy campaign can
    keep a tombstone for an image the world no longer has -- and the only reason
    that is survivable rather than permanent invisible blindness is that it
    shows up here with a Restore beside it.

    Filtered to names the world actually holds, so a tombstone the sweep already
    cleared, or one aimed at an image that never existed, is not offered as
    something to restore.
    """
    gone = overlay.deleted(cid)
    return sorted(i["name"] for i in _world_images(cid)
                  if overlay.library_ref(i["name"]) in gone)


def image_path(cid: str, name: str) -> Path | None:
    """The file backing `name` for this campaign: its own, else the world's.

    Deliberately *not* filtered by ``addressable``: this answers "what is on
    disk under this name", and a caller that already holds a name -- a serve
    route, the export resolving a URL a post carries -- is asking about a file,
    not about what the picker offers.

    A tombstone **stops** the search rather than being skipped, so the serve
    route 404s instead of falling through to the picture the campaign hid.
    ``overlay.image_root`` has the same shape for record art.
    """
    mine = assets.path_in(images_dir(cid), name, supported_only=True)
    if mine is not None:
        return mine
    if overlay.library_ref(name) in overlay.deleted(cid):
        return None
    return _world_image_path(cid, name)


def image_version(cid: str, name: str) -> str:
    """Cache-busting token for `name`'s current bytes, "" when there is none.

    Follows ``image_path`` rather than ``images_dir``, so an inherited image is
    versioned by the world file that actually backs it. A `?v=` URL is answered
    ``immutable, max-age=1y``, so a token that did not follow those bytes would
    pin a reader for a year to art that has been replaced.

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
    """Store `data` as `<name>.<ext>` campaign-side; returns the extension.

    `ext` is what the route detected in these bytes (``_upload_image_ext``),
    never what a filename claimed -- see ``routes.common._upload_image_ext``
    and #321.

    **A name the world currently holds is refused.** There is no shadowing in
    this design: a campaign that wants a different picture uses a different
    name. The refusal is also what keeps the accidental collision -- a world
    adding a name a campaign already has -- rare rather than routine, since
    nothing can prevent that one.
    """
    if not image_library.addressable(name):
        raise ValueError("image name cannot be used in a link")
    d = images_dir(cid)
    with locks.campaign_lock(cid):
        # Checked inside the hold, and against what the campaign holds *now*:
        # outside it this is a check-then-act, and the interesting interleaving
        # is two uploads of the same name racing each other.
        if assets.path_in(d, name, supported_only=True) is None and \
                _world_image_path(cid, name) is not None:
            raise ValueError("that name belongs to the world")
        return assets.put_in(d, name, data, ext, supported_only=True)


def restore_image(cid: str, name: str) -> None:
    """Un-hide an inherited image. The exit from `delete_image`'s tombstone."""
    overlay.drop_library_tombstone(cid, name)


def read_descriptions(cid: str) -> dict[str, str]:
    """What each image this campaign can see depicts.

    A union rather than a merge, and disjoint by construction: an inherited
    image is described world-side and a campaign's own is described here, so no
    name can be described twice. (The accidental collision is the one exception,
    and the campaign's own wins there for the same reason its bytes do.)
    """
    own = image_descriptions.read_in(images_dir(cid),
                                     names={i["name"] for i in _own_images(cid)})
    wid = _world_of(cid)
    inherited: dict[str, str] = {}
    if wid:
        try:
            inherited = world_images.read_descriptions(wid)
        except (worlds_paths.WorldNotFound, OSError):
            inherited = {}
    visible = {i["name"] for i in list_images(cid)}
    return {name: text for name, text in {**inherited, **own}.items()
            if name in visible}


def own_undescribed(cid: str) -> list[dict]:
    """``[{"name"}, ...]`` for this campaign's OWN unreviewed library images.

    Its own, never the inherited ones: art a campaign reaches through its world
    belongs to the world's queue, where describing it once serves every campaign
    on that world. Handing the merged listing to this would re-offer every world
    image in every campaign's backlog -- the failure
    ``GET /campaigns/{cid}/images/undescribed``'s docstring exists to prevent.
    """
    d = images_dir(cid)
    reviewed = image_descriptions.read_raw(d)
    return [{"name": i["name"]} for i in _own_images(cid)
            if i["name"] not in reviewed]


def set_description(cid: str, name: str, text: str) -> None:
    """Describe one of this campaign's OWN library images, under `campaign_lock`.

    Campaign-owned only: an inherited image is described in the world's editor,
    which is where describing it once serves every campaign on that world.

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
                                  names={i["name"] for i in _own_images(cid)})


def delete_image(cid: str, name: str) -> None:
    """Remove `name` from this campaign: unlink its own copy, then hide the
    world's if the world still has one.

    **Both halves, in that order** -- ``overlay.delete_image``'s shape. It reads
    as belt-and-braces and is not: the accidental collision (a campaign holding
    a name the world later took) is a case where both are true at once, and
    unlinking alone would let the world's picture appear under a name the user
    just deleted, turning Delete into Revert. Tombstoning alone would leave the
    campaign's own bytes on disk behind a tombstone that does not hide them.

    For an image the campaign owns and the world does not, the tombstone is
    simply not written -- ``_world_image_path`` answers None.

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
        if _world_image_path(cid, name) is not None:
            overlay.add_deleted(cid, overlay.library_ref(name))
