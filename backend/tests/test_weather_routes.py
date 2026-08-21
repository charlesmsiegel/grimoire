import importlib

import pytest
from fastapi.testclient import TestClient

from grimoire import store
from grimoire.main import create_app


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    importlib.reload(store)
    # `with`, so the lifespan runs: producing routes hand their work to a
    # runner that lives on it, and a client without one cannot drive a turn.
    with TestClient(create_app()) as c:
        yield c


def scene(client, when="2026-06-14T09:00"):
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    cid = client.post("/api/campaigns", json={"name": "Saltmarch Chronicle",
                                              "world": wid}).json()["id"]
    lid = client.post(f"/api/campaigns/{cid}/locations",
                      json={"name": "Saltmarch Docks", "body": "A place"}).json()["id"]
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "Arrival"}).json()["id"]
    client.put(f"/api/campaigns/{cid}/scenes/{sid}/location", json={"location": lid})
    r = client.put(f"/api/campaigns/{cid}/scenes/{sid}/datetime", json={"datetime": when})
    sid = r.json().get("id", sid)  # set_datetime renames the scene file on first set
    return cid, sid, lid


# ---- GET ----

def test_scene_weather_resolves_from_the_scene(client):
    cid, sid, lid = scene(client)
    got = client.get(f"/api/campaigns/{cid}/scenes/{sid}/weather").json()
    assert set(got["weather"]) == {"condition", "temperature", "wind"}
    assert got["location"] == lid
    assert got["climate"] and got["season"]


def test_scene_weather_carries_the_active_seasons_tables(client):
    # The popover's selects need these, and the client cannot derive them: the
    # climate may be inherited or fallen back, and the season needs calendar
    # arithmetic.
    cid, sid, lid = scene(client)
    tables = client.get(f"/api/campaigns/{cid}/scenes/{sid}/weather").json()["tables"]
    assert set(tables) == {"temperature", "condition", "wind"}
    assert all(tables[k] for k in tables)
    got = client.get(f"/api/campaigns/{cid}/scenes/{sid}/weather").json()
    for axis, key in (("condition", "condition"), ("temperature", "temperature"),
                      ("wind", "wind")):
        assert got["weather"][axis] in tables[key]


def test_scene_weather_reports_per_axis_provenance(client):
    cid, sid, lid = scene(client)
    got = client.get(f"/api/campaigns/{cid}/scenes/{sid}/weather").json()
    assert set(got["source"].values()) == {"procedural"}


def test_scene_weather_accepts_a_preview_moment(client):
    cid, sid, lid = scene(client)
    a = client.get(f"/api/campaigns/{cid}/scenes/{sid}/weather").json()
    b = client.get(f"/api/campaigns/{cid}/scenes/{sid}/weather",
                   params={"native": "2026-12-25T09:00"}).json()
    assert b["native"] == "2026-12-25T09:00"
    assert b["season"] != a["season"] or b["weather"] != a["weather"]


def test_scene_weather_is_null_without_a_moment(client):
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    cid = client.post("/api/campaigns", json={"name": "C", "world": wid}).json()["id"]
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "Arrival"}).json()["id"]
    assert client.get(f"/api/campaigns/{cid}/scenes/{sid}/weather").json()["weather"] is None


def test_scene_weather_404s_for_an_unknown_scene(client):
    cid, sid, lid = scene(client)
    assert client.get(f"/api/campaigns/{cid}/scenes/nope/weather").status_code == 404


def test_the_weather_route_is_not_captured_by_the_entity_catch_all(client):
    # `@router.get("/campaigns/{cid}/{kind}")` would swallow a later-registered
    # weather GET as an entity-list request for kind "weather".
    cid, sid, lid = scene(client)
    r = client.get(f"/api/campaigns/{cid}/scenes/{sid}/weather")
    assert r.status_code == 200
    assert isinstance(r.json(), dict) and "weather" in r.json()


# ---- PUT ----

def test_put_override_pins_one_axis(client):
    cid, sid, lid = scene(client)
    base = client.get(f"/api/campaigns/{cid}/scenes/{sid}/weather").json()
    r = client.put(f"/api/campaigns/{cid}/weather",
                   json={"location": lid, "start": "2026-06-14", "condition": "blizzard"})
    assert r.status_code == 200 and r.json()["id"]
    got = client.get(f"/api/campaigns/{cid}/scenes/{sid}/weather").json()
    assert got["weather"]["condition"] == "blizzard"
    assert got["source"]["condition"] == "manual"
    assert got["weather"]["wind"] == base["weather"]["wind"]


def test_put_override_accepts_the_campaign_default_target(client):
    cid, sid, lid = scene(client)
    r = client.put(f"/api/campaigns/{cid}/weather",
                   json={"location": "_default", "start": "2026-06-14", "condition": "fog"})
    assert r.status_code == 200
    assert client.get(f"/api/campaigns/{cid}/scenes/{sid}/weather"
                      ).json()["weather"]["condition"] == "fog"


def test_put_override_with_no_axes_is_rejected(client):
    # Such a span appears in no covering stack, so its id is discoverable
    # nowhere and repeated calls accumulate rows no client can see or delete.
    cid, sid, lid = scene(client)
    r = client.put(f"/api/campaigns/{cid}/weather",
                   json={"location": lid, "start": "2026-06-14"})
    assert r.status_code == 400


def test_put_override_rejects_an_unparseable_moment(client):
    cid, sid, lid = scene(client)
    r = client.put(f"/api/campaigns/{cid}/weather",
                   json={"location": lid, "start": "not-a-date", "condition": "fog"})
    assert r.status_code == 400


def test_an_open_ended_override_needs_no_end(client):
    cid, sid, lid = scene(client)
    client.put(f"/api/campaigns/{cid}/weather",
               json={"location": lid, "start": "2026-06-14", "condition": "storm"})
    got = client.get(f"/api/campaigns/{cid}/scenes/{sid}/weather",
                     params={"native": "2031-01-01T09:00"}).json()
    assert got["weather"]["condition"] == "storm"


def test_clearing_a_range_returns_it_to_procedural(client):
    cid, sid, lid = scene(client)
    base = client.get(f"/api/campaigns/{cid}/scenes/{sid}/weather").json()
    client.put(f"/api/campaigns/{cid}/weather",
               json={"location": lid, "start": "2026-06-10", "condition": "storm"})
    r = client.put(f"/api/campaigns/{cid}/weather",
                   json={"location": lid, "start": "2026-06-14", "end": "2026-06-14",
                         "clear": True})
    assert r.status_code == 200 and r.json()["cleared"] == 1
    got = client.get(f"/api/campaigns/{cid}/scenes/{sid}/weather").json()
    assert got["weather"]["condition"] == base["weather"]["condition"]


# ---- DELETE ----

def test_delete_retracts_a_span(client):
    cid, sid, lid = scene(client)
    base = client.get(f"/api/campaigns/{cid}/scenes/{sid}/weather").json()
    made = client.put(f"/api/campaigns/{cid}/weather",
                      json={"location": lid, "start": "2026-06-14",
                            "condition": "blizzard"}).json()
    assert client.delete(f"/api/campaigns/{cid}/weather/{lid}/{made['id']}").status_code == 200
    got = client.get(f"/api/campaigns/{cid}/scenes/{sid}/weather").json()
    assert got["weather"]["condition"] == base["weather"]["condition"]


def test_delete_404s_for_an_unknown_span(client):
    cid, sid, lid = scene(client)
    assert client.delete(f"/api/campaigns/{cid}/weather/{lid}/nope").status_code == 404


def test_the_generated_id_is_addressable_in_the_delete_route(client):
    # The id is a path segment; `/` or a dot-only id would be unaddressable.
    cid, sid, lid = scene(client)
    made = client.put(f"/api/campaigns/{cid}/weather",
                      json={"location": lid, "start": "2026-06-14",
                            "condition": "blizzard"}).json()
    assert "/" not in made["id"] and made["id"].strip(".")
    assert client.delete(f"/api/campaigns/{cid}/weather/{lid}/{made['id']}").status_code == 200


def test_the_covering_stack_is_reported_for_the_hud(client):
    cid, sid, lid = scene(client)
    client.put(f"/api/campaigns/{cid}/weather",
               json={"location": "_default", "start": "2026-06-14", "condition": "clear"})
    client.put(f"/api/campaigns/{cid}/weather",
               json={"location": lid, "start": "2026-06-14", "condition": "fog"})
    stack = client.get(f"/api/campaigns/{cid}/scenes/{sid}/weather").json()["stack"]
    assert [s["condition"] for s in stack] == ["fog", "clear"]
    assert stack[0]["location"] == lid


# ---- clear / resume ----

def test_clearing_one_axis_leaves_the_others(client):
    cid, sid, lid = scene(client)
    client.put(f"/api/campaigns/{cid}/weather",
               json={"location": lid, "start": "2026-06-14",
                     "condition": "storm", "wind": "gale"})
    r = client.post(f"/api/campaigns/{cid}/weather/clear",
                    json={"location": lid, "start": "2026-06-14", "axes": ["condition"]})
    assert r.status_code == 200 and r.json()["cleared"] == 1
    got = client.get(f"/api/campaigns/{cid}/scenes/{sid}/weather").json()
    assert got["source"]["condition"] == "procedural"
    assert got["weather"]["wind"] == "gale"


def test_clearing_at_a_location_leaves_other_locations_inheriting(client):
    # Truncating the _default span would clear everywhere; an inherited span is
    # suppressed at the one location instead.
    cid, sid, lid = scene(client)
    client.put(f"/api/campaigns/{cid}/weather",
               json={"location": "_default", "start": "2026-06-14", "condition": "storm"})
    client.post(f"/api/campaigns/{cid}/weather/clear",
                json={"location": lid, "start": "2026-06-14", "axes": ["condition"]})
    here = client.get(f"/api/campaigns/{cid}/scenes/{sid}/weather").json()
    assert here["source"]["condition"] == "procedural"
    elsewhere = client.get(f"/api/campaigns/{cid}/scenes/{sid}/weather",
                           params={"location": "lighthouse"}).json()
    assert elsewhere["weather"]["condition"] == "storm"


def test_resume_restores_inheritance_for_one_axis(client):
    cid, sid, lid = scene(client)
    client.put(f"/api/campaigns/{cid}/weather",
               json={"location": "_default", "start": "2026-06-14",
                     "condition": "storm", "wind": "gale"})
    client.post(f"/api/campaigns/{cid}/weather/clear",
                json={"location": lid, "start": "2026-06-14"})
    r = client.post(f"/api/campaigns/{cid}/weather/resume",
                    json={"location": lid, "start": "2026-06-14", "axes": ["wind"]})
    assert r.status_code == 200 and r.json()["resumed"] == 1
    got = client.get(f"/api/campaigns/{cid}/scenes/{sid}/weather").json()
    assert got["weather"]["wind"] == "gale"
    assert got["source"]["condition"] == "procedural"


def test_clearing_the_whole_stack_does_not_promote_a_shadowed_span(client):
    # Deleting only the winner would promote the record beneath it: the sky
    # would change rather than return to procedural.
    cid, sid, lid = scene(client)
    base = client.get(f"/api/campaigns/{cid}/scenes/{sid}/weather").json()
    client.put(f"/api/campaigns/{cid}/weather",
               json={"location": lid, "start": "2026-06-14", "condition": "drizzle"})
    client.put(f"/api/campaigns/{cid}/weather",
               json={"location": lid, "start": "2026-06-14", "condition": "storm"})
    client.post(f"/api/campaigns/{cid}/weather/clear",
                json={"location": lid, "start": "2026-06-14", "axes": ["condition"]})
    got = client.get(f"/api/campaigns/{cid}/scenes/{sid}/weather").json()
    assert got["weather"]["condition"] == base["weather"]["condition"]
    assert got["source"]["condition"] == "procedural"


def test_each_stack_entry_carries_its_storage_key_for_delete(client):
    # The key is not the scene's location — a covering span may live under
    # _default — so the delete is uncallable without it.
    cid, sid, lid = scene(client)
    client.put(f"/api/campaigns/{cid}/weather",
               json={"location": "_default", "start": "2026-06-14", "condition": "storm"})
    span = client.get(f"/api/campaigns/{cid}/scenes/{sid}/weather").json()["stack"][0]
    assert span["location"] == "_default"
    assert client.delete(
        f"/api/campaigns/{cid}/weather/{span['location']}/{span['id']}").status_code == 200


def test_a_block_count_bounds_the_span_without_client_calendar_maths(client):
    # "this block" and "the rest of today" are block counts; turning them into
    # native strings client-side means reimplementing month lengths.
    #
    # Asserted on provenance, not the value: the procedural draw at this moment
    # is itself "storm", so comparing values cannot tell an override from a
    # coincidence.
    cid, sid, lid = scene(client)
    client.put(f"/api/campaigns/{cid}/weather",
               json={"location": lid, "start": "2026-06-14T09:00",
                     "condition": "storm", "blocks": 1})
    src = lambda t: client.get(f"/api/campaigns/{cid}/scenes/{sid}/weather",
                               params={"native": t}).json()["source"]["condition"]
    assert src("2026-06-14T09:00") == "manual"
    assert src("2026-06-14T13:00") == "procedural"


def test_a_block_count_can_clear_exactly_one_block(client):
    cid, sid, lid = scene(client)
    client.put(f"/api/campaigns/{cid}/weather",
               json={"location": lid, "start": "2026-06-10", "end": "2026-06-20",
                     "condition": "storm"})
    client.post(f"/api/campaigns/{cid}/weather/clear",
                json={"location": lid, "start": "2026-06-14T09:00", "blocks": 1,
                      "axes": ["condition"]})
    src = lambda t: client.get(f"/api/campaigns/{cid}/scenes/{sid}/weather",
                               params={"native": t}).json()["source"]["condition"]
    assert src("2026-06-14T09:00") == "procedural"   # the one block cleared
    assert src("2026-06-14T13:00") == "manual"       # the rest of the span stands
    assert src("2026-06-12T09:00") == "manual"


def test_clearing_one_block_of_an_open_ended_span_ends_it(client):
    # Truncate and discard, never split: a fresh open-ended fragment a block
    # later would resume the storm immediately and run forever.
    cid, sid, lid = scene(client)
    client.put(f"/api/campaigns/{cid}/weather",
               json={"location": lid, "start": "2026-06-10", "condition": "storm"})
    client.post(f"/api/campaigns/{cid}/weather/clear",
                json={"location": lid, "start": "2026-06-14T09:00", "blocks": 1,
                      "axes": ["condition"]})
    src = lambda t: client.get(f"/api/campaigns/{cid}/scenes/{sid}/weather",
                               params={"native": t}).json()["source"]["condition"]
    assert src("2026-06-12T09:00") == "manual"       # history is not retracted
    assert src("2026-06-14T09:00") == "procedural"
    assert src("2026-06-14T13:00") == "procedural"   # never resumes
    assert src("2027-01-01T09:00") == "procedural"


# ---- from Codex review of #232 ----

def test_an_unknown_axis_is_a_400(client):
    cid, sid, lid = scene(client)
    r = client.post(f"/api/campaigns/{cid}/weather/clear",
                    json={"location": lid, "start": "2026-06-14", "axes": ["to_fixed"]})
    assert r.status_code == 400 and "to_fixed" in r.json()["detail"]


def test_a_block_bounded_put_keeps_its_suppression(client):
    # put_ordinals used to drop suppress, so a suppression-only request passed
    # validation and stored a record setting nothing.
    cid, sid, lid = scene(client)
    client.put(f"/api/campaigns/{cid}/weather",
               json={"location": "_default", "start": "2026-06-14", "condition": "storm"})
    r = client.put(f"/api/campaigns/{cid}/weather",
                   json={"location": lid, "start": "2026-06-14T09:00", "blocks": 1,
                         "suppress": ["condition"]})
    assert r.status_code == 200 and r.json().get("suppress") == ["condition"]
    got = client.get(f"/api/campaigns/{cid}/scenes/{sid}/weather").json()
    assert got["source"]["condition"] == "procedural"


def test_the_scene_read_carries_the_block_ordinal(client):
    cid, sid, lid = scene(client)
    assert isinstance(client.get(f"/api/campaigns/{cid}/scenes/{sid}/weather").json()["ordinal"], int)


def test_advancing_time_reports_the_weather_changes(client):
    # The sweep was unreachable in production: nothing called it.
    cid, sid, lid = scene(client)
    r = client.put(f"/api/campaigns/{cid}/scenes/{sid}/datetime",
                   json={"datetime": "2026-12-14T09:00"})
    assert r.status_code == 200
    changes = r.json()["weather_changes"]
    assert isinstance(changes, list) and changes
    assert {"location", "axis", "before", "after"} == set(changes[0])
    assert all(c["before"] != c["after"] for c in changes)


def test_the_campaign_default_climate_is_readable_and_settable(client):
    # Create-time was the only writer, so the default was immutable afterwards.
    cid, sid, lid = scene(client)
    assert client.get(f"/api/campaigns/{cid}/climate").json()["default_climate"] == "temperate-interior"
    assert client.put(f"/api/campaigns/{cid}/climate",
                      json={"default_climate": "high-desert"}).status_code == 200
    assert client.get(f"/api/campaigns/{cid}/climate").json()["default_climate"] == "high-desert"
    assert client.get(f"/api/campaigns/{cid}/scenes/{sid}/weather").json()["climate"] == "high-desert"


def test_setting_an_unknown_campaign_default_is_rejected(client):
    cid, sid, lid = scene(client)
    assert client.put(f"/api/campaigns/{cid}/climate",
                      json={"default_climate": "no-such"}).status_code == 400


def test_the_override_note_reaches_the_prompt(client):
    # The note exists to give the model context beyond a bare `storm`; if no
    # prompt path reads it, storing it is decoration.
    from grimoire.store import context
    cid, sid, lid = scene(client)
    client.put(f"/api/campaigns/{cid}/weather",
               json={"location": lid, "start": "2026-06-14", "condition": "blizzard",
                     "note": "the Wintertide storm"})
    data = context._assemble(cid, sid)["data"]
    assert data["weather"]["notes"] == ["the Wintertide storm"]
    assert "the Wintertide storm" in context.build_messages(cid, sid)[0]["content"]


def test_a_reversed_range_is_rejected(client):
    # Both endpoints parse, so the span persists with to <= from, covers no
    # block, and appears in no stack — success for an override that can never
    # apply.
    cid, sid, lid = scene(client)
    r = client.put(f"/api/campaigns/{cid}/weather",
                   json={"location": lid, "start": "2026-06-16", "end": "2026-06-14",
                         "condition": "storm"})
    assert r.status_code == 400 and "ends before" in r.json()["detail"]


def test_an_empty_timed_range_is_rejected(client):
    cid, sid, lid = scene(client)
    r = client.put(f"/api/campaigns/{cid}/weather",
                   json={"location": lid, "start": "2026-06-14T08:00",
                         "end": "2026-06-14T08:00", "condition": "storm"})
    assert r.status_code == 400


def test_a_reversed_clear_range_is_rejected(client):
    # _cut given an inverted interval processes it anyway: for an open-ended
    # override it builds the head and discards everything after `start`, so a
    # malformed clear truncates a real override.
    cid, sid, lid = scene(client)
    client.put(f"/api/campaigns/{cid}/weather",
               json={"location": lid, "start": "2026-06-10", "condition": "storm"})
    r = client.post(f"/api/campaigns/{cid}/weather/clear",
                    json={"location": lid, "start": "2026-06-20", "end": "2026-06-12"})
    assert r.status_code == 400
    # The override is untouched.
    assert client.get(f"/api/campaigns/{cid}/scenes/{sid}/weather",
                      params={"native": "2026-06-25T09:00"}).json()["weather"]["condition"] == "storm"


def test_a_reversed_resume_range_is_rejected(client):
    cid, sid, lid = scene(client)
    r = client.post(f"/api/campaigns/{cid}/weather/resume",
                    json={"location": lid, "start": "2026-06-20", "end": "2026-06-12"})
    assert r.status_code == 400


def test_a_suppression_naming_no_real_axis_is_rejected(client):
    # put filters unknown names out, so this would store a record affecting no
    # axis and report a successful override.
    cid, sid, lid = scene(client)
    r = client.put(f"/api/campaigns/{cid}/weather",
                   json={"location": lid, "start": "2026-06-14", "suppress": ["humidity"]})
    assert r.status_code == 400


def test_a_nonpositive_block_count_is_rejected(client):
    # max(1, ...) would turn an empty selection into a one-block override.
    cid, sid, lid = scene(client)
    for bad in (0, -3):
        r = client.put(f"/api/campaigns/{cid}/weather",
                       json={"location": lid, "start": "2026-06-14",
                             "condition": "storm", "blocks": bad})
        assert r.status_code == 400, bad
    r = client.post(f"/api/campaigns/{cid}/weather/clear",
                    json={"location": lid, "start": "2026-06-14", "blocks": 0})
    assert r.status_code == 400


def test_setting_and_suppressing_one_axis_is_rejected(client):
    # Resolution checks suppression first, so this would report a successful
    # authored value for an axis that stays procedural.
    cid, sid, lid = scene(client)
    r = client.put(f"/api/campaigns/{cid}/weather",
                   json={"location": lid, "start": "2026-06-14",
                         "condition": "rain", "suppress": ["condition"]})
    assert r.status_code == 400 and "condition" in r.json()["detail"]


def test_replacing_a_span_is_atomic_and_keeps_its_identity(client):
    cid, sid, lid = scene(client)
    made = client.put(f"/api/campaigns/{cid}/weather",
                      json={"location": lid, "start": "2026-06-10",
                            "condition": "storm", "note": "old"}).json()
    r = client.put(f"/api/campaigns/{cid}/weather/{lid}/{made['id']}",
                   json={"location": lid, "start": "2026-06-10",
                         "condition": "storm", "note": "the Wintertide storm"})
    assert r.status_code == 200
    assert r.json()["id"] == made["id"] and r.json()["seq"] == made["seq"]
    stack = client.get(f"/api/campaigns/{cid}/scenes/{sid}/weather").json()["stack"]
    assert len(stack) == 1 and stack[0]["note"] == "the Wintertide storm"


def test_a_failed_replace_leaves_the_original_standing(client):
    # The delete-then-create pair would have destroyed it before failing.
    cid, sid, lid = scene(client)
    made = client.put(f"/api/campaigns/{cid}/weather",
                      json={"location": lid, "start": "2026-06-10",
                            "condition": "storm"}).json()
    r = client.put(f"/api/campaigns/{cid}/weather/{lid}/{made['id']}",
                   json={"location": lid, "start": "not-a-date", "condition": "storm"})
    assert r.status_code == 400
    assert client.get(f"/api/campaigns/{cid}/scenes/{sid}/weather"
                      ).json()["weather"]["condition"] == "storm"


def test_replace_counts_blocks_from_the_moment_being_viewed(client):
    # A span that began days ago given "this block" here should end after this
    # block, keeping what it already covered.
    cid, sid, lid = scene(client)
    made = client.put(f"/api/campaigns/{cid}/weather",
                      json={"location": lid, "start": "2026-06-10",
                            "condition": "storm"}).json()
    client.put(f"/api/campaigns/{cid}/weather/{lid}/{made['id']}",
               json={"location": lid, "start": "2026-06-10", "condition": "storm",
                     "blocks": 1, "blocks_from": "2026-06-14T09:00"})
    src = lambda t: client.get(f"/api/campaigns/{cid}/scenes/{sid}/weather",
                               params={"native": t}).json()["source"]["condition"]
    assert src("2026-06-12T09:00") == "manual"    # earlier coverage kept
    assert src("2026-06-14T09:00") == "manual"
    assert src("2026-06-14T13:00") == "procedural"


def test_blocks_left_today_counts_a_post_midnight_moment_correctly(client):
    # At 01:00 the block is the previous date's night, whose position is
    # indistinguishable from an ordinary 22:00 night — the client cannot tell
    # a whole day still lies ahead.
    cid, sid, lid = scene(client)
    at = lambda t: client.get(f"/api/campaigns/{cid}/scenes/{sid}/weather",
                              params={"native": t}).json()["blocks_left_today"]
    assert at("2026-06-14T09:00") == 4    # morning: afternoon, evening, night left
    assert at("2026-06-14T22:00") == 1    # night: just itself
    assert at("2026-06-15T01:00") == 6    # that same night, plus all of the 15th
