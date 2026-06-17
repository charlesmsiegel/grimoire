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
