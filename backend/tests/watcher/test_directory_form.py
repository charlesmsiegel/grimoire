"""Watcher recognises directory-form character cards (`<id>/card.md`)."""

from __future__ import annotations

from pathlib import Path

from grimoire.watcher import classify_path


def _root(tmp_path: Path) -> Path:
    (tmp_path / "library").mkdir(parents=True, exist_ok=True)
    (tmp_path / "campaigns").mkdir(parents=True, exist_ok=True)
    return tmp_path


def test_directory_form_card_classified_as_character(tmp_path: Path) -> None:
    root = _root(tmp_path)
    target = root / "library" / "worlds" / "wod-london" / "characters" / "beatrice" / "card.md"
    target.parent.mkdir(parents=True)
    target.touch()
    w = classify_path(root, target)
    assert w is not None
    assert w.scope == "library"
    assert w.kind == "library_entity"
    assert w.entity_kind == "character"
    assert w.asset_id == "beatrice"
    assert w.world_id == "wod-london"
    assert w.library_id == "worlds/wod-london/characters/beatrice"


def test_sibling_avatar_png_not_classified(tmp_path: Path) -> None:
    root = _root(tmp_path)
    target = root / "library" / "worlds" / "w" / "characters" / "beatrice" / "avatar.png"
    target.parent.mkdir(parents=True)
    target.touch()
    assert classify_path(root, target) is None


def test_sprites_png_not_classified(tmp_path: Path) -> None:
    root = _root(tmp_path)
    target = root / "library" / "worlds" / "w" / "characters" / "beatrice" / "sprites" / "happy.png"
    target.parent.mkdir(parents=True)
    target.touch()
    assert classify_path(root, target) is None


def test_directory_without_card_md_ignored(tmp_path: Path) -> None:
    # A random file under a character directory that isn't card.md is
    # treated as a data asset and ignored.
    root = _root(tmp_path)
    target = root / "library" / "worlds" / "w" / "characters" / "beatrice" / "notes.txt"
    target.parent.mkdir(parents=True)
    target.touch()
    assert classify_path(root, target) is None


def test_flat_form_still_works(tmp_path: Path) -> None:
    root = _root(tmp_path)
    target = root / "library" / "worlds" / "w" / "characters" / "alistair.md"
    target.parent.mkdir(parents=True)
    target.touch()
    w = classify_path(root, target)
    assert w is not None
    assert w.kind == "library_entity"
    assert w.asset_id == "alistair"
