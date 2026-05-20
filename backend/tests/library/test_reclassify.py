"""Tests for the lore-entry reclassification transform + service (spec §§1, 2, 6)."""

from __future__ import annotations

import dataclasses

import pytest

from grimoire.library.reclassify import (
    ReclassificationResult,
    apply_mapping,
    required_overrides_for,
)
from grimoire.types.common import EntityKind
from grimoire.types.world import LoreEntry


def _lore(**kw) -> LoreEntry:
    base = dict(
        world_id="w",
        id="tremere-chantry",
        title="Tremere Chantry",
        body="The chantry rises three floors.",
        tags=["wod", "imported"],
        keywords=["chantry", "tremere"],
        related_factions=["tremere"],
        secrecy="restricted",
    )
    base.update(kw)
    return LoreEntry(**base)


def test_apply_mapping_to_character_keeps_core_fields() -> None:
    lore = _lore(title="Beatrice", body="She studied alchemy in the chantry.")
    fm, body, kept, dropped, into_notes, warnings = apply_mapping(
        lore, EntityKind.CHARACTER, overrides=None,
    )
    assert fm["name"] == "Beatrice"
    assert fm["aliases"] == ["chantry", "tremere"]
    assert fm["tags"] == ["wod", "imported"]
    assert fm["secrecy"] == "restricted"
    assert "She studied alchemy" in body
    assert "name" in kept
    assert "aliases" in kept
    assert "tags" in kept
    assert "secrecy" in kept


def test_apply_mapping_to_location_drops_related_factions_into_notes_section() -> None:
    lore = _lore(title="The Tremere Chantry")
    fm, body, kept, dropped, into_notes, warnings = apply_mapping(
        lore, EntityKind.LOCATION, overrides={"kind": "building"},
    )
    assert fm["name"] == "The Tremere Chantry"
    assert fm["kind"] == "building"
    assert "related_factions" in into_notes
    assert "## Notes" in body
    assert "related_factions" in body


def test_apply_mapping_to_faction_maps_related_factions_to_allies() -> None:
    lore = _lore(title="House Tremere", related_factions=["camarilla"])
    fm, _body, kept, _dropped, _into_notes, _warnings = apply_mapping(
        lore, EntityKind.FACTION, overrides=None,
    )
    assert fm["allies"] == ["camarilla"]
    assert "allies" in kept


def test_apply_mapping_to_item_drops_secrecy_into_notes() -> None:
    lore = _lore(title="Sword of Caine", secrecy="secret")
    fm, body, _kept, _dropped, into_notes, _warnings = apply_mapping(
        lore, EntityKind.ITEM, overrides=None,
    )
    assert "secrecy" not in fm
    assert "secrecy" in into_notes
    assert "secrecy" in body


def test_apply_mapping_overrides_win_over_defaults() -> None:
    lore = _lore(title="Beatrice")
    fm, _body, _kept, _dropped, _into_notes, _warnings = apply_mapping(
        lore, EntityKind.CHARACTER,
        overrides={"name": "Lady Beatrice", "role": "major_npc"},
    )
    assert fm["name"] == "Lady Beatrice"
    assert fm["role"] == "major_npc"


def test_required_overrides_for_location_includes_kind() -> None:
    required = required_overrides_for(EntityKind.LOCATION)
    assert "kind" in required


def test_required_overrides_for_character_is_empty() -> None:
    required = required_overrides_for(EntityKind.CHARACTER)
    assert required == []


def test_reclassification_result_is_frozen() -> None:
    r = ReclassificationResult(
        source_id="lore/x",
        target_id="characters/x",
        target_kind=EntityKind.CHARACTER,
        fields_kept=[], fields_dropped=[], fields_into_notes=[], warnings=[],
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.target_id = "y"  # type: ignore[misc]
