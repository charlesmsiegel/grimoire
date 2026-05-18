"""Tests for the Library service: reads, writes, cascade, composition, promotion."""

from __future__ import annotations

import pytest

from grimoire.library import (
    LibraryConflictError,
    LibraryNotFoundError,
    LibraryService,
    PromotionError,
)
from grimoire.state_store import StateStore
from grimoire.types.common import EntityKind
from grimoire.types.composition import Composition, WorldRef


async def _seed_world(
    store: StateStore,
    world_id: str,
    *,
    name: str = "London by Night",
) -> None:
    await store.write_library_file(
        library_id=f"worlds/{world_id}/world",
        frontmatter={
            "id": world_id,
            "name": name,
            "tags": ["wod", "vampire"],
            "genre": "urban gothic horror",
            "version": 1,
            "atmosphere": {"default_register": "low"},
            "defaults": {"starting_location": "elysium"},
        },
        body="",
        source="user",
    )


async def _seed_character(
    store: StateStore,
    world_id: str,
    asset_id: str,
    *,
    name: str | None = None,
) -> None:
    await store.write_library_file(
        library_id=f"worlds/{world_id}/characters/{asset_id}",
        frontmatter={
            "id": asset_id,
            "name": name or asset_id.replace("-", " ").title(),
            "tags": ["vampire"],
        },
        body=f"# {asset_id}\n\nA character.",
        source="user",
    )


# ---------------------------------------------------------------------------
# Discovery / listing
# ---------------------------------------------------------------------------


async def test_list_and_get_worlds(library: LibraryService, store: StateStore) -> None:
    await _seed_world(store, "wod-london", name="London by Night")
    await _seed_world(store, "wod-nyc", name="NYC by Night")

    worlds = await library.list_worlds()
    ids = {s.id for s in worlds}
    assert ids == {"wod-london", "wod-nyc"}

    london = await library.get_world("wod-london")
    assert london.name == "London by Night"
    assert "wod" in london.tags
    assert london.atmosphere["default_register"] == "low"


async def test_get_world_missing_raises(library: LibraryService) -> None:
    with pytest.raises(LibraryNotFoundError):
        await library.get_world("nope")


async def test_list_in_world_and_get_entity(library: LibraryService, store: StateStore) -> None:
    await _seed_world(store, "wod-london")
    await _seed_character(store, "wod-london", "alistair", name="Alistair")
    await _seed_character(store, "wod-london", "winifred", name="winifred")

    chars = await library.list_in_world("wod-london", EntityKind.CHARACTER)
    assert {c.asset_id for c in chars} == {"alistair", "winifred"}
    assert all(c.kind == EntityKind.CHARACTER for c in chars)
    assert all(c.world_id == "wod-london" for c in chars)

    alistair = await library.get_entity("wod-london", "character", "alistair")
    assert alistair.name == "Alistair"
    assert "vampire" in alistair.tags

    with pytest.raises(LibraryNotFoundError):
        await library.get_entity("wod-london", "character", "no-such")


async def test_list_in_world_accepts_directory_alias(
    library: LibraryService, store: StateStore
) -> None:
    await _seed_world(store, "wod-london")
    await _seed_character(store, "wod-london", "alistair")
    # Pass "characters" (directory form) and "character" (singular form).
    plural = await library.list_in_world("wod-london", "characters")
    singular = await library.list_in_world("wod-london", "character")
    assert [c.asset_id for c in plural] == [c.asset_id for c in singular]


# ---------------------------------------------------------------------------
# Greetings
# ---------------------------------------------------------------------------


async def test_greeting_listing_returns_typed_greeting(
    library: LibraryService, store: StateStore
) -> None:
    await _seed_world(store, "wod-london")
    await store.write_library_file(
        library_id="worlds/wod-london/greetings/elysium-opening",
        frontmatter={
            "id": "elysium-opening",
            "name": "Elysium Opening",
            "starting_location": "elysium",
            "starting_time": "2024-10-31T22:00:00",
            "present_characters": ["alistair"],
            "pov_character": "alistair",
            "mood": "tense civility",
        },
        body="The Prince's tower is candle-lit tonight.",
        source="user",
    )
    greetings = await library.list_greetings("wod-london")
    assert len(greetings) == 1
    g = greetings[0]
    assert g.id == "elysium-opening"
    assert g.world_id == "wod-london"
    assert g.mood == "tense civility"
    assert g.body.startswith("The Prince's tower")

    fetched = await library.get_greeting("wod-london", "elysium-opening")
    assert fetched.starting_location == "elysium"
    assert fetched.present_characters == ["alistair"]


# ---------------------------------------------------------------------------
# Top-level assets
# ---------------------------------------------------------------------------


async def test_style_guides_and_image_presets(library: LibraryService, store: StateStore) -> None:
    await store.write_library_file(
        library_id="style-guides/gothic-horror",
        frontmatter={"id": "gothic-horror", "name": "Gothic Horror", "tags": ["horror"]},
        body="Present tense, third-limited.",
        source="user",
    )
    await store.write_library_file(
        library_id="image-presets/oil-painting",
        frontmatter={
            "id": "oil-painting",
            "name": "Oil painting",
            "style_preamble": "oil painting, dark academia",
        },
        body="",
        source="user",
    )

    guides = await library.list_style_guides()
    assert [g.asset_id for g in guides] == ["gothic-horror"]
    fetched = await library.get_style_guide("gothic-horror")
    assert fetched.name == "Gothic Horror"

    presets = await library.list_image_presets()
    assert [p.asset_id for p in presets] == ["oil-painting"]
    preset = await library.get_image_preset("oil-painting")
    assert preset.frontmatter["style_preamble"].startswith("oil painting")

    with pytest.raises(LibraryNotFoundError):
        await library.get_style_guide("missing")
    with pytest.raises(LibraryNotFoundError):
        await library.get_image_preset("missing")


async def test_create_style_guide_renders_bulleted_sections(library: LibraryService) -> None:
    created = await library.create_style_guide(
        "cozy-mystery",
        name="Cozy Mystery",
        description="Low stakes, high warmth.",
        tags=["mystery", "cozy"],
        pacing=["Unhurried.", "Tea between clues."],
        voice=["Warm third-limited."],
        themes=["Community.", "Small redemption."],
        avoid=["Graphic violence.", "Nihilism."],
    )
    assert created.asset_id == "cozy-mystery"
    assert created.name == "Cozy Mystery"
    assert created.frontmatter.get("description") == "Low stakes, high warmth."
    assert created.tags == ["mystery", "cozy"]

    body = created.body
    assert "# Cozy Mystery" in body
    assert "## Pacing\n- Unhurried.\n- Tea between clues." in body
    assert "## Voice\n- Warm third-limited." in body
    assert "## Themes\n- Community.\n- Small redemption." in body
    assert "## Avoid\n- Graphic violence.\n- Nihilism." in body

    # Duplicate id -> conflict, not silent overwrite.
    with pytest.raises(LibraryConflictError):
        await library.create_style_guide("cozy-mystery", name="dup")


async def test_create_style_guide_omits_blank_sections(library: LibraryService) -> None:
    created = await library.create_style_guide(
        "voice-only",
        name="Voice Only",
        voice=["Terse, declarative."],
    )
    body = created.body
    assert "## Voice" in body
    assert "## Pacing" not in body
    assert "## Themes" not in body
    assert "## Avoid" not in body


async def test_update_style_guide_replaces_bullets_and_preserves_intro(
    library: LibraryService, store: StateStore
) -> None:
    await store.write_library_file(
        library_id="style-guides/gothic-horror",
        frontmatter={"id": "gothic-horror", "name": "Gothic Horror", "tags": ["horror"]},
        body=(
            "# Gothic Horror\n\n"
            "Atmosphere first, action second.\n\n"
            "## Pacing\n"
            "- Original bullet.\n\n"
            "## Footnote\n"
            "Hand-authored prose we should keep.\n"
        ),
        source="user",
    )

    parsed = await library.parse_style_guide("gothic-horror")
    assert parsed["intro"] == "Atmosphere first, action second."
    assert parsed["pacing"] == ["Original bullet."]
    assert parsed["extra_sections"] == [("Footnote", "Hand-authored prose we should keep.")]

    updated = await library.update_style_guide(
        "gothic-horror",
        pacing=["New bullet A.", "New bullet B."],
        voice=["Whispered."],
    )
    assert "## Pacing\n- New bullet A.\n- New bullet B." in updated.body
    assert "## Voice\n- Whispered." in updated.body
    # Intro and extra section round-trip.
    assert "Atmosphere first, action second." in updated.body
    assert "## Footnote" in updated.body
    assert "Hand-authored prose we should keep." in updated.body


# ---------------------------------------------------------------------------
# Cross-world variants
# ---------------------------------------------------------------------------


async def test_variants_of_shared_asset_id(library: LibraryService, store: StateStore) -> None:
    await _seed_world(store, "faerun")
    await _seed_world(store, "mythic-europe")
    await _seed_character(store, "faerun", "drizzt", name="Drizzt")
    await _seed_character(store, "mythic-europe", "drizzt", name="Drizzt")
    await _seed_character(store, "faerun", "elminster", name="Elminster")

    variants = await library.variants_of("drizzt", EntityKind.CHARACTER)
    assert {v.world_id for v in variants} == {"faerun", "mythic-europe"}

    solo = await library.variants_of("elminster", EntityKind.CHARACTER)
    assert [v.world_id for v in solo] == ["faerun"]


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


async def test_create_world_and_entity(library: LibraryService) -> None:
    meta = await library.create_world("wod-paris", {"name": "Paris by Night"})
    assert meta.id == "wod-paris"
    assert meta.name == "Paris by Night"

    entity = await library.create_entity(
        "wod-paris",
        EntityKind.LOCATION,
        "the-louvre",
        frontmatter={"name": "The Louvre", "tags": ["public", "art"]},
        body="A vast museum.",
    )
    assert entity.asset_id == "the-louvre"
    assert entity.kind == EntityKind.LOCATION
    assert entity.body == "A vast museum."


async def test_update_entity_preserves_unchanged_fields(
    library: LibraryService, store: StateStore
) -> None:
    await _seed_world(store, "wod-london")
    await _seed_character(store, "wod-london", "alistair", name="Alistair")

    updated = await library.update_entity(
        "wod-london",
        "character",
        "alistair",
        frontmatter_patch={"age": "perpetually 34"},
    )
    assert updated.frontmatter["name"] == "Alistair"
    assert updated.frontmatter["age"] == "perpetually 34"
    # Body untouched.
    assert "A character" in updated.body

    body_only = await library.update_entity(
        "wod-london",
        "character",
        "alistair",
        body="Rewritten body.",
    )
    assert body_only.body == "Rewritten body."
    assert body_only.frontmatter["age"] == "perpetually 34"


async def test_update_entity_missing_raises(library: LibraryService) -> None:
    with pytest.raises(LibraryNotFoundError):
        await library.update_entity("ghost-world", "character", "ghost")


async def test_update_entity_deep_merges_nested_dict(
    library: LibraryService, store: StateStore
) -> None:
    """Save-prompt-template-to-card (§5) only sends the changed image fields.

    The patch ``{image: {base_prompt: "..."}}`` should preserve other
    sub-keys of the ``image:`` block rather than clobbering it. Lists and
    scalars still replace wholesale.
    """
    await _seed_world(store, "wod-london")
    await store.write_library_file(
        library_id="worlds/wod-london/characters/alistair",
        frontmatter={
            "id": "alistair",
            "name": "Alistair",
            "tags": ["vampire"],
            "image": {
                "base_prompt": "old prompt",
                "negative_prompt": "blurry",
                "canonical_seed": 42,
            },
        },
        body="A character.",
        source="user",
    )

    updated = await library.update_entity(
        "wod-london",
        "character",
        "alistair",
        frontmatter_patch={"image": {"base_prompt": "new prompt", "canonical_seed": 99}},
    )
    assert updated.frontmatter["image"]["base_prompt"] == "new prompt"
    assert updated.frontmatter["image"]["negative_prompt"] == "blurry"  # preserved
    assert updated.frontmatter["image"]["canonical_seed"] == 99

    # Lists still replace wholesale.
    updated2 = await library.update_entity(
        "wod-london",
        "character",
        "alistair",
        frontmatter_patch={"tags": ["elder"]},
    )
    assert updated2.frontmatter["tags"] == ["elder"]


# ---------------------------------------------------------------------------
# Image preset CRUD (§14)
# ---------------------------------------------------------------------------


async def test_image_preset_crud_round_trip(library: LibraryService) -> None:
    created = await library.create_image_preset(
        "noir-portraits",
        name="Noir portraits",
        description="High-contrast B&W.",
        tags=["noir"],
        style_preamble="stark shadows, 35mm film",
        default_negative_prompt="blurry",
        default_params={"steps": 24, "width": 512, "height": 768},
    )
    assert created.asset_id == "noir-portraits"
    assert created.frontmatter["style_preamble"] == "stark shadows, 35mm film"
    assert created.frontmatter["default_params"] == {
        "steps": 24,
        "width": 512,
        "height": 768,
    }

    with pytest.raises(LibraryConflictError):
        await library.create_image_preset("noir-portraits", name="dup")

    parsed = await library.parse_image_preset("noir-portraits")
    assert parsed["style_preamble"] == "stark shadows, 35mm film"
    assert parsed["default_negative_prompt"] == "blurry"

    updated = await library.update_image_preset(
        "noir-portraits",
        style_preamble="softer film grain",
    )
    assert updated.frontmatter["style_preamble"] == "softer film grain"
    # Other fields preserved.
    assert updated.frontmatter["default_negative_prompt"] == "blurry"

    await library.delete_image_preset("noir-portraits")
    with pytest.raises(LibraryNotFoundError):
        await library.get_image_preset("noir-portraits")


async def test_delete_entity(library: LibraryService, store: StateStore) -> None:
    await _seed_world(store, "wod-london")
    await _seed_character(store, "wod-london", "alistair")
    await library.delete_entity("wod-london", "character", "alistair")

    with pytest.raises(LibraryNotFoundError):
        await library.get_entity("wod-london", "character", "alistair")
    with pytest.raises(LibraryNotFoundError):
        await library.delete_entity("wod-london", "character", "alistair")


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------


async def test_set_and_get_composition_round_trip(
    library: LibraryService, store: StateStore
) -> None:
    await _seed_world(store, "wod-london")
    await _seed_world(store, "wod-nyc")
    await store.upsert_campaign(campaign_id="camp-1", name="My Campaign", mechanics_module="wod")

    composition = Composition(
        worlds=[
            WorldRef(
                world_id="wod-london",
                priority=1,
                include=["characters", "locations"],
                track_latest=False,
            ),
            WorldRef(
                world_id="wod-nyc",
                priority=2,
                include=["characters"],
                track_latest=True,
            ),
        ],
        mechanics="wod",
        style_guide_id="gothic-horror",
        image_preset_id="oil-painting",
    )
    await library.set_composition("camp-1", composition)

    roundtrip = await library.get_composition("camp-1")
    assert roundtrip.mechanics == "wod"
    assert roundtrip.style_guide_id == "gothic-horror"
    assert roundtrip.image_preset_id == "oil-painting"
    by_id = {r.world_id: r for r in roundtrip.worlds}
    assert by_id["wod-london"].priority == 1
    assert by_id["wod-london"].include == ["characters", "locations"]
    assert by_id["wod-london"].track_latest is False
    assert by_id["wod-nyc"].track_latest is True


async def test_set_composition_preserves_campaign_config(
    library: LibraryService, store: StateStore
) -> None:
    """Regression: set_composition must not wipe campaigns.config to NULL."""
    await _seed_world(store, "wod-london")
    await store.upsert_campaign(
        campaign_id="camp-1",
        name="Camp",
        config={"per_task_prompts": {"main": "be terse"}, "backup_policy": "daily"},
    )

    await library.set_composition(
        "camp-1",
        Composition(worlds=[WorldRef(world_id="wod-london", priority=1, include=None)]),
    )

    row = await store.db.fetchone("SELECT config FROM campaigns WHERE id = ?", ("camp-1",))
    assert row is not None
    assert row["config"] is not None
    import json as _json

    config = _json.loads(row["config"])
    assert config["per_task_prompts"]["main"] == "be terse"
    assert config["backup_policy"] == "daily"


async def test_set_composition_drops_removed_refs(
    library: LibraryService, store: StateStore
) -> None:
    await _seed_world(store, "wod-london")
    await _seed_world(store, "wod-nyc")
    await store.upsert_campaign(campaign_id="camp-1", name="Camp")

    await library.set_composition(
        "camp-1",
        Composition(
            worlds=[
                WorldRef(world_id="wod-london", priority=1, include=None),
                WorldRef(world_id="wod-nyc", priority=2, include=None),
            ]
        ),
    )

    await library.set_composition(
        "camp-1",
        Composition(worlds=[WorldRef(world_id="wod-london", priority=1, include=None)]),
    )
    final = await library.get_composition("camp-1")
    assert [r.world_id for r in final.worlds] == ["wod-london"]


async def test_set_composition_pins_snapshot_when_not_tracking_latest(
    library: LibraryService, store: StateStore
) -> None:
    await _seed_world(store, "wod-london")
    await _seed_character(store, "wod-london", "alistair")
    await store.upsert_campaign(campaign_id="camp-1", name="Camp")

    await library.set_composition(
        "camp-1",
        Composition(worlds=[WorldRef(world_id="wod-london", priority=1, include=None)]),
    )

    # Library mutates after pinning.
    await _seed_character(store, "wod-london", "alistair", name="Alistair v2")
    snap = await store.db.fetchone(
        "SELECT * FROM library_snapshots WHERE campaign_id = ? AND library_id = ?",
        ("camp-1", "worlds/wod-london/characters/alistair"),
    )
    assert snap is not None
    # Snapshot version is the version at bind time (v1, before mutation to v2).
    assert int(snap["version"]) == 1


async def test_upgrade_world_ref_refreshes_snapshots(
    library: LibraryService, store: StateStore
) -> None:
    await _seed_world(store, "wod-london")
    await _seed_character(store, "wod-london", "alistair")
    await store.upsert_campaign(campaign_id="camp-1", name="Camp")
    await library.set_composition(
        "camp-1",
        Composition(worlds=[WorldRef(world_id="wod-london", priority=1, include=None)]),
    )
    # Library mutates after pinning.
    await _seed_character(store, "wod-london", "alistair", name="v2")

    report = await library.upgrade_world_ref("camp-1", "wod-london")
    assert report.world_id == "wod-london"
    assert "worlds/wod-london/characters/alistair" in report.changed_entities
    assert report.to_version >= report.from_version


async def test_get_composition_missing_campaign_raises(
    library: LibraryService,
) -> None:
    with pytest.raises(LibraryNotFoundError):
        await library.get_composition("nope")


# ---------------------------------------------------------------------------
# Resolution cascade
# ---------------------------------------------------------------------------


async def test_resolve_library_live(library: LibraryService, store: StateStore) -> None:
    await _seed_world(store, "wod-london")
    await _seed_character(store, "wod-london", "alistair", name="Alistair")
    await store.upsert_campaign(campaign_id="camp-1", name="Camp")
    await library.set_composition(
        "camp-1",
        Composition(
            worlds=[
                WorldRef(
                    world_id="wod-london",
                    priority=1,
                    include=None,
                    track_latest=True,
                )
            ]
        ),
    )

    resolved = await library.resolve("worlds/wod-london/characters/alistair", "camp-1")
    assert resolved.kind == EntityKind.CHARACTER
    assert resolved.asset_id == "alistair"
    assert resolved.frontmatter["name"] == "Alistair"
    assert resolved.source_chain[0].layer.value == "library_live"


async def test_resolve_picks_up_override(library: LibraryService, store: StateStore) -> None:
    await _seed_world(store, "wod-london")
    await _seed_character(store, "wod-london", "alistair", name="Alistair")
    await store.upsert_campaign(campaign_id="camp-1", name="Camp")
    await library.set_composition(
        "camp-1",
        Composition(
            worlds=[
                WorldRef(
                    world_id="wod-london",
                    priority=1,
                    include=None,
                    track_latest=True,
                )
            ]
        ),
    )
    await store.write_override(
        campaign_id="camp-1",
        library_id="worlds/wod-london/characters/alistair",
        patch={"age": "ancient"},
        source="user",
    )

    resolved = await library.resolve("worlds/wod-london/characters/alistair", "camp-1")
    assert resolved.frontmatter["age"] == "ancient"
    assert resolved.frontmatter["name"] == "Alistair"
    assert resolved.source_chain[0].layer.value == "override"
    assert resolved.overrides_applied == ["override"]


async def test_resolve_prefers_campaign_emergent(
    library: LibraryService, store: StateStore
) -> None:
    await _seed_world(store, "wod-london")
    await _seed_character(store, "wod-london", "alistair", name="Library Alistair")
    await store.upsert_campaign(campaign_id="camp-1", name="Camp")
    await library.set_composition(
        "camp-1",
        Composition(
            worlds=[
                WorldRef(
                    world_id="wod-london",
                    priority=1,
                    include=None,
                    track_latest=True,
                )
            ]
        ),
    )
    await store.write_emergent(
        campaign_id="camp-1",
        kind="character",
        entity_id="alistair",
        frontmatter={"name": "Emergent Alistair"},
        body="overwritten",
        source="user",
    )

    resolved = await library.resolve("worlds/wod-london/characters/alistair", "camp-1")
    assert resolved.frontmatter["name"] == "Emergent Alistair"
    assert resolved.source_chain[0].layer.value == "emergent"


async def test_resolve_emergent_only_shorthand(library: LibraryService, store: StateStore) -> None:
    await store.upsert_campaign(campaign_id="camp-1", name="Camp")
    await store.write_emergent(
        campaign_id="camp-1",
        kind="character",
        entity_id="the-bartender",
        frontmatter={"name": "The Bartender"},
        body="A surprisingly chatty bartender.",
        source="extractor",
    )
    resolved = await library.resolve("emergent/character/the-bartender", "camp-1")
    assert resolved.frontmatter["name"] == "The Bartender"
    assert resolved.world_id is None
    assert resolved.source_chain[0].layer.value == "emergent"


async def test_resolve_missing_raises(library: LibraryService, store: StateStore) -> None:
    await _seed_world(store, "wod-london")
    await store.upsert_campaign(campaign_id="camp-1", name="Camp")
    with pytest.raises(LibraryNotFoundError):
        await library.resolve("worlds/wod-london/characters/ghost", "camp-1")


async def test_resolve_pinned_snapshot_doesnt_see_library_updates(
    library: LibraryService, store: StateStore
) -> None:
    await _seed_world(store, "wod-london")
    await _seed_character(store, "wod-london", "alistair", name="v1")
    await store.upsert_campaign(campaign_id="camp-1", name="Camp")
    await library.set_composition(
        "camp-1",
        Composition(worlds=[WorldRef(world_id="wod-london", priority=1, include=None)]),
    )
    await _seed_character(store, "wod-london", "alistair", name="v2")

    resolved = await library.resolve("worlds/wod-london/characters/alistair", "camp-1")
    assert resolved.frontmatter["name"] == "v1"
    assert resolved.source_chain[0].layer.value == "library_snapshot"


# ---------------------------------------------------------------------------
# Promotion
# ---------------------------------------------------------------------------


async def test_promote_emergent_to_library(library: LibraryService, store: StateStore) -> None:
    await _seed_world(store, "wod-london")
    await store.upsert_campaign(campaign_id="camp-1", name="Camp")
    await store.write_emergent(
        campaign_id="camp-1",
        kind="item",
        entity_id="the-camden-blade",
        frontmatter={"name": "The Camden Blade", "tags": ["weapon"]},
        body="A single-edged knife.",
        source="extractor",
    )

    path = await library.promote_to_library("camp-1", "item", "the-camden-blade", "wod-london")
    assert "wod-london" in path
    assert path.endswith("the-camden-blade.md")

    promoted = await library.get_entity("wod-london", "item", "the-camden-blade")
    assert promoted.frontmatter["name"] == "The Camden Blade"
    assert promoted.body == "A single-edged knife."


async def test_promote_missing_emergent_raises(library: LibraryService) -> None:
    with pytest.raises(PromotionError):
        await library.promote_to_library("camp-x", "item", "ghost", "wod-london")


async def test_promote_deletes_emergent_when_identical(
    library: LibraryService, store: StateStore
) -> None:
    """§4: After promotion the emergent file matches what we wrote → delete it."""
    await _seed_world(store, "wod-london")
    await store.upsert_campaign(campaign_id="camp-1", name="Camp")
    await store.write_emergent(
        campaign_id="camp-1",
        kind="item",
        entity_id="the-camden-blade",
        frontmatter={"name": "The Camden Blade", "id": "the-camden-blade"},
        body="A blade.",
        source="extractor",
    )
    await library.promote_to_library("camp-1", "item", "the-camden-blade", "wod-london")

    # Emergent file + index row are both gone.
    assert (
        await store.get_emergent("camp-1", "item", "the-camden-blade")
    ) is None
    from grimoire.state_store.paths import emergent_path

    assert not emergent_path(
        store.data_root, "camp-1", "item", "the-camden-blade"
    ).exists()


async def test_promote_writes_override_when_emergent_diverged_after_read(
    library: LibraryService, store: StateStore
) -> None:
    """§4: Continued mutations after the in-memory read survive as an override."""
    from unittest.mock import AsyncMock

    await _seed_world(store, "wod-london")
    await store.upsert_campaign(campaign_id="camp-1", name="Camp")
    await store.write_emergent(
        campaign_id="camp-1",
        kind="item",
        entity_id="the-camden-blade",
        frontmatter={"name": "The Camden Blade", "id": "the-camden-blade"},
        body="A blade.",
        source="extractor",
    )

    # Promote reads emergent, writes library, then re-reads emergent. Patch
    # the second get_emergent call to simulate the campaign editing the file
    # between the two reads — a divergent name + body.
    real_get = store.get_emergent
    calls = {"n": 0}

    async def _flip(campaign_id: str, kind: str, eid: str):  # noqa: ANN001
        calls["n"] += 1
        if calls["n"] == 1:
            return await real_get(campaign_id, kind, eid)
        return {
            "frontmatter": {
                "name": "The Camden Blade (engraved)",
                "id": "the-camden-blade",
            },
            "body": "A blade with a new inscription.",
        }

    store.get_emergent = AsyncMock(side_effect=_flip)  # type: ignore[method-assign]
    await library.promote_to_library("camp-1", "item", "the-camden-blade", "wod-london")
    store.get_emergent = real_get  # type: ignore[method-assign]

    # Resolving through the campaign now layers the override on top of the
    # promoted library row.
    resolved = await library.resolve(
        "worlds/wod-london/items/the-camden-blade", "camp-1"
    )
    assert resolved.frontmatter["name"] == "The Camden Blade (engraved)"
    assert resolved.source_chain[0].layer.value == "override"

    # The emergent file is gone.
    from grimoire.state_store.paths import emergent_path

    assert not emergent_path(
        store.data_root, "camp-1", "item", "the-camden-blade"
    ).exists()


async def test_promote_emits_library_entity_promoted_event(
    store: StateStore,
) -> None:
    """§4: promotion fires ``library_entity_promoted`` so subscribers can refresh."""
    from grimoire.event_bus import EventBus
    from grimoire.library import LibraryService

    bus = EventBus()
    seen: list = []

    async def _on(event):  # noqa: ANN001
        seen.append(event)

    bus.subscribe("library_entity_promoted", _on)
    lib = LibraryService(store, event_bus=bus)

    await _seed_world(store, "wod-london")
    await store.upsert_campaign(campaign_id="camp-1", name="Camp")
    await store.write_emergent(
        campaign_id="camp-1",
        kind="lore",
        entity_id="founding-of-london",
        frontmatter={"name": "Founding of London", "id": "founding-of-london"},
        body="Long ago.",
        source="extractor",
    )
    await lib.promote_to_library("camp-1", "lore", "founding-of-london", "wod-london")

    assert any(e.type == "library_entity_promoted" for e in seen)
    payload = next(e.payload for e in seen if e.type == "library_entity_promoted")
    assert payload["library_id"] == "worlds/wod-london/lore/founding-of-london"
    assert payload["cleanup"] == "deleted"
    assert payload["body_diverged"] is False


async def test_promote_rejects_top_level_kinds(library: LibraryService) -> None:
    with pytest.raises(PromotionError):
        await library.promote_to_library("camp-1", "style_guide", "g", "wod-london")


# ---------------------------------------------------------------------------
# Dependents
# ---------------------------------------------------------------------------


async def test_dependents_lists_referencing_campaigns(
    library: LibraryService, store: StateStore
) -> None:
    await _seed_world(store, "wod-london")
    await store.upsert_campaign(campaign_id="camp-A", name="Alpha")
    await store.upsert_campaign(campaign_id="camp-B", name="Bravo")
    await store.upsert_campaign(campaign_id="camp-C", name="Charlie")
    await library.set_composition(
        "camp-A",
        Composition(worlds=[WorldRef(world_id="wod-london", priority=1, include=None)]),
    )
    await library.set_composition(
        "camp-B",
        Composition(worlds=[WorldRef(world_id="wod-london", priority=1, include=None)]),
    )

    deps = await library.dependents("wod-london", "character", "alistair")
    ids = {d.id for d in deps}
    assert ids == {"camp-A", "camp-B"}


# ---------------------------------------------------------------------------
# Composition-aware listing
# ---------------------------------------------------------------------------


async def test_list_for_composition_respects_priority_and_include(
    library: LibraryService, store: StateStore
) -> None:
    await _seed_world(store, "primary")
    await _seed_world(store, "secondary")
    await _seed_character(store, "primary", "alistair", name="Primary Alistair")
    await _seed_character(store, "secondary", "alistair", name="Secondary Alistair")
    await _seed_character(store, "secondary", "winifred", name="winifred")
    await store.upsert_campaign(campaign_id="camp-1", name="Camp")
    await library.set_composition(
        "camp-1",
        Composition(
            worlds=[
                WorldRef(
                    world_id="primary",
                    priority=1,
                    include=["characters"],
                    track_latest=True,
                ),
                WorldRef(
                    world_id="secondary",
                    priority=2,
                    include=["characters"],
                    track_latest=True,
                ),
            ]
        ),
    )

    chars = await library.list_for_composition("camp-1", EntityKind.CHARACTER)
    by_id = {c.asset_id: c for c in chars}
    # Primary wins for shared asset.
    assert by_id["alistair"].frontmatter["name"] == "Primary Alistair"
    # Secondary contributes its unique entities.
    assert "winifred" in by_id


async def test_list_for_composition_skips_excluded_kinds(
    library: LibraryService, store: StateStore
) -> None:
    await _seed_world(store, "wod-london")
    await _seed_character(store, "wod-london", "alistair")
    await store.upsert_campaign(campaign_id="camp-1", name="Camp")
    await library.set_composition(
        "camp-1",
        Composition(
            worlds=[
                WorldRef(
                    world_id="wod-london",
                    priority=1,
                    include=["locations"],
                    track_latest=True,
                )
            ]
        ),
    )
    chars = await library.list_for_composition("camp-1", EntityKind.CHARACTER)
    assert chars == []
