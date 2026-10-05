"""Hiding a post from context: the flag, the mutator, the route and every reader."""

import pytest

from grimoire import routes, store
from grimoire.store.scenes import serialize
from tests.llm_fakes import FakeLLM
from tests.test_character_turns import seed
from tests.test_response_controls_routes import _answer

STAMP = "2026-10-05T12:00:00Z"


def _round_trip(messages):
    return serialize._parse_messages(serialize._serialize_messages(messages), frozenset())


def test_flag_round_trips_on_player_model_and_response_posts():
    out = _round_trip([
        {"role": "user", "speaker": "You", "content": "ooc: brb", "excluded": STAMP},
        {"role": "assistant", "speaker": "Mara", "content": "Hm.", "excluded": STAMP},
        {"role": "assistant", "speaker": "Winifred", "content": "Go.", "response_id": "a" * 32,
         "excluded": STAMP},
    ])
    assert [m.get("excluded") for m in out] == [STAMP, STAMP, STAMP]
    assert all("grimoire-response" not in m["content"] for m in out)


def test_excluded_false_writes_no_key():
    body = serialize._serialize_messages([{"role": "assistant", "speaker": "Mara",
                                           "content": "Hm.", "excluded": False}])
    assert "grimoire-response" not in body
    assert "excluded" not in _round_trip([{"role": "assistant", "speaker": "Mara",
                                           "content": "Hm.", "excluded": False}])[0]


def test_without_excluded_keeps_director_notes_and_in_context_drops_them():
    note = {"role": "assistant", "speaker": serialize.DIRECTOR_SPEAKER, "content": "faster"}
    hidden = {"role": "user", "speaker": "You", "content": "ooc: brb", "excluded": STAMP}
    plain = {"role": "assistant", "speaker": "Mara", "content": "Hm."}
    ms = [note, hidden, plain]
    assert serialize.without_excluded(ms) == [note, plain]
    assert serialize.in_context(ms) == [plain]


def test_excludable_refuses_the_three_synthetic_speakers():
    for speaker in (serialize.ROLL_SPEAKER, serialize.TRANSITION_SPEAKER, serialize.DIRECTOR_SPEAKER):
        assert serialize.excludable({"role": "assistant", "speaker": speaker, "content": "x"}) is False
    assert serialize.excludable({"role": "assistant", "speaker": "Mara", "content": "x"}) is True
    assert serialize.excludable({"role": "user", "content": "x"}) is True


def test_excluded_since_is_the_latest_stamp_before_the_index():
    ms = [{"role": "user", "content": str(i)} for i in range(6)]
    ms[0]["excluded"] = "2026-10-05T10:00:00Z"
    ms[2]["excluded"] = "2026-10-05T11:00:00Z"
    ms[4]["excluded"] = STAMP
    assert serialize.excluded_since(ms, 4) == "2026-10-05T11:00:00Z"
    assert serialize.excluded_since(ms, 0) is None
    assert serialize.excluded_since(ms, 6) == STAMP


# --- the store mutator, and the ledger carrying the flag --------------------

def _messages(cid, sid):
    return store.scenes.read_scene(cid, sid)["messages"]


def test_set_excluded_sets_and_clears_and_is_idempotent(client):
    cid, sid = seed(client)
    store.scenes.append_message(cid, sid, "user", "ooc: brb")
    assert store.scenes.set_excluded(cid, sid, 0, True) is True
    stamp = _messages(cid, sid)[0]["excluded"]
    assert isinstance(stamp, str) and stamp
    assert store.scenes.set_excluded(cid, sid, 0, True) is False
    assert _messages(cid, sid)[0]["excluded"] == stamp
    assert store.scenes.set_excluded(cid, sid, 0, False) is True
    assert "excluded" not in _messages(cid, sid)[0]
    assert store.scenes.set_excluded(cid, sid, 0, False) is False


@pytest.mark.parametrize("speaker", [store.scenes.ROLL_SPEAKER, store.scenes.TRANSITION_SPEAKER,
                                     store.scenes.DIRECTOR_SPEAKER])
def test_set_excluded_refuses_roll_transition_and_note_lines(client, speaker):
    cid, sid = seed(client)
    store.scenes.append_message(cid, sid, "assistant", "a synthetic line", speaker=speaker)
    with pytest.raises(store.scenes.NotExcludable):
        store.scenes.set_excluded(cid, sid, 0, True)
    with pytest.raises(IndexError):
        store.scenes.set_excluded(cid, sid, 99, True)
    assert "excluded" not in _messages(cid, sid)[0]


def _two_part_response(client):
    cid, sid = seed(client)
    fake = FakeLLM([
        ['Wait.\n```roll\n{"check":"notice"}\n```'],
        ['No roll.\n```handoff\n{"next":"characters:winifred"}\n```'],
        ['Answer.\n```handoff\n{"next":null}\n```'],
    ])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    client.post(base + "/chat", json={"content": "Hello", "speaker_ref": "characters:mara"})
    proposal = store.proposals.get(cid, sid)
    response = client.post(base + "/roll-proposal", json={"proposal": proposal["id"], "action": "decline"})
    assert "error" not in response.text, response.text
    return cid, sid


def test_toggling_one_part_sets_every_part(client):
    cid, sid = _two_part_response(client)
    messages = _messages(cid, sid)
    first = next(i for i, m in enumerate(messages) if m["content"] == "Wait.")
    rid = messages[first]["response_id"]
    assert store.scenes.set_excluded(cid, sid, first, True) is True
    parts = [m for m in _messages(cid, sid) if m.get("response_id") == rid]
    assert [m["content"] for m in parts] == ["Wait.", "No roll."]
    assert all(m.get("excluded") for m in parts)
    assert not _messages(cid, sid)[-1].get("excluded")   # Winifred's reply is another response
    # Returning the second part to context returns the whole response.
    second = next(i for i, m in enumerate(_messages(cid, sid)) if m["content"] == "No roll.")
    store.scenes.set_excluded(cid, sid, second, False)
    assert not any(m.get("excluded") for m in _messages(cid, sid) if m.get("response_id") == rid)


def test_a_new_part_inherits_the_flag(client):
    cid, sid = seed(client)
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    rid = _answer(client, base)
    index = len(_messages(cid, sid)) - 1
    store.scenes.set_excluded(cid, sid, index, True)
    store.responses.save_variant(cid, sid, rid, "More.", "complete", part="p2")
    more = next(m for m in _messages(cid, sid) if m["content"] == "More.")
    assert more.get("excluded")


def test_activate_keeps_the_flag_on_a_swiped_response(client):
    cid, sid = seed(client)
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    rid = _answer(client, base)
    replacement = store.responses.save_variant(cid, sid, rid, "Replacement.", "complete", activate=False)
    index = len(_messages(cid, sid)) - 1
    store.scenes.set_excluded(cid, sid, index, True)
    result = client.post(base + f"/responses/{rid}/variants/{replacement['id']}/activate")
    assert result.status_code == 200, result.text
    message = _messages(cid, sid)[index]
    assert message["content"] == "Replacement." and message.get("excluded")


def test_reroll_keeps_the_flag(client, monkeypatch):
    cid, sid = seed(client)
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    rid = _answer(client, base)
    index = len(_messages(cid, sid)) - 1
    store.scenes.set_excluded(cid, sid, index, True)
    fake = FakeLLM([['Again.\n```handoff\n{"next":null}\n```']])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    result = client.post(base + f"/responses/{rid}/regenerate")
    assert "error" not in result.text, result.text
    message = _messages(cid, sid)[index]
    assert message["content"] == "Again." and message.get("excluded")


def test_a_later_response_is_flagged_context_changed(client):
    cid, sid = seed(client)
    fake = FakeLLM([['Hm.\n```handoff\n{"next":null}\n```']])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    client.post(base + "/chat", json={"content": "Hello", "speaker_ref": "characters:mara"})
    assert _messages(cid, sid)[1].get("context_changed") is False
    store.scenes.set_excluded(cid, sid, 0, True)
    assert _messages(cid, sid)[1]["context_changed"] is True
