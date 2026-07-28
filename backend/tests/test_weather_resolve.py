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
