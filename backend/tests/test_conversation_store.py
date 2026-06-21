import importlib

import grimoire.store as store


def reload_with_home(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    importlib.reload(store)
    return store


def test_create_list_and_read_empty(monkeypatch, tmp_path):
    s = reload_with_home(monkeypatch, tmp_path)
    cid = s.create_conversation("My First Chat")
    assert cid.endswith("my-first-chat")
    metas = s.list_conversations()
    assert len(metas) == 1
    assert metas[0]["id"] == cid
    assert metas[0]["title"] == "My First Chat"
    conv = s.read_conversation(cid)
    assert conv["messages"] == []


def test_append_and_parse_roundtrip(monkeypatch, tmp_path):
    s = reload_with_home(monkeypatch, tmp_path)
    cid = s.create_conversation("Roundtrip")
    s.append_message(cid, "user", "Describe the keeper.\n\n**Not a real marker** still mine.")
    s.append_message(cid, "assistant", "She is older than the salt.")
    conv = s.read_conversation(cid)
    assert conv["messages"] == [
        {"role": "user", "content": "Describe the keeper.\n\n**Not a real marker** still mine."},
        {"role": "assistant", "content": "She is older than the salt."},
    ]


def test_unknown_conversation_raises(monkeypatch, tmp_path):
    s = reload_with_home(monkeypatch, tmp_path)
    try:
        s.read_conversation("nope")
        assert False, "expected error"
    except store.ConversationNotFound:
        pass


def test_rename_changes_id_to_reflect_title(monkeypatch, tmp_path):
    s = reload_with_home(monkeypatch, tmp_path)
    cid = s.create_conversation("Old Title")
    s.append_message(cid, "user", "keep me")
    before = s.list_conversations()[0]["updated"]
    new_cid = s.rename_conversation(cid, "Shiny New Name")
    assert new_cid != cid
    assert new_cid.endswith("shiny-new-name")  # filename reflects the title
    metas = s.list_conversations()
    assert len(metas) == 1
    assert metas[0]["id"] == new_cid
    assert metas[0]["title"] == "Shiny New Name"
    assert metas[0]["updated"] == before  # rename does not reorder
    # content moves to the new id; the old id is gone
    assert s.read_conversation(new_cid)["messages"] == [{"role": "user", "content": "keep me"}]
    try:
        s.read_conversation(cid)
        assert False, "old id should no longer exist"
    except store.ConversationNotFound:
        pass


def test_rename_to_same_title_keeps_messages(monkeypatch, tmp_path):
    s = reload_with_home(monkeypatch, tmp_path)
    cid = s.create_conversation("Same")
    s.append_message(cid, "user", "hi")
    new_cid = s.rename_conversation(cid, "Same")
    assert new_cid == cid  # no-op slug, no collision suffix
    assert s.read_conversation(new_cid)["messages"] == [{"role": "user", "content": "hi"}]


def test_delete_removes_conversation(monkeypatch, tmp_path):
    s = reload_with_home(monkeypatch, tmp_path)
    cid = s.create_conversation("Doomed")
    s.delete_conversation(cid)
    assert s.list_conversations() == []
    try:
        s.read_conversation(cid)
        assert False, "expected error"
    except store.ConversationNotFound:
        pass


def test_delete_missing_raises(monkeypatch, tmp_path):
    s = reload_with_home(monkeypatch, tmp_path)
    try:
        s.delete_conversation("nope")
        assert False, "expected error"
    except store.ConversationNotFound:
        pass
