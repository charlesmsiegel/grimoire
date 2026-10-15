"""The lifecycle of a scope on the shared image objects.

A subject tag is written on the image object, under a scope that names the
world or campaign it was made in (R10): ``world:<canonical wid>`` or
``campaign:<cid>``. Both ids are slugs, and a slug is reusable -- so a world
deleted and then created again under its old name, with the same picture
placed again, would find the dead world's tags waiting on the object (Review
Focus 4). The directory that held them is gone; the object outlives it.

So the scope follows its record's lifecycle here:

- **delete** strips it (`strip_world` from `worlds.delete_world`,
  `strip_campaign` from `campaigns.delete_campaign`), before the tree goes;
- **fork** copies it onto the fork's own (`copy_world` from
  `worlds.fork_world`, `copy_campaign` from `fork.fork_campaign`), a union, so
  the fork shows what its source showed and the two then diverge.

`strip` and `copy` inventory the sidecars, but #477's compact scope cache
selects candidates before `read_fresh`: a warm sweep parses no unchanged,
unrelated object within the bounded cache. Membership is stat-validated, and
`update` is called only for an object with something to change -- with a
callback that re-decides on the copy it is handed, so a concurrent edit is not
overwritten with what the sweep read. Neither takes a campaign lock: the
`*_campaign` wrappers do, around the whole sweep, and world scopes have no lock
to take (worlds have none). Nothing here calls `update` from inside another
`update`.

Imports `image_store`, `locks`, `statcache` and `worlds.paths`.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from . import image_store, locks, statcache
from .worlds import paths as worlds_paths


def world_scope(wid: str) -> str:
    """The scope of world `wid` (R10): its id as the filesystem spells it, so
    a world reached as `REALM` keeps its tags where `realm` does."""
    return f"world:{worlds_paths.canonical_id(wid)}"


def world_scope_of_dir(dirname: str) -> str:
    """The scope of the world whose directory is named `dirname`, as a
    directory listing spells it. That spelling is already the canonical one, so
    this skips the listing `world_scope` makes to find it."""
    return f"world:{dirname}"


def campaign_scope(cid: str) -> str:
    """The scope of campaign `cid` (R10): the id as stored."""
    return f"campaign:{cid}"


def _assoc_list(raw: dict) -> list | None:
    got = raw.get("associations")
    return got if isinstance(got, list) else None


def _review_list(raw: dict) -> list | None:
    reviews = raw.get("reviews")
    listed = reviews.get("subjects") if isinstance(reviews, dict) else None
    return listed if isinstance(listed, list) else None


def _in_scope(a: object, scope: str) -> bool:
    return isinstance(a, dict) and a.get("scope") == scope


def _stripped(raw: dict, scope: str) -> dict | None:
    """`raw` without `scope`'s associations and review, or None when it holds
    neither. Tolerant: a malformed field is left exactly as found."""
    dirty = False
    assoc = _assoc_list(raw)
    if assoc is not None and any(_in_scope(a, scope) for a in assoc):
        raw["associations"] = [a for a in assoc if not _in_scope(a, scope)]
        dirty = True
    listed = _review_list(raw)
    if listed is not None and scope in listed:
        raw["reviews"] = {**raw["reviews"], "subjects": [s for s in listed if s != scope]}
        dirty = True
    return raw if dirty else None


def _copied(raw: dict, src: str, dst: str) -> dict | None:
    """`raw` with `src`'s associations and review added under `dst` as a union,
    or None when that adds nothing. `src`'s own entries stay."""
    dirty = False
    assoc = _assoc_list(raw)
    if assoc is not None:
        have = list(assoc)
        for a in assoc:
            if _in_scope(a, src):
                moved = {**a, "scope": dst}
                if moved not in have:
                    have.append(moved)
                    dirty = True
        if dirty:
            raw["associations"] = have
    listed = _review_list(raw)
    if listed is not None and src in listed and dst not in listed:
        raw["reviews"] = {**raw["reviews"],
                          "subjects": sorted({s for s in listed if isinstance(s, str)} | {dst})}
        dirty = True
    return raw if dirty else None


# A separate, bounded pool of scope sets, never raw objects: a lifecycle
# sweep must not evict the object/card caches every gallery depends on (#477).
_SCOPE_POOL: dict = {}


def _scopes(raw: dict) -> frozenset[str]:
    assoc = _assoc_list(raw) or []
    reviews = _review_list(raw) or []
    return frozenset([a["scope"] for a in assoc
                      if isinstance(a, dict) and isinstance(a.get("scope"), str)]
                     + [s for s in reviews if isinstance(s, str)])


def _memberships(image_id: str, root: Path) -> frozenset[str]:
    path = image_store.object_path(image_id, root=root)

    def compute() -> tuple[frozenset[str], tuple[statcache.Stamp, ...] | None]:
        # Stamp BEFORE reading, under the same captured root: an external
        # replacement during the read must invalidate on the next sweep.
        stamp = statcache.stamp(path)
        obj = image_store.read_fresh(image_id, root=root)
        return (_scopes(obj.raw) if obj is not None else frozenset(),
                (stamp,) if stamp is not None else None)

    return statcache.memo_stamped(("image_scopes", str(path)), compute,
                                  pool=_SCOPE_POOL, max_entries=statcache.MAX_ENTRIES)


def _sweep(scope: str, edit: Callable[[dict], dict | None]) -> int:
    """Apply `edit` to every object it changes; the number written."""
    changed = 0
    root = image_store.store_root().parent.parent.resolve()
    for image_id in image_store.iter_ids(root=root):
        if scope not in _memberships(image_id, root):
            continue
        obj = image_store.read_fresh(image_id, root=root)
        # Decided on what was read first, so an object with nothing to change
        # is never rewritten (nor its stripe taken); `update` then decides
        # again on what it reads under the stripe. `read_fresh` shares its
        # `raw` with no cache, so the trial edit touches nobody else's dict.
        if obj is None or edit(dict(obj.raw)) is None:
            continue
        if image_store.update(image_id, edit):
            changed += 1
    return changed


def strip(scope: str) -> int:
    """Remove `scope`'s associations and its ``reviews.subjects`` entry from
    every object. Returns how many objects changed; idempotent."""
    return _sweep(scope, lambda raw: _stripped(raw, scope))


def copy(src: str, dst: str) -> int:
    """Add `src`'s associations and review under `dst`, as a union, on every
    object that has them. Returns how many objects changed; idempotent."""
    return _sweep(src, lambda raw: _copied(raw, src, dst))


def strip_world(wid: str) -> int:
    """`strip` for world `wid`. No lock: worlds have none."""
    return strip(world_scope(wid))


def copy_world(src_wid: str, dst_wid: str) -> int:
    """`copy` from world `src_wid` onto world `dst_wid`. No lock."""
    return copy(world_scope(src_wid), world_scope(dst_wid))


def strip_campaign(cid: str) -> int:
    """`strip` for campaign `cid`, under its lock, so a tag written in the
    campaign cannot land between the sweep's read and its write."""
    with locks.campaign_lock(cid):
        return strip(campaign_scope(cid))


def copy_campaign(cid: str, new_cid: str) -> int:
    """`copy` from campaign `cid` onto `new_cid`, under both locks. Reentrant
    inside `fork.fork_campaign`'s own `hold_all` of the same two."""
    with locks.hold_all([cid, new_cid]):
        return copy(campaign_scope(cid), campaign_scope(new_cid))
