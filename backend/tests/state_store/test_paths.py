"""Path parsing + composite-id round-trips."""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.state_store import (
    LibraryRef,
    emergent_path,
    library_path,
    override_path,
    parse_library_id,
    sheet_path,
)
from grimoire.state_store.errors import InvalidRefError
from grimoire.state_store.indexers import make_library_id


def test_parse_world_scoped_character() -> None:
    ref = parse_library_id("worlds/wod-london/characters/winifred")
    assert ref == LibraryRef(
        library_id="worlds/wod-london/characters/winifred",
        world_id="wod-london",
        kind="character",
        asset_id="winifred",
        path_segments=("worlds", "wod-london", "characters", "winifred"),
    )


def test_parse_world_card() -> None:
    ref = parse_library_id("worlds/wod-london")
    assert ref.world_id == "wod-london"
    assert ref.kind == "world"
    assert ref.asset_id == "wod-london"


def test_parse_top_level_style_guide() -> None:
    ref = parse_library_id("style-guides/gothic")
    assert ref.world_id is None
    assert ref.kind == "style_guide"
    assert ref.asset_id == "gothic"


def test_parse_image_preset() -> None:
    ref = parse_library_id("image-presets/oil-painting")
    assert ref.kind == "image_preset"
    assert ref.asset_id == "oil-painting"


def test_parse_malformed_raises() -> None:
    with pytest.raises(InvalidRefError):
        parse_library_id("")
    with pytest.raises(InvalidRefError):
        parse_library_id("worlds")
    with pytest.raises(InvalidRefError):
        parse_library_id("garbage/foo")


def test_library_path_round_trip(tmp_path: Path) -> None:
    p = library_path(tmp_path, "worlds/wod-london/characters/winifred")
    assert p == tmp_path / "library/worlds/wod-london/characters/winifred.md"

    p2 = library_path(tmp_path, "image-presets/oil")
    assert p2.suffix == ".yaml"

    p3 = library_path(tmp_path, "worlds/wod-london")
    assert p3.name == "world.yaml"


def test_make_library_id_inverse() -> None:
    ids = [
        "worlds/wod-london/world",
        "worlds/wod-london/characters/winifred",
        "style-guides/gothic",
        "image-presets/oil-painting",
    ]
    for library_id in ids:
        ref = parse_library_id(library_id)
        rebuilt = make_library_id(ref.world_id, ref.kind, ref.asset_id)
        assert rebuilt == library_id


def test_override_path(tmp_path: Path) -> None:
    p = override_path(tmp_path, "c1", "wod-london", "character", "winifred")
    assert p == (tmp_path / "campaigns/c1/overrides/worlds/wod-london/characters/winifred.yaml")


def test_emergent_and_sheet_paths(tmp_path: Path) -> None:
    e = emergent_path(tmp_path, "c1", "character", "the-bartender")
    assert e == tmp_path / "campaigns/c1/emergent/characters/the-bartender.md"

    s = sheet_path(tmp_path, "c1", "character", "winifred", "wod")
    assert s == tmp_path / "campaigns/c1/sheets/characters/winifred.wod.yaml"


def test_character_variant_paths(tmp_path):
    from grimoire.state_store.paths import character_variant_path, character_variants_dir

    directory = character_variants_dir(tmp_path, "wod-london", "alistair")
    assert directory == (
        tmp_path / "library" / "worlds" / "wod-london" / "characters" / "alistair" / "variants"
    )
    target = character_variant_path(tmp_path, "wod-london", "alistair", "young")
    assert target == directory / "young.md"
    with pytest.raises(InvalidRefError):
        character_variant_path(tmp_path, "wod-london", "alistair", "../escape")
    with pytest.raises(InvalidRefError):
        character_variant_path(tmp_path, "wod-london", "../up", "young")
