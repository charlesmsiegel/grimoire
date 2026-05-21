"""Tests for the lore-entry reclassification transform + service (spec §§1, 2, 6)."""

from __future__ import annotations

import dataclasses

import pytest

from grimoire.library import LibraryNotFoundError, LibraryService
from grimoire.library.errors import ReclassificationError
from grimoire.library.reclassify import (
    ReclassificationResult,
    _lore_entry_from_ingested,
    apply_mapping,
    iter_audit,
    required_overrides_for,
)
from grimoire.state_store import StateStore
from grimoire.types.characters import IngestedLoreEntry
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
    fm, body, kept, _dropped, _into_notes, _warnings = apply_mapping(
        lore,
        EntityKind.CHARACTER,
        overrides=None,
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
    fm, body, _kept, _dropped, into_notes, _warnings = apply_mapping(
        lore,
        EntityKind.LOCATION,
        overrides={"kind": "building"},
    )
    assert fm["name"] == "The Tremere Chantry"
    assert fm["kind"] == "building"
    assert "related_factions" in into_notes
    assert "## Notes" in body
    assert "related_factions" in body


def test_apply_mapping_to_faction_maps_related_factions_to_allies() -> None:
    lore = _lore(title="House Tremere", related_factions=["camarilla"])
    fm, _body, kept, _dropped, _into_notes, _warnings = apply_mapping(
        lore,
        EntityKind.FACTION,
        overrides=None,
    )
    assert fm["allies"] == ["camarilla"]
    assert "allies" in kept


def test_apply_mapping_to_item_drops_secrecy_into_notes() -> None:
    lore = _lore(title="Sword of Caine", secrecy="secret")
    fm, body, _kept, _dropped, into_notes, _warnings = apply_mapping(
        lore,
        EntityKind.ITEM,
        overrides=None,
    )
    assert "secrecy" not in fm
    assert "secrecy" in into_notes
    assert "secrecy" in body


@pytest.mark.parametrize(
    "target_kind",
    [EntityKind.CHARACTER, EntityKind.LOCATION, EntityKind.FACTION, EntityKind.ITEM],
)
def test_related_locations_and_characters_routed_to_notes_not_silently_dropped(
    target_kind: EntityKind,
) -> None:
    """Regression: `related_locations` and `related_characters` are LoreEntry
    fields with no matching schema field on any of the four target kinds.
    They must survive the conversion as prose in the ``## Notes`` body
    section, not vanish silently. Required overrides are supplied so
    Location is exercised under the same code path as the others.
    """
    lore = _lore(
        related_locations=["chantry-rooftop", "wesley-residence"],
        related_characters=["beatrice", "alistair"],
    )
    overrides = {"kind": "building"} if target_kind == EntityKind.LOCATION else None
    fm, body, _kept, _dropped, into_notes, _warnings = apply_mapping(
        lore,
        target_kind,
        overrides=overrides,
    )
    assert "related_locations" in into_notes
    assert "related_characters" in into_notes
    # None of the four target kinds expose either as a frontmatter field.
    assert "related_locations" not in fm
    assert "related_characters" not in fm
    # Values survive in the body so the user can reconcile manually.
    assert "chantry-rooftop" in body
    assert "wesley-residence" in body
    assert "beatrice" in body
    assert "alistair" in body


def test_apply_mapping_overrides_win_over_defaults() -> None:
    lore = _lore(title="Beatrice")
    fm, _body, _kept, _dropped, _into_notes, _warnings = apply_mapping(
        lore,
        EntityKind.CHARACTER,
        overrides={"name": "Lady Beatrice", "role": "major_npc"},
    )
    assert fm["name"] == "Lady Beatrice"
    assert fm["role"] == "major_npc"


def test_lore_entry_from_ingested_copies_fields() -> None:
    ingested = IngestedLoreEntry(
        source_index=3,
        name="Brackhollow Inn",
        keys=["Brackhollow", "inn"],
        body="A quiet inn on the road north.",
        secondary_keys=["alehouse"],
        selective_logic="and_any",
        priority=200,
        probability=50,
        position="before_cast",
        at_depth=2,
        scan_depth=4,
        comment="cosy",
    )
    proxy = _lore_entry_from_ingested(ingested, world_id="w1")
    assert proxy.world_id == "w1"
    assert proxy.title == "Brackhollow Inn"
    assert proxy.body == "A quiet inn on the road north."
    assert proxy.keywords == ["Brackhollow", "inn"]
    assert proxy.secondary_keys == ["alehouse"]
    assert proxy.priority == 200
    assert proxy.probability == 50
    assert proxy.at_depth == 2
    assert proxy.scan_depth == 4
    assert proxy.comment == "cosy"


def test_lore_entry_from_ingested_falls_back_to_keys_or_index_for_title() -> None:
    no_name = IngestedLoreEntry(source_index=7, name=None, keys=["solo-key"], body="body")
    proxy = _lore_entry_from_ingested(no_name, world_id="w1")
    assert proxy.title == "solo-key"

    bare = IngestedLoreEntry(source_index=9, name=None, keys=[], body="body")
    proxy2 = _lore_entry_from_ingested(bare, world_id="w1")
    assert proxy2.title == "entry-9"


def test_apply_mapping_no_dropped_warning_for_lore_at_defaults() -> None:
    """Regression: a LoreEntry left at every default should NOT trip the
    'matching metadata discarded' warning. The position and selective_logic
    sentinels in _DEFAULT_VALUES must match the model defaults
    (LorePosition.AFTER_CAST, SelectiveLogic.AND_ANY), not None.
    """
    lore = _lore()  # all defaulted matching-metadata fields
    _fm, _body, _kept, dropped, _into_notes, warnings = apply_mapping(
        lore,
        EntityKind.CHARACTER,
        overrides=None,
    )
    assert dropped == []
    assert not any("matching metadata" in w for w in warnings)


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
        fields_kept=[],
        fields_dropped=[],
        fields_into_notes=[],
        warnings=[],
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.target_id = "y"  # type: ignore[misc]


# --------------------------------------------------------------------------- #
# Service-level tests (round-trip through LibraryService)
# --------------------------------------------------------------------------- #


async def _seed_world(store: StateStore, world_id: str) -> None:
    await store.write_library_file(
        library_id=f"worlds/{world_id}/world",
        frontmatter={"id": world_id, "name": world_id, "version": 1},
        body="",
        source="user",
    )


async def _seed_lore(
    store: StateStore,
    world_id: str,
    entity_id: str,
    *,
    title: str,
    body: str = "",
    **extras,
) -> None:
    fm: dict = {"id": entity_id, "title": title}
    fm.update(extras)
    await store.write_library_file(
        library_id=f"worlds/{world_id}/lore/{entity_id}",
        frontmatter=fm,
        body=body,
        source="user",
    )


async def test_reclassify_to_character_writes_target_and_deletes_source(
    library: LibraryService,
    store: StateStore,
) -> None:
    await _seed_world(store, "w")
    await _seed_lore(
        store,
        "w",
        "beatrice",
        title="Beatrice",
        body="She studied alchemy.",
        keywords=["tremere", "beatrice"],
        tags=["wod"],
    )
    result = await library.reclassify_entity(
        "w",
        "beatrice",
        target_kind=EntityKind.CHARACTER,
        overrides=None,
        actor="tester",
    )
    assert result.target_kind == EntityKind.CHARACTER
    assert result.target_id
    with pytest.raises(LibraryNotFoundError):
        await library.get_entity("w", "lore", "beatrice")
    target = await library.get_entity("w", "character", result.target_id)
    assert target.name == "Beatrice"
    assert target.frontmatter.get("aliases") == ["tremere", "beatrice"]


async def test_reclassify_to_location_requires_kind_override(
    library: LibraryService,
    store: StateStore,
) -> None:
    await _seed_world(store, "w")
    await _seed_lore(store, "w", "chantry", title="The Chantry")
    with pytest.raises(ReclassificationError, match="kind"):
        await library.reclassify_entity(
            "w",
            "chantry",
            target_kind=EntityKind.LOCATION,
            overrides=None,
        )


async def test_reclassify_resolves_target_id_collisions_with_suffix(
    library: LibraryService,
    store: StateStore,
) -> None:
    await _seed_world(store, "w")
    await store.write_library_file(
        library_id="worlds/w/characters/beatrice",
        frontmatter={"id": "beatrice", "name": "Other Beatrice"},
        body="",
        source="user",
    )
    await _seed_lore(store, "w", "beatrice-lore", title="Beatrice", body="She lived.")
    result = await library.reclassify_entity(
        "w",
        "beatrice-lore",
        target_kind=EntityKind.CHARACTER,
        overrides=None,
    )
    assert result.target_id == "beatrice-2"


async def test_reclassify_appends_audit_record(
    library: LibraryService,
    store: StateStore,
) -> None:
    await _seed_world(store, "w")
    await _seed_lore(store, "w", "beatrice", title="Beatrice", body="She lived.")
    await library.reclassify_entity(
        "w",
        "beatrice",
        target_kind=EntityKind.CHARACTER,
        overrides=None,
        actor="tester",
    )
    records = list(iter_audit(store.data_root, world_id="w"))
    assert len(records) == 1
    assert records[0]["source_id"] == "beatrice"
    assert records[0]["target_kind"] == "character"
    assert records[0]["actor"] == "tester"
    assert records[0]["source_snapshot"]["frontmatter"]["title"] == "Beatrice"


async def test_reclassify_missing_source_raises_not_found(
    library: LibraryService,
) -> None:
    with pytest.raises(LibraryNotFoundError):
        await library.reclassify_entity(
            "w",
            "missing",
            target_kind=EntityKind.CHARACTER,
            overrides=None,
        )


async def test_preview_reclassification_returns_mapping_without_writing(
    library: LibraryService,
    store: StateStore,
) -> None:
    await _seed_world(store, "w")
    await _seed_lore(
        store,
        "w",
        "beatrice",
        title="Beatrice",
        body="She studied alchemy.",
        keywords=["b"],
        tags=["wod"],
    )
    preview = await library.preview_reclassification(
        "w",
        "beatrice",
        target_kind=EntityKind.CHARACTER,
    )
    assert preview["target_kind"] == "character"
    assert preview["frontmatter"]["name"] == "Beatrice"
    assert preview["required_overrides"] == []
    assert "kept" in preview and "dropped" in preview and "into_notes" in preview
    assert preview["suggestion"]["kind"] in {"character", "lore"}
    assert (await library.get_entity("w", "lore", "beatrice")).asset_id == "beatrice"


async def test_undo_reclassification_recreates_source_and_deletes_target(
    library: LibraryService,
    store: StateStore,
) -> None:
    await _seed_world(store, "w")
    await _seed_lore(store, "w", "beatrice", title="Beatrice", body="She lived.")
    result = await library.reclassify_entity(
        "w",
        "beatrice",
        target_kind=EntityKind.CHARACTER,
        overrides=None,
        actor="tester",
    )
    records = list(iter_audit(store.data_root, world_id="w"))
    ts = records[0]["ts"]

    undo_result = await library.undo_reclassification("w", ts, actor="tester")
    assert undo_result["restored_source_id"] == "beatrice"
    assert undo_result["deleted_target_id"] == result.target_id
    restored = await library.get_entity("w", "lore", "beatrice")
    assert restored.frontmatter.get("title") == "Beatrice"
    with pytest.raises(LibraryNotFoundError):
        await library.get_entity("w", "character", result.target_id)
    records_after = list(iter_audit(store.data_root, world_id="w"))
    assert len(records_after) == 2
    assert records_after[1]["overrides"].get("_undo_of") == ts


async def test_undo_with_collision_suffixes_restored_source(
    library: LibraryService,
    store: StateStore,
) -> None:
    await _seed_world(store, "w")
    await _seed_lore(store, "w", "beatrice", title="Beatrice", body="She lived.")
    await library.reclassify_entity(
        "w",
        "beatrice",
        target_kind=EntityKind.CHARACTER,
        overrides=None,
    )
    await _seed_lore(store, "w", "beatrice", title="Different Beatrice", body="x")

    records = list(iter_audit(store.data_root, world_id="w"))
    ts = records[0]["ts"]

    undo_result = await library.undo_reclassification("w", ts)
    assert undo_result["restored_source_id"] == "beatrice-2"


async def test_undo_missing_timestamp_raises(library: LibraryService, store: StateStore) -> None:
    await _seed_world(store, "w")
    with pytest.raises(ReclassificationError, match="no audit"):
        await library.undo_reclassification("w", "2026-05-19T00:00:00Z")


async def test_list_reclassifications_returns_records_in_order(
    library: LibraryService,
    store: StateStore,
) -> None:
    await _seed_world(store, "w")
    await _seed_lore(store, "w", "a", title="Beatrice", body="She lived.")
    await _seed_lore(store, "w", "b", title="Caine", body="He walked.")
    await library.reclassify_entity("w", "a", target_kind=EntityKind.CHARACTER)
    await library.reclassify_entity("w", "b", target_kind=EntityKind.CHARACTER)
    records = await library.list_reclassifications("w")
    assert len(records) == 2
    assert records[0]["source_id"] == "a"
    assert records[1]["source_id"] == "b"
