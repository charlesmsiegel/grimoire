"""Post addresses belong to content identities, never mutable positions."""

import re

import pytest

from grimoire import store
from grimoire.store import atomic
from grimoire.store.scenes import paths, serialize


@pytest.fixture
def scene(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = store.worlds.create_world("Realm")
    cid = store.campaigns.create_campaign("Saltmarch", wid)
    return cid, store.scenes.create_scene(cid, "Opening")


def test_equal_posts_keep_distinct_ids_after_edit_and_delete(scene):
    cid, sid = scene
    for _ in range(2):
        store.scenes.append_message(cid, sid, "assistant", "Same.", speaker="Mara")
    before = store.scenes.read_scene(cid, sid)["messages"]
    assert all(re.fullmatch(r"[0-9a-f]{32}", m.get("post_id", "")) for m in before)
    first, second = [m["post_id"] for m in before]
    assert first != second
    records = store.responses.migrate(cid, sid)
    store.scenes.edit_message(cid, sid, 0, "Changed.")
    assert store.scenes.read_scene(cid, sid)["messages"][0]["post_id"] == first
    store.responses.delete(cid, sid, records[0]["id"])
    assert store.scenes.read_scene(cid, sid)["messages"][0]["post_id"] == second


def test_swipe_restores_ids_without_reusing_other_variant_ids(scene):
    cid, sid = scene
    store.scenes.append_message(cid, sid, "assistant", "First.", speaker="Mara")
    record, = store.responses.migrate(cid, sid)
    original = store.scenes.read_scene(cid, sid)["messages"][0]
    original_variant = record["active_variant"]
    store.responses.save_variant(cid, sid, record["id"], "Second.", "complete")
    second = store.scenes.read_scene(cid, sid)["messages"][0]
    assert original.get("post_id") and second.get("post_id")
    assert original["post_id"] != second["post_id"]
    store.responses.activate(cid, sid, record["id"], original_variant)
    restored = store.scenes.read_scene(cid, sid)["messages"][0]
    assert restored["content"] == "First."
    assert restored["post_id"] == original["post_id"]


def test_synthetic_and_batch_posts_have_distinct_durable_addresses(scene):
    cid, sid = scene
    store.scenes.append_messages(cid, sid, [
        {"role": "user", "content": "Hello."},
        {"role": "assistant", "speaker": serialize.TRANSITION_SPEAKER, "content": "*Moved.*"},
        {"role": "assistant", "speaker": serialize.ROLL_SPEAKER, "content": "4"},
    ])
    posts = store.scenes.read_scene(cid, sid)["messages"]
    assert len({m.get("post_id") for m in posts}) == 3
    assert all(m.get("post_id") for m in posts)


def test_legacy_identity_migration_preserves_content_and_is_idempotent(scene):
    cid, sid = scene
    path = paths._scene_path(cid, sid)
    with path.open("a", encoding="utf-8") as stream:
        stream.write("**You:** Same.\n\n**Grimoire:** Same.\n")
    before = store.scenes.read_scene(cid, sid)["messages"]
    assert len(before) == 2
    store.responses.migrate_if_needed(cid, sid)
    after = store.scenes.read_scene(cid, sid)["messages"]
    assert all(m.get("post_id") for m in after)
    assert [(m["role"], m.get("speaker"), m["content"]) for m in after] == [
        (m["role"], m.get("speaker"), m["content"]) for m in before]
    raw = path.read_bytes()
    store.responses.migrate_if_needed(cid, sid)
    assert path.read_bytes() == raw


def test_failed_identity_migration_preserves_original_transcript(scene, monkeypatch):
    cid, sid = scene
    path = paths._scene_path(cid, sid)
    with path.open("a", encoding="utf-8") as stream:
        stream.write("**You:** Legacy.\n")
    before = path.read_bytes()
    original = atomic.write_text

    def fail_scene(target, content, *args, **kwargs):
        if target == path:
            raise OSError("disk full")
        return original(target, content, *args, **kwargs)

    monkeypatch.setattr(atomic, "write_text", fail_scene)
    with pytest.raises(OSError, match="disk full"):
        store.responses.migrate_if_needed(cid, sid)
    assert path.read_bytes() == before


def test_swipe_restores_each_post_of_multipart_variant(scene):
    cid, sid = scene
    store.scenes.append_message(cid, sid, "assistant", "First.", speaker="Mara")
    record, = store.responses.migrate(cid, sid)
    full = store.responses.save_variant(cid, sid, record["id"], "Second.", "complete", part="b")
    original = store.scenes.read_scene(cid, sid)["messages"]
    alternate = store.responses.save_variant(cid, sid, record["id"], "Other.", "complete", activate=False)
    store.responses.activate(cid, sid, record["id"], alternate["id"])
    store.responses.activate(cid, sid, record["id"], full["id"])
    restored = store.scenes.read_scene(cid, sid)["messages"]
    assert [(m["content"], m["post_id"]) for m in restored] == [
        (m["content"], m["post_id"]) for m in original]
