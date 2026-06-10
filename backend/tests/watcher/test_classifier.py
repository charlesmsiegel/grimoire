"""Path classification covers every layout in spec 03 / spec 18."""

from __future__ import annotations

from pathlib import Path

from grimoire.watcher import classify_path


def _root(tmp_path: Path) -> Path:
    (tmp_path / "library").mkdir(parents=True, exist_ok=True)
    (tmp_path / "campaigns").mkdir(parents=True, exist_ok=True)
    return tmp_path


def test_library_entity_path(tmp_path: Path) -> None:
    root = _root(tmp_path)
    target = root / "library" / "worlds" / "wod-london" / "characters" / "winifred.md"
    target.parent.mkdir(parents=True)
    target.touch()
    w = classify_path(root, target)
    assert w is not None
    assert w.scope == "library"
    assert w.kind == "library_entity"
    assert w.library_id == "worlds/wod-london/characters/winifred"
    assert w.entity_kind == "character"
    assert w.world_id == "wod-london"
    assert w.event_type == "library_file_changed"


def test_library_world_yaml(tmp_path: Path) -> None:
    root = _root(tmp_path)
    target = root / "library" / "worlds" / "wod-london" / "world.yaml"
    target.parent.mkdir(parents=True)
    target.touch()
    w = classify_path(root, target)
    assert w is not None
    assert w.kind == "library_world"
    assert w.library_id == "worlds/wod-london/world"
    assert w.world_id == "wod-london"


def test_style_guide_and_image_preset(tmp_path: Path) -> None:
    root = _root(tmp_path)
    sg = root / "library" / "style-guides" / "noir.md"
    sg.parent.mkdir(parents=True)
    sg.touch()
    ip = root / "library" / "image-presets" / "oil-painting.yaml"
    ip.parent.mkdir(parents=True)
    ip.touch()

    w1 = classify_path(root, sg)
    assert w1 is not None and w1.kind == "library_style_guide"
    assert w1.library_id == "style-guides/noir"

    w2 = classify_path(root, ip)
    assert w2 is not None and w2.kind == "library_image_preset"
    assert w2.library_id == "image-presets/oil-painting"


def test_scene_files(tmp_path: Path) -> None:
    root = _root(tmp_path)
    md = root / "campaigns" / "c1" / "scenes" / "0001-elysium-opening.md"
    yaml = md.with_suffix(".yaml")
    md.parent.mkdir(parents=True)
    md.touch()
    yaml.touch()

    w1 = classify_path(root, md)
    assert w1 is not None
    assert w1.kind == "scene_body"
    assert w1.event_type == "scene_file_changed"
    assert w1.campaign_id == "c1"
    assert w1.scene_basename == "0001-elysium-opening"

    w2 = classify_path(root, yaml)
    assert w2 is not None
    assert w2.kind == "scene_sidecar"


def test_override_emergent_sheet_image(tmp_path: Path) -> None:
    root = _root(tmp_path)
    override = (
        root
        / "campaigns"
        / "c1"
        / "overrides"
        / "worlds"
        / "wod-london"
        / "characters"
        / "winifred.yaml"
    )
    override.parent.mkdir(parents=True)
    override.touch()
    emergent = root / "campaigns" / "c1" / "emergent" / "characters" / "stranger.md"
    emergent.parent.mkdir(parents=True)
    emergent.touch()
    sheet = root / "campaigns" / "c1" / "sheets" / "characters" / "winifred.wod.yaml"
    sheet.parent.mkdir(parents=True)
    sheet.touch()
    img = root / "campaigns" / "c1" / "images" / "abc123.yaml"
    img.parent.mkdir(parents=True)
    img.touch()

    w = classify_path(root, override)
    assert w is not None and w.kind == "override"
    assert w.event_type == "campaign_file_changed"
    assert w.content_index_id.endswith("winifred")
    assert w.library_id == "worlds/wod-london/characters/winifred"

    w = classify_path(root, emergent)
    assert w is not None and w.kind == "emergent"
    assert w.entity_kind == "character"
    assert w.asset_id == "stranger"

    w = classify_path(root, sheet)
    assert w is not None and w.kind == "sheet"
    assert w.event_type == "sheet_file_changed"
    assert w.asset_id == "winifred"
    assert w.mechanics_id == "wod"

    w = classify_path(root, img)
    assert w is not None and w.kind == "image_metadata"
    assert w.image_id == "abc123"


def test_campaign_config(tmp_path: Path) -> None:
    root = _root(tmp_path)
    cfg = root / "campaigns" / "c1" / "campaign.yaml"
    cfg.parent.mkdir(parents=True)
    cfg.touch()
    w = classify_path(root, cfg)
    assert w is not None and w.kind == "campaign_config"
    assert w.event_type == "campaign_file_changed"


def test_paths_outside_roots_return_none(tmp_path: Path) -> None:
    root = _root(tmp_path)
    outside = tmp_path / "other" / "thing.md"
    outside.parent.mkdir(parents=True)
    outside.touch()
    assert classify_path(root, outside) is None


def test_unknown_filename_returns_none(tmp_path: Path) -> None:
    root = _root(tmp_path)
    odd = root / "library" / "worlds" / "wod-london" / "characters" / "notes.txt"
    odd.parent.mkdir(parents=True)
    odd.touch()
    assert classify_path(root, odd) is None


def test_character_variant_path(tmp_path: Path) -> None:
    """Variant overlays classify for change events but stay out of the index
    (library_id is None so the watcher never upserts library_index)."""
    root = _root(tmp_path)
    target = (
        root
        / "library"
        / "worlds"
        / "wod-london"
        / "characters"
        / "winifred"
        / "variants"
        / "young.md"
    )
    target.parent.mkdir(parents=True)
    target.touch()
    w = classify_path(root, target)
    assert w is not None
    assert w.scope == "library"
    assert w.kind == "library_character_variant"
    assert w.library_id is None
    assert w.entity_kind == "character_variant"
    assert w.asset_id == "young"
    assert w.variant_of == "winifred"
    assert w.world_id == "wod-london"
    assert w.event_type == "library_file_changed"


def test_character_variant_non_md_ignored(tmp_path: Path) -> None:
    root = _root(tmp_path)
    target = (
        root
        / "library"
        / "worlds"
        / "wod-london"
        / "characters"
        / "winifred"
        / "variants"
        / "notes.txt"
    )
    target.parent.mkdir(parents=True)
    target.touch()
    assert classify_path(root, target) is None
