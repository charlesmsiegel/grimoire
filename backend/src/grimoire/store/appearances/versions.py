"""Version locking: materializing a picked version into the campaign tree,
purging siblings, and the sync-base bookkeeping that goes with it.

``_lock`` lives here rather than in ``paths.py``: it calls ``actor_hash``,
``_copy_actor``, ``_purge_other_versions``, ``_set_default`` and
``_drop_manifest_ref``, all defined here, and those call back into
``record``/``_ref``/``_write`` -- splitting the two would close a
``paths <-> versions`` cycle.
"""

from __future__ import annotations

from pathlib import Path

from .. import atomic, characters, overlay, pcs
from ..campaigns import paths as campaigns_paths, read as campaigns_read
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
