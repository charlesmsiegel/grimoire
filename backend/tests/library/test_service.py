"""Tests for the Library service: reads, writes, cascade, composition, promotion."""

from __future__ import annotations

import pytest

from grimoire.library import LibraryNotFoundError, LibraryService, PromotionError
from grimoire.state_store import StateStore
from grimoire.types.common import EntityKind
from grimoire.types.composition import Composition, SettingRef


async def _seed_setting(
    store: StateStore,
    setting_id: str,
    *,
    name: str = "London by Night",
) -> None:
    await store.write_library_file(
        library_id=f"settings/{setting_id}",
        frontmatter={
            "id": setting_id,
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
    setting_id: str,
    asset_id: str,
    *,
    name: str | None = None,
) -> None:
    await store.write_library_file(
        library_id=f"settings/{setting_id}/characters/{asset_id}",
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


async def test_list_and_get_settings(library: LibraryService, store: StateStore) -> None:
    await _seed_setting(store, "wod-london", name="London by Night")
    await _seed_setting(store, "wod-nyc", name="NYC by Night")

    settings = await library.list_settings()
    ids = {s.id for s in settings}
    assert ids == {"wod-london", "wod-nyc"}

    london = await library.get_setting("wod-london")
    assert london.name == "London by Night"
    assert "wod" in london.tags
    assert london.atmosphere["default_register"] == "low"


async def test_get_setting_missing_raises(library: LibraryService) -> None:
    with pytest.raises(LibraryNotFoundError):
        await library.get_setting("nope")


async def test_list_in_setting_and_get_entity(library: LibraryService, store: StateStore) -> None:
    await _seed_setting(store, "wod-london")
    await _seed_character(store, "wod-london", "alistair", name="Alistair")
    await _seed_character(store, "wod-london", "winifred", name="winifred")

    chars = await library.list_in_setting("wod-london", EntityKind.CHARACTER)
    assert {c.asset_id for c in chars} == {"alistair", "winifred"}
    assert all(c.kind == EntityKind.CHARACTER for c in chars)
    assert all(c.setting_id == "wod-london" for c in chars)

    alistair = await library.get_entity("wod-london", "character", "alistair")
    assert alistair.name == "Alistair"
    assert "vampire" in alistair.tags

    with pytest.raises(LibraryNotFoundError):
        await library.get_entity("wod-london", "character", "no-such")


async def test_list_in_setting_accepts_directory_alias(
    library: LibraryService, store: StateStore
) -> None:
    await _seed_setting(store, "wod-london")
    await _seed_character(store, "wod-london", "alistair")
    # Pass "characters" (directory form) and "character" (singular form).
    plural = await library.list_in_setting("wod-london", "characters")
    singular = await library.list_in_setting("wod-london", "character")
    assert [c.asset_id for c in plural] == [c.asset_id for c in singular]


# ---------------------------------------------------------------------------
# Greetings
# ---------------------------------------------------------------------------


async def test_greeting_listing_returns_typed_greeting(
    library: LibraryService, store: StateStore
) -> None:
    await _seed_setting(store, "wod-london")
    await store.write_library_file(
        library_id="settings/wod-london/greetings/elysium-opening",
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
    assert g.setting_id == "wod-london"
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


# ---------------------------------------------------------------------------
# Cross-setting variants
# ---------------------------------------------------------------------------


async def test_variants_of_shared_asset_id(library: LibraryService, store: StateStore) -> None:
    await _seed_setting(store, "faerun")
    await _seed_setting(store, "mythic-europe")
    await _seed_character(store, "faerun", "drizzt", name="Drizzt")
    await _seed_character(store, "mythic-europe", "drizzt", name="Drizzt")
    await _seed_character(store, "faerun", "elminster", name="Elminster")

    variants = await library.variants_of("drizzt", EntityKind.CHARACTER)
    assert {v.setting_id for v in variants} == {"faerun", "mythic-europe"}

    solo = await library.variants_of("elminster", EntityKind.CHARACTER)
    assert [v.setting_id for v in solo] == ["faerun"]


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


async def test_create_setting_and_entity(library: LibraryService) -> None:
    meta = await library.create_setting("wod-paris", {"name": "Paris by Night"})
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
    await _seed_setting(store, "wod-london")
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
        await library.update_entity("ghost-setting", "character", "ghost")


async def test_delete_entity(library: LibraryService, store: StateStore) -> None:
    await _seed_setting(store, "wod-london")
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
    await _seed_setting(store, "wod-london")
    await _seed_setting(store, "wod-nyc")
    await store.upsert_campaign(campaign_id="camp-1", name="My Campaign", mechanics_module="wod")

    composition = Composition(
        settings=[
            SettingRef(
                setting_id="wod-london",
                priority=1,
                include=["characters", "locations"],
                track_latest=False,
            ),
            SettingRef(
                setting_id="wod-nyc",
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
    by_id = {r.setting_id: r for r in roundtrip.settings}
    assert by_id["wod-london"].priority == 1
    assert by_id["wod-london"].include == ["characters", "locations"]
    assert by_id["wod-london"].track_latest is False
    assert by_id["wod-nyc"].track_latest is True


async def test_set_composition_preserves_campaign_config(
    library: LibraryService, store: StateStore
) -> None:
    """Regression: set_composition must not wipe campaigns.config to NULL."""
    await _seed_setting(store, "wod-london")
    await store.upsert_campaign(
        campaign_id="camp-1",
        name="Camp",
        config={"per_task_prompts": {"main": "be terse"}, "backup_policy": "daily"},
    )

    await library.set_composition(
        "camp-1",
        Composition(settings=[SettingRef(setting_id="wod-london", priority=1, include=None)]),
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
    await _seed_setting(store, "wod-london")
    await _seed_setting(store, "wod-nyc")
    await store.upsert_campaign(campaign_id="camp-1", name="Camp")

    await library.set_composition(
        "camp-1",
        Composition(
            settings=[
                SettingRef(setting_id="wod-london", priority=1, include=None),
                SettingRef(setting_id="wod-nyc", priority=2, include=None),
            ]
        ),
    )

    await library.set_composition(
        "camp-1",
        Composition(settings=[SettingRef(setting_id="wod-london", priority=1, include=None)]),
    )
    final = await library.get_composition("camp-1")
    assert [r.setting_id for r in final.settings] == ["wod-london"]


async def test_set_composition_pins_snapshot_when_not_tracking_latest(
    library: LibraryService, store: StateStore
) -> None:
    await _seed_setting(store, "wod-london")
    await _seed_character(store, "wod-london", "alistair")
    await store.upsert_campaign(campaign_id="camp-1", name="Camp")

    await library.set_composition(
        "camp-1",
        Composition(settings=[SettingRef(setting_id="wod-london", priority=1, include=None)]),
    )

    # Library mutates after pinning.
    await _seed_character(store, "wod-london", "alistair", name="Alistair v2")
    snap = await store.db.fetchone(
        "SELECT * FROM library_snapshots WHERE campaign_id = ? AND library_id = ?",
        ("camp-1", "settings/wod-london/characters/alistair"),
    )
    assert snap is not None
    # Snapshot version is the version at bind time (v1, before mutation to v2).
    assert int(snap["version"]) == 1


async def test_upgrade_setting_ref_refreshes_snapshots(
    library: LibraryService, store: StateStore
) -> None:
    await _seed_setting(store, "wod-london")
    await _seed_character(store, "wod-london", "alistair")
    await store.upsert_campaign(campaign_id="camp-1", name="Camp")
    await library.set_composition(
        "camp-1",
        Composition(settings=[SettingRef(setting_id="wod-london", priority=1, include=None)]),
    )
    # Library mutates after pinning.
    await _seed_character(store, "wod-london", "alistair", name="v2")

    report = await library.upgrade_setting_ref("camp-1", "wod-london")
    assert report.setting_id == "wod-london"
    assert "settings/wod-london/characters/alistair" in report.changed_entities
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
    await _seed_setting(store, "wod-london")
    await _seed_character(store, "wod-london", "alistair", name="Alistair")
    await store.upsert_campaign(campaign_id="camp-1", name="Camp")
    await library.set_composition(
        "camp-1",
        Composition(
            settings=[
                SettingRef(
                    setting_id="wod-london",
                    priority=1,
                    include=None,
                    track_latest=True,
                )
            ]
        ),
    )

    resolved = await library.resolve("settings/wod-london/characters/alistair", "camp-1")
    assert resolved.kind == EntityKind.CHARACTER
    assert resolved.asset_id == "alistair"
    assert resolved.frontmatter["name"] == "Alistair"
    assert resolved.source_chain[0].layer.value == "library_live"


async def test_resolve_picks_up_override(library: LibraryService, store: StateStore) -> None:
    await _seed_setting(store, "wod-london")
    await _seed_character(store, "wod-london", "alistair", name="Alistair")
    await store.upsert_campaign(campaign_id="camp-1", name="Camp")
    await library.set_composition(
        "camp-1",
        Composition(
            settings=[
                SettingRef(
                    setting_id="wod-london",
                    priority=1,
                    include=None,
                    track_latest=True,
                )
            ]
        ),
    )
    await store.write_override(
        campaign_id="camp-1",
        library_id="settings/wod-london/characters/alistair",
        patch={"age": "ancient"},
        source="user",
    )

    resolved = await library.resolve("settings/wod-london/characters/alistair", "camp-1")
    assert resolved.frontmatter["age"] == "ancient"
    assert resolved.frontmatter["name"] == "Alistair"
    assert resolved.source_chain[0].layer.value == "override"
    assert resolved.overrides_applied == ["override"]


async def test_resolve_prefers_campaign_emergent(
    library: LibraryService, store: StateStore
) -> None:
    await _seed_setting(store, "wod-london")
    await _seed_character(store, "wod-london", "alistair", name="Library Alistair")
    await store.upsert_campaign(campaign_id="camp-1", name="Camp")
    await library.set_composition(
        "camp-1",
        Composition(
            settings=[
                SettingRef(
                    setting_id="wod-london",
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

    resolved = await library.resolve("settings/wod-london/characters/alistair", "camp-1")
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
    assert resolved.setting_id is None
    assert resolved.source_chain[0].layer.value == "emergent"


async def test_resolve_missing_raises(library: LibraryService, store: StateStore) -> None:
    await _seed_setting(store, "wod-london")
    await store.upsert_campaign(campaign_id="camp-1", name="Camp")
    with pytest.raises(LibraryNotFoundError):
        await library.resolve("settings/wod-london/characters/ghost", "camp-1")


async def test_resolve_pinned_snapshot_doesnt_see_library_updates(
    library: LibraryService, store: StateStore
) -> None:
    await _seed_setting(store, "wod-london")
    await _seed_character(store, "wod-london", "alistair", name="v1")
    await store.upsert_campaign(campaign_id="camp-1", name="Camp")
    await library.set_composition(
        "camp-1",
        Composition(settings=[SettingRef(setting_id="wod-london", priority=1, include=None)]),
    )
    await _seed_character(store, "wod-london", "alistair", name="v2")

    resolved = await library.resolve("settings/wod-london/characters/alistair", "camp-1")
    assert resolved.frontmatter["name"] == "v1"
    assert resolved.source_chain[0].layer.value == "library_snapshot"


# ---------------------------------------------------------------------------
# Promotion
# ---------------------------------------------------------------------------


async def test_promote_emergent_to_library(library: LibraryService, store: StateStore) -> None:
    await _seed_setting(store, "wod-london")
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


async def test_promote_rejects_top_level_kinds(library: LibraryService) -> None:
    with pytest.raises(PromotionError):
        await library.promote_to_library("camp-1", "style_guide", "g", "wod-london")


# ---------------------------------------------------------------------------
# Dependents
# ---------------------------------------------------------------------------


async def test_dependents_lists_referencing_campaigns(
    library: LibraryService, store: StateStore
) -> None:
    await _seed_setting(store, "wod-london")
    await store.upsert_campaign(campaign_id="camp-A", name="Alpha")
    await store.upsert_campaign(campaign_id="camp-B", name="Bravo")
    await store.upsert_campaign(campaign_id="camp-C", name="Charlie")
    await library.set_composition(
        "camp-A",
        Composition(settings=[SettingRef(setting_id="wod-london", priority=1, include=None)]),
    )
    await library.set_composition(
        "camp-B",
        Composition(settings=[SettingRef(setting_id="wod-london", priority=1, include=None)]),
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
    await _seed_setting(store, "primary")
    await _seed_setting(store, "secondary")
    await _seed_character(store, "primary", "alistair", name="Primary Alistair")
    await _seed_character(store, "secondary", "alistair", name="Secondary Alistair")
    await _seed_character(store, "secondary", "winifred", name="winifred")
    await store.upsert_campaign(campaign_id="camp-1", name="Camp")
    await library.set_composition(
        "camp-1",
        Composition(
            settings=[
                SettingRef(
                    setting_id="primary",
                    priority=1,
                    include=["characters"],
                    track_latest=True,
                ),
                SettingRef(
                    setting_id="secondary",
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
    await _seed_setting(store, "wod-london")
    await _seed_character(store, "wod-london", "alistair")
    await store.upsert_campaign(campaign_id="camp-1", name="Camp")
    await library.set_composition(
        "camp-1",
        Composition(
            settings=[
                SettingRef(
                    setting_id="wod-london",
                    priority=1,
                    include=["locations"],
                    track_latest=True,
                )
            ]
        ),
    )
    chars = await library.list_for_composition("camp-1", EntityKind.CHARACTER)
    assert chars == []
