import json

from grimoire.store import absorb, campaigns, entities, scenes, weather, worlds


def setup(monkeypatch, tmp_path, when="2026-06-14T09:00"):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    worlds.create_world("Realm")
    cid = campaigns.create_campaign("Saltmarch Chronicle", "realm")
    lid = entities.create_entity(campaigns.campaign_root(cid), "locations", "Saltmarch Docks")
    sid = scenes.create_scene(cid, "Arrival")
    scenes.set_location(cid, sid, lid)
    sid = scenes.set_datetime(cid, sid, when).get("id", sid)
    return cid, sid, lid


def test_parse_output_keeps_weather_edits(monkeypatch, tmp_path):
    # parse_output rebuilds an explicit dict of known keys, so an unlisted key
    # is dropped on the floor and every branch below it is unreachable.
    parsed = absorb.parse_output(json.dumps({
        "one_line": "x", "summary": "y",
        "weather_edits": [{"location": "saltmarch-docks", "condition": "blizzard"}]}))
    assert parsed["weather_edits"] == [
        {"location": "saltmarch-docks", "condition": "blizzard", "temperature": "",
         "wind": "", "duration_blocks": "", "note": ""}]


def test_materialize_stages_a_changed_axis(monkeypatch, tmp_path):
    cid, sid, lid = setup(monkeypatch, tmp_path)
    current = weather.current_weather(cid, lid, "2026-06-14T09:00")
    rows = absorb.materialize(cid, sid, {"weather_edits": [{"condition": "blizzard"}]})
    row = next(r for r in rows if r["kind"] == "weather")
    assert row["field"] == "condition"
    assert row["before"] == current["condition"]
    assert row["after"] == "blizzard"
    assert row["target"] == {"kind": "weather", "id": lid}


def test_materialize_stages_nothing_when_narration_matches_the_draw(monkeypatch, tmp_path):
    cid, sid, lid = setup(monkeypatch, tmp_path)
    current = weather.current_weather(cid, lid, "2026-06-14T09:00")
    rows = absorb.materialize(cid, sid, {"weather_edits": [{"condition": current["condition"]}]})
    assert [r for r in rows if r["kind"] == "weather"] == []


def test_materialize_only_stages_the_axes_narration_gave(monkeypatch, tmp_path):
    cid, sid, lid = setup(monkeypatch, tmp_path)
    rows = [r for r in absorb.materialize(cid, sid, {"weather_edits": [{"condition": "hail"}]})
            if r["kind"] == "weather"]
    assert {r["field"] for r in rows} == {"condition"}


def test_materialize_defaults_to_the_scenes_location(monkeypatch, tmp_path):
    cid, sid, lid = setup(monkeypatch, tmp_path)
    rows = [r for r in absorb.materialize(cid, sid, {"weather_edits": [{"condition": "hail"}]})
            if r["kind"] == "weather"]
    assert rows[0]["payload"]["location"] == lid


def test_materialize_needs_a_moment(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    worlds.create_world("Realm")
    cid = campaigns.create_campaign("Saltmarch Chronicle", "realm")
    lid = entities.create_entity(campaigns.campaign_root(cid), "locations", "Saltmarch Docks")
    sid = scenes.create_scene(cid, "Arrival")
    scenes.set_location(cid, sid, lid)
    rows = absorb.materialize(cid, sid, {"weather_edits": [{"condition": "hail"}]})
    assert [r for r in rows if r["kind"] == "weather"] == []


def test_apply_writes_an_extractor_override(monkeypatch, tmp_path):
    cid, sid, lid = setup(monkeypatch, tmp_path)
    rows = [r for r in absorb.materialize(cid, sid, {"weather_edits": [{"condition": "blizzard"}]})
            if r["kind"] == "weather"]
    absorb.apply_edits(cid, rows, sid=sid)
    got = weather.resolve(cid, lid, "2026-06-14T09:00")
    assert got["condition"] == "blizzard"
    assert got["source"]["condition"] == "extractor"


def test_an_applied_override_defaults_to_one_block(monkeypatch, tmp_path):
    # Narration that implies onset rather than extent gets one block, and is
    # re-narratable next turn.
    cid, sid, lid = setup(monkeypatch, tmp_path)
    rows = [r for r in absorb.materialize(cid, sid, {"weather_edits": [{"condition": "blizzard"}]})
            if r["kind"] == "weather"]
    absorb.apply_edits(cid, rows, sid=sid)
    assert weather.resolve(cid, lid, "2026-06-14T09:00")["condition"] == "blizzard"
    assert weather.resolve(cid, lid, "2026-06-14T13:00")["condition"] != "blizzard"


def test_a_stated_duration_extends_the_span(monkeypatch, tmp_path):
    cid, sid, lid = setup(monkeypatch, tmp_path)
    rows = [r for r in absorb.materialize(
        cid, sid, {"weather_edits": [{"condition": "downpour", "duration_blocks": "15"}]})
        if r["kind"] == "weather"]
    absorb.apply_edits(cid, rows, sid=sid)
    assert weather.resolve(cid, lid, "2026-06-16T09:00")["condition"] == "downpour"
    assert weather.resolve(cid, lid, "2026-06-20T09:00")["condition"] != "downpour"


def test_a_manual_override_still_beats_the_extractor(monkeypatch, tmp_path):
    from grimoire.store import calendars
    cid, sid, lid = setup(monkeypatch, tmp_path)
    rows = [r for r in absorb.materialize(cid, sid, {"weather_edits": [{"condition": "drizzle"}]})
            if r["kind"] == "weather"]
    absorb.apply_edits(cid, rows, sid=sid)
    provider = calendars.get_provider(
        calendars.read_calendar(campaigns.campaign_root(cid))["primary"])
    weather.overrides.put(cid, provider, lid, "2026-06-14", None, {"condition": "blizzard"})
    assert weather.resolve(cid, lid, "2026-06-14T09:00")["condition"] == "blizzard"


def test_a_garbled_duration_falls_back_to_one_block(monkeypatch, tmp_path):
    cid, sid, lid = setup(monkeypatch, tmp_path)
    rows = [r for r in absorb.materialize(
        cid, sid, {"weather_edits": [{"condition": "hail", "duration_blocks": "a while"}]})
        if r["kind"] == "weather"]
    absorb.apply_edits(cid, rows, sid=sid)
    assert weather.resolve(cid, lid, "2026-06-14T09:00")["condition"] == "hail"
    assert weather.resolve(cid, lid, "2026-06-14T13:00")["condition"] != "hail"


def test_a_narrated_location_that_does_not_exist_is_dropped(monkeypatch, tmp_path):
    # current_weather answers for any id — a deleted location keeps resolving
    # on purpose — so it cannot tell a typo from a tombstone. Staging one would
    # write under an orphan weather.json key no scene can reach.
    cid, sid, lid = setup(monkeypatch, tmp_path)
    rows = absorb.materialize(cid, sid, {"weather_edits": [
        {"location": "Saltmarch Docks", "condition": "hail"}]})   # a name, not an id
    assert [r for r in rows if r["kind"] == "weather"] == []


def test_a_narrated_location_that_does_exist_is_staged(monkeypatch, tmp_path):
    cid, sid, lid = setup(monkeypatch, tmp_path)
    other = entities.create_entity(campaigns.campaign_root(cid), "locations", "Winifred Hall")
    rows = [r for r in absorb.materialize(cid, sid, {"weather_edits": [
        {"location": other, "condition": "hail"}]}) if r["kind"] == "weather"]
    assert rows and rows[0]["payload"]["location"] == other
