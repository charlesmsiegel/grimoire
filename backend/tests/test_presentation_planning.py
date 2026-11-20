"""068 runtime adoption: an opt-in, capped presentation suggestion before an NPC's own reply.

Every provider call is scripted; no paid model is called. With the stage off, a turn makes exactly
one call per speaking NPC and nothing else. With it on, a planner sees only its own brief, its
suggestion reaches only its own reply prompt (labelled untrusted), and a failed, refused or
screened-out suggestion leaves the NPC speaking as it would with the stage off.
"""

from __future__ import annotations

import json

import pytest
from generation_fixtures import BELLAMY, ODA, Harness, brief, say, world
from voice_fixtures import CAMPAIGN, PC

from grimoire.app.config import parse_model_config, policy_for
from grimoire.core.inference import InferencePolicy, InferenceStage
from grimoire.generation import GenerationError, PLANNING_PHASE, plan_generation_turn
from grimoire.testing.providers import Reply, Status

PRESENT = ("npc-oda", "npc-bellamy", "npc-quill", PC)
SPEAKERS = ("npc-oda", "npc-bellamy")
PLAN_ONE = InferencePolicy({"planning": 1})
PLAN_TWO = InferencePolicy({"planning": 2})
SUGGESTION = "Keep it clipped; count the coin aloud."
LABEL = "Untrusted presentation-only suggestion"
ASK = "Offer only a short presentation suggestion"


def turn(steps, *, policy, planners=SPEAKERS, briefs=None, attempts=1, present=PRESENT):
    controls, voices = world()
    harness = Harness(steps, attempts=attempts)
    plan = plan_generation_turn(CAMPAIGN, "t1", present=present, speakers=SPEAKERS, controls=controls,
                                policy=policy, planners=planners)
    meter = harness.meter(plan)
    briefs = briefs or [brief(ODA, "Oda", voices), brief(BELLAMY, "Bellamy", voices)]
    outcome = harness.runtime.run_turn(meter, briefs, controls=controls)
    return harness, plan, meter, outcome


def stages(plan):
    return [(call.call_id.phase, call.stage.value, call.actor_id) for call in plan.calls]


def test_off_by_default_one_call_per_speaker_and_declines_kept():
    controls, _ = world()
    plan = plan_generation_turn(CAMPAIGN, "t1", present=PRESENT, speakers=SPEAKERS, controls=controls,
                                planners=SPEAKERS)
    assert stages(plan) == [("main", "npc_generation", "npc-oda"), ("main", "npc_generation", "npc-bellamy")]
    assert [(d.phase, d.request.stage, d.request.actor_id) for d in plan.declined] == [
        ("main", InferenceStage.PLANNING, "npc-oda"), ("main", InferenceStage.PLANNING, "npc-bellamy")]
    harness, plan, meter, outcome = turn([say("Coin first."), say("Darling!")], policy=InferencePolicy())
    assert outcome.status == "complete" and len(harness.transport.sends) == 2
    assert not any(ASK in prompt or LABEL in prompt for prompt in harness.prompts())


def test_capped_planning_is_planned_before_replies_and_extra_requests_declined():
    controls, _ = world()
    plan = plan_generation_turn(CAMPAIGN, "t1", present=PRESENT, speakers=SPEAKERS, controls=controls,
                                policy=PLAN_ONE, planners=SPEAKERS)
    assert stages(plan) == [(PLANNING_PHASE, "planning", "npc-oda"), ("main", "npc_generation", "npc-oda"),
                            ("main", "npc_generation", "npc-bellamy")]
    assert [(d.phase, d.request.actor_id) for d in plan.declined] == [(PLANNING_PHASE, "npc-bellamy")]


def test_a_planner_must_speak_and_the_phase_name_is_reserved():
    controls, _ = world()
    with pytest.raises(GenerationError) as caught:
        plan_generation_turn(CAMPAIGN, "t1", present=PRESENT, speakers=("npc-oda",), controls=controls,
                             policy=PLAN_ONE, planners=("npc-quill",))
    assert caught.value.reason == "planner_not_speaking"
    with pytest.raises(GenerationError) as caught:
        plan_generation_turn(CAMPAIGN, "t1", present=PRESENT, speakers=SPEAKERS, controls=controls,
                             policy=PLAN_ONE, planners=("npc-oda",), phase=PLANNING_PHASE)
    assert caught.value.reason == "bad_input"
    with pytest.raises(GenerationError) as caught:
        plan_generation_turn(CAMPAIGN, "t1", present=PRESENT, speakers=SPEAKERS, controls=controls,
                             policy=PLAN_ONE, planners=("npc-oda", "npc-oda"))
    assert caught.value.reason == "duplicate_planner"


def test_a_suggestion_reaches_only_its_own_reply_and_never_the_transcript():
    harness, plan, meter, outcome = turn([say(SUGGESTION), say("Coin first. Then talk."), say("Darling, splendid!")],
                                         policy=PLAN_ONE)
    assert outcome.status == "complete"
    assert [c.status for c in outcome.calls] == ["accepted", "accepted", "accepted"]
    assert [line.text for line in outcome.lines] == ["Coin first. Then talk.", "Darling, splendid!"]
    planning, oda, bellamy = harness.prompts()
    assert ASK in planning and LABEL not in planning
    assert LABEL in oda and SUGGESTION in oda
    assert SUGGESTION not in bellamy and LABEL not in bellamy
    assert [a.call_id.stage for a in meter.ledger.attempts] == [
        InferenceStage.PLANNING, InferenceStage.NPC_GENERATION, InferenceStage.NPC_GENERATION]


def test_a_planner_hears_no_line_of_this_turn_and_uses_a_small_budget():
    from grimoire.runtime import GenerationRuntime

    controls, voices = world()
    harness = Harness([say(SUGGESTION), say("Coin first. Then talk."), say("Darling, splendid!")])
    runtime = GenerationRuntime(harness.adapter, harness.runtime._routes, harness.handle, max_output_tokens=4096)
    plan = plan_generation_turn(CAMPAIGN, "t1", present=PRESENT, speakers=SPEAKERS, controls=controls,
                                policy=PLAN_ONE, planners=("npc-bellamy",))
    outcome = runtime.run_turn(harness.meter(plan), [brief(ODA, "Oda", voices), brief(BELLAMY, "Bellamy", voices)],
                               controls=controls)
    assert outcome.status == "complete"
    planning, oda, bellamy = harness.prompts()
    # The plan phase precedes every reply: Bellamy plans before Oda speaks, so nothing of this turn is heard.
    assert ASK in planning and "Coin first" not in planning
    assert "Coin first" in bellamy and SUGGESTION in bellamy and SUGGESTION not in oda
    bodies = [json.loads(send.body) for send in harness.transport.sends]
    assert bodies[0]["max_tokens"] == 256 and bodies[1]["max_tokens"] == 4096


@pytest.mark.parametrize("step, status, reason", [
    (say("x" * 1001), "rejected", "too_long"),
    (say("Hint at the duke's betrayal with a tight jaw."), "rejected", "unlearned_secret"),
    (Status(503), "failed", None),
])
def test_a_bad_suggestion_leaves_the_reply_as_with_the_stage_off(step, status, reason):
    harness, plan, meter, outcome = turn([step, say("Coin first."), say("Darling!")], policy=PLAN_ONE)
    assert outcome.status == "complete"
    assert outcome.calls[0].status == status
    if reason:
        assert outcome.calls[0].reason == reason
    assert [line.text for line in outcome.lines] == ["Coin first.", "Darling!"]
    assert not any(LABEL in prompt for prompt in harness.prompts()[1:])


def test_an_npc_with_a_decided_choice_gets_no_planning_call():
    from test_decisions import brief as chosen_brief, oda_choice

    controls, voices = world()
    with_choice = chosen_brief(oda_choice())
    harness, plan, meter, outcome = turn([say("Coin first."), say("Darling!")], policy=PLAN_ONE,
                                         briefs=[with_choice, brief(BELLAMY, "Bellamy", voices)],
                                         present=("npc-oda", "npc-bellamy", PC))
    assert outcome.calls[0].status == "not_issued" and outcome.calls[0].reason == "has_choice"
    assert len(harness.transport.sends) == 2
    assert meter.status(plan.calls[0].call_id) == "forgone"
    from grimoire.runtime.record import recorded_turn

    recorded = recorded_turn(1, meter, outcome)  # a forgone suggestion is no line and no mismatch
    assert [line.text for line in recorded.lines] == ["Coin first.", "Darling!"]


def test_a_cancelled_suggestion_stops_the_turn():
    from grimoire.providers.cancel import CancelToken

    controls, voices = world()
    harness = Harness([say(SUGGESTION), say("Coin first."), say("Darling!")])
    plan = plan_generation_turn(CAMPAIGN, "t1", present=PRESENT, speakers=SPEAKERS, controls=controls,
                                policy=PLAN_ONE, planners=SPEAKERS)
    cancel = CancelToken()
    cancel.cancel()
    outcome = harness.runtime.run_turn(harness.meter(plan), [brief(ODA, "Oda", voices),
                                                             brief(BELLAMY, "Bellamy", voices)],
                                       controls=controls, cancel=cancel)
    assert outcome.status == "cancelled" and outcome.lines == () and harness.transport.sends == []


def test_app_configuration_caps_planning_and_round_trips():
    raw = {"provider_id": "local", "base_url": "http://127.0.0.1:1/v1", "model": "m", "planning": 2}
    config = parse_model_config(raw)
    assert config.planning_calls == 2
    assert policy_for(config).cap_for(InferenceStage.PLANNING) == 2
    assert policy_for(parse_model_config({**raw, "planning": 0})).cap_for(InferenceStage.PLANNING) is None
    from grimoire.app.config import ConfigError, config_json
    assert config_json(config)["planning"] == 2 and "planning" not in config_json(parse_model_config({**raw, "planning": 0}))
    for bad in (-1, 17, True, "1"):
        with pytest.raises(ConfigError):
            parse_model_config({**raw, "planning": bad})


def test_actual_app_plans_meters_persists_usage_and_turns_off(tmp_path):
    from provider_servers import base_url, sse_lines, sse_server, stream
    from scene_fixtures import MARA, SERAPHINE
    from textapi_fixtures import Device, V1_CHAT, desktop_library

    from grimoire.app.adapters.providers import configure
    from grimoire.app.appapi import AppApi

    chat = str(V1_CHAT)

    def model_reply(handler):
        request = json.loads(handler.server.requests[-1][2])
        planning = ASK in request["messages"][-1]["content"]
        stream(sse_lines(deltas=(SUGGESTION if planning else "The tide keeps its own ledger.",),
                         usage={"prompt_tokens": 90, "completion_tokens": 8}))(handler)

    def sent(server, text):
        return sum(text in "\n".join(m["content"] for m in json.loads(body)["messages"])
                   for _, _, body in server.requests)

    folder, _ = desktop_library(tmp_path / "fixture")
    path = tmp_path / "model.json"
    with sse_server(model_reply) as server:
        with Device(folder, runtime=False) as device:
            configure(device.api, path, environ={})
            app = AppApi(device.api)
            app.call("continue", {"chat": chat})
            saved = app.call("providers.save_models", {"expected": None, "settings": {
                "provider_id": "local", "base_url": base_url(server), "model": "synthetic", "planning": 1}})
            planning_row = next(s for s in app.call("providers.models")["stages"] if s["stage"] == "planning")
            assert planning_row["available"] and planning_row["enabled"] and planning_row["cap"] == 1
            app.call("post", {"chat": chat, "text": "Is anyone keeping watch?"})
            app.call("generate", {"chat": chat, "speakers": [str(MARA), str(SERAPHINE)]})
            entries = app.call("providers.usage", {"chat": chat})["turns"][-1]["calls"]
            calls = [entry for entry in entries if "stage" in entry]
            assert [entry["declined"] for entry in entries if "declined" in entry]  # the second planner, over the cap
            assert [c["stage"] for c in calls] == ["planning", "npc_generation", "npc_generation"]
            assert all(c["status"] == "accepted" for c in calls)
            assert calls[0]["kind"] == "optional" and calls[0]["attempts"][0]["measurement"] == "provider_reported"
            assert len(server.requests) == 3 and sent(server, ASK) == 1 and sent(server, LABEL) == 1
            record = device.api.runner.open(V1_CHAT).last
            texts = [line.text for take in record.takes for line in take.lines]
            assert texts and not any(SUGGESTION in text for text in texts)  # never stored as a line
            models = app.call("providers.models")
            app.call("providers.save_models", {"expected": models["etag"],
                                               "settings": {**models["settings"], "planning": 0}})
            app.call("generate", {"chat": chat, "speakers": [str(MARA), str(SERAPHINE)]})
            assert len(server.requests) == 5 and sent(server, ASK) == 1
    stored = "".join(p.read_text("utf-8", errors="replace") for p in folder.rglob("*") if p.is_file()
                     and p.suffix in (".json", ".md", ".jsonl"))
    assert SUGGESTION not in stored


def test_an_adapter_exception_in_planning_halts_the_turn_as_it_does_for_a_reply():
    controls, voices = world()
    harness = Harness([say("Coin first."), say("Darling!")])
    plan = plan_generation_turn(CAMPAIGN, "t1", present=PRESENT, speakers=SPEAKERS, controls=controls,
                                policy=PLAN_TWO, planners=SPEAKERS)
    real = harness.adapter.execute

    def broken(meter, call, *args, **kwargs):
        if call.stage is InferenceStage.PLANNING:
            raise RuntimeError("approvals callback bug")
        return real(meter, call, *args, **kwargs)

    harness.adapter.execute = broken
    meter = harness.meter(plan)
    outcome = harness.runtime.run_turn(meter, [brief(ODA, "Oda", voices), brief(BELLAMY, "Bellamy", voices)],
                                       controls=controls)
    assert outcome.status == "halted" and outcome.lines == () and harness.transport.sends == []
    assert outcome.calls[0].status == "failed" and outcome.calls[0].reason == "RuntimeError"
    assert [call.status for call in outcome.calls[1:]] == ["not_issued"] * 3
    assert meter.status(plan.calls[1].call_id) is None  # never reached: not forgone
    from grimoire.runtime.record import recorded_turn

    recorded = recorded_turn(1, meter, outcome)  # an unreached planning call records like any other
    assert recorded.lines == ()
