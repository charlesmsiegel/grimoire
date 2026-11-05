"""027: a generation turn is planned from the campaign's control, never from the caller's say-so."""

from __future__ import annotations

import pytest
from voice_fixtures import CAMPAIGN, PC
from generation_fixtures import DIRECTOR_POLICY, world

from grimoire.core import ControlAssignment
from grimoire.core.authority import DIRECTOR
from grimoire.core.inference import InferencePolicy, InferenceStage
from grimoire.generation import DIRECTOR_PURPOSE, GenerationError, plan_generation_turn


def plan(controls, **kw):
    kw.setdefault("present", ("npc-oda", "npc-bellamy", PC))
    kw.setdefault("speakers", ("npc-oda", "npc-bellamy"))
    return plan_generation_turn(CAMPAIGN, "t1", controls=controls, **kw)


def test_one_call_per_speaker_and_players_derived_from_control():
    controls, _voices = world()
    turn = plan(controls, present=("npc-oda", "npc-bellamy", "npc-quill", PC))
    assert [call.actor_id for call in turn.calls] == ["npc-oda", "npc-bellamy"]
    assert {call.stage for call in turn.calls} == {InferenceStage.NPC_GENERATION}
    assert turn.player_controlled_ids == {PC}  # from controls, not a caller argument
    assert "npc-quill" not in turn.generated_actor_ids  # a silent present NPC costs nothing


@pytest.mark.parametrize("speaker, reason", [
    (PC, "player_speaker"), ("npc-stranger", "absent_speaker"),
])
def test_a_speaker_must_be_a_present_director_npc(speaker, reason):
    controls, _voices = world()
    with pytest.raises(GenerationError) as caught:
        plan(controls, speakers=(speaker,))
    assert caught.value.reason == reason


def test_an_unassigned_speaker_is_refused():
    controls, _voices = world()
    controls = tuple(c for c in controls if c.actor_id != "npc-oda")
    with pytest.raises(GenerationError) as caught:
        plan(controls, speakers=("npc-oda",))
    assert caught.value.reason == "unassigned_speaker"


@pytest.mark.parametrize("field, reason", [("present", "duplicate_present"), ("speakers", "duplicate_speaker")])
def test_an_actor_listed_twice_is_refused(field, reason):
    controls, _voices = world()
    with pytest.raises(GenerationError) as caught:
        plan(controls, **{field: ("npc-oda", "npc-oda", PC) if field == "present" else ("npc-oda", "npc-oda")})
    assert caught.value.reason == reason


def test_a_director_reply_needs_every_present_actor_assigned():
    controls, _voices = world()
    plan(controls, speakers=(), present=("npc-oda", PC, "npc-stranger"))  # fine without a director reply
    with pytest.raises(GenerationError) as caught:
        plan(controls, speakers=(), present=("npc-oda", PC, "npc-stranger"), director_reply=True,
             policy=DIRECTOR_POLICY)
    assert caught.value.reason == "unassigned_actor"


def test_ambiguous_control_is_refused_even_for_a_silent_actor():
    controls, _voices = world()
    controls = controls + (ControlAssignment(CAMPAIGN, PC, "human:bob"),)
    with pytest.raises(GenerationError) as caught:
        plan(controls, speakers=("npc-oda",))
    assert caught.value.reason == "ambiguous_control"


def test_director_reply_is_declined_by_the_default_policy():
    controls, _voices = world()
    turn = plan(controls, speakers=(), director_reply=True)
    assert turn.calls == ()
    assert [(d.request.stage, d.reason) for d in turn.declined] == [(InferenceStage.DIRECTOR_GENERATION, "disabled")]


def test_director_reply_runs_after_the_speakers_when_enabled():
    controls, _voices = world()
    turn = plan(controls, director_reply=True, policy=DIRECTOR_POLICY)
    assert [(call.stage, call.actor_id) for call in turn.calls] == [
        (InferenceStage.NPC_GENERATION, "npc-oda"), (InferenceStage.NPC_GENERATION, "npc-bellamy"),
        (InferenceStage.DIRECTOR_GENERATION, None)]
    assert turn.calls[-1].purpose == DIRECTOR_PURPOSE
    assert [call.stage for call in turn.optional_calls] == [InferenceStage.DIRECTOR_GENERATION]


def test_director_reply_is_never_implicit():
    controls, _voices = world()
    turn = plan(controls, policy=DIRECTOR_POLICY)
    assert InferenceStage.DIRECTOR_GENERATION not in {call.stage for call in turn.calls}


@pytest.mark.parametrize("bad", ["npc-oda", 3, [ControlAssignment(CAMPAIGN, "npc-oda", DIRECTOR), "x"]])
def test_inputs_are_typed(bad):
    controls, _voices = world()
    with pytest.raises(GenerationError):
        if isinstance(bad, list):
            plan(bad)
        else:
            plan(controls, speakers=bad)


def test_director_reply_flag_must_be_a_bool():
    controls, _voices = world()
    with pytest.raises(GenerationError):
        plan(controls, director_reply="yes")


def test_policy_retries_pass_through():
    controls, _voices = world()
    assert plan(controls, policy=InferencePolicy(max_attempts_per_call=3)).worst_case_attempts == 6
