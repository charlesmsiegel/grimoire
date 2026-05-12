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


def test_parse_setting_scoped_character() -> None:
    ref = parse_library_id("settings/wod-london/characters/winifred")
    assert ref == LibraryRef(
        library_id="settings/wod-london/characters/winifred",
        setting_id="wod-london",
        kind="character",
        asset_id="winifred",
        path_segments=("settings", "wod-london", "characters", "winifred"),
    )


def test_parse_setting_card() -> None:
    ref = parse_library_id("settings/wod-london")
    assert ref.setting_id == "wod-london"
    assert ref.kind == "setting"
    assert ref.asset_id == "wod-london"


def test_parse_top_level_style_guide() -> None:
    ref = parse_library_id("style-guides/gothic")
    assert ref.setting_id is None
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
        parse_library_id("settings")
    with pytest.raises(InvalidRefError):
        parse_library_id("garbage/foo")


def test_library_path_round_trip(tmp_path: Path) -> None:
    p = library_path(tmp_path, "settings/wod-london/characters/winifred")
    assert p == tmp_path / "library/settings/wod-london/characters/winifred.md"

    p2 = library_path(tmp_path, "image-presets/oil")
    assert p2.suffix == ".yaml"

    p3 = library_path(tmp_path, "settings/wod-london")
    assert p3.name == "setting.yaml"


def test_make_library_id_inverse() -> None:
    ids = [
        "settings/wod-london/characters/winifred",
        "style-guides/gothic",
        "image-presets/oil-painting",
    ]
    for library_id in ids:
        ref = parse_library_id(library_id)
        rebuilt = make_library_id(ref.setting_id, ref.kind, ref.asset_id)
        assert rebuilt == library_id


def test_override_path(tmp_path: Path) -> None:
    p = override_path(tmp_path, "c1", "wod-london", "character", "winifred")
    assert p == (tmp_path / "campaigns/c1/overrides/settings/wod-london/characters/winifred.yaml")


def test_emergent_and_sheet_paths(tmp_path: Path) -> None:
    e = emergent_path(tmp_path, "c1", "character", "the-bartender")
    assert e == tmp_path / "campaigns/c1/emergent/characters/the-bartender.md"

    s = sheet_path(tmp_path, "c1", "character", "winifred", "wod")
    assert s == tmp_path / "campaigns/c1/sheets/characters/winifred.wod.yaml"
