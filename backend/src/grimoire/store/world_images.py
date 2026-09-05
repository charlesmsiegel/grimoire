"""The world's own image library: ``<world>/assets/images/<name>.<ext>``.

Art that belongs to the *world* and to none of its records. ``store.campaign_images``
(#376) made a home for art that belongs to no record of one campaign, and every
example in its docstring -- a map of the coastline, a picture of the room the
narrator is describing -- is more often a property of the world than of one
campaign in it. Kept per campaign it had to be uploaded once per campaign, or
had by only one of them.

So this is where it lives, and **every campaign on the world reads through to
it** (``store.campaign_images``). What a campaign may do with an image it does
not own is hide it, not replace it: there is no shadowing anywhere in this
design, because a library image is bytes rather than a document you edit, and
"a different picture in this campaign" is answered completely by a different
name.

Three things this shares with its campaign-side sibling, and one it does not:

- **The policy is one copy**, in ``store.image_library`` -- what a name may be,
  how big an upload may get, how a flat directory enumerates. Two copies would
  agree right up until one of them was fixed.
- **``assets``' directory-level primitives**, with ``supported_only``, because
  this is a directory a human browses and a sync client writes into: a
  ``notes.txt`` beside ``coastline.png`` must neither win resolution nor be
  deleted by a replace or a remove -- it is not ours.
- **The write calls stay here** rather than moving into ``image_library``:
  ``tests/test_lock_domain_guard.py`` recognizes a mutating module by its
  ``assets.put_in``/``assets.delete_in`` call sites, and mutation does not
  propagate across an import.
- What it does **not** share is a lock. See below.

**Nothing here takes a lock, because worlds have no lock domain at all.** That
is not a gap this module opens; ``overlay.set_description``'s docstring already
names it -- the world-side description write is unlocked too, and ``focus.json``
and ``subjects.json`` race there in exactly the same way. Inventing a half-lock
for one directory would imply a guarantee the world root does not make. It also
means this module is never surveyed by the lock-domain guard (``_takes_cid``
looks for a parameter named ``cid``, and everything here takes ``wid``), so it
is deliberately absent from ``locks.py``'s three lists rather than declared in
one of them -- a declared module that is never surveyed fails
``test_the_declaration_has_no_phantom_modules``.

**Deleting an image sweeps the campaigns that hid it.** A tombstone that
outlives the image it hid is a defect ``overlay.forget_world_record`` already
names for record art -- "they hide by slot" -- and a library image has no record
to be swept along with. Without the sweep, deleting ``map`` and uploading a new
``map`` would leave every campaign that had hidden the old one blind to the new
one forever. The sweep is best-effort per campaign, and what makes that
affordable is that a hidden image is *listed* with a Restore beside it
(``campaign_images.list_hidden``): a skipped campaign keeps a stale tombstone
the reader can see and clear, rather than permanent invisible blindness. The two
halves are one decision and neither works without the other.
"""

from __future__ import annotations

import logging
from pathlib import Path

from . import assets, image_descriptions, image_library, locks, overlay
from .worlds import paths as worlds_paths

log = logging.getLogger(__name__)

DIRNAME = "images"


def images_dir(wid: str) -> Path:
    """``<world>/assets/images``, after proving the world is actually there.

    ``world_root`` is a syntax guard, not an existence check. Without this, a put
    for an unknown id would create a world directory holding images and no
    ``world.md``: bytes no listing can ever show and no delete route can ever
    name, reported to the caller as a successful upload (#360, #373). The route
    gates on the world too, so this is defence in depth for any other caller.
    """
    if not worlds_paths.world_exists(wid):
        raise worlds_paths.WorldNotFound(wid)
    return worlds_paths.world_root(wid) / "assets" / DIRNAME


def list_images(wid: str) -> list[dict]:
    """``[{"name", "ext", "v"}, ...]`` for every image a post could link to."""
    return image_library.listing(images_dir(wid))


def image_path(wid: str, name: str) -> Path | None:
    """The file backing `name`, or None.

    Deliberately *not* filtered by ``addressable``: this answers "what is on
    disk under this name", and a caller that already holds a name -- a serve
    route, the export resolving a URL a post carries -- is asking about a file,
    not about what the picker offers.
    """
    return assets.path_in(images_dir(wid), name, supported_only=True)


def image_version(wid: str, name: str) -> str:
    """Cache-busting token for `name`'s current bytes, "" when there is none.

    Swallows `OSError` for ``covers.cover_version``'s reason: this resolves a
    path and then stats it, and the file can go between the two -- a sync
    client, another device. A missing token costs one revalidation; a 500 would
    cost the caller the answer to a write that already landed.
    """
    p = image_path(wid, name)
    if p is None:
        return ""
    try:
        return assets.image_version(p)
    except OSError:
        return ""


def put_image(wid: str, name: str, data: bytes, ext: str) -> str:
    """Store `data` as `<name>.<ext>`; returns the stored extension.

    `ext` is what the route detected in these bytes (``_upload_image_ext``),
    never what a filename claimed -- see ``routes.common._upload_image_ext``
    and #321.
    """
    if not image_library.addressable(name):
        raise ValueError("image name cannot be used in a link")
    return assets.put_in(images_dir(wid), name, data, ext, supported_only=True)


def read_descriptions(wid: str) -> dict[str, str]:
    """What each library image depicts, for the images that are actually there."""
    d = images_dir(wid)
    return image_descriptions.read_in(d, names={i["name"] for i in list_images(wid)})


def set_description(wid: str, name: str, text: str) -> None:
    """Describe one library image.

    Unlocked, unlike the campaign side: see the module docstring. The sidecar's
    own lock still serializes each individual write, so what is unprotected here
    is a read-modify-write across two writers, which is the same exposure every
    other world-side sidecar already carries.
    """
    image_descriptions.set_in(images_dir(wid), name, text,
                              names={i["name"] for i in list_images(wid)})


def undescribed(wid: str) -> list[dict]:
    """``[{"name"}, ...]`` for every library image with NO sidecar key.

    Key ABSENT, never merely empty: an image reviewed and deliberately left
    undescribed is finished, and re-offering it is how a queue never empties.

    This is the flat-directory twin of ``image_descriptions.undescribed``, which
    is a *base walker* -- it requires ``<root>/<base>/<record>/assets/<vid>/``
    and so structurally cannot reach a library that hangs off no record.
    """
    d = images_dir(wid)
    reviewed = image_descriptions.read_raw(d)
    return [{"name": i["name"]} for i in image_library.listing(d)
            if i["name"] not in reviewed]


def undescribed_count(wid: str) -> int:
    """How many library images are unreviewed -- the badge's half of the count."""
    return len(undescribed(wid))


def has_undescribed(wid: str) -> bool:
    """``undescribed_count`` stopping at the first one.

    Separate from the count on purpose: ``routes/todo.py``'s ``_CHEAP`` roster
    exists for chores whose COUNT costs far more than their presence, and
    answering a presence question by summing the backlog is the thing that
    roster is there to avoid.
    """
    d = images_dir(wid)
    reviewed = image_descriptions.read_raw(d)
    return any(i["name"] not in reviewed for i in image_library.listing(d))


def delete_image(wid: str, name: str) -> None:
    """Remove `name`, confirm it, and clear it from the campaigns that hid it.

    ``assets.delete_in`` swallows a failed unlink by design -- a lost cleanup
    self-heals there, because resolution prefers the newest file. Here the
    unlink IS the operation: on Windows a sync client or a scanner can hold the
    file, and a swallowed failure would answer "removed" to a Remove that did
    nothing.
    """
    d = images_dir(wid)
    assets.delete_in(d, name, supported_only=True)
    if assets.path_in(d, name, supported_only=True) is not None:
        raise OSError(f"image could not be removed: {name}")
    # The description goes with the bytes, as it does on every other image
    # surface: a kept entry would caption the next image uploaded under this
    # name, which is different art and immediately eligible for the narrator.
    assets.drop_sidecar_entry(d, assets.DESCRIPTIONS_FILE, name)
    _forget_in_dependents(wid, name)


def _forget_in_dependents(wid: str, name: str) -> None:
    """Drop this image's tombstone wherever a campaign hid it.

    Per campaign and best-effort, ``overlay.forget_world_record``'s shape and for
    the reason its comment gives: aborting the sweep on one busy campaign would
    500 a delete that has already happened, and abandon every campaign after it.
    Holding all the locks up front instead would refuse the delete outright
    whenever any campaign in the world is mid-turn -- a minutes-long absorb is an
    ordinary hold here -- which turns housekeeping into a routine failure.

    A skipped campaign keeps a stale tombstone. That is survivable ONLY because
    ``campaign_images.list_hidden`` surfaces it with a Restore beside it, so the
    reader can clear what the sweep could not. The two halves are one decision.
    """
    try:
        root = worlds_paths.world_root(wid)
    except worlds_paths.WorldNotFound:
        return
    for cid in overlay.dependent_campaigns(root):
        try:
            overlay.drop_library_tombstone(cid, name)
        except (OSError, ValueError, locks.StoreBusy) as exc:
            log.warning(
                "could not clear the hidden entry for world image %s in campaign %s "
                "(%s) -- it stays listed as hidden there and can be restored by hand",
                name, cid, exc)
