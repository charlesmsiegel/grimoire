"""Swiping preserves the historical audience of each stable multipart post."""

import pytest

from grimoire import store
from grimoire.store.appearances import paths


@pytest.mark.parametrize("legacy_snapshots", [False, True])
@pytest.mark.parametrize("audience", ["present", "empty", "unknown"])
def test_multipart_swipe_restores_per_post_audience(monkeypatch, tmp_path, audience, legacy_snapshots):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    world = store.worlds.create_world("Realm")
    actor, version = store.characters.create_character(store.worlds.world_root(world), "Mara")
    cid = store.campaigns.create_campaign("Saltmarch", world)
    sid = store.scenes.create_scene(cid, "Opening")
    store.appearances.appear(cid, sid, "characters", actor, version, "npc", narrate=False)
    if audience != "present":
        store.appearances.leave(cid, sid, "characters", actor)
    if audience == "unknown":
        record = paths.record(cid)
        record[f"characters/{actor}"]["presence"].pop(sid)
        paths._write(cid, record)
    store.scenes.append_message(cid, sid, "assistant", "First.", speaker="Mara")
    first, = store.responses.migrate(cid, sid)
    store.scenes.append_message(cid, sid, "assistant", "A roll.", speaker="Roll")
    full = store.responses.save_variant(cid, sid, first["id"], "Later.", "complete", part="b")
    if audience == "present":
        store.appearances.leave(cid, sid, "characters", actor)
    if legacy_snapshots:
        # Pre-audience ledgers still have evidence for the currently visible
        # posts. Capture it before removing a multipart take for the first time.
        _legacy_audience_ledger(cid)
    before = store.scenes.read_scene(cid, sid)["messages"]
    expected = paths.observed_indices(cid, sid, f"characters:{actor}", before)
    if audience == "empty":
        store.appearances.appear(cid, sid, "characters", actor, version, "npc", narrate=False)
    store.responses.activate(cid, sid, first["id"], first["active_variant"])
    store.responses.activate(cid, sid, first["id"], full["id"])
    after = store.scenes.read_scene(cid, sid)["messages"]
    assert [(m["post_id"], m["content"]) for m in after] == [(m["post_id"], m["content"]) for m in before]
    assert paths.observed_indices(cid, sid, f"characters:{actor}", after) == expected
    if audience == "unknown":
        assert sid not in paths.record(cid)[f"characters/{actor}"]["presence"]


def _legacy_audience_ledger(cid):
    data = store.responses._read(cid)
    for scene in data["scenes"].values():
        for response in scene["responses"].values():
            for variant in response["variants"]:
                variant.pop("audience", None)
                for post in variant.get("posts", []):
                    post.pop("audience", None)
    store.responses._write(cid, data)
