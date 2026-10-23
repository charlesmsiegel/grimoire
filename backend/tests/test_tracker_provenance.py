"""Historical evidence survives index loss without resurrecting stale state."""

import pytest

from grimoire import store
from grimoire.store.tracker import fields, paths, provenance, records, walk


@pytest.fixture
def history(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = store.campaigns.create_campaign("Saltmarch", store.worlds.create_world("Realm"))
    sid = store.scenes.create_scene(cid, "Opening")
    for content in ("First.", "Second.", "Third."):
        store.scenes.append_message(cid, sid, "user", content)
    return cid, sid, [key for _, key in walk.ordered_keys(cid, sid)]


def save(cid, sid, key, snapshot=None, certificate=None):
    records.save(cid, store.scenes.scene_identity(cid, sid), key, snapshot or {},
                 changed=[], fields_digest=fields.digest(fields.effective(cid, sid)), model="test",
                 provenance=certificate if certificate is not None else provenance.capture(cid, sid, key))


def test_missing_index_cannot_certify_stale_snapshot(history):
    cid, sid, (a, b, _) = history
    save(cid, sid, a)
    save(cid, sid, b)
    proof = records.read_snapshot(cid, store.scenes.scene_identity(cid, sid), b)["provenance"]
    assert provenance.valid(cid, sid, b, proof)
    store.scenes.edit_message(cid, sid, 0, "Changed.")
    (paths.scene_dir(cid, store.scenes.scene_identity(cid, sid)) / "index.json").unlink()
    assert not provenance.valid(cid, sid, b, proof)


def test_late_predecessor_completion_invalidates_descendant_without_index(history):
    cid, sid, (a, b, c) = history
    save(cid, sid, a)
    proof = provenance.capture(cid, sid, c)
    save(cid, sid, c, certificate=proof)
    assert provenance.valid(cid, sid, c, proof)
    save(cid, sid, b)
    (paths.scene_dir(cid, store.scenes.scene_identity(cid, sid)) / "index.json").unlink()
    assert not provenance.valid(cid, sid, c, proof)


def test_prior_manual_tracker_edit_invalidates_descendant_proof(history):
    cid, sid, (a, b, _) = history
    save(cid, sid, a)
    proof = provenance.capture(cid, sid, b)
    save(cid, sid, a, {"characters:mara": {"present": True, "fields": {}}})
    assert not provenance.valid(cid, sid, b, proof)


def test_result_cannot_certify_inputs_changed_during_run(history):
    cid, sid, (a, _, _) = history
    proof = provenance.capture(cid, sid, a)
    assert provenance.valid(cid, sid, a, proof)
    store.scenes.edit_message(cid, sid, 0, "Different input.")
    assert not provenance.valid(cid, sid, a, proof)


def test_future_append_preserves_proof_and_legacy_base_cannot_be_certified(history):
    cid, sid, (a, b, _) = history
    proof = provenance.capture(cid, sid, a)
    store.scenes.append_message(cid, sid, "user", "Future.")
    assert provenance.valid(cid, sid, a, proof)
    records.save(cid, store.scenes.scene_identity(cid, sid), a, {}, changed=[],
                 fields_digest="old", model="legacy")
    assert provenance.capture(cid, sid, b) is None
