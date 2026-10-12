"""Roadmap 02-S2: the turn intent, dark -- one closed question about how the
assigned actor answers, asked before their contribution is composed, with the
answer taken as given. Off is byte-identical; every skip leaves the turn
playing; the block rides the snapshot, the roll resume and Keep writing, and
nothing replays a second call."""

import pytest
from fastapi import HTTPException

from grimoire import content_parts, routes, store
from grimoire.llm_errors import LLMError
from grimoire.routes import character_turns, common
from grimoire.store import turn_plan
from tests.llm_fakes import FakeLLM, decision_reply

MARA = "characters:mara"
_HANDOFF = '\n```handoff\n{"next":null}\n```'


def seed(client):
    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-or-x"})
    wid = store.worlds.create_world("Realm")
    cid = store.campaigns.create_campaign("Saltmarch", wid)
    sid = store.scenes.create_scene(cid, "Mara")
    for name in ("Mara", "Winifred"):
        response = client.post(f"/api/campaigns/{cid}/characters", json={"name": name})
        assert response.status_code == 200, response.text
        actor = response.json()["character"]
        response = client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast", json={"id": actor})
        assert response.status_code == 200, response.text
    return cid, sid, f"/api/campaigns/{cid}/scenes/{sid}"


def _fake(client, *turns):
    fake = FakeLLM([list(t) for t in turns])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    return fake


def _send(client, base, content="Mara, where were you?", **body):
    response = client.post(base + "/chat",
                           json={"content": content, "speaker_ref": MARA, **body})
    assert response.status_code == 200, response.text
    assert '"error"' not in response.text, response.text
    return response


def _intent_on(mode="intent"):
    store.write_config(decide_turn_plan=mode)


def _block(stance="deflect"):
    return turn_plan.block({"stance": stance}, "Mara")


def _text(messages):
    return "\n".join(content_parts.text_of(m["content"]) for m in messages)


def _snapshot(snapshot):
    return list(character_turns.PreparedMessages.from_snapshot(snapshot, "m"))


def _last_response(cid, sid):
    rid = [m for m in store.scenes.read_scene(cid, sid)["messages"]
           if m.get("response_id")][-1]["response_id"]
    return store.responses.get(cid, sid, rid, private=True)


def _rows(cid, task):
    return [r for r in store.usage.calls(campaign=cid) if r.get("task") == task]


# --- off ----------------------------------------------------------------------


def test_off_asks_nothing_and_writes_no_intent(client):
    cid, sid, base = seed(client)
    fake = _fake(client, ["Mara answers." + _HANDOFF])
    _send(client, base)
    assert fake.calls == 1 and fake.schemas == []
    assert "intent" not in _last_response(cid, sid)
    assert _rows(cid, "turn-intent") == []


# --- on -----------------------------------------------------------------------


def test_one_decision_before_the_reply_and_its_block_after_the_history(client):
    cid, sid, base = seed(client)
    _intent_on()
    fake = _fake(client, [decision_reply({"stance": "deflect"})], ["Mara shrugs." + _HANDOFF])
    response = _send(client, base)
    assert fake.calls == 2
    # Only the decision is a `complete` call, sent its schema; the reply streams.
    assert len(fake.schemas) == 1 and fake.schemas[0] is not None
    # The decision came before the contribution's first frame.
    assert response.text.index("response_start") > 0
    decide_request, chat_request = fake.requests
    assert "Character about to reply: Mara" in _text(decide_request["messages"])
    chat = chat_request["messages"]
    at = next(i for i, m in enumerate(chat) if m["content"] == _block())
    assert chat[at]["role"] == "system"
    assert "Mara, where were you?" in content_parts.text_of(chat[at - 1]["content"])
    record = _last_response(cid, sid)
    assert record["intent"] == {"task": "turn-intent", "skipped": "", "stance": "deflect"}
    # The decision is metered under the response it shaped.
    (row,) = _rows(cid, "turn-intent")
    assert row["response_id"] == record["id"] and row["operation"] == "decide"
    assert row["post"] == record["post"] and row["round_id"] == record["round_id"]
    # Frozen into the snapshot, and captured as a decision.
    assert _block() in _text(_snapshot(record["snapshot"]))
    assert any(e["task"] == "turn-intent" for e in store.prompt_log.list_entries(cid, sid))


def test_an_abstained_stance_sends_no_block(client):
    cid, sid, base = seed(client)
    _intent_on()
    fake = _fake(client, [decision_reply({"stance": None})], ["Mara answers." + _HANDOFF])
    _send(client, base)
    assert fake.calls == 2
    assert "approach in this reply" not in _text(fake.requests[1]["messages"])
    assert _last_response(cid, sid)["intent"] == {
        "task": "turn-intent", "skipped": "", "stance": None, "reason": "abstained"}


def test_an_off_option_answer_is_no_stance(client):
    cid, sid, base = seed(client)
    _intent_on()
    _fake(client, [decision_reply({"stance": "monologue"})], ["Mara answers." + _HANDOFF])
    _send(client, base)
    intent = _last_response(cid, sid)["intent"]
    assert intent["stance"] is None and intent["reason"] == "unreadable"


# --- what never asks ------------------------------------------------------------


def test_the_narrator_is_never_asked(client):
    _cid, _sid, base = seed(client)
    _intent_on()
    fake = _fake(client, ["The tide turns." + _HANDOFF])
    response = client.post(base + "/chat", json={"content": "Look.", "speaker_ref": "grimoire"})
    assert response.status_code == 200, response.text
    assert fake.calls == 1 and fake.schemas == []


def test_a_typed_director_note_is_never_asked(client):
    cid, sid, base = seed(client)
    _intent_on()
    fake = _fake(client, ["Mara answers." + _HANDOFF])
    response = client.post(base + "/chat", json={"content": "Make Mara confess.",
                                                 "speaker_ref": MARA, "director": True})
    assert response.status_code == 200, response.text
    assert fake.calls == 1
    assert "intent" not in _last_response(cid, sid)


# --- every skip leaves the turn playing ---------------------------------------


def test_a_missing_key_skips_without_calling_decide(client, monkeypatch):
    cid, sid, base = seed(client)
    _intent_on()
    real = character_turns.require_inference

    def refusing(task, *args, **kwargs):
        if task == "turn-intent":
            raise HTTPException(409, detail={"kind": "missing_key", "detail": "no key"})
        return real(task, *args, **kwargs)

    monkeypatch.setattr(character_turns, "require_inference", refusing)
    fake = _fake(client, ["Mara answers." + _HANDOFF])
    _send(client, base)
    assert fake.calls == 1 and fake.schemas == []
    assert _last_response(cid, sid)["intent"] == {"task": "turn-intent",
                                                  "skipped": "missing_key"}


def test_a_failed_decision_skips_with_its_kind(client, monkeypatch):
    cid, sid, base = seed(client)
    _intent_on()

    async def failing(*_args, **_kwargs):
        raise LLMError("rate_limit", "slow down")

    monkeypatch.setattr(character_turns.operations, "decide", failing)
    fake = _fake(client, ["Mara answers." + _HANDOFF])
    _send(client, base)
    assert fake.calls == 1
    assert _last_response(cid, sid)["intent"]["skipped"] == "rate_limit"


def test_a_spent_deadline_is_refused_unsent_and_never_blamed(client, monkeypatch):
    cid, sid, base = seed(client)
    _intent_on()
    monkeypatch.setattr(common, "PLAN_CEILING_S", 0.0)
    fake = _fake(client, ["Mara answers." + _HANDOFF])
    noted = []
    monkeypatch.setattr(fake, "note_outcome", lambda *a, **k: noted.append(a), raising=False)
    _send(client, base)
    assert fake.calls == 1 and fake.schemas == []
    assert _last_response(cid, sid)["intent"]["skipped"] == "timeout"
    assert noted == []
    assert not [r for r in _rows(cid, "turn-intent") if r.get("status") == "error"]


def test_any_exception_skips_by_class_and_a_retry_does_not_loop(client, monkeypatch):
    cid, sid, base = seed(client)
    _intent_on()

    def broken(*_args):
        raise RuntimeError("a builder bug")

    monkeypatch.setattr(character_turns, "_intent_item", broken)
    fake = _fake(client, ["Mara answers." + _HANDOFF])
    _send(client, base)
    assert fake.calls == 1
    record = _last_response(cid, sid)
    assert record["intent"] == {"task": "turn-intent", "skipped": "error:RuntimeError"}
    assert record["status"] == "complete"
    assert store.responses.unfinished(cid, sid) is None


# --- nothing replays a second call ---------------------------------------------


def test_a_reroll_and_a_retry_reuse_the_snapshot(client):
    cid, sid, base = seed(client)
    _intent_on()
    _fake(client, [decision_reply({"stance": "press"})], ["Mara presses." + _HANDOFF])
    _send(client, base)
    rid = _last_response(cid, sid)["id"]
    reroll = _fake(client, ["Mara presses harder." + _HANDOFF])
    result = client.post(base + f"/responses/{rid}/regenerate")
    assert '"error"' not in result.text, result.text
    assert reroll.calls == 1 and reroll.schemas == []
    assert _block("press") in _text(reroll.messages)
    assert len(_rows(cid, "turn-intent")) == 1


def test_a_retry_of_an_interrupted_contribution_asks_nothing(client):
    cid, sid, base = seed(client)
    _intent_on()
    fake = FakeLLM([[decision_reply({"stance": "withdraw"})], ["Partial."]],
                   error=LLMError("rate_limit", "Wait"), fail_after=1)
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    client.post(base + "/chat", json={"content": "Hello", "speaker_ref": MARA})
    assert store.responses.unfinished(cid, sid) is not None
    retry = _fake(client, ["Finished." + _HANDOFF])
    result = client.post(base + "/retry")
    assert '"error"' not in result.text, result.text
    assert retry.calls == 1 and retry.schemas == []
    assert _block("withdraw") in _text(retry.messages)
    assert len(_rows(cid, "turn-intent")) == 1


def test_a_roll_resume_and_keep_writing_carry_the_block(client):
    cid, sid, base = seed(client)
    _intent_on()
    fake = _fake(client, [decision_reply({"stance": "act"})],
                 ['Wait.\n```roll\n{"check":"notice"}\n```'],
                 ["No roll." + _HANDOFF])
    _send(client, base, "Hello")
    proposal = store.proposals.get(cid, sid)
    result = client.post(base + "/roll-proposal",
                         json={"proposal": proposal["id"], "action": "decline"})
    assert '"error"' not in result.text, result.text
    assert fake.calls == 3
    assert _block("act") in _text(fake.requests[2]["messages"])
    record = _last_response(cid, sid)
    assert _block("act") in _text(_snapshot(record["resume_snapshot"]))
    assert len(_rows(cid, "turn-intent")) == 1
    extend = _fake(client, ["Then she sat." + _HANDOFF])
    result = client.post(base + f"/responses/{record['id']}/extend", json={})
    assert '"error"' not in result.text, result.text
    assert extend.calls == 1 and _block("act") in _text(extend.messages)
    assert len(_rows(cid, "turn-intent")) == 1


@pytest.mark.parametrize("mode", ["off", "maybe"])
def test_a_switch_that_is_not_intent_asks_nothing(client, mode):
    _cid, _sid, base = seed(client)
    store.write_config(decide_turn_plan=mode)
    fake = _fake(client, ["Mara answers." + _HANDOFF])
    _send(client, base)
    assert fake.calls == 1


def test_plan_mode_reads_the_round(monkeypatch, tmp_path):
    """Which rounds ask (§6.3), off the round alone: a replayed turn's round
    carries the app's director note and no typed note, so it asks."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = store.campaigns.create_campaign("Saltmarch", store.worlds.create_world("Realm"))
    fresh = {"pending_response": None, "typed_note": "", "note": "Continue the scene."}
    assert character_turns._plan_mode(cid, fresh, MARA, ()) == ""
    _intent_on()
    assert character_turns._plan_mode(cid, fresh, MARA, ()) == "intent"
    assert character_turns._plan_mode(cid, fresh, "grimoire", ()) == ""
    assert character_turns._plan_mode(cid, fresh, MARA, (("Roll", "system", "x"),)) == ""
    assert character_turns._plan_mode(cid, {**fresh, "pending_response": "r1"}, MARA, ()) == ""
    assert character_turns._plan_mode(cid, {**fresh, "typed_note": "Confess."}, MARA, ()) == ""


def test_a_format_one_store_resolves_the_intent_through_its_role(legacy_client):
    """`turn_plan` has no legacy key (`routing.NO_LEGACY`): a store the
    migration has not reached resolves it through the Decision role, never
    through a `route_turn_plan` nobody could have written -- and a legacy
    `route_scene` pin does not reach it."""
    legacy_client.put("/api/llm-connections/openrouter", json={"api_key": "sk-or-x"})
    cid = store.campaigns.create_campaign("Saltmarch", store.worlds.create_world("Realm"))
    resolved = routes.common.require_inference("turn-intent", cid, operation="decide")
    selector = routes.common.require_inference("response-selector", cid, operation="decide")
    assert resolved.task == "turn-intent" and resolved.operation == "decide"
    assert resolved.chain.primary.model == selector.chain.primary.model
