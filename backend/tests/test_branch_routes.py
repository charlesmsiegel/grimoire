"""A closed branch is read-only (play controls III, gate 2).

A scene is closed while another member of its branch group is absorbed: the
campaign's files hold that member's past now. `runs.require_scene_open` is its
own check, applied per door like `test_scene_freeze.py`'s -- so this file has a
row per door too, because a door that forgets it is open again and nothing
else would say so.
"""

from __future__ import annotations

import pytest

import grimoire.store as store
from grimoire import routes
from tests.llm_fakes import FakeLLM


def seed(client, module=None):
    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-or-x"})
    wid = store.worlds.create_world("Realm")
    cid = store.campaigns.create_campaign("Saltmarch", wid, module=module)
    sid = store.scenes.create_scene(cid, "Mara")
    for name in ("Mara", "Winifred"):
        response = client.post(f"/api/campaigns/{cid}/characters", json={"name": name})
        assert response.status_code == 200, response.text
        actor = response.json()["character"]
        response = client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast", json={"id": actor})
        assert response.status_code == 200, response.text
    return cid, sid


def _turn(client, cid, sid, content):
    fake = FakeLLM([['Mara answers.\n```handoff\n{"next":null}\n```']])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/chat",
                    json={"content": content, "speaker_ref": "characters:mara"})
    assert r.status_code == 200, r.text


@pytest.fixture
def closed(client):
    """`(cid, absorbed, closed)`: a two-turn scene, a whole-transcript branch of
    it with a replay begun inside (so the replay-turn door has a session), and
    the source absorbed -- which closes the branch."""
    cid, sid = seed(client)
    _turn(client, cid, sid, "Hello")
    _turn(client, cid, sid, "Onward")
    b = store.branch.branch_scene(cid, sid, 3)
    store.replay.begin(cid, b, 2)
    store.scenes.mark_absorbed(cid, sid, "x", "y")
    return cid, sid, b


def test_every_transcript_door_refuses_a_closed_scene(closed, client):
    """One row per door, as test_scene_freeze.py: the guard is per call site."""
    cid, absorbed, sid = closed
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    rid = next(m["response_id"] for m in store.scenes.read_scene(cid, sid)["messages"]
               if m.get("response_id"))
    calls = [
        ("post", f"{base}/chat", {"content": "Hi"}),
        ("post", f"{base}/retry", None),
        ("post", f"{base}/regenerate", None),
        ("post", f"{base}/responses/{rid}/regenerate", {}),
        ("post", f"{base}/replay/turn", None),
        ("post", f"{base}/roll-proposal", {"proposal": "nope", "action": "accept"}),
        ("put", f"{base}/messages/0", {"content": "edited"}),
        ("delete", f"{base}/messages/0", None),
        ("post", f"{base}/messages/0/retcon", {"content": "retconned"}),
        ("put", f"{base}/messages/0/excluded", {"excluded": True}),
        ("post", f"{base}/alternates/v-nope", None),
        ("post", f"{base}/replay", {"index": 0}),
        ("post", f"{base}/roll", {"notation": "1d20"}),
        ("post", f"{base}/cast", {"kind": "characters", "id": "nobody", "version": "default",
                                  "role": "npc"}),
        ("delete", f"{base}/cast/characters/x", None),
        ("post", f"{base}/cast/batch", {"refs": []}),
        ("post", f"{base}/cast/emergent", {"name": "Seraphine", "role": "npc"}),
        ("put", f"{base}/location", {"location": "saltmarch-docks"}),
        ("put", f"{base}/datetime", {"datetime": "1834-04-02"}),
        ("delete", f"{base}/responses/{rid}", None),
        ("post", f"{base}/responses/{rid}/variants/v/activate", None),
        ("post", f"{base}/responses/{rid}/character",
         {"name": "Seraphine", "passage": "Mara answers.", "source_text": "Mara answers."}),
        ("post", f"{base}/absorb", None),
        ("put", f"{base}/chronicle", {"one_line": "x", "summary": "y", "keywords": [],
                                      "timeline_events": [], "edits": [], "commit_token": "t"}),
        ("post", f"{base}/first-post", {"text": "The lamps are lit."}),
        ("post", f"{base}/start-from-greeting", {"greeting": "g1"}),
        ("post", f"{base}/branch", {"through": 0}),
    ]
    before = store.scenes._scene_path(cid, sid).read_bytes()
    for method, path, body in calls:
        r = getattr(client, method)(path, **({"json": body} if body is not None else {}))
        assert r.status_code == 409, f"{method} {path} answered {r.status_code}: {r.text}"
        assert r.json().get("kind") == "branch_closed", f"{method} {path}: {r.json()}"
        assert r.json()["closed_by"]["sid"] == absorbed
    assert store.scenes._scene_path(cid, sid).read_bytes() == before


def test_a_manual_check_on_a_closed_scene_is_refused(client):
    """Set up in full, as test_scene_freeze's check case: an unresolvable check
    is a 400 raised before the lock, which would pass with no guard at all."""
    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-or-x"})
    wid = store.worlds.create_world("Realm")
    cid = store.campaigns.create_campaign("Saltmarch", wid)
    client.put(f"/api/campaigns/{cid}/module", json={"module": "pool-basic"})
    chid = client.post(f"/api/worlds/{wid}/characters",
                       json={"name": "Mara"}).json()["character"]
    sid = client.post(f"/api/campaigns/{cid}/scenes",
                      json={"title": "Mara"}).json()["id"]
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast",
                json={"kind": "characters", "id": chid, "version": "default",
                      "role": "npc"})
    store.sheets.write(cid, "characters", chid, "medium",
                       {"vigor": 3, "brawl": 2, "wits": 2, "occult": 1}, expected=None)
    body = {"check": "brawl", "actor": "characters:mara", "difficulty": 6}
    assert client.post(f"/api/campaigns/{cid}/scenes/{sid}/check",
                       json=body).status_code == 200
    b = store.branch.branch_scene(cid, sid, 0)
    assert client.post(f"/api/campaigns/{cid}/scenes/{b}/check", json=body).status_code == 200
    store.scenes.mark_absorbed(cid, sid, "x", "y")

    r = client.post(f"/api/campaigns/{cid}/scenes/{b}/check", json=body)

    assert r.status_code == 409, r.text
    assert r.json()["kind"] == "branch_closed"


def test_a_closed_scene_can_still_be_renamed_deleted_and_its_replay_stopped(closed, client):
    cid, _, sid = closed
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    r = client.post(f"{base}/replay/accept")
    assert r.json().get("kind") != "branch_closed", r.text
    r = client.post(f"{base}/replay/cancel", json={})
    assert r.status_code == 200, r.text
    r = client.put(base, json={"title": "Winifred"})
    assert r.status_code == 200, r.text
    new = r.json()["id"]
    r = client.delete(f"/api/campaigns/{cid}/scenes/{new}")
    assert r.status_code == 200, r.text


def test_cutting_into_the_absorbed_member_reopens_the_others(closed, client):
    cid, absorbed, sid = closed
    r = client.delete(f"/api/campaigns/{cid}/scenes/{absorbed}/messages/0")
    assert r.status_code == 200, r.text
    r = client.put(f"/api/campaigns/{cid}/scenes/{sid}/messages/0", json={"content": "edited"})
    assert r.status_code == 200, r.text


def _open_group(client):
    cid, sid = seed(client)
    store.scenes.append_message(cid, sid, "user", "Mara waits.")
    b = store.branch.branch_scene(cid, sid, 0)
    return cid, sid, b


def test_absorbing_waits_for_every_member_of_the_group(client):
    cid, sid, b = _open_group(client)
    ident = store.scenes.scene_identity(cid, b)
    client.app.state.runs.start_or_existing(
        ("scene", cid, ident), "turn", "chat", "a1", ident,
        {"campaign": "Saltmarch", "scene": "Mara (branch)"})
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    r = client.put(f"{base}/chronicle", json={
        "one_line": "x", "summary": "y", "keywords": [], "timeline_events": [], "edits": [],
        "commit_token": "t"})
    assert r.status_code == 409 and r.json()["kind"] == "scene_busy", r.text
    r = client.post(f"{base}/absorb")
    assert r.status_code == 409 and r.json()["kind"] == "scene_busy", r.text
    assert not store.scenes.read_scene_meta(cid, sid).get("done")


def test_an_open_member_is_not_refused(client):
    cid, sid, b = _open_group(client)
    for s in (sid, b):
        r = client.put(f"/api/campaigns/{cid}/scenes/{s}/messages/0", json={"content": "edited"})
        assert r.status_code == 200, r.text


# --- POST .../branch and closed_by on the payload ------------------------------


def test_branch_route_answers_the_sibling(client):
    cid, sid = seed(client)
    store.scenes.append_message(cid, sid, "user", "Mara waits.")
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/branch", json={"through": 0})
    assert r.status_code == 200, r.text
    assert r.json()["scene"]["meta"]["title"] == "Mara (branch)"
    assert r.json()["id"] in {s["id"] for s in store.scenes.list_scenes(cid)}
    assert [m["content"] for m in r.json()["scene"]["messages"]] == ["Mara waits."]


def test_branch_route_refusals(client):
    cid, sid = seed(client)
    store.scenes.append_message(cid, sid, "user", "Mara waits.")
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    r = client.post(f"{base}/branch", json={"through": 9})
    assert r.status_code == 400, r.text
    b = client.post(f"{base}/branch", json={"through": 0}).json()["id"]
    store.scenes.mark_absorbed(cid, sid, "x", "y")
    r = client.post(f"{base}/branch", json={"through": 0})
    assert r.status_code == 409 and r.json().get("kind") == "absorbed_use_fork", r.text
    r = client.post(f"/api/campaigns/{cid}/scenes/{b}/branch", json={"through": 0})
    assert r.status_code == 409 and r.json().get("kind") == "branch_closed", r.text
    assert r.json()["closed_by"]["sid"] == sid
    r = client.post(f"/api/campaigns/{cid}/scenes/999--nobody/branch", json={"through": 0})
    assert r.status_code == 404, r.text


def test_branch_route_takes_a_title(client):
    cid, sid = seed(client)
    store.scenes.append_message(cid, sid, "user", "Mara waits.")
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/branch",
                    json={"through": 0, "title": "Winifred"})
    assert r.status_code == 200, r.text
    assert r.json()["scene"]["meta"]["title"] == "Winifred"


def test_branch_route_stamps_the_campaign_revision(client):
    cid, sid = seed(client)
    store.scenes.append_message(cid, sid, "user", "Mara waits.")
    before = store.revision.current(cid)
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/branch", json={"through": 0})
    assert r.status_code == 200, r.text
    assert store.revision.current(cid) != before


def test_the_scene_payload_names_who_closed_it(closed, client):
    cid, absorbed, sid = closed
    for params in ({}, {"limit": 1}):
        meta = client.get(f"/api/campaigns/{cid}/scenes/{sid}", params=params).json()["meta"]
        assert meta["closed_by"] == {"sid": absorbed, "title": "Mara"}
    meta = client.get(f"/api/campaigns/{cid}/scenes/{absorbed}").json()["meta"]
    assert "closed_by" not in meta
