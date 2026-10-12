"""Validated effective addresses for the seven record-art sources.

An address names the user's world or campaign context; an inherited image's
physical root is a separate question answered by overlay. Keeping those apart
lets reuse revalidate campaign visibility without bypassing its tombstones.
Greetings intentionally never become a record-art target.
"""

from dataclasses import dataclass
from pathlib import Path

from .. import (
    assets,
    campaign_images,
    campaigns,
    characters,
    entities,
    image_library,
    overlay,
    paths,
    pcs,
    world_images,
    worlds,
)

ART_RECORD_KINDS = ("characters", pcs.ASSET_BASE, *entities.ENTITY_KINDS)
ACTOR_KINDS = ("characters", pcs.ASSET_BASE)


class ArtTargetNotFoundError(LookupError):
    """An unsupported kind, missing record, or invalid selected version."""


@dataclass(frozen=True)
class Target:
    scope: str
    scope_id: str
    kind: str
    record_id: str
    version: str
    root: Path
    view: overlay.View | None = None

    def dto(self) -> dict:
        value: dict = {"scope": {"kind": self.scope, "id": self.scope_id},
                 "kind": self.kind, "record_id": self.record_id}
        if self.kind in ACTOR_KINDS:
            value["version"] = self.version
        return value


@dataclass(frozen=True)
class Source:
    name: str
    expected_v: str
    dirs: tuple[Path, ...]
    record: Target | None = None
    library_scope: str = ""
    library_id: str = ""
    inherited_only: bool = False


def directories(target: Target) -> tuple[Path, ...]:
    # Detached campaign records never consult their world id-mate. Its
    # pending promotion must neither block this record nor be replayed by it.
    roots = [target.root]
    if target.view and f"{target.kind}/{target.record_id}" not in target.view.off:
        roots.append(target.view.wroot)
    return tuple(assets.version_dir(root, target.record_id, target.version, target.kind) for root in roots)


def _record_source(destination: Target, ref: dict, name: str, expected: str) -> Source:
    address = ref.get("target")
    if not isinstance(address, dict) or address.get("scope") != destination.dto()["scope"]:
        raise ArtTargetNotFoundError("source must be visible in the destination scope")
    if (not isinstance(address.get("kind"), str) or not isinstance(address.get("record_id"), str)
            or (address.get("version") is not None and not isinstance(address["version"], str))):
        raise ArtTargetNotFoundError("invalid source record address")
    target = resolve(destination.scope, destination.scope_id, address.get("kind", ""),
                     address.get("record_id", ""), address.get("version"))
    return Source(name, expected, directories(target), record=target)


def _library_source(destination: Target, ref: dict, name: str, expected: str) -> Source:
    if not image_library.addressable(name):
        raise ArtTargetNotFoundError("source image not found")
    scope = ref.get("scope")
    world = destination.view.wroot.name if destination.view else destination.scope_id
    inherited_only = scope == {"kind": "world", "id": world} and destination.scope == "campaign"
    if scope != destination.dto()["scope"] and not inherited_only:
        raise ArtTargetNotFoundError("source library must be visible in the destination scope")
    dirs: tuple[Path, ...]
    if destination.view:
        dirs = (campaign_images.images_dir(destination.scope_id), world_images.images_dir(world))
    else:
        dirs = (world_images.images_dir(world),)
    return Source(name, expected, dirs, library_scope=destination.scope,
                  library_id=destination.scope_id, inherited_only=inherited_only)


def resolve_source(destination: Target, ref: dict) -> Source:
    """Permit visible placements only, including a campaign's inherited library.

    An inherited record is addressed through this campaign, not by its physical
    world root. Explicit world-library references remain useful to the picker,
    but must still resolve through campaign visibility and cannot name a hidden
    or shadowed world original. Arbitrary image ids and paths authorize nothing.
    """
    name, expected = ref.get("name"), ref.get("expected_v")
    if not isinstance(name, str) or not assets.storable(name) or not isinstance(expected, str) or not expected:
        raise ArtTargetNotFoundError("source placement and image token required")
    if ref.get("surface") == "record":
        return _record_source(destination, ref, name, expected)
    if ref.get("surface") == "library":
        return _library_source(destination, ref, name, expected)
    raise ArtTargetNotFoundError("unsupported art source")


def source_image(source: Source) -> tuple[dict, Path]:
    if source.record:
        target = source.record
        if target.view:
            rows = overlay.list_images(target.scope_id, target.record_id, target.version,
                                       target.kind, v=target.view)
            root = overlay.image_root(target.scope_id, target.record_id, target.version,
                                      source.name, target.kind, v=target.view)
        else:
            rows = assets.list_images(target.root, target.record_id, target.version, target.kind)
            root = target.root
        d = assets.version_dir(root, target.record_id, target.version, target.kind)
    else:
        rows = (campaign_images.list_images(source.library_id) if source.library_scope == "campaign"
                else world_images.list_images(source.library_id))
        d = source.dirs[0]
        inherited = next((r.get("inherited", False) for r in rows if r["name"] == source.name), False)
        if source.inherited_only and not inherited:
            raise ArtTargetNotFoundError("world library image is hidden or shadowed in this campaign")
        if inherited:
            d = source.dirs[-1]
    row = next((r for r in rows if r["name"] == source.name), None)
    if row is None:
        raise ArtTargetNotFoundError("source image not found")
    return row, d


def _version(kind: str, version: str | None) -> str:
    if kind in ACTOR_KINDS:
        if not version or not paths.safe_id(version):
            raise ArtTargetNotFoundError("selected version required")
        return version
    if version is not None:
        raise ArtTargetNotFoundError("entity art has no version modifier")
    return "default"


def resolve(scope: str, scope_id: str, kind: str, rid: str, version: str | None) -> Target:
    if (kind not in ART_RECORD_KINDS or not paths.safe_id(rid)
            or not paths.safe_id(scope_id)):
        raise ArtTargetNotFoundError("record not found")
    vid = _version(kind, version)
    try:
        ov = None
        if scope == "world":
            if not worlds.world_exists(scope_id):
                raise ArtTargetNotFoundError("world not found")
            root = worlds.world_root(scope_id)
            record_root = root
        elif scope == "campaign":
            campaigns.ensure_campaign_slim(scope_id)
            ov = overlay.view(scope_id)
            root = ov.croot
            record_root = (overlay.actor_root(scope_id, kind, rid, v=ov)
                           if kind in ACTOR_KINDS else overlay.entity_root(scope_id, kind, rid))
        else:
            raise ArtTargetNotFoundError("unknown scope")
        if kind == "characters":
            characters.require_version(record_root, rid, vid)
        elif kind == pcs.ASSET_BASE:
            pcs.require_version(record_root, rid, vid)
        else:
            entities.require_entity(record_root, kind, rid)
    except (campaigns.CampaignNotFound, characters.CharacterNotFound,
            characters.VersionNotFound, pcs.PCNotFound, pcs.PCVersionNotFound,
            entities.EntityNotFound) as exc:
        raise ArtTargetNotFoundError(str(exc)) from exc
    return Target(scope, scope_id, kind, rid, vid, root, ov)
