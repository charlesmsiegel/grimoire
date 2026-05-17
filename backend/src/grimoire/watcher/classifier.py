"""Path classification for the filesystem watcher.

Given a path under ``data/library/`` or ``data/campaigns/<id>/``, decide which
SQLite index (if any) needs updating and which event type to emit. Returns
``None`` for paths outside the watched roots or for filenames that don't match
the conventions defined in spec 03 / spec 18.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from grimoire.scenes.storage import _from_safe_segment
from grimoire.state_store.paths import DIR_TO_KIND

# Map a watched-file ``kind`` to the bus event type the watcher emits.
EVENT_TYPE_BY_KIND: dict[str, str] = {
    "library_entity": "library_file_changed",
    "library_world": "library_file_changed",
    "library_style_guide": "library_file_changed",
    "library_image_preset": "library_file_changed",
    "scene_body": "scene_file_changed",
    "scene_sidecar": "scene_file_changed",
    "sheet": "sheet_file_changed",
    "override": "campaign_file_changed",
    "emergent": "campaign_file_changed",
    "image_metadata": "campaign_file_changed",
    "image_asset": "campaign_file_changed",
    "campaign_config": "campaign_file_changed",
}


@dataclass(frozen=True, slots=True)
class WatchedFile:
    """A classified path. ``kind`` selects the index update + event type."""

    scope: str  # "library" | "campaign"
    kind: str
    path: Path
    library_id: str | None = None
    campaign_id: str | None = None
    branch_id: str | None = None
    world_id: str | None = None
    entity_kind: str | None = None
    asset_id: str | None = None
    mechanics_id: str | None = None
    scene_basename: str | None = None
    image_id: str | None = None

    @property
    def event_type(self) -> str:
        return EVENT_TYPE_BY_KIND[self.kind]

    @property
    def content_index_id(self) -> str | None:
        """Composite id used in ``campaign_content_index`` for this file (if any)."""
        if self.kind == "override":
            dir_name = _kind_to_dir(self.entity_kind or "")
            return (
                f"campaigns/{self.campaign_id}/overrides/worlds/"
                f"{self.world_id}/{dir_name}/{self.asset_id}"
            )
        if self.kind == "emergent":
            dir_name = _kind_to_dir(self.entity_kind or "")
            return f"campaigns/{self.campaign_id}/emergent/{dir_name}/{self.asset_id}"
        if self.kind == "sheet":
            return (
                f"campaigns/{self.campaign_id}/sheets/"
                f"{self.entity_kind}/{self.asset_id}.{self.mechanics_id}"
            )
        if self.kind == "image_metadata":
            return f"campaigns/{self.campaign_id}/images/{self.image_id}"
        return None


def _kind_to_dir(kind: str) -> str:
    # Inverse of DIR_TO_KIND; falls back to identity for unknown kinds so the
    # composite id stays stable even when callers pass through directory names
    # that aren't in the singular-kind table.
    from grimoire.state_store.paths import KIND_TO_DIR

    return KIND_TO_DIR.get(kind, kind)


def classify_path(data_root: Path, path: Path) -> WatchedFile | None:
    """Return a :class:`WatchedFile` for ``path`` or ``None`` if uninteresting."""
    data_root = Path(data_root).resolve()
    try:
        abs_path = Path(path).resolve()
    except OSError:
        return None

    library_root = data_root / "library"
    campaigns_root = data_root / "campaigns"

    try:
        rel = abs_path.relative_to(library_root)
    except ValueError:
        rel = None
    if rel is not None:
        return _classify_library(abs_path, rel)

    try:
        rel = abs_path.relative_to(campaigns_root)
    except ValueError:
        return None
    return _classify_campaign(abs_path, rel)


def _classify_library(abs_path: Path, rel: Path) -> WatchedFile | None:
    parts = rel.parts
    if not parts:
        return None
    head = parts[0]

    if head == "worlds":
        if len(parts) == 3 and parts[2] == "world.yaml":
            world_id = parts[1]
            return WatchedFile(
                scope="library",
                kind="library_world",
                path=abs_path,
                world_id=world_id,
                entity_kind="world",
                asset_id=world_id,
                library_id=f"worlds/{world_id}/world",
            )
        if len(parts) == 4:
            world_id = parts[1]
            dir_name = parts[2]
            entity_kind = DIR_TO_KIND.get(dir_name)
            if entity_kind is None:
                return None
            filename = parts[3]
            if not filename.endswith(".md"):
                return None
            asset_id = filename[:-3]
            return WatchedFile(
                scope="library",
                kind="library_entity",
                path=abs_path,
                world_id=world_id,
                entity_kind=entity_kind,
                asset_id=asset_id,
                library_id=f"worlds/{world_id}/{dir_name}/{asset_id}",
            )
        return None

    if head == "style-guides":
        if len(parts) == 2 and parts[1].endswith(".md"):
            asset_id = parts[1][:-3]
            return WatchedFile(
                scope="library",
                kind="library_style_guide",
                path=abs_path,
                entity_kind="style_guide",
                asset_id=asset_id,
                library_id=f"style-guides/{asset_id}",
            )
        return None

    if head == "image-presets":
        if len(parts) == 2 and parts[1].endswith(".yaml"):
            asset_id = parts[1][:-5]
            return WatchedFile(
                scope="library",
                kind="library_image_preset",
                path=abs_path,
                entity_kind="image_preset",
                asset_id=asset_id,
                library_id=f"image-presets/{asset_id}",
            )
        return None

    return None


def _classify_campaign(abs_path: Path, rel: Path) -> WatchedFile | None:
    parts = rel.parts
    if len(parts) < 2:
        return None
    campaign_id = parts[0]
    sub = parts[1]
    rest = parts[2:]

    if len(parts) == 2 and sub == "campaign.yaml":
        return WatchedFile(
            scope="campaign",
            kind="campaign_config",
            path=abs_path,
            campaign_id=campaign_id,
        )

    if sub == "scenes" and rest:
        return _classify_scene(abs_path, campaign_id, "main", rest)

    if sub == "branches" and len(rest) >= 3 and rest[1] == "scenes":
        branch_id = _from_safe_segment(rest[0])
        return _classify_scene(abs_path, campaign_id, branch_id, rest[2:])

    if sub == "overrides" and len(rest) == 4 and rest[0] == "worlds":
        world_id = rest[1]
        dir_name = rest[2]
        filename = rest[3]
        if not filename.endswith(".yaml"):
            return None
        entity_kind = DIR_TO_KIND.get(dir_name) or dir_name
        asset_id = filename[:-5]
        return WatchedFile(
            scope="campaign",
            kind="override",
            path=abs_path,
            campaign_id=campaign_id,
            world_id=world_id,
            entity_kind=entity_kind,
            asset_id=asset_id,
            library_id=f"worlds/{world_id}/{dir_name}/{asset_id}",
        )

    if sub == "emergent" and len(rest) == 2:
        dir_name = rest[0]
        filename = rest[1]
        if not filename.endswith(".md"):
            return None
        entity_kind = DIR_TO_KIND.get(dir_name) or dir_name
        asset_id = filename[:-3]
        return WatchedFile(
            scope="campaign",
            kind="emergent",
            path=abs_path,
            campaign_id=campaign_id,
            entity_kind=entity_kind,
            asset_id=asset_id,
        )

    if sub == "sheets" and len(rest) == 2:
        dir_name = rest[0]
        filename = rest[1]
        if not filename.endswith(".yaml"):
            return None
        entity_kind = DIR_TO_KIND.get(dir_name) or dir_name
        base = filename[:-5]
        if "." not in base:
            return None
        asset_id, mech_id = base.rsplit(".", 1)
        if not mech_id:
            return None
        return WatchedFile(
            scope="campaign",
            kind="sheet",
            path=abs_path,
            campaign_id=campaign_id,
            entity_kind=entity_kind,
            asset_id=asset_id,
            mechanics_id=mech_id,
        )

    if sub == "images" and len(rest) == 1:
        filename = rest[0]
        if filename.endswith(".yaml"):
            return WatchedFile(
                scope="campaign",
                kind="image_metadata",
                path=abs_path,
                campaign_id=campaign_id,
                image_id=filename[:-5],
            )
        if filename.endswith(".png"):
            return WatchedFile(
                scope="campaign",
                kind="image_asset",
                path=abs_path,
                campaign_id=campaign_id,
                image_id=filename[:-4],
            )
        return None

    return None


def _classify_scene(
    abs_path: Path,
    campaign_id: str,
    branch_id: str,
    rest: tuple[str, ...],
) -> WatchedFile | None:
    if len(rest) != 1:
        return None
    name = rest[0]
    if name.endswith(".md"):
        return WatchedFile(
            scope="campaign",
            kind="scene_body",
            path=abs_path,
            campaign_id=campaign_id,
            branch_id=branch_id,
            scene_basename=name[:-3],
        )
    if name.endswith(".yaml"):
        return WatchedFile(
            scope="campaign",
            kind="scene_sidecar",
            path=abs_path,
            campaign_id=campaign_id,
            branch_id=branch_id,
            scene_basename=name[:-5],
        )
    return None
