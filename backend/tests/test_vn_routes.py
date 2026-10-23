"""VN playback consumes public post history, never today's cast or private fields."""

import json

import pytest

from grimoire import store
from grimoire.store import assets, atomic, branch
from grimoire.store.expression_library import contracts, paths, read, write
from grimoire.store.scene_history import records
from grimoire.store.scenes import identity
from grimoire.store.tracker import fields, provenance, walk
from grimoire.store.tracker import records as tracker_records
from tests.test_expressions_store import png


@pytest.fixture
def vn(client):
    wid = store.worlds.create_world("Realm")
    root = store.worlds.world_root(wid)
    aid, vid = store.characters.create_character(root, "Mara")
    cid = store.campaigns.create_campaign("Saltmarch", wid)
    sid = store.scenes.create_scene(cid, "Opening")
    store.appearances.appear(cid, sid, "characters", aid, vid, "npc", narrate=False)
    target = contracts.ExpressionTarget("world", wid, "characters", aid, vid)
    write.apply_effective(target, [
        {"op": "create-outfit", "outfit": "armor", "metadata": {
            "clothing_aliases": ["coat"], "presentation": {"scale": 1.5, "offset_x": 0.1}}},
        {"op": "put-expression", "outfit": "armor", "expression": "neutral", "data": png()},
        {"op": "put-expression", "outfit": "armor", "expression": "joy", "data": png()},
    ], expected_effective_revision=read.effective(target)["effective_revision"])
    store.scenes.append_message(cid, sid, "assistant", "Hello.", speaker="Mara")
    post = store.scenes.read_scene(cid, sid)["messages"][0]["post_id"]
    return client, cid, sid, aid, vid, post


def url(vn):
    _, cid, sid, _, _, post = vn
    return f"/api/campaigns/{cid}/scenes/{sid}/vn/posts/{post}"


def save(vn, stage, **override):
    return vn[0].put(url(vn) + "/presentation", json={
        "scene_identity": stage["scene_identity"], "expected_revision": stage["revision"],
        "actors": {}, "background": None, **override})


def test_vn_index_and_historical_stage(vn):
    client, cid, sid, aid, _, post = vn
    index = client.get(f"/api/campaigns/{cid}/scenes/{sid}/vn")
    assert index.status_code == 200
    assert index.json()["title"] == "Opening"
    assert index.json()["posts"][0] == {"post_id": post, "role": "assistant", "speaker": "Mara",
                                       "content": "Hello.", "excluded": False}
    store.appearances.leave(cid, sid, "characters", aid)
    stage = client.get(url(vn)).json()
    assert [a["ref"] for a in stage["actors"]] == ["characters:" + aid]
    assert stage["actors"][0]["candidates"][0]["presentation"]["scale"] == 1.5
    assert stage["actors"][0]["candidates"][0]["presentation"]["offset_x"] == 0.1


def test_overrides_save_reset_and_reject_stale_revision(vn):
    stage = vn[0].get(url(vn)).json()
    ref = "characters:" + vn[3]
    changed = save(vn, stage, actors={ref: {"outfit": "armor", "expression": "joy", "hidden": True}})
    assert changed.status_code == 200
    assert changed.json()["actors"][0]["hidden"] is True
    assert vn[0].get(url(vn)).json()["override"] == changed.json()["override"]
    assert save(vn, stage).status_code == 409
    reset = save(vn, changed.json())
    assert reset.status_code == 200
    assert reset.json()["override"] == {"actors": {}, "background": None}
    assert reset.json()["revision"] != changed.json()["revision"]
    assert save(vn, changed.json()).status_code == 409


@pytest.mark.parametrize("override", [
    {"actors": {"characters:stranger": {"hidden": False}}},
    {"actors": {"characters:mara": {"outfit": "missing"}}},
    {"actors": {"characters:mara": {"expression": "../secret"}}},
    {"background": "https://example.com/secret.png"},
])
def test_unknown_or_unsafe_overrides_do_not_publish(vn, override):
    stage = vn[0].get(url(vn)).json()
    assert save(vn, stage, **override).status_code == 400
    assert vn[0].get(url(vn)).json()["revision"] == stage["revision"]


def test_missing_history_does_not_backfill_current_cast(vn):
    _, cid, sid, _, _, post = vn
    ident = identity.scene_identity(cid, sid)
    message = store.scenes.read_scene(cid, sid)["messages"][0]
    checkpoint = records.directory(cid, ident) / (message["scene_state_token"] + ".json")
    checkpoint.unlink()
    stage = vn[0].get(url(vn)).json()
    assert stage["actors"] == []
    assert stage["backgrounds"] == []
    assert stage["notices"]
    assert stage["post_id"] == post


def test_public_mood_and_clothing_resolve_without_exposing_private_tracker_fields(vn):
    client, cid, sid, aid, _, _ = vn
    client.get(f"/api/campaigns/{cid}/scenes/{sid}/vn")
    key = walk.ordered_keys(cid, sid)[0][1]
    tracker_records.save(cid, identity.scene_identity(cid, sid), key,
        {"characters:" + aid: {"present": True, "fields": {
            "visible_mood": {"value": "joy", "aware": "present"},
            "clothing": {"value": "coat", "aware": "present"},
            "true_mood": {"value": "private-secret", "aware": "self"}}}},
        changed=[], fields_digest=fields.digest(fields.effective(cid, sid)), model="test",
        provenance=provenance.capture(cid, sid, key))
    result = client.get(url(vn))
    assert result.status_code == 200
    assert "/armor/joy/image" in result.json()["actors"][0]["candidates"][0]["url"]
    assert "private-secret" not in result.text
    assert "true_mood" not in result.text


def test_background_is_limited_to_historical_location_and_none(vn):
    client, cid, sid, _, _, _ = vn
    root = store.campaigns.campaign_root(cid)
    lid = store.entities.create_entity(root, "locations", "Harbour", "")
    assets.put_image(root, lid, "default", "avatar", png(), "png", "locations")
    store.scenes.set_location(cid, sid, lid)
    store.scenes.append_message(cid, sid, "user", "Here.")
    vn = (*vn[:5], store.scenes.read_scene(cid, sid)["messages"][-1]["post_id"])
    stage = client.get(url(vn)).json()
    assert stage["location_name"] == "Harbour"
    assert [row["name"] for row in stage["backgrounds"]] == ["avatar"]
    assert stage["background_url"]
    assert save(vn, stage, background="none").json()["background_url"] is None


def test_scene_incarnation_post_and_branch_isolation(vn):
    client, cid, sid, _, _, _ = vn
    stage = client.get(url(vn)).json()
    assert save(vn, stage, background="none").status_code == 200
    sibling = branch.branch_scene(cid, sid, 0)
    result = client.get(url(vn).replace(f"/{sid}/vn", f"/{sibling}/vn"))
    assert result.status_code == 200
    assert result.json()["override"]["background"] is None
    store.scenes.delete_scene(cid, sibling)
    store.scenes.delete_scene(cid, sid)
    assert store.scenes.create_scene(cid, "Opening") == sid
    store.scenes.append_message(cid, sid, "user", "New.")
    assert client.get(url(vn)).status_code == 404
    new_post = store.scenes.read_scene(cid, sid)["messages"][0]["post_id"]
    assert client.put(url(vn).replace(vn[5], new_post) + "/presentation", json={
        "scene_identity": stage["scene_identity"], "expected_revision": stage["revision"],
        "actors": {}, "background": None}).status_code == 409


def test_dangling_sprite_override_falls_back_without_losing_text(vn):
    stage = vn[0].get(url(vn)).json()
    ref = "characters:" + vn[3]
    assert save(vn, stage, actors={ref: {"expression": "joy"}}).status_code == 200
    directory = paths.directory(contracts.ExpressionTarget("campaign", vn[1], "characters", vn[3], vn[4]))
    assert not directory.exists()
    target = contracts.ExpressionTarget("world", store.campaigns.read_campaign(vn[1])["meta"]["world"], "characters", vn[3], vn[4])
    write.apply_effective(target, [{"op": "remove-expression", "outfit": "armor", "expression": "joy"}],
                          expected_effective_revision=read.effective(target)["effective_revision"])
    result = vn[0].get(url(vn)).json()
    assert "/neutral/image" in result["actors"][0]["candidates"][0]["url"]
    assert result["notices"]


def test_corrupt_presentation_is_not_overwritten_as_empty(vn):
    stage = vn[0].get(url(vn)).json()
    path = records.directory(vn[1], stage["scene_identity"]) / "presentation.json"
    atomic.write_text(path, json.dumps({"format": 99}))
    assert vn[0].get(url(vn)).status_code == 503
    assert save(vn, stage).status_code == 503
    assert json.loads(path.read_text())["format"] == 99


def test_automatic_expression_choices_stay_in_resolved_outfit(vn):
    client, cid, _sid, aid, vid, _ = vn
    target = contracts.ExpressionTarget("world", store.campaigns.read_campaign(cid)["meta"]["world"], "characters", aid, vid)
    write.apply_effective(target, [
        {"op": "create-outfit", "outfit": "everyday"},
        {"op": "put-expression", "outfit": "everyday", "expression": "neutral", "data": png()},
        {"op": "set-default", "outfit": "everyday"},
    ], expected_effective_revision=read.effective(target)["effective_revision"])
    stage = client.get(url(vn)).json()
    ref = "characters:" + aid
    assert save(vn, stage, actors={ref: {"expression": "joy"}}).status_code == 400
    assert stage["actors"][0]["automatic_outfit"] == "everyday"
    selected = save(vn, stage, actors={ref: {"outfit": "armor", "expression": "joy"}})
    assert selected.status_code == 200
    assert selected.json()["actors"][0]["automatic_outfit"] == "everyday"
    assert "/armor/joy/image" in selected.json()["actors"][0]["candidates"][0]["url"]
