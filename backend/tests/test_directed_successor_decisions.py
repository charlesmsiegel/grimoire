"""Directed play: an independent Decision chooses every post-generation speaker.

These tests intentionally return prose WITHOUT a handoff fence. The writer
must not control the next speaker, and a completed reply survives an abstention.
"""

import asyncio
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
    store.write_config(decide_speaker_sampling="on")
    model = FakeLLM([["Mara reacts."], ["Winifred responds."], ["Corin adds something."]])
    client.app.dependency_overrides[routes.get_llm] = lambda: model
    asked = scripted_decisions(monkeypatch, WINIFRED, CORIN)

    response = client.post(base + "/chat",
                           json={"content": "What does everyone make of this?", "speaker_ref": MARA})
    assert '"error"' not in response.text, response.text
    assert replies(cid, sid) == ["Mara", "Winifred", "Corin"]
    assert model.calls == 3
    assert len(asked) == 2  # no narrator-only fourth call
    assert set(asked[0][1]) == {WINIFRED, CORIN, "grimoire"}
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


def test_successor_decision_excludes_pre_round_private_history(client):
    cid, sid, _ = seed(client, count=2)
    store.scenes.append_message(cid, sid, "assistant", "PRIVATE_BEFORE_THE_PLAYER")
    store.scenes.append_message(cid, sid, "user", "Winifred, what happened?")
    store.scenes.append_message(cid, sid, "assistant", "Mara responds.", speaker="Mara")
    item, _trigger = character_turns._successor_inputs(
        cid, sid, {"note": ""}, [{"ref": WINIFRED, "name": "Winifred"}], False)
    assert "PRIVATE_BEFORE_THE_PLAYER" not in item.context
    assert "Winifred, what happened?" in item.context
    assert "Mara responds." in item.context


def test_a_stopped_round_never_runs_a_successor_decision(client, monkeypatch):
    cid, sid, _ = seed(client, count=2)

    async def unexpected(*args, **kwargs):
        raise AssertionError("Stop must never select a successor")

    monkeypatch.setattr(character_turns, "_select_successor", unexpected)
    round_ = {"automatic": True, "mode": "directed", "stopped": True}
    watcher = SimpleNamespace(handoff=None, issue=None)
    result = asyncio.run(character_turns._after_saved_contribution(
        cid, sid, None, round_, watcher, [MARA], MARA, "response-id", False))
    assert result["next"] is None and result["issue"] is None


def test_an_invalid_successor_decision_preserves_the_saved_reply(client, monkeypatch):
    cid, sid, base = seed(client, count=2)
    model = FakeLLM([["Mara answers."]])
    client.app.dependency_overrides[routes.get_llm] = lambda: model

    async def rejected(*args, **kwargs):
        raise decisions.DecideRequestError("the selector refused its choices")

    monkeypatch.setattr(character_turns.operations, "decide", rejected)
    response = client.post(base + "/chat",
                           json={"content": "Mara?", "speaker_ref": MARA})
    assert '"error"' not in response.text
    assert replies(cid, sid) == ["Mara"]
    assert last_round(cid, sid)["issue"] == store.response_protocol.INVALID_HANDOFF


def test_an_unusable_distribution_keeps_the_plain_successor(client, monkeypatch):
    cid, sid, base = seed(client, count=2)
    store.write_config(decide_speaker_sampling="on")
    model = FakeLLM([["Mara answers."], ["Winifred reacts."]])
    client.app.dependency_overrides[routes.get_llm] = lambda: model
    asked = scripted_decisions(monkeypatch, WINIFRED)

    def bad_draw(*args, **kwargs):
        raise ValueError("unusable distribution")

    monkeypatch.setattr(store.response_protocol, "sampled_pick", bad_draw)
    response = client.post(base + "/chat",
                           json={"content": "Mara, explain.", "speaker_ref": MARA})
    assert '"error"' not in response.text
    assert replies(cid, sid) == ["Mara", "Winifred"]
    assert len(asked) == 1 and last_round(cid, sid)["issue"] is None


def test_a_sit_out_during_decision_prevents_stale_successor(client, monkeypatch):
    cid, sid, base = seed(client, count=2)
    store.write_config(decide_speaker_sampling="off")
    model = FakeLLM([["Mara answers."]])
    client.app.dependency_overrides[routes.get_llm] = lambda: model

    async def decide(task, items, **kwargs):
        assert WINIFRED in {entry.id for entry in items[0].questions[0].options}
        scene = store.scenes.read_scene(cid, sid)
        settings = store.group_play.settings_of(scene["meta"])
        settings["sitting_out"] = [WINIFRED]
        store.scenes.set_group(cid, sid, store.group_play.dump(settings))
        result = decisions.ItemResult(
            {"next": decisions.Answer(WINIFRED)}, backend="structured")
        return decisions.Decision(items=(result,), backend="structured")

    monkeypatch.setattr(character_turns.operations, "decide", decide)
    response = client.post(base + "/chat",
                           json={"content": "Mara, speak.", "speaker_ref": MARA})
    assert '"error"' not in response.text
    assert replies(cid, sid) == ["Mara"]
    assert last_round(cid, sid)["issue"] == store.response_protocol.INELIGIBLE


def test_exhausted_explicit_extra_round_never_forces_a_narrator_response(client):
    cid, sid, _ = seed(client, count=1)
    round_ = {"automatic": True, "mode": "directed",
              "eligible": character_turns.roster(cid, sid),
              "auto_remaining": 1, "used": [MARA]}
    result = asyncio.run(character_turns._select_successor(
        cid, sid, None, round_, [MARA], MARA))
    assert result["next"] is None and result["lead"] is None
    assert result["handed_back"] is False  # only an explicitly requested round proceeds


def test_the_recovered_reply_can_stop_without_another_decision(client, monkeypatch):
    cid, sid, _ = seed(client, count=2)
    round_ = store.responses.new_round(
        cid, sid, eligible=character_turns.roster(cid, sid), automatic=True,
        post=None, run_id="recover", actor_ref=MARA, mode="directed")
    saved = store.responses.prepare(cid, sid, round_["id"], MARA, "Mara", {})
    store.responses.save_variant(cid, sid, saved["id"], "Mara answers.", "complete")

    async def unexpected(*args, **kwargs):
        raise AssertionError("a cancelled recovery cannot select a successor")

    monkeypatch.setattr(character_turns, "_select_successor", unexpected)
    after = {"needs_decision": True, "used": [MARA], "actor": MARA,
             "response_id": saved["id"], "content_issue": None}
    settled, choice = asyncio.run(character_turns._settle_recovered_successor(
        cid, sid, None, round_, after, True))
    assert settled["pending_response"] is None and settled["status"] == "complete"
    assert choice["next"] is None and choice["issue"] is None
