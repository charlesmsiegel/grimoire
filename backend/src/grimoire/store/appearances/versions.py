"""Version locking: materializing a picked version into the campaign tree,
purging siblings, and the sync-base bookkeeping that goes with it -- plus
``actor_source``, which is that bookkeeping read back rather than written.

``actor_source`` is a read, so ``cast.py`` -- this package's read-only
queries -- is where it looks like it belongs. It is here because what it
reads is the `base` this module writes: it is the same comparison ``_lock``
sets up and ``store/sync.py`` makes, and splitting the two halves of that
contract across modules is how they drift. It also needs ``overlay`` and
``campaigns.read``, which this module already binds and ``cast.py`` does not.

``_lock`` lives here rather than in ``paths.py``: it calls ``actor_hash``,
``_copy_actor``, ``_purge_other_versions``, ``_set_default`` and
``_drop_manifest_ref``, all defined here, and those call back into
``record``/``_ref``/``_write`` -- splitting the two would close a
``paths <-> versions`` cycle.
"""

from __future__ import annotations

from pathlib import Path

from .. import atomic, characters, overlay, pcs
from ..campaigns import paths as campaigns_paths
from ..campaigns import read as campaigns_read
from ..frontmatter import dump_frontmatter, parse_frontmatter
from . import paths


def set_base(cid: str, kind: str, actor_id: str, base: str) -> None:
    """Advance the recorded sync base hash for an appeared actor (sync uses this)."""
    data = paths.record(cid)
    ref = paths._ref(kind, actor_id)
    if ref in data:
        data[ref]["base"] = base
        paths._write(cid, data)


def actor_hash(root: Path, kind: str, actor_id: str, vid: str) -> str | None:
    if kind == "characters":
        return characters.card_hash(root, actor_id, vid)
    return pcs.version_hash(root, actor_id, vid)


def actor_source(cid: str, kind: str, actor_id: str) -> str:
    """Where an appeared actor's text came from: `"library"`, `"override"` or
    `"emergent"` (#99).

    Derived, never stored. A campaign is a copy-on-create deep copy of its
    world, so nothing on disk says "this one is the library's" outright -- and
    a field that did would go stale on the first edit, which is the whole
    reason the answer is computed from hashes that already exist:

    - **emergent** -- this campaign owns the actor outright, and no library
      record stands behind it. Either the world never had one under that id
      (`overlay.create_character`, the emergent-cast route, #98), or it had one
      and it was deleted -- which `overlay.detached` records precisely because
      whatever claims the freed slug next is a stranger. A campaign whose world
      is missing entirely reads the same way, and deliberately: that campaign
      inherits nothing, which is the reading `campaigns.read.world_root_of`
      already gives it by answering an unoccupiable path.
    - **library** -- the campaign's copy still hashes to the `base` recorded
      when the version was locked, so nobody has edited it here.
    - **override** -- it does not, so the text under the lock is this
      campaign's own.

    Provenance, not sync state. A *world*-side edit since the lock leaves this
    on "library": the campaign is still holding the library's text as it took
    it, and the fact that the library has moved on is a pending update
    `store/sync.py` reports (#71 is that axis). The two are separate on purpose
    -- accepting an update advances `base` and copies, so the badge stays
    "library"; rejecting one advances `base` without copying, which is a
    campaign that has deliberately pinned its own text and reads as "override"
    from then on.

    Raises `AppearError` for an actor that has not appeared: there is no lock
    to compare against, and unpicked actors take world changes wholesale
    through sync rather than holding a version of their own.

    `overlay.detached` is fail-soft, so a corrupt `detached.json` loses the
    first test above -- and the actor then falls to the hash comparison against
    a base recorded from the original the world no longer has, which cannot
    match. The badge degrades to "override", never to "library": a wrong answer
    that overstates the campaign's ownership, rather than one that promises a
    library record stands behind a card the library never wrote.
    """
    ref = paths._ref(kind, actor_id)
    rec = paths.record(cid).get(ref)
    if rec is None:
        raise paths.AppearError(f"{ref} has not appeared in campaign {cid}")
    if ref in overlay.detached(cid):
        return "emergent"
    if not _actor_exists(campaigns_read.world_root_of(cid), kind, actor_id):
        return "emergent"
    # `None` means the campaign copy is not there to hash. It is reachable: a
    # campaign-side `delete_character` rmtree's the actor dir and deliberately
    # does NOT sweep `appearances.json` (the emergent-cast route says so where
    # it handles the consequence), so the record can outlive the card. Answer
    # "override" rather than "library": the one thing this must never do is
    # call a card the library's when it could not read the card at all.
    mine = actor_hash(paths.locked_actor_root(cid), kind, actor_id, rec["version"])
    return "library" if mine is not None and mine == rec["base"] else "override"


def _actor_exists(root: Path, kind: str, actor_id: str) -> bool:
    """Does `root` hold this actor at all? Kind dispatch, like `actor_hash`."""
    if kind == "characters":
        return characters.character_exists(root, actor_id)
    return pcs.pc_exists(root, actor_id)


def _version_ext(kind: str) -> str:
    return "json" if kind == "characters" else "md"


def _meta_name(kind: str) -> str:
    return "character.md" if kind == "characters" else "pc.md"


def _copy_actor(wroot: Path, croot: Path, kind: str, actor_id: str, vid: str) -> None:
    src_dir = wroot / kind / actor_id
    dst_dir = croot / kind / actor_id
    dst_dir.mkdir(parents=True, exist_ok=True)
    ext = _version_ext(kind)
    atomic.write_text(dst_dir / f"{vid}.{ext}", (src_dir / f"{vid}.{ext}").read_text(encoding="utf-8"))
    # container meta so campaign-side reads work; default_version points at the copied
    # version. An existing campaign meta is kept (its tag/name edits win) — callers
    # that lock re-point default_version themselves.
    if not (dst_dir / _meta_name(kind)).exists():
        meta, _ = parse_frontmatter((src_dir / _meta_name(kind)).read_text(encoding="utf-8"))
        meta["default_version"] = vid
        atomic.write_text(dst_dir / _meta_name(kind), dump_frontmatter(meta, ""))


def _purge_other_versions(croot: Path, kind: str, actor_id: str, keep: str) -> None:
    d = croot / kind / actor_id
    ext = _version_ext(kind)
    for p in d.glob(f"*.{ext}"):
        if p.name not in (f"{keep}.{ext}", _meta_name(kind)):
            p.unlink()


def _set_default(croot: Path, kind: str, actor_id: str, vid: str) -> None:
    if kind == "characters":
        characters.set_default_version(croot, actor_id, vid)
    else:
        pcs.set_default_version(croot, actor_id, vid)


def _drop_manifest_ref(cid: str, kind: str, actor_id: str) -> None:
    manifest = campaigns_paths.read_manifest(cid)
    if manifest.pop(paths._ref(kind, actor_id), None) is not None:
        campaigns_paths.write_manifest(cid, manifest)


def _lock(cid: str, kind: str, actor_id: str, version_id: str) -> str:
    """Materialize a version lock in the campaign tree: ensure the version file is
    present, purge every sibling version, point default_version at the pick, and
    drop the whole-actor sync ref (the locked per-version flow takes over).
    Returns the sync base hash for the appearance record."""
    wroot = campaigns_read.world_root_of(cid)
    croot = campaigns_paths.campaign_root(cid)
    base = actor_hash(wroot, kind, actor_id, version_id)
    if actor_hash(croot, kind, actor_id, version_id) is None:
        # Not in the campaign yet: a world actor created after the fork (copy it),
        # or nothing anywhere -> error.
        if base is None:
            raise paths.AppearError(f"no {paths._ref(kind, actor_id)}/{version_id} in world or campaign")
        _copy_actor(wroot, croot, kind, actor_id, version_id)
    _purge_other_versions(croot, kind, actor_id, version_id)
    _set_default(croot, kind, actor_id, version_id)
    _drop_manifest_ref(cid, kind, actor_id)
    return base or ""  # campaign-local actor: empty world-base, sync skips it


def pick_version(cid: str, kind: str, actor_id: str, version_id: str) -> None:
    """Explicit pick from the campaign's world pages: lock without a scene."""
    ref = paths._ref(kind, actor_id)
    data = paths.record(cid)
    if ref in data:
        raise paths.AppearError(f"{ref} is already locked to version {data[ref]['version']}")
    if actor_hash(overlay.actor_root(cid, kind, actor_id), kind, actor_id, version_id) is None:
        raise paths.AppearError(f"no {ref}/{version_id} in campaign")
    base = _lock(cid, kind, actor_id, version_id)
    data[ref] = {"version": version_id, "base": base, "scenes": [],
                 "role": "player" if kind == "pcs" else "npc"}
    paths._write(cid, data)
    campaigns_read.touch(cid)


def import_version(cid: str, kind: str, actor_id: str, version_id: str) -> None:
    """Replace the locked version with `version_id` from the source world. The
    one-version-per-locked-actor invariant always holds; unlocked actors take
    world changes via sync instead."""
    data = paths.record(cid)
    ref = paths._ref(kind, actor_id)
    rec = data.get(ref)
    if rec is None:
        raise paths.AppearError(f"{ref} is not locked; world changes arrive via sync until a version is picked")
    wroot = campaigns_read.world_root_of(cid)
    base = actor_hash(wroot, kind, actor_id, version_id)
    if base is None:
        raise paths.AppearError(f"no {ref}/{version_id} in world")
    croot = campaigns_paths.campaign_root(cid)
    ext = _version_ext(kind)
    d = croot / kind / actor_id
    d.mkdir(parents=True, exist_ok=True)
    atomic.write_text(d / f"{version_id}.{ext}",
                      (wroot / kind / actor_id / f"{version_id}.{ext}").read_text(encoding="utf-8"))
    _set_default(croot, kind, actor_id, version_id)
    old = rec["version"]
    if old != version_id and (d / f"{old}.{ext}").exists():
        (d / f"{old}.{ext}").unlink()
    rec["version"] = version_id
    rec["base"] = base
    paths._write(cid, data)
    campaigns_read.touch(cid)


def locked_version(cid: str, kind: str, actor_id: str) -> str | None:
    rec = paths.record(cid).get(paths._ref(kind, actor_id))
    return rec["version"] if rec else None
