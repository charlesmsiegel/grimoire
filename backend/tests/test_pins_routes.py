"""The pin/exclude endpoints (#129), driven through the real routes.

The store tests cover the rules; this covers the half the store cannot — that
the TTL is anchored to the transcript the server reads rather than a number the
client guessed, that a rule reaches the assembled prompt through the API alone,
and that the panel is handed names it can render.
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
    # `with`, so the lifespan runs: producing routes hand their work to a
    # runner that lives on it, and a client without one cannot drive a turn.
    with TestClient(create_app()) as c:
        yield c


def _scene(client):
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "Saltmarch"}).json()["id"]
    return cid, sid


def _lore(client, cid, name="Tide oath", body="The tide keeps its promises."):
    return client.post(f"/api/campaigns/{cid}/lore", json={"name": name, "body": body}).json()["id"]


def _pin(client, cid, **body):
    return client.post(f"/api/campaigns/{cid}/pins", json=body)


def _post(cid, sid, text="we make for the harbor"):
    """Grow the transcript. There is no plain append endpoint -- posts arrive
    through /chat, which would need the LLM for something this is not about."""
    store.scenes.append_message(cid, sid, "user", text)


def test_a_new_campaign_has_no_rules(client):
    cid, sid = _scene(client)
    r = client.get(f"/api/campaigns/{cid}/pins", params={"sid": sid})
    assert r.status_code == 200 and r.json() == {"pins": []}


def test_a_rule_round_trips_with_the_name_the_panel_shows(client):
    cid, sid = _scene(client)
    lid = _lore(client, cid)
    assert _pin(client, cid, ref=f"lore:{lid}", mode="pin", sid=sid, ttl_posts=3).status_code == 200

    rows = client.get(f"/api/campaigns/{cid}/pins", params={"sid": sid}).json()["pins"]
    assert len(rows) == 1
    assert rows[0]["ref"] == f"lore:{lid}"
    assert rows[0]["kind"] == "lore" and rows[0]["id"] == lid
    assert rows[0]["name"] == "Tide oath"
    assert rows[0]["missing"] is False
    assert rows[0]["mode"] == "pin" and rows[0]["scope"] == "scene"
    assert rows[0]["remaining"] == 3


def test_the_post_answers_in_the_same_shape_the_list_does(client):
    """The client renders what it just wrote; two shapes for one record is how
    that ends up disagreeing with the read that follows it."""
    cid, sid = _scene(client)
    lid = _lore(client, cid)
    written = _pin(client, cid, ref=f"lore:{lid}", mode="pin", sid=sid).json()["pin"]
    listed = client.get(f"/api/campaigns/{cid}/pins", params={"sid": sid}).json()["pins"][0]
    assert written == listed


def test_the_ttl_is_anchored_to_the_transcript_the_server_reads(client):
    """The client never sends the post count -- it would be guessing at the
    length of a transcript the server already has."""
    cid, sid = _scene(client)
    lid = _lore(client, cid)
    for text in ("we make for the harbor", "the tide turns"):
        _post(cid, sid, text)
    _pin(client, cid, ref=f"lore:{lid}", mode="pin", sid=sid, ttl_posts=1)

    assert client.get(f"/api/campaigns/{cid}/pins", params={"sid": sid}).json()["pins"][0]["remaining"] == 1
    _post(cid, sid, "and again")
    assert client.get(f"/api/campaigns/{cid}/pins", params={"sid": sid}).json()["pins"] == []


def test_a_pin_set_through_the_api_reaches_the_assembled_prompt(client):
    cid, sid = _scene(client)
    lid = client.post(f"/api/campaigns/{cid}/lore",
                      json={"name": "Tide oath", "body": "The tide keeps its promises.",
                            "keys": "dragon"}).json()["id"]
    _post(cid, sid, "hello")

    def world_info():
        secs = client.get(f"/api/campaigns/{cid}/scenes/{sid}/context").json()["sections"]
        return next((s for s in secs if s["label"] == "World info"), None)

    assert world_info() is None                       # 'dragon' never said
    _pin(client, cid, ref=f"lore:{lid}", mode="pin", sid=sid)
    assert "tide keeps its promises" in world_info()["text"]
    assert world_info()["pinned"] is True


def test_an_exclude_set_through_the_api_leaves_the_prompt(client):
    cid, sid = _scene(client)
    lid = _lore(client, cid)
    _post(cid, sid, "hello")

    def sections():
        return [s["label"] for s in
                client.get(f"/api/campaigns/{cid}/scenes/{sid}/context").json()["sections"]]

    assert "World info" in sections()
    _pin(client, cid, ref=f"lore:{lid}", mode="exclude", sid=sid)
    assert "World info" not in sections()


def test_a_campaign_rule_lists_under_every_scene_and_needs_no_scene_to_set(client):
    cid, sid = _scene(client)
    lid = _lore(client, cid)
    assert _pin(client, cid, ref=f"lore:{lid}", mode="exclude", scope="campaign").status_code == 200
    other = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "Later"}).json()["id"]
    for scene in (sid, other):
        rows = client.get(f"/api/campaigns/{cid}/pins", params={"sid": scene}).json()["pins"]
        assert [r["scope"] for r in rows] == ["campaign"]


def test_setting_the_same_target_again_replaces_the_rule(client):
    cid, sid = _scene(client)
    lid = _lore(client, cid)
    _pin(client, cid, ref=f"lore:{lid}", mode="pin", sid=sid)
    _pin(client, cid, ref=f"lore:{lid}", mode="exclude", sid=sid)
    rows = client.get(f"/api/campaigns/{cid}/pins", params={"sid": sid}).json()["pins"]
    assert [r["mode"] for r in rows] == ["exclude"]


def test_delete_lifts_one_rule_and_reports_a_stale_one(client):
    cid, sid = _scene(client)
    lid = _lore(client, cid)
    _pin(client, cid, ref=f"lore:{lid}", mode="pin", sid=sid)
    params = {"ref": f"lore:{lid}", "scope": "scene", "sid": sid}
    assert client.request("DELETE", f"/api/campaigns/{cid}/pins", params=params).status_code == 200
    assert client.request("DELETE", f"/api/campaigns/{cid}/pins", params=params).status_code == 404
    assert client.get(f"/api/campaigns/{cid}/pins", params={"sid": sid}).json()["pins"] == []


def test_a_rule_whose_target_was_deleted_says_so_rather_than_vanishing(client):
    cid, sid = _scene(client)
    lid = _lore(client, cid)
    _pin(client, cid, ref=f"lore:{lid}", mode="pin", sid=sid)
    assert client.delete(f"/api/campaigns/{cid}/lore/{lid}").status_code < 400

    rows = client.get(f"/api/campaigns/{cid}/pins", params={"sid": sid}).json()["pins"]
    assert rows[0]["missing"] is True and rows[0]["name"] == lid


def test_the_refusals_are_400s_not_500s(client):
    cid, sid = _scene(client)
    assert _pin(client, cid, ref="recipes:soup", mode="pin", sid=sid).status_code == 400
    assert _pin(client, cid, ref="lore:x", mode="pin", sid="").status_code == 400
    assert _pin(client, cid, ref="lore:x", mode="pin", scope="campaign",
                ttl_posts=4).status_code == 400
    # A closed value space is refused by the model itself, before the store.
    assert _pin(client, cid, ref="lore:x", mode="maybe", sid=sid).status_code == 422


def test_an_unknown_campaign_or_scene_is_a_404(client):
    cid, sid = _scene(client)
    assert client.get("/api/campaigns/nope/pins").status_code == 404
    assert client.get(f"/api/campaigns/{cid}/pins", params={"sid": "9999"}).status_code == 404
    assert _pin(client, cid, ref="lore:x", mode="pin", sid="9999").status_code == 404


def test_a_campaign_rule_is_not_refused_over_a_scene_it_does_not_use(client):
    """Our own client keeps the scene id in the same field for both scopes, so
    validating it for a rule that neither measures against a scene nor stores
    one would block a campaign-wide pin from a scene renamed underneath it."""
    cid, sid = _scene(client)
    lid = _lore(client, cid)
    r = _pin(client, cid, ref=f"lore:{lid}", mode="exclude", scope="campaign", sid="9999")
    assert r.status_code == 200
    assert r.json()["pin"]["sid"] == ""


def test_deleting_a_scene_takes_its_rules_with_it(client):
    cid, sid = _scene(client)
    lid = _lore(client, cid)
    _pin(client, cid, ref=f"lore:{lid}", mode="pin", sid=sid)
    _pin(client, cid, ref=f"lore:{lid}", mode="exclude", scope="campaign")
    assert client.delete(f"/api/campaigns/{cid}/scenes/{sid}").status_code < 400

    again = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "Reused"}).json()["id"]
    rows = client.get(f"/api/campaigns/{cid}/pins", params={"sid": again}).json()["pins"]
    assert [r["scope"] for r in rows] == ["campaign"]


def test_renaming_a_scene_keeps_its_rules(client):
    cid, sid = _scene(client)
    lid = _lore(client, cid)
    _pin(client, cid, ref=f"lore:{lid}", mode="pin", sid=sid)
    moved = client.put(f"/api/campaigns/{cid}/scenes/{sid}",
                       json={"title": "The drowned village"}).json()["id"]
    assert moved != sid
    rows = client.get(f"/api/campaigns/{cid}/pins", params={"sid": moved}).json()["pins"]
    assert [r["ref"] for r in rows] == [f"lore:{lid}"]


def test_a_pinned_pc_resolves_its_name_and_survives_deletion(client):
    """The one target kind `_record_name` does not cover, so it has its own path."""
    cid, sid = _scene(client)
    wid = client.get(f"/api/campaigns/{cid}").json()["meta"]["world"]
    client.post(f"/api/worlds/{wid}/pcs", json={"name": "Winifred"})
    _pin(client, cid, ref="pcs:winifred", mode="pin", sid=sid)
    rows = client.get(f"/api/campaigns/{cid}/pins", params={"sid": sid}).json()["pins"]
    assert rows[0]["name"] == "Winifred" and rows[0]["missing"] is False

    assert _pin(client, cid, ref="pcs:never-existed", mode="pin", sid=sid).status_code == 200
    rows = client.get(f"/api/campaigns/{cid}/pins", params={"sid": sid}).json()["pins"]
    assert [r["missing"] for r in rows if r["id"] == "never-existed"] == [True]
