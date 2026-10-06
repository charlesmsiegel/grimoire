"""Hiding a post from context: the flag, the mutator, the route and every reader."""

import hashlib
import json

import pytest

from grimoire import routes, store
from grimoire.routes import character_turns
from grimoire.store import campaigns, chronicle, entities
from grimoire.store.absorb import routing as absorb_routing
from grimoire.store.context import story
from grimoire.store.scenes import serialize
from tests.llm_fakes import FakeLLM
from tests.test_character_turns import seed
from tests.test_group_play_turns import MARA, WINIFRED, group, reply, speakers, use
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


# --- the three staleness digests ---------------------------------------------

MSGS = [{"role": "user", "speaker": "You", "content": "hi"},
        {"role": "assistant", "speaker": "Mara", "content": "Hm."}]


def test_unflagged_digests_match_the_previous_formula():
    old_hash = hashlib.sha256(json.dumps(
        [{k: m[k] for k in ("role", "speaker", "content")} for m in MSGS],
        sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    assert store.responses.transcript_hash(MSGS) == old_hash
    old_cov = hashlib.sha256(json.dumps(
        [[[m["role"], m["speaker"], m["content"]] for m in MSGS], "Mara"],
        ensure_ascii=False).encode("utf-8")).hexdigest()
    assert store.rolling_summary.covered_digest(MSGS, "Mara") == old_cov
    h = hashlib.sha256()
    for m in MSGS:
        h.update(json.dumps([m["role"], m["speaker"], m["content"]], ensure_ascii=False).encode("utf-8"))
        h.update(b"\x1e")
    assert store.pending_reviews.watermark(MSGS) == {"count": 2, "digest": h.hexdigest()}


def test_excluding_a_post_moves_every_digest():
    flagged = [MSGS[0], {**MSGS[1], "excluded": STAMP}]
    assert store.responses.transcript_hash(flagged) != store.responses.transcript_hash(MSGS)
    assert store.rolling_summary.covered_digest(flagged) != store.rolling_summary.covered_digest(MSGS)
    assert store.pending_reviews.watermark(flagged)["digest"] != store.pending_reviews.watermark(MSGS)["digest"]


def test_covered_digest_reads_only_the_posts_in_context():
    """The summary is folded from the in-context render, so a hidden post's
    prose is not part of what it covers: rewriting it moves nothing."""
    hidden = [MSGS[0], {**MSGS[1], "excluded": STAMP}]
    swiped = [MSGS[0], {**MSGS[1], "content": "Something else.", "excluded": STAMP}]
    digest = store.rolling_summary.covered_digest
    assert digest(hidden, "Mara") == digest(swiped, "Mara") == digest(MSGS[:1], "Mara")
    assert digest(hidden, "Mara") != digest(MSGS, "Mara")


def test_activating_an_excluded_folded_response_keeps_the_summary(client):
    from tests.test_responses import _fold, _summary, _two_turns_with_a_reroll
    cid, sid, base, rid, old = _two_turns_with_a_reroll(client)
    store.scenes.set_excluded(cid, sid, 3, True)
    _fold(cid, sid, 4)
    swiped = client.post(base + f"/responses/{rid}/variants/{old}/activate")
    assert swiped.status_code == 200, swiped.text
    kept = _summary(cid, sid)
    assert (kept["summary"], kept["at"]) == ("Earlier.", 4)
    messages = _messages(cid, sid)
    assert messages[3]["content"] == "Second." and messages[3].get("excluded")
    assert kept["digest"] == store.rolling_summary.covered_digest(
        messages[:4], store.appearances.player_label(cid, sid))


# --- the toggle route, and refusing a stale frozen prompt -------------------

SYNTHETIC = [store.scenes.ROLL_SPEAKER, store.scenes.TRANSITION_SPEAKER, store.scenes.DIRECTOR_SPEAKER]


def test_route_excludes_and_reincludes(client):
    cid, sid = seed(client)
    store.scenes.append_message(cid, sid, "user", "ooc: brb")
    url = f"/api/campaigns/{cid}/scenes/{sid}/messages/0/excluded"
    assert client.put(url, json={"excluded": True}).json() == {"ok": True}
    assert _messages(cid, sid)[0]["excluded"]
    assert client.put(url, json={"excluded": False}).status_code == 200
    assert "excluded" not in _messages(cid, sid)[0]


@pytest.mark.parametrize("speaker", SYNTHETIC)
def test_route_refuses_a_synthetic_line(client, speaker):
    cid, sid = seed(client)
    store.scenes.append_message(cid, sid, "assistant", "a synthetic line", speaker=speaker)
    r = client.put(f"/api/campaigns/{cid}/scenes/{sid}/messages/0/excluded", json={"excluded": True})
    assert r.status_code == 400 and r.json()["kind"] == "not_excludable"
    assert "excluded" not in _messages(cid, sid)[0]


def test_route_refuses_out_of_range_unknown_and_absorbed(client):
    cid, sid = seed(client)
    store.scenes.append_message(cid, sid, "user", "ooc: brb")
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    assert client.put(base + "/messages/9/excluded", json={"excluded": True}).status_code == 400
    assert client.put(f"/api/campaigns/{cid}/scenes/nope/messages/0/excluded",
                      json={"excluded": True}).status_code == 404
    store.scenes.mark_absorbed(cid, sid, "x", "y")
    r = client.put(base + "/messages/0/excluded", json={"excluded": True})
    assert r.status_code == 409 and r.json()["kind"] == "scene_absorbed"
    assert "excluded" not in _messages(cid, sid)[0]


def test_route_refuses_while_a_round_is_open(client):
    cid, sid = seed(client)
    fake = FakeLLM([['Wait.\n```roll\n{"check":"notice"}\n```']])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    client.post(base + "/chat", json={"content": "Hello", "speaker_ref": "characters:mara"})
    assert store.responses.unfinished(cid, sid) is not None
    r = client.put(base + "/messages/0/excluded", json={"excluded": True})
    assert r.status_code == 409 and r.json()["kind"] == "round_open"
    assert "excluded" not in _messages(cid, sid)[0]


def _chat(client, base, content="Hello", reply="Hm."):
    fake = FakeLLM([[reply + '\n```handoff\n{"next":null}\n```']])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    result = client.post(base + "/chat", json={"content": content, "speaker_ref": "characters:mara"})
    assert "error" not in result.text, result.text
    return fake, client.get(base).json()["messages"][-1]["response_id"]


def test_reroll_of_a_reply_composed_before_an_exclusion_is_refused(client, monkeypatch):
    cid, sid = seed(client)
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    fake, rid = _chat(client, base)
    monkeypatch.setattr(store.scenes.write, "now_iso", lambda: "2999-01-01T00:00:00Z")
    assert client.put(base + "/messages/0/excluded", json={"excluded": True}).status_code == 200
    r = client.post(base + f"/responses/{rid}/regenerate")
    assert r.status_code == 409 and r.json()["kind"] == "context_excluded"
    assert r.json()["detail"] == ("A post this reply was written from is now hidden. "
                                  "Replay from here to regenerate without it.")
    assert fake.calls == 1   # no second generation


def test_a_reply_composed_after_the_exclusion_rerolls(client, monkeypatch):
    cid, sid = seed(client)
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    store.scenes.append_message(cid, sid, "user", "ooc: brb")
    with monkeypatch.context() as m:
        m.setattr(store.scenes.write, "now_iso", lambda: "2000-01-01T00:00:00Z")
        assert client.put(base + "/messages/0/excluded", json={"excluded": True}).status_code == 200
    _, rid = _chat(client, base)
    before = len(store.responses.get(cid, sid, rid)["variants"])
    fake = FakeLLM([['Again.\n```handoff\n{"next":null}\n```']])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    r = client.post(base + f"/responses/{rid}/regenerate")
    assert r.status_code == 200 and "error" not in r.text, r.text
    assert len(store.responses.get(cid, sid, rid)["variants"]) == before + 1


# --- every prompt input skips excluded posts --------------------------------

def test_composed_turn_and_inspector_omit_an_excluded_post(client):
    cid, sid = seed(client)
    store.scenes.append_message(cid, sid, "user", "ooc: the heliotrope is my cat")
    store.scenes.append_message(cid, sid, "user", "Mara, the tide is turning.")
    entities.create_entity(campaigns.campaign_root(cid), "lore", "Heliotrope",
                           "The flower of oaths.", keys="heliotrope")
    text = "\n".join(m["content"] for m in store.context.build_messages(cid, sid))
    assert "heliotrope is my cat" in text and "The flower of oaths." in text   # the control
    store.scenes.set_excluded(cid, sid, 0, True)
    text = "\n".join(m["content"] for m in store.context.build_messages(cid, sid))
    assert "heliotrope is my cat" not in text and "The flower of oaths." not in text
    assert "the tide is turning" in text
    rows = store.context.context_breakdown(cid, sid)["sections"]
    assert all("heliotrope is my cat" not in r.get("text", "") for r in rows)
    assert any("the tide is turning" in r.get("text", "") for r in rows)


def test_selector_conversation_omits_an_excluded_post(client):
    cid, sid = seed(client)
    store.scenes.append_message(cid, sid, "user", "ooc: brb, kettle")
    store.scenes.append_message(cid, sid, "user", "Winifred, the lamps.")
    store.scenes.set_excluded(cid, sid, 0, True)
    prompt = character_turns._selector_messages(cid, sid, {"eligible": [], "note": ""})[0]["content"]
    assert "brb, kettle" not in prompt and "Winifred, the lamps." in prompt


def test_transcript_text_filters_by_default():
    ms = [{"role": "user", "content": "ooc: brb", "excluded": STAMP},
          {"role": "assistant", "speaker": "Mara", "content": "The tide turns."}]
    assert "ooc: brb" not in chronicle.transcript_text(ms)
    assert "The tide turns." in chronicle.transcript_text(ms)
    assert "ooc: brb" in chronicle.transcript_text(ms, include_excluded=True)


def test_absorb_evidence_ignores_a_quote_only_in_an_excluded_post(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = store.worlds.create_world("Realm")
    cid = store.campaigns.create_campaign("Saltmarch", wid)
    sid = store.scenes.create_scene(cid, "The Pier")
    store.scenes.append_message(cid, sid, "assistant", "I hid the key.", speaker="Mara")
    store.scenes.append_message(cid, sid, "assistant", "The fog lifts.", speaker="Mara")
    shown = store.scenes.read_scene(cid, sid)["messages"]
    assert "i hid the key" in absorb_routing.speaker_index(cid, sid, shown, player_label="")["texts"]["Mara"]
    store.scenes.set_excluded(cid, sid, 0, True)
    shown = store.scenes.read_scene(cid, sid)["messages"]
    for messages in (shown, None):    # the snapshot and the fallback read alike
        index = absorb_routing.speaker_index(cid, sid, messages, player_label="")
        assert "i hid the key" not in index["texts"].get("Mara", "")
        assert "the fog lifts" in index["texts"]["Mara"]


def test_an_all_excluded_scene_is_empty_to_absorb_dossiers_and_the_fold(client):
    cid, sid = seed(client)
    store.scenes.append_message(cid, sid, "user", "ooc: testing")
    store.scenes.append_message(cid, sid, "assistant", "ooc: ok", speaker="Mara")
    store.scenes.set_excluded(cid, sid, 0, True)
    store.scenes.set_excluded(cid, sid, 1, True)
    fake = FakeLLM([["must not run"]])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    r = client.post(base + "/absorb")
    assert r.status_code == 400 and r.json()["detail"] == "nothing to absorb"
    r = client.post(base + "/dossiers")
    assert r.status_code == 400 and r.json()["detail"] == "nothing to build dossiers from"
    body = client.post(base + "/rolling-summary?force=true").json()
    assert body["refreshed"] is False
    assert fake.calls == 0


# --- group play: the speaker-order planner and image slots ------------------

def test_the_speaker_order_planner_continues_from_the_last_post_in_context(client):
    # List order Mara, Winifred: an empty send continues after the newest
    # contribution. Hiding Winifred's reply makes Mara's the newest one the
    # planner can see, so Winifred is next again -- not Mara.
    cid, sid = seed(client)
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    group(client, base, order="list", order_list=[MARA, WINIFRED])
    use(client, FakeLLM([reply("Mara answers.", None)]))
    client.post(base + "/chat", json={"speaker_ref": MARA})
    use(client, FakeLLM([reply("Winifred continues.", MARA)]))
    client.post(base + "/chat", json={"content": ""})
    assert speakers(cid, sid) == ["Mara", "Winifred"]
    hidden = next(i for i, m in enumerate(_messages(cid, sid)) if m.get("speaker") == "Winifred")
    store.scenes.set_excluded(cid, sid, hidden, True)
    fake = FakeLLM([reply("Winifred again.", MARA)])
    use(client, fake)
    response = client.post(base + "/chat", json={"content": ""})
    assert "error" not in response.text, response.text
    assert speakers(cid, sid) == ["Mara", "Winifred", "Winifred"]


def test_an_excluded_post_takes_no_image_slot(monkeypatch):
    monkeypatch.setattr(story.post_images, "eligible", lambda cid, url: True)
    ms = [{"role": "assistant", "speaker": "Mara", "content": "![the gate](/a.png)"},
          {"role": "user", "content": "ooc: ![my cat](/b.png)", "excluded": STAMP}]
    assert story._chosen_images(ms, 1, "c") == {(0, 0)}
