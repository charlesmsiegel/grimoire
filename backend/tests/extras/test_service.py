"""``ExtrasService`` behavior tests: CRUD, cascade, caps, search, promotion."""

from __future__ import annotations

import pytest

from grimoire.extras import ExtrasHardCapError, ExtrasNotFoundError, ExtrasService
from grimoire.library import LibraryService
from grimoire.state_store import StateStore
from grimoire.types.common import EntityKind
from grimoire.types.composition import Composition, WorldRef
from grimoire.types.extras import (
    HARD_CAP_PER_ENTITY,
    SOFT_CAP_PER_ENTITY,
    ExtraScope,
)

WORLD = "by-night"
CAMPAIGN = "camp-1"
CHAR = "winifred"


async def _setup(library: LibraryService, store: StateStore, seed_world, seed_character):
    await seed_world(store, WORLD)
    await seed_character(store, WORLD, CHAR)
    await store.upsert_campaign(campaign_id=CAMPAIGN, name="Camp One")
    await library.set_composition(
        CAMPAIGN,
        Composition(worlds=[WorldRef(world_id=WORLD, priority=1, include=None)]),
    )


# ---------------------------------------------------------------------- #
# Set / Get
# ---------------------------------------------------------------------- #


async def test_set_library_writes_frontmatter_and_mirror(
    extras: ExtrasService,
    library: LibraryService,
    store: StateStore,
    seed_world,
    seed_character,
):
    await _setup(library, store, seed_world, seed_character)
    result = await extras.set(
        entity_kind=EntityKind.CHARACTER,
        entity_id=CHAR,
        key="favorite_drink",
        value="Glenfarclas 25",
        scope=ExtraScope.LIBRARY,
        campaign_id=None,
        world_id=WORLD,
        actor="user",
    )
    assert result.extra.value == "Glenfarclas 25"

    # Frontmatter persists the extra in the library file.
    entity = await library.get_entity(WORLD, "character", CHAR)
    assert entity.frontmatter["extras"]["favorite_drink"]["value"] == "Glenfarclas 25"

    # Mirror row exists.
    row = await store.db.fetchone(
        """
        SELECT * FROM entity_extras
        WHERE entity_id = ? AND key = ? AND scope = ?
        """,
        (CHAR, "favorite_drink", "library"),
    )
    assert row is not None
    assert row["set_by"] == "user"


async def test_get_returns_library_scope_only_when_campaign_none(
    extras: ExtrasService, library, store, seed_world, seed_character
):
    await _setup(library, store, seed_world, seed_character)
    await extras.set(
        entity_kind=EntityKind.CHARACTER,
        entity_id=CHAR,
        key="scars",
        value=["above brow"],
        scope=ExtraScope.LIBRARY,
        campaign_id=None,
        world_id=WORLD,
    )
    resolved = await extras.get(
        entity_kind=EntityKind.CHARACTER,
        entity_id=CHAR,
        campaign_id=None,
        world_id=WORLD,
    )
    assert resolved["scars"].value == ["above brow"]


async def test_cascade_override_replaces_library(
    extras: ExtrasService, library, store, seed_world, seed_character
):
    await _setup(library, store, seed_world, seed_character)
    await extras.set(
        entity_kind=EntityKind.CHARACTER,
        entity_id=CHAR,
        key="drink",
        value="wine",
        scope=ExtraScope.LIBRARY,
        campaign_id=None,
        world_id=WORLD,
    )
    await extras.set(
        entity_kind=EntityKind.CHARACTER,
        entity_id=CHAR,
        key="drink",
        value="whisky",
        scope=ExtraScope.OVERRIDE,
        campaign_id=CAMPAIGN,
        world_id=WORLD,
    )
    resolved = await extras.get(
        entity_kind=EntityKind.CHARACTER,
        entity_id=CHAR,
        campaign_id=CAMPAIGN,
        world_id=WORLD,
    )
    assert resolved["drink"].value == "whisky"


async def test_override_null_clears_library_key(
    extras: ExtrasService, library, store, seed_world, seed_character
):
    await _setup(library, store, seed_world, seed_character)
    await extras.set(
        entity_kind=EntityKind.CHARACTER,
        entity_id=CHAR,
        key="smokes",
        value="Sobranies",
        scope=ExtraScope.LIBRARY,
        campaign_id=None,
        world_id=WORLD,
    )
    await extras.delete(
        entity_kind=EntityKind.CHARACTER,
        entity_id=CHAR,
        key="smokes",
        scope=ExtraScope.OVERRIDE,
        campaign_id=CAMPAIGN,
        world_id=WORLD,
    )
    resolved = await extras.get(
        entity_kind=EntityKind.CHARACTER,
        entity_id=CHAR,
        campaign_id=CAMPAIGN,
        world_id=WORLD,
    )
    assert "smokes" not in resolved


async def test_get_raw_single_scope(
    extras: ExtrasService, library, store, seed_world, seed_character
):
    await _setup(library, store, seed_world, seed_character)
    await extras.set(
        entity_kind=EntityKind.CHARACTER,
        entity_id=CHAR,
        key="dialect_notes",
        value="drops his aitches",
        scope=ExtraScope.LIBRARY,
        campaign_id=None,
        world_id=WORLD,
    )
    await extras.set(
        entity_kind=EntityKind.CHARACTER,
        entity_id=CHAR,
        key="cologne",
        value="cedar",
        scope=ExtraScope.OVERRIDE,
        campaign_id=CAMPAIGN,
        world_id=WORLD,
    )
    lib_only = await extras.get_raw(
        entity_kind=EntityKind.CHARACTER,
        entity_id=CHAR,
        scope=ExtraScope.LIBRARY,
        world_id=WORLD,
    )
    override_only = await extras.get_raw(
        entity_kind=EntityKind.CHARACTER,
        entity_id=CHAR,
        scope=ExtraScope.OVERRIDE,
        campaign_id=CAMPAIGN,
        world_id=WORLD,
    )
    assert set(lib_only.keys()) == {"dialect_notes"}
    assert set(override_only.keys()) == {"cologne"}


# ---------------------------------------------------------------------- #
# Caps
# ---------------------------------------------------------------------- #


async def test_hard_cap_rejected(extras: ExtrasService, library, store, seed_world, seed_character):
    await _setup(library, store, seed_world, seed_character)
    # Populate up to the hard cap, then try one more.
    for i in range(HARD_CAP_PER_ENTITY):
        await extras.set(
            entity_kind=EntityKind.CHARACTER,
            entity_id=CHAR,
            key=f"k_{i}",
            value=f"v{i}",
            scope=ExtraScope.LIBRARY,
            campaign_id=None,
            world_id=WORLD,
        )
    with pytest.raises(ExtrasHardCapError):
        await extras.set(
            entity_kind=EntityKind.CHARACTER,
            entity_id=CHAR,
            key="one_too_many",
            value="x",
            scope=ExtraScope.LIBRARY,
            campaign_id=None,
            world_id=WORLD,
        )


async def test_soft_cap_emits_warning(
    extras: ExtrasService, library, store, seed_world, seed_character
):
    await _setup(library, store, seed_world, seed_character)
    for i in range(SOFT_CAP_PER_ENTITY):
        await extras.set(
            entity_kind=EntityKind.CHARACTER,
            entity_id=CHAR,
            key=f"k_{i}",
            value=f"v{i}",
            scope=ExtraScope.LIBRARY,
            campaign_id=None,
            world_id=WORLD,
        )
    result = await extras.set(
        entity_kind=EntityKind.CHARACTER,
        entity_id=CHAR,
        key="one_over",
        value="x",
        scope=ExtraScope.LIBRARY,
        campaign_id=None,
        world_id=WORLD,
    )
    assert any("soft cap" in w for w in result.warnings)


# ---------------------------------------------------------------------- #
# Search
# ---------------------------------------------------------------------- #


async def test_search_fts_finds_substring(
    extras: ExtrasService, library, store, seed_world, seed_character
):
    await _setup(library, store, seed_world, seed_character)
    await extras.set(
        entity_kind=EntityKind.CHARACTER,
        entity_id=CHAR,
        key="dialect_notes",
        value="drops aitches when angry",
        scope=ExtraScope.LIBRARY,
        campaign_id=None,
        world_id=WORLD,
    )
    hits = await extras.search("aitches")
    assert len(hits) == 1
    assert hits[0].entity_id == CHAR
    assert hits[0].key == "dialect_notes"


# ---------------------------------------------------------------------- #
# Promotion
# ---------------------------------------------------------------------- #


async def test_promote_override_to_library(
    extras: ExtrasService, library, store, seed_world, seed_character
):
    await _setup(library, store, seed_world, seed_character)
    await extras.set(
        entity_kind=EntityKind.CHARACTER,
        entity_id=CHAR,
        key="ring_pattern",
        value="signet, three diamonds",
        scope=ExtraScope.OVERRIDE,
        campaign_id=CAMPAIGN,
        world_id=WORLD,
    )
    await extras.promote_to_library(
        entity_kind=EntityKind.CHARACTER,
        entity_id=CHAR,
        key="ring_pattern",
        campaign_id=CAMPAIGN,
        world_id=WORLD,
    )
    # Library now owns the key.
    lib_only = await extras.get_raw(
        entity_kind=EntityKind.CHARACTER,
        entity_id=CHAR,
        scope=ExtraScope.LIBRARY,
        world_id=WORLD,
    )
    assert "ring_pattern" in lib_only


async def test_promote_missing_key_raises(
    extras: ExtrasService, library, store, seed_world, seed_character
):
    await _setup(library, store, seed_world, seed_character)
    from grimoire.extras import ExtrasPromotionError

    with pytest.raises(ExtrasPromotionError):
        await extras.promote_to_library(
            entity_kind=EntityKind.CHARACTER,
            entity_id=CHAR,
            key="no_such_key",
            campaign_id=CAMPAIGN,
            world_id=WORLD,
        )


# ---------------------------------------------------------------------- #
# Delete
# ---------------------------------------------------------------------- #


async def test_delete_library_removes_key_and_mirror(
    extras: ExtrasService, library, store, seed_world, seed_character
):
    await _setup(library, store, seed_world, seed_character)
    await extras.set(
        entity_kind=EntityKind.CHARACTER,
        entity_id=CHAR,
        key="favorite_color",
        value="amber",
        scope=ExtraScope.LIBRARY,
        campaign_id=None,
        world_id=WORLD,
    )
    await extras.delete(
        entity_kind=EntityKind.CHARACTER,
        entity_id=CHAR,
        key="favorite_color",
        scope=ExtraScope.LIBRARY,
        campaign_id=None,
        world_id=WORLD,
    )
    lib_only = await extras.get_raw(
        entity_kind=EntityKind.CHARACTER,
        entity_id=CHAR,
        scope=ExtraScope.LIBRARY,
        world_id=WORLD,
    )
    assert "favorite_color" not in lib_only
    row = await store.db.fetchone(
        "SELECT * FROM entity_extras WHERE entity_id = ? AND key = ?",
        (CHAR, "favorite_color"),
    )
    assert row is None


async def test_delete_missing_raises(
    extras: ExtrasService, library, store, seed_world, seed_character
):
    await _setup(library, store, seed_world, seed_character)
    with pytest.raises(ExtrasNotFoundError):
        await extras.delete(
            entity_kind=EntityKind.CHARACTER,
            entity_id=CHAR,
            key="nope",
            scope=ExtraScope.LIBRARY,
            campaign_id=None,
            world_id=WORLD,
        )
