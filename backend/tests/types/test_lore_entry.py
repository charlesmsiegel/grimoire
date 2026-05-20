"""Tests for the extended LoreEntry schema.

Spec: docs/superpowers/specs/2026-05-19-card-imports-design.md §3.
Backwards compatibility: existing frontmatter parses with sensible defaults.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from grimoire.types.world import (
    ImportSource,
    LoreEntry,
    LorePosition,
    SelectiveLogic,
)


def test_legacy_lore_parses_with_defaults() -> None:
    entry = LoreEntry.model_validate(
        {
            "world_id": "wod-london",
            "id": "lore-1",
            "title": "The Tremere",
            "body": "Vampires of secrecy.",
            "tags": ["vampire", "faction"],
            "keywords": ["Tremere"],
            "related_factions": ["tremere"],
            "secrecy": "open",
        }
    )
    assert entry.enabled is True
    assert entry.position == LorePosition.AFTER_CAST
    assert entry.priority == 100
    assert entry.probability == 100
    assert entry.scan_depth is None
    assert entry.at_depth is None
    assert entry.constant is False
    assert entry.case_sensitive is False
    assert entry.match_whole_words is False
    assert entry.secondary_keys == []
    assert entry.selective_logic == SelectiveLogic.AND_ANY
    assert entry.comment == ""
    assert entry.import_source is None


def test_full_shape_roundtrips() -> None:
    entry = LoreEntry(
        world_id="w1",
        id="x",
        title="X",
        body="b",
        keywords=["x"],
        secondary_keys=["y", "z"],
        selective_logic=SelectiveLogic.AND_ALL,
        constant=True,
        enabled=False,
        case_sensitive=True,
        match_whole_words=True,
        priority=42,
        probability=30,
        position=LorePosition.AT_DEPTH,
        at_depth=2,
        scan_depth=10,
        comment="test",
        import_source=ImportSource(
            kind="sillytavern_character_book",
            card_asset_id="cv1",
            source_index=3,
        ),
    )
    dumped = entry.model_dump()
    parsed = LoreEntry.model_validate(dumped)
    assert parsed == entry


def test_position_enum_string_values() -> None:
    entry = LoreEntry.model_validate(
        {
            "world_id": "w1",
            "id": "x",
            "title": "X",
            "position": "before_cast",
        }
    )
    assert entry.position == LorePosition.BEFORE_CAST

    for value in ("after_cast", "at_depth", "archive"):
        ent = LoreEntry.model_validate(
            {"world_id": "w1", "id": "x", "title": "X", "position": value}
        )
        assert ent.position.value == value


def test_selective_logic_enum_string_values() -> None:
    for value in ("and_any", "and_all", "not_any", "not_all"):
        ent = LoreEntry.model_validate(
            {
                "world_id": "w1",
                "id": "x",
                "title": "X",
                "selective_logic": value,
            }
        )
        assert ent.selective_logic.value == value


def test_invalid_position_rejected() -> None:
    with pytest.raises(ValidationError):
        LoreEntry.model_validate({"world_id": "w1", "id": "x", "title": "X", "position": "nowhere"})


def test_import_source_round_trip() -> None:
    src = ImportSource(kind="sillytavern_character_book", card_asset_id="cv1", source_index=0)
    entry = LoreEntry(
        world_id="w1",
        id="x",
        title="X",
        import_source=src,
    )
    dumped = entry.model_dump()
    assert dumped["import_source"]["kind"] == "sillytavern_character_book"
    parsed = LoreEntry.model_validate(dumped)
    assert parsed.import_source == src
