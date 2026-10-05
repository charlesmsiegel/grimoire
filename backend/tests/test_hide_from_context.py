"""Hiding a post from context: the flag, the mutator, the route and every reader."""

from grimoire.store.scenes import serialize

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
