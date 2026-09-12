"""Durable response identity and strict handoff protocol."""

import pytest

from grimoire import store
from grimoire.store.response_protocol import ResponseWatcher, validate_handoff


@pytest.mark.parametrize("cut", range(1, 40))
def test_split_handoff_never_leaks(cut):
    text = 'Hello.\n```state\n{}\n```\n```handoff\n{"next":"characters:winifred"}\n```'
    watcher = ResponseWatcher()
    shown = watcher.feed(text[:cut]) + watcher.feed(text[cut:]) + watcher.finish()
    assert shown.strip() == "Hello."
    assert watcher.handoff == {"next": "characters:winifred"}
    assert watcher.narration.strip() == "Hello.\n```state\n{}\n```"


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {"next": []},
        {"next": "characters:mara"},
        {"next": "pcs:seraphine"},
        {"next": "absent"},
        {"next": "grimoire"},
        {"next": None, "extra": 1},
    ],
)
def test_invalid_handoff_stops(payload):
    nxt, issue = validate_handoff(
        payload, ["characters:winifred", "grimoire"], ["characters:mara", "grimoire"]
    )
    assert nxt is None and issue


def test_valid_stop_and_actor():
    assert validate_handoff({"next": None}, [], []) == (None, None)
    assert validate_handoff({"next": "characters:mara"}, ["characters:mara"], []) == (
        "characters:mara",
        None,
    )


def test_roll_precedes_handoff():
    watcher = ResponseWatcher()
    watcher.feed(
        'Wait.\n```roll\n{"check":"notice"}\n```\n```handoff\n{"next":"characters:mara"}\n```'
    )
    watcher.finish()
    assert watcher.roll.complete and watcher.handoff is None


def test_response_ids_survive_identical_text_and_middle_delete(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = store.worlds.create_world("Realm")
    cid = store.campaigns.create_campaign("Saltmarch", wid)
    sid = store.scenes.create_scene(cid, "Mara")
    store.scenes.append_message(cid, sid, "assistant", "Same.", speaker="Mara")
    store.scenes.append_message(cid, sid, "assistant", "Same.", speaker="Mara")
    records = store.responses.migrate(cid, sid)
    first, second = [r["id"] for r in records]
    assert first != second
    store.responses.delete(cid, sid, first)
    messages = store.scenes.read_scene(cid, sid)["messages"]
    assert len(messages) == 1 and messages[0]["response_id"] == second
    assert messages[0]["context_changed"]
    store.scenes.edit_message(cid, sid, 0, "Edited.")
    assert store.scenes.read_scene(cid, sid)["messages"][0]["response_id"] == second
    assert store.responses.get(cid, sid, second)["content"] == "Edited."


@pytest.mark.parametrize("tail", ["```h", "```hand", "```handoff", '```handoff\n{"next":'])
def test_torn_handoff_hidden(tail):
    watcher = ResponseWatcher()
    shown = watcher.feed("Hello.\n" + tail) + watcher.finish()
    assert shown.strip() == "Hello."
    assert watcher.narration.strip() == "Hello."
    assert watcher.handoff is None


def test_legacy_migration_preserves_combined_boundaries(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = store.worlds.create_world("Realm")
    cid = store.campaigns.create_campaign("Saltmarch", wid)
    sid = store.scenes.create_scene(cid, "Mara")
    store.scenes.append_reply(
        cid,
        sid,
        [{"speaker": "Mara", "content": "One."}, {"speaker": "Winifred", "content": "Two."}],
    )
    before = store.scenes.get_turn_sizes(cid, sid)
    store.responses.migrate(cid, sid)
    assert store.scenes.get_turn_sizes(cid, sid) == before
    assert store.scenes.remove_trailing_assistant_run(cid, sid)["size"] == 2


def test_recovery_replaces_partial_text_after_variant_commit_crash(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = store.worlds.create_world("Realm")
    cid = store.campaigns.create_campaign("Saltmarch", wid)
    sid = store.scenes.create_scene(cid, "Mara")
    round_record = store.responses.new_round(
        cid, sid, eligible=[], automatic=False, post=None, run_id="test")
    record = store.responses.prepare(cid, sid, round_record["id"],
                                     "characters:mara", "Mara", {})
    rid = record["id"]
    store.responses.save_variant(cid, sid, rid, "Partial", "incomplete")
    from grimoire.store.scenes import write
    original = write.replace_messages
    def crash(*args, **kwargs):
        raise OSError("simulated transcript write failure")
    monkeypatch.setattr(write, "replace_messages", crash)
    with pytest.raises(OSError):
        store.responses.save_variant(cid, sid, rid, "Complete answer", "complete")
    monkeypatch.setattr(write, "replace_messages", original)
    store.responses.publish_saved(cid, sid, rid)
    store.responses.publish_saved(cid, sid, rid)
    messages = store.scenes.read_scene(cid, sid)["messages"]
    assert len(messages) == 1
    assert messages[0]["content"] == "Complete answer"
    assert messages[0]["response_status"] == "complete"
