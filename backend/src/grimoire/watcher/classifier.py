"""Path classification for the filesystem watcher.

Given a path under ``data/library/`` or ``data/campaigns/<id>/``, decide which
SQLite index (if any) needs updating and which event type to emit. Returns
``None`` for paths outside the watched roots or for filenames that don't match
the conventions defined in spec 03 / spec 18.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from grimoire import events
from grimoire.state_store.paths import DIR_TO_KIND

# Map a watched-file ``kind`` to the bus event type the watcher emits.
EVENT_TYPE_BY_KIND: dict[str, str] = {
    "library_entity": events.LIBRARY_FILE_CHANGED,
    "library_character_variant": events.LIBRARY_FILE_CHANGED,
    "library_world": events.LIBRARY_FILE_CHANGED,
    "library_style_guide": events.LIBRARY_FILE_CHANGED,
    "library_image_preset": events.LIBRARY_FILE_CHANGED,
    "scene_body": events.SCENE_FILE_CHANGED,
    "scene_sidecar": events.SCENE_FILE_CHANGED,
    "sheet": events.SHEET_FILE_CHANGED,
    "override": events.CAMPAIGN_FILE_CHANGED,
    "emergent": events.CAMPAIGN_FILE_CHANGED,
    "image_metadata": events.CAMPAIGN_FILE_CHANGED,
    "image_asset": events.CAMPAIGN_FILE_CHANGED,
    "campaign_config": events.CAMPAIGN_FILE_CHANGED,
}

# Flat single-file library assets stored as ``<head>/<asset_id>.<ext>``. They
# differ only in directory, file extension, and the kind/entity_kind they map
# to, so they're table-driven rather than copy-pasted one ``if`` per directory.
_FLAT_LIBRARY_ASSETS: dict[str, tuple[str, str, str]] = {
    # Each value is the extension, kind, entity_kind for that directory.
    "style-guides": (".md", "library_style_guide", "style_guide"),
    "image-presets": (".yaml", "library_image_preset", "image_preset"),
    "calendars": (".yaml", "library_calendar", "calendar"),
    "holiday-sets": (".yaml", "library_holiday_set", "holiday_set"),
}


@dataclass(frozen=True, slots=True)
class WatchedFile:
    """A classified path. ``kind`` selects the index update + event type."""

    scope: str  # "library" | "campaign"
    kind: str
    path: Path
    library_id: str | None = None
    campaign_id: str | None = None
    world_id: str | None = None
    entity_kind: str | None = None
    asset_id: str | None = None
    mechanics_id: str | None = None
    scene_basename: str | None = None
    image_id: str | None = None
    variant_of: str | None = None  # base character asset id (character variants)

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
        # Directory-form character card: worlds/<w>/characters/<id>/card.md
        # Sibling avatar.png + sprites/*.png under the same directory are
        # data assets, not entities — they're ignored here.
        if len(parts) == 5 and parts[2] == "characters" and parts[4] == "card.md":
            world_id = parts[1]
            asset_id = parts[3]
            return WatchedFile(
                scope="library",
                kind="library_entity",
                path=abs_path,
                world_id=world_id,
                entity_kind="character",
                asset_id=asset_id,
                library_id=f"worlds/{world_id}/characters/{asset_id}",
            )
        # In-world character variant overlay:
        # worlds/<w>/characters/<base>/variants/<variant-id>.md. Variants are
        # leaf files of the base character and never enter library_index —
        # library_id stays None so the watcher only emits a change event
        # (cache invalidation, UI refresh) without touching the index.
        if (
            len(parts) == 6
            and parts[2] == "characters"
            and parts[4] == "variants"
            and parts[5].endswith(".md")
        ):
            world_id = parts[1]
            return WatchedFile(
                scope="library",
                kind="library_character_variant",
                path=abs_path,
                world_id=world_id,
                entity_kind="character_variant",
                asset_id=parts[5][:-3],
                variant_of=parts[3],
            )
        return None

    flat = _FLAT_LIBRARY_ASSETS.get(head)
    if flat is not None:
        extension, kind, entity_kind = flat
        if len(parts) == 2 and parts[1].endswith(extension):
            asset_id = parts[1][: -len(extension)]
            return WatchedFile(
                scope="library",
                kind=kind,
                path=abs_path,
                entity_kind=entity_kind,
                asset_id=asset_id,
                library_id=f"{head}/{asset_id}",
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
        return _classify_scene(abs_path, campaign_id, rest)

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
            scene_basename=name[:-3],
        )
    if name.endswith(".yaml"):
        return WatchedFile(
            scope="campaign",
            kind="scene_sidecar",
            path=abs_path,
            campaign_id=campaign_id,
            scene_basename=name[:-5],
        )
    return None
