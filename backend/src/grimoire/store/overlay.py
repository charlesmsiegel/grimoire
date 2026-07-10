"""Campaign-over-world copy-on-write resolution.

A campaign materializes a record only when it diverges from its world (edit,
version lock, delete, campaign-local create); everything else reads through to
the world live. Rules:

- Flat records (locations/lore/greetings, plotmap.json): campaign file wins;
  else a tombstone means absent; else the world file.
- Actors (characters/pcs): whole-dir, keyed on character.md / pc.md existing in
  the campaign — a materialized actor is authoritative for meta + versions, so
  lock-purged versions stay purged. Sidecars (tagline.md) and assets still
  overlay per file.
- sync.md holds base hashes for materialized records only. Tombstones live in
  <campaign>/deleted.json (a sorted JSON list of refs); a tombstoned id counts
  as taken for uniquify, so nothing ever resurrects under a reused id.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import campaigns, entities, worlds


def croot_of(cid: str) -> Path:
    return campaigns.campaign_root(cid)


def wroot_of(cid: str) -> Path:
    """The campaign's world root. May not exist (world deleted before the
    guard existed) — resolution treats a missing world as an empty one."""
    return worlds.world_root(campaigns.read_campaign(cid)["meta"].get("world", ""))


# ---- tombstones ----

def _deleted_path(cid: str) -> Path:
    return campaigns.campaign_root(cid) / "deleted.json"


def deleted(cid: str) -> set[str]:
    p = _deleted_path(cid)
    if not p.exists():
        return set()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return set()
    return set(data) if isinstance(data, list) else set()


def add_deleted(cid: str, ref: str) -> None:
    _deleted_path(cid).write_text(
        json.dumps(sorted(deleted(cid) | {ref}), indent=2) + "\n", encoding="utf-8")


# ---- flat records (locations / lore; greetings + plotmap join in Task 2) ----

def _flat_ref(kind: str, eid: str) -> str:
    return f"{kind}/{eid}"


def _flat_path(root: Path, kind: str, eid: str) -> Path:
    return root / kind / f"{eid}.md"


def _materialize_flat(cid: str, kind: str, eid: str) -> bool:
    """Copy an inherited flat record into the campaign and record its sync
    base. True if the campaign file exists afterwards. Assets are never
    copied — they overlay per file. Tombstoned records don't materialize."""
    croot = croot_of(cid)
    if _flat_path(croot, kind, eid).exists():
        return True
    wroot = wroot_of(cid)
    src = _flat_path(wroot, kind, eid)
    if not src.exists() or _flat_ref(kind, eid) in deleted(cid):
        return False
    dst = _flat_path(croot, kind, eid)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    manifest = campaigns.read_manifest(cid)
    manifest[_flat_ref(kind, eid)] = entities.entity_hash(wroot, kind, eid) or ""
    campaigns.write_manifest(cid, manifest)
    return True


def _drop_manifest_ref(cid: str, ref: str) -> None:
    manifest = campaigns.read_manifest(cid)
    if manifest.pop(ref, None) is not None:
        campaigns.write_manifest(cid, manifest)


def materialize_entity(cid: str, kind: str, eid: str) -> None:
    if not _materialize_flat(cid, kind, eid):
        raise entities.EntityNotFound(f"{kind}/{eid}")


def list_entities(cid: str, kind: str) -> list[dict]:
    mine = entities.list_entities(croot_of(cid), kind)
    have = {e["id"] for e in mine}
    gone = deleted(cid)
    inherited = [e for e in entities.list_entities(wroot_of(cid), kind)
                 if e["id"] not in have and _flat_ref(kind, e["id"]) not in gone]
    return sorted(mine + inherited, key=lambda e: e["id"])


def read_entity(cid: str, kind: str, eid: str) -> dict:
    try:
        return entities.read_entity(croot_of(cid), kind, eid)
    except entities.EntityNotFound:
        if _flat_ref(kind, eid) in deleted(cid):
            raise
        return entities.read_entity(wroot_of(cid), kind, eid)


def create_entity(cid: str, kind: str, name: str, body: str = "", keys: str = "",
                  owners: str = "") -> str:
    wroot, gone = wroot_of(cid), deleted(cid)

    def taken(eid: str) -> bool:
        return _flat_path(wroot, kind, eid).exists() or _flat_ref(kind, eid) in gone

    return entities.create_entity(croot_of(cid), kind, name, body, keys, owners, taken=taken)


def update_entity(cid: str, kind: str, eid: str, *, name: str | None = None,
                  body: str | None = None, keys: str | None = None,
                  owners: str | None = None) -> None:
    croot = croot_of(cid)
    if not _flat_path(croot, kind, eid).exists():
        materialize_entity(cid, kind, eid)
    entities.update_entity(croot, kind, eid, name=name, body=body, keys=keys, owners=owners)


def delete_entity(cid: str, kind: str, eid: str) -> None:
    ref = _flat_ref(kind, eid)
    in_world = _flat_path(wroot_of(cid), kind, eid).exists() and ref not in deleted(cid)
    try:
        entities.delete_entity(croot_of(cid), kind, eid)
        _drop_manifest_ref(cid, ref)
    except entities.EntityNotFound:
        if not in_world:
            raise
    if in_world:
        add_deleted(cid, ref)   # keep the world's copy from showing through
