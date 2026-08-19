"""The retcon, replay and fork endpoints (#78/#79/#80), driven through the real
routes.

The store tests cover the rules. This covers the half the store cannot: that the
retcon route is a different request from the plain edit and answers with the
reversal's report, that a replay's steps are addressed per scene and refuse
across scenes, that the preview prices a replay before anything is cut, and that
forking leaves the original alone.
"""

import importlib
import json

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


def test_running_a_turn_over_an_unanswered_reply_is_a_409(client, scene):
    """The client's memory of having run a turn is not what stops it running
    twice — a reload loses that. The server does."""
    cid, sid = scene
    # A usable connection, because the route checks that BEFORE it stages: the
    # player's originals must not be re-posted for a turn that then cannot run.
    conn = client.post("/api/llm-connections", json={
        "kind": "openai_compatible", "name": "Local", "base_url": "http://x",
        "api_key": "sk-x"}).json()["id"]
    client.put("/api/config", json={"active_connection_id": conn})
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/replay", json={"index": 1})
    store.scenes.append_reply(cid, sid, [{"speaker": None, "content": "a fresh reply"}])
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/replay/turn")
    assert r.status_code == 409 and r.json()["kind"] == "replay_refused"
    assert "waiting on you" in r.json()["detail"]


def test_the_session_says_whether_a_reply_is_waiting(client, scene):
    cid, sid = scene
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/replay", json={"index": 1})
    assert client.get(f"/api/campaigns/{cid}/scenes/{sid}/replay").json()["pending"] is False
    store.scenes.append_reply(cid, sid, [{"speaker": None, "content": "a fresh reply"}])
    assert client.get(f"/api/campaigns/{cid}/scenes/{sid}/replay").json()["pending"] is True


def test_starting_a_replay_never_ships_the_backlog(client, scene):
    """The one response that has the whole cut transcript in hand is the one
    that must not return it — the GET's rule holds however the session is asked
    for."""
    cid, sid = scene
    body = client.post(f"/api/campaigns/{cid}/scenes/{sid}/replay", json={"index": 1}).json()
    assert body["steps"] == 3 and body["turns_left"] == 2
    assert "reply one" not in str(body)


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


def test_a_replay_in_the_fork_leaves_the_original_scene_intact(client, scene):
    """The whole point of the nudge: replay into the copy, and the campaign you
    were playing is still there if it goes wrong."""
    cid, sid = scene
    fork = client.post(f"/api/campaigns/{cid}/fork", json={"name": "Run (retcon)"}).json()["id"]
    client.post(f"/api/campaigns/{fork}/scenes/{sid}/replay", json={"index": 1})
    assert _contents(fork, sid) == ["player one"]
    assert len(_contents(cid, sid)) == 4


# --- the whole point, end to end -------------------------------------------


def test_a_re_absorbed_retcon_badges_what_the_later_scene_answered(client):
    """Retcon an old scene, absorb it again, and the review says which of its
    findings a scene played after it has already answered differently.

    The three pieces are tested apart above; this is the claim they add up to,
    and the one thing none of them proves — that the badge survives the round
    trip through `materialize`'s real staged rows.
    """
    from grimoire import routes

    from tests.llm_fakes import FakeOpenRouterComplete

    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    client.post(f"/api/worlds/{wid}/characters",
                json={"name": "Seraphine", "version_name": "main"})
    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-or-x"})

    def scene(title):
        sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": title}).json()["id"]
        client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast",
                    json={"kind": "characters", "id": "seraphine", "version": "main",
                          "role": "npc"})
        return sid

    def absorb_and_save(sid, state):
        client.app.dependency_overrides[routes.get_llm] = lambda: FakeOpenRouterComplete(
            json.dumps({"one_line": "They spoke.", "summary": "A long night.",
                        "keywords": [], "timeline_events": [],
                        "character_state_edits": [{"id": "seraphine", "current_state": state}]}))
        body = client.post(f"/api/campaigns/{cid}/scenes/{sid}/absorb").json()
        client.put(f"/api/campaigns/{cid}/scenes/{sid}/chronicle",
                   json={"one_line": body["one_line"], "summary": body["summary"],
                         "keywords": [], "timeline_events": [], "edits": body["edits"],
                         "commit_token": body["commit_token"]})
        return body

    first = scene("Saltmarch")
    store.scenes.append_message(cid, first, "user", "She swore the pact.")
    later = scene("The Long Quay")
    store.scenes.append_message(cid, later, "user", "She broke it.")

    absorb_and_save(first, "Loyal.")
    absorb_and_save(later, "Faithless now.")

    # The retcon: the first scene did not go that way after all.
    r = client.post(f"/api/campaigns/{cid}/scenes/{first}/messages/0/retcon",
                    json={"content": "She refused the pact."})
    assert r.status_code == 200 and r.json()["later"] == [later]

    # ... and the re-extraction disagrees with what the later scene recorded.
    # `changes`, not `citation`: this extraction quotes nobody, so no provenance
    # row was written for it and the coarser attribution is the one that can
    # answer — which is the order those sources are consulted in.
    review = absorb_and_save(first, "Wary of everyone.")
    assert [(c["id"], c["scene"], c["source"]) for c in review["contradictions"]] == [
        ("character_state:seraphine", later, "changes")]


def test_the_ordinary_end_of_scene_review_badges_nothing(client):
    """Absorbing the newest scene has no later scene to disagree with, which is
    why the pass is unconditional rather than a mode the caller asks for."""
    from grimoire import routes

    from tests.llm_fakes import FakeOpenRouterComplete

    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-or-x"})
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    store.scenes.append_message(cid, sid, "user", "We entered the crypt.")
    client.app.dependency_overrides[routes.get_llm] = lambda: FakeOpenRouterComplete(
        '{"one_line": "They entered.", "summary": "The party entered.",'
        ' "keywords": [], "timeline_events": []}')
    assert client.post(f"/api/campaigns/{cid}/scenes/{sid}/absorb").json()["contradictions"] == []
