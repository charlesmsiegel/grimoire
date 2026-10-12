"""Roadmap 02-S8: Decision as a tool from play, dark (02-C4). With
`decide_play_tool=on`, a fresh NPC contribution -- and a reroll of one -- may
call `decide_choice` once, before any text its reader would see; the model
gets the selection, never the distribution; the decision is recorded on the
variant, and tool turns never reach the transcript or the watcher."""

from __future__ import annotations

import json

import pytest

from grimoire import routes, store
from grimoire.routes import character_turns
from tests.llm_fakes import FakeLLM, FakeToolTurns, ToolTurn, decision_reply

MARA = "characters:mara"
_HANDOFF = '\n```handoff\n{"next":null}\n```'
ASK = {"question": "Does Mara tell Seraphine the truth about the ledger?",
       "options": [{"id": "truth", "description": "she tells the truth"},
                   {"id": "lie", "description": "she lies"}],
       "allow_none": False}


def seed(client):
    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-or-x"})
    wid = store.worlds.create_world("Realm")
    cid = store.campaigns.create_campaign("Saltmarch", wid)
    sid = store.scenes.create_scene(cid, "Mara")
    for name in ("Mara", "Winifred"):
        actor = client.post(f"/api/campaigns/{cid}/characters", json={"name": name}).json()
        client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast", json={"id": actor["character"]})
    return cid, sid, f"/api/campaigns/{cid}/scenes/{sid}"


def _install(client, fake):
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    return fake


def _send(client, base, speaker=MARA):
    response = client.post(base + "/chat", json={"content": "Mara, the ledger?",
                                                 "speaker_ref": speaker})
    assert response.status_code == 200, response.text
    return response


def _variant(cid, sid):
    rid = [m for m in store.scenes.read_scene(cid, sid)["messages"]
           if m.get("response_id")][-1]["response_id"]
    record = store.responses.get(cid, sid, rid, private=True)
    return record, next(v for v in record["variants"] if v["id"] == record["active_variant"])


def _rows(cid, task):
    return [r for r in store.usage.calls(campaign=cid) if r.get("task") == task]


def _replies(cid, sid):
    return [m["content"] for m in store.scenes.read_scene(cid, sid)["messages"]
            if m.get("response_id")]


def test_off_offers_no_tool(client):
    cid, sid, base = seed(client)
    fake = _install(client, FakeLLM([["Mara answers." + _HANDOFF]]))
    _send(client, base)
    assert fake.calls == 1 and "tools" not in fake.requests[0]
    _record, variant = _variant(cid, sid)
    assert "tool" not in variant
    (row,) = _rows(cid, "chat")
    assert not row.get("loop_turn") and not row.get("run_id")   # a plain generation


def test_a_call_before_any_text_is_answered_with_the_selection_only(client):
    cid, sid, base = seed(client)
    store.write_config(decide_play_tool="on")
    fake = _install(client, FakeToolTurns(
        ToolTurn("", (("decide_choice", ASK),)),
        ToolTurn(decision_reply({"choice": "lie"})),
        ToolTurn(deltas=("Mara smiles. \"I burned it.\"", _HANDOFF))))
    response = _send(client, base)
    assert '"error"' not in response.text, response.text
    assert fake.calls == 3
    assert [t["name"] for t in fake.requests[0]["tools"]] == ["decide_choice"]
    # The model is sent the selection, never the distribution.
    results = [m for m in fake.requests[2]["messages"] if m.get("role") == "tool"]
    assert [json.loads(m["content"]) for m in results] == [{"selected": "lie"}]
    # Only the final visible text reaches the transcript.
    assert _replies(cid, sid) == ["Mara smiles. \"I burned it.\""]
    _record, variant = _variant(cid, sid)
    tool = variant["tool"]
    assert (tool["offered"], tool["called"], tool["selected"]) == (True, True, "lie")
    assert tool["question"] == ASK["question"] and tool["options"] == ["truth", "lie"]
    assert tool["draw"]["basis"] == "answer"
    # One meter per loop turn under one run id, and the decision its own row.
    chat = _rows(cid, "chat")
    assert [r["loop_turn"] for r in chat] == [1, 2] and len({r["run_id"] for r in chat}) == 1
    (decided,) = _rows(cid, "turn-tool-decision")
    assert decided["response_id"] == _record["id"]


def test_a_call_after_visible_text_is_declined(client):
    cid, sid, base = seed(client)
    store.write_config(decide_play_tool="on")
    fake = _install(client, FakeToolTurns(
        ToolTurn("Mara hesitates.", (("decide_choice", ASK),)), ("never", [])))
    _send(client, base)
    assert fake.calls == 1
    assert _rows(cid, "turn-tool-decision") == []
    _record, variant = _variant(cid, sid)
    assert variant["tool"]["declined"] == "after_text"
    assert _replies(cid, sid) == ["Mara hesitates."]


def test_a_call_after_a_hidden_perception_fence_is_honoured(client):
    cid, sid, base = seed(client)
    store.write_config(decide_play_tool="on")
    fence = "```perception\nShe notices the ink on Seraphine's cuff.\n```\n"
    fake = _install(client, FakeToolTurns(
        ToolTurn(deltas=(fence,), calls=(("decide_choice", ASK),)),
        ToolTurn(decision_reply({"choice": "truth"})),
        ToolTurn(deltas=("```perception\nStill the ink.\n```\n", "Mara sighs. \"Fine.\"",
                         _HANDOFF))))
    _send(client, base)
    assert fake.calls == 3
    assert _replies(cid, sid) == ["Mara sighs. \"Fine.\""]
    _record, variant = _variant(cid, sid)
    assert variant["tool"]["selected"] == "truth"
    assert "Still the ink." in variant["reasoning"] and "ink on Seraphine" in variant["reasoning"]


def test_the_narrator_is_never_offered_the_tool(client):
    _cid, _sid, base = seed(client)
    store.write_config(decide_play_tool="on")
    fake = _install(client, FakeLLM([["The tide turns." + _HANDOFF]]))
    _send(client, base, speaker="grimoire")
    assert "tools" not in fake.requests[0]


def test_a_model_known_unable_to_call_tools_is_never_offered(client, monkeypatch):
    _cid, _sid, base = seed(client)
    store.write_config(decide_play_tool="on")
    monkeypatch.setattr(character_turns.inference_resolve, "known_lacks",
                        lambda resolved, cap, **_k: cap == "tools")
    fake = _install(client, FakeLLM([["Mara answers." + _HANDOFF]]))
    _send(client, base)
    assert "tools" not in fake.requests[0]


def test_a_reroll_is_offered_the_tool_and_a_retry_is_not(client):
    cid, sid, base = seed(client)
    store.write_config(decide_play_tool="on")
    _install(client, FakeToolTurns(ToolTurn(deltas=("Mara answers.", _HANDOFF))))
    _send(client, base)
    record, first = _variant(cid, sid)
    assert first["tool"] == {"task": "turn-tool-decision", "offered": True, "called": False}
    reroll = _install(client, FakeToolTurns(
        ToolTurn("", (("decide_choice", ASK),)),
        ToolTurn(decision_reply({"choice": "truth"})),
        ToolTurn(deltas=("Mara tells it straight.", _HANDOFF))))
    result = client.post(base + f"/responses/{record['id']}/regenerate")
    assert '"error"' not in result.text, result.text
    assert "tools" in reroll.requests[0]
    after = store.responses.get(cid, sid, record["id"], private=True)
    by_id = {v["id"]: v for v in after["variants"]}
    assert by_id[first["id"]]["tool"] == first["tool"]          # untouched
    assert by_id[after["active_variant"]]["tool"]["selected"] == "truth"

    from grimoire.llm_errors import LLMError
    broken = _install(client, FakeToolTurns(LLMError("rate_limit", "Wait")))
    client.post(base + "/chat", json={"content": "And Winifred?", "speaker_ref": MARA})
    assert broken.requests[0].get("tools")                      # a fresh contribution
    assert store.responses.unfinished(cid, sid) is not None
    retry = _install(client, FakeToolTurns(ToolTurn(deltas=("Finished.", _HANDOFF))))
    result = client.post(base + "/retry")
    assert '"error"' not in result.text, result.text
    assert retry.calls == 1 and not retry.requests[0].get("tools")


def test_the_budget_holds_one_call_and_a_cap_is_a_result():
    assert character_turns.PLAY_TOOL_CALLS == 1
    budget = character_turns.PLAY_TOOL_BUDGET
    assert (budget.max_turns, budget.max_tool_calls, budget.max_decisions) == (2, 1, 1)
    shape = character_turns.PLAY_TOOL_SHAPE
    assert (shape.result, shape.on_cap, shape.context) == ("selection", "result", False)
    assert (shape.min_options, shape.max_options, shape.max_question_chars) == (2, 6, 300)


@pytest.mark.parametrize("args,reason", [
    ({"question": "Which?", "options": [{"id": "a", "description": "a"}],
      "allow_none": False}, "invalid_request"),
])
def test_an_invalid_request_is_a_result_not_a_failure(client, args, reason):
    cid, sid, base = seed(client)
    store.write_config(decide_play_tool="on")
    fake = _install(client, FakeToolTurns(
        ToolTurn("", (("decide_choice", args),)),
        ToolTurn(deltas=("Mara shrugs.", _HANDOFF))))
    _send(client, base)
    results = [json.loads(m["content"]) for m in fake.requests[1]["messages"]
               if m.get("role") == "tool"]
    assert results == [{"selected": None, "reason": reason}]
    assert _replies(cid, sid) == ["Mara shrugs."]
