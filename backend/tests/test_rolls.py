"""Recorded rolls without a store (Sprint 075): the dice an action needs and the one result they give."""

from __future__ import annotations

import itertools

import pytest

from grimoire.core.authority import PlayerContribution
from grimoire.core.identity import InstanceRef, SourceRef
from grimoire.rolls import (
    PARTIAL_CHOICE, RecordedRoll, RollError, dice_for, resolve, result_token, roll_from_payload, roll_to_payload,
)
from grimoire.rulesets import (
    ActionRequest, ArsStudy, D20Check, ModuleBinding, PbtaMove, RulesetModule, WodTest, propose,
)
from grimoire.rulesets.families import check_result

ACTOR = InstanceRef("q-1", "character", "mara-1")
EDITIONS = {"d20": "5e-2024", "wod": "v5", "pbta": "dungeon-world-1e", "ars": "5e"}
STEALTH = D20Check("dexterity", "stealth", 15, 3)
AWARENESS = WodTest("wits", "awareness", 3, 1, 3)
BRIDGE = PbtaMove("Mara crosses a collapsing bridge", "dexterity", 1)


def request(action, family: str, request_id: str = "c10") -> ActionRequest:
    binding = ModuleBinding(family, EDITIONS[family])
    module = RulesetModule(SourceRef("synthetic", "ruleset", family, "v1"), (binding,))
    return ActionRequest(request_id, ACTOR, module, binding, action)


def test_each_family_draws_its_own_dice():
    assert dice_for(STEALTH) == (20,)
    assert dice_for(AWARENESS) == (10, 10, 10)  # normal dice first, the Hunger die last
    assert dice_for(BRIDGE) == (6, 6)
    with pytest.raises(RollError) as caught:
        dice_for(ArsStudy("ignem", 13, 5, 8, 1220, "spring"))
    assert caught.value.reason == "no_roll"


@pytest.mark.parametrize("faces, token", [((11,), "failure"), ((12,), "success"), ((20,), "success"),
                                          ((1,), "failure")])
def test_a_d20_result_is_the_face_plus_the_modifier_against_the_dc(faces, token):
    result = resolve(STEALTH, faces)
    assert (result.die, result.total, result_token(result)) == (faces[0], faces[0] + 3, token)


@pytest.mark.parametrize("faces, outcome, token", [
    ((10, 6, 10), "messy-critical", "success"),  # a pair of tens with one on a Hunger die
    ((10, 10, 6), "critical", "success"),
    ((6, 7, 8), "success", "success"),
    ((2, 3, 1), "bestial-failure", "failure"),  # a one on the Hunger die
    ((2, 3, 4), "failure", "failure"),
])
def test_a_wod_pool_splits_normal_from_hunger_and_reads_its_outcome(faces, outcome, token):
    result = resolve(AWARENESS, faces)
    assert (result.normal, result.hunger) == (faces[:2], faces[2:])
    assert (result.outcome, result_token(result)) == (outcome, token)


@pytest.mark.parametrize("faces, outcome, token", [((3, 4), "partial", "partial"), ((5, 4), "strong", "success"),
                                                   ((1, 2), "miss", "failure")])
def test_a_pbta_partial_owes_a_choice_it_never_makes(faces, outcome, token):
    result = resolve(BRIDGE, faces)
    assert (result.outcome, result_token(result)) == (outcome, token)
    assert result.choice == (PARTIAL_CHOICE if outcome == "partial" else None)


@pytest.mark.parametrize("action", [STEALTH, AWARENESS, BRIDGE])
def test_every_face_combination_gives_a_result_062_accepts(action):
    """The roll and 062's profile check are one rule: no face combination can disagree with it."""
    for faces in itertools.product(*(range(1, side + 1) for side in dice_for(action))):
        check_result(action, resolve(action, faces))


@pytest.mark.parametrize("faces", [(), (21,), (0,), (12, 3), (True,), [12]])
def test_impossible_faces_are_refused(faces):
    with pytest.raises(RollError) as caught:
        resolve(STEALTH, faces)
    assert caught.value.reason == "invalid_faces"


@pytest.mark.parametrize("action, family, faces", [(STEALTH, "d20", (4,)), (AWARENESS, "wod", (10, 6, 10)),
                                                   (BRIDGE, "pbta", (3, 4))])
def test_a_roll_round_trips_and_its_result_is_derived_again(action, family, faces):
    roll = RecordedRoll(request(action, family), None, faces, "app:roller")
    payload = roll_to_payload(roll.request, None, faces)
    again = roll_from_payload("q-1", payload, "app:roller")
    assert again == roll and again.result == roll.result
    assert propose(roll.request.module, roll.request, roll.result).result == roll.result
    assert "result" not in payload  # a stored record cannot claim a result of its own


def test_a_roll_no_player_backs_binds_narration_as_the_directors():
    played = PlayerContribution("q-1", "c10", "s1", "t10", "mara-1", "human:ana", "action", "I slip past.")
    assert RecordedRoll(request(STEALTH, "d20"), played, (4,), "app:roller").resolution().attempt_id == "c10"
    assert RecordedRoll(request(STEALTH, "d20"), None, (4,), "app:roller").resolution().attempt_id == "director:c10"


@pytest.mark.parametrize("change", [{"faces": [21]}, {"faces": [4, 4]}, {"faces": [4.0]},
                                    {"result": "success"}, {"contribution": {"kind": "action"}}])
def test_a_record_that_is_no_roll_is_undecodable(change):
    payload = {**roll_to_payload(request(STEALTH, "d20"), None, (4,)), **change}
    with pytest.raises(RollError) as caught:
        roll_from_payload("q-1", payload, "app:roller")
    assert caught.value.reason == "undecodable_entry"


def test_a_record_of_another_campaign_or_an_unrollable_action_is_undecodable():
    payload = roll_to_payload(request(STEALTH, "d20"), None, (4,))
    with pytest.raises(RollError):
        roll_from_payload("q-2", payload, "app:roller")
    study = request(ArsStudy("ignem", 13, 5, 8, 1220, "spring"), "ars")
    with pytest.raises(RollError) as caught:
        roll_from_payload("q-1", {**payload, "request": roll_to_payload(study, None, ())["request"],
                                  "faces": []}, "app:roller")
    assert caught.value.reason == "undecodable_entry"
