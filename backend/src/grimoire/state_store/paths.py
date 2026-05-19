"""Filesystem path conventions for library and campaign content.

Library content layout (singular ``kind`` → plural directory)::

    data/library/worlds/<world>/characters/<id>.md
    data/library/worlds/<world>/items/<id>.md
    data/library/worlds/<world>/locations/<id>.md
    data/library/worlds/<world>/lore/<id>.md
    data/library/worlds/<world>/factions/<id>.md
    data/library/worlds/<world>/greetings/<id>.md
    data/library/worlds/<world>/world.yaml
    data/library/style-guides/<id>.md
    data/library/image-presets/<id>.yaml

Campaign content layout::

    data/campaigns/<id>/campaign.yaml
    data/campaigns/<id>/scenes/NNNN-<slug>.{md,yaml}
    data/campaigns/<id>/overrides/worlds/<world>/<kind>/<id>.yaml
    data/campaigns/<id>/emergent/<kind>/<id>.md
    data/campaigns/<id>/sheets/<kind>/<id>.<mechanics-id>.yaml
    data/campaigns/<id>/images/<id>.{png,yaml}

The composite ``library_id`` keys used in ``library_index.id`` look like
``worlds/<world>/<kind>/<id>``, ``style-guides/<id>``, or
``image-presets/<id>``.
"""

from __future__ import annotations

import re
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

# Allowlist of safe characters for any id that becomes a filesystem path
# component (campaign_id, world_id, asset_id, kind, mechanics_id, image_id).
# Must start with an alphanumeric so a leading "." can't produce a dotfile,
# and must not contain "/", "\", or null bytes — which together with the
# leading-alnum rule also rules out "..".
_SAFE_COMPONENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def validate_path_component(value: str, *, name: str) -> str:
    """Reject ids that would let untrusted input escape the data root.

    Every helper in this module that interpolates an id into a Path runs
    its variable components through this guard. Returns ``value`` so call
    sites can inline the check.
    """
    if not isinstance(value, str) or not _SAFE_COMPONENT_RE.match(value):
        raise InvalidRefError(f"unsafe {name}: {value!r}")
    return value


@dataclass(frozen=True)
class LibraryRef:
    """Parsed view of a ``library_index.id``."""

    library_id: str
    world_id: str | None  # ``None`` for top-level style-guides / image-presets
    kind: str  # singular: 'character', 'style_guide', 'image_preset', ...
    asset_id: str
    path_segments: tuple[str, ...]  # original path parts as they appear in the id

    @property
    def is_world_level(self) -> bool:
        return self.world_id is not None


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

    Accepts paths of the form ``worlds/<world>/<kind>/<id>``,
    ``style-guides/<id>``, ``image-presets/<id>``, or
    ``worlds/<world>/world`` (for the world card itself).
    """
    if not library_id:
        raise InvalidRefError("empty library_id")
    parts = library_id.strip("/").split("/")
    segments = tuple(parts)

    if parts[0] == "worlds":
        if len(parts) < 2:
            raise InvalidRefError(f"malformed library_id: {library_id!r}")
        world_id = validate_path_component(parts[1], name="world_id")
        if len(parts) == 2 or (len(parts) == 3 and parts[2] == "world"):
            # `worlds/<world>` or `worlds/<world>/world`
            return LibraryRef(
                library_id=library_id,
                world_id=world_id,
                kind="world",
                asset_id=world_id,
                path_segments=segments,
            )
        if len(parts) < 4:
            raise InvalidRefError(f"malformed library_id: {library_id!r}")
        kind = _normalize_kind_segment(parts[2])
        asset_id = validate_path_component(parts[3], name="asset_id")
        return LibraryRef(
            library_id=library_id,
            world_id=world_id,
            kind=kind,
            asset_id=asset_id,
            path_segments=segments,
        )

    if parts[0] in {"style-guides", "image-presets"}:
        if len(parts) < 2:
            raise InvalidRefError(f"malformed library_id: {library_id!r}")
        return LibraryRef(
            library_id=library_id,
            world_id=None,
            kind=_normalize_kind_segment(parts[0]),
            asset_id=validate_path_component(parts[1], name="asset_id"),
            path_segments=segments,
        )

    raise InvalidRefError(f"unknown library namespace in {library_id!r}")


def library_root(data_root: Path) -> Path:
    return data_root / "library"


def campaigns_root(data_root: Path) -> Path:
    return data_root / "campaigns"


@dataclass(frozen=True)
class CharacterLayout:
    """Resolved on-disk layout for a character card.

    Characters may live as flat ``characters/<id>.md`` files (no sprites)
    or as a directory ``characters/<id>/`` containing ``card.md`` plus
    optional ``avatar.png`` / ``sprites/`` siblings. Both shapes are
    indexed identically; the layout drives sprite resolution.
    """

    asset_id: str
    form: str  # "flat" | "directory" | "missing"
    card_md: Path
    avatar: Path | None
    sprites_dir: Path | None


def character_dir_layout(world_id: str, asset_id: str, *, data_root: Path) -> CharacterLayout:
    """Return the on-disk layout for a character.

    ``data_root`` is the data root (``settings.data_root``) — the directory
    that contains ``library/`` and ``campaigns/``. Existence of the flat-form
    file or the directory determines the layout; if neither exists, the
    result returns ``form="missing"`` with paths pointing at where the
    directory form would live.
    """
    validate_path_component(world_id, name="world_id")
    validate_path_component(asset_id, name="asset_id")
    base = library_root(data_root) / "worlds" / world_id / "characters"
    flat = base / f"{asset_id}.md"
    directory = base / asset_id
    if flat.exists():
        return CharacterLayout(
            asset_id=asset_id,
            form="flat",
            card_md=flat,
            avatar=None,
            sprites_dir=None,
        )
    card = directory / "card.md"
    avatar = directory / "avatar.png"
    sprites = directory / "sprites"
    if card.exists():
        return CharacterLayout(
            asset_id=asset_id,
            form="directory",
            card_md=card,
            avatar=avatar if avatar.exists() else None,
            sprites_dir=sprites if sprites.is_dir() else None,
        )
    return CharacterLayout(
        asset_id=asset_id,
        form="missing",
        card_md=card,
        avatar=None,
        sprites_dir=None,
    )


def library_path(data_root: Path, library_id: str) -> Path:
    """Return the on-disk path for a library entity.

    Characters/items/locations/lore/factions/greetings live as ``.md`` files.
    Style-guides live as ``.md`` files. Image-presets and the world card
    itself live as ``.yaml`` files.
    """
    ref = parse_library_id(library_id)
    root = library_root(data_root)
    if ref.kind == "world":
        return root / "worlds" / ref.world_id / "world.yaml"
    if ref.kind == "image_preset":
        return root / "image-presets" / f"{ref.asset_id}.yaml"
    if ref.kind == "style_guide":
        return root / "style-guides" / f"{ref.asset_id}.md"
    if ref.world_id is None:
        raise InvalidRefError(f"library kind {ref.kind!r} requires a world")
    dir_name = KIND_TO_DIR.get(ref.kind)
    if dir_name is None:
        raise InvalidRefError(f"unknown library kind {ref.kind!r}")
    # Characters may live in directory form (characters/<id>/card.md);
    # prefer that shape when present so the resolver picks up the
    # sprite-bearing card. Other kinds remain flat-only.
    if ref.kind == "character":
        directory = root / "worlds" / ref.world_id / dir_name / ref.asset_id / "card.md"
        if directory.exists():
            return directory
    return root / "worlds" / ref.world_id / dir_name / f"{ref.asset_id}.md"


def override_path(
    data_root: Path,
    campaign_id: str,
    world_id: str,
    kind: str,
    asset_id: str,
) -> Path:
    validate_path_component(campaign_id, name="campaign_id")
    validate_path_component(world_id, name="world_id")
    validate_path_component(asset_id, name="asset_id")
    dir_name = KIND_TO_DIR.get(kind, kind)
    validate_path_component(dir_name, name="kind")
    return (
        campaigns_root(data_root)
        / campaign_id
        / "overrides"
        / "worlds"
        / world_id
        / dir_name
        / f"{asset_id}.yaml"
    )


def emergent_path(
    data_root: Path,
    campaign_id: str,
    kind: str,
    asset_id: str,
) -> Path:
    validate_path_component(campaign_id, name="campaign_id")
    validate_path_component(asset_id, name="asset_id")
    dir_name = KIND_TO_DIR.get(kind, kind)
    validate_path_component(dir_name, name="kind")
    return campaigns_root(data_root) / campaign_id / "emergent" / dir_name / f"{asset_id}.md"


def sheet_path(
    data_root: Path,
    campaign_id: str,
    kind: str,
    asset_id: str,
    mechanics_id: str,
) -> Path:
    validate_path_component(campaign_id, name="campaign_id")
    validate_path_component(asset_id, name="asset_id")
    validate_path_component(mechanics_id, name="mechanics_id")
    dir_name = KIND_TO_DIR.get(kind, kind)
    validate_path_component(dir_name, name="kind")
    return (
        campaigns_root(data_root)
        / campaign_id
        / "sheets"
        / dir_name
        / f"{asset_id}.{mechanics_id}.yaml"
    )


def content_path(
    data_root: Path,
    campaign_id: str,
    kind: str,
    content_id: str,
    mechanics_id: str,
) -> Path:
    """Return the on-disk path for a mechanics content instance.

    Mirrors :func:`sheet_path`. ``kind`` is taken verbatim as the directory
    name (content kinds are module-defined, not in ``KIND_TO_DIR``).
    """
    validate_path_component(campaign_id, name="campaign_id")
    validate_path_component(content_id, name="content_id")
    validate_path_component(mechanics_id, name="mechanics_id")
    validate_path_component(kind, name="kind")
    return (
        campaigns_root(data_root)
        / campaign_id
        / "content"
        / kind
        / f"{content_id}.{mechanics_id}.yaml"
    )


def image_metadata_path(data_root: Path, campaign_id: str, image_id: str) -> Path:
    validate_path_component(campaign_id, name="campaign_id")
    validate_path_component(image_id, name="image_id")
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
