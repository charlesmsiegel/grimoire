"""Roadmap 02-S4: the turn plan, dark -- the intent question plus disclosure
and tension in ONE decide item and ONE call, replacing the intent call, with
an `extra` slot whose items ride the same call and are stored by their owner
and rendered by nobody."""

import json

import pytest

from grimoire import decisions, routes, store
from grimoire.decisions import NONE_KEY, Answer, ItemResult
from grimoire.routes import character_turns
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
        actor = client.post(f"/api/campaigns/{cid}/characters", json={"name": name}).json()
        client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast", json={"id": actor["character"]})
    return cid, sid, f"/api/campaigns/{cid}/scenes/{sid}"


def _fake(client, *turns):
    fake = FakeLLM([list(t) for t in turns])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    return fake


def _send(client, base):
    response = client.post(base + "/chat",
                           json={"content": "Mara, where were you?", "speaker_ref": MARA})
    assert response.status_code == 200 and '"error"' not in response.text, response.text


def _last_response(cid, sid):
    rid = [m for m in store.scenes.read_scene(cid, sid)["messages"]
           if m.get("response_id")][-1]["response_id"]
    return store.responses.get(cid, sid, rid, private=True)


def _rows(cid, task):
    return [r for r in store.usage.calls(campaign=cid) if r.get("task") == task]


PLAN = {"stance": "deflect", "disclosure": "withhold", "tension": 1}


def test_the_plan_is_one_call_of_three_questions_and_replaces_the_intent(client):
    cid, sid, base = seed(client)
    store.write_config(decide_turn_plan="plan")
    fake = _fake(client, [decision_reply(PLAN)], ["Mara shrugs." + _HANDOFF])
    _send(client, base)
    assert fake.calls == 2 and len(fake.schemas) == 1
    schema = json.dumps(fake.schemas[0])
    for qid in (turn_plan.STANCE_ID, turn_plan.DISCLOSURE_ID, turn_plan.TENSION_ID):
        assert f'"{qid}"' in schema
    intent = dict(_last_response(cid, sid)["intent"])
    intent.pop("draw")
    assert intent == {"task": "turn-plan", "skipped": "", **PLAN}
    block = turn_plan.block(PLAN, "Mara")
    assert turn_plan.disclosure_description("withhold") in block
    assert turn_plan.tension_description(1) in block
    assert block in [m["content"] for m in fake.requests[1]["messages"]]
    assert len(_rows(cid, "turn-plan")) == 1 and _rows(cid, "turn-intent") == []


def test_an_unread_plan_answer_renders_nothing_for_it(client):
    cid, sid, base = seed(client)
    store.write_config(decide_turn_plan="plan")
    fake = _fake(client, [decision_reply({"stance": None, "disclosure": None, "tension": 9})],
                 ["Mara answers." + _HANDOFF])
    _send(client, base)
    intent = _last_response(cid, sid)["intent"]
    assert (intent["stance"], intent["disclosure"], intent["tension"]) == (None, None, None)
    assert "approach in this reply" not in str(fake.requests[1]["messages"])


def test_disclosure_and_tension_are_the_answer_even_beside_a_report():
    result = ItemResult({
        turn_plan.DISCLOSURE_ID: Answer("hint", distribution={"share": 0.6, "hint": 0.3,
                                                              "withhold": 0.1}),
        turn_plan.TENSION_ID: Answer(0, distribution={"0": 0.4, "1": 0.1, "2": 0.5}),
    }, backend="native")
    assert turn_plan.plan_answers(result) == {"disclosure": "hint", "tension": 0}


def test_only_the_stance_is_drawn():
    dist = {"press": 0.5, "deflect": 0.45, NONE_KEY: 0.05}
    result = ItemResult({turn_plan.STANCE_ID: Answer("press", distribution=dist),
                         turn_plan.DISCLOSURE_ID: Answer("share"),
                         turn_plan.TENSION_ID: Answer(2)}, backend="native")
    item = turn_plan.plan_item("Mara", [], "")
    stances = {turn_plan.drawn_stance(item, result, s)[0] for s in range(200)}
    assert stances == {"press", "deflect"}
    assert turn_plan.plan_answers(result) == {"disclosure": "share", "tension": 2}


def _action_item():
    return decisions.Item("Mara's legal actions.", (decisions.Choice(
        "action", "Which action does Mara take?",
        (decisions.Option("shove", "shove"), decisions.Option("wait", "wait"))),))


def test_extra_items_ride_the_same_call_and_are_stored_by_their_owner(client, monkeypatch):
    cid, sid, base = seed(client)
    store.write_config(decide_turn_plan="plan")
    seen = []

    def read(result):
        seen.append(result)
        return {"chosen": result.answers["action"].answer}

    monkeypatch.setattr(character_turns, "_plan_extras", lambda *_a: (
        turn_plan.Extra("npc-action", _action_item(), read),
        turn_plan.Extra("npc-plain", _action_item())))
    fake = _fake(client, [decision_reply(PLAN, {"action": "wait"}, {"action": "shove"})],
                 ["Mara waits." + _HANDOFF])
    _send(client, base)
    assert fake.calls == 2 and len(fake.schemas) == 1      # one call, three items
    intent = _last_response(cid, sid)["intent"]
    assert intent["extra"]["npc-action"] == {"chosen": "wait"}
    assert intent["extra"]["npc-plain"]["answers"]["action"]["answer"] == "shove"
    assert len(seen) == 1
    # Rendered by nobody: the block is the plan's alone.
    sent = str(fake.requests[1]["messages"])
    assert "wait" not in turn_plan.block(intent, "Mara") and "npc-action" not in sent


def test_two_extras_under_one_owner_skip_the_plan(client, monkeypatch):
    cid, sid, base = seed(client)
    store.write_config(decide_turn_plan="plan")
    monkeypatch.setattr(character_turns, "_plan_extras", lambda *_a: (
        turn_plan.Extra("npc", _action_item()), turn_plan.Extra("npc", _action_item())))
    fake = _fake(client, ["Mara answers." + _HANDOFF])
    _send(client, base)
    assert fake.calls == 1
    assert _last_response(cid, sid)["intent"] == {"task": "turn-plan",
                                                  "skipped": "error:ValueError"}


@pytest.mark.parametrize("mode,task", [("intent", "turn-intent"), ("plan", "turn-plan")])
def test_a_contribution_never_pays_for_both(client, mode, task):
    cid, _sid, base = seed(client)
    store.write_config(decide_turn_plan=mode)
    _fake(client, [decision_reply(PLAN)], ["Mara answers." + _HANDOFF])
    _send(client, base)
    asked = {r["task"] for r in store.usage.calls(campaign=cid)
             if r.get("task") in ("turn-intent", "turn-plan")}
    assert asked == {task}
