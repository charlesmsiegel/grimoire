"""Tests for WorldService.

Covers CRUD-by-kind, composition-aware listing, spatial queries, lore keyword
triggers, calendar/season/holiday queries, procedural weather (determinism +
override), faction state, and fork.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from grimoire.library import LibraryService
from grimoire.state_store import StateStore
from grimoire.state_store.errors import InvalidRefError
from grimoire.types.common import EntityKind, InGameTime
from grimoire.types.composition import ResolutionLayer
from grimoire.types.world import Weather, WeatherKind
from grimoire.world import WorldService
from grimoire.world.errors import OverrideTargetError, WorldError, WorldNotFoundError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_world(
    world: WorldService,
    world_id: str,
    *,
    name: str = "London by Night",
    calendar: dict | None = None,
    atmosphere: dict | None = None,
) -> None:
    await world.create_world(
        world_id,
        {
            "id": world_id,
            "name": name,
            "tags": ["wod"],
            "calendar": calendar or {},
            "atmosphere": atmosphere or {"default_register": "low"},
            "version": 1,
        },
    )


async def _seed_location(
    world: WorldService,
    world_id: str,
    location_id: str,
    *,
    name: str | None = None,
    parent_id: str | None = None,
    connections: list[dict] | None = None,
    climate_zone: str | None = None,
    indoor: bool = False,
    kind: str = "outdoor",
) -> None:
    await world.create_entity(
        world_id,
        "location",
        location_id,
        {
            "id": location_id,
            "name": name or location_id.replace("-", " ").title(),
            "parent_id": parent_id,
            "kind": kind,
            "climate_zone": climate_zone,
            "indoor": indoor,
            "connections": connections or [],
            "tags": ["urban"],
        },
        body=f"# {location_id}",
    )


async def _seed_lore(
    world: WorldService,
    world_id: str,
    lore_id: str,
    *,
    title: str,
    keywords: list[str],
    secrecy: str = "public",
) -> None:
    await world.create_entity(
        world_id,
        "lore",
        lore_id,
        {
            "id": lore_id,
            "name": title,
            "title": title,
            "keywords": keywords,
            "secrecy": secrecy,
            "tags": ["lore"],
        },
        body=f"{title} — describes the keywords {', '.join(keywords)}.",
    )


async def _seed_faction(
    world: WorldService,
    world_id: str,
    faction_id: str,
    *,
    name: str,
) -> None:
    await world.create_entity(
        world_id,
        "faction",
        faction_id,
        {
            "id": faction_id,
            "name": name,
            "kind": "vampire-sect",
        },
        body=f"{name} body",
    )


async def _bind_campaign(
    store: StateStore,
    campaign_id: str,
    refs: list[tuple[str, list[str]]],
) -> None:
    await store.upsert_campaign(campaign_id=campaign_id, name=campaign_id)
    for i, (sid, include) in enumerate(refs, start=1):
        # Tests use [] to mean "all kinds" historically; preserve that intent
        # by translating empty to None at the boundary.
        await store.upsert_world_ref(
            campaign_id=campaign_id,
            world_id=sid,
            priority=i,
            include=include if include else None,
            track_latest=True,
        )


# ---------------------------------------------------------------------------
# CRUD + listing
# ---------------------------------------------------------------------------


async def test_create_get_list_locations(world: WorldService) -> None:
    await _seed_world(world, "wod-london")
    await _seed_location(world, "wod-london", "camden-market", climate_zone="temperate-oceanic")
    await _seed_location(world, "wod-london", "elysium", indoor=True, kind="building")

    locations = await world.list_locations("wod-london")
    assert {loc.id for loc in locations} == {"camden-market", "elysium"}

    market = await world.get_location("wod-london", "camden-market")
    assert market.climate_zone == "temperate-oceanic"
    assert market.indoor is False


async def test_character_kind_rejected(world: WorldService) -> None:
    await _seed_world(world, "wod-london")
    with pytest.raises(WorldError):
        await world.create_entity("wod-london", "character", "alistair", {"id": "alistair"})


# ---------------------------------------------------------------------------
# Spatial queries
# ---------------------------------------------------------------------------


async def test_adjacent_locations_via_parent_and_connections(world: WorldService) -> None:
    await _seed_world(world, "wod-london")
    await _seed_location(world, "wod-london", "camden")
    await _seed_location(
        world,
        "wod-london",
        "camden-market",
        parent_id="camden",
        connections=[{"to": "chalk-farm", "via": "street", "duration_min": 8}],
    )
    await _seed_location(world, "wod-london", "chalk-farm")

    adj = await world.adjacent_locations("library:worlds/wod-london/locations/camden-market")
    ids = {loc.id for loc in adj}
    assert ids == {"camden", "chalk-farm"}


async def test_adjacent_locations_skips_missing_neighbors(world: WorldService) -> None:
    await _seed_world(world, "wod-london")
    await _seed_location(
        world,
        "wod-london",
        "camden-market",
        parent_id="ghost-parent",
        connections=[
            {"to": "chalk-farm", "via": "street", "duration_min": 8},
            {"to": "vanished", "via": "street", "duration_min": 5},
        ],
    )
    await _seed_location(world, "wod-london", "chalk-farm")

    adj = await world.adjacent_locations("library:worlds/wod-london/locations/camden-market")
    assert {loc.id for loc in adj} == {"chalk-farm"}


async def test_adjacent_locations_includes_parent_skips_missing_neighbor(
    world: WorldService,
) -> None:
    await _seed_world(world, "wod-london")
    await _seed_location(world, "wod-london", "camden")
    await _seed_location(
        world,
        "wod-london",
        "camden-market",
        parent_id="camden",
        connections=[
            {"to": "chalk-farm", "via": "street", "duration_min": 8},
            {"to": "ghost-station", "via": "tube", "duration_min": 4},
        ],
    )
    await _seed_location(world, "wod-london", "chalk-farm")

    adj = await world.adjacent_locations("library:worlds/wod-london/locations/camden-market")
    ids = {loc.id for loc in adj}
    assert ids == {"camden", "chalk-farm"}


async def test_path_between(world: WorldService) -> None:
    await _seed_world(world, "wod-london")
    await _seed_location(
        world,
        "wod-london",
        "a",
        connections=[{"to": "b", "via": "street", "duration_min": 5}],
    )
    await _seed_location(
        world,
        "wod-london",
        "b",
        connections=[
            {"to": "a", "via": "street", "duration_min": 5},
            {"to": "c", "via": "street", "duration_min": 7},
        ],
    )
    await _seed_location(
        world,
        "wod-london",
        "c",
        connections=[{"to": "b", "via": "street", "duration_min": 7}],
    )

    path = await world.path_between(
        "library:worlds/wod-london/locations/a",
        "library:worlds/wod-london/locations/c",
    )
    assert [c.to for c in path] == ["b", "c"]
    assert sum(c.duration_min for c in path) == 12

    # No-op path between identical nodes.
    assert (
        await world.path_between(
            "library:worlds/wod-london/locations/a",
            "library:worlds/wod-london/locations/a",
        )
        == []
    )
    # No route → empty list.
    await _seed_location(world, "wod-london", "d")
    assert (
        await world.path_between(
            "library:worlds/wod-london/locations/a",
            "library:worlds/wod-london/locations/d",
        )
        == []
    )


async def test_locations_within_depth(world: WorldService) -> None:
    await _seed_world(world, "wod-london")
    await _seed_location(world, "wod-london", "city")
    await _seed_location(world, "wod-london", "ward-a", parent_id="city")
    await _seed_location(world, "wod-london", "ward-b", parent_id="city")
    await _seed_location(world, "wod-london", "pub", parent_id="ward-a")

    direct = await world.locations_within("library:worlds/wod-london/locations/city", depth=1)
    assert {loc.id for loc in direct} == {"ward-a", "ward-b"}

    deeper = await world.locations_within("library:worlds/wod-london/locations/city", depth=2)
    assert {loc.id for loc in deeper} == {"ward-a", "ward-b", "pub"}


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------


async def test_list_for_campaign_applies_include_filter(
    world: WorldService, store: StateStore
) -> None:
    await _seed_world(world, "wod-london")
    await _seed_world(world, "faerun")
    await _seed_location(world, "wod-london", "elysium")
    await _seed_location(world, "faerun", "waterdeep")
    await _seed_lore(world, "wod-london", "masquerade", title="Masquerade", keywords=["masquerade"])
    await _seed_lore(world, "faerun", "weave", title="The Weave", keywords=["weave"])

    await _bind_campaign(
        store,
        "camp1",
        [
            ("wod-london", ["locations", "lore"]),
            ("faerun", ["lore"]),  # exclude locations from faerun
        ],
    )

    locations = await world.list_for_campaign("camp1", EntityKind.LOCATION)
    assert {loc.asset_id for loc in locations} == {"elysium"}

    lore = await world.list_for_campaign("camp1", EntityKind.LORE)
    assert {ent.asset_id for ent in lore} == {"masquerade", "weave"}


# ---------------------------------------------------------------------------
# Cascade-resolved listing + campaign overrides (#600)
# ---------------------------------------------------------------------------


async def test_list_resolved_for_campaign_runs_cascade(
    world: WorldService, store: StateStore
) -> None:
    """Lists carry one row per cascade layer: library-live, override, emergent."""
    await _seed_world(world, "wod-london")
    await _seed_location(world, "wod-london", "camden-market")
    await _seed_location(world, "wod-london", "elysium")
    await _bind_campaign(store, "camp1", [("wod-london", [])])

    await world.upsert_override(
        "camp1", "location", "elysium", {"name": "Elysium (Condemned)"}, world_id="wod-london"
    )
    await store.write_emergent(
        campaign_id="camp1",
        kind="location",
        entity_id="bone-orchard",
        frontmatter={"id": "bone-orchard", "name": "Bone Orchard"},
        body="A grim orchard at the edge of town.",
        source="extractor",
    )

    rows = await world.list_resolved_for_campaign("camp1", EntityKind.LOCATION)
    by_id = {r.asset_id: r for r in rows}
    assert set(by_id) == {"camden-market", "elysium", "bone-orchard"}

    plain = by_id["camden-market"]
    assert plain.source_chain[0].layer == ResolutionLayer.LIBRARY_LIVE
    assert plain.overrides_applied == []

    overridden = by_id["elysium"]
    assert overridden.name == "Elysium (Condemned)"
    assert overridden.source_chain[0].layer == ResolutionLayer.OVERRIDE
    assert overridden.overrides_applied

    emergent = by_id["bone-orchard"]
    assert emergent.world_id is None
    assert emergent.source_chain[0].layer == ResolutionLayer.EMERGENT


async def test_list_resolved_for_campaign_emergent_shadows_library(
    world: WorldService, store: StateStore
) -> None:
    """An emergent sharing a composed asset_id wins the cascade and lists once."""
    await _seed_world(world, "wod-london")
    await _seed_location(world, "wod-london", "elysium")
    await _bind_campaign(store, "camp1", [("wod-london", [])])
    await store.write_emergent(
        campaign_id="camp1",
        kind="location",
        entity_id="elysium",
        frontmatter={"id": "elysium", "name": "Elysium (Rebuilt)"},
        body="",
        source="extractor",
    )

    rows = await world.list_resolved_for_campaign("camp1", EntityKind.LOCATION)
    assert [r.asset_id for r in rows] == ["elysium"]
    assert rows[0].name == "Elysium (Rebuilt)"
    assert rows[0].source_chain[0].layer == ResolutionLayer.EMERGENT
    # The row keeps campaign-local identity rather than being labelled as
    # the composed world's content.
    assert rows[0].world_id is None


async def test_list_resolved_for_campaign_respects_include_filter(
    world: WorldService, store: StateStore
) -> None:
    """Excluded worlds stay excluded; campaign-local emergent rows still list."""
    await _seed_world(world, "wod-london")
    await _seed_world(world, "faerun")
    await _seed_location(world, "wod-london", "elysium")
    await _seed_location(world, "faerun", "waterdeep")
    await _bind_campaign(
        store,
        "camp1",
        [("wod-london", ["locations"]), ("faerun", ["lore"])],
    )
    await store.write_emergent(
        campaign_id="camp1",
        kind="location",
        entity_id="bone-orchard",
        frontmatter={"id": "bone-orchard", "name": "Bone Orchard"},
        body="",
        source="extractor",
    )

    rows = await world.list_resolved_for_campaign("camp1", EntityKind.LOCATION)
    assert {r.asset_id for r in rows} == {"elysium", "bone-orchard"}


async def test_list_resolved_for_campaign_greetings(world: WorldService, store: StateStore) -> None:
    await _seed_world(world, "wod-london")
    await world.create_entity(
        "wod-london",
        "greeting",
        "first-night",
        {"id": "first-night", "name": "First Night", "mood": "tense"},
        body="The city holds its breath.",
    )
    await _bind_campaign(store, "camp1", [("wod-london", [])])

    rows = await world.list_resolved_for_campaign("camp1", "greeting")
    assert [r.asset_id for r in rows] == ["first-night"]
    assert rows[0].source_chain[0].layer == ResolutionLayer.LIBRARY_LIVE


async def test_upsert_override_visible_on_detail_resolve(
    world: WorldService, store: StateStore
) -> None:
    await _seed_world(world, "wod-london")
    await _seed_faction(world, "wod-london", "camarilla", name="Camarilla")
    await _bind_campaign(store, "camp1", [("wod-london", [])])

    await world.upsert_override(
        "camp1", "factions", "camarilla", {"name": "Camarilla (Fractured)"}, world_id="wod-london"
    )

    resolved = await world.resolve("worlds/wod-london/factions/camarilla", "camp1")
    assert resolved.name == "Camarilla (Fractured)"
    assert resolved.overrides_applied


async def test_upsert_override_merges_with_existing_override(
    world: WorldService, store: StateStore
) -> None:
    """PATCH semantics: a second override patch keeps earlier overridden keys."""
    await _seed_world(world, "wod-london")
    await _seed_faction(world, "wod-london", "camarilla", name="Camarilla")
    await _bind_campaign(store, "camp1", [("wod-london", [])])

    await world.upsert_override(
        "camp1", "faction", "camarilla", {"name": "Camarilla (Fractured)"}, world_id="wod-london"
    )
    await world.upsert_override(
        "camp1", "faction", "camarilla", {"kind": "shadow-court"}, world_id="wod-london"
    )

    resolved = await world.resolve("worlds/wod-london/factions/camarilla", "camp1")
    assert resolved.name == "Camarilla (Fractured)"
    assert resolved.frontmatter["kind"] == "shadow-court"


async def test_upsert_override_rejects_emergent_shadowed_entity(
    world: WorldService, store: StateStore
) -> None:
    """Emergent content wins the cascade, so an override could never surface."""
    await _seed_world(world, "wod-london")
    await _bind_campaign(store, "camp1", [("wod-london", [])])
    await store.write_emergent(
        campaign_id="camp1",
        kind="item",
        entity_id="bone-knife",
        frontmatter={"id": "bone-knife", "name": "Bone Knife"},
        body="",
        source="extractor",
    )

    with pytest.raises(OverrideTargetError):
        await world.upsert_override(
            "camp1", "item", "bone-knife", {"name": "X"}, world_id="wod-london"
        )


async def test_upsert_override_resolves_world_from_composition(
    world: WorldService, store: StateStore
) -> None:
    """Without an explicit world_id, the owning world comes from the composition."""
    await _seed_world(world, "wod-london")
    await _seed_faction(world, "wod-london", "camarilla", name="Camarilla")
    await _bind_campaign(store, "camp1", [("wod-london", [])])

    resolved_world = await world.upsert_override(
        "camp1", "faction", "camarilla", {"name": "Camarilla (Fractured)"}
    )
    assert resolved_world == "wod-london"

    resolved = await world.resolve("worlds/wod-london/factions/camarilla", "camp1")
    assert resolved.name == "Camarilla (Fractured)"


async def test_upsert_override_unresolvable_world_raises(
    world: WorldService, store: StateStore
) -> None:
    await _seed_world(world, "wod-london")
    await _bind_campaign(store, "camp1", [("wod-london", [])])

    with pytest.raises(WorldNotFoundError):
        await world.upsert_override("camp1", "item", "ghost", {"name": "X"})


async def test_upsert_override_rejects_missing_target(
    world: WorldService, store: StateStore
) -> None:
    """A typo'd id must not leave an orphan override file on disk."""
    await _seed_world(world, "wod-london")
    await _bind_campaign(store, "camp1", [("wod-london", [])])

    with pytest.raises(WorldNotFoundError):
        await world.upsert_override(
            "camp1", "item", "no-such-item", {"name": "X"}, world_id="wod-london"
        )


async def test_upsert_override_rejects_characters_and_unknown_kinds(
    world: WorldService,
) -> None:
    with pytest.raises(WorldError):
        await world.upsert_override("camp1", "character", "alistair", {}, world_id="wod-london")
    with pytest.raises(WorldError):
        await world.upsert_override("camp1", "widget", "x", {}, world_id="wod-london")


# ---------------------------------------------------------------------------
# Version pinning (track_latest=false) and the cascade list
# ---------------------------------------------------------------------------


async def test_list_resolved_for_campaign_pinned_ref_enumerates_snapshot(
    world: WorldService, store: StateStore
) -> None:
    """Pinned (track_latest=false) refs list their bind-time snapshot.

    Entities added to the live world after binding must not leak in, and
    snapshotted entities deleted from the live world must survive — matching
    the per-entity resolve path, which prefers snapshots for pinned refs.
    """
    await _seed_world(world, "wod-london")
    await _seed_location(world, "wod-london", "old-square")
    await _seed_location(world, "wod-london", "doomed-alley")
    await store.upsert_campaign(campaign_id="camp-pinned", name="camp-pinned")
    await store.upsert_world_ref(
        campaign_id="camp-pinned",
        world_id="wod-london",
        priority=1,
        include=None,
        track_latest=False,  # pinned; snapshot_on_bind defaults to True
    )

    await _seed_location(world, "wod-london", "post-bind-plaza")
    await world.delete_entity("wod-london", "location", "doomed-alley")

    rows = {
        r.asset_id: r
        for r in await world.list_resolved_for_campaign("camp-pinned", EntityKind.LOCATION)
    }
    assert set(rows) == {"old-square", "doomed-alley"}
    assert rows["doomed-alley"].source_chain[0].layer == ResolutionLayer.LIBRARY_SNAPSHOT

    # A live (track_latest) campaign over the same world sees the live index.
    await _bind_campaign(store, "camp-live", [("wod-london", [])])
    live = {
        r.asset_id for r in await world.list_resolved_for_campaign("camp-live", EntityKind.LOCATION)
    }
    assert live == {"old-square", "post-bind-plaza"}


async def test_upsert_override_accepts_snapshot_only_target(
    world: WorldService, store: StateStore
) -> None:
    """Pinned campaigns can override entities that only survive in their snapshot."""
    await _seed_world(world, "wod-london")
    await _seed_location(world, "wod-london", "doomed-alley", name="Doomed Alley")
    await store.upsert_campaign(campaign_id="camp-pinned", name="camp-pinned")
    await store.upsert_world_ref(
        campaign_id="camp-pinned",
        world_id="wod-london",
        priority=1,
        include=None,
        track_latest=False,
    )
    await world.delete_entity("wod-london", "location", "doomed-alley")

    await world.upsert_override(
        "camp-pinned", "location", "doomed-alley", {"name": "The Alley, Remembered"}
    )

    rows = {
        r.asset_id: r
        for r in await world.list_resolved_for_campaign("camp-pinned", EntityKind.LOCATION)
    }
    assert rows["doomed-alley"].name == "The Alley, Remembered"
    assert rows["doomed-alley"].overrides_applied


# ---------------------------------------------------------------------------
# Lore keyword triggers
# ---------------------------------------------------------------------------


async def test_lore_by_keyword(world: WorldService, store: StateStore) -> None:
    await _seed_world(world, "wod-london")
    await _seed_lore(
        world,
        "wod-london",
        "masquerade",
        title="The Masquerade",
        keywords=["masquerade", "breach"],
    )
    await _seed_lore(world, "wod-london", "cam", title="Camarilla", keywords=["camarilla"])
    await _bind_campaign(store, "camp1", [("wod-london", [])])

    hits = await world.lore_by_keyword("masquerade", "camp1")
    assert {h.id for h in hits} == {"masquerade"}

    # Too short to match (under default min_length).
    assert await world.lore_by_keyword("the", "camp1") == []


async def test_lore_for_post_extracts_triggers(world: WorldService, store: StateStore) -> None:
    await _seed_world(world, "wod-london")
    await _seed_lore(world, "wod-london", "masq", title="Masquerade", keywords=["masquerade"])
    await _seed_lore(world, "wod-london", "cam", title="Camarilla", keywords=["camarilla"])
    await _bind_campaign(store, "camp1", [("wod-london", [])])

    hits = await world.lore_for_post("She breached the masquerade in front of mortals.", "camp1")
    assert {h.id for h in hits} == {"masq"}


async def test_search_lore_scores_and_orders(world: WorldService, store: StateStore) -> None:
    await _seed_world(world, "wod-london")
    await _seed_lore(world, "wod-london", "a", title="Camarilla", keywords=["camarilla"])
    await _seed_lore(world, "wod-london", "b", title="History", keywords=["history"])
    await _bind_campaign(store, "camp1", [("wod-london", [])])

    hits = await world.search_lore("camarilla", "camp1")
    assert hits and hits[0].id == "a"


# ---------------------------------------------------------------------------
# Calendar / season / holiday
# ---------------------------------------------------------------------------


async def test_calendar_and_season_for_campaign(world: WorldService, store: StateStore) -> None:
    cal_yaml = {
        "epoch": "2024-01-01",
        "months": [{"name": f"M{i + 1}", "days": 30} for i in range(12)],
        "seasons": [
            {"name": "spring", "start_month": 3, "start_day": 1},
            {"name": "summer", "start_month": 6, "start_day": 1},
            {"name": "autumn", "start_month": 9, "start_day": 1},
            {"name": "winter", "start_month": 12, "start_day": 1},
        ],
        "holidays": [{"name": "Hallows Eve", "month": 10, "day": 31}],
    }
    await _seed_world(world, "wod-london", calendar=cal_yaml)
    await _bind_campaign(store, "camp1", [("wod-london", [])])

    cal = await world.calendar_for_campaign("camp1")
    assert {m.name for m in cal.months} >= {"M1", "M12"}
    assert {s.name for s in cal.seasons} == {"spring", "summer", "autumn", "winter"}

    season = await world.season_for(InGameTime(moment=datetime(2024, 4, 1)), "camp1")
    assert season is not None and season.name == "spring"

    season_winter = await world.season_for(InGameTime(moment=datetime(2024, 1, 15)), "camp1")
    assert season_winter is not None and season_winter.name == "winter"

    holiday = await world.holiday_at(InGameTime(moment=datetime(2024, 10, 31)), "camp1")
    assert holiday is not None and holiday.name == "Hallows Eve"

    assert await world.holiday_at(InGameTime(moment=datetime(2024, 11, 1)), "camp1") is None


# ---------------------------------------------------------------------------
# Weather (determinism + override)
# ---------------------------------------------------------------------------


async def test_weather_is_deterministic(world: WorldService, store: StateStore) -> None:
    await _seed_world(world, "wod-london")
    await _seed_location(
        world, "wod-london", "camden", climate_zone="temperate-oceanic", indoor=False
    )
    await _bind_campaign(store, "camp1", [("wod-london", [])])
    when = InGameTime(moment=datetime(2024, 11, 1, 18, 0, 0))

    w1 = await world.weather_for("wod-london", "camden", when, "camp1")
    w2 = await world.weather_for("wod-london", "camden", when, "camp1")
    assert w1 == w2


async def test_weather_varies_per_location(world: WorldService, store: StateStore) -> None:
    await _seed_world(world, "wod-london")
    await _seed_location(world, "wod-london", "camden", climate_zone="temperate-oceanic")
    await _seed_location(world, "wod-london", "shoreditch", climate_zone="temperate-oceanic")
    await _bind_campaign(store, "camp1", [("wod-london", [])])
    when = InGameTime(moment=datetime(2024, 11, 1, 18, 0, 0))

    w1 = await world.weather_for("wod-london", "camden", when, "camp1")
    w2 = await world.weather_for("wod-london", "shoreditch", when, "camp1")
    # The full record is unlikely to be equal across two distinct locations.
    assert (w1.kind, w1.temperature_c, w1.wind_kph) != (w2.kind, w2.temperature_c, w2.wind_kph)


async def test_weather_for_missing_location_returns_procedural_default(
    world: WorldService, store: StateStore
) -> None:
    await _seed_world(world, "wod-london")
    await _bind_campaign(store, "camp1", [("wod-london", [])])
    when = InGameTime(moment=datetime(2024, 11, 1, 18, 0, 0))

    w = await world.weather_for("wod-london", "no-such-place", when, "camp1")
    assert isinstance(w, Weather)
    assert w.source == "procedural"


async def test_weather_indoor_is_clear(world: WorldService, store: StateStore) -> None:
    await _seed_world(world, "wod-london")
    await _seed_location(world, "wod-london", "elysium", indoor=True, kind="building")
    await _bind_campaign(store, "camp1", [("wod-london", [])])
    w = await world.weather_for(
        "wod-london",
        "elysium",
        InGameTime(moment=datetime(2024, 11, 1, 18, 0, 0)),
        "camp1",
    )
    assert w.kind == WeatherKind.CLEAR
    assert w.source == "procedural"


async def test_weather_override_wins(world: WorldService, store: StateStore) -> None:
    await _seed_world(world, "wod-london")
    await _seed_location(world, "wod-london", "camden", climate_zone="temperate-oceanic")
    await _bind_campaign(store, "camp1", [("wod-london", [])])
    when = InGameTime(moment=datetime(2024, 11, 1, 18, 0, 0))

    forced = Weather(kind=WeatherKind.STORM, summary="thunder cracks", source="override")
    await world.override_weather("wod-london", "camden", forced, "camp1")

    w = await world.weather_for("wod-london", "camden", when, "camp1")
    assert w.kind == WeatherKind.STORM
    assert w.source == "override"


# ---------------------------------------------------------------------------
# Faction state
# ---------------------------------------------------------------------------


async def test_faction_state_round_trip(world: WorldService, store: StateStore) -> None:
    await _seed_world(world, "wod-london")
    await _seed_faction(world, "wod-london", "camarilla", name="The Camarilla")
    await _bind_campaign(store, "camp1", [("wod-london", [])])

    ref = "library:worlds/wod-london/factions/camarilla"
    blank = await world.faction_state(ref, "camp1")
    assert blank.goals == [] and blank.current_focus == ""

    updated = await world.update_faction_state(
        ref,
        "camp1",
        {
            "current_focus": "protect the masquerade",
            "public_perception": "feared, respected",
            "secrets": ["the prince's lineage"],
            "goals": [
                {"id": "g1", "description": "expel the sabbat", "progress": 0.2},
            ],
            "resources": {"influence": 5},
        },
    )
    assert updated.current_focus == "protect the masquerade"
    assert updated.resources == {"influence": 5}
    assert len(updated.goals) == 1 and updated.goals[0].progress == 0.2

    refetched = await world.faction_state(ref, "camp1")
    assert refetched.current_focus == "protect the masquerade"
    assert refetched.goals[0].id == "g1"


# ---------------------------------------------------------------------------
# Fork
# ---------------------------------------------------------------------------


async def test_fork_world_copies_directory_and_reindexes(
    world: WorldService, library: LibraryService
) -> None:
    await _seed_world(world, "wod-london", name="London by Night")
    await _seed_location(world, "wod-london", "camden")
    await _seed_lore(world, "wod-london", "masq", title="Masquerade", keywords=["masquerade"])

    forked = await world.fork_world("wod-london", "wod-paris")
    assert forked.id == "wod-paris"

    paris_locs = await world.list_locations("wod-paris")
    assert {loc.id for loc in paris_locs} == {"camden"}
    paris_lore = await world.list_lore("wod-paris")
    assert {ent.id for ent in paris_lore} == {"masq"}

    # Original world is untouched.
    london_locs = await world.list_locations("wod-london")
    assert {loc.id for loc in london_locs} == {"camden"}


async def test_fork_world_collision(world: WorldService) -> None:
    await _seed_world(world, "wod-london")
    await _seed_world(world, "wod-paris")
    with pytest.raises(WorldError):
        await world.fork_world("wod-london", "wod-paris")


async def test_fork_world_missing_source(world: WorldService) -> None:
    with pytest.raises(WorldNotFoundError):
        await world.fork_world("nope", "still-nope")


# ---------------------------------------------------------------------------
# Path traversal — delete_world / fork_world reject unsafe world ids before
# any filesystem operation runs. Regression for issue #30.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_id",
    [
        "../escape",
        "../../escape",
        "..",
        ".hidden",
        "a/b",
        "a\\b",
        "with space",
        "",
    ],
)
async def test_delete_world_rejects_unsafe_world_id(
    world: WorldService, store: StateStore, bad_id: str, tmp_path
) -> None:
    # Drop a sentinel directory at a sibling of data/library/worlds/ to prove
    # the validator stops rmtree before it ever touches the filesystem.
    sentinel = tmp_path / "sentinel"
    sentinel.mkdir()
    (sentinel / "keep.txt").write_text("do not delete")

    with pytest.raises(InvalidRefError):
        await world.delete_world(bad_id)

    assert sentinel.exists()
    assert (sentinel / "keep.txt").read_text() == "do not delete"


@pytest.mark.parametrize(
    "src_id,dst_id",
    [
        ("../evil", "ok"),
        ("ok", "../evil"),
        ("..", "ok"),
        (".hidden", "ok"),
        ("a/b", "ok"),
    ],
)
async def test_fork_world_rejects_unsafe_world_id(
    world: WorldService, src_id: str, dst_id: str, tmp_path
) -> None:
    sentinel = tmp_path / "sentinel"
    sentinel.mkdir()
    (sentinel / "keep.txt").write_text("do not overwrite")

    with pytest.raises(InvalidRefError):
        await world.fork_world(src_id, dst_id)

    assert (sentinel / "keep.txt").read_text() == "do not overwrite"


async def test_delete_world_traversal_does_not_remove_outside_root(
    world: WorldService, store: StateStore, tmp_path
) -> None:
    """End-to-end check: even with a path that would resolve outside the
    worlds root, the validator rejects the call before rmtree runs.
    """
    outside = store.data_root.parent / "important_dir"
    outside.mkdir()
    (outside / "treasure.txt").write_text("keep me")

    # The literal string a caller could send to an HTTP endpoint.
    with pytest.raises(InvalidRefError):
        await world.delete_world("../../important_dir")

    assert outside.exists()
    assert (outside / "treasure.txt").read_text() == "keep me"


# ---------------------------------------------------------------------------
# Promotion (non-character kinds)
# ---------------------------------------------------------------------------


async def test_promote_to_library_routes_through_library(
    world: WorldService, store: StateStore
) -> None:
    await _seed_world(world, "wod-london")
    await _bind_campaign(store, "camp1", [("wod-london", [])])
    await store.write_emergent(
        campaign_id="camp1",
        kind="location",
        entity_id="bone-orchard",
        frontmatter={"id": "bone-orchard", "name": "Bone Orchard", "kind": "outdoor"},
        body="A grim orchard at the edge of town.",
        source="extractor",
    )

    path = await world.promote_to_library("camp1", "location", "bone-orchard", "wod-london")
    assert path.replace("\\", "/").endswith("wod-london/locations/bone-orchard.md")

    # The library now contains the row.
    promoted = await world.get_location("wod-london", "bone-orchard")
    assert promoted.name == "Bone Orchard"


async def test_promote_character_rejected(world: WorldService) -> None:
    with pytest.raises(WorldError):
        await world.promote_to_library("camp1", "character", "x", "y")
