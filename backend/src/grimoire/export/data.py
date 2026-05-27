"""Filesystem-based snapshot of a campaign for export.

Adapters call :func:`load_fs_snapshot` to get a self-contained, read-only view
of the campaign so they don't each have to walk ``data/campaigns/<id>/``
themselves. The snapshot pulls from campaign-local content (scenes,
emergent entities, overrides, images) — library content is referenced by
id but not embedded, matching the spec's default of campaign-centric
exports.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from grimoire.files.frontmatter import parse_frontmatter
from grimoire.files.yaml_io import load_yaml
from grimoire.scenes.storage import read_posts, read_sidecar
from grimoire.scenes.types import Scene


@dataclass(slots=True)
class SceneRecord:
    scene: Scene
    posts: list[tuple[int, str, str | None, str | None, str]] = field(default_factory=list)
    md_path: Path | None = None
    yaml_path: Path | None = None

    @property
    def post_count(self) -> int:
        return len(self.posts)


@dataclass(slots=True)
class EntityCard:
    """A markdown entity card (frontmatter + body) plus its origin path."""

    kind: str
    asset_id: str
    frontmatter: dict[str, Any]
    body: str
    path: Path
    scope: str = "emergent"  # emergent | override

    @property
    def name(self) -> str:
        name = self.frontmatter.get("name")
        if isinstance(name, str) and name.strip():
            return name
        return self.asset_id


@dataclass(slots=True)
class CharacterCard(EntityCard):
    pass


@dataclass(slots=True)
class ImageRecord:
    image_id: str
    metadata: dict[str, Any]
    yaml_path: Path
    image_path: Path | None


@dataclass(slots=True)
class FsCampaignSnapshot:
    campaign_id: str
    campaign_yaml: dict[str, Any]
    scenes: list[SceneRecord]
    characters: list[CharacterCard]
    locations: list[EntityCard]
    lore: list[EntityCard]
    factions: list[EntityCard]
    items: list[EntityCard]
    greetings: list[EntityCard]
    images: list[ImageRecord]

    @property
    def title(self) -> str:
        title = self.campaign_yaml.get("title")
        if isinstance(title, str) and title.strip():
            return title
        return self.campaign_id


_ENTITY_DIRS: dict[str, str] = {
    "characters": "characters",
    "locations": "locations",
    "lore": "lore",
    "factions": "factions",
    "items": "items",
    "monsters": "monsters",
    "greetings": "greetings",
}


def _scenes_dir(data_root: Path, campaign_id: str) -> Path:
    return data_root / "campaigns" / campaign_id / "scenes"


def _read_campaign_yaml(data_root: Path, campaign_id: str) -> dict[str, Any]:
    path = data_root / "campaigns" / campaign_id / "campaign.yaml"
    if not path.is_file():
        return {}
    try:
        raw = load_yaml(path)
    except Exception:
        return {}
    return dict(raw) if isinstance(raw, dict) else {}


def _load_scenes(data_root: Path, campaign_id: str) -> list[SceneRecord]:
    directory = _scenes_dir(data_root, campaign_id)
    if not directory.is_dir():
        return []
    records: list[SceneRecord] = []
    for yaml_path in sorted(directory.glob("*.yaml")):
        try:
            scene = read_sidecar(yaml_path)
        except Exception:
            continue
        md_path = yaml_path.with_suffix(".md")
        posts = read_posts(md_path, scene.id) if md_path.is_file() else []
        records.append(
            SceneRecord(
                scene=scene,
                posts=posts,
                md_path=md_path if md_path.is_file() else None,
                yaml_path=yaml_path,
            )
        )
    records.sort(key=lambda r: r.scene.ordinal)
    return records


def _read_card(path: Path, kind: str, scope: str) -> EntityCard | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        parsed = parse_frontmatter(text)
    except Exception:
        return None
    asset_id = path.stem
    cls = CharacterCard if kind == "characters" else EntityCard
    return cls(
        kind=kind,
        asset_id=asset_id,
        frontmatter=dict(parsed.frontmatter or {}),
        body=parsed.body or "",
        path=path,
        scope=scope,
    )


def _load_emergent(data_root: Path, campaign_id: str, kind: str) -> list[EntityCard]:
    dir_name = _ENTITY_DIRS.get(kind, kind)
    directory = data_root / "campaigns" / campaign_id / "emergent" / dir_name
    if not directory.is_dir():
        return []
    cards: list[EntityCard] = []
    for path in sorted(directory.glob("*.md")):
        card = _read_card(path, kind, scope="emergent")
        if card is not None:
            cards.append(card)
    return cards


def _load_overrides(data_root: Path, campaign_id: str, kind: str) -> list[EntityCard]:
    dir_name = _ENTITY_DIRS.get(kind, kind)
    base = data_root / "campaigns" / campaign_id / "overrides" / "worlds"
    if not base.is_dir():
        return []
    cards: list[EntityCard] = []
    for world_dir in sorted(p for p in base.iterdir() if p.is_dir()):
        kind_dir = world_dir / dir_name
        if not kind_dir.is_dir():
            continue
        for path in sorted(kind_dir.glob("*.yaml")):
            try:
                raw = load_yaml(path)
            except Exception:
                continue
            if not isinstance(raw, dict):
                continue
            body = raw.pop("body", "") if isinstance(raw.get("body"), str) else ""
            asset_id = path.stem
            cls = CharacterCard if kind == "characters" else EntityCard
            cards.append(
                cls(
                    kind=kind,
                    asset_id=asset_id,
                    frontmatter=dict(raw),
                    body=body,
                    path=path,
                    scope="override",
                )
            )
    return cards


def _load_kind(data_root: Path, campaign_id: str, kind: str) -> list[EntityCard]:
    return _load_emergent(data_root, campaign_id, kind) + _load_overrides(
        data_root, campaign_id, kind
    )


def _load_images(data_root: Path, campaign_id: str) -> list[ImageRecord]:
    directory = data_root / "campaigns" / campaign_id / "images"
    if not directory.is_dir():
        return []
    records: list[ImageRecord] = []
    for yaml_path in sorted(directory.glob("*.yaml")):
        try:
            raw = load_yaml(yaml_path)
        except Exception:
            continue
        if not isinstance(raw, dict):
            continue
        image_id = yaml_path.stem
        png = yaml_path.with_suffix(".png")
        records.append(
            ImageRecord(
                image_id=image_id,
                metadata=dict(raw),
                yaml_path=yaml_path,
                image_path=png if png.is_file() else None,
            )
        )
    return records


def load_fs_snapshot(
    data_root: Path,
    campaign_id: str,
) -> FsCampaignSnapshot:
    """Walk ``data/campaigns/<id>/`` and return a structured snapshot.

    Missing files and directories are tolerated — adapters export what's
    actually on disk and emit warnings about gaps where appropriate.
    """
    data_root = Path(data_root)
    return FsCampaignSnapshot(
        campaign_id=campaign_id,
        campaign_yaml=_read_campaign_yaml(data_root, campaign_id),
        scenes=_load_scenes(data_root, campaign_id),
        characters=[
            c
            for c in _load_kind(data_root, campaign_id, "characters")
            if isinstance(c, CharacterCard)
        ],
        locations=_load_kind(data_root, campaign_id, "locations"),
        lore=_load_kind(data_root, campaign_id, "lore"),
        factions=_load_kind(data_root, campaign_id, "factions"),
        items=_load_kind(data_root, campaign_id, "items"),
        greetings=_load_kind(data_root, campaign_id, "greetings"),
        images=_load_images(data_root, campaign_id),
    )


__all__ = [
    "CharacterCard",
    "EntityCard",
    "FsCampaignSnapshot",
    "ImageRecord",
    "SceneRecord",
    "load_fs_snapshot",
]
