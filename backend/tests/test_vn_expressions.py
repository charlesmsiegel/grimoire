"""Historical sprite decisions precede the unchanged narrative fallback."""

import copy

import pytest

from grimoire import store
from grimoire.store.expression_library import contracts, read, write
from grimoire.store.tracker import fields, provenance, records, walk
from tests.test_expressions_store import png
from tests.test_vn_routes import vn as vn


@pytest.fixture
def chosen(vn):
    client, cid, sid, aid, vid, post = vn
    client.get(f"/api/campaigns/{cid}/scenes/{sid}/vn")
    wid = store.campaigns.read_campaign(cid)["meta"]["world"]
    target = contracts.ExpressionTarget("world", wid, "characters", aid, vid)
    write.apply_effective(target, [
        {"op": "create-outfit", "outfit": "casual", "metadata": {}},
        {"op": "put-expression", "outfit": "casual", "expression": "determined", "data": png()},
        {"op": "put-expression", "outfit": "casual", "expression": "neutral", "data": png()},
    ], expected_effective_revision=read.effective(target)["effective_revision"])
    ident = store.scenes.scene_identity(cid, sid)
    key = walk.ordered_keys(cid, sid)[0][1]
    ref = "characters:" + aid
    snap = {ref: {"present": True, "fields": {
        "clothing": {"value": "coat", "aware": "present"},
        "visible_mood": {"value": "joy", "aware": "present"},
        "expression-outfit": {"value": "casual", "aware": "present"},
        "expression-mood": {"value": "determined", "aware": "present"}}}}
    records.save(cid, ident, key, snap, changed=[], fields_digest=fields.digest(fields.effective(cid,sid)),
                 model="test", provenance=provenance.capture(cid,sid,key), expressions={
                     "scene_identity":ident,"key":key,"post_id":post,"versions":{ref:vid}})
    return vn, target, key


def stage(vn):
    client, cid, sid, _, _, post = vn
    return client.get(f"/api/campaigns/{cid}/scenes/{sid}/vn/posts/{post}").json()


def test_exact_decision_sprite_then_original_automatic_chain(chosen):
    vn, _, _ = chosen
    actor = stage(vn)["actors"][0]
    urls = [c["url"] for c in actor["candidates"]]
    assert "/casual/determined/image" in urls[0]
    assert "/armor/joy/image" in urls[1]
    assert actor["automatic_outfit"] == "casual"
    assert len(urls) == len(set(urls))


def test_manual_outfit_does_not_reuse_another_outfits_computed_mood(chosen):
    vn, _, _ = chosen
    client,cid,sid,aid,_,post = vn
    before=stage(vn)
    response=client.put(f"/api/campaigns/{cid}/scenes/{sid}/vn/posts/{post}/presentation",json={
        "scene_identity":before["scene_identity"],"expected_revision":before["revision"],
        "actors":{"characters:"+aid:{"outfit":"armor"}},"background":None})
    assert response.status_code == 200
    assert "/armor/joy/image" in response.json()["actors"][0]["candidates"][0]["url"]


def test_deleted_decision_image_restores_current_fallback(chosen):
    vn,target,_ = chosen
    write.apply_effective(target,[{"op":"remove-expression","outfit":"casual","expression":"determined"}],
                          expected_effective_revision=read.effective(target)["effective_revision"])
    assert "/armor/joy/image" in stage(vn)["actors"][0]["candidates"][0]["url"]


def test_untracked_later_post_never_inherits_computed_pair(chosen):
    vn,_,_ = chosen
    client,cid,sid,aid,vid,_ = vn
    at=store.scenes.append_message(cid,sid,"user","Later.")
    post=store.scenes.read_scene(cid,sid)["messages"][at]["post_id"]
    later=(client,cid,sid,aid,vid,post)
    assert "/armor/joy/image" in stage(later)["actors"][0]["candidates"][0]["url"]


def test_wrong_source_actor_version_and_disabled_fields_reject_pair(chosen):
    vn,_,key = chosen
    _,cid,sid,aid,_,_ = vn
    ident=store.scenes.scene_identity(cid,sid)
    original=records.read_snapshot(cid,ident,key)
    meta=copy.deepcopy(original["expressions"])
    meta["versions"]["characters:"+aid]="other"
    assert records.save_expressions(cid,ident,key,original["snapshot"],meta,original,
                                    records.read_index(cid,ident)[key])
    assert "/armor/joy/image" in stage(vn)["actors"][0]["candidates"][0]["url"]
    now=records.read_snapshot(cid,ident,key)
    assert records.save_expressions(cid,ident,key,now["snapshot"],original["expressions"],now,
                                    records.read_index(cid,ident)[key])
    fields.write_scene_layer(cid,sid,{"off":["expression-mood"]})
    assert "/armor/joy/image" in stage(vn)["actors"][0]["candidates"][0]["url"]


@pytest.mark.parametrize("field", ["scene_identity", "key", "post_id"])
def test_wrong_record_source_never_selects_the_pair(chosen, field):
    vn, _, key = chosen
    _, cid, sid, _, _, _ = vn
    ident = store.scenes.scene_identity(cid, sid)
    before = records.read_snapshot(cid, ident, key)
    metadata = {**before["expressions"], field: "unrelated"}
    assert records.save_expressions(cid, ident, key, before["snapshot"], metadata, before,
                                    records.read_index(cid, ident)[key])
    assert "/armor/joy/image" in stage(vn)["actors"][0]["candidates"][0]["url"]


def test_multipart_response_uses_its_shared_record_pair_for_each_part(chosen):
    vn, _, key = chosen
    client, cid, sid, aid, vid, _ = vn
    ident = store.scenes.scene_identity(cid, sid)
    old = records.read_snapshot(cid, ident, key)
    record = store.responses.get(cid, sid, store.scenes.read_scene(cid, sid)["messages"][0]["response_id"])
    store.responses.save_variant(cid, sid, record["id"], "Later.", "complete", part="b")
    posts = [m for m in store.scenes.read_scene(cid, sid)["messages"] if m.get("response_id") == record["id"]]
    shared = walk.ordered_keys(cid, sid)[0][1]
    records.save(cid, ident, shared, old["snapshot"], changed=[],
                 fields_digest=fields.digest(fields.effective(cid, sid)), model="test",
                 provenance=provenance.capture(cid, sid, shared), expressions={
                     "scene_identity": ident, "key": shared, "post_id": posts[-1]["post_id"],
                     "versions": {"characters:" + aid: vid}})
    assert len(posts) == 2
    for post in posts:
        current = (client, cid, sid, aid, vid, post["post_id"])
        assert "/casual/determined/image" in stage(current)["actors"][0]["candidates"][0]["url"]
