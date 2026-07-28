import json

from grimoire.store import campaigns, weather, worlds

SPLIT_YEAR = {
    "id": "split-year", "name": "Split Year", "persistence": 0.5,
    "seasons": [
        {"name": "first", "from": 0.0, "to": 182 / 365,
         "temperature": [{"name": "mild", "weight": 1}],
         "conditions": [{"name": "clear", "weight": 1}],
         "wind": [{"name": "calm", "weight": 1}]},
        {"name": "second", "from": 182 / 365, "to": 0.0,
         "temperature": [{"name": "hot", "weight": 1}],
         "conditions": [{"name": "dry", "weight": 1}],
         "wind": [{"name": "still", "weight": 1}]},
    ]}


def setup(monkeypatch, tmp_path):
    """Returns (cid, location id). `create_entity` slugifies the name it is given."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    worlds.create_world("Realm")
    cid = campaigns.create_campaign("Saltmarch Chronicle", "realm")
    from grimoire.store import entities
    lid = entities.create_entity(campaigns.campaign_root(cid), "locations", "Saltmarch Docks")
    return cid, lid


def use_split_year(tmp_path, cid):
    (tmp_path / "climates").mkdir(exist_ok=True)
    (tmp_path / "climates" / "split-year.json").write_text(
        json.dumps(SPLIT_YEAR), encoding="utf-8")
    (campaigns.campaign_root(cid) / "climate.json").write_text(
        json.dumps({"default_climate": "split-year"}), encoding="utf-8")


def test_resolves_all_three_axes(monkeypatch, tmp_path):
    cid, lid = setup(monkeypatch, tmp_path)
    got = weather.current_weather(cid, lid, "2026-06-14T09:00")
    assert set(got) >= {"temperature", "condition", "wind", "climate", "season"}


def test_same_moment_resolves_identically(monkeypatch, tmp_path):
    cid, lid = setup(monkeypatch, tmp_path)
    a = weather.current_weather(cid, lid, "2026-06-14T09:00")
    b = weather.current_weather(cid, lid, "2026-06-14T09:00")
    assert a == b


def test_one_night_block_is_stable_across_midnight(monkeypatch, tmp_path):
    cid, lid = setup(monkeypatch, tmp_path)
    late = weather.current_weather(cid, lid, "2026-06-14T23:00")
    early = weather.current_weather(cid, lid, "2026-06-15T01:00")
    assert late == early


def test_a_night_spanning_a_season_boundary_stays_in_one_season(monkeypatch, tmp_path):
    # The boundary must actually fall on the crossed date, or the test passes
    # even when the season is (wrongly) looked up per queried moment. The
    # shipped fallback's winter wraps the year end, so 31 Dec / 1 Jan are both
    # winter and would prove nothing. 182/365 puts the boundary on 2 July.
    cid, lid = setup(monkeypatch, tmp_path)
    use_split_year(tmp_path, cid)

    late = weather.current_weather(cid, lid, "2026-07-01T23:00")
    early = weather.current_weather(cid, lid, "2026-07-02T01:00")
    assert late["season"] == "first"      # 1 July's night, owned by 1 July
    assert early["season"] == "first"     # the same block, not 2 July's season
    assert late == early


def test_the_season_does_change_at_the_boundary_dawn(monkeypatch, tmp_path):
    # The mirror of the test above: the boundary is real, it just takes effect
    # at the first block the new date owns rather than at midnight.
    cid, lid = setup(monkeypatch, tmp_path)
    use_split_year(tmp_path, cid)
    assert weather.current_weather(cid, lid, "2026-07-02T06:00")["season"] == "second"


def test_a_malformed_calendar_config_does_not_raise(monkeypatch, tmp_path):
    # read_calendar catches JSONDecodeError but not a valid-JSON non-object.
    cid, lid = setup(monkeypatch, tmp_path)
    (campaigns.campaign_root(cid) / "calendar.json").write_text("[]", encoding="utf-8")
    assert weather.current_weather(cid, lid, "2026-06-14T09:00") is None


def test_a_moment_at_the_calendar_lower_bound_does_not_raise(monkeypatch, tmp_path):
    # 0001-01-01T01:00 parses, but its block belongs to the previous date, and
    # `date.fromordinal(0)` raises. Weather must degrade, not take the turn down.
    cid, lid = setup(monkeypatch, tmp_path)
    assert weather.current_weather(cid, lid, "0001-01-01T01:00") is None


def test_different_blocks_can_differ(monkeypatch, tmp_path):
    cid, lid = setup(monkeypatch, tmp_path)
    seen = {tuple(sorted(weather.current_weather(cid, lid, f"2026-06-{d:02d}T09:00").items()))
            for d in range(1, 29)}
    assert len(seen) > 1


def test_missing_moment_returns_none(monkeypatch, tmp_path):
    cid, lid = setup(monkeypatch, tmp_path)
    assert weather.current_weather(cid, lid, None) is None


def test_missing_location_returns_none(monkeypatch, tmp_path):
    cid, lid = setup(monkeypatch, tmp_path)
    assert weather.current_weather(cid, None, "2026-06-14T09:00") is None


def test_unparseable_moment_returns_none(monkeypatch, tmp_path):
    cid, lid = setup(monkeypatch, tmp_path)
    assert weather.current_weather(cid, lid, "not-a-date") is None


def test_an_out_of_range_clock_returns_none(monkeypatch, tmp_path):
    # minutes_of raises CalendarError for 25:00 rather than returning None.
    cid, lid = setup(monkeypatch, tmp_path)
    assert weather.current_weather(cid, lid, "2026-06-14T25:00") is None


def test_date_without_a_clock_resolves(monkeypatch, tmp_path):
    cid, lid = setup(monkeypatch, tmp_path)
    assert weather.current_weather(cid, lid, "2026-06-14") is not None


def test_deleted_location_still_resolves(monkeypatch, tmp_path):
    cid, lid = setup(monkeypatch, tmp_path)
    assert weather.current_weather(cid, "gone-away", "2026-06-14T09:00") is not None


def test_the_reported_climate_is_the_one_actually_drawn_from(monkeypatch, tmp_path):
    # The returned climate id labels the draw; if it were reported from the
    # campaign default while the location overrode it, the HUD would name a
    # climate that produced none of the weather shown.
    cid, lid = setup(monkeypatch, tmp_path)
    use_split_year(tmp_path, cid)
    got = weather.current_weather(cid, lid, "2026-06-14T09:00")
    assert got["climate"] == "split-year"
    assert got["condition"] == "clear"  # split-year's only first-season condition


def test_locations_in_one_zone_share_weather(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    worlds.create_world("Realm")
    cid = campaigns.create_campaign("Saltmarch Chronicle", "realm")
    from grimoire.store import entities
    root = campaigns.campaign_root(cid)
    a = entities.create_entity(root, "locations", "Saltmarch Docks",
                               fields={"weather_zone": "saltmarch"})
    b = entities.create_entity(root, "locations", "Saltmarch Market",
                               fields={"weather_zone": "saltmarch"})
    assert (weather.current_weather(cid, a, "2026-06-14T09:00")
            == weather.current_weather(cid, b, "2026-06-14T09:00"))


# ---- override resolution ----

def provider_of(cid):
    from grimoire.store import calendars
    return calendars.get_provider(calendars.read_calendar(campaigns.campaign_root(cid))["primary"])


def test_a_manual_override_replaces_only_the_axis_it_sets(monkeypatch, tmp_path):
    from grimoire.store.weather import overrides
    cid, lid = setup(monkeypatch, tmp_path)
    base = weather.current_weather(cid, lid, "2026-06-14T09:00")
    overrides.put(cid, provider_of(cid), lid, "2026-06-14", None, {"condition": "blizzard"})
    got = weather.current_weather(cid, lid, "2026-06-14T09:00")
    assert got["condition"] == "blizzard"
    assert got["wind"] == base["wind"]           # still procedural
    assert got["temperature"] == base["temperature"]


def test_per_axis_provenance_is_reported(monkeypatch, tmp_path):
    from grimoire.store.weather import overrides
    cid, lid = setup(monkeypatch, tmp_path)
    overrides.put(cid, provider_of(cid), lid, "2026-06-14", None, {"condition": "blizzard"})
    got = weather.resolve(cid, lid, "2026-06-14T09:00")
    assert got["source"] == {"condition": "manual", "temperature": "procedural",
                             "wind": "procedural"}


def test_an_extractor_override_is_marked_as_such(monkeypatch, tmp_path):
    from grimoire.store.weather import overrides
    cid, lid = setup(monkeypatch, tmp_path)
    overrides.put(cid, provider_of(cid), lid, "2026-06-14", None, {"condition": "rain"},
                  source="extractor")
    assert weather.resolve(cid, lid, "2026-06-14T09:00")["source"]["condition"] == "extractor"


def test_suppress_returns_an_axis_to_the_procedural_value(monkeypatch, tmp_path):
    from grimoire.store.weather import overrides
    cid, lid = setup(monkeypatch, tmp_path)
    base = weather.current_weather(cid, lid, "2026-06-14T09:00")
    p = provider_of(cid)
    overrides.put(cid, p, overrides.DEFAULT_KEY, "2026-06-14", None, {"condition": "blizzard"})
    overrides.put(cid, p, lid, "2026-06-14", None, {}, suppress=["condition"])
    got = weather.resolve(cid, lid, "2026-06-14T09:00")
    assert got["condition"] == base["condition"]
    assert got["source"]["condition"] == "procedural"


def test_an_override_outside_its_span_does_not_apply(monkeypatch, tmp_path):
    from grimoire.store.weather import overrides
    cid, lid = setup(monkeypatch, tmp_path)
    base = weather.current_weather(cid, lid, "2026-06-20T09:00")
    overrides.put(cid, provider_of(cid), lid, "2026-06-14", "2026-06-16", {"condition": "blizzard"})
    assert weather.current_weather(cid, lid, "2026-06-20T09:00") == base


def test_current_weather_keeps_its_narrow_shape(monkeypatch, tmp_path):
    # Prompt assembly reads this; the HUD extras belong to resolve().
    cid, lid = setup(monkeypatch, tmp_path)
    got = weather.current_weather(cid, lid, "2026-06-14T09:00")
    assert set(got) == {"temperature", "condition", "wind", "climate", "season"}


# ---- sweep ----

def test_sweep_reports_only_changed_axes(monkeypatch, tmp_path):
    cid, lid = setup(monkeypatch, tmp_path)
    from grimoire.store import scenes
    sid = scenes.create_scene(cid, "Arrival")
    scenes.set_location(cid, sid, lid)
    rows = weather.sweep(cid, sid, "2026-06-14T09:00", "2026-09-14T09:00")
    before = weather.current_weather(cid, lid, "2026-06-14T09:00")
    after = weather.current_weather(cid, lid, "2026-09-14T09:00")
    changed = {a for a in ("condition", "temperature", "wind") if before[a] != after[a]}
    assert {r["axis"] for r in rows} == changed
    for row in rows:
        assert row["location"] == lid
        assert row["before"] != row["after"]


def test_sweep_is_empty_when_nothing_changed(monkeypatch, tmp_path):
    cid, lid = setup(monkeypatch, tmp_path)
    from grimoire.store import scenes
    sid = scenes.create_scene(cid, "Arrival")
    scenes.set_location(cid, sid, lid)
    assert weather.sweep(cid, sid, "2026-06-14T09:00", "2026-06-14T09:00") == []


def test_sweep_covers_every_distinct_location_the_scene_visited(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    worlds.create_world("Realm")
    cid = campaigns.create_campaign("Saltmarch Chronicle", "realm")
    from grimoire.store import entities, scenes
    root = campaigns.campaign_root(cid)
    a = entities.create_entity(root, "locations", "Saltmarch Docks")
    b = entities.create_entity(root, "locations", "Winifred Hall")
    sid = scenes.create_scene(cid, "Arrival")
    scenes.set_location(cid, sid, a)
    scenes.set_location(cid, sid, b)
    scenes.set_location(cid, sid, a)  # revisited: must not be swept twice
    rows = weather.sweep(cid, sid, "2026-06-14T09:00", "2026-12-14T09:00")
    assert {r["location"] for r in rows} <= {a, b}
    assert len(rows) == len({(r["location"], r["axis"]) for r in rows})


def test_sweep_without_a_previous_moment_reports_nothing(monkeypatch, tmp_path):
    cid, lid = setup(monkeypatch, tmp_path)
    from grimoire.store import scenes
    sid = scenes.create_scene(cid, "Arrival")
    scenes.set_location(cid, sid, lid)
    assert weather.sweep(cid, sid, None, "2026-06-14T09:00") == []
