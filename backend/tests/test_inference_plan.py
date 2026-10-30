"""A turn's model calls are planned before any are made, from synthetic ids only."""

from dataclasses import replace

import pytest

from grimoire.core.inference import (
    OPTIONAL_STAGES, CallId, DeclinedRequest, InferencePlanError, InferencePolicy,
    InferenceStage, OptionalRequest, PlannedCall, TurnInferencePlan, plan_turn,
)

PRESENT = ("npc-a", "npc-b", "npc-c", "npc-d")
GEN = InferenceStage.NPC_GENERATION


def plan(**overrides):
    args = dict(present_actor_ids=PRESENT, speaking_npc_ids=("npc-b", "npc-d"))
    args.update(overrides)
    return plan_turn("campaign-1", "turn-1", **args)


def test_four_present_two_speaking_npcs_cost_two_generation_calls():
    result = plan()
    assert [call.actor_id for call in result.calls] == ["npc-b", "npc-d"]
    assert {call.stage for call in result.calls} == {GEN}
    assert result.baseline_calls == result.calls
    assert result.optional_calls == ()
    assert result.declined == ()
    assert result.generated_actor_ids == {"npc-b", "npc-d"}
    assert result.worst_case_attempts == 2


def test_optional_requests_are_declined_unless_enabled_and_within_cap():
    requests = (
        OptionalRequest("planning", "npc-a", "weigh a bribe"),
        OptionalRequest("planning", "npc-c", "plan an exit"),
        OptionalRequest("critique", None, "check continuity"),
    )
    result = plan(optional_requests=requests, policy=InferencePolicy({"planning": 1}))
    assert len(result.calls) == 3
    assert [(c.stage, c.actor_id) for c in result.optional_calls] == [
        (InferenceStage.PLANNING, "npc-a")]
    assert [(d.request, d.reason) for d in result.declined] == [
        (requests[1], "cap_reached"), (requests[2], "disabled")]
    assert all(d.phase == "main" for d in result.declined)


def test_default_policy_plans_no_optional_calls():
    requests = [OptionalRequest(stage, "npc-a", "extra thought") for stage in sorted(OPTIONAL_STAGES)]
    result = plan(optional_requests=requests)
    assert result.optional_calls == ()
    assert [d.reason for d in result.declined] == ["disabled"] * len(OPTIONAL_STAGES)


@pytest.mark.parametrize("overrides", [
    {"speaking_npc_ids": ("npc-z",)},
    {"speaking_npc_ids": ("npc-b", "npc-b")},
    {"player_controlled_ids": ("npc-b",)},
    {"optional_requests": (OptionalRequest("critique", "npc-z", "absent"),)},
    {"optional_requests": (OptionalRequest("planning", "npc-a", "pc choice"),),
     "player_controlled_ids": ("npc-a",), "policy": InferencePolicy({"planning": 1})},
    {"present_actor_ids": ("npc-b", "npc-b", "npc-d")},
    {"present_actor_ids": ("npc-b", " ", "npc-d")},
    {"phase": ""},
])
def test_planning_errors(overrides):
    with pytest.raises(InferencePlanError):
        plan(**overrides)


@pytest.mark.parametrize("campaign_id, turn_id", [("", "turn-1"), ("campaign-1", " ")])
def test_blank_turn_identity_is_an_error(campaign_id, turn_id):
    with pytest.raises(InferencePlanError):
        plan_turn(campaign_id, turn_id, present_actor_ids=PRESENT)


def test_optional_request_validation():
    with pytest.raises(InferencePlanError):
        OptionalRequest("npc_generation", "npc-a", "not a flag")
    with pytest.raises(ValueError):
        OptionalRequest("planning", "npc-a", " ")
    with pytest.raises(ValueError):
        OptionalRequest("planning", "", "blank actor")


def test_offscreen_player_character_is_allowed():
    result = plan(player_controlled_ids=("pc-1",))
    assert result.player_controlled_ids == {"pc-1"}
    assert len(result.calls) == 2


def test_phases_share_the_turn_budget():
    decide = OptionalRequest("decision", None, "choose who answers")
    select = plan(phase="select", speaking_npc_ids=(), optional_requests=[decide],
                  policy=InferencePolicy({"decision": 1}))
    main = select.add_phase("main", speaking_npc_ids=("npc-b", "npc-d"),
                            optional_requests=[decide])
    assert main.phases == ("select", "main")
    assert main.calls[:1] == select.calls
    assert [(d.phase, d.reason) for d in main.declined] == [("main", "cap_reached")]
    assert select.phases == ("select",)  # the earlier value is unchanged
    with pytest.raises(InferencePlanError):
        main.add_phase("main")
    with pytest.raises(InferencePlanError):
        main.add_phase("after", speaking_npc_ids=("npc-b",))


def test_call_ids_are_structured_and_turn_scoped():
    two = (OptionalRequest("planning", "npc-a", "first"),
           OptionalRequest("planning", "npc-a", "second"))
    result = plan(optional_requests=two, policy=InferencePolicy({"planning": 2}))
    assert [c.call_id.ordinal for c in result.optional_calls] == [1, 2]
    assert CallId("c", "t", "x", GEN, "a/b", 1) != CallId("c", "t", "x/a", GEN, "b", 1)
    later = plan_turn("campaign-1", "turn-2", present_actor_ids=PRESENT,
                      speaking_npc_ids=("npc-b",))
    assert later.calls[0].call_id != result.calls[0].call_id
    for bad in (("", "t", "p", GEN, "a", 1), ("c", "t", "p", GEN, "", 1),
                ("c", "t", "p", GEN, "a", 0), ("c", "t", "p", GEN, "a", True),
                ("c", "t", "p", "telepathy", "a", 1)):
        with pytest.raises(ValueError):
            CallId(*bad)


def test_hand_built_plans_are_validated():
    base = plan()
    enabled = InferencePolicy({"planning": 1})

    def call(stage, actor, ordinal=1, campaign="campaign-1", turn="turn-1", phase="main"):
        return PlannedCall(CallId(campaign, turn, phase, stage, actor, ordinal), "test")

    planning = InferenceStage.PLANNING
    request = OptionalRequest("planning", "npc-a", "x")
    bad_overrides = [
        {"calls": base.calls + (call(planning, "npc-a"),)},  # disabled stage
        {"policy": enabled,
         "calls": base.calls + (call(planning, "npc-a"), call(planning, "npc-c"))},  # over cap
        {"calls": (call(GEN, "npc-b", turn="turn-9"),)},
        {"calls": (call(GEN, "npc-b", phase="later"),)},
        {"calls": (call(GEN, "npc-z"),)},
        {"calls": (call(GEN, "npc-b"), call(GEN, "npc-b"))},
        {"calls": (call(GEN, "npc-b", ordinal=2),)},
        {"player_controlled_ids": frozenset({"npc-b"})},
        {"phases": ()},
        {"phases": ("main", "main")},
        {"declined": (DeclinedRequest("main", request, "cap_reached"),)},
        {"policy": enabled, "declined": (DeclinedRequest("main", request, "disabled"),)},
        {"declined": (DeclinedRequest("later", OptionalRequest("critique", None, "x"),
                                      "disabled"),)},
    ]
    for overrides in bad_overrides:
        with pytest.raises(InferencePlanError):
            replace(base, **overrides)
    with pytest.raises(ValueError):
        DeclinedRequest("main", OptionalRequest("planning", "npc-a", "x"), "too_expensive")


def test_plan_is_deterministic_and_accepts_iterables():
    requests = [OptionalRequest("critique", None, "check")]
    policy = InferencePolicy({"critique": 1})
    first = plan_turn("campaign-1", "turn-1", present_actor_ids=iter(PRESENT),
                      speaking_npc_ids=iter(["npc-b"]), optional_requests=iter(requests),
                      policy=policy)
    second = plan_turn("campaign-1", "turn-1", present_actor_ids=list(PRESENT),
                       speaking_npc_ids=["npc-b"], optional_requests=requests, policy=policy)
    assert first == second
    assert first.call(first.calls[1].call_id).stage is InferenceStage.CRITIQUE
    assert first.call_ids == {c.call_id for c in first.calls}
