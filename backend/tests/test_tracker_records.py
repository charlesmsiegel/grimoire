"""Scene tracker records: per-post snapshot files and their index, keyed by the
scene's identity, and the transcript walk that orders them."""

import json

import pytest

from grimoire.store import appearances, campaigns, characters, responses, scenes, worlds
from grimoire.store.appearances import paths as appearances_paths
from grimoire.store.scenes import identity, serialize
from grimoire.store.tracker import fields, records, walk
from grimoire.store.tracker import paths as tpaths

SNAP = {"characters:mara": {"present": True, "fields": {
    "clothing": {"value": "grey cloak", "aware": "present"}}}}
SNAP2 = {"characters:mara": {"present": True, "fields": {
    "clothing": {"value": "soaked grey cloak", "aware": "present"}}}}
A, B, C = "a" * 32, "b" * 32, "c" * 32


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    cid = campaigns.create_campaign("Saltmarch", wid)
    sid = scenes.create_scene(cid, "Quay")
    return wid, cid, sid


def _reply(cid, sid, speaker, content):
    """Append a model post and give it a response id; return that id."""
    scenes.append_reply(cid, sid, [{"speaker": speaker, "content": content}])
    responses.migrate(cid, sid)
    return scenes.read_scene(cid, sid)["messages"][-1]["response_id"]


def _three_posts(cid, sid):
    for pid, text in ((A, "One."), (B, "Two."), (C, "Three.")):
        scenes.append_message(cid, sid, "user", text, post_id=pid)
    return identity.ensure_identity(cid, sid), [tpaths.post_key(p) for p in (A, B, C)]


def test_save_then_read(home):
    _, cid, sid = home
    ident = identity.ensure_identity(cid, sid)
    key = tpaths.post_key(A)
    records.mark_pending(cid, ident, key)
    assert records.read_index(cid, ident)[key]["status"] == "pending"
    assert records.read_snapshot(cid, ident, key) is None

    changed = [["characters:mara", "clothing", "grey cloak"]]
    records.save(cid, ident, key, SNAP, changed=changed, fields_digest="d", model="m")
    assert records.read_index(cid, ident)[key] == {
        "status": "ok", "changed": changed,
        "flags": {"upstream_changed": False, "text_changed": False}, "mark_gen": 1}
    body = records.read_snapshot(cid, ident, key)
    assert body["version"] == 1 and body["snapshot"] == SNAP
    assert body["fields_digest"] == "d" and body["model"] == "m" and body["at"]
    assert (tpaths.scene_dir(cid, ident) / f"{key}.json").is_file()

    records.mark_failed(cid, ident, key, "the model said no")
    entry = records.read_index(cid, ident)[key]
    assert entry["status"] == "failed" and entry["error"] == "the model said no"
    # A failed re-run keeps the earlier snapshot file; a later save clears the error.
    assert records.read_snapshot(cid, ident, key)["snapshot"] == SNAP
    records.save(cid, ident, key, SNAP2, changed=[], fields_digest="d", model="m")
    assert "error" not in records.read_index(cid, ident)[key]


def test_garbled_index_rebuilds_from_files(home):
    _, cid, sid = home
    ident = identity.ensure_identity(cid, sid)
    key = tpaths.post_key(A)
    records.save(cid, ident, key, SNAP, changed=[], fields_digest="d", model="m")
    # The scene layer lives in the same directory and must not become a record.
    fields.write_scene_layer(cid, sid, {"off": ["clothing"]})
    (tpaths.scene_dir(cid, ident) / "index.json").write_text("{", encoding="utf-8")
    index = records.read_index(cid, ident)
    assert index[key]["status"] == "ok"
    assert set(index) == {key}
    assert index[key]["flags"] == {"upstream_changed": False, "text_changed": False}

    (tpaths.scene_dir(cid, ident) / "index.json").unlink()
    assert set(records.read_index(cid, ident)) == {key}


def test_set_flags_ignores_unknown_keys_and_refuses_unknown_flags(home):
    _, cid, sid = home
    ident = identity.ensure_identity(cid, sid)
    key = tpaths.post_key(A)
    records.save(cid, ident, key, SNAP, changed=[], fields_digest="d", model="m")
    records.set_flags(cid, ident, [key, tpaths.post_key(B)], "text_changed")
    index = records.read_index(cid, ident)
    assert set(index) == {key} and index[key]["flags"]["text_changed"] is True
    with pytest.raises(ValueError):
        records.set_flags(cid, ident, [key], "bogus")


def test_discard_and_drop(home):
    _, cid, sid = home
    ident = identity.ensure_identity(cid, sid)
    k1, k2 = tpaths.post_key(A), tpaths.post_key(B)
    for k in (k1, k2):
        records.save(cid, ident, k, SNAP, changed=[], fields_digest="d", model="m")
    records.discard(cid, ident, [k1])
    assert set(records.read_index(cid, ident)) == {k2}
    assert records.read_snapshot(cid, ident, k1) is None
    records.drop(cid, ident)
    assert not tpaths.scene_dir(cid, ident).exists()
    records.drop(cid, ident)  # already gone: fine


def test_ordered_keys_uses_active_variant_and_last_part(home):
    _, cid, sid = home
    scenes.append_message(cid, sid, "user", "Hi.", post_id=A)               # 0
    r1 = _reply(cid, sid, "Mara", "First half.")                            # 1
    scenes.append_message(cid, sid, "assistant", "Rolled 4.",
                          speaker=serialize.ROLL_SPEAKER)                   # 2
    scenes.append_reply(cid, sid, [{"speaker": "Mara", "content": "Second half.",
                                    "response_id": r1, "response_part": "b"}])  # 3
    scenes.append_message(cid, sid, "user", "An old post with no id.")      # 4
    r2 = _reply(cid, sid, "Winifred", "Hello.")                             # 5
    alt = responses.save_variant(cid, sid, r2, "Hello again.", "complete", activate=False)
    responses.activate(cid, sid, r2, alt["id"])

    variants = responses.variants_by_response(cid, sid)
    v1 = variants[r1][0]
    assert variants[r2][0] == alt["id"] and alt["id"] in variants[r2][1]
    assert len(variants[r2][1]) == 2

    expected = [(0, tpaths.post_key(A)), (3, tpaths.response_key(r1, v1)),
                (5, tpaths.response_key(r2, alt["id"]))]
    assert walk.ordered_keys(cid, sid) == expected
    assert walk.key_at(cid, sid, 3) == tpaths.response_key(r1, v1)
    assert walk.key_at(cid, sid, 1) is None
    assert walk.key_at(cid, sid, 2) is None
    assert walk.index_of(cid, sid, tpaths.response_key(r2, alt["id"])) == 5
    assert walk.index_of(cid, sid, tpaths.post_key(B)) is None


def test_variants_by_response_does_not_mint_an_identity(home):
    _, cid, _ = home
    other = scenes.create_scene(cid, "Harbour")
    # Strip the identity a fresh scene was given, to stand for a legacy scene.
    p = scenes.paths._scene_path(cid, other)
    raw = p.read_text(encoding="utf-8")
    p.write_text("\n".join(l for l in raw.split("\n") if not l.startswith("identity:")),
                 encoding="utf-8")
    assert identity.scene_identity(cid, other) is None
    assert responses.variants_by_response(cid, other) == {}
    assert identity.scene_identity(cid, other) is None


def test_state_before_skips_failed_and_pending(home):
    _, cid, sid = home
    ident, (k1, k2, k3) = _three_posts(cid, sid)
    records.save(cid, ident, k1, SNAP, changed=[], fields_digest="d", model="m")
    records.mark_pending(cid, ident, k2)
    records.mark_failed(cid, ident, k2, "boom")
    records.mark_pending(cid, ident, k3)

    assert walk.state_before(cid, sid, walk.index_of(cid, sid, k3)) == (k1, SNAP)
    assert walk.current(cid, sid) == (k1, SNAP)
    assert walk.state_before(cid, sid, 0) == (None, {})

    records.save(cid, ident, k3, SNAP2, changed=[], fields_digest="d", model="m")
    assert walk.current(cid, sid) == (k3, SNAP2)
    assert walk.state_before(cid, sid, walk.index_of(cid, sid, k3)) == (k1, SNAP)


def test_flag_edited_marks_text_and_later(home):
    _, cid, sid = home
    ident, (k1, k2, k3) = _three_posts(cid, sid)
    for k in (k1, k2, k3):
        records.save(cid, ident, k, SNAP, changed=[], fields_digest="d", model="m")

    walk.flag_edited(cid, sid, 1)
    index = records.read_index(cid, ident)
    assert index[k1]["flags"] == {"upstream_changed": False, "text_changed": False}
    assert index[k2]["flags"] == {"upstream_changed": False, "text_changed": True}
    assert index[k3]["flags"] == {"upstream_changed": True, "text_changed": False}

    walk.flag_after(cid, sid, 0)
    index = records.read_index(cid, ident)
    assert index[k1]["flags"]["upstream_changed"] is False
    assert index[k2]["flags"]["upstream_changed"] is True


def test_flag_edited_on_an_earlier_part_flags_its_response(home):
    _, cid, sid = home
    r1 = _reply(cid, sid, "Mara", "First half.")                            # 0
    scenes.append_message(cid, sid, "assistant", "Rolled 4.",
                          speaker=serialize.ROLL_SPEAKER)                   # 1
    scenes.append_reply(cid, sid, [{"speaker": "Mara", "content": "Second half.",
                                    "response_id": r1, "response_part": "b"}])  # 2
    scenes.append_message(cid, sid, "user", "Then?", post_id=A)             # 3
    ident = identity.ensure_identity(cid, sid)
    rk = tpaths.response_key(r1, responses.variants_by_response(cid, sid)[r1][0])
    pk = tpaths.post_key(A)
    for k in (rk, pk):
        records.save(cid, ident, k, SNAP, changed=[], fields_digest="d", model="m")

    walk.flag_edited(cid, sid, 0)
    index = records.read_index(cid, ident)
    assert index[rk]["flags"] == {"upstream_changed": False, "text_changed": True}
    assert index[pk]["flags"] == {"upstream_changed": True, "text_changed": False}


def test_prune_keeps_inactive_variants_drops_cut_posts(home):
    _, cid, sid = home
    scenes.append_message(cid, sid, "user", "Hi.", post_id=A)               # 0
    r1 = _reply(cid, sid, "Mara", "Hello.")                                 # 1
    alt = responses.save_variant(cid, sid, r1, "Hello there.", "complete", activate=False)
    scenes.append_message(cid, sid, "user", "Bye.", post_id=B)              # 2
    ident = identity.ensure_identity(cid, sid)
    active, _ = responses.variants_by_response(cid, sid)[r1]
    keep = [tpaths.post_key(A), tpaths.response_key(r1, active),
            tpaths.response_key(r1, alt["id"])]
    cut = tpaths.post_key(B)
    stray = tpaths.response_key("e" * 32, "f" * 32)
    for k in [*keep, cut, stray]:
        records.save(cid, ident, k, SNAP, changed=[], fields_digest="d", model="m")

    scenes.delete_from(cid, sid, 2)
    assert walk.prune(cid, sid) == 2
    assert set(records.read_index(cid, ident)) == set(keep)
    assert records.read_snapshot(cid, ident, cut) is None
    assert records.read_snapshot(cid, ident, stray) is None
    assert walk.prune(cid, sid) == 0


def test_delete_scene_drops_tracker_dir_and_recycled_sid_starts_clean(home):
    _, cid, sid = home
    scenes.append_message(cid, sid, "user", "Hi.", post_id=A)
    ident = identity.ensure_identity(cid, sid)
    records.save(cid, ident, tpaths.post_key(A), SNAP, changed=[], fields_digest="d", model="m")
    assert walk.current(cid, sid) == (tpaths.post_key(A), SNAP)

    scenes.delete_scene(cid, sid)
    assert not tpaths.scene_dir(cid, ident).exists()

    again = scenes.create_scene(cid, "Quay")
    assert again == sid
    scenes.append_message(cid, again, "user", "Hi.", post_id=A)
    assert walk.ordered_keys(cid, again) == [(0, tpaths.post_key(A))]
    assert walk.current(cid, again) == (None, {})


def test_a_tracker_drop_that_fails_still_deletes_the_scene(home, monkeypatch, caplog):
    """The drop runs after the transcript is gone and fails soft: a failing
    removal must not leave a scene standing with its sidecars already gone."""
    import logging

    from grimoire.store.scenes import lifecycle

    _, cid, sid = home
    scenes.append_message(cid, sid, "user", "Hi.", post_id=A)
    ident = identity.ensure_identity(cid, sid)
    records.save(cid, ident, tpaths.post_key(A), SNAP, changed=[], fields_digest="d", model="m")

    def boom(c, i):
        raise OSError("a file in there is held open")

    monkeypatch.setattr(lifecycle.tracker_records, "drop", boom)
    with caplog.at_level(logging.WARNING):
        scenes.delete_scene(cid, sid)
    assert sid not in {s["id"] for s in scenes.list_scenes(cid)}
    assert any("could not drop the tracker records" in r.getMessage() for r in caplog.records)


def test_present_at_follows_intervals(home):
    wid, cid, sid = home
    for name in ("Mara", "Winifred"):
        characters.create_character(worlds.world_root(wid), name, "main",
                                    characters.blank_card(name))
    appearances.appear(cid, sid, "characters", "mara", "main", "npc", narrate=False)
    scenes.append_message(cid, sid, "user", "One.")                         # 0
    scenes.append_message(cid, sid, "user", "Two.")                         # 1
    appearances.appear(cid, sid, "characters", "winifred", "main", "npc", narrate=False)
    scenes.append_message(cid, sid, "user", "Three.")                       # 2
    scenes.append_message(cid, sid, "user", "Four.")                        # 3
    appearances.leave(cid, sid, "characters", "mara")                       # 4: transition

    mara, winifred = "characters:mara", "characters:winifred"
    assert walk.present_at(cid, sid, 0) == {mara}
    assert walk.present_at(cid, sid, 1) == {mara}
    assert walk.present_at(cid, sid, 2) == {mara, winifred}
    assert walk.present_at(cid, sid, 3) == {mara, winifred}
    assert walk.present_at(cid, sid, 4) == {winifred}
    assert walk.present_at(cid, sid, 9) == {winifred}
    # Mara left, and is still named: earlier posts' records describe her.
    assert walk.roster(cid, sid) == {mara: "Mara", winifred: "Winifred"}
    assert walk.roster(cid, sid, departed=False) == {winifred: "Winifred"}

    # A cast member with no intervals for this scene (a legacy appearance) counts
    # as present throughout.
    record_path = appearances_paths._path(cid)
    data = json.loads(record_path.read_text(encoding="utf-8"))
    data["characters/winifred"]["presence"].pop(sid)
    record_path.write_text(json.dumps(data), encoding="utf-8")
    assert walk.present_at(cid, sid, 0) == {mara, winifred}


def test_prune_keeps_response_records_when_a_later_identity_read_fails(home, monkeypatch):
    """`scene_identity` answers None for an unreadable scene file. A second
    lookup failing inside prune must not make every response record look dead."""
    _, cid, sid = home
    r1 = _reply(cid, sid, "Mara", "Hello.")
    ident = identity.ensure_identity(cid, sid)
    key = tpaths.response_key(r1, responses.variants_by_response(cid, sid)[r1][0])
    records.save(cid, ident, key, SNAP, changed=[], fields_digest="d", model="m")

    real = identity.scene_identity
    calls = []

    def flaky(c, s):
        calls.append(s)
        return real(c, s) if len(calls) == 1 else None

    with monkeypatch.context() as m:
        m.setattr(identity, "scene_identity", flaky)
        assert walk.prune(cid, sid) == 0
    assert set(records.read_index(cid, ident)) == {key}
    assert records.read_snapshot(cid, ident, key) is not None
