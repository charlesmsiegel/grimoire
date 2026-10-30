"""012 scenario G: a PC's beliefs are the player's, not the director's."""

import pickle
from dataclasses import replace

import pytest

from grimoire.core import (
    DIRECTOR, AuthorityError, Belief, BeliefAuthorityError, ControlAssignment, Knowledge,
    KnowledgeError, PlayerContribution, Proposition, admit_belief,
)
from knowledge_fixtures import C, INES, TAVI, actor, at

PROPOSITION = Proposition(C, "orrin-lies", "Orrin is lying about the duke", at(1), "director")
LEDGER = Knowledge(C, propositions=[PROPOSITION])
CONTROLS = (ControlAssignment(C, "ines", "player-one"), ControlAssignment(C, "tavi", DIRECTOR))
CONTRIBUTION = PlayerContribution(C, "c-1", "s1", "t4", "ines", "player-one", "interiority",
                                  "  Orrin is lying about the duke ")


def pc_belief(**changes) -> Belief:
    base = Belief(C, "b-ines", INES, "orrin-lies", "believed", at(4, "s1", "t4"), "player-one",
                  ("r-1",), contribution_id="c-1")
    return replace(base, **changes)


def refusal(belief, contribution=CONTRIBUTION, controls=CONTROLS) -> str:
    with pytest.raises(BeliefAuthorityError) as caught:
        admit_belief(belief, LEDGER, controls, contribution)
    return caught.value.reason


def test_a_player_authored_belief_backed_by_their_interiority_is_admitted():
    belief = pc_belief()
    assert admit_belief(belief, LEDGER, CONTROLS, CONTRIBUTION) is belief


def test_an_npc_belief_is_the_directors():
    belief = Belief(C, "b-tavi", TAVI, "orrin-lies", "suspected", at(4), DIRECTOR, ("r-1",))
    assert admit_belief(belief, LEDGER, (c for c in CONTROLS)) is belief


@pytest.mark.parametrize("belief, reason", [
    (pc_belief(authored_by=DIRECTOR, contribution_id=None), "pc_belief_authorship"),
    (pc_belief(authored_by="player-two"), "pc_belief_authorship"),
])
def test_nobody_but_the_controller_authors_a_pc_belief(belief, reason):
    assert refusal(belief) == reason


@pytest.mark.parametrize("contribution", [
    None,
    replace(CONTRIBUTION, kind="speech"),                 # speech cannot back a belief
    replace(CONTRIBUTION, kind="directorial_request"),
    replace(CONTRIBUTION, turn_id="t3"),                  # another turn
    replace(CONTRIBUTION, scene_id="s2"),
    replace(CONTRIBUTION, actor_id="orrin"),
    replace(CONTRIBUTION, contribution_id="c-2"),
    replace(CONTRIBUTION, campaign_id="campaign-b"),
    replace(CONTRIBUTION, submitted_by="player-two"),
    "Orrin is lying about the duke",
])
def test_the_backing_contribution_must_be_this_turns_interiority_from_the_controller(contribution):
    assert refusal(pc_belief(), contribution) == "invalid_expression"


def test_a_belief_without_scene_and_turn_cannot_match_a_contribution():
    assert refusal(pc_belief(moment=at(4))) == "invalid_expression"


@pytest.mark.parametrize("text", ["Orrin might be lying about the duke", "orrin is lying about the duke",
                                  "Ines doubts Orrin is lying about the duke"])
def test_the_proposition_is_the_players_words_verbatim(text):
    assert refusal(pc_belief(), replace(CONTRIBUTION, text=text)) == "not_verbatim"


def test_npc_beliefs_take_no_contribution():
    npc = Belief(C, "b-tavi", TAVI, "orrin-lies", "suspected", at(4), DIRECTOR, ("r-1",))
    assert refusal(replace(npc, contribution_id="c-1"), contribution=None) == "wrong_author"
    assert refusal(npc, contribution=CONTRIBUTION) == "wrong_author"
    assert refusal(replace(npc, authored_by="player-one"), contribution=None) == "wrong_author"


def test_unknown_and_unsupported_holders_fail_closed():
    stranger = pc_belief(holder=actor("wren"))
    assert refusal(stranger) == "unknown_control"
    blade = pc_belief(holder=actor("ines", "item"))
    assert refusal(blade) == "unsupported_holder_kind"


def test_ambiguous_control_fails_closed():
    contested = CONTROLS + (ControlAssignment(C, "ines", "player-two"),)
    with pytest.raises(AuthorityError):
        admit_belief(pc_belief(), LEDGER, contested, CONTRIBUTION)


def test_the_proposition_comes_from_the_ledger_never_the_caller():
    # A writer cannot make the verbatim check pass by fabricating the proposition's text.
    other_ledger = Knowledge(C, propositions=[replace(PROPOSITION, text="Orrin is honest")])
    with pytest.raises(BeliefAuthorityError) as caught:
        admit_belief(pc_belief(), other_ledger, CONTROLS, CONTRIBUTION)
    assert caught.value.reason == "not_verbatim"
    for belief, ledger, reason in (
        (pc_belief(proposition_id="ghost"), LEDGER, "unknown_proposition"),
        (pc_belief(), Knowledge("campaign-b"), "campaign_mismatch"),
        (pc_belief(), PROPOSITION, "invalid_record"),
    ):
        with pytest.raises(KnowledgeError) as caught:
            admit_belief(belief, ledger, CONTROLS, CONTRIBUTION)
        assert caught.value.reason == reason and "lying" not in str(caught.value)


@pytest.mark.parametrize("controls", [[object()], ["ines"]])
def test_malformed_controls_are_refused(controls):
    with pytest.raises(KnowledgeError) as caught:
        admit_belief(pc_belief(), LEDGER, controls, CONTRIBUTION)
    assert caught.value.reason == "invalid_record"


def test_refusals_are_authority_errors_not_os_errors_and_carry_no_text():
    error = BeliefAuthorityError("not_verbatim")
    assert isinstance(error, AuthorityError) and not isinstance(error, OSError)
    copy = pickle.loads(pickle.dumps(error))
    assert (type(copy), copy.reason) == (BeliefAuthorityError, "not_verbatim")
    with pytest.raises(BeliefAuthorityError) as caught:
        admit_belief(pc_belief(), LEDGER, CONTROLS, replace(CONTRIBUTION, text="SECRET-TEXT"))
    assert "SECRET" not in str(caught.value) and "lying" not in repr(caught.value.args)
    assert caught.value.__context__ is None
