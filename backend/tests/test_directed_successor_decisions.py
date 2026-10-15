"""Directed play: an independent Decision chooses every post-generation speaker.

These tests intentionally return prose WITHOUT a handoff fence. The writer
must not control the next speaker, and a completed reply survives an abstention.
"""

from types import SimpleNamespace

from grimoire import decisions, routes, store
from grimoire.routes import character_turns, streaming
from tests.llm_fakes import FakeLLM

MARA = "characters:mara"
WINIFRED = "characters:winifred"
CORIN = "characters:corin"


def seed(client, *, count=3):
    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-or-x"})
    wid = store.worlds.create_world("Realm")
    cid = store.campaigns.create_campaign("Saltmarch", wid)
    sid = store.scenes.create_scene(cid, "The common room")
    for name in ("Mara", "Winifred", "Corin")[:count]:
        response = client.post(f"/api/campaigns/{cid}/characters", json={"name": name})
        assert response.status_code == 200, response.text
        actor = response.json()["character"]
        seated = client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast", json={"id": actor})
        assert seated.status_code == 200, seated.text
    return cid, sid, f"/api/campaigns/{cid}/scenes/{sid}"


def scripted_decisions(monkeypatch, *answers):
    sequence = iter(answers)
    asked = []

    async def decide(task, items, **kwargs):
        assert task == "response-selector"
        (question,) = items[0].questions
        available = tuple(option.id for option in question.options)
        selected = next(sequence)
        asked.append((question.instructions, available, selected))
        result = decisions.ItemResult(
            {"next": decisions.Answer(
                selected, "abstained" if selected is None else "")},
            backend="structured",
        )
        return decisions.Decision(items=(result,), backend="structured")

    monkeypatch.setattr(character_turns.operations, "decide", decide)
    return asked


def replies(cid, sid):
    return [m["speaker"] for m in store.scenes.read_scene(cid, sid)["messages"]
            if m.get("response_id")]


def last_round(cid, sid):
    scope = store.responses._scope(cid, sid, store.responses._read(cid))
    return list(scope["rounds"].values())[-1]


def test_follow_on_decisions_produce_three_distinct_npcs_without_writer_handoffs(
        client, monkeypatch):
    cid, sid, base = seed(client)
    store.write_config(decide_speaker_sampling="off")
    model = FakeLLM([["Mara reacts."], ["Winifred responds."], ["Corin adds something."]])
    client.app.dependency_overrides[routes.get_llm] = lambda: model
    asked = scripted_decisions(monkeypatch, WINIFRED, CORIN)

    response = client.post(base + "/chat",
                           json={"content": "What does everyone make of this?", "speaker_ref": MARA})
    assert '"error"' not in response.text, response.text
    assert replies(cid, sid) == ["Mara", "Winifred", "Corin"]
    assert model.calls == 3
    assert len(asked) == 2  # no narrator-only fourth call
    assert asked[0][1] == (WINIFRED, CORIN, "grimoire")
    assert asked[1][1] == (CORIN, "grimoire")
    assert all("NEXT speaker" in prompt for prompt, _options, _selected in asked)
    first_prompt = "\n".join(m["content"] for m in model.requests[0]["messages"])
    assert "Do not propose or emit a handoff" in first_prompt
    assert "Every ordinary completed response MUST end" not in first_prompt
    assert last_round(cid, sid)["used"] == [MARA, WINIFRED, CORIN]


def test_null_follow_on_hands_back_without_undoing_completed_prose(client, monkeypatch):
    cid, sid, base = seed(client, count=2)
    store.write_config(decide_speaker_sampling="off")
    model = FakeLLM([["Mara answers."]])
    client.app.dependency_overrides[routes.get_llm] = lambda: model
    asked = scripted_decisions(monkeypatch, None)

    response = client.post(base + "/chat",
                           json={"content": "Mara, what happened?", "speaker_ref": MARA})
    assert '"error"' not in response.text, response.text
    assert replies(cid, sid) == ["Mara"]
    assert len(asked) == 1
    assert asked[0][1] == (WINIFRED, "grimoire")
    assert last_round(cid, sid)["issue"] is None
    assert last_round(cid, sid)["status"] == "complete"


def test_one_npc_does_not_get_an_automatic_narrator_tail(client, monkeypatch):
    cid, sid, base = seed(client, count=1)
    model = FakeLLM([["Mara answers."]])
    client.app.dependency_overrides[routes.get_llm] = lambda: model
    asked = scripted_decisions(monkeypatch)

    response = client.post(base + "/chat", json={"content": "Mara?", "speaker_ref": MARA})
    assert '"error"' not in response.text, response.text
    assert replies(cid, sid) == ["Mara"]
    assert model.calls == 1
    assert asked == []


def test_out_of_pool_decision_cannot_repeat_current_npc(client, monkeypatch):
    cid, sid, base = seed(client, count=2)
    store.write_config(decide_speaker_sampling="off")
    model = FakeLLM([["Mara answers."]])
    client.app.dependency_overrides[routes.get_llm] = lambda: model
    asked = scripted_decisions(monkeypatch, MARA)  # malformed backend answer

    response = client.post(base + "/chat",
                           json={"content": "Mara, your thoughts?", "speaker_ref": MARA})
    assert '"error"' not in response.text, response.text
    assert replies(cid, sid) == ["Mara"]
    assert MARA not in asked[0][1]
    assert last_round(cid, sid)["issue"] == store.response_protocol.INELIGIBLE


def test_extra_rounds_can_repeat_a_different_used_npc_but_not_current():
    round_ = {"automatic": True, "mode": "directed", "auto_remaining": 1}
    assert character_turns._successor_answer(
        round_, [MARA, WINIFRED], WINIFRED, MARA)["lead"] == MARA
    assert character_turns._successor_answer(
        round_, [MARA, WINIFRED], WINIFRED, WINIFRED)["issue"] == (
            store.response_protocol.INELIGIBLE)
    round_["auto_remaining"] = 0
    assert character_turns._successor_answer(
        round_, [MARA, WINIFRED], WINIFRED, MARA)["issue"] == (
            store.response_protocol.INELIGIBLE)


def test_completed_reply_recovery_keeps_pending_until_successor_is_decided(client):
    cid, sid, _ = seed(client, count=2)
    entries = character_turns.roster(cid, sid)
    round_ = store.responses.new_round(
        cid, sid, eligible=entries, automatic=True, post=None,
        run_id="recover", actor_ref=MARA, mode="directed")
    saved = store.responses.prepare(cid, sid, round_["id"], MARA, "Mara", {})
    store.responses.save_variant(cid, sid, saved["id"], "Mara answers.", "complete")
    run = SimpleNamespace(
        id="recover", scene_identity=store.scenes.scene_identity(cid, sid),
        cancel_requested=False)
    token = streaming._claim_turn(cid, sid)

    pending = store.responses._scope(cid, sid, store.responses._read(cid))["rounds"][round_["id"]]
    recovered, after = character_turns._recover_completed(cid, sid, run, token, pending)
    assert after["needs_decision"] is True
    assert after["response_id"] == saved["id"]
    assert after["used"] == [MARA]
    assert recovered["pending_response"] == saved["id"]
    assert replies(cid, sid) == ["Mara"]
