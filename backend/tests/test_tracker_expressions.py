"""Presentation decisions do not change narrative state or its historical proof."""

import copy
import hashlib
import json

import pytest

from grimoire import store
from grimoire.store.tracker import fields, merge, prompt, provenance, records, view, walk

PAIR = {
    "expression-outfit": {"value": "armor", "aware": "present"},
    "expression-mood": {"value": "determined", "aware": "present"},
}


@pytest.fixture
def history(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = store.worlds.create_world("Realm")
    aid, vid = store.characters.create_character(store.worlds.world_root(wid), "Mara")
    cid = store.campaigns.create_campaign("Saltmarch", wid)
    sid = store.scenes.create_scene(cid, "Opening")
    store.appearances.appear(cid, sid, "characters", aid, vid, "npc", narrate=False)
    for text in ("First.", "Second.", "Third."):
        store.scenes.append_message(cid, sid, "user", text)
    ident = store.scenes.scene_identity(cid, sid)
    keys = [key for _, key in walk.ordered_keys(cid, sid)]
    snapshot = {f"characters:{aid}": {"present": True, "fields": {
        "pose": {"value": "standing", "aware": "present", "set_by": "user"}}}}
    for key in keys:
        records.save(cid, ident, key, snapshot, changed=[],
                     fields_digest=fields.digest(fields.effective(cid, sid)), model="original",
                     provenance=provenance.capture(cid, sid, key))
    return cid, sid, ident, keys, f"characters:{aid}", vid


def test_ordinary_tracker_cannot_write_or_disclose_expression_identifiers():
    roster = {"characters:mara": "Mara"}
    prior = {"characters:mara": {"present": True, "fields": copy.deepcopy(PAIR)}}
    messages = prompt.build_messages(list(fields.DEFAULT_FIELDS), prior, roster, set(roster), [], [],
                                     {"speaker": "Mara", "content": "She stands."})
    assert "expression-outfit" not in str(messages)
    assert "expression-mood" not in str(messages)
    assert "determined" not in str(messages)
    snapshot, changes = merge.apply_reply({}, {"changes": {"Mara": {
        "expression-outfit": "armor", "expression-mood": "determined", "pose": "standing"}}},
        list(fields.DEFAULT_FIELDS), roster, set(roster), store.scenes.match_name)
    assert snapshot["characters:mara"]["fields"] == {"pose": {"value": "standing", "aware": "present"}}
    assert changes == [["characters:mara", "pose", "standing"]]
    assert view.lines_for(prior, list(fields.DEFAULT_FIELDS), None, roster)[0]["values"] == []


def test_reserved_fields_can_be_disabled_but_not_redefined_or_hand_edited(history):
    cid, sid, _, _, ref, _ = history
    definitions = fields.effective(cid, sid)
    fields.write_scene_layer(cid, sid, {"off": ["expression-outfit"]})
    assert next(f for f in fields.effective(cid, sid) if f["key"] == "expression-outfit")["off"]
    with pytest.raises(fields.FieldLayerError):
        fields.write_campaign_layer(cid, {"change": {"expression-mood": {"type": "list"}}})
    with pytest.raises(fields.FieldLayerError):
        fields.write_campaign_layer(cid, {"fields": [
            {"key": "other-hyphen", "label": "Other", "type": "text", "aware": "present"}]})
    with pytest.raises(ValueError, match="computed"):
        merge.apply_edit({ref: {"present": True, "fields": {}}},
                         {ref: {"expression-mood": {"value": "joy"}}}, definitions)


def test_expression_patch_preserves_narrative_envelope_and_later_certificates(history):
    cid, sid, ident, keys, ref, vid = history
    before = records.read_snapshot(cid, ident, keys[0])
    entry = records.read_index(cid, ident)[keys[0]]
    updated = copy.deepcopy(before["snapshot"])
    updated[ref]["fields"].update(PAIR)
    metadata = {"scene_identity": ident, "key": keys[0],
                "post_id": store.scenes.read_scene(cid, sid)["messages"][0]["post_id"],
                "versions": {ref: vid}}
    assert records.save_expressions(cid, ident, keys[0], updated, metadata, before, entry)
    after = records.read_snapshot(cid, ident, keys[0])
    assert {k: v for k, v in after.items() if k not in {"snapshot", "expressions"}} == {
        k: v for k, v in before.items() if k != "snapshot"}
    assert after["snapshot"][ref]["fields"]["pose"] == before["snapshot"][ref]["fields"]["pose"]
    assert records.read_index(cid, ident)[keys[0]] == entry
    assert set(provenance.certificates(cid, sid)[1]) == set(keys)
    # A real narrative edit still invalidates downstream history.
    after["snapshot"][ref]["fields"]["pose"]["value"] = "kneeling"
    records.save(cid, ident, keys[0], after["snapshot"], changed=[],
                 fields_digest=after["fields_digest"], model="edited", provenance=after["provenance"])
    assert keys[1] not in provenance.certificates(cid, sid)[1]


def test_expression_patch_refuses_newer_tracker_generation_without_snapshot_change(history):
    cid, _, ident, keys, ref, _ = history
    before = records.read_snapshot(cid, ident, keys[0])
    entry = records.read_index(cid, ident)[keys[0]]
    updated = copy.deepcopy(before["snapshot"])
    updated[ref]["fields"].update(PAIR)
    records.mark_pending(cid, ident, keys[0])
    assert not records.save_expressions(cid, ident, keys[0], updated, {}, before, entry)
    assert records.read_snapshot(cid, ident, keys[0]) == before


def test_pre_feature_serialized_certificates_remain_proven(history):
    cid, sid, ident, keys, _, _ = history
    # Construct the format shipped before expression fields existed: its field
    # digest includes every definition, and its predecessor hashes the entire
    # persisted JSON envelope. This input is independent of the new filters.
    legacy_definitions = [f for f in fields.DEFAULT_FIELDS
                          if f["key"] not in {"expression-outfit", "expression-mood"}]
    legacy_digest = hashlib.sha256(json.dumps(legacy_definitions, sort_keys=True).encode()).hexdigest()[:16]
    predecessor = None
    for key in keys:
        body = records.read_snapshot(cid, ident, key)
        certificate = {**body["provenance"], "fields": legacy_digest, "predecessor": predecessor}
        records.save(cid, ident, key, body["snapshot"], changed=[], fields_digest=legacy_digest,
                     model=body["model"], provenance=certificate)
        persisted = records.read_snapshot(cid, ident, key)
        fingerprint = hashlib.sha256(json.dumps(persisted, sort_keys=True, ensure_ascii=True,
                                                separators=(",", ":")).encode()).hexdigest()
        predecessor = {"key": key, "fingerprint": fingerprint}
    assert set(provenance.certificates(cid, sid)[1]) == set(keys)
