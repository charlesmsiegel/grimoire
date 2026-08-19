"""The retcon, replay and fork endpoints (#78/#79/#80), driven through the real
routes.

The store tests cover the rules. This covers the half the store cannot: that the
retcon route is a different request from the plain edit and answers with the
reversal's report, that a replay's steps are addressed per scene and refuse
across scenes, that the preview prices a replay before anything is cut, and that
forking leaves the original alone.
"""

import importlib

import pytest
from fastapi.testclient import TestClient

from grimoire import store
from grimoire.main import create_app


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    importlib.reload(store)
    return TestClient(create_app())


@pytest.fixture
def scene(client):
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "Saltmarch"}).json()["id"]
    store.scenes.append_message(cid, sid, "user", "player one")
    store.scenes.append_reply(cid, sid, [{"speaker": None, "content": "reply one"}])
    store.scenes.append_message(cid, sid, "user", "player two")
    store.scenes.append_reply(cid, sid, [{"speaker": None, "content": "reply two"}])
    return cid, sid


def _contents(cid, sid):
    return [m["content"] for m in store.scenes.read_scene(cid, sid)["messages"]]


# --- retcon (#78) ----------------------------------------------------------


def test_a_retcon_rewrites_the_post_and_reports_the_reversal(client, scene):
    cid, sid = scene
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/messages/0/retcon",
                    json={"content": "she never said it"})
    assert r.status_code == 200
    body = r.json()
    assert body["was_absorbed"] is False and body["later"] == []
    assert _contents(cid, sid)[0] == "she never said it"


def test_a_retcon_un_absorbs_the_scene_where_a_plain_edit_does_not(client, scene):
    """The two routes differ in exactly this, which is why both exist."""
    cid, sid = scene
    store.scenes.mark_absorbed(cid, sid, "They swore.", "A long night.")
    client.put(f"/api/campaigns/{cid}/scenes/{sid}/messages/0",
               json={"content": "a typo fix"})
    assert store.scenes.read_scene(cid, sid)["meta"].get("done") == "true"

    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/messages/0/retcon",
                    json={"content": "she never said it"})
    assert r.status_code == 200 and r.json()["was_absorbed"] is True
    assert "done" not in store.scenes.read_scene(cid, sid)["meta"]


def test_an_out_of_range_retcon_is_a_400(client, scene):
    cid, sid = scene
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/messages/9/retcon",
                    json={"content": "nope"})
    assert r.status_code == 400


def test_a_retcon_of_an_unknown_scene_is_a_404(client, scene):
    cid, _ = scene
    r = client.post(f"/api/campaigns/{cid}/scenes/nope/messages/0/retcon",
                    json={"content": "x"})
    assert r.status_code == 404


def test_a_retconned_macro_is_resolved_once_at_persist_time(client, scene):
    """The same treatment a fresh send and a plain edit get (#137): a
    `{{roll:1d20}}` left unresolved would re-roll on every later context build."""
    cid, sid = scene
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/messages/0/retcon",
                json={"content": "she rolls {{roll:1d6}}"})
    assert "{{roll:" not in _contents(cid, sid)[0]


# --- replay (#79) ----------------------------------------------------------


def test_a_scene_with_no_replay_reports_null(client, scene):
    cid, sid = scene
    r = client.get(f"/api/campaigns/{cid}/scenes/{sid}/replay")
    assert r.status_code == 200 and r.json() is None


def test_starting_a_replay_cuts_the_scene_and_reports_the_cascade(client, scene):
    cid, sid = scene
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/replay", json={"index": 1})
    assert r.status_code == 200
    assert r.json()["cascade"]["removed"] == 3
    assert _contents(cid, sid) == ["player one"]
    assert client.get(f"/api/campaigns/{cid}/scenes/{sid}/replay").json()["next"] == "generation"


def test_a_replay_in_another_scene_is_a_409_naming_it(client, scene):
    cid, sid = scene
    other = client.post(f"/api/campaigns/{cid}/scenes",
                        json={"title": "The Long Quay"}).json()["id"]
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/replay", json={"index": 1})
    r = client.post(f"/api/campaigns/{cid}/scenes/{other}/replay/accept")
    assert r.status_code == 409
    # The dict detail arrives flattened -- `main.create_app` unwraps a mapping
    # detail so a client reads `kind` off the body rather than out of `detail`.
    assert r.json()["kind"] == "replay_elsewhere" and r.json()["scene"] == sid
    # ... and this scene's own GET still reports no replay of its own.
    assert client.get(f"/api/campaigns/{cid}/scenes/{other}/replay").json() is None


def test_acting_on_a_replay_that_is_not_running_is_a_409(client, scene):
    cid, sid = scene
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/replay/cancel", json={"restore": True})
    assert r.status_code == 409 and r.json()["kind"] == "no_replay"


def test_a_refused_span_is_a_409_with_the_reason(client, scene):
    cid, sid = scene
    store.scenes.append_message(cid, sid, "user", "player three")
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/replay", json={"index": 4})
    assert r.status_code == 409 and r.json()["kind"] == "replay_refused"
    assert len(_contents(cid, sid)) == 5


def test_cancelling_puts_the_scene_back(client, scene):
    cid, sid = scene
    before = _contents(cid, sid)
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/replay", json={"index": 1})
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/replay/cancel", json={"restore": True})
    assert r.status_code == 200 and r.json()["restored"] == 3
    assert _contents(cid, sid) == before
    assert client.get(f"/api/campaigns/{cid}/scenes/{sid}/replay").json() is None


def test_cancelling_defaults_to_putting_the_scene_back(client, scene):
    """A cancel that silently dropped the rest of the scene would be the one
    mistake this flow exists to make recoverable."""
    cid, sid = scene
    before = _contents(cid, sid)
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/replay", json={"index": 1})
    assert client.post(f"/api/campaigns/{cid}/scenes/{sid}/replay/cancel").status_code == 200
    assert _contents(cid, sid) == before


def test_accepting_advances_the_walk(client, scene):
    cid, sid = scene
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/replay", json={"index": 1})
    store.scenes.append_reply(cid, sid, [{"speaker": None, "content": "a fresh reply"}])
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/replay/accept")
    assert r.status_code == 200 and r.json()["turns_left"] == 1


def test_accepting_nothing_is_a_409(client, scene):
    cid, sid = scene
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/replay", json={"index": 1})
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/replay/accept")
    assert r.status_code == 409 and r.json()["kind"] == "replay_refused"


# --- the fork nudge (#80) --------------------------------------------------


def test_the_preview_prices_a_replay_without_cutting_anything(client, scene):
    cid, sid = scene
    r = client.get(f"/api/campaigns/{cid}/scenes/{sid}/replay/preview", params={"index": 1})
    assert r.status_code == 200
    assert r.json() == {"posts": 3, "turns": 2, "threshold": 10, "fork": False, "blocked": ""}
    assert len(_contents(cid, sid)) == 4


def test_the_preview_nudges_a_fork_past_the_threshold(client, scene):
    cid, sid = scene
    client.put("/api/config", json={"replay_fork_threshold": "1"})
    r = client.get(f"/api/campaigns/{cid}/scenes/{sid}/replay/preview", params={"index": 1})
    assert r.json()["threshold"] == 1 and r.json()["fork"] is True


def test_an_out_of_range_preview_is_a_400(client, scene):
    cid, sid = scene
    r = client.get(f"/api/campaigns/{cid}/scenes/{sid}/replay/preview", params={"index": 99})
    assert r.status_code == 400


def test_forking_copies_the_campaign_and_leaves_the_original_alone(client, scene):
    cid, sid = scene
    r = client.post(f"/api/campaigns/{cid}/fork", json={"name": "Run (retcon)"})
    assert r.status_code == 200
    fork = r.json()["id"]
    assert fork != cid
    assert _contents(fork, sid) == _contents(cid, sid)

    store.scenes.append_message(fork, sid, "user", "only in the fork")
    assert "only in the fork" not in _contents(cid, sid)


def test_the_fork_records_where_it_came_from(client, scene):
    cid, _ = scene
    fork = client.post(f"/api/campaigns/{cid}/fork", json={"name": "Run (retcon)"}).json()["id"]
    meta = client.get(f"/api/campaigns/{fork}").json()["meta"]
    assert meta["parent"] == cid and meta["name"] == "Run (retcon)"
    assert meta["world"] == client.get(f"/api/campaigns/{cid}").json()["meta"]["world"]


def test_a_fork_needs_a_name(client, scene):
    cid, _ = scene
    assert client.post(f"/api/campaigns/{cid}/fork", json={"name": "  "}).status_code == 400


def test_forking_an_unknown_campaign_is_a_404(client):
    assert client.post("/api/campaigns/nope/fork", json={"name": "X"}).status_code == 404


def test_a_replay_in_the_fork_leaves_the_original_scene_intact(client, scene):
    """The whole point of the nudge: replay into the copy, and the campaign you
    were playing is still there if it goes wrong."""
    cid, sid = scene
    fork = client.post(f"/api/campaigns/{cid}/fork", json={"name": "Run (retcon)"}).json()["id"]
    client.post(f"/api/campaigns/{fork}/scenes/{sid}/replay", json={"index": 1})
    assert _contents(fork, sid) == ["player one"]
    assert len(_contents(cid, sid)) == 4
