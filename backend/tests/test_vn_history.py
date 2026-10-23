"""Public history is observed at publication, never inferred from the future."""

import pytest

from grimoire import store
from grimoire.store import atomic, playstate
from grimoire.store.scene_history import projection, records
from grimoire.store.scenes import paths
from grimoire.store.tracker import fields, provenance, walk
from grimoire.store.tracker import records as tracker_records


@pytest.fixture
def scene(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = store.worlds.create_world("Realm")
    root = store.worlds.world_root(wid)
    actor, version = store.characters.create_character(root, "Mara")
    cid = store.campaigns.create_campaign("Saltmarch", wid)
    sid = store.scenes.create_scene(cid, "Opening")
    store.appearances.appear(cid, sid, "characters", actor, version, "npc", narrate=False)
    return cid, sid, actor, version


def append(cid, sid, text="Hello."):
    at = store.scenes.append_message(cid, sid, "user", text)
    return store.scenes.read_scene(cid, sid)["messages"][at]["post_id"]


def test_earlier_post_never_uses_later_cast_or_location(scene):
    cid, sid, actor, version = scene
    root = store.campaigns.campaign_root(cid)
    a = store.entities.create_entity(root, "locations", "Harbour", "")
    b = store.entities.create_entity(root, "locations", "Market", "")
    store.scenes.set_location(cid, sid, a)
    first = append(cid, sid)
    store.scenes.set_location(cid, sid, b)
    transition = store.scenes.read_scene(cid, sid)["messages"][-1]["post_id"]
    assert projection.at_post(cid, sid, transition).location_id == b
    store.appearances.leave(cid, sid, "characters", actor)
    last = append(cid, sid, "Later.")
    earlier = projection.at_post(cid, sid, first)
    assert earlier.location_id == a
    assert [(a.ref, a.version_id, a.visible_mood) for a in earlier.actors] == [
        ("characters:" + actor, version, None)]
    assert projection.at_post(cid, sid, last).actors == ()


def test_failed_checkpoint_does_not_carry_actor_forward(scene, monkeypatch):
    cid, sid, actor, _ = scene
    first = append(cid, sid)
    assert projection.at_post(cid, sid, first).actors

    def fail(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(records, "save_checkpoint", fail)
    store.appearances.leave(cid, sid, "characters", actor)
    last = append(cid, sid)
    assert projection.at_post(cid, sid, last).actors == ()


@pytest.mark.parametrize("awareness,expected", [("present", "joy"), ("self", None)])
def test_public_mood_only_at_or_before_cursor(scene, awareness, expected):
    cid, sid, actor, _ = scene
    first = append(cid, sid)
    second = append(cid, sid)
    key = walk.ordered_keys(cid, sid)[1][1]
    snapshot = {"characters:" + actor: {"present": True, "fields": {
        "visible_mood": {"value": "joy", "aware": awareness},
        "true_mood": {"value": "anger", "aware": "self"}}}}
    tracker_records.save(cid, store.scenes.scene_identity(cid, sid), key, snapshot,
                         changed=[], fields_digest=fields.digest(fields.effective(cid, sid)),
                         model="test", provenance=provenance.capture(cid, sid, key))
    assert projection.at_post(cid, sid, first).actors[0].visible_mood is None
    assert projection.at_post(cid, sid, second).actors[0].visible_mood == expected


def test_legacy_import_has_no_retroactive_cast(scene):
    cid, sid, _, _ = scene
    store.scenes.append_messages(cid, sid, [{"role": "user", "content": "Old."}])
    post = store.scenes.read_scene(cid, sid)["messages"][0]
    assert projection.at_post(cid, sid, post["post_id"]).actors == ()


def test_reroll_before_later_transition_preserves_historical_location(scene):
    cid, sid, _, _ = scene
    root = store.campaigns.campaign_root(cid)
    a = store.entities.create_entity(root, "locations", "Harbour", "")
    b = store.entities.create_entity(root, "locations", "Market", "")
    store.scenes.set_location(cid, sid, a)
    store.scenes.append_message(cid, sid, "assistant", "First.", speaker="Mara")
    record, = store.responses.migrate(cid, sid)
    store.scenes.set_location(cid, sid, b)
    store.responses.save_variant(cid, sid, record["id"], "Replacement.", "complete")
    post = store.scenes.read_scene(cid, sid)["messages"][0]
    assert projection.at_post(cid, sid, post["post_id"]).location_id == a


def test_failed_canonical_move_keeps_transcript_and_location(scene, monkeypatch):
    cid, sid, _, _ = scene
    root = store.campaigns.campaign_root(cid)
    a = store.entities.create_entity(root, "locations", "Harbour", "")
    b = store.entities.create_entity(root, "locations", "Market", "")
    store.scenes.set_location(cid, sid, a)
    first = append(cid, sid)
    path = paths._scene_path(cid, sid)
    before = path.read_bytes()
    original = atomic.write_text

    def fail_scene(target, *args, **kwargs):
        if target == path:
            raise OSError("disk full")
        return original(target, *args, **kwargs)

    monkeypatch.setattr(atomic, "write_text", fail_scene)
    with pytest.raises(OSError):
        store.scenes.set_location(cid, sid, b)
    assert path.read_bytes() == before
    assert store.scenes.get_location_history(cid, sid) == [a]
    assert projection.at_post(cid, sid, first).location_id == a


def test_multipart_swipe_preserves_transition_order_and_each_location(scene):
    cid, sid, _, _ = scene
    root = store.campaigns.campaign_root(cid)
    a = store.entities.create_entity(root, "locations", "Harbour", "")
    b = store.entities.create_entity(root, "locations", "Market", "")
    store.scenes.set_location(cid, sid, a)
    store.scenes.append_message(cid, sid, "assistant", "First.", speaker="Mara")
    record, = store.responses.migrate(cid, sid)
    store.scenes.set_location(cid, sid, b)
    full = store.responses.save_variant(cid, sid, record["id"], "Later.", "complete", part="b")
    before = store.scenes.read_scene(cid, sid)["messages"]
    store.responses.activate(cid, sid, record["id"], record["active_variant"])
    store.responses.activate(cid, sid, record["id"], full["id"])
    after = store.scenes.read_scene(cid, sid)["messages"]
    assert [m["post_id"] for m in after] == [m["post_id"] for m in before]
    assert [projection.at_post(cid, sid, m["post_id"]).location_id for m in after] == [a, b, b]


def test_tracker_inputs_changed_since_post_cannot_be_certified(scene):
    cid, sid, actor, _ = scene
    append(cid, sid)
    key = walk.ordered_keys(cid, sid)[0][1]
    proof = provenance.capture(cid, sid, key)
    assert proof is not None
    playstate.write_state(store.campaigns.campaign_root(cid), actor, "## Current state\nA later disguise.")
    assert not provenance.valid(cid, sid, key, proof)
    assert provenance.capture(cid, sid, key) is None


def test_old_version_fields_do_not_carry_into_new_version(scene):
    cid, sid, actor, _ = scene
    first = append(cid, sid)
    key = walk.ordered_keys(cid, sid)[0][1]
    tracker_records.save(cid, store.scenes.scene_identity(cid, sid), key,
        {"characters:" + actor: {"present": True, "fields": {
            "visible_mood": {"value": "joy", "aware": "present"}}}},
        changed=[], fields_digest=fields.digest(fields.effective(cid, sid)), model="test",
        provenance=provenance.capture(cid, sid, key))
    root = store.campaigns.world_root_of(cid)
    version = store.characters.create_version(root, actor, "Veteran", store.characters.blank_card("Mara"))
    store.appearances.import_version(cid, "characters", actor, version)
    second = append(cid, sid)
    assert projection.at_post(cid, sid, first).actors[0].visible_mood == "joy"
    new = projection.at_post(cid, sid, second).actors[0]
    assert new.version_id == version
    assert new.visible_mood is None
