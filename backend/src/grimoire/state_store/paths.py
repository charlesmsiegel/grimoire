"""Filesystem path conventions for library and campaign content.

Library content layout (singular ``kind`` → plural directory)::

    data/library/settings/<setting>/characters/<id>.md
    data/library/settings/<setting>/items/<id>.md
    data/library/settings/<setting>/locations/<id>.md
    data/library/settings/<setting>/lore/<id>.md
    data/library/settings/<setting>/factions/<id>.md
    data/library/settings/<setting>/greetings/<id>.md
    data/library/settings/<setting>/setting.yaml
    data/library/style-guides/<id>.md
    data/library/image-presets/<id>.yaml

Campaign content layout::

    data/campaigns/<id>/campaign.yaml
    data/campaigns/<id>/scenes/NNNN-<slug>.{md,yaml}
    data/campaigns/<id>/overrides/settings/<setting>/<kind>/<id>.yaml
    data/campaigns/<id>/emergent/<kind>/<id>.md
    data/campaigns/<id>/sheets/<kind>/<id>.<mechanics-id>.yaml
    data/campaigns/<id>/images/<id>.{png,yaml}

The composite ``library_id`` keys used in ``library_index.id`` look like
``settings/<setting>/<kind>/<id>``, ``style-guides/<id>``, or
``image-presets/<id>``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from grimoire.state_store.errors import InvalidRefError

# Singular kind → plural directory name on disk.
KIND_TO_DIR: dict[str, str] = {
    "character": "characters",
    "item": "items",
    "location": "locations",
    "lore": "lore",
    "faction": "factions",
    "greeting": "greetings",
}

DIR_TO_KIND: dict[str, str] = {v: k for k, v in KIND_TO_DIR.items()}


@dataclass(frozen=True)
class LibraryRef:
    """Parsed view of a ``library_index.id``."""

    library_id: str
    setting_id: str | None  # ``None`` for top-level style-guides / image-presets
    kind: str  # singular: 'character', 'style_guide', 'image_preset', ...
    asset_id: str
    path_segments: tuple[str, ...]  # original path parts as they appear in the id

    @property
    def is_setting_level(self) -> bool:
        return self.setting_id is not None


def _normalize_kind_segment(segment: str) -> str:
    """Translate a directory-style segment ('characters') to a singular kind."""
    if segment in DIR_TO_KIND:
        return DIR_TO_KIND[segment]
    if segment == "style-guides":
        return "style_guide"
    if segment == "image-presets":
        return "image_preset"
    return segment


def parse_library_id(library_id: str) -> LibraryRef:
    """Parse a composite library id into its components.

    Accepts paths of the form ``settings/<setting>/<kind>/<id>``,
    ``style-guides/<id>``, ``image-presets/<id>``, or
    ``settings/<setting>/setting`` (for the setting card itself).
    """
    if not library_id:
        raise InvalidRefError("empty library_id")
    parts = library_id.strip("/").split("/")
    segments = tuple(parts)

    if parts[0] == "settings":
        if len(parts) < 2:
            raise InvalidRefError(f"malformed library_id: {library_id!r}")
        setting_id = parts[1]
        if len(parts) == 2 or (len(parts) == 3 and parts[2] == "setting"):
            # `settings/<setting>` or `settings/<setting>/setting`
            return LibraryRef(
                library_id=library_id,
                setting_id=setting_id,
                kind="setting",
                asset_id=setting_id,
                path_segments=segments,
            )
        if len(parts) < 4:
            raise InvalidRefError(f"malformed library_id: {library_id!r}")
        kind = _normalize_kind_segment(parts[2])
        asset_id = parts[3]
        return LibraryRef(
            library_id=library_id,
            setting_id=setting_id,
            kind=kind,
            asset_id=asset_id,
            path_segments=segments,
        )

    if parts[0] in {"style-guides", "image-presets"}:
        if len(parts) < 2:
            raise InvalidRefError(f"malformed library_id: {library_id!r}")
        return LibraryRef(
            library_id=library_id,
            setting_id=None,
            kind=_normalize_kind_segment(parts[0]),
            asset_id=parts[1],
            path_segments=segments,
        )

    raise InvalidRefError(f"unknown library namespace in {library_id!r}")


def library_root(data_root: Path) -> Path:
    return data_root / "library"


def campaigns_root(data_root: Path) -> Path:
    return data_root / "campaigns"


def library_path(data_root: Path, library_id: str) -> Path:
    """Return the on-disk path for a library entity.

    Characters/items/locations/lore/factions/greetings live as ``.md`` files.
    Style-guides live as ``.md`` files. Image-presets and the setting card
    itself live as ``.yaml`` files.
    """
    ref = parse_library_id(library_id)
    root = library_root(data_root)
    if ref.kind == "setting":
        return root / "settings" / ref.setting_id / "setting.yaml"
    if ref.kind == "image_preset":
        return root / "image-presets" / f"{ref.asset_id}.yaml"
    if ref.kind == "style_guide":
        return root / "style-guides" / f"{ref.asset_id}.md"
    if ref.setting_id is None:
        raise InvalidRefError(f"library kind {ref.kind!r} requires a setting")
    dir_name = KIND_TO_DIR.get(ref.kind)
    if dir_name is None:
        raise InvalidRefError(f"unknown library kind {ref.kind!r}")
    return root / "settings" / ref.setting_id / dir_name / f"{ref.asset_id}.md"


def override_path(
    data_root: Path,
    campaign_id: str,
    setting_id: str,
    kind: str,
    asset_id: str,
) -> Path:
    dir_name = KIND_TO_DIR.get(kind, kind)
    return (
        campaigns_root(data_root)
        / campaign_id
        / "overrides"
        / "settings"
        / setting_id
        / dir_name
        / f"{asset_id}.yaml"
    )


def emergent_path(
    data_root: Path,
    campaign_id: str,
    kind: str,
    asset_id: str,
) -> Path:
    dir_name = KIND_TO_DIR.get(kind, kind)
    return campaigns_root(data_root) / campaign_id / "emergent" / dir_name / f"{asset_id}.md"


def sheet_path(
    data_root: Path,
    campaign_id: str,
    kind: str,
    asset_id: str,
    mechanics_id: str,
) -> Path:
    dir_name = KIND_TO_DIR.get(kind, kind)
    return (
        campaigns_root(data_root)
        / campaign_id
        / "sheets"
        / dir_name
        / f"{asset_id}.{mechanics_id}.yaml"
    )


def image_metadata_path(data_root: Path, campaign_id: str, image_id: str) -> Path:
    return campaigns_root(data_root) / campaign_id / "images" / f"{image_id}.yaml"


def relative_to_root(data_root: Path, path: Path) -> str:
    """Return ``path`` relative to ``data_root`` for storage in indexes."""
    try:
        return str(path.resolve().relative_to(data_root.resolve()))
    except ValueError:
        return str(path)


def campaign_id_for_path(data_root: Path, path: Path) -> str | None:
    """Best-effort: pull the campaign id out of a path under data/campaigns/."""
    try:
        rel = path.resolve().relative_to(campaigns_root(data_root).resolve())
    except ValueError:
        return None
    parts = rel.parts
    return parts[0] if parts else None
