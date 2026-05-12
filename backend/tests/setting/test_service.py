"""Tests for SettingService.

Covers CRUD-by-kind, composition-aware listing, spatial queries, cross-setting
variants, lore keyword triggers, calendar/season/holiday queries, procedural
weather (determinism + override), faction state, and fork.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from grimoire.library import LibraryService
from grimoire.setting import SettingService
from grimoire.setting.errors import SettingError, SettingNotFoundError
from grimoire.state_store import StateStore
from grimoire.types.common import EntityKind, InGameTime
from grimoire.types.setting import Weather, WeatherKind

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_setting(
    setting: SettingService,
    setting_id: str,
    *,
    name: str = "London by Night",
    calendar: dict | None = None,
    atmosphere: dict | None = None,
) -> None:
    await setting.create_setting(
        setting_id,
        {
            "id": setting_id,
            "name": name,
            "tags": ["wod"],
            "calendar": calendar or {},
            "atmosphere": atmosphere or {"default_register": "low"},
            "version": 1,
        },
    )


async def _seed_location(
    setting: SettingService,
    setting_id: str,
    location_id: str,
    *,
    name: str | None = None,
    parent_id: str | None = None,
    connections: list[dict] | None = None,
    climate_zone: str | None = None,
    indoor: bool = False,
    kind: str = "outdoor",
) -> None:
    await setting.create_entity(
        setting_id,
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
    setting: SettingService,
    setting_id: str,
    lore_id: str,
    *,
    title: str,
    keywords: list[str],
    secrecy: str = "public",
) -> None:
    await setting.create_entity(
        setting_id,
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
    setting: SettingService,
    setting_id: str,
    faction_id: str,
    *,
    name: str,
) -> None:
    await setting.create_entity(
        setting_id,
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
        await store.upsert_setting_ref(
            campaign_id=campaign_id,
            setting_id=sid,
            priority=i,
            include=include,
            track_latest=True,
        )


# ---------------------------------------------------------------------------
# CRUD + listing
# ---------------------------------------------------------------------------


async def test_create_get_list_locations(setting: SettingService) -> None:
    await _seed_setting(setting, "wod-london")
    await _seed_location(setting, "wod-london", "camden-market", climate_zone="temperate-oceanic")
    await _seed_location(setting, "wod-london", "elysium", indoor=True, kind="building")

    locations = await setting.list_locations("wod-london")
    assert {loc.id for loc in locations} == {"camden-market", "elysium"}

    market = await setting.get_location("wod-london", "camden-market")
    assert market.climate_zone == "temperate-oceanic"
    assert market.indoor is False


async def test_character_kind_rejected(setting: SettingService) -> None:
    await _seed_setting(setting, "wod-london")
    with pytest.raises(SettingError):
        await setting.create_entity("wod-london", "character", "alistair", {"id": "alistair"})


# ---------------------------------------------------------------------------
# Spatial queries
# ---------------------------------------------------------------------------


async def test_adjacent_locations_via_parent_and_connections(setting: SettingService) -> None:
    await _seed_setting(setting, "wod-london")
    await _seed_location(setting, "wod-london", "camden")
    await _seed_location(
        setting,
        "wod-london",
        "camden-market",
        parent_id="camden",
        connections=[{"to": "chalk-farm", "via": "street", "duration_min": 8}],
    )
    await _seed_location(setting, "wod-london", "chalk-farm")

    adj = await setting.adjacent_locations("wod-london", "camden-market")
    ids = {loc.id for loc in adj}
    assert ids == {"camden", "chalk-farm"}


async def test_path_between(setting: SettingService) -> None:
    await _seed_setting(setting, "wod-london")
    await _seed_location(
        setting,
        "wod-london",
        "a",
        connections=[{"to": "b", "via": "street", "duration_min": 5}],
    )
    await _seed_location(
        setting,
        "wod-london",
        "b",
        connections=[
            {"to": "a", "via": "street", "duration_min": 5},
            {"to": "c", "via": "street", "duration_min": 7},
        ],
    )
    await _seed_location(
        setting,
        "wod-london",
        "c",
        connections=[{"to": "b", "via": "street", "duration_min": 7}],
    )

    path = await setting.path_between("wod-london", "a", "c")
    assert [c.to for c in path] == ["b", "c"]
    assert sum(c.duration_min for c in path) == 12

    # No-op path between identical nodes.
    assert await setting.path_between("wod-london", "a", "a") == []
    # No route → empty list.
    await _seed_location(setting, "wod-london", "d")
    assert await setting.path_between("wod-london", "a", "d") == []


async def test_locations_within_depth(setting: SettingService) -> None:
    await _seed_setting(setting, "wod-london")
    await _seed_location(setting, "wod-london", "city")
    await _seed_location(setting, "wod-london", "ward-a", parent_id="city")
    await _seed_location(setting, "wod-london", "ward-b", parent_id="city")
    await _seed_location(setting, "wod-london", "pub", parent_id="ward-a")

    direct = await setting.locations_within("wod-london", "city", depth=1)
    assert {loc.id for loc in direct} == {"ward-a", "ward-b"}

    deeper = await setting.locations_within("wod-london", "city", depth=2)
    assert {loc.id for loc in deeper} == {"ward-a", "ward-b", "pub"}


# ---------------------------------------------------------------------------
# Composition + cross-setting
# ---------------------------------------------------------------------------


async def test_list_for_campaign_applies_include_filter(
    setting: SettingService, store: StateStore
) -> None:
    await _seed_setting(setting, "wod-london")
    await _seed_setting(setting, "faerun")
    await _seed_location(setting, "wod-london", "elysium")
    await _seed_location(setting, "faerun", "waterdeep")
    await _seed_lore(
        setting, "wod-london", "masquerade", title="Masquerade", keywords=["masquerade"]
    )
    await _seed_lore(setting, "faerun", "weave", title="The Weave", keywords=["weave"])

    await _bind_campaign(
        store,
        "camp1",
        [
            ("wod-london", ["locations", "lore"]),
            ("faerun", ["lore"]),  # exclude locations from faerun
        ],
    )

    locations = await setting.list_for_campaign("camp1", EntityKind.LOCATION)
    assert {loc.asset_id for loc in locations} == {"elysium"}

    lore = await setting.list_for_campaign("camp1", EntityKind.LORE)
    assert {ent.asset_id for ent in lore} == {"masquerade", "weave"}


async def test_cross_setting_lookup_by_asset_id(setting: SettingService) -> None:
    await _seed_setting(setting, "wod-london")
    await _seed_setting(setting, "faerun")
    await _seed_location(setting, "wod-london", "orchard")
    await _seed_location(setting, "faerun", "orchard")

    found = await setting.cross_setting_lookup("orchard", EntityKind.LOCATION)
    assert {ent.setting_id for ent in found} == {"wod-london", "faerun"}

    excluded = await setting.cross_setting_lookup(
        "orchard", EntityKind.LOCATION, exclude_setting="wod-london"
    )
    assert {ent.setting_id for ent in excluded} == {"faerun"}


# ---------------------------------------------------------------------------
# Lore keyword triggers
# ---------------------------------------------------------------------------


async def test_lore_by_keyword(setting: SettingService, store: StateStore) -> None:
    await _seed_setting(setting, "wod-london")
    await _seed_lore(
        setting,
        "wod-london",
        "masquerade",
        title="The Masquerade",
        keywords=["masquerade", "breach"],
    )
    await _seed_lore(setting, "wod-london", "cam", title="Camarilla", keywords=["camarilla"])
    await _bind_campaign(store, "camp1", [("wod-london", [])])

    hits = await setting.lore_by_keyword("masquerade", "camp1")
    assert {h.id for h in hits} == {"masquerade"}

    # Too short to match (under default min_length).
    assert await setting.lore_by_keyword("the", "camp1") == []


async def test_lore_for_post_extracts_triggers(setting: SettingService, store: StateStore) -> None:
    await _seed_setting(setting, "wod-london")
    await _seed_lore(setting, "wod-london", "masq", title="Masquerade", keywords=["masquerade"])
    await _seed_lore(setting, "wod-london", "cam", title="Camarilla", keywords=["camarilla"])
    await _bind_campaign(store, "camp1", [("wod-london", [])])

    hits = await setting.lore_for_post("She breached the masquerade in front of mortals.", "camp1")
    assert {h.id for h in hits} == {"masq"}


async def test_search_lore_scores_and_orders(setting: SettingService, store: StateStore) -> None:
    await _seed_setting(setting, "wod-london")
    await _seed_lore(setting, "wod-london", "a", title="Camarilla", keywords=["camarilla"])
    await _seed_lore(setting, "wod-london", "b", title="History", keywords=["history"])
    await _bind_campaign(store, "camp1", [("wod-london", [])])

    hits = await setting.search_lore("camarilla", "camp1")
    assert hits and hits[0].id == "a"


# ---------------------------------------------------------------------------
# Calendar / season / holiday
# ---------------------------------------------------------------------------


async def test_calendar_and_season_for_campaign(setting: SettingService, store: StateStore) -> None:
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
    await _seed_setting(setting, "wod-london", calendar=cal_yaml)
    await _bind_campaign(store, "camp1", [("wod-london", [])])

    cal = await setting.calendar_for_campaign("camp1")
    assert {m.name for m in cal.months} >= {"M1", "M12"}
    assert {s.name for s in cal.seasons} == {"spring", "summer", "autumn", "winter"}

    season = await setting.season_for(InGameTime(moment=datetime(2024, 4, 1)), "camp1")
    assert season is not None and season.name == "spring"

    season_winter = await setting.season_for(InGameTime(moment=datetime(2024, 1, 15)), "camp1")
    assert season_winter is not None and season_winter.name == "winter"

    holiday = await setting.holiday_at(InGameTime(moment=datetime(2024, 10, 31)), "camp1")
    assert holiday is not None and holiday.name == "Hallows Eve"

    assert await setting.holiday_at(InGameTime(moment=datetime(2024, 11, 1)), "camp1") is None


# ---------------------------------------------------------------------------
# Weather (determinism + override)
# ---------------------------------------------------------------------------


async def test_weather_is_deterministic(setting: SettingService, store: StateStore) -> None:
    await _seed_setting(setting, "wod-london")
    await _seed_location(
        setting, "wod-london", "camden", climate_zone="temperate-oceanic", indoor=False
    )
    await _bind_campaign(store, "camp1", [("wod-london", [])])
    when = InGameTime(moment=datetime(2024, 11, 1, 18, 0, 0))

    w1 = await setting.weather_for("wod-london", "camden", when, "camp1")
    w2 = await setting.weather_for("wod-london", "camden", when, "camp1")
    assert w1 == w2


async def test_weather_varies_per_location(setting: SettingService, store: StateStore) -> None:
    await _seed_setting(setting, "wod-london")
    await _seed_location(setting, "wod-london", "camden", climate_zone="temperate-oceanic")
    await _seed_location(setting, "wod-london", "shoreditch", climate_zone="temperate-oceanic")
    await _bind_campaign(store, "camp1", [("wod-london", [])])
    when = InGameTime(moment=datetime(2024, 11, 1, 18, 0, 0))

    w1 = await setting.weather_for("wod-london", "camden", when, "camp1")
    w2 = await setting.weather_for("wod-london", "shoreditch", when, "camp1")
    # The full record is unlikely to be equal across two distinct locations.
    assert (w1.kind, w1.temperature_c, w1.wind_kph) != (w2.kind, w2.temperature_c, w2.wind_kph)


async def test_weather_indoor_is_clear(setting: SettingService, store: StateStore) -> None:
    await _seed_setting(setting, "wod-london")
    await _seed_location(setting, "wod-london", "elysium", indoor=True, kind="building")
    await _bind_campaign(store, "camp1", [("wod-london", [])])
    w = await setting.weather_for(
        "wod-london",
        "elysium",
        InGameTime(moment=datetime(2024, 11, 1, 18, 0, 0)),
        "camp1",
    )
    assert w.kind == WeatherKind.CLEAR
    assert w.source == "procedural"


async def test_weather_override_wins(setting: SettingService, store: StateStore) -> None:
    await _seed_setting(setting, "wod-london")
    await _seed_location(setting, "wod-london", "camden", climate_zone="temperate-oceanic")
    await _bind_campaign(store, "camp1", [("wod-london", [])])
    when = InGameTime(moment=datetime(2024, 11, 1, 18, 0, 0))

    forced = Weather(kind=WeatherKind.STORM, summary="thunder cracks", source="override")
    await setting.override_weather("wod-london", "camden", forced, "camp1")

    w = await setting.weather_for("wod-london", "camden", when, "camp1")
    assert w.kind == WeatherKind.STORM
    assert w.source == "override"


# ---------------------------------------------------------------------------
# Faction state
# ---------------------------------------------------------------------------


async def test_faction_state_round_trip(setting: SettingService, store: StateStore) -> None:
    await _seed_setting(setting, "wod-london")
    await _seed_faction(setting, "wod-london", "camarilla", name="The Camarilla")
    await _bind_campaign(store, "camp1", [("wod-london", [])])

    ref = "library:settings/wod-london/factions/camarilla"
    blank = await setting.faction_state(ref, "camp1")
    assert blank.goals == [] and blank.current_focus == ""

    updated = await setting.update_faction_state(
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

    refetched = await setting.faction_state(ref, "camp1")
    assert refetched.current_focus == "protect the masquerade"
    assert refetched.goals[0].id == "g1"


# ---------------------------------------------------------------------------
# Fork
# ---------------------------------------------------------------------------


async def test_fork_setting_copies_directory_and_reindexes(
    setting: SettingService, library: LibraryService
) -> None:
    await _seed_setting(setting, "wod-london", name="London by Night")
    await _seed_location(setting, "wod-london", "camden")
    await _seed_lore(setting, "wod-london", "masq", title="Masquerade", keywords=["masquerade"])

    forked = await setting.fork_setting("wod-london", "wod-paris")
    assert forked.id == "wod-paris"

    paris_locs = await setting.list_locations("wod-paris")
    assert {loc.id for loc in paris_locs} == {"camden"}
    paris_lore = await setting.list_lore("wod-paris")
    assert {ent.id for ent in paris_lore} == {"masq"}

    # Original setting is untouched.
    london_locs = await setting.list_locations("wod-london")
    assert {loc.id for loc in london_locs} == {"camden"}


async def test_fork_setting_collision(setting: SettingService) -> None:
    await _seed_setting(setting, "wod-london")
    await _seed_setting(setting, "wod-paris")
    with pytest.raises(SettingError):
        await setting.fork_setting("wod-london", "wod-paris")


async def test_fork_setting_missing_source(setting: SettingService) -> None:
    with pytest.raises(SettingNotFoundError):
        await setting.fork_setting("nope", "still-nope")


# ---------------------------------------------------------------------------
# Promotion (non-character kinds)
# ---------------------------------------------------------------------------


async def test_promote_to_library_routes_through_library(
    setting: SettingService, store: StateStore
) -> None:
    await _seed_setting(setting, "wod-london")
    await _bind_campaign(store, "camp1", [("wod-london", [])])
    await store.write_emergent(
        campaign_id="camp1",
        kind="location",
        entity_id="bone-orchard",
        frontmatter={"id": "bone-orchard", "name": "Bone Orchard", "kind": "outdoor"},
        body="A grim orchard at the edge of town.",
        source="extractor",
    )

    path = await setting.promote_to_library("camp1", "location", "bone-orchard", "wod-london")
    assert path.endswith("wod-london/locations/bone-orchard.md")

    # The library now contains the row.
    promoted = await setting.get_location("wod-london", "bone-orchard")
    assert promoted.name == "Bone Orchard"


async def test_promote_character_rejected(setting: SettingService) -> None:
    with pytest.raises(SettingError):
        await setting.promote_to_library("camp1", "character", "x", "y")
